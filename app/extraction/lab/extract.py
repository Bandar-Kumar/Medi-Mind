"""Deterministic lab-observation extraction (``lab-rules-1``; docs/PHASE_3_PLAN.md §4).

``extract_lab_observations(document, stored_texts, reference_date=...)`` is pure: it reads the
verified IR and the stored page texts, and returns drafts with exact spans, raw and normalized
values, reasons and abstention counts. It never reads the clock (the reference date is an
argument), never writes, and never logs content.

Per page: subject guard → tables → labelled lines → normalization → grounding → checks.
"""

import datetime as dt
from collections import Counter
from collections.abc import Mapping

from app.extraction.checks import flag_disagrees, implausible, with_document_checks
from app.extraction.grounding import grounded
from app.extraction.lab.context import COLLECTION, REPORT, PageContext, page_context
from app.extraction.lab.lines import line_rows
from app.extraction.lab.tables import RawRow, table_rows
from app.extraction.normalize.analytes import ALIAS_EXACT, map_analyte
from app.extraction.normalize.numbers import parse_flag, parse_value, text_key
from app.extraction.normalize.ranges import compare_to_range, parse_range
from app.extraction.normalize.units import normalize_unit
from app.extraction.reasons import Abstention, QualityNote, ReviewReason
from app.extraction.reference import Reference, load_reference
from app.extraction.schemas import NEEDS_REVIEW, DateFinding, ExtractionOutput, ObservationDraft
from app.ingestion.ir import ParsedDocument
from app.ingestion.ir import ReviewReason as PageFlag

UNPLACED_FLAGS = frozenset({PageFlag.UNPLACED_ITEM, PageFlag.MULTI_PAGE_ITEM_UNPLACED})


def _unique(findings: list[DateFinding]) -> tuple[DateFinding | None, bool]:
    """(the finding, conflicting): one distinct printed value, or a conflict, or none."""
    if not findings:
        return None, False
    distinct = {text_key(f.raw) for f in findings}
    if len(distinct) > 1:
        return None, True
    return findings[0], False


def _choose_date(
    context: PageContext, document_dates: dict[str, list[DateFinding]]
) -> tuple[DateFinding | None, str | None, set[str], set[str]]:
    """(finding, role, reasons, notes): collection preferred, then report; page before document."""
    reasons: set[str] = set()
    notes: set[str] = set()
    for role in (COLLECTION, REPORT):
        on_page = [d for d in context.dates if d.role == role]
        finding, conflicting = _unique(on_page) if on_page else _unique(document_dates[role])
        if conflicting:
            reasons.add(ReviewReason.CONFLICTING_DATES.value)
            return None, None, reasons, notes
        if finding is not None:
            if role == REPORT:
                notes.add(QualityNote.EFFECTIVE_DATE_FROM_REPORT.value)
            reasons.update(finding.reasons)
            return finding, role, reasons, notes
    notes.add(QualityNote.NO_EFFECTIVE_DATE.value)
    return None, None, reasons, notes


