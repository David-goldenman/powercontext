# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Capture the raw visible events of one selected Pi session branch.

A Pi session file is a JSONL archive whose records form a tree through
``parentId``. The selected leaf and its ancestors define one branch; this
module preserves that branch's original visible events as immutable
``pi-event`` Sources and incrementally imports newly appended or newly
branched events through the public Connector lifecycle.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_SOURCE_VERSION = "1"
PI_SESSION_FORMAT_VERSION = 3
PI_SESSION_CHECKPOINT_VERSION = 1

_CHECKPOINT_KEY = "sessions"
_TOOL_UNKNOWN_NAME = "unknown"


@dataclass(frozen=True, slots=True)
class PiSessionInput:
    """One UTF-8 Pi session archive plus the optional selected leaf."""

    content: bytes
    leaf_id: str | None = None

    def __post_init__(self) -> None:
        value = self.content
        if isinstance(value, str):
            value = value.encode()
        elif isinstance(value, (bytearray, memoryview)):
            value = bytes(value)
        elif not isinstance(value, bytes):
            raise ValueError("Pi session content must be UTF-8 encoded bytes")
        object.__setattr__(self, "content", value)
        if self.leaf_id is not None and not isinstance(self.leaf_id, str):
            raise ValueError("Pi session leaf id must be a string or None")


class PiEventInput(BaseModel):
    """One visible event of the selected branch, with its original record."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """A captured, immutable Pi session event."""

    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def event_source_name(session_id: str, event_id: str) -> str:
    """Derive the stable Source identity of one session event.

    The identity depends only on the session and event ids. The readable form is
    used whenever it is unambiguous and bounded; otherwise a digest of the same
    pair keeps the identity stable and unique.
    """

    readable = f"{PI_EVENT_SOURCE_NAME}:{session_id}:{event_id}"
    if (
        ":" not in session_id
        and ":" not in event_id
        and readable == readable.strip()
        and len(readable) <= MAX_SOURCE_ID_LENGTH
    ):
        return readable
    digest = hashlib.sha256(f"{len(session_id)}:{session_id}:{event_id}".encode()).hexdigest()
    return f"{PI_EVENT_SOURCE_NAME}:{digest}"


class PiEventSourceAdapter:
    """Resolve and read one captured Pi session event."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(
            name=event_source_name(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one captured event as standard textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    version=PI_SESSION_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Return the visible events of the selected branch, ordered root to leaf."""

    return _parse_session(value.content, value.leaf_id)[1]


class PiSessionConnector:
    """Re-parse one Pi session file and import only events not yet imported."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_SOURCE_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        try:
            content = self._path.read_bytes()
        except OSError as error:
            raise ValueError(f"Pi session file {str(self._path)!r} could not be read") from error

        session_id, events, records = _parse_session(content, self._leaf_id)
        imported = _decode_checkpoint(session.checkpoint)
        known = imported.setdefault(session_id, {})

        # Every record an earlier run persisted must still carry its original payload,
        # even when it now sits on a branch this run does not select.
        for event_id, digest in known.items():
            record = records.get(event_id)
            if record is not None and _record_digest(record) != digest:
                raise ValueError(f"Pi session event {event_id!r} changed after it was imported")

        pending: list[tuple[PiEventInput, str]] = [
            (event, _record_digest(event.record)) for event in events if event.event_id not in known
        ]

        for event, digest in pending:
            outcome = await session.submit(
                event_source_name(event.session_id, event.event_id),
                PI_EVENT_SOURCE_NAME,
                event,
            )
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                return ConnectorRunCompletion(status=ConnectorRunStatus.INCOMPLETE)
            known[event.event_id] = digest

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint=_encode_checkpoint(imported),
        )


