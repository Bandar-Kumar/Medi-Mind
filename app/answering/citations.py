"""Deterministic citation validator (Phase 5; ARCHITECTURE §9.3; ADR-040). Pure.

The generator (extractive composer or LLM) is never trusted to validate its own citations. Every
answer is checked against two independent things:

- the **supplied sources** (``EvidenceItem``): what the generator was actually given;
- the **patient catalog** (``SourceCatalog``): what the database says exists *for this patient*,
  read through the patient-scoped repositories by the service (page texts, usable fact ids).

Checks, each with a fixed issue code (never text):

==========================  ======================================================================
``unknown_source``          a cited id was not among the supplied sources (fabricated id)
``source_not_found``        a cited source's (document, page) is not a stored page of this patient
                            (fabricated or another patient's document/page)
``excerpt_mismatch``        a source's excerpt is not exactly in the stored page text (altered)
``fact_not_found``          a cited fact id is not a stored fact of this patient
``fact_location_mismatch``  a fact source claims a document/page other than the stored fact's
``source_location_mismatch``  a passage claims a document/page other than its stored chunk's
``unusable_fact``           a cited fact is rejected, needs review or superseded (ADR-023)
``uncited_claim``           a factual or calculation claim (or one naming a value, date or
                            analyte) cites nothing
``unsupported_number``      a number in a claim appears in none of its cited sources (and, for a
                            calculation, is not the difference of two cited same-unit values)
``unsupported_date``        a date in a claim appears in none of its cited sources
``page_mismatch``           "page N" in a claim is not the page of any cited source
``misquoted_text``          text in double quotes is not in any cited source (whitespace-normalized)
``irrelevant_citation``     a claim names an analyte that none of its cited sources is about
``prohibited_content``      the claim diagnoses, advises, prescribes or interprets
``empty_answer``            status answered/partial without claims
``claims_with_insufficient_status``  status insufficient_evidence but claims present
==========================  ======================================================================

Nothing is repaired: an invalid answer is reported as invalid. Distinguishing "invalid citations"
from "insufficient evidence" is the caller's job (``CopilotStatus.VALIDATION_FAILED`` vs
``INSUFFICIENT_EVIDENCE``).

**Limits (documented, not hidden):** number and date grounding is lexical (a number that *does*
appear in a cited source passes even if the sentence misstates what it means), and relevance is
checked through analyte names only. A semantic claim-support check stays an evaluation-time tool
(ARCHITECTURE §9.3).
"""

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.answering.guardrails import prohibited_output
from app.answering.model import CitationIssue, DraftAnswer, EvidenceItem, SourceKind
from app.answering.router import find_analytes

USABLE_STATUSES = frozenset({"validated", "verified", "corrected"})

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_DATE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
_NUMBER = re.compile(r"(?<![\w.])([+\-−]?)(\d+(?:,\d{2,3})*(?:\.\d+)?)(?![\w])")
_PAGE_REF = re.compile(r"\bpage\s+(\d+)\b", re.IGNORECASE)
_QUOTE = re.compile(r"\"([^\"]+)\"|“([^”]+)”")


def _squash(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class CatalogFact:
    document_id: str
    page_number: int
    review_status: str
    superseded: bool


@dataclass(frozen=True)
class SourceCatalog:
    """What exists for the selected patient, read through patient-scoped repositories."""

    pages: Mapping[tuple[str, int], str] = field(repr=False)  # (document id, page) -> stored text
    facts: Mapping[str, CatalogFact] = field(default_factory=dict)
    # chunk id -> (document id, page, exact stored content)
    chunks: Mapping[str, tuple[str, int, str]] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ValidationResult:
    issues: tuple[CitationIssue, ...]
    cited: tuple[str, ...]  # source ids cited by at least one claim, first-use order

    @property
    def valid(self) -> bool:
        return not self.issues

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted({issue.code for issue in self.issues}))


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def numbers_in(text: str) -> set[Decimal]:
    """Absolute values of the numbers in ``text`` (dates removed first)."""
    text = _DMY_DATE.sub(" ", _ISO_DATE.sub(" ", text))
    found = set()
    for _, digits in _NUMBER.findall(text):
        value = _decimal(digits)
        if value is not None:
            found.add(abs(value))
    return found