def _draft(
    row: RawRow,
    page_text: str,
    context: PageContext,
    document_dates: dict[str, list[DateFinding]],
    reference: Reference,
) -> ObservationDraft | Abstention:
    parts = [
        (p.text, p.start, p.end)
        for p in (row.analyte, row.value, row.unit, row.ref_range, row.flag, row.specimen)
        if p
    ]
    if not grounded(page_text, row.excerpt.text, row.excerpt.start, row.excerpt.end, parts):
        return Abstention.SPAN_VERIFICATION_FAILED
    reasons: set[str] = {r.value for r in context.flag_reasons}
    notes: set[str] = set()

    analyte = map_analyte(row.analyte.text, reference)
    if analyte is None:
        reasons.add(ReviewReason.UNMAPPED_ANALYTE.value)
    value = parse_value(row.value.text)
    if value.reason is not None:
        reasons.add(value.reason.value)
    flag = value.flag
    if row.flag is not None:
        printed = parse_flag(row.flag.text)
        flag = printed if printed is not None else row.flag.text
    unit_raw = row.unit.text if row.unit else None
    unit = normalize_unit(unit_raw, value.value_num, analyte, reference)
    reasons.update(r.value for r in unit.reasons)
    notes.update(n.value for n in unit.notes)
    range_raw = row.ref_range.text if row.ref_range else None
    printed_range = parse_range(range_raw, unit_key=text_key(unit_raw) if unit_raw else None)
    if range_raw and not printed_range.parsed:
        notes.add(QualityNote.REF_RANGE_UNPARSED.value)
    comparison = compare_to_range(value.value_num, value.comparator, printed_range)
    if flag_disagrees(flag, comparison):
        reasons.add(ReviewReason.FLAG_RANGE_DISAGREEMENT.value)

    finding, role, date_reasons, date_notes = _choose_date(context, document_dates)
    reasons.update(date_reasons)
    notes.update(date_notes)
    specimen = (
        row.specimen.text if row.specimen else (context.specimen.text if context.specimen else None)
    )
    if finding is not None and finding.time_raw and finding.time_value is None:
        notes.add(QualityNote.TIME_UNPARSED.value)
    candidate = dict(
        page_number=row.page_number,
        source_locator=row.locator,
        source_excerpt=row.excerpt.text,
        char_start=row.excerpt.start,
        char_end=row.excerpt.end,
        analyte_raw=row.analyte.text,
        analyte_name=analyte.canonical_name if analyte else None,
        analyte_match=ALIAS_EXACT if analyte else None,
        value_raw=row.value.text,
        value_num=value.value_num,
        comparator=value.comparator,
        value_qualitative=value.qualitative,
        unit_raw=unit_raw,
        unit_norm=unit.unit_norm,
        value_num_norm=unit.value_num_norm,
        ref_range_raw=range_raw,
        ref_low=printed_range.low,
        ref_high=printed_range.high,
        flag_as_printed=flag,
        ref_range_comparison=comparison,
        specimen=specimen,
        effective_date=finding.value if finding and finding.value else None,
        date_precision=finding.precision if finding and finding.value else None,
        date_raw=finding.raw if finding else None,
        effective_date_source=role if finding and finding.value else None,
        date_page_number=finding.page_number if finding else None,
        date_char_start=finding.char_start if finding else None,
        date_char_end=finding.char_end if finding else None,
        effective_time=finding.time_value if finding else None,
        time_raw=finding.time_raw if finding else None,
    )
    probe = ObservationDraft(**candidate, review_status="validated")
    if implausible(probe, analyte):
        reasons.add(ReviewReason.IMPLAUSIBLE_VALUE.value)
    ordered = tuple(sorted(reasons))
    return ObservationDraft(
        **candidate,
        review_status=NEEDS_REVIEW if ordered else "validated",
        review_reasons=ordered,
        quality_notes=tuple(sorted(notes)),
    )


def extract_lab_observations(
    document: ParsedDocument,
    stored_texts: Mapping[int, str],
    *,
    reference_date: dt.date,
    reference: Reference | None = None,
) -> ExtractionOutput:
    """Extract lab observations from a verified IR whose page texts equal the stored ones."""
    reference = reference or load_reference()
    abstentions: Counter = Counter()
    pages = sorted(document.pages, key=lambda p: p.page_number)
    if any(stored_texts.get(p.page_number) != p.text for p in pages) or len(stored_texts) != len(
        pages
    ):
        return ExtractionOutput(
            observations=(), abstentions={Abstention.PAGE_TEXT_MISMATCH.value: 1}
        )
    if set(document.review_flags) & UNPLACED_FLAGS:
        abstentions[Abstention.UNPLACED_ITEM] += 1
    contexts = {p.page_number: page_context(p, p.text, reference_date) for p in pages}
    document_dates: dict[str, list[DateFinding]] = {COLLECTION: [], REPORT: []}
    for context in contexts.values():
        for finding in context.dates:
            document_dates[finding.role].append(finding)

    drafts: list[ObservationDraft] = []
    seen_locators: set[str] = set()
    for page in pages:
        context = contexts[page.page_number]
        if context.multiple_subjects:
            abstentions[Abstention.MULTIPLE_SUBJECTS] += 1
            continue
        if set(page.review_flags) & UNPLACED_FLAGS:
            abstentions[Abstention.UNPLACED_ITEM] += 1
        rows: list[RawRow] = []
        excluded: list[tuple[int, int]] = []
        all_located = True
        for table in page.tables:
            result = table_rows(page.page_number, page.text, table)
            abstentions.update(result.abstentions)
            rows.extend(result.rows)
            if result.span is None:
                all_located = False
            else:
                excluded.append(result.span)
        if all_located:
            line_found, line_abstentions = line_rows(
                page.page_number, page.text, reference, excluded=excluded
            )
            rows.extend(line_found)
            abstentions.update(line_abstentions)
        for row in rows:
            if row.locator in seen_locators:
                abstentions[Abstention.DUPLICATE_SOURCE] += 1
                continue
            outcome = _draft(row, page.text, context, document_dates, reference)
            if isinstance(outcome, Abstention):
                abstentions[outcome] += 1
                continue
            seen_locators.add(row.locator)
            drafts.append(outcome)

    report, conflicting = _unique(document_dates[REPORT])
    report_date = (
        report if report and not conflicting and report.value and not report.reasons else None
    )
    return ExtractionOutput(
        observations=with_document_checks(drafts, reference),
        abstentions={str(k): v for k, v in sorted(abstentions.items())},
        report_date=report_date,
    )
