"""Literal evidence regressions derived from the published selection contract."""

from __future__ import annotations

import json

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates

REF = ArtifactRef(family="memory", artifact_id="shared-memory", revision=1)


def candidates(scope: str, text: str, *, ref: ArtifactRef = REF, version: str = "v1") -> PreparedMemoryCandidates:
    return PreparedMemoryCandidates(
        scope_id=scope,
        memory_ref=ref,
        hits=(
            MemoryHit(
                memory_ref=ref, entry_id="entry", entry_version_id=version, text=text, score=1.0, matched_by=("fts",)
            ),
        ),
    )


def select(query: str, groups: tuple[PreparedMemoryCandidates, ...], budget: int = 1800):
    result = PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True),
        current_scope_id="scope-a",
        memory_candidates=groups,
    )
    content = result.context.content or ""
    assert result.context.content_bytes == len(content.encode("utf-8")) <= budget
    if content:
        items = json.loads(content.splitlines()[-2])["items"]
        assert len(items) == len(result.fragments) == len(result.origins)
        for item, fragment, origin in zip(items, result.fragments, result.origins, strict=True):
            assert origin == fragment.address
            assert item["content"] == fragment.text
            assert item["start"] == fragment.start and item["end"] == fragment.end
            assert item["citation"]["scope_id"] == fragment.address.memory.scope_id
            assert item["citation"]["memory_ref"] == fragment.address.memory.artifact.model_dump(mode="json")
            assert item["citation"]["entry_version_id"] == fragment.address.entry_version_id
            source = next(
                group.hits[0].text
                for group in groups
                if group.scope_id == fragment.address.memory.scope_id
                and group.memory_ref == fragment.address.memory.artifact
                and group.hits[0].entry_version_id == fragment.address.entry_version_id
            )
            assert type(fragment.start) is int and type(fragment.end) is int
            assert 0 <= fragment.start < fragment.end <= len(source)
            assert fragment.text == source[fragment.start : fragment.end]
    return result


def test_identical_entry_ids_in_distinct_scopes_never_borrow_text():
    select(
        "sensor calibration",
        (
            candidates("scope-a", "Sensor calibration uses cobalt at 17 degrees."),
            candidates("scope-b", "Sensor calibration uses amber at 29 degrees."),
        ),
    )


def test_scientific_decimal_evidence_is_retained_as_a_complete_fact():
    text = "Background information. The cobalt threshold was 1.25 mg and reduced exposure by 37.5%."
    result = select("cobalt threshold reduced exposure", (candidates("scope-a", text),), 1000)
    assert any("1.25 mg and reduced exposure by 37.5%." in fragment.text for fragment in result.fragments)


def test_adjacent_supporting_sentences_fit_with_one_citation():
    first = "Cobalt therapy reduced kinase activation in all treated samples."
    second = "Kinase activation fell by 80 percent after cobalt therapy."
    result = select("cobalt therapy kinase activation", (candidates("scope-a", first + " " + second),), 900)
    assert any(first + " " + second in fragment.text for fragment in result.fragments)


def test_query_terms_outweigh_shared_function_words_and_match_inflections():
    distractor = "The study was performed in the laboratory with the samples and the controls."
    evidence = "Activated platelets accumulate rapidly at injured vessels."
    result = select(
        "The activation of platelet accumulation in vessels", (candidates("scope-a", distractor + " " + evidence),), 850
    )
    assert any(evidence in fragment.text for fragment in result.fragments)


def test_unicode_query_selects_late_original_evidence():
    evidence = "海洋温度传感器在三十七度完成校准。"
    text = "通用管理事项。" * 60 + evidence
    result = select("海洋温度传感器校准", (candidates("scope-a", text),), 850)
    assert any(evidence in fragment.text for fragment in result.fragments)


@pytest.mark.parametrize("budget", [512, 600, 680, 900, 1400])
def test_escaped_unicode_content_and_budget_boundaries(budget):
    text = '海洋温度 sensor calibrated "safe".\nA second sensor records 1.25 degrees. '
    select("海洋 sensor calibrated", (candidates("scope-a", text),), budget)


def test_reported_output_size_can_be_used_as_an_exact_budget():
    groups = (
        candidates("scope-a", "Cobalt sensor calibration uses 17 degrees."),
        candidates(
            "scope-b",
            "Cobalt sensor calibration uses 29 degrees.",
            ref=ArtifactRef(family="memory", artifact_id="other-memory", revision=1),
        ),
    )
    complete = select("cobalt sensor calibration", groups)
    assert len(complete.fragments) == 2
    exact = select("cobalt sensor calibration", groups, complete.context.content_bytes)
    assert exact.context.content == complete.context.content
    assert exact.fragments == complete.fragments
    smaller = select("cobalt sensor calibration", groups, complete.context.content_bytes - 1)
    assert len(smaller.fragments) < len(complete.fragments)


def test_biomedical_letter_number_names_match_hyphenated_spelling():
    evidence = "BRCA-2 mutations increase cancer risk."
    result = select("BRCA2", (candidates("scope-a", evidence),), 850)
    assert any(evidence in fragment.text for fragment in result.fragments)


def test_late_evidence_in_a_long_unpunctuated_body_uses_literal_windows():
    evidence = "The cobalt sensor is calibrated at 37 degrees"
    text = "Administrative background material " * 100 + evidence
    result = select("cobalt sensor calibrated", (candidates("scope-a", text),), 1000)
    assert any(evidence in fragment.text for fragment in result.fragments)
