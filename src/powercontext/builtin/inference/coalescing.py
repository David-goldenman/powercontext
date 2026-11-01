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

"""Cross-call embedding coalescing with a bounded vector cache.

``CoalescingEmbeddingModel`` wraps an :class:`~powercontext.builtin.inference.EmbeddingModel`
and coordinates every caller that shares the same wrapper instance inside one event loop:

* identical texts submitted by concurrent calls — and by calls that arrive while a
  batch is still running — share a single underlying provider request,
* successful vectors stay in a size-bounded LRU cache for later calls,
* each caller still receives vectors in its own original order, repeats included,
* a caller's usage counts every distinct underlying batch it waited on exactly once,
* cancellation and failure stay local to the caller that hit them.
"""

from __future__ import annotations

import asyncio
import itertools
from collections import OrderedDict
from collections.abc import Iterable, Sequence

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel

__all__ = ["CoalescingEmbeddingModel"]

_CacheKey = tuple[str, str]
_WaiterOutcome = tuple[EmbeddingVector, int, InferenceUsage]


class _Entry:
    """One distinct text waiting for, or currently inside, a provider batch."""

    __slots__ = ("key", "kind", "text", "token", "waiters")

    def __init__(self, key: _CacheKey, text: str, kind: str) -> None:
        self.key = key
        self.kind = kind
        self.text = text
        self.token: int | None = None
        self.waiters: list[asyncio.Future[_WaiterOutcome]] = []


def _merge_usage(usages: Iterable[InferenceUsage]) -> InferenceUsage:
    """Sum usage of distinct underlying batches, keeping absent token counts absent."""

    collected = tuple(usages)
    if not collected:
        return InferenceUsage(requests=0)
    input_tokens = [usage.input_tokens for usage in collected if usage.input_tokens is not None]
    output_tokens = [usage.output_tokens for usage in collected if usage.output_tokens is not None]
    return InferenceUsage(
        requests=sum(usage.requests for usage in collected),
        input_tokens=sum(input_tokens) if input_tokens else None,
        output_tokens=sum(output_tokens) if output_tokens else None,
    )