def dates_in(text: str) -> set[dt.date]:
    found = set()
    for y, m, d in _ISO_DATE.findall(text):
        try:
            found.add(dt.date(int(y), int(m), int(d)))
        except ValueError:
            continue
    for d, m, y in _DMY_DATE.findall(text):
        try:
            found.add(dt.date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return found


def _haystack(item: EvidenceItem) -> str:
    parts = [
        item.excerpt,
        item.filename,
        str(item.page_number),
        item.value or "",
        item.qualitative or "",
        item.unit or "",
        item.value_raw or "",
        item.unit_raw or "",
        item.ref_range_raw or "",
    ]
    return "\n".join(parts)


def _item_dates(item: EvidenceItem) -> set[dt.date]:
    dates = dates_in(item.excerpt)
    for value in (item.effective_date, item.document_date):
        if value is not None:
            dates.add(value)
    return dates


def _differences(items: Sequence[EvidenceItem]) -> set[Decimal]:
    """|b - a| for every pair of cited numeric fact values with the same unit."""
    values = [
        (item.unit, _decimal(item.value))
        for item in items
        if item.kind is SourceKind.FACT and item.value and item.comparator is None
    ]
    found = set()
    for i, (unit_a, a) in enumerate(values):
        for unit_b, b in values[i + 1 :]:
            if a is not None and b is not None and unit_a == unit_b:
                found.add(abs(b - a))
    return found


def validate_sources(
    supplied: Sequence[EvidenceItem], catalog: SourceCatalog
) -> tuple[CitationIssue, ...]:
    """Check EVERY supplied source, cited or not, against the patient catalog.

    Run before generation: a source that is not this patient's stored, usable evidence must never
    reach a generator or the screen (not even as "retrieved but not cited"). Any issue means a tool
    or retrieval fault, and the caller fails closed. ``claim_index`` is None for these issues.
    """
    issues: list[CitationIssue] = []
    for item in supplied:
        _check_source(item, catalog, issues, None)
    return tuple(issues)


def _check_source(
    item: EvidenceItem, catalog: SourceCatalog, issues: list[CitationIssue], index: int | None
) -> None:
    def issue(code: str) -> None:
        issues.append(CitationIssue(code=code, claim_index=index, source_id=item.source_id))

    page_text = catalog.pages.get((item.document_id, item.page_number))
    if page_text is None:
        issue("source_not_found")
        return
    if item.kind is SourceKind.PASSAGE and item.chunk_id is not None:
        # Table-row chunks pair headers with cells, so they are compared with the stored chunk.
        chunk = catalog.chunks.get(item.chunk_id)
        if chunk is None:
            issue("source_not_found")
        elif chunk[:2] != (item.document_id, item.page_number):
            issue("source_location_mismatch")
        elif chunk[2] != item.excerpt:
            issue("excerpt_mismatch")
    elif item.excerpt not in page_text:
        issue("excerpt_mismatch")
    if item.kind is SourceKind.FACT:
        stored = catalog.facts.get(item.fact_id or "")
        if stored is None:
            issue("fact_not_found")
            return
        if (stored.document_id, stored.page_number) != (item.document_id, item.page_number):
            issue("fact_location_mismatch")
        if stored.superseded or stored.review_status not in USABLE_STATUSES:
            issue("unusable_fact")


def validate_answer(
    draft: DraftAnswer,
    supplied: Sequence[EvidenceItem],
    catalog: SourceCatalog,
    *,
    aliases: Mapping[str, str],
) -> ValidationResult:
    """Every issue found in ``draft``; an empty tuple means the answer is valid."""
    issues: list[CitationIssue] = []
    by_id = {item.source_id: item for item in supplied}
    cited: list[str] = []
    checked_sources: set[str] = set()
    if draft.status == "insufficient_evidence" and draft.claims:
        issues.append(CitationIssue(code="claims_with_insufficient_status"))
    if draft.status in ("answered", "partial") and not draft.claims:
        issues.append(CitationIssue(code="empty_answer"))
    for index, claim in enumerate(draft.claims):

        def issue(code: str, source: str | None = None, at: int = index) -> None:
            issues.append(CitationIssue(code=code, claim_index=at, source_id=source))

        sources: list[EvidenceItem] = []
        for source_id in claim.citations:
            item = by_id.get(source_id)
            if item is None:
                issue("unknown_source", source_id)
                continue
            sources.append(item)
            if source_id not in cited:
                cited.append(source_id)
            if source_id not in checked_sources:
                checked_sources.add(source_id)
                _check_source(item, catalog, issues, index)
        text = claim.text
        numbers = numbers_in(text)
        dates = dates_in(text)
        analytes = find_analytes(text, aliases)
        needs_citation = claim.kind != "summary" or numbers or dates or analytes
        if needs_citation and not claim.citations:
            issue("uncited_claim")
        if prohibited_output(text):
            issue("prohibited_content")
        if not sources:
            continue
        grounded = set().union(*(numbers_in(_haystack(s)) for s in sources))
        for day in set().union(*(_item_dates(s) for s in sources)):
            grounded |= {Decimal(day.year), Decimal(day.month), Decimal(day.day)}
        if claim.kind == "calculation":
            grounded |= _differences(sources)
        if numbers - grounded:
            issue("unsupported_number")
        if dates - set().union(*(_item_dates(s) for s in sources)):
            issue("unsupported_date")
        quoted_in = " \n ".join(_squash(_haystack(s)) for s in sources)
        for straight, curly in _QUOTE.findall(text):
            if _squash(straight or curly) not in quoted_in:
                issue("misquoted_text")
                break
        pages = {s.page_number for s in sources}
        if any(int(n) not in pages for n in _PAGE_REF.findall(text)):
            issue("page_mismatch")
        for analyte in analytes:
            about = any(
                s.analyte == analyte
                or (s.kind is SourceKind.PASSAGE and analyte in find_analytes(s.excerpt, aliases))
                for s in sources
            )
            if not about:
                issue("irrelevant_citation")
                break
    return ValidationResult(tuple(issues), tuple(cited))