def _parse_session(
    content: bytes, leaf_id: str | None
) -> tuple[str, tuple[PiEventInput, ...], dict[str, dict[str, Any]]]:
    records = _decode_records(content)
    session_id = _session_identity(records[0])
    nodes, order = _index_nodes(records[1:])
    leaf = _select_leaf(nodes, order, leaf_id)
    events = tuple(
        event
        for record in _branch(nodes, leaf)
        if (event := _visible_event(session_id, record)) is not None
    )
    return session_id, events, dict(nodes)


def _decode_records(content: bytes) -> list[dict[str, Any]]:
    if isinstance(content, str):
        text = content.removeprefix("\ufeff")
    elif isinstance(content, (bytes, bytearray, memoryview)):
        try:
            text = bytes(content).decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("Pi session content is not valid UTF-8") from error
    else:
        raise ValueError("Pi session content must be UTF-8 encoded bytes")

    records: list[dict[str, Any]] = []
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Pi session line {number} is not valid JSON") from error
        if not isinstance(record, dict):
            raise ValueError(f"Pi session line {number} must be a JSON object")
        records.append(record)
    if not records:
        raise ValueError("Pi session archive is empty")
    return records


def _session_identity(header: Mapping[str, Any]) -> str:
    if header.get("type") != "session":
        raise ValueError("Pi session must start with a session header")
    version = header.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != PI_SESSION_FORMAT_VERSION:
        raise ValueError(f"Pi session header must declare version {PI_SESSION_FORMAT_VERSION}")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Pi session header must declare a non-empty string id")
    return session_id


def _index_nodes(records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    nodes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("every Pi session record must declare a non-empty string id")
        if event_id in nodes:
            raise ValueError(f"Pi session event id {event_id!r} is duplicated")
        parent_id = record.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str) or parent_id not in nodes:
                raise ValueError(
                    f"Pi session event {event_id!r} references an unknown parent {parent_id!r}"
                )
        nodes[event_id] = record
        order.append(event_id)
    return nodes, order


def _select_leaf(nodes: Mapping[str, dict[str, Any]], order: list[str], leaf_id: str | None) -> str:
    if leaf_id is None:
        if not order:
            raise ValueError("Pi session archive has no event to select as leaf")
        return order[-1]
    if not isinstance(leaf_id, str) or leaf_id not in nodes:
        raise ValueError(f"Pi session leaf {leaf_id!r} is absent from the archive")
    return leaf_id


def _branch(nodes: Mapping[str, dict[str, Any]], leaf: str) -> tuple[dict[str, Any], ...]:
    chain: list[dict[str, Any]] = []
    current: str | None = leaf
    while current is not None:
        record = nodes[current]
        chain.append(record)
        current = record.get("parentId")
    chain.reverse()
    return tuple(chain)


def _visible_event(session_id: str, record: Mapping[str, Any]) -> PiEventInput | None:
    event_type = record.get("type")
    if event_type == "message":
        role, body = _message_body(record.get("message"))
    elif event_type in {"compaction", "branch_summary"}:
        role, body = event_type, _text_body(record.get("summary"))
    elif event_type == "custom_message":
        role, body = "custom", _text_body(record.get("content"))
    else:
        # Control nodes stay on the graph without producing text evidence.
        return None
    if role is None or body is None or not body.strip():
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=record["id"],
        parent_id=record.get("parentId"),
        event_type=str(event_type),
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _message_body(message: object) -> tuple[str | None, str | None]:
    if not isinstance(message, Mapping):
        return None, None
    role = message.get("role")
    if not isinstance(role, str) or not role.strip():
        return None, None
    content = message.get("content")
    if role == "toolResult":
        return role, _tool_result_body(message, content)
    if role == "bashExecution":
        return role, _bash_execution_body(message)
    return role, _message_content(content)


def _message_content(content: object) -> str | None:
    """Render message text blocks and tool calls; reasoning and images are dropped."""

    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        return _message_part(content)
    if isinstance(content, (list, tuple)):
        parts = [text for block in content if (text := _message_part(block)) is not None]
        return "\n".join(parts)
    return None


