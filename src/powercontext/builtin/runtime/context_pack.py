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

"""Serializable Context Packs and prefix-reusing incremental deltas.

A Context Pack is the full prepared context split into bounded, valid-UTF-8 chunks
carrying a SHA-256 digest and its own wire size. A delta lets a receiver holding an
earlier pack rebuild a later one from a reused chunk prefix plus an appended suffix,
so consecutive turns do not resend bytes the client already has.

Every field name, encoding, and digest rule here is a receiver-side interoperability
contract: the wire form is canonical JSON (sorted keys, ``,``/``:`` separators, UTF-8,
standard Base64 chunk arrays), and decoding validates the digest and byte count rather
than trusting the sender.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

PACK_SCHEMA: Final = "powercontext.context-pack.v1"
DELTA_SCHEMA: Final = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES: Final = 512

_DIGEST_PREFIX: Final = "sha256:"
_DIGEST_HEX_CHARS: Final = frozenset("0123456789abcdef")
_PACK_FIELDS: Final = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS: Final = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})

__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]


class ContextPackError(ValueError):
    """Protocol or chunking violation for a Context Pack or a Context Pack delta."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """Encode one wire payload: sorted keys, no extra whitespace, UTF-8."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_chunk(chunk: bytes) -> str:
    return base64.b64encode(chunk).decode("ascii")


def _decode_chunk(value: object) -> bytes:
    if not isinstance(value, str):
        raise ContextPackError("context-pack-chunk-not-base64")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as error:
        raise ContextPackError("context-pack-chunk-not-base64") from error
    if _encode_chunk(raw) != value:
        raise ContextPackError("context-pack-chunk-not-base64")
    return raw


def _normalize_items(items: Iterable[object]) -> tuple[bytes, ...]:
    chunks: list[bytes] = []
    for item in items:
        if not isinstance(item, (bytes, bytearray, memoryview)):
            raise ContextPackError("context-pack-chunk-not-bytes")
        chunks.append(bytes(item))
    return tuple(chunks)


def _load_object(data: object, code: str) -> dict[str, Any]:
    if isinstance(data, str):
        raw = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray, memoryview)):
        raw = bytes(data)
    else:
        raise ContextPackError(code)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for key, value in pairs:
            if key in decoded:
                raise ContextPackError(code)
            decoded[key] = value
        return decoded

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError(code) from error
    if not isinstance(payload, dict):
        raise ContextPackError(code)
    return payload


def _require_exact_fields(payload: dict[str, Any], expected: frozenset[str], code: str) -> None:
    if set(payload) != expected:
        raise ContextPackError(code)


def _validate_digest(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX):
        raise ContextPackError(code)
    hexdigest = value[len(_DIGEST_PREFIX) :]
    if len(hexdigest) != 64 or not _DIGEST_HEX_CHARS.issuperset(hexdigest):
        raise ContextPackError(code)
    return value


def _validate_prefix(value: object, *, upper: int | None = None) -> int:
    if type(value) is not int or value < 0:
        raise ContextPackError("context-pack-delta-prefix")
    if upper is not None and value > upper:
        raise ContextPackError("context-pack-delta-prefix")
    return value


def _split_utf8(data: bytes, chunk_bytes: int) -> list[bytes]:
    """Split one overlong line into ``chunk_bytes``-bounded pieces on character boundaries."""

    pieces: list[bytes] = []
    pending = bytearray()
    for character in data.decode("utf-8"):
        encoded = character.encode("utf-8")
        if len(encoded) > chunk_bytes:
            raise ContextPackError("context-pack-chunk-boundary")
        if pending and len(pending) + len(encoded) > chunk_bytes:
            pieces.append(bytes(pending))
            pending = bytearray()
        pending.extend(encoded)
    if pending:
        pieces.append(bytes(pending))
    return pieces


def _pack_digest(items: Iterable[bytes]) -> str:
    unsigned = {"schema": PACK_SCHEMA, "items": [_encode_chunk(item) for item in items]}
    return _DIGEST_PREFIX + hashlib.sha256(_canonical_json(unsigned)).hexdigest()


