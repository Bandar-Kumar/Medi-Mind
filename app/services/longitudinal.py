"""Longitudinal views for the UI (Phase 4; docs/PHASE_4_PLAN.md §8; ADR-039).

Orchestration only: the records layer reads (patient-scoped, a fixed number of queries per call),
``app.services.facts.fact_view`` resolves each fact's display values exactly as the document page
does (latest correction, else machine-normalized), and ``app.longitudinal`` computes the timeline,
trends, duplicates and comparisons. Deterministic; no model and no LLM is loaded (ADR-003).

What is shown follows ADR-023: validated (labelled "auto-checked, not verified"), verified and
corrected facts. ``needs_review`` facts are only *counted* here (they are reviewed on the document
page); rejected facts and identity-``mismatch`` documents never appear.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.extraction.compare import CONFLICT, ComparableObservation, compare_pair
from app.extraction.reference import Reference, load_reference
from app.longitudinal.dates import date_label
from app.longitudinal.duplicates import EXACT, DuplicateGroup, find_duplicates
from app.longitudinal.model import (
    HUMAN_CORRECTION,
    LongitudinalDocument,
    LongitudinalObservation,
)
from app.longitudinal.timeline import (
    DOCUMENT,
    LAB_RESULT,
    TimelineFilter,
    TimelinePage,
    build_timeline,
)
from app.longitudinal.trends import (
    DateComparisonRow,
    Trend,
    build_trend,
    range_state,
)
from app.longitudinal.trends import (
    compare_dates as compare_trend_dates,
)
from app.records.facts import ConflictRecord, list_conflicts
from app.records.longitudinal import (
    TimelineDocumentRecord,
    count_facts_by_status,
    list_facts_by_ids,
    list_timeline_documents,
    list_usable_facts,
)
from app.services.facts import FactView, fact, fact_view

# Re-exported for the UI, which imports only services (tests/test_ui.py).
UI_HELPERS = (date_label, range_state, TimelineFilter, DOCUMENT, LAB_RESULT)

NEEDS_REVIEW = "needs_review"
REJECTED = "rejected"


def _decimal(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def to_observation(view: FactView, document: TimelineDocumentRecord) -> LongitudinalObservation:
    """One fact's display values (correction or machine) with its printed values and context."""
    f = view.fact
    shown = view.display
    if view.correction is not None:
        normalized = view.correction.get("normalized", {})
        ref_low = _decimal(normalized.get("ref_low"))
        ref_high = _decimal(normalized.get("ref_high"))
        comparison = normalized.get("ref_range_comparison")
    else:
        ref_low, ref_high, comparison = f.ref_low, f.ref_high, f.ref_range_comparison
    return LongitudinalObservation(
        fact_id=str(f.id),
        document_id=str(f.document_id),
        extraction_run_id=str(f.extraction_run_id),
        page_number=f.page_number,
        source_locator=f.source_locator,
        char_start=f.char_start,
        char_end=f.char_end,
        analyte_id=shown.get("analyte_id"),
        analyte_name=shown.get("analyte_name"),
        value=shown.get("value"),
        comparator=shown.get("comparator"),
        unit=shown.get("unit"),
        qualitative=shown.get("qualitative"),
        ref_low=ref_low,
        ref_high=ref_high,
        range_comparison=comparison,
        effective_date=shown.get("effective_date"),
        date_precision=shown.get("date_precision") if shown.get("effective_date") else None,
        date_source=f.effective_date_source,
        effective_time=f.effective_time,
        review_status=f.review_status,
        value_source=view.source,
        doc_type=document.doc_type,
        identity_check=document.identity_check,
        analyte_raw=f.analyte_raw,
        value_raw=f.value_raw,
        unit_raw=f.unit_raw,
        ref_range_raw=f.ref_range_raw,
        flag_as_printed=f.flag_as_printed,
        date_raw=f.date_raw,
    )


def to_document(d: TimelineDocumentRecord) -> LongitudinalDocument:
    return LongitudinalDocument(
        document_id=str(d.id),
        doc_type=d.doc_type,
        identity_check=d.identity_check,
        processing_status=d.processing_status,
        document_date=d.document_date,
        document_date_precision=d.document_date_precision,
        uploaded_at=d.created_at,
        processed_at=d.processed_at,
        filename=d.original_filename,
    )


