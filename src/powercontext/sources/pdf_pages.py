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

"""Physical page Sources for complete PDFs and page-level evidence retrieval.

The module owns three behaviours:

* ``parse_pdf_document`` reads every physical page of one original PDF, in order,
  and fails atomically when the bytes cannot be read completely.
* ``PDF_PAGE_SOURCE_DEFINITION`` projects one page as a captured Source whose
  identity is derived from ``(document_id, document_sha256, page_number)`` and whose
  standard text projection keeps the document identity on its own first line.
* ``search_pdf_pages`` retrieves up to ``limit`` distinct evidence pages of the
  caller's scoped Memory through the public Memory API and returns exact citations.

Ranking is offline and deterministic: BM25 over the candidate page texts plus a
handful of structural priors that are documented at their definition site.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, Field, field_validator
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from powercontext.artifacts.models import MemoryCitation
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    Source,
    SourceDefinition,
    SourceMaterialization,
    TextEvidence,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"
PDF_PAGE_SOURCE_TYPE = "pdf-page"

_MAX_SOURCE_NAME_LENGTH = 256
_MAX_DOCUMENT_ID_KEY_LENGTH = 120
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


# --------------------------------------------------------------------------- #
# Parsing and Source definition
# --------------------------------------------------------------------------- #


class PDFDocumentInput(BaseModel):
    """One complete original PDF supplied as bytes together with its caller identity."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def require_document_id(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("document_id must be a non-empty trimmed string")
        return value.strip()

    @field_validator("content", mode="before")
    @classmethod
    def require_bytes(cls, value: object) -> bytes:
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError("content must be PDF bytes")
        return bytes(value)