class CoalescingEmbeddingModel:
    """Coalesce concurrent embedding requests and cache successful vectors.

    The wrapper is single-event-loop by design: it holds no locks and assumes every
    call runs on the loop that created it. Callers only share work and cache entries
    when they use the same wrapper instance.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be positive")
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")
        self._model = model
        self.profile = model.profile
        self._max_batch_size = int(max_batch_size)
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = int(cache_size)
        self._cache: OrderedDict[_CacheKey, EmbeddingVector] = OrderedDict()
        self._pending: OrderedDict[_CacheKey, _Entry] = OrderedDict()
        self._tokens = itertools.count(1)
        self._worker: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ public

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed documents, sharing identical texts with every concurrent caller."""

        return await self._embed(texts, kind="document")

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed retrieval queries, falling back to documents for symmetric providers."""

        kind = "query" if callable(getattr(self._model, "embed_query", None)) else "document"
        return await self._embed(texts, kind=kind)

    # ----------------------------------------------------------------- request

    async def _embed(self, texts: Sequence[str], *, kind: str) -> EmbeddingResult:
        ordered = tuple(texts)
        if not ordered:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        results: dict[str, EmbeddingVector] = {}
        waits: list[tuple[_Entry, asyncio.Future[_WaiterOutcome]]] = []
        for text in dict.fromkeys(ordered):
            key: _CacheKey = (kind, text)
            cached = self._cache_lookup(key)
            if cached is not None:
                results[text] = cached
                continue
            entry = self._pending.get(key)
            if entry is None:
                entry = _Entry(key=key, text=text, kind=kind)
                self._pending[key] = entry
                self._ensure_worker()
            waiter: asyncio.Future[_WaiterOutcome] = asyncio.get_running_loop().create_future()
            entry.waiters.append(waiter)
            waits.append((entry, waiter))

        usage = await self._wait(waits, results) if waits else InferenceUsage(requests=0)
        return EmbeddingResult(vectors=tuple(results[text] for text in ordered), usage=usage)

    async def _wait(
        self,
        waits: list[tuple[_Entry, asyncio.Future[_WaiterOutcome]]],
        results: dict[str, EmbeddingVector],
    ) -> InferenceUsage:
        """Wait on this caller's texts; a failure or cancellation never touches other callers."""

        try:
            outcomes = await asyncio.gather(*(waiter for _, waiter in waits), return_exceptions=True)
        except BaseException:
            for entry, waiter in waits:
                self._discard(entry, waiter)
            raise
        failure = next((outcome for outcome in outcomes if isinstance(outcome, BaseException)), None)
        if failure is not None:
            for entry, waiter in waits:
                self._discard(entry, waiter)
            raise failure
        usage_by_batch: dict[int, InferenceUsage] = {}
        for (entry, _), outcome in zip(waits, outcomes, strict=True):
            vector, token, usage = outcome  # type: ignore[misc]
            results[entry.text] = vector
            usage_by_batch.setdefault(token, usage)
        return _merge_usage(usage_by_batch.values())

    # -------------------------------------------------------------- dispatcher

    def _ensure_worker(self) -> None:
        worker = self._worker
        if worker is not None and not worker.done():
            return
        self._worker = asyncio.get_running_loop().create_task(self._run())
        self._worker.add_done_callback(self._worker_done)

    def _worker_done(self, task: asyncio.Task[None]) -> None:
        if self._worker is task:
            self._worker = None
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self._fail_all(error)

    async def _run(self) -> None:
        while True:
            if not self._has_undispatched():
                return
            # Even a zero window yields one event-loop turn, so callers that are already
            # runnable register before the first batch is claimed.
            await asyncio.sleep(self._coalesce_seconds)
            batches = self._claim()
            if batches:
                await asyncio.gather(*(self._dispatch(batch) for batch in batches))

    def _has_undispatched(self) -> bool:
        return any(entry.token is None for entry in self._pending.values())

    def _claim(self) -> list[list[_Entry]]:
        """Take the currently waiting distinct texts, split into bounded batches."""

        waiting: list[_Entry] = []
        for key, entry in tuple(self._pending.items()):
            if entry.token is not None:
                continue
            if not entry.waiters:
                # Every waiter cancelled before dispatch: never spend a provider request.
                self._pending.pop(key, None)
                continue
            waiting.append(entry)
        batches: list[list[_Entry]] = []
        for start in range(0, len(waiting), self._max_batch_size):
            chunk = waiting[start : start + self._max_batch_size]
            token = next(self._tokens)
            for entry in chunk:
                entry.token = token
            batches.append(chunk)
        return batches

    async def _dispatch(self, chunk: list[_Entry]) -> None:
        token = chunk[0].token
        texts = tuple(entry.text for entry in chunk)
        try:
            if chunk[0].kind == "query":
                result = await self._model.embed_query(texts)
            else:
                result = await self._model.embed(texts)
            vectors = tuple(tuple(vector) for vector in result.vectors)
            if len(vectors) != len(texts):
                raise ValueError(f"embedding provider returned {len(vectors)} vectors for {len(texts)} texts")
            usage = result.usage
        except BaseException as error:  # every waiter of this batch sees the provider failure
            self._fail(chunk, error)
            if isinstance(error, asyncio.CancelledError):
                raise
            return
        for entry, vector in zip(chunk, vectors, strict=True):
            self._cache_store(entry.key, vector)
        for entry, vector in zip(chunk, vectors, strict=True):
            self._settle(entry, (vector, token, usage))

    def _settle(self, entry: _Entry, outcome: _WaiterOutcome) -> None:
        waiters, entry.waiters = entry.waiters, []
        self._pending.pop(entry.key, None)
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(outcome)

    def _fail(self, entries: Iterable[_Entry], error: BaseException) -> None:
        for entry in tuple(entries):
            waiters, entry.waiters = entry.waiters, []
            self._pending.pop(entry.key, None)
            for waiter in waiters:
                if not waiter.done():
                    waiter.set_exception(error)

    def _fail_all(self, error: BaseException) -> None:
        self._fail(tuple(self._pending.values()), error)

    @staticmethod
    def _discard(entry: _Entry, waiter: asyncio.Future[_WaiterOutcome]) -> None:
        for index, candidate in enumerate(entry.waiters):
            if candidate is waiter:
                del entry.waiters[index]
                return

    # -------------------------------------------------------------------- cache

    def _cache_lookup(self, key: _CacheKey) -> EmbeddingVector | None:
        if self._cache_size == 0:
            return None
        vector = self._cache.get(key)
        if vector is None:
            return None
        self._cache.move_to_end(key)
        return vector

    def _cache_store(self, key: _CacheKey, vector: EmbeddingVector) -> None:
        if self._cache_size == 0:
            return
        self._cache[key] = vector
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
