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

"""Prepare final, bounded context from approved Artifact evidence."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import chain
from typing import TypeVar

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit, render_experience
from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.profile.models import Profile
from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemorySearchHit
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_code import (
    CodeEvidenceRef,
    PreparedCodeCandidate,
    assemble_code,
)
from powercontext.builtin.runtime.prepared_text import (
    TRUST_POLICY,
    ContextTextItem,
    fit_context_text_item,
    render_context_text,
)
from powercontext.builtin.runtime.recall_sufficiency import RecallBudgetView

_MIN_TRUNCATED_CONTENT_BYTES = 64
_ELLIPSIS = "…"
_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_Item = TypeVar("_Item")


@dataclass(frozen=True)
class _PreparedContextEntry:
    origin: PreparedContextOrigin
    kind: str
    citation: dict[str, object]
    content: str
    truncated: bool


@dataclass(frozen=True)
class _EntryFit:
    """One fit attempt on the non-assembly path: the entry that fitted, or why none did.

    The two drop reasons are disjoint and mirror the assembly path's
    :class:`~powercontext.builtin.runtime.prepared_text.FitOutcome`. An entry is dropped
    *below the minimum truncated content* when a shorter rendering exists and fits but is too
    short to be an honest delivery; it is dropped *with no fitting truncation* when no
    shortened rendering fits at all.
    """

    entry: _PreparedContextEntry | None = None
    dropped_below_min_bytes: bool = False
    dropped_no_fitting_truncation: bool = False


@dataclass(frozen=True)
class PreparedContextOmissions:
    """Aggregate counts of the items the byte budget omitted in one build.

    Every count is a per-call aggregate, never per-entry attribution and never a verdict about
    an entry: an entry losing to the budget is not a negative result about that entry.

    ``dropped_items`` is the sum of its two sub-counts, so "the budget could not fit this
    item" can be told apart from "this item was too short to truncate into the remaining
    space". A single merged counter would misreport the second cause as the first.
    """

    truncated_items: int = 0
    dropped_items: int = 0
    dropped_below_min_bytes: int = 0
    dropped_no_fitting_truncation: int = 0


@dataclass(frozen=True)
class PreparedContextBuild:
    """Final public context and the exact origins selected to produce it.

    ``omissions`` is always filled by the Builder. The RFC 1560 recall trace is deliberately
    **not** a field here: ``ScopedContextApplication._prepare`` returns ``build.context`` and
    discards the rest, so such a field would have no production observer. The trace is
    delivered through the Runtime's optional ``RecallEffortSink`` instead.
    """

    context: PreparedContext
    origins: tuple[PreparedContextOrigin, ...]
    omissions: PreparedContextOmissions = PreparedContextOmissions()
    code_origins: tuple[CodeEvidenceRef, ...] = ()
    fragments: tuple[PreparedContextFragment, ...] = ()


@dataclass(frozen=True)
class MemoryEntryAddress:
    """Identify one exact Memory entry version across Scope boundaries."""

    memory: ArtifactAddress
    entry_id: str
    entry_version_id: str


PreparedContextOrigin = MemoryCitation | ArtifactRef | MemoryEntryAddress | ArtifactAddress


@dataclass(frozen=True)
class PreparedContextFragment:
    """One exact span of one Memory entry selected as query-relevant evidence.

    ``text`` is always the literal slice ``entry.text[start:end]``: fragments are quoted
    from the original, never rewritten, translated, or summarized.
    """

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _EvidenceText:
    address: MemoryEntryAddress
    body: str
    escaped_bytes: tuple[int, ...]
    item_overhead: int

    def item_bytes(self, start: int, end: int) -> int:
        return (
            self.item_overhead + self.escaped_bytes[end] - self.escaped_bytes[start] + len(str(start)) + len(str(end))
        )


@dataclass(frozen=True)
class _EvidenceSentence:
    source: _EvidenceText
    start: int
    end: int
    terms: dict[str, int]


@dataclass(frozen=True)
class PreparedMemoryCandidates:
    """Memory candidates read from one Scope."""

    scope_id: str
    memory_ref: ArtifactRef | None = None
    hits: tuple[MemoryHit, ...] = ()


@dataclass(frozen=True)
class PreparedExperienceCandidates:
    """Experience candidates read from one Scope."""

    scope_id: str
    hits: tuple[ExperienceSearchHit, ...] = ()


@dataclass(frozen=True)
class PreparedProfileCandidate:
    """The latest committed Profile snapshot read from one Scope."""

    scope_id: str
    profile: Profile


class PreparedContextBuilder:
    """Select and render final context without I/O, persistence, or reranking."""

    memory_candidate_limit = 16
    topic_memory_candidate_limit = 8
    experience_candidate_limit = 8
    candidate_limit = memory_candidate_limit
    entry_limit = 8
    topic_memory_entry_limit = 8
    experience_entry_limit = 2
    max_entry_content_bytes = 2000
    _text_entry_limit: int | None = None

    def empty(self) -> PreparedContext:
        return PreparedContext(status="empty", content=None, content_bytes=0)

    def build(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContext:
        return self.build_result(
            request=request,
            scope_id=scope_id,
            memory_ref=memory_ref,
            hits=hits,
            topic_memory_hits=topic_memory_hits,
            experience_hits=experience_hits,
        ).context

    def build_result(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContextBuild:
        return self.build_scopes_result(
            request=request,
            current_scope_id=scope_id,
            memory_candidates=(
                PreparedMemoryCandidates(scope_id=scope_id or "", memory_ref=memory_ref, hits=tuple(hits)),
            ),
            experience_candidates=(PreparedExperienceCandidates(scope_id=scope_id or "", hits=tuple(experience_hits)),),
            topic_memory_hits=topic_memory_hits,
        )

    def build_scopes_result(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
        code_candidates: Sequence[PreparedCodeCandidate] = (),
    ) -> PreparedContextBuild:
        if request.include_code:

            def historical(history_request: PrepareContextRequest, entries: int) -> PreparedContextBuild:
                builder = PreparedContextBuilder()
                builder.entry_limit = entries
                builder._text_entry_limit = entries if entries < self.entry_limit else None
                return builder.build_scopes_result(
                    request=history_request.model_copy(update={"include_code": False}),
                    current_scope_id=current_scope_id,
                    memory_candidates=memory_candidates,
                    topic_memory_hits=topic_memory_hits,
                    experience_candidates=experience_candidates,
                    profile_candidates=profile_candidates,
                )

            return assemble_code(request, code_candidates, self.entry_limit, historical)

        if request.evidence_selection:
            return self._select_evidence(
                request=request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
            )

        if sum(len(candidates.hits) for candidates in memory_candidates) > self.memory_candidate_limit:
            raise PreparedContextInvariantError("memory-candidate-limit")
        if len(topic_memory_hits) > self.topic_memory_candidate_limit:
            raise PreparedContextInvariantError("topic-memory-candidate-limit")
        if sum(len(candidates.hits) for candidates in experience_candidates) > self.experience_candidate_limit:
            raise PreparedContextInvariantError("experience-candidate-limit")

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        if content is None:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=omissions)
        content_bytes = len(content.encode("utf-8"))
        # The assembly path is bounded by construction (every fitted item is measured against
        # `max_bytes`), so the ceiling check belongs to the non-assembly renderer only.
        if request.assembly is None and content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=origins,
            omissions=omissions,
        )

    def probe_budget(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
    ) -> RecallBudgetView:
        """Report what the byte budget does to one candidate set, without delivering it.

        This is the RFC 1560 *budget probe*: one pass of this Builder's own pure selection and
        rendering code, keeping only the counters and discarding the rendered output. It lets
        the gate tell budget-limited thinness from recall-limited thinness.

        Pure and side-effect free: no I/O, no persistence, nothing reported to any sink, and
        the same inputs as :meth:`build_scopes_result` are guaranteed to yield the same
        counters because both go through :meth:`_select_entries`.
        """

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        content_bytes = 0 if content is None else len(content.encode("utf-8"))
        return RecallBudgetView(
            max_bytes=request.max_bytes,
            delivered_items=len(origins),
            truncated_items=omissions.truncated_items,
            dropped_items=omissions.dropped_items,
            unused_bytes=max(0, request.max_bytes - content_bytes),
        )

    def _select_entries(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        """Run one complete pure selection pass and render it.

        Returns ``(content, origins, omissions)``; ``content`` is ``None`` exactly when nothing
        was selected, which is the empty-result case for both paths. This is the single
        selection path shared by :meth:`build_scopes_result` and :meth:`probe_budget`, so the
        probe's counters always describe the selection the build would actually perform.
        """

        if request.assembly is not None:
            return self._select_text(
                request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
                topic_memory_hits=topic_memory_hits,
            )

        memory_entries = _interleave_groups(
            tuple(
                self._memory_entries(
                    candidates.memory_ref,
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in memory_candidates
            )
        )
        topic_memory_entries = self._topic_memory_entries(topic_memory_hits)
        experience_entries = _interleave_groups(
            tuple(
                self._experience_entries(
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in experience_candidates
            )
        )[: self.experience_entry_limit]
        entries, omissions = self._fit_entries(request, memory_entries, topic_memory_entries, experience_entries)
        if not entries:
            return None, (), omissions
        return _render(entries), tuple(entry.origin for entry in entries), omissions

    def _select_text(
        self,
        request: PrepareContextRequest,
        *,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        assembly = request.assembly
        if assembly is None:
            raise PreparedContextInvariantError("text-assembly-missing")
        included: list[ContextTextItem] = []
        origins: list[PreparedContextOrigin] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for section in assembly.sections:
            # Unfilled earlier sections leave their capacity available to later ones.
            limit = (
                section.limit
                if self._text_entry_limit is None
                else min(section.limit, self._text_entry_limit - len(included))
            )
            if section.family == "profile":
                entries = self._profile_entries(profile_candidates)
            elif section.family == "topic-memory":
                entries = self._topic_memory_entries(topic_memory_hits, scope_id=current_scope_id)
            else:
                groups = (
                    tuple(
                        tuple(
                            self._memory_entries(group.memory_ref, (hit,), scope_id=group.scope_id)
                            for hit in group.hits
                        )
                        for group in memory_candidates
                    )
                    if section.family == "memory"
                    else tuple(
                        tuple(self._experience_entries((hit,), scope_id=group.scope_id) for hit in group.hits)
                        for group in experience_candidates
                    )
                )
                entries = tuple(chain.from_iterable(_interleave_groups(groups)))
            seen: set[tuple[str, str, str, int, str | None, str | None]] = set()
            rank = 0
            selected_count = 0
            for entry in entries:
                if selected_count >= limit:
                    break
                item = _text_item(entry)
                artifact = item.artifact
                identity = (
                    artifact.scope_id,
                    artifact.artifact.family,
                    artifact.artifact.artifact_id,
                    artifact.artifact.revision,
                    item.entry_id,
                    item.entry_version_id,
                )
                if identity in seen or not item.content.strip():
                    continue
                seen.add(identity)
                rank += 1
                outcome = fit_context_text_item(included, replace(item, recall_rank=rank), assembly, request.max_bytes)
                if outcome.item is None:
                    dropped_below_min_bytes += int(outcome.dropped_below_min_bytes)
                    dropped_no_fitting_truncation += int(outcome.dropped_no_fitting_truncation)
                    continue
                fitted = outcome.item
                included.append(fitted)
                origins.append(entry.origin)
                selected_count += 1
                truncated_items += int(fitted.truncated)
        omissions = PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )
        if not included:
            return None, (), omissions
        content = render_context_text(included, assembly)
        return content, tuple(origins), omissions

    def _select_evidence(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
    ) -> PreparedContextBuild:
        """Select query-relevant spans from Memory candidate bodies under the byte budget.

        The budget covers the *complete* output — trust policy, envelope markers, JSON,
        citations, and body — not just the quoted text, so a fragment is admitted only when
        the rendered context still fits as a whole.
        """

        if sum(len(candidates.hits) for candidates in memory_candidates) > self.memory_candidate_limit:
            raise PreparedContextInvariantError("memory-candidate-limit")

        query_tokens = _evidence_tokens(request.query)
        sentences = _evidence_sentences(memory_candidates, current_scope_id, request.max_bytes)
        scores = _evidence_scores(sentences, query_tokens)
        selected: set[int] = set()
        fragments: tuple[PreparedContextFragment, ...] = ()
        current_bytes = _FRAGMENT_OVERHEAD_BYTES
        while len(selected) < len(sentences):
            best = _next_evidence_sentence(
                sentences,
                scores,
                selected,
                fragments,
                current_bytes,
                request.max_bytes,
                self.entry_limit * 4,
            )
            if best is None:
                break
            index, current_bytes = best
            selected.add(index)
            fragments = _evidence_fragments(sentences, selected)

        if not fragments:
            return PreparedContextBuild(context=self.empty(), origins=())
        items = [_fragment_item(part.address, part.text, part.start, part.end) for part in fragments]
        content = _render_fragment_items(items)
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=tuple(fragment.address for fragment in fragments),
            fragments=fragments,
        )

    def _profile_entries(self, candidates: Sequence[PreparedProfileCandidate]) -> tuple[_PreparedContextEntry, ...]:
        entries = []
        for candidate in candidates:
            origin = ArtifactAddress(scope_id=candidate.scope_id, artifact=candidate.profile.as_ref())
            entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="profile",
                    citation={"artifact": origin.model_dump(mode="json")},
                    content=candidate.profile.content.content,
                    truncated=False,
                )
            )
        return tuple(entries)

    def _memory_entries(
        self,
        memory_ref: ArtifactRef | None,
        hits: Sequence[MemoryHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        if hits and memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        memory_entries: list[_PreparedContextEntry] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            if hit.memory_ref != memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")

            citation_key = (hit.entry_id, hit.entry_version_id)
            if citation_key in seen:
                continue
            seen.add(citation_key)
            if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            origin: PreparedContextOrigin = citation
            rendered_citation = citation.model_dump(mode="json")
            if scope_id is not None:
                memory = ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref)
                origin = MemoryEntryAddress(
                    memory=memory,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                rendered_citation = {
                    "memory": memory.model_dump(mode="json"),
                    "entry_id": hit.entry_id,
                    "entry_version_id": hit.entry_version_id,
                }
            memory_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="memory",
                    citation=rendered_citation,
                    content=hit.text,
                    truncated=False,
                )
            )
        return tuple(memory_entries)

    def _topic_memory_entries(
        self,
        hits: Sequence[TopicMemorySearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        topic_entries: list[_PreparedContextEntry] = []
        seen_topics: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != TopicMemory.family:
                raise PreparedContextInvariantError("topic-memory-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_topics:
                continue
            seen_topics.add(identity)
            if len(topic_entries) >= self.topic_memory_entry_limit:
                break
            content = {"title": hit.title, "summary": hit.summary}
            if hit.snippet is not None:
                content["snippet"] = hit.snippet
            origin: PreparedContextOrigin = hit.artifact_ref
            citation = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            body = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                citation = {"artifact": origin.model_dump(mode="json")}
                body = "\n\n".join(f"{label.capitalize()}: {value}" for label, value in content.items())
            topic_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="topic-memory",
                    citation=citation,
                    content=body,
                    truncated=False,
                )
            )
        return tuple(topic_entries)

    def _experience_entries(
        self,
        hits: Sequence[ExperienceSearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        experience_entries: list[_PreparedContextEntry] = []
        seen_experiences: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != Experience.family:
                raise PreparedContextInvariantError("experience-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_experiences:
                continue
            seen_experiences.add(identity)
            origin: PreparedContextOrigin = hit.artifact_ref
            rendered_citation: dict[str, object] = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                rendered_citation = {"artifact": origin.model_dump(mode="json")}
            experience_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="experience",
                    citation=rendered_citation,
                    content=render_experience(hit.content),
                    truncated=False,
                )
            )
        return tuple(experience_entries)

    def _fit_entries(
        self,
        request: PrepareContextRequest,
        memory_entries: Sequence[_PreparedContextEntry],
        topic_memory_entries: Sequence[_PreparedContextEntry],
        experience_entries: Sequence[_PreparedContextEntry],
    ) -> tuple[tuple[_PreparedContextEntry, ...], PreparedContextOmissions]:
        entries: list[_PreparedContextEntry] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for candidate in _interleave(memory_entries, topic_memory_entries, experience_entries):
            if len(entries) >= self.entry_limit:
                break
            fit = self._fit_entry(
                entries,
                origin=candidate.origin,
                kind=candidate.kind,
                citation=candidate.citation,
                text=candidate.content,
                max_bytes=request.max_bytes,
            )
            if fit.entry is None:
                dropped_below_min_bytes += int(fit.dropped_below_min_bytes)
                dropped_no_fitting_truncation += int(fit.dropped_no_fitting_truncation)
                continue
            entries.append(fit.entry)
            if fit.entry.truncated:
                truncated_items += 1
        return tuple(entries), PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )

    def _fit_entry(
        self,
        entries: Sequence[_PreparedContextEntry],
        *,
        origin: PreparedContextOrigin,
        kind: str,
        citation: dict[str, object],
        text: str,
        max_bytes: int,
    ) -> _EntryFit:
        """Fit one entry into the remaining budget, reporting why it was dropped if it was.

        The two ``None`` paths of the original code are kept apart: ``below-min-bytes`` means a
        shorter rendering exists and fits but is too short to deliver honestly, while
        ``no-fitting-truncation`` means no shortened rendering fitted at all.
        """

        source_bytes = len(text.encode("utf-8"))
        entry_budget = min(source_bytes, self.max_entry_content_bytes)
        candidate = _PreparedContextEntry(
            origin=origin,
            kind=kind,
            citation=citation,
            content=text if source_bytes <= entry_budget else _truncate_utf8(text, entry_budget),
            truncated=source_bytes > entry_budget,
        )
        if _rendered_bytes((*entries, candidate)) <= max_bytes:
            return _EntryFit(entry=candidate)
        if source_bytes < _MIN_TRUNCATED_CONTENT_BYTES:
            return _EntryFit(dropped_below_min_bytes=True)

        lower = _MIN_TRUNCATED_CONTENT_BYTES
        upper = min(entry_budget, source_bytes - 1)
        best: _PreparedContextEntry | None = None
        while lower <= upper:
            byte_budget = (lower + upper) // 2
            candidate = _PreparedContextEntry(
                origin=origin,
                kind=kind,
                citation=citation,
                content=_truncate_utf8(text, byte_budget),
                truncated=True,
            )
            if len(candidate.content.encode("utf-8")) < _MIN_TRUNCATED_CONTENT_BYTES:
                lower = byte_budget + 1
                continue
            if _rendered_bytes((*entries, candidate)) <= max_bytes:
                best = candidate
                lower = byte_budget + 1
            else:
                upper = byte_budget - 1
        if best is None:
            return _EntryFit(dropped_no_fitting_truncation=True)
        return _EntryFit(entry=best)


def _interleave(
    memory: Sequence[_PreparedContextEntry],
    topics: Sequence[_PreparedContextEntry],
    experiences: Sequence[_PreparedContextEntry],
) -> tuple[_PreparedContextEntry, ...]:
    """Preserve each family's rank while alternating Memory, Topic, Experience."""

    ordered: list[_PreparedContextEntry] = []
    for index in range(max(len(memory), len(topics), len(experiences))):
        if index < len(memory):
            ordered.append(memory[index])
        if index < len(topics):
            ordered.append(topics[index])
        if index < len(experiences):
            ordered.append(experiences[index])
    return tuple(ordered)