@dataclass(frozen=True)
class ContextPack:
    """One complete prepared-context payload: ordered chunks, digest, and wire size."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: Iterable[bytes]) -> ContextPack:
        """Build the pack for an ordered chunk tuple, deriving digest and byte count."""

        chunks = _normalize_items(items)
        digest = _pack_digest(chunks)
        # `byte_count` is part of the payload it measures, so the size is the fixed point of
        # "serialize, then measure". The digit count settles after one or two rounds.
        byte_count = 0
        for _ in range(8):
            candidate = cls(items=chunks, digest=digest, byte_count=byte_count)
            measured = len(candidate.to_bytes())
            if measured == byte_count:
                return candidate
            byte_count = measured
        raise ContextPackError("context-pack-byte-count")

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Chunk text deterministically into bounded, valid-UTF-8 pieces that rejoin exactly.

        Whole lines are packed into a chunk while they fit, so a chunk boundary lands on the
        structural seams of a rendered context (its policy preamble, markers, and one item per
        line). That alignment is what makes prefix reuse pay: a change inside the body leaves
        the leading chunks byte-identical, and a chunk that would straddle the seam would
        otherwise have to be re-sent whenever the body moves. Overlong lines fall back to
        capacity splitting at character boundaries.

        A positive limit that no single character fits inside is not a legal chunking, so it
        fails loudly rather than emitting an oversized chunk or corrupting a character.
        """

        if isinstance(content, (bytes, bytearray, memoryview)):
            try:
                content = bytes(content).decode("utf-8")
            except UnicodeDecodeError as error:
                raise ContextPackError("context-pack-content-not-text") from error
        elif not isinstance(content, str):
            raise ContextPackError("context-pack-content-not-text")
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("context-pack-chunk-size")
        chunks: list[bytes] = []
        pending = bytearray()
        for line in content.splitlines(keepends=True):
            encoded = line.encode("utf-8")
            if len(encoded) <= chunk_bytes:
                if pending and len(pending) + len(encoded) > chunk_bytes:
                    chunks.append(bytes(pending))
                    pending = bytearray()
                pending.extend(encoded)
                continue
            if pending:
                chunks.append(bytes(pending))
                pending = bytearray()
            chunks.extend(_split_utf8(encoded, chunk_bytes))
        if pending:
            chunks.append(bytes(pending))
        return cls.build(chunks)

    def to_bytes(self) -> bytes:
        return _canonical_json({
            "schema": PACK_SCHEMA,
            "items": [_encode_chunk(item) for item in self.items],
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, data: object) -> ContextPack:
        """Decode a wire pack, validating its fields, digest, and byte count."""

        payload = _load_object(data, "context-pack-invalid-json")
        if payload.get("schema") != PACK_SCHEMA:
            raise ContextPackError("context-pack-schema")
        _require_exact_fields(payload, _PACK_FIELDS, "context-pack-fields")
        raw_items = payload["items"]
        if not isinstance(raw_items, list):
            raise ContextPackError("context-pack-items")
        chunks = tuple(_decode_chunk(value) for value in raw_items)
        digest = _validate_digest(payload["digest"], "context-pack-digest")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count < 0:
            raise ContextPackError("context-pack-byte-count")
        pack = cls.build(chunks)
        if pack.digest != digest:
            raise ContextPackError("context-pack-digest")
        if len(pack.to_bytes()) != byte_count:
            raise ContextPackError("context-pack-byte-count")
        return pack


@dataclass(frozen=True)
class ContextPackDelta:
    """The reused chunk prefix of a base pack plus the target pack's appended suffix."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "appended", _normalize_items(self.appended))

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Choose the longest common chunk prefix and carry the target suffix."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("context-pack-delta-pack")
        prefix_items = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix_items += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix_items,
            appended=target.items[prefix_items:],
        )

    @property
    def byte_count(self) -> int:
        """Wire size of this delta; never serialized as its own field."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _canonical_json({
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [_encode_chunk(item) for item in self.appended],
        })

    @classmethod
    def from_bytes(cls, data: object) -> ContextPackDelta:
        """Decode a wire delta, validating its fields, encodings, and prefix."""

        payload = _load_object(data, "context-pack-delta-invalid-json")
        if payload.get("schema") != DELTA_SCHEMA:
            raise ContextPackError("context-pack-delta-schema")
        _require_exact_fields(payload, _DELTA_FIELDS, "context-pack-delta-fields")
        base_digest = _validate_digest(payload["base_digest"], "context-pack-delta-base-digest")
        target_digest = _validate_digest(payload["target_digest"], "context-pack-delta-target-digest")
        prefix_items = _validate_prefix(payload["prefix_items"])
        raw_appended = payload["appended"]
        if not isinstance(raw_appended, list):
            raise ContextPackError("context-pack-delta-appended")
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=tuple(_decode_chunk(value) for value in raw_appended),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from a base pack and a delta, or fail on any mismatch."""

    if not isinstance(base, ContextPack):
        raise ContextPackError("context-pack-delta-base")
    if not isinstance(delta, ContextPackDelta):
        raise ContextPackError("context-pack-delta")
    if base.digest != delta.base_digest:
        raise ContextPackError("context-pack-delta-base-mismatch")
    prefix_items = _validate_prefix(delta.prefix_items, upper=len(base.items))
    target = ContextPack.build((*base.items[:prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("context-pack-delta-target-mismatch")
    return target
