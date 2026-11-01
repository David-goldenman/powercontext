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

"""Propagate Source lifecycle events across the evidence that depends on them.

A Scope owns one acyclic dependency graph of Sources, Memories, and Topic
Memories. Withdrawing a Source deactivates every dependent node that loses its
supporting evidence, and restoring it reactivates exactly the nodes that regain
support. Lifecycle state only changes use eligibility: entry bodies, Artifact
Revisions, and the References the caller registered are never rewritten.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import Boolean, Column, Integer, MetaData, Table, Text, insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.tables import identity_string
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import ArtifactNotFoundError
from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import SourceRef

NodeFamily = Literal["source", "memory", "topic"]
_MAX_NODE_ID_LENGTH = 256
_REASON = "source lifecycle"

LIFECYCLE_METADATA = MetaData()

LIFECYCLE_NODES_TABLE = Table(
    "pc_source_lifecycle_nodes",
    LIFECYCLE_METADATA,
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("node_id", identity_string(_MAX_NODE_ID_LENGTH), primary_key=True),
    Column("family", identity_string(16), nullable=False),
    Column("reference", Text, nullable=False),
    Column("parents", Text, nullable=False),
    Column("policy", identity_string(8), nullable=False),
    Column("lifecycle_revision", Integer, nullable=False),
    Column("available", Boolean, nullable=False),
    Column("last_sequence", Integer, nullable=False),
    Column("last_event", Text, nullable=False),
)

_MESSAGES = {
    "conflict": "node id is already bound to a different registration",
    "cycle": "the evidence dependency graph must stay acyclic",
    "duplicate-parent": "parents must not repeat",
    "empty-parents": "a derived node requires at least one parent",
    "event-content": "the event sequence is already bound to different content",
    "event-sequence": "event sequence must increase for one Source",
    "family": "this reference family cannot back that registration",
    "memory-entry": "a registered Memory entry is no longer readable",
    "missing-node": "node id is not registered in this Scope",
    "missing-parent": "a parent node must be registered before its child",
    "node-id": "node id must be a non-empty string",
    "policy": "the dependency policy must be 'all' or 'any'",
    "purpose": "purpose must be 'generation' or 'audit'",
    "revision": "the event revision must match the registered lifecycle revision",
    "source": "lifecycle events apply to registered Sources only",
    "unavailable": "the node is not available for generation",
}


class SourceLifecycleError(ValueError):
    """Report a rejected lifecycle registration, event, or resolution."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(detail if detail is not None else _MESSAGES.get(code, code))
        self.code = code


