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

"""One native seekdb statement that yields both full-text and vector candidates.

The two channel arms are combined with ``UNION ALL`` inside a single statement so
one round trip returns the lexical candidates from the FULLTEXT index and the
ANN candidates from the vector index. Each arm keeps its own index-driven
selection and candidate limit; the outer ``ORDER BY`` only restores per-channel
order because a union does not promise one.

The ANN arm selects neighbors before joining, mirroring the Topic Memory
projection: CE 4.3.5.6 can lose neighbors when a distance-ordered vector scan
takes part directly in a merge join.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pyobvector import VECTOR
from sqlalchemy import TextClause, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import memory_channel_hits
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_FTS_ARM = """
(
SELECT
    h.memory_artifact_id,
    h.head_revision,
    h.entry_id,
    h.entry_version_id,
    v.text,
    MATCH(h.searchable_text) AGAINST (:analyzed_query) AS score,
    NULL AS distance,
    0 AS channel_ordinal
FROM pc_memory_entry_heads AS h
JOIN pc_memory_entry_versions AS v
  ON v.scope_id = h.scope_id
 AND v.memory_artifact_id = h.memory_artifact_id
 AND v.entry_version_id = h.entry_version_id
WHERE h.scope_id = :scope_id
  AND h.memory_artifact_id IN :memory_artifact_ids
  {fts_tags}
  AND MATCH(h.searchable_text) AGAINST (:analyzed_query)
ORDER BY score DESC, h.memory_artifact_id, h.entry_id, h.entry_version_id
LIMIT :candidate_limit
)
"""

# The ANN selection stays join free; heads and text are hydrated after the
# LIMIT so the vector index alone decides the neighbour set.
_VECTOR_ARM = """
UNION ALL
(
SELECT
    h.memory_artifact_id,
    h.head_revision,
    h.entry_id,
    h.entry_version_id,
    v.text,
    NULL AS score,
    n.distance,
    1 AS channel_ordinal
FROM (
    SELECT
        m.scope_id,
        m.memory_artifact_id,
        m.entry_id,
        l2_distance(m.embedding, :query_vector) AS distance
    FROM pc_memory_vector_entries AS m
    WHERE m.scope_id = :scope_id
      AND m.memory_artifact_id IN :memory_artifact_ids
      {vector_tags}
    ORDER BY l2_distance(m.embedding, :query_vector) {approximate}
    LIMIT :candidate_limit
) AS n
JOIN pc_memory_entry_heads AS h
  ON h.scope_id = n.scope_id
 AND h.memory_artifact_id = n.memory_artifact_id
 AND h.entry_id = n.entry_id
JOIN pc_memory_entry_versions AS v
  ON v.scope_id = h.scope_id
 AND v.memory_artifact_id = h.memory_artifact_id
 AND v.entry_version_id = h.entry_version_id
)
ORDER BY channel_ordinal, score DESC, distance, memory_artifact_id, entry_id, entry_version_id
"""


def _hybrid_sql(*, tagged: bool, approximate: bool, dimension: int) -> TextClause:
    tags = memory_tag_sql("h") if tagged else ""
    vector_tags = memory_tag_sql("m") if tagged else ""
    statement = _FTS_ARM.format(fts_tags=tags) + _VECTOR_ARM.format(
        vector_tags=vector_tags,
        approximate="APPROXIMATE" if approximate else "",
    )
    parameters = [
        bindparam("memory_artifact_ids", expanding=True),
        bindparam("query_vector", type_=VECTOR(dimension)),
    ]
    if tagged:
        parameters += [
            bindparam("tag_keys", expanding=True),
            bindparam("tag_hashes", expanding=True),
            bindparam("tag_minimum"),
        ]
    return text(statement).bindparams(*parameters)


class OceanBaseNativeHybridSearch:
    """Channel candidates for ``mode="hybrid"`` from exactly one statement."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile
        self._plain = _hybrid_sql(tagged=False, approximate=True, dimension=profile.dimension)
        # A tag filter must be applied before the neighbour LIMIT, and seekdb
        # only pushes a correlated predicate through the exact (scanned) order.
        self._tagged = _hybrid_sql(tagged=True, approximate=False, dimension=profile.dimension)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode != "hybrid":
            raise CapabilityNotSupportedError("hybrid")
        if request.embedding_profile != self.profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        rows = (
            await connection.execute(
                self._plain if request.tag_filter is None else self._tagged,
                {
                    "scope_id": scope_id,
                    "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                    "analyzed_query": request.analyzed_query,
                    "query_vector": validate_embedding(
                        request.query_vector,
                        dimension=self.profile.dimension,
                    ),
                    "candidate_limit": request.candidate_limit,
                    **memory_tag_parameters(request.tag_filter),
                },
            )
        ).mappings()
        channels: dict[int, list[Mapping[Any, Any]]] = {0: [], 1: []}
        for row in rows:
            channels[int(row["channel_ordinal"])].append(row)
        return MemorySearchChannels(
            fts=memory_channel_hits(channels[0], request.memories),
            vector=memory_channel_hits(channels[1], request.memories),
        )
