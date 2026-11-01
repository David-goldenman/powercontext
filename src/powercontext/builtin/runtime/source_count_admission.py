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

"""Count-threshold admission for Source-driven automatic processing.

Memory and Topic Memory may start processing as soon as one Scope has
accumulated enough new journal identities, without waiting for a time condition.
The count is per Scope and per Family: every durable journal position after that
Family's last successful Source cursor is one pending entry, and the first
processing starts from cursor zero. Replayed captures and receipt trust upgrades
never reserve a new position, so they cannot advance a count, while a
lineage-only server Source does reserve one and is counted here even though
generation still excludes it from model evidence.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_INTENTS_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
)
from powercontext.limits import MAX_BINDING_NAME_LENGTH, MAX_SCOPE_ID_LENGTH

DEFAULT_COUNT_SCAN_LIMIT = 200


class SourceCountAdmission:
    """Accept one invocation per Scope whose pending journal count is sufficient.

    Only an idle binding is accepted: while an earlier request is still
    unhandled, a further capture must not queue another invocation. That is what
    keeps one reached threshold equal to one processing call instead of draining
    every remaining input.
    """

    def __init__(
        self,
        *,
        intents: ArtifactProcessingIntentRepository | None = None,
        cursors: SourceCursorRepository | None = None,
        limit: int = DEFAULT_COUNT_SCAN_LIMIT,
    ) -> None:
        if type(limit) is not int or isinstance(limit, bool) or limit < 1:
            raise ValueError("count admission page limit must be a positive integer")  # noqa: TRY003
        self._intents = ArtifactProcessingIntentRepository() if intents is None else intents
        self._cursors = SourceCursorRepository() if cursors is None else cursors
        self._limit = limit

    async def admit(
        self,
        connection: AsyncConnection,
        binding_name: str,
        threshold: int,
        /,
        *,
        after_scope_id: str | None = None,
    ) -> tuple[tuple[str, ...], str | None]:
        """Accept the ready Scopes of one bounded journal page.

        Returns the accepted Scope ids together with the next keyset position, or
        ``None`` when this pass reached the last journal head.
        """

        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        if type(threshold) is not int or isinstance(threshold, bool) or threshold < 1:
            raise ValueError("source count threshold must be a positive integer")  # noqa: TRY003
        heads = SOURCE_JOURNAL_HEADS_TABLE
        statement = select(heads.c.scope_id, heads.c.position).order_by(heads.c.scope_id).limit(self._limit)
        if after_scope_id is not None:
            _require_identifier("after_scope_id", after_scope_id, MAX_SCOPE_ID_LENGTH)
            statement = statement.where(heads.c.scope_id > after_scope_id)
        rows = tuple((await connection.execute(statement)).all())
        if not rows:
            return (), None
        scope_ids = tuple(str(scope_id) for scope_id, _ in rows)
        heads_by_scope = {str(scope_id): int(position) for scope_id, position in rows}
        cursors = await self._cursors.load_many(connection, scope_ids, binding_name)
        intents = await self._generations(connection, binding_name, scope_ids)
        admitted: list[str] = []
        for scope_id in scope_ids:
            cursor = cursors.get(scope_id)
            consumed = 0 if cursor is None else cursor.cursor.sequence
            if admitted_source_count(heads_by_scope[scope_id], consumed) < threshold:
                continue
            generations = intents.get(scope_id)
            if generations is not None and generations[0] != generations[1]:
                # An accepted invocation already owns this Scope's next window.
                continue
            if await self._request(connection, binding_name, scope_id, registered=generations is not None):
                admitted.append(scope_id)
        next_after = None if len(rows) < self._limit else scope_ids[-1]
        return tuple(admitted), next_after

    async def _generations(
        self,
        connection: AsyncConnection,
        binding_name: str,
        scope_ids: tuple[str, ...],
    ) -> dict[str, tuple[int, int]]:
        """Return the requested/handled generations of one bounded Scope page."""

        table = ARTIFACT_PROCESSING_INTENTS_TABLE
        rows = (
            await connection.execute(
                select(table.c.scope_id, table.c.requested_generation, table.c.handled_generation).where(
                    table.c.binding_name == binding_name,
                    table.c.scope_id.in_(scope_ids),
                )
            )
        ).all()
        return {str(scope_id): (int(requested), int(handled)) for scope_id, requested, handled in rows}

    async def _request(
        self,
        connection: AsyncConnection,
        binding_name: str,
        scope_id: str,
        /,
        *,
        registered: bool,
    ) -> bool:
        """Accept one invocation only while no unhandled request exists."""

        if not registered:
            await self._intents.ensure(connection, scope_id, binding_name)
        table = ARTIFACT_PROCESSING_INTENTS_TABLE
        result = await connection.execute(
            update(table)
            .where(
                table.c.scope_id == scope_id,
                table.c.binding_name == binding_name,
                table.c.requested_generation == table.c.handled_generation,
            )
            .values(requested_generation=table.c.requested_generation + 1)
        )
        return result.rowcount == 1


def admitted_source_count(head: int, cursor: int, /) -> int:
    """Return the pending journal identities of one Scope/Family pair."""

    return max(0, head - cursor)


def _require_identifier(field: str, value: object, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be a non-empty trimmed string")  # noqa: TRY003
    if len(value) > maximum:
        raise ValueError(f"{field} must not exceed {maximum} characters")  # noqa: TRY003


__all__ = ["SourceCountAdmission", "admitted_source_count"]