def _interleave_groups(groups: Sequence[Sequence[_Item]]) -> tuple[_Item, ...]:
    ordered: list[_Item] = []
    for index in range(max((len(group) for group in groups), default=0)):
        ordered.extend(group[index] for group in groups if index < len(group))
    return tuple(ordered)


_EVIDENCE_TOKEN_RE = re.compile(r"[\u3400-\u9fff]+|[^\W\d_\u3400-\u9fff]+|\d+", re.UNICODE)
_EVIDENCE_SENTENCE_END = frozenset(".!?\u3002\uff01\uff1f")
_EVIDENCE_STOP_WORDS = frozenset([
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "hers",
    "him",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "may",
    "might",
    "of",
    "on",
    "or",
    "our",
    "ours",
    "she",
    "should",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "these",
    "they",
    "this",
    "those",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
    "would",
    "you",
    "your",
])
_EVIDENCE_ABBREVIATIONS = frozenset([
    "al",
    "approx",
    "cf",
    "dr",
    "e.g",
    "etc",
    "fig",
    "figs",
    "i.e",
    "mr",
    "mrs",
    "ms",
    "no",
    "prof",
    "ref",
    "refs",
    "vs",
])
_FRAGMENT_JSON_PREFIX = '{"trust":"untrusted_history","items":['
_FRAGMENT_JSON_SUFFIX = "]}"