class SourceLifecycleEvent(BaseModel):
    """One validated Source availability change carrying its lifecycle identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: StrictBool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string with visible content")  # noqa: TRY003
        return value

    @field_validator("revision", "sequence", mode="before")
    @classmethod
    def reject_boolean_identity(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("must be a positive integer, not a boolean")  # noqa: TRY003
        return value


def _require_node_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_NODE_ID_LENGTH:
        raise SourceLifecycleError("node-id")
    return value


def _require_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceLifecycleError("revision")
    return value


def _require_policy(value: object) -> Literal["all", "any"]:
    if value not in {"all", "any"}:
        raise SourceLifecycleError("policy")
    return value  # type: ignore[return-value]


def _event_digest(revision: int, available: bool) -> str:
    """Identify one event by the state it records, not by its prose."""

    return json.dumps({"available": bool(available), "revision": revision}, sort_keys=True)


class _Node:
    """One registered lifecycle node, independent of its native Artifact state."""

    __slots__ = (
        "available",
        "family",
        "last_event",
        "last_sequence",
        "lifecycle_revision",
        "node_id",
        "parents",
        "policy",
        "reference",
    )

    def __init__(
        self,
        *,
        node_id: str,
        family: NodeFamily,
        reference: dict[str, Any],
        parents: tuple[str, ...],
        policy: str,
        lifecycle_revision: int,
        available: bool = False,
        last_sequence: int = 0,
        last_event: str = "",
    ) -> None:
        self.node_id = node_id
        self.family = family
        self.reference = reference
        self.parents = parents
        self.policy = policy
        self.lifecycle_revision = lifecycle_revision
        self.available = available
        self.last_sequence = last_sequence
        self.last_event = last_event

    @property
    def entry_id(self) -> str:
        return str(self.reference["entry_id"])

    @property
    def artifact_id(self) -> str:
        if self.family == "memory":
            return str(self.reference["memory_ref"]["artifact_id"])
        return str(self.reference["artifact_id"])

    def binding(self) -> tuple[str, str, tuple[str, ...], str]:
        return (
            self.family,
            json.dumps(self.reference, sort_keys=True),
            self.parents,
            self.policy,
        )


class SourceLifecycleApplication:
    """Own the lifecycle schema and hide unsupported Topic evidence from search."""

    def __init__(self, runtime: Any, database: AsyncDatabase) -> None:
        self.runtime = runtime
        self.database = database
        self._schema_ready = False
        inner = getattr(runtime, "_topic_memory_search", None)
        self._inner_topic_search = inner
        if inner is not None:
            runtime._topic_memory_search = self._filtered_topic_search

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self, scope_id)

    async def ensure_schema(self) -> None:
        """Create the lifecycle table once per Runtime when the profile has not."""

        if self._schema_ready:
            return
        async with self.database.transaction() as connection:
            await create_tables(connection, (LIFECYCLE_NODES_TABLE,))
        self._schema_ready = True

    async def _filtered_topic_search(self, scope_id: str, query: str, /, **kwargs: Any) -> Any:
        """Drop hits whose registered Topic node currently lacks support."""

        result = await self._inner_topic_search(scope_id, query, **kwargs)
        await self.ensure_schema()
        hidden = await self.unavailable_topic_ids(scope_id)
        if not hidden:
            return result
        hits = tuple(hit for hit in result.hits if hit.artifact_ref.artifact_id not in hidden)
        if len(hits) == len(result.hits):
            return result
        return result.model_copy(update={"hits": hits})

    async def unavailable_topic_ids(self, scope_id: str) -> frozenset[str]:
        """Return the Topic artifact ids this Scope may not search right now."""

        scope = validate_scope_id(scope_id)
        table = LIFECYCLE_NODES_TABLE
        async with self.database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(table.c.reference).where(
                        table.c.scope_id == scope,
                        table.c.family == "topic",
                        table.c.available.is_(False),
                    )
                )
            ).scalars()
            return frozenset(str(json.loads(row)["artifact_id"]) for row in rows)


class ScopedSourceLifecycle:
    """Register one Scope's evidence graph and propagate Source availability."""

    def __init__(self, application: SourceLifecycleApplication, scope_id: str) -> None:
        self._application = application
        self._runtime = application.runtime
        self.scope_id = validate_scope_id(scope_id)

    # ---------------------------------------------------------------- registration

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int = 1) -> None:
        """Bind one captured Source to a node id at an explicit lifecycle revision."""

        node = _require_node_id(node_id)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("family")
        registered_revision = _require_revision(revision)
        await self._application.ensure_schema()
        await self._require_readable_source(source_ref)
        await self._register(
            _Node(
                node_id=node,
                family="source",
                reference=source_ref.model_dump(mode="json"),
                parents=(),
                policy="",
                lifecycle_revision=registered_revision,
                available=True,
            )
        )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: Iterable[str],
        policy: Literal["all", "any"] = "all",
    ) -> None:
        """Bind one exact Memory entry version to a node id and its supports."""

        node = _require_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("family")
        selected_policy = _require_policy(policy)
        await self._application.ensure_schema()
        registered = await self._load_nodes()
        normalized = self._require_parents(node, parents, registered)
        await self._require_readable_citation(citation)
        await self._register(
            _Node(
                node_id=node,
                family="memory",
                reference=citation.model_dump(mode="json"),
                parents=normalized,
                policy=selected_policy,
                lifecycle_revision=0,
            )
        )

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: Iterable[str],
        policy: Literal["all", "any"] = "all",
    ) -> None:
        """Bind one Topic Memory Revision to a node id and its supports."""

        node = _require_node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError("family")
        selected_policy = _require_policy(policy)
        await self._application.ensure_schema()
        registered = await self._load_nodes()
        normalized = self._require_parents(node, parents, registered)
        await self._require_readable_artifact(artifact_ref)
        await self._register(
            _Node(
                node_id=node,
                family="topic",
                reference=artifact_ref.model_dump(mode="json"),
                parents=normalized,
                policy=selected_policy,
                lifecycle_revision=0,
            )
        )

    async def _register(self, candidate: _Node) -> None:
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            registered = await self._load_nodes()
            existing = registered.get(candidate.node_id)
            if existing is not None:
                same = existing.binding() == candidate.binding()
                if same and candidate.family == "source":
                    same = existing.lifecycle_revision == candidate.lifecycle_revision
                if not same:
                    raise SourceLifecycleError("conflict")
                return
            registered[candidate.node_id] = candidate
            availability = self._propagate(registered)
            candidate.available = availability[candidate.node_id]
            await self._reconcile_memory(registered, availability)
            await self._persist(registered, availability)

    # ---------------------------------------------------------------------- events

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        """Apply one Source event and return every available node id, sorted."""

        await self._application.ensure_schema()
        node_id = _require_node_id(getattr(event, "node_id", None))
        revision = _require_revision(getattr(event, "revision", None))
        sequence = getattr(event, "sequence", None)
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise SourceLifecycleError("event-sequence")
        available = getattr(event, "available", None)
        if not isinstance(available, bool):
            raise SourceLifecycleError("event-sequence")

        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            registered = await self._load_nodes()
            node = registered.get(node_id)
            if node is None:
                raise SourceLifecycleError("missing-node")
            if node.family != "source":
                raise SourceLifecycleError("source")
            if node.lifecycle_revision != revision:
                raise SourceLifecycleError("revision")

            digest = _event_digest(revision, available)
            if node.last_sequence and sequence < node.last_sequence:
                raise SourceLifecycleError("event-sequence")
            replay = sequence == node.last_sequence
            if replay:
                if node.last_event != digest:
                    raise SourceLifecycleError("event-content")
            else:
                node.available = available
                node.last_sequence = sequence
                node.last_event = digest

            availability = self._propagate(registered)
            # Reconciliation is idempotent, so a replay also finishes an interrupted propagation.
            await self._reconcile_memory(registered, availability)
            if not replay:
                await self._persist(registered, availability)
            return sorted(name for name, value in availability.items() if value)

    # ----------------------------------------------------------------- resolution

    async def resolve(self, node_id: str, *, purpose: Literal["generation", "audit"] = "generation") -> Any:
        """Return the exact Reference registered for a node when its purpose allows it."""

        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("purpose")
        node = _require_node_id(node_id)
        await self._application.ensure_schema()
        registered = await self._load_nodes()
        target = registered.get(node)
        if target is None:
            raise SourceLifecycleError("missing-node")
        if purpose == "generation" and not target.available:
            raise SourceLifecycleError("unavailable")
        if target.family == "source":
            return SourceRef.model_validate(target.reference)
        if target.family == "memory":
            return MemoryCitation.model_validate(target.reference)
        return ArtifactRef.model_validate(target.reference)

    # ------------------------------------------------------------------ internals

    def _require_parents(
        self,
        node_id: str,
        parents: object,
        registered: Mapping[str, _Node],
    ) -> tuple[str, ...]:
        if isinstance(parents, str) or not isinstance(parents, Iterable):
            raise SourceLifecycleError("empty-parents")
        normalized: list[str] = []
        for parent in parents:
            parent_id = _require_node_id(parent)
            if parent_id == node_id:
                raise SourceLifecycleError("cycle")
            if parent_id in normalized:
                raise SourceLifecycleError("duplicate-parent")
            if parent_id not in registered:
                raise SourceLifecycleError("missing-parent")
            normalized.append(parent_id)
        if not normalized:
            raise SourceLifecycleError("empty-parents")
        return tuple(normalized)

    def _propagate(self, registered: Mapping[str, _Node]) -> dict[str, bool]:
        """Recompute every node's availability from the registered Source states."""

        resolved: dict[str, bool] = {}
        visiting: set[str] = set()

        def resolve(node_id: str) -> bool:
            if node_id in resolved:
                return resolved[node_id]
            if node_id in visiting:
                raise SourceLifecycleError("cycle")
            node = registered[node_id]
            if node.family == "source":
                value = node.available
            else:
                visiting.add(node_id)
                support = [resolve(parent) for parent in node.parents]
                visiting.discard(node_id)
                value = all(support) if node.policy == "all" else any(support)
            resolved[node_id] = value
            return value

        for name in registered:
            resolve(name)
        return resolved

    async def _reconcile_memory(
        self,
        registered: Mapping[str, _Node],
        availability: Mapping[str, bool],
    ) -> None:
        """Mirror the propagated state onto native Memory entry states."""

        memory_nodes = sorted(
            (node for node in registered.values() if node.family == "memory"),
            key=lambda node: node.node_id,
        )
        if not memory_nodes:
            return
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            head = await _head_or_none(service, context.artifacts.memory_artifact_id)
            if head is None:
                raise SourceLifecycleError("memory-entry")
            states = {item.entry_id: item.state for item in head.content.manifest.entries}
            entries = {entry.entry_id: entry for entry in await service.entries(head)}
            deactivate: list[Any] = []
            activate: list[Any] = []
            for node in memory_nodes:
                entry = entries.get(node.entry_id)
                if entry is None or node.entry_id not in states:
                    raise SourceLifecycleError("memory-entry")
                wanted = availability[node.node_id]
                state = states[node.entry_id]
                if wanted and state != "active":
                    activate.append(entry)
                elif not wanted and state == "active":
                    deactivate.append(entry)
            if deactivate:
                head = await service.forget(head, entries=tuple(deactivate), reason=_REASON)
            if activate:
                await service.reactivate(head, entries=tuple(activate), reason=_REASON)

    async def _load_nodes(self) -> dict[str, _Node]:
        table = LIFECYCLE_NODES_TABLE
        async with self._application.database.transaction() as connection:
            rows = (
                await connection.execute(select(table).where(table.c.scope_id == self.scope_id))
            ).mappings()
            registered: dict[str, _Node] = {}
            for row in rows.all():
                registered[str(row["node_id"])] = _Node(
                    node_id=str(row["node_id"]),
                    family=row["family"],
                    reference=json.loads(row["reference"]),
                    parents=tuple(json.loads(row["parents"])),
                    policy=str(row["policy"]),
                    lifecycle_revision=int(row["lifecycle_revision"]),
                    available=bool(row["available"]),
                    last_sequence=int(row["last_sequence"]),
                    last_event=str(row["last_event"]),
                )
        return registered

    async def _persist(
        self,
        registered: Mapping[str, _Node],
        availability: Mapping[str, bool],
    ) -> None:
        """Write the full registered graph; rows are updated in place or inserted once."""

        table = LIFECYCLE_NODES_TABLE
        async with self._application.database.transaction() as connection:
            for node in registered.values():
                values = {
                    "family": node.family,
                    "reference": json.dumps(node.reference, sort_keys=True),
                    "parents": json.dumps(list(node.parents)),
                    "policy": node.policy,
                    "lifecycle_revision": node.lifecycle_revision,
                    "available": availability[node.node_id],
                    "last_sequence": node.last_sequence,
                    "last_event": node.last_event,
                }
                updated = await connection.execute(
                    update(table)
                    .where(table.c.scope_id == self.scope_id, table.c.node_id == node.node_id)
                    .values(**values)
                )
                if updated.rowcount:
                    continue
                await connection.execute(
                    insert(table).values(scope_id=self.scope_id, node_id=node.node_id, **values)
                )

    # ------------------------------------------------------------ reference checks

    async def _require_readable_source(self, source_ref: SourceRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(
                source_ref.source_type, source_ref.source_id
            )
        except Exception as error:  # noqa: BLE001 - any unreadable reference is a lifecycle error
            raise SourceLifecycleError("missing-node") from error

    async def _require_readable_citation(self, citation: MemoryCitation) -> None:
        from powercontext.builtin.runtime.models import GetMemoryEntryRequest

        try:
            await self._runtime.memory.for_scope(self.scope_id).get(
                GetMemoryEntryRequest(citation=citation)
            )
        except Exception as error:  # noqa: BLE001 - any unreadable reference is a lifecycle error
            raise SourceLifecycleError("memory-entry") from error

    async def _require_readable_artifact(self, artifact_ref: ArtifactRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                artifact_ref.family, artifact_ref.artifact_id, artifact_ref.revision
            )
        except Exception as error:  # noqa: BLE001 - any unreadable reference is a lifecycle error
            raise SourceLifecycleError("missing-node") from error


async def _head_or_none(service: Any, artifact_id: str) -> Any:
    try:
        return await service.head(artifact_id)
    except ArtifactNotFoundError:
        return None


__all__ = [
    "LIFECYCLE_METADATA",
    "LIFECYCLE_NODES_TABLE",
    "ScopedSourceLifecycle",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
]