@dataclass(frozen=True)
class PatientHistory:
    """Everything one view needs, loaded once."""

    observations: tuple[LongitudinalObservation, ...]
    documents: tuple[LongitudinalDocument, ...]
    duplicates: tuple[DuplicateGroup, ...]
    filenames: dict[str, str]  # document id -> file name, for display only


def load_history(
    patient_id: uuid.UUID,
    *,
    analytes: frozenset[str] | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    reference: Reference | None = None,
) -> PatientHistory:
    documents = list_timeline_documents(patient_id)
    by_id = {d.id: d for d in documents}
    records = list_usable_facts(
        patient_id, analyte_names=analytes, date_from=date_from, date_to=date_to
    )
    observations = tuple(
        to_observation(fact_view(r), by_id[r.document_id])
        for r in records
        if r.document_id in by_id
    )
    if analytes is not None:  # exact filter on display values (corrections can change analytes)
        observations = tuple(o for o in observations if o.analyte_name in analytes)
    return PatientHistory(
        observations=observations,
        documents=tuple(to_document(d) for d in documents),
        duplicates=find_duplicates(observations, reference or load_reference()),
        filenames={str(d.id): d.original_filename for d in documents},
    )


# --- timeline ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TimelineView:
    page: TimelinePage
    filenames: dict[str, str]
    awaiting_review: int  # needs_review results not shown (reviewed on the document page)


def timeline(
    patient_id: uuid.UUID,
    *,
    filters: TimelineFilter | None = None,
    page: int = 1,
    page_size: int = 50,
    newest_first: bool = False,
) -> TimelineView:
    filters = filters or TimelineFilter()
    history = load_history(
        patient_id,
        analytes=filters.analytes,
        date_from=filters.date_from,
        date_to=filters.date_to,
    )
    built = build_timeline(
        history.observations,
        history.documents,
        history.duplicates,
        filters=filters,
        page=page,
        page_size=page_size,
        newest_first=newest_first,
    )
    pending = sum(
        c.count
        for c in count_facts_by_status(patient_id)
        if c.review_status == NEEDS_REVIEW
        and (filters.analytes is None or c.analyte_name in filters.analytes)
    )
    return TimelineView(built, history.filenames, pending)


# --- analytes, trends, comparisons ---------------------------------------------------------------


@dataclass(frozen=True)
class AnalyteSummary:
    name: str
    results: int  # usable results (exact duplicates counted once)
    units: tuple[str, ...]  # display units; more than one means separate series
    first_date: dt.date | None
    last_date: dt.date | None
    awaiting_review: int


def analytes(patient_id: uuid.UUID) -> tuple[AnalyteSummary, ...]:
    history = load_history(patient_id)
    hidden = {
        m for g in history.duplicates if g.kind == EXACT for m in g.members if m != g.representative
    }
    pending: dict[str, int] = {}
    for c in count_facts_by_status(patient_id):
        if c.review_status == NEEDS_REVIEW and c.analyte_name:
            pending[c.analyte_name] = pending.get(c.analyte_name, 0) + c.count
    names = sorted({o.analyte_name for o in history.observations if o.analyte_name} | set(pending))
    summaries = []
    for name in names:
        mine = [
            o for o in history.observations if o.analyte_name == name and o.fact_id not in hidden
        ]
        dates = [o.effective_date for o in mine if o.effective_date is not None]
        summaries.append(
            AnalyteSummary(
                name=name,
                results=len(mine),
                units=tuple(sorted({o.unit for o in mine if o.unit})),
                first_date=min(dates) if dates else None,
                last_date=max(dates) if dates else None,
                awaiting_review=pending.get(name, 0),
            )
        )
    return tuple(summaries)


@dataclass(frozen=True)
class TrendView:
    trend: Trend
    conflicts: tuple["ConflictPresentation", ...]
    filenames: dict[str, str]
    awaiting_review: int