def _evidence_sentences(
    memory_candidates: Sequence[PreparedMemoryCandidates],
    current_scope_id: str | None,
    max_bytes: int,
) -> list[_EvidenceSentence]:
    """Keep source text bound to its exact address throughout selection."""

    sentences = []
    seen: set[tuple[str, str, str, int, str, str]] = set()
    for candidates in memory_candidates:
        scope = candidates.scope_id or current_scope_id or ""
        for hit in candidates.hits:
            if candidates.memory_ref is None or hit.memory_ref != candidates.memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")
            if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            identity = (
                scope,
                hit.memory_ref.family,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            if identity in seen:
                continue
            seen.add(identity)
            address = MemoryEntryAddress(
                ArtifactAddress(scope_id=scope, artifact=hit.memory_ref), hit.entry_id, hit.entry_version_id
            )
            escaped_sizes = {
                char: len(json.dumps(char, ensure_ascii=False).encode("utf-8")) - 2 for char in set(hit.text)
            }
            escaped_bytes = [0]
            for char in hit.text:
                escaped_bytes.append(escaped_bytes[-1] + escaped_sizes[char])
            source = _EvidenceText(
                address, hit.text, tuple(escaped_bytes), len(_fragment_item(address, "", 0, 1).encode("utf-8")) - 2
            )
            for start, end in _sentence_spans(hit.text):
                for left, right in _budgeted_evidence_spans(source, start, end, max_bytes):
                    sentences.append(_EvidenceSentence(source, left, right, _evidence_tokens(hit.text[left:right])))
    return sentences


def _budgeted_evidence_spans(
    source: _EvidenceText,
    start: int,
    end: int,
    max_bytes: int,
) -> list[tuple[int, int]]:
    """Retain whole sentences when possible; window oversized unpunctuated bodies."""

    def fits(left: int, right: int) -> bool:
        return _FRAGMENT_OVERHEAD_BYTES + source.item_bytes(left, right) <= max_bytes

    if fits(start, end):
        return [(start, end)]
    spans = []
    while start < end:
        low, high = start, end
        while low < high:
            middle = (low + high + 1) // 2
            if fits(start, middle):
                low = middle
            else:
                high = middle - 1
        if low == start:
            break
        spans.append(_trimmed_span(source.body, start, low))
        if low == end:
            break
        # Half-window overlap keeps local phrases reachable at a window edge.
        start += max(1, (low - start) // 2)
    return [(left, right) for left, right in spans if left < right]


def _next_evidence_sentence(
    sentences: Sequence[_EvidenceSentence],
    scores: Sequence[float],
    selected: set[int],
    fragments: Sequence[PreparedContextFragment],
    current_bytes: int,
    max_bytes: int,
    fragment_limit: int,
) -> tuple[int, int] | None:
    """Choose the best fitting marginal evidence addition using rendered byte cost."""

    best_value = 0.0
    best = None
    for index, sentence in enumerate(sentences):
        if index in selected:
            continue
        score = scores[index]
        # Supporting sentences can refer back without repeating query terms.
        if score <= 0:
            neighbors = [
                other
                for other in (index - 1, index + 1)
                if other in selected and sentences[other].source is sentence.source
            ]
            score = max((scores[other] * 0.15 for other in neighbors), default=0.0)
        if score <= 0:
            continue
        rendered_bytes = _evidence_addition_bytes(sentence, fragments, current_bytes, fragment_limit)
        if rendered_bytes is None or rendered_bytes > max_bytes:
            continue
        # A neighboring span pays only for new text, not a second citation.
        value = score / max(1, rendered_bytes - current_bytes) ** 0.6
        if value > best_value:
            best_value = value
            best = (index, rendered_bytes)
    return best


def _evidence_addition_bytes(
    sentence: _EvidenceSentence,
    fragments: Sequence[PreparedContextFragment],
    current_bytes: int,
    fragment_limit: int,
) -> int | None:
    """Price a local union exactly without serializing unchanged fragments."""

    source = sentence.source
    start, end = sentence.start, sentence.end
    removed_bytes = 0
    removed_count = 0
    for fragment in fragments:
        # Sources are deduplicated by complete address before their spans exist;
        # every fragment for this source reuses the same address object.
        if fragment.address is not source.address:
            continue
        if fragment.start <= start and end <= fragment.end:
            return None
        if (fragment.end < start and source.body[fragment.end : start].strip()) or (
            end < fragment.start and source.body[end : fragment.start].strip()
        ):
            continue
        start, end = min(start, fragment.start), max(end, fragment.end)
        removed_bytes += source.item_bytes(fragment.start, fragment.end)
        removed_count += 1
    count = len(fragments) - removed_count + 1
    if count > fragment_limit:
        return None
    commas = max(0, count - 1) - max(0, len(fragments) - 1)
    return current_bytes + source.item_bytes(start, end) - removed_bytes + commas


def _stem_evidence_token(token: str) -> str:
    """Conservatively fold common English inflections for offline matching."""

    if not token.isascii() or not token.isalpha():
        return token
    if token.endswith("ies") and len(token) > 4:
        token = token[:-3] + "y"
    elif token.endswith("s") and not token.endswith(("ss", "us", "is")) and len(token) > 4:
        token = token[:-1]
    for suffix, replacement in (("ation", "at"), ("ing", ""), ("ed", "")):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            token = token[: -len(suffix)] + replacement
            break
    if token.endswith("e") and len(token) > 5:
        token = token[:-1]
    return token


def _evidence_tokens(value: str) -> dict[str, int]:
    """Normalize query terms without changing any delivered evidence text."""

    counts: dict[str, int] = {}
    for token in _EVIDENCE_TOKEN_RE.findall(value.casefold()):
        if token in _EVIDENCE_STOP_WORDS:
            continue
        if any("\u3400" <= char <= "\u9fff" for char in token):
            # Chinese phrases do not carry whitespace word boundaries. Character
            # bigrams match query phrases inside longer literal sentences.
            terms = [token[index : index + 2] for index in range(len(token) - 1)] or [token]
            for term in terms:
                counts[term] = counts.get(term, 0) + 1
            continue
        token = _stem_evidence_token(token)
        counts[token] = counts.get(token, 0) + 1
    return counts


def _evidence_scores(sentences: Sequence[_EvidenceSentence], query: dict[str, int]) -> list[float]:
    """Weight query coverage by within-request term rarity and sentence length."""

    weights = {
        term: math.log(1 + len(sentences) / (1 + sum(term in item.terms for item in sentences))) for term in query
    }
    average_length = sum(sum(item.terms.values()) for item in sentences) / max(1, len(sentences))
    scores = []
    for sentence in sentences:
        length = sum(sentence.terms.values())
        coverage = sum(weights[term] for term in query if term in sentence.terms)
        relevance = sum(weights[term] * min(2, sentence.terms.get(term, 0)) for term in query)
        scores.append(
            relevance * (1 + coverage / max(1, sum(weights.values()))) / (0.75 + 0.25 * length / max(1, average_length))
        )
    return scores


def _evidence_fragments(
    sentences: Sequence[_EvidenceSentence],
    selected: set[int],
) -> tuple[PreparedContextFragment, ...]:
    """Coalesce neighboring selected sentences while preserving their literal gap."""

    fragments: list[PreparedContextFragment] = []
    for index in sorted(selected):
        sentence = sentences[index]
        source = sentence.source
        if fragments and fragments[-1].address is source.address:
            previous = fragments[-1]
            if not source.body[previous.end : sentence.start].strip():
                fragments[-1] = PreparedContextFragment(
                    address=source.address,
                    start=previous.start,
                    end=max(previous.end, sentence.end),
                    text=source.body[previous.start : max(previous.end, sentence.end)],
                )
                continue
        fragments.append(
            PreparedContextFragment(
                source.address, sentence.start, sentence.end, source.body[sentence.start : sentence.end]
            )
        )
    return tuple(fragments)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split text into trimmed sentence spans carrying original character offsets."""

    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char not in _EVIDENCE_SENTENCE_END:
            continue
        if char == ".":
            # Decimal points, identifiers and dotted abbreviations are not ends
            # of evidence sentences (e.g. "P = 0.0004" or "et al. reported").
            if index + 1 < len(text) and not text[index + 1].isspace():
                continue
            word = re.search(r"([\w.]+)$", text[:index])
            if word and (
                word.group(1).casefold() in _EVIDENCE_ABBREVIATIONS
                or (len(word.group(1)) == 1 and word.group(1).isalpha())
            ):
                continue
        spans.append(_trimmed_span(text, start, index + 1))
        start = index + 1
    spans.append(_trimmed_span(text, start, len(text)))
    return [(start, end) for start, end in spans if start < end]


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _fragment_item(address: MemoryEntryAddress, text: str, start: int, end: int) -> str:
    """Render one fragment item exactly as it appears inside the envelope."""

    item = {
        "citation": {
            "scope_id": address.memory.scope_id,
            "memory_ref": address.memory.artifact.model_dump(mode="json"),
            "entry_id": address.entry_id,
            "entry_version_id": address.entry_version_id,
        },
        "content": text,
        "truncated": False,
        "start": start,
        "end": end,
    }
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def _render_fragment_items(items: Sequence[str]) -> str:
    """Render selected fragment items in the standard prepared-context envelope."""

    encoded = _FRAGMENT_JSON_PREFIX + ",".join(items) + _FRAGMENT_JSON_SUFFIX
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


_FRAGMENT_OVERHEAD_BYTES = len(_render_fragment_items([]).encode("utf-8"))


def _render(entries: Sequence[_PreparedContextEntry]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [_render_entry(entry) for entry in entries],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _text_item(entry: _PreparedContextEntry) -> ContextTextItem:
    origin = entry.origin
    if isinstance(origin, MemoryEntryAddress):
        return ContextTextItem(
            artifact=origin.memory,
            content=entry.content,
            recall_rank=0,
            entry_id=origin.entry_id,
            entry_version_id=origin.entry_version_id,
        )
    if isinstance(origin, ArtifactAddress):
        return ContextTextItem(artifact=origin, content=entry.content, recall_rank=0)
    raise PreparedContextInvariantError("text-scope-missing")


def _render_entry(entry: _PreparedContextEntry) -> dict[str, object]:
    rendered: dict[str, object] = {
        "citation": entry.citation,
        "content": entry.content,
        "truncated": entry.truncated,
    }
    if entry.kind != "memory":
        rendered["kind"] = entry.kind
    return rendered


def _rendered_bytes(entries: Sequence[_PreparedContextEntry]) -> int:
    return len(_render(entries).encode("utf-8"))


def _truncate_utf8(text: str, byte_budget: int) -> str:
    prefix_budget = byte_budget - len(_ELLIPSIS.encode("utf-8"))
    encoded_prefix = text.encode("utf-8")[:prefix_budget]
    return f"{encoded_prefix.decode('utf-8', errors='ignore')}{_ELLIPSIS}"