def _tool_result_body(message: Mapping[str, Any], content: object) -> str | None:
    tool_name = _required_text(message.get("toolName")) or _TOOL_UNKNOWN_NAME
    reference = _call_reference(tool_name, message.get("toolCallId"))
    text = _text_body(content)
    if not text:
        return None
    return f"[tool_result {reference}] {text}"


def _bash_execution_body(message: Mapping[str, Any]) -> str | None:
    """Render a bash execution as ``$ COMMAND`` followed by its full output."""

    command = message.get("command")
    output = message.get("output")
    if isinstance(command, str) and command:
        text = f"$ {command}"
        return f"{text}\n{output}" if isinstance(output, str) and output else text
    if isinstance(output, str) and output:
        return output
    return _text_body(message.get("content"))


def _text_body(content: object) -> str | None:
    """Render a string or a block list as its visible text parts joined by newline."""

    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        return _text_block(content)
    if isinstance(content, (list, tuple)):
        parts = [text for block in content if (text := _text_block(block)) is not None]
        return "\n".join(parts)
    return None


def _text_block(block: object) -> str | None:
    if not isinstance(block, Mapping):
        return None
    if block.get("type") == "text":
        text = block.get("text")
        return text if isinstance(text, str) else None
    return None


def _message_part(block: object) -> str | None:
    text = _text_block(block)
    if text is not None:
        return text
    if isinstance(block, Mapping) and block.get("type") == "toolCall":
        name = _required_text(block.get("name")) or _TOOL_UNKNOWN_NAME
        return f"[tool_call {_call_reference(name, block.get('id'))}] {_canonical_json(block.get('arguments', {}))}"
    # Thinking, image and other non-visible blocks never become text evidence.
    return None


def _required_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _call_reference(name: str, call_id: object) -> str:
    identifier = _required_text(call_id)
    return f"{name} {identifier}" if identifier else name


def _canonical_json(value: object) -> str:
    """Render compact JSON that keeps Unicode, sorts object keys and adds no spaces."""

    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _record_digest(record: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=repr
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _decode_checkpoint(checkpoint: JsonValue | None) -> dict[str, dict[str, str]]:
    sessions: dict[str, dict[str, str]] = {}
    if checkpoint is None:
        return sessions
    if not isinstance(checkpoint, Mapping):
        raise ValueError("Pi session checkpoint must be a JSON object")
    version = checkpoint.get("version", PI_SESSION_CHECKPOINT_VERSION)
    if version != PI_SESSION_CHECKPOINT_VERSION:
        raise ValueError(f"Pi session checkpoint version {version!r} is not supported")
    stored = checkpoint.get(_CHECKPOINT_KEY, {})
    if not isinstance(stored, Mapping):
        raise ValueError("Pi session checkpoint must declare its imported events")
    for session_id, entries in stored.items():
        if not isinstance(session_id, str) or not isinstance(entries, (list, tuple)):
            raise ValueError("Pi session checkpoint entries must be a list of pairs")
        imported: dict[str, str] = {}
        for entry in entries:
            if (
                not isinstance(entry, (list, tuple))
                or len(entry) != 2
                or not all(isinstance(part, str) for part in entry)
            ):
                raise ValueError("Pi session checkpoint entries must be event and digest pairs")
            event_id, digest = entry
            imported[event_id] = digest
        sessions[session_id] = imported
    return sessions


def _encode_checkpoint(sessions: Mapping[str, Mapping[str, str]]) -> dict[str, JsonValue]:
    return {
        "version": PI_SESSION_CHECKPOINT_VERSION,
        _CHECKPOINT_KEY: {
            session_id: [[event_id, digest] for event_id, digest in sorted(entries.items())]
            for session_id, entries in sorted(sessions.items())
        },
    }


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "event_source_name",
    "parse_pi_session",
]
