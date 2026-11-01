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

"""HTTP listwise reranking for coarse Memory search candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import (
    MemoryRerankCandidate,
    MemoryRerankDecision,
    _validate_bounds,
)
from powercontext.builtin.inference import InferenceUsage

if TYPE_CHECKING:
    import httpx

HTTP_MEMORY_RERANK_POLICY_ID = "powercontext.memory.rerank.http.v1"
DEFAULT_HTTP_RERANK_BATCH_SIZE = 32


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HTTPMemoryRerankRequest(_StrictModel):
    """One bounded batch of identity-preserving coarse candidates."""

    query: str = Field(min_length=1)
    candidates: tuple[MemoryRerankCandidate, ...]


class HTTPMemoryRerankScore(_StrictModel):
    """One finite relevance score for an original coarse rank."""

    rank: int = Field(ge=1)
    score: float = Field(allow_inf_nan=False)


class HTTPMemoryRerankResponse(_StrictModel):
    """The complete score coverage a rerank endpoint must return for one batch."""

    scores: tuple[HTTPMemoryRerankScore, ...]


class HTTPMemoryReranker:
    """Score coarse candidates through an external HTTP rerank endpoint.

    Candidates are sent in bounded batches so one request never exceeds the endpoint's
    documented pool size. Every batch must score exactly its own ranks once; scores are
    then merged and sorted globally, so batch boundaries never leak into the final order.
    Transport failures propagate unchanged: only a malformed response becomes ``ValueError``.
    """

    policy_id = HTTP_MEMORY_RERANK_POLICY_ID

    def __init__(
        self,
        endpoint: str,
        /,
        *,
        client: httpx.AsyncClient,
        batch_size: int = DEFAULT_HTTP_RERANK_BATCH_SIZE,
    ) -> None:
        if batch_size < 1:
            raise ValueError("rerank batch size must be at least one")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Return validated original ranks without changing hit identity."""

        _validate_bounds(len(candidates), limit)
        scored: list[tuple[float, int]] = []
        requests = 0
        for offset in range(0, len(candidates), self._batch_size):
            batch = candidates[offset : offset + self._batch_size]
            ranks = tuple(range(offset + 1, offset + 1 + len(batch)))
            payload = HTTPMemoryRerankRequest(
                query=query,
                candidates=tuple(
                    MemoryRerankCandidate(rank=rank, text=candidate.text)
                    for rank, candidate in zip(ranks, batch, strict=True)
                ),
            )
            response = await self._client.post(
                self._endpoint,
                json=payload.model_dump(mode="json"),
            )
            response.raise_for_status()
            requests += 1
            scored.extend(_scores_for_batch(response, ranks))
        ordered = sorted(scored, key=_descending_score)
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for _score, rank in ordered[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _descending_score(scored: tuple[float, int]) -> float:
    return -scored[0]


def _scores_for_batch(response: httpx.Response, ranks: tuple[int, ...]) -> list[tuple[float, int]]:
    """Validate one response against the exact ranks that were submitted."""

    try:
        payload = HTTPMemoryRerankResponse.model_validate_json(response.content)
    except ValidationError as error:
        raise ValueError("rerank response does not match the required schema") from error
    observed = [item.rank for item in payload.scores]
    if len(set(observed)) != len(observed):
        raise ValueError("rerank response scored the same rank more than once")  # noqa: TRY003
    if set(observed) != set(ranks):
        raise ValueError("rerank response must score every submitted rank exactly once")  # noqa: TRY003
    return [(item.score, item.rank) for item in payload.scores]


__all__ = [
    "DEFAULT_HTTP_RERANK_BATCH_SIZE",
    "HTTP_MEMORY_RERANK_POLICY_ID",
    "HTTPMemoryRerankRequest",
    "HTTPMemoryRerankResponse",
    "HTTPMemoryRerankScore",
    "HTTPMemoryReranker",
]