def trend(patient_id: uuid.UUID, analyte_name: str) -> TrendView:
    history = load_history(patient_id, analytes=frozenset({analyte_name}))
    built = build_trend(analyte_name, history.observations, history.duplicates)
    pending = sum(
        c.count
        for c in count_facts_by_status(patient_id)
        if c.review_status == NEEDS_REVIEW and c.analyte_name == analyte_name
    )
    related = tuple(c for c in conflicts(patient_id) if c.analyte_name == analyte_name)
    return TrendView(built, related, history.filenames, pending)


@dataclass(frozen=True)
class DateComparison:
    first: dt.date
    second: dt.date
    rows: tuple[DateComparisonRow, ...]
    filenames: dict[str, str]


def compare_dates(patient_id: uuid.UUID, first: dt.date, second: dt.date) -> DateComparison:
    if first == second:
        raise ValueError("choose two different dates")
    history = load_history(patient_id, date_from=min(first, second), date_to=max(first, second))
    rows = compare_trend_dates(history.observations, history.duplicates, first, second)
    return DateComparison(first, second, rows, history.filenames)


def result_dates(patient_id: uuid.UUID) -> tuple[dt.date, ...]:
    """Day-precision dates that have at least one usable result (for date pickers)."""
    history = load_history(patient_id)
    return tuple(
        sorted(
            {
                o.effective_date
                for o in history.observations
                if o.effective_date is not None and o.date_precision == "day"
            }
        )
    )


# --- duplicates ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class DuplicateView:
    group: DuplicateGroup
    members: tuple[LongitudinalObservation, ...]


def duplicates(patient_id: uuid.UUID) -> tuple[DuplicateView, ...]:
    history = load_history(patient_id)
    by_id = {o.fact_id: o for o in history.observations}
    return tuple(DuplicateView(g, tuple(by_id[m] for m in g.members)) for g in history.duplicates)


# --- conflicts -----------------------------------------------------------------------------------

DIRECT = "direct"  # disagrees directly with at least one other member
LINKED = "linked"  # in the group only through other members' disagreements
NON_OVERLAPPING_VALUES = "non_overlapping_values"
OPPOSITE_RESULTS = "opposite_qualitative_results"


@dataclass(frozen=True)
class ConflictMember:
    view: FactView
    role: str  # direct / linked
    rejected_or_superseded: bool  # history only: the member is no longer usable


@dataclass(frozen=True)
class ConflictPresentation:
    record: ConflictRecord
    analyte_name: str | None
    members: tuple[ConflictMember, ...]
    direct_pairs: tuple[tuple[uuid.UUID, uuid.UUID, str], ...]  # (a, b, explanation code)


def _comparable(view: FactView) -> ComparableObservation:
    return ComparableObservation(
        fact_id=str(view.fact.id),
        analyte_name=view.display.get("analyte_name") or "",
        effective_time=view.fact.effective_time,
        value=view.display.get("value"),
        comparator=view.display.get("comparator"),
        unit=view.display.get("unit"),
        qualitative=view.display.get("qualitative"),
    )


def _present(
    record: ConflictRecord, views: dict[uuid.UUID, FactView], reference: Reference
) -> ConflictPresentation:
    members = [views[i] for i in record.member_fact_ids if i in views]
    pairs = []
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            if compare_pair(_comparable(a), _comparable(b), reference) == CONFLICT:
                code = (
                    OPPOSITE_RESULTS
                    if a.display.get("value") is None and b.display.get("value") is None
                    else NON_OVERLAPPING_VALUES
                )
                pairs.append((a.fact.id, b.fact.id, code))
    direct = {x for a, b, _ in pairs for x in (a, b)}
    return ConflictPresentation(
        record=record,
        analyte_name=record.analyte_name,
        members=tuple(
            ConflictMember(
                view=v,
                role=DIRECT if v.fact.id in direct else LINKED,
                rejected_or_superseded=v.fact.review_status == REJECTED or v.fact.superseded,
            )
            for v in members
        ),
        direct_pairs=tuple(pairs),
    )