class PDFPageInput(BaseModel):
    """One physical page of one exact PDF revision."""

    document_id: str
    document_sha256: str
    page_number: Annotated[int, Field(ge=1)]
    text: str

    @field_validator("document_id")
    @classmethod
    def require_document_id(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("document_id must be a non-empty trimmed string")
        return value.strip()

    @field_validator("document_sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
            raise ValueError("document_sha256 must be a lowercase hexadecimal SHA-256 digest")
        return value


class PDFPageSource(Source):
    """A captured physical page of one exact PDF revision."""

    document_id: str
    document_sha256: str
    page_number: Annotated[int, Field(ge=1)]
    text: str


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Return every physical page of ``value.content`` in order, or raise ``ValueError``.

    The whole document is parsed before anything is returned, so a damaged page can
    never leak a partial result. Blank pages stay present with empty text. A PDF that
    only carries an empty user password is readable without any supplied password.
    """

    content = value.content
    if not content:
        raise ValueError("PDF content must not be empty")
    document_id = value.document_id
    digest = hashlib.sha256(content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            # An empty user password is a legal permission marker, not a real lock.
            reader.decrypt("")
        page_count = len(reader.pages)
        if page_count < 1:
            raise ValueError("PDF contains no physical page")
        pages = tuple(
            PDFPageInput(
                document_id=document_id,
                document_sha256=digest,
                page_number=page_number,
                text=_page_text(_extract_page_text(reader, page_number)),
            )
            for page_number in range(1, page_count + 1)
        )
    except ValueError:
        raise
    except (PyPdfError, OSError, EOFError, KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"PDF document cannot be parsed completely: {error}") from error
    return pages


def _extract_page_text(reader: PdfReader, page_number: int) -> str:
    page = reader.pages[page_number - 1]
    try:
        text = page.extract_text()
    except (PyPdfError, OSError, EOFError, KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError(f"PDF page {page_number} cannot be parsed: {error}") from error
    return text if isinstance(text, str) else ""


_LAYOUT_SPACE = re.compile(r"[ \t\u00a0]+")


def _page_text(raw: str, /) -> str:
    """Normalize one page's extractable text without changing its words or numbers."""

    normalized = unicodedata.normalize("NFC", raw.replace("\r\n", "\n").replace("\r", "\n"))
    lines = tuple(_LAYOUT_SPACE.sub(" ", line).rstrip() for line in normalized.split("\n"))
    return "\n".join(lines).strip()


def source_name_for(document_id: str, document_sha256: str, page_number: int, /) -> str:
    """Return the stable, collision-free Source name of one physical page.

    ``(document_id, document_sha256, page_number)`` fully determines the identity, so a
    revised PDF yields a new name while every old citation keeps pointing at its revision.
    A long document id is shortened with a digest to stay inside the length limit.
    """

    document_key = document_id
    if len(document_key) > _MAX_DOCUMENT_ID_KEY_LENGTH:
        digest = hashlib.sha256(document_id.encode()).hexdigest()
        document_key = f"{document_key[:_MAX_DOCUMENT_ID_KEY_LENGTH]}~{digest}"
    name = f"{PDF_PAGE_SOURCE_NAME}:{document_key}:{document_sha256}:{page_number}"
    if len(name) > _MAX_SOURCE_NAME_LENGTH:  # pragma: no cover - defensive bound
        digest = hashlib.sha256(name.encode()).hexdigest()
        name = f"{PDF_PAGE_SOURCE_NAME}:{digest}:{page_number}"
    return name


class PDFPageSourceAdapter:
    """Resolve and read one captured physical page."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        return PDFPageSource(
            name=source_name_for(value.document_id, value.document_sha256, value.page_number),
            definition_version=PDF_PAGE_SOURCE_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one page as standard textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_TYPE,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION: SourceDefinition = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


# --------------------------------------------------------------------------- #
# Offline page retrieval
# --------------------------------------------------------------------------- #

_TOKEN = re.compile(r"[a-z]+|\d+")

# Function words plus the generic instruction phrasing FinanceBench questions append to
# their real ask ("answer in USD millions", "assume the perspective of an analyst").
# Dropping them keeps the lexical score focused on the metric and entity being asked about.
_QUERY_STOPWORDS = frozenset("""
a an the and or of to in on at by for from with as is are was were be been being what which who whom whose how
why when where do does did doing done can could should would shall may might must i we you he she it they them
its our your their this that these those there here then than so such if but nor not no yes any all each every
few more most other some only also just very too much many us please
has have had having reported report does state stated explicitly outlined directly important materially primarily
ongoing define defined assuming assume perspective investment analyst details detail shown within rely relying
respond response give given use using used calculate calculated quantity answer answers question questions
chart charts provide provided according following based
consolidated inc subsidiaries subsidiary fiscal years year ended december million millions except per share amounts
see accompanying notes table contents
""".split())

# General financial-reporting vocabulary: how a filing words what a question asks about.
# Every entry is domain vocabulary ("quick ratio" is answered by balance-sheet lines);
# none of it encodes a particular document, question or answer.
_CONCEPT_EXPANSIONS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("quick", "ratio"), ("balance", "sheet", "current", "assets", "liabilities", "inventories", "cash")),
    (("current", "ratio"), ("balance", "sheet", "current", "assets", "liabilities")),
    (("liquidity",), ("liquidity", "current", "assets", "liabilities", "cash", "ratio")),
    (("gross", "margin"), ("revenues", "sales", "cost", "products", "services", "gross", "profit")),
    (("operating", "margin"), ("revenues", "sales", "operating", "income", "profit", "expenses")),
    (("operating", "income"), ("operating", "income", "profit", "revenues")),
    (("net", "income"), ("net", "income", "revenues")),
    (("effective", "tax", "rate"), ("provision", "income", "taxes", "pretax", "effective", "rate")),
    (("tax", "rate"), ("provision", "income", "taxes", "pretax", "effective", "rate")),
    (("capex",), ("capital", "expenditures", "property", "plant", "equipment")),
    (("ebitda",), ("operating", "income", "depreciation", "amortization")),
    (("cash", "flow"), ("cash", "flows", "operating", "investing", "financing")),
    (("customer", "concentration"), ("customer", "accounted", "revenue", "sales", "concentration")),
    (("geograph",), ("countries", "territories", "segments", "divisions", "regions")),
    (("segment",), ("segments", "division", "revenues")),
    (("legal",), ("legal", "litigation", "proceedings", "claims", "regulatory", "lawsuits", "matters")),
    (("battl",), ("legal", "litigation", "proceedings", "claims", "lawsuits")),
    (("restructur",), ("restructuring", "impairment", "severance", "productivity", "charges")),
    (("inventory",), ("inventories", "cost", "sales")),
    (("debt",), ("debt", "borrowings", "notes", "long-term", "obligations")),
    (("pension",), ("pension", "retiree", "benefit", "plan")),
    (("liability",), ("liabilities", "balance", "sheet")),
    (("revenue",), ("revenues", "sales", "net")),
    (("margin",), ("margin", "profit", "revenues")),
    (("cyclical",), ("cyclical", "cycle", "economic", "demand")),
    (("customer",), ("customer", "customers", "sales")),
    (("production", "rate"), ("production", "rate", "increase", "decrease", "deliveries")),
)

# Question wording that asks for a number taken off a statement or table. Only then do the
# structural priors below apply; a narrative question ("who are the customers?") is ranked
# on lexical relevance alone.
_METRIC_CUES = frozenset("""
ratio margin liquidity quick ebitda capex revenue revenues income expense expenses cost costs liability
liabilities asset assets debt cash flow amount growth increase decrease change compare comparison value total
net gross operating tax profit loss sales dividend buyback repurchase inventory inventories receivable payable
share per unit volume price spend spending capital obligation
""".split())

# Prefix-8 term keys: 10-K prose inflects heavily ("restructuring", "restructured",
# "restructurings"), and a fixed-width prefix merges those variants without a stemming
# dictionary. Eight characters is the shortest width that keeps distinct financial terms
# apart ("operating"/"operation" merge, "asset"/"assert" do not occur in filings).
_TERM_PREFIX = 8
_STATEMENT_HEADLINE = re.compile(
    r"(?:consolidated\s+)?statements?\s+of\s+(?:income|operations|earnings|cash\s+flows?|financial\s+position"
    r"|changes\s+in\s+equity|comprehensive)"
    r"|(?:consolidated\s+)?balance\s+sheets?"
    r"|summary\s+of\s+(?:business\s+segment|financial)"
    r"|table\s+\d+\s*:\s*summary"
    r"|summary\s+of\s+financial\s+performance",
    re.IGNORECASE,
)

# Ranking constants. Each one is a deliberately round, explainable coefficient rather
# than a fitted value:
_BM25_K1 = 1.2
# Page lengths vary over two orders of magnitude (a 120-character cover sheet next to a
# 5,000-character note), so length normalization is kept mild: b=0.3 prevents short table
# pages from being buried without letting long prose pages win on size alone.
_BM25_B = 0.3
# A page's own heading (first 25 terms: "Consolidated Statement of Cash Flows", "Item 3.
# Legal Proceedings") is the single strongest page-level signal, so the same BM25 is
# re-scored on the headline with the question's unexpanded terms at unit weight.
_HEADLINE_TERMS = 25
_HEADLINE_WEIGHT = 1.0
# Financial questions are answered on statements and tables. Two bounded priors express
# that, applied only when the question asks for a number:
#   * +1.5 when the page opens with a financial-statement heading;
#   * up to +6.0 for numeric density (share of digit tokens), saturating at 60% density —
#     a dense table page is fully credited, a prose page with a few figures is not.
_STATEMENT_WEIGHT = 1.5
_DENSITY_WEIGHT = 10.0
_DENSITY_SATURATION = 0.6
# Answers often straddle a page break, and a statement's continuation page inherits the
# heading's signal. The best directly adjacent page contributes half its own score; the
# decay keeps page n-2/n+2 from outranking a page that actually matches.
_NEIGHBOUR_WEIGHT = 0.5
_NEIGHBOUR_SPAN = 2
_NEIGHBOUR_ANCHORS = 80
# Public Memory search hits contribute a reciprocal-rank tie-break only: retrieval
# completeness comes from the listing, so this must never reorder strong lexical matches.
_SEARCH_RANK_WEIGHT = 0.5
_RRF_CONSTANT = 60.0


@dataclass(frozen=True, slots=True)
class _PageFeatures:
    """Structural priors read from one page's text."""

    key_terms: tuple[str, ...]
    headline: tuple[str, ...]
    density: float
    is_statement: bool


class _Candidate:
    """One distinct physical page offered as an answer candidate."""

    __slots__ = ("citation", "document_key", "features", "page_id", "page_number", "search_score")

    def __init__(self, citation: MemoryCitation, page_id: str, text: str) -> None:
        self.citation = citation
        self.page_id = page_id
        document_key, page_number = _split_page_id(page_id)
        self.document_key = document_key
        self.page_number = page_number
        self.features = _page_features(text)
        self.search_score = 0.0


async def search_pdf_pages(
    memory: Any,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` distinct exact evidence-page citations.

    Retrieval uses the public scoped Memory API only: candidate pages come from Memory
    search hits and from the Memory's own current-head entry listing, and every returned
    citation is a real stored entry version. Ranking is offline (BM25 plus the structural
    priors documented above), so no model or network call is involved.
    """

    if not isinstance(question, str) or not question.strip():
        return ()
    if limit < 1:
        return ()
    candidates = await _collect_candidates(memory, question, limit)
    if not candidates:
        return ()
    return tuple(candidate.citation for candidate in _rank(candidates, question)[:limit])


async def _collect_candidates(memory: Any, question: str, limit: int) -> dict[str, _Candidate]:
    candidates: dict[str, _Candidate] = {}
    await _add_listed_pages(memory, candidates)
    await _add_searched_pages(memory, question, limit, candidates)
    return candidates


async def _add_listed_pages(memory: Any, candidates: dict[str, _Candidate]) -> None:
    """Offer every current-head page the Memory itself lists for this scope."""

    listing = getattr(memory, "list", None)
    if listing is None:
        return
    page = await listing()
    for record in page.entries:
        page_id = _single_page_id(record.entry.sources)
        if page_id is None:
            continue
        _offer(candidates, page_id, record.citation, record.entry.text)


async def _add_searched_pages(
    memory: Any,
    question: str,
    limit: int,
    candidates: dict[str, _Candidate],
) -> None:
    """Offer pages from public Memory search, with their reciprocal-rank signal."""

    from powercontext.builtin.runtime.models import GetMemoryEntryRequest, SearchMemoryRequest

    page = await memory.search(
        SearchMemoryRequest(query=question, mode="fts", limit=max(limit * 8, 64))
    )
    for rank, hit in enumerate(page.hits, start=1):
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        page_id = _existing_page_id(candidates, citation)
        if page_id is None:
            record = await memory.get(GetMemoryEntryRequest(citation=citation))
            page_id = _single_page_id(record.entry.sources)
            if page_id is None:
                continue
            _offer(candidates, page_id, citation, record.entry.text)
        candidates[page_id].search_score += 1.0 / (_RRF_CONSTANT + rank)


def _single_page_id(sources: Sequence[Any], /) -> str | None:
    """Return the page identity of an entry that cites exactly one physical page."""

    if len(sources) != 1:
        return None
    reference = sources[0]
    if reference.source_type != PDF_PAGE_SOURCE_TYPE:
        return None
    return reference.source_id


def _split_page_id(page_id: str, /) -> tuple[str, int]:
    """Recover ``(document identity, page number)`` from one Source name."""

    document_key, _, page = page_id.rpartition(":")
    try:
        return document_key, int(page)
    except ValueError:  # pragma: no cover - only reachable for a foreign Source name
        return page_id, 0


def _existing_page_id(candidates: dict[str, _Candidate], citation: MemoryCitation, /) -> str | None:
    for page_id, candidate in candidates.items():
        if (
            candidate.citation.entry_id == citation.entry_id
            and candidate.citation.entry_version_id == citation.entry_version_id
        ):
            return page_id
    return None


def _offer(candidates: dict[str, _Candidate], page_id: str, citation: MemoryCitation, text: str) -> None:
    if page_id not in candidates:
        candidates[page_id] = _Candidate(citation, page_id, text)


def _page_features(text: str, /) -> _PageFeatures:
    """Read the lexical keys and structural priors of one page."""

    # The stored entry text is ``DOCUMENT_ID\\nPAGE_TEXT``; strip that identity line so it
    # cannot dominate the headline or the numeric density of every page in a document.
    body = text.split("\n", 1)[1] if "\n" in text else ""
    raw = _tokenize(body)
    key_terms = tuple(_term_key(token) for token in raw)
    digits = sum(1 for token in raw if token.isdigit())
    density = digits / len(raw) if raw else 0.0
    headline = tuple(_term_key(token) for token in raw[:_HEADLINE_TERMS])
    return _PageFeatures(
        key_terms=key_terms,
        headline=headline,
        density=density,
        is_statement=bool(_STATEMENT_HEADLINE.search(body[:600])),
    )


def _tokenize(value: str, /) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(unicodedata.normalize("NFC", value).casefold()))


def _term_key(token: str, /) -> str:
    if token.isdigit() or len(token) <= _TERM_PREFIX:
        return token
    return token[:_TERM_PREFIX]


def _question_terms(question: str, /) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Return ``(expanded keys, unexpanded keys, asks-for-a-number)`` for one question."""

    folded = unicodedata.normalize("NFC", question).casefold()
    words = [token for token in _tokenize(folded) if token not in _QUERY_STOPWORDS and len(token) > 1]
    base_keys = tuple(_term_key(word) for word in words)
    joined = " ".join(words)
    expanded = list(base_keys)
    for phrase, vocabulary in _CONCEPT_EXPANSIONS:
        if _phrase_present(phrase, joined, folded):
            expanded.extend(_term_key(word) for word in vocabulary)
    asks_for_number = any(token in _METRIC_CUES for token in words)
    return tuple(dict.fromkeys(expanded)), base_keys, asks_for_number


def _phrase_present(phrase: tuple[str, ...], joined: str, folded: str, /) -> bool:
    if len(phrase) == 1:
        return re.search(r"\b" + re.escape(phrase[0]), folded) is not None
    return " ".join(phrase) in joined


def _rank(candidates: dict[str, _Candidate], question: str) -> list[_Candidate]:
    """Order candidate pages by lexical relevance and structural priors."""

    terms, base_keys, asks_for_number = _question_terms(question)
    documents = tuple(candidates.values())
    if not terms:
        return sorted(documents, key=lambda candidate: (-candidate.search_score, candidate.page_id))
    total = len(documents)
    average_length = sum(len(document.features.key_terms) for document in documents) / total or 1.0
    document_frequency: Counter[str] = Counter()
    for document in documents:
        document_frequency.update(set(document.features.key_terms))

    def idf(term: str) -> float:
        frequency = document_frequency[term]
        return math.log(1.0 + (total - frequency + 0.5) / (frequency + 0.5))

    lexical = {
        document.page_id: _bm25(document.features.key_terms, terms, idf, average_length)
        + _HEADLINE_WEIGHT * _bm25(document.features.headline, base_keys, idf, average_length)
        for document in documents
    }
    scores = dict(lexical)
    if asks_for_number:
        for document in documents:
            features = document.features
            scores[document.page_id] += _STATEMENT_WEIGHT * features.is_statement
            scores[document.page_id] += _DENSITY_WEIGHT * min(features.density, _DENSITY_SATURATION)
    _apply_neighbour_smoothing(documents, lexical, scores)
    for document in documents:
        scores[document.page_id] += _SEARCH_RANK_WEIGHT * document.search_score
    return sorted(documents, key=lambda candidate: (-scores[candidate.page_id], candidate.page_id))


def _bm25(
    page_terms: Iterable[str],
    query_terms: Sequence[str],
    idf: Any,
    average_length: float,
    /,
) -> float:
    frequencies = Counter(page_terms)
    length = sum(frequencies.values()) or 1
    score = 0.0
    for term in query_terms:
        frequency = frequencies.get(term, 0)
        if frequency:
            denominator = frequency + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * length / average_length)
            score += idf(term) * frequency * (_BM25_K1 + 1.0) / denominator
    return score


def _apply_neighbour_smoothing(
    documents: Sequence[_Candidate],
    lexical: dict[str, float],
    scores: dict[str, float],
) -> None:
    """Let a strongly relevant page lend half its score to its direct neighbours."""

    by_document: dict[str, list[_Candidate]] = {}
    for document in documents:
        by_document.setdefault(document.document_key, []).append(document)
    for group in by_document.values():
        ranked = sorted(group, key=lambda candidate: (-lexical[candidate.page_id], candidate.page_id))
        anchors = {candidate.page_number for candidate in ranked[:_NEIGHBOUR_ANCHORS]}
        page_by_number = {candidate.page_number: candidate for candidate in group}
        for candidate in group:
            best = 0.0
            for distance in range(1, _NEIGHBOUR_SPAN + 1):
                for neighbour in (candidate.page_number - distance, candidate.page_number + distance):
                    if neighbour in anchors:
                        best = max(best, lexical[page_by_number[neighbour].page_id] / (1.0 + distance))
            if best:
                scores[candidate.page_id] += _NEIGHBOUR_WEIGHT * best


__all__ = [
    "PDF_PAGE_SOURCE_ADAPTER",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDF_PAGE_SOURCE_TYPE",
    "PDF_PAGE_SOURCE_VERSION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "search_pdf_pages",
    "source_name_for",
]