def conflicts(
    patient_id: uuid.UUID,
    *,
    include_closed: bool = False,
) -> tuple[ConflictPresentation, ...]:
    """Stored conflicts (never re-detected or changed here) with their members and the pairs
    that disagree directly. Closed conflicts are history: their members may since have been
    rejected, corrected or superseded, and are labelled so."""
    records = list(list_conflicts(patient_id, current=True))
    if include_closed:
        records += list_conflicts(patient_id, current=False)
    ids = {i for r in records for i in r.member_fact_ids}
    views = {r.id: fact_view(r) for r in list_facts_by_ids(patient_id, ids)}
    reference = load_reference()
    return tuple(_present(r, views, reference) for r in records)


# --- overview and evidence -----------------------------------------------------------------------


@dataclass(frozen=True)
class LatestResult:
    observation: LongitudinalObservation
    range_state: str


@dataclass(frozen=True)
class PatientOverview:
    documents: int
    documents_by_status: dict[str, int]
    identity_mismatch_documents: int
    identity_unknown_documents: int
    results_shown: int  # usable results, exact duplicates counted once
    awaiting_review: int
    rejected: int
    analytes: int
    first_result_date: dt.date | None
    last_result_date: dt.date | None
    current_conflicts: int
    exact_duplicate_groups: int
    possible_duplicate_groups: int
    undated_results: int
    latest: tuple[LatestResult, ...]  # the most recent dated result of each analyte


def overview(patient_id: uuid.UUID) -> PatientOverview:
    history = load_history(patient_id)
    counts = count_facts_by_status(patient_id)
    hidden = {
        m for g in history.duplicates if g.kind == EXACT for m in g.members if m != g.representative
    }
    shown = [o for o in history.observations if o.fact_id not in hidden]
    dated = [o for o in shown if o.effective_date is not None]
    latest: dict[str, LongitudinalObservation] = {}
    for o in sorted(dated, key=lambda o: (o.effective_date, o.fact_id)):
        if o.analyte_name:
            latest[o.analyte_name] = o
    by_status: dict[str, int] = {}
    for d in history.documents:
        by_status[d.processing_status] = by_status.get(d.processing_status, 0) + 1
    return PatientOverview(
        documents=len(history.documents),
        documents_by_status=by_status,
        identity_mismatch_documents=sum(d.identity_check == "mismatch" for d in history.documents),
        identity_unknown_documents=sum(d.identity_check == "unknown" for d in history.documents),
        results_shown=len(shown),
        awaiting_review=sum(c.count for c in counts if c.review_status == NEEDS_REVIEW),
        rejected=sum(c.count for c in counts if c.review_status == REJECTED),
        analytes=len({o.analyte_name for o in shown if o.analyte_name}),
        first_result_date=min((o.effective_date for o in dated), default=None),  # type: ignore[type-var]
        last_result_date=max((o.effective_date for o in dated), default=None),  # type: ignore[type-var]
        current_conflicts=len(list_conflicts(patient_id, current=True)),
        exact_duplicate_groups=sum(g.kind == EXACT for g in history.duplicates),
        possible_duplicate_groups=sum(g.kind != EXACT for g in history.duplicates),
        undated_results=sum(o.effective_date is None for o in shown),
        latest=tuple(LatestResult(o, range_state(o)) for _, o in sorted(latest.items())),
    )


@dataclass(frozen=True)
class Evidence:
    view: FactView
    filename: str
    is_correction: bool


def evidence(patient_id: uuid.UUID, fact_id: uuid.UUID) -> Evidence:
    """One result's exact source: document, page, locator, excerpt, printed and shown values.

    Another patient's fact is ``fact_not_found`` (scoped in SQL)."""
    view = fact(patient_id, fact_id)
    documents = {d.id: d for d in list_timeline_documents(patient_id)}
    document = documents.get(view.fact.document_id)
    return Evidence(
        view=view,
        filename=document.original_filename if document else "",
        is_correction=view.source == HUMAN_CORRECTION,
    )


def document_types(patient_id: uuid.UUID) -> tuple[str, ...]:
    """Document types present for the patient (timeline filter options)."""
    return tuple(sorted({d.doc_type for d in list_timeline_documents(patient_id)}))
