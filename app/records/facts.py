"""Patient-scoped extraction, review and conflict repository (Phase 3; docs/PHASE_3_PLAN.md).

The records layer is the only database access path (ADR-016). Every public function that reads or
writes patient data takes ``patient_id`` first; another patient's id behaves exactly like a
missing one. ``seed_reference_data`` is the one unscoped function: it writes reference data only.

**Extraction gate** (§8; the indexing gate's precedence, ADR-036 A1): pages must exist; the
identity outcome must not be ``mismatch``; a succeeded ``identity`` run and a succeeded
``classify`` run must exist. ``extraction_gate_refusal`` is the single rule: the pre-check, the
re-check under the document lock, and the offline system evaluation all use it.

**Runs** (§9.2): a lab report gets a ``succeeded`` ``extract`` run written in the same transaction
as all its facts (all or nothing); another document type gets a ``skipped`` run with
``skip_reason = unsupported_doc_type``. Either one completes the stage: a repeat is refused with
``already_extracted``. A failed run is history only, and the stage can be retried.

**Review status** (§5.2a): extraction stores only ``validated`` or ``needs_review``;
``verified`` / ``corrected`` / ``rejected`` come only from ``record_review_decision``, which
appends a ``review_decisions`` row and sets the status in the same transaction. The latest
decision decides. Raw, normalized and derived fact columns are never updated: the only UPDATEs on
facts touch ``review_status`` and the supersession columns.

**Conflicts** (§6.6, §6.7): this module never compares values itself. Each trigger (extraction,
re-extraction, review decision, document deletion) calls ``_recompute_conflicts`` in its own
transaction, which hands the patient's usable observations and their decisions to an injected
``ConflictDetector`` (built by the services on ``app.extraction.compare``) and applies the result:
an unchanged detection key keeps its conflict (acknowledgment included), a vanished key closes
its conflict with the trigger's reason, a new key opens a conflict. Closed conflicts are never
reopened. The patient row is locked first, so recomputations of one patient serialize.

Logs carry identifiers, counts, statuses and reason codes only: never values, excerpts or names.
"""

import datetime as dt
import logging
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import DocumentProcessingError
from app.records.db import session_scope
from app.records.models import (
    AnalyteAlias,
    AnalyteCatalog,
    AssertionType,
    Chunk,
    Conflict,
    ConflictMember,
    ConflictStatus,
    Document,
    ExtractedFact,
    Observation,
    Page,
    Patient,
    PipelineRun,
    ProcessingStatus,
    ReviewDecision,
    ReviewStatus,
    RunStatus,
    Subject,
    UnitConversion,
)
from app.records.repositories import (
    _ERROR_CLASS,
    _ERROR_KIND,
    CLASSIFY_STAGE,
    DOCUMENT_NOT_FOUND,
    IDENTITY_STAGE,
    _has_succeeded_run,
    _lock_document,
)

logger = logging.getLogger(__name__)

EXTRACT_STAGE: Final = "extract"
INDEX_STAGE: Final = "index"
ALREADY_EXTRACTED: Final = "already_extracted"  # a succeeded or skipped extract run exists
NOT_PARSED: Final = "not_parsed"
NOT_CLASSIFIED: Final = "not_classified"
IDENTITY_NOT_CHECKED: Final = "identity_not_checked"
IDENTITY_MISMATCH: Final = "identity_mismatch"
EXTRACT_GATE_REFUSALS: Final = frozenset({NOT_CLASSIFIED, IDENTITY_NOT_CHECKED, IDENTITY_MISMATCH})
UNSUPPORTED_DOC_TYPE: Final = "unsupported_doc_type"  # skip reason of a not-applicable run
NOT_APPLICABLE: Final = "not_applicable"  # a lab extraction attempted on another document type
REFERENCE_DATA_NOT_SEEDED: Final = "reference_data_not_seeded"
NOT_REEXTRACTABLE: Final = "not_reextractable"  # nothing to replace, or the versions are equal
HAS_REVIEW_DECISIONS: Final = "has_review_decisions"
FACT_NOT_FOUND: Final = "fact_not_found"
CONFLICT_NOT_FOUND: Final = "conflict_not_found"
LAB_REPORT: Final = "lab_report"
FACT_TYPE_OBSERVATION: Final = "observation"
USABLE_STATUSES: Final = frozenset(
    {ReviewStatus.VALIDATED.value, ReviewStatus.VERIFIED.value, ReviewStatus.CORRECTED.value}
)
MACHINE_STATUSES: Final = frozenset({ReviewStatus.VALIDATED.value, ReviewStatus.NEEDS_REVIEW.value})
DECISION_STATUS: Final = {
    "verify": ReviewStatus.VERIFIED.value,
    "correct": ReviewStatus.CORRECTED.value,
    "reject": ReviewStatus.REJECTED.value,
}
DECISION_CLOSE_REASON: Final = {
    "verify": "member_reviewed",
    "correct": "member_corrected",
    "reject": "member_rejected",
}
CONFLICT_DESCRIPTION: Final = "value_mismatch_same_date"  # a template code, never values
VERSION_KEYS: Final = (
    "extractor_version",
    "normalizer_version",
    "reference_data_version",
    "date_policy_version",
)
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
MAX_NOTE_CHARS: Final = 1000


def _error(kind: str, message: str) -> DocumentProcessingError:
    return DocumentProcessingError(message, kind=kind)


# --- the gate ---------------------------------------------------------------------------------


def extraction_gate_refusal(
    *, has_pages: bool, classified: bool, identity_checked: bool, identity_check: str
) -> str | None:
    """Why a document may not be extracted, or None. The single extraction gate rule."""
    if not has_pages:
        return NOT_PARSED
    if identity_check == "mismatch":
        return IDENTITY_MISMATCH
    if not identity_checked:
        return IDENTITY_NOT_CHECKED
    if not classified:
        return NOT_CLASSIFIED
    return None


def extraction_applies(doc_type: str) -> bool:
    """The lab extractor runs on lab reports only (D-9); other types get a skipped run."""
    return doc_type == LAB_REPORT


def _has_run(
    session: Session, patient_id: uuid.UUID, document_id: uuid.UUID, statuses: frozenset[str]
) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    PipelineRun.patient_id == patient_id,
                    PipelineRun.document_id == document_id,
                    PipelineRun.stage == EXTRACT_STAGE,
                    PipelineRun.status.in_(statuses),
                )
            )
        )
    )


_COMPLETED = frozenset({RunStatus.SUCCEEDED.value, RunStatus.SKIPPED.value})


def _gate(session: Session, patient_id: uuid.UUID, document: Document) -> str | None:
    has_pages = session.scalar(
        select(exists().where(Page.patient_id == patient_id, Page.document_id == document.id))
    )
    return extraction_gate_refusal(
        has_pages=bool(has_pages),
        classified=_has_succeeded_run(session, patient_id, document.id, CLASSIFY_STAGE),
        identity_checked=_has_succeeded_run(session, patient_id, document.id, IDENTITY_STAGE),
        identity_check=document.identity_check,
    )


@dataclass(frozen=True)
class ExtractionTarget:
    """What extraction needs before it reads any file. Identifiers and codes only."""

    document_id: uuid.UUID
    sha256: str
    page_count: int | None
    doc_type: str
    identity_check: str
    reference_date: dt.date  # the upload date (UTC): the fixed future-date bound (§7.1)
    blocked_reason: str | None
    extracted: bool  # a succeeded or skipped extract run exists


def get_extraction_target(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> ExtractionTarget:
    """The document's extraction pre-check. Read-only.

    Raises:
        DocumentProcessingError: ``document_not_found`` (also for another patient's document).
    """
    with session_scope(factory) as session:
        document = session.scalars(
            select(Document).where(Document.id == document_id, Document.patient_id == patient_id)
        ).one_or_none()
        if document is None:
            raise _error(DOCUMENT_NOT_FOUND, f"Document {document_id} not found for this patient")
        created = document.created_at.astimezone(dt.UTC).date()
        return ExtractionTarget(
            document_id=document.id,
            sha256=document.sha256,
            page_count=document.page_count,
            doc_type=document.doc_type,
            identity_check=document.identity_check,
            reference_date=created,
            blocked_reason=_gate(session, patient_id, document),
            extracted=_has_run(session, patient_id, document_id, _COMPLETED),
        )


# --- writing extractions ----------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationRecord:
    """One observation to store, as produced by the extractor. Text fields are evidence."""

    page_number: int
    source_locator: str
    source_excerpt: str = field(repr=False)
    char_start: int
    char_end: int
    analyte_raw: str = field(repr=False)
    analyte_name: str | None = field(repr=False)
    analyte_match: str | None
    value_raw: str = field(repr=False)
    value_num: Decimal | None
    comparator: str | None
    value_qualitative: str | None
    unit_raw: str | None = field(repr=False)
    unit_norm: str | None
    value_num_norm: Decimal | None
    ref_range_raw: str | None = field(repr=False)
    ref_low: Decimal | None
    ref_high: Decimal | None
    flag_as_printed: str | None = field(repr=False)
    ref_range_comparison: str | None
    specimen: str | None = field(repr=False)
    effective_date: dt.date | None
    date_precision: str | None
    date_raw: str | None = field(repr=False)
    effective_date_source: str | None
    date_page_number: int | None
    date_char_start: int | None
    date_char_end: int | None
    effective_time: dt.time | None
    time_raw: str | None = field(repr=False)
    review_status: str
    review_reasons: tuple[str, ...]
    quality_notes: tuple[str, ...]


@dataclass(frozen=True)
class ReportDate:
    """The report date for ``documents.document_date`` (written only while that is NULL)."""

    value: dt.date
    precision: str
    raw: str = field(repr=False)


@dataclass(frozen=True)
class ExtractionRecord:
    document_id: uuid.UUID
    run_id: uuid.UUID
    status: str  # succeeded / skipped
    facts: int
    needs_review: int
    superseded: int = 0
    conflicts_opened: int = 0
    conflicts_closed: int = 0


def _check_versions(versions: Mapping[str, str]) -> dict[str, str]:
    if set(versions) != set(VERSION_KEYS):
        raise ValueError("versions must name exactly the extraction version keys")
    for value in versions.values():
        if not isinstance(value, str) or not _VERSION.fullmatch(value):
            raise ValueError("versions must be short identifiers")
    return dict(versions)


def _check_counts(counts: Mapping[str, int]) -> dict[str, int]:
    for key, value in counts.items():
        if not _CODE.fullmatch(key) or isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("counts must map reason codes to integers")
        if value < 0:
            raise ValueError("counts must not be negative")
    return dict(sorted(counts.items()))


def _check_duration(duration_ms: int) -> None:
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
        raise ValueError("duration_ms must be a non-negative integer")


def _check_observations(observations: Sequence[ObservationRecord]) -> None:
    locators: set[str] = set()
    for record in observations:
        if record.review_status not in MACHINE_STATUSES:
            raise ValueError("extraction stores only validated or needs_review")
        if (record.review_status == ReviewStatus.NEEDS_REVIEW.value) != bool(record.review_reasons):
            raise ValueError("needs_review exactly when there are reasons")
        for code in (*record.review_reasons, *record.quality_notes):
            if not _CODE.fullmatch(code):
                raise ValueError("reasons and notes must be fixed codes")
        if record.source_locator in locators:
            raise ValueError("duplicate source locator")
        locators.add(record.source_locator)
        if not 0 <= record.char_start <= record.char_end:
            raise ValueError("invalid character span")
        if "\x00" in record.source_excerpt:
            raise ValueError("text that cannot be stored")


def _now_run(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    status: RunStatus,
    duration_ms: int,
    component_versions: dict[str, Any],
    **extra: Any,
) -> PipelineRun:
    return PipelineRun(
        patient_id=patient_id,
        document_id=document_id,
        stage=EXTRACT_STAGE,
        status=status.value,
        started_at=func.now() - dt.timedelta(milliseconds=duration_ms),
        finished_at=func.now(),
        duration_ms=duration_ms,
        component_versions=component_versions,
        **extra,
    )


def _check_page_numbers(
    session: Session,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    observations: Sequence[ObservationRecord],
) -> None:
    pages = set(
        session.scalars(
            select(Page.page_number).where(
                Page.patient_id == patient_id, Page.document_id == document_id
            )
        )
    )
    for record in observations:
        if record.page_number not in pages:
            raise ValueError("a fact cites a page the document does not have")
        if record.date_page_number is not None and record.date_page_number not in pages:
            raise ValueError("a date cites a page the document does not have")


def _analyte_ids(session: Session, names: set[str]) -> dict[str, int]:
    if not names:
        return {}
    rows = session.execute(
        select(AnalyteCatalog.canonical_name, AnalyteCatalog.id).where(
            AnalyteCatalog.canonical_name.in_(sorted(names))
        )
    ).all()
    ids = {row[0]: row[1] for row in rows}
    if set(ids) != names:
        raise _error(
            REFERENCE_DATA_NOT_SEEDED,
            "Reference data is not seeded; run `python -m app seed-reference`",
        )
    return ids


def _insert_observations(
    session: Session,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    run_id: uuid.UUID,
    observations: Sequence[ObservationRecord],
    versions: Mapping[str, str],
) -> None:
    ids = _analyte_ids(session, {o.analyte_name for o in observations if o.analyte_name})
    for o in observations:
        session.add(
            Observation(
                patient_id=patient_id,
                document_id=document_id,
                page_number=o.page_number,
                source_excerpt=o.source_excerpt,
                char_start=o.char_start,
                char_end=o.char_end,
                assertion_type=AssertionType.DIRECT_FACT.value,
                negated=False,
                subject=Subject.PATIENT.value,
                review_status=o.review_status,
                review_reasons=list(o.review_reasons),
                extractor_version=versions["extractor_version"],
                prompt_version="none",
                model_id="none",
                extraction_run_id=run_id,
                source_locator=o.source_locator,
                normalizer_version=versions["normalizer_version"],
                reference_data_version=versions["reference_data_version"],
                quality_notes=list(o.quality_notes),
                analyte_raw=o.analyte_raw,
                analyte_id=ids.get(o.analyte_name) if o.analyte_name else None,
                analyte_match=o.analyte_match,
                value_raw=o.value_raw,
                value_num=o.value_num,
                comparator=o.comparator,
                value_qualitative=o.value_qualitative,
                unit_raw=o.unit_raw,
                unit_norm=o.unit_norm,
                value_num_norm=o.value_num_norm,
                ref_range_raw=o.ref_range_raw,
                ref_low=o.ref_low,
                ref_high=o.ref_high,
                flag_as_printed=o.flag_as_printed,
                ref_range_comparison=o.ref_range_comparison,
                specimen=o.specimen,
                effective_date=o.effective_date,
                date_precision=o.date_precision,
                date_raw=o.date_raw,
                effective_date_source=o.effective_date_source,
                date_page_number=o.date_page_number,
                date_char_start=o.date_char_start,
                date_char_end=o.date_char_end,
                effective_time=o.effective_time,
                time_raw=o.time_raw,
            )
        )
    session.flush()


def _component_versions(
    versions: Mapping[str, str],
    reference_date: dt.date,
    facts: int,
    needs_review: int,
    abstentions: Mapping[str, int],
    reasons: Mapping[str, int],
) -> dict[str, Any]:
    return {
        **versions,
        "reference_date": reference_date.isoformat(),
        "region_profile": "IN",
        "facts_written": facts,
        "facts_needs_review": needs_review,
        "abstentions": dict(abstentions),
        "reasons": dict(reasons),
    }


def _write_document_date(document: Document, report_date: ReportDate | None) -> None:
    if report_date is None or document.document_date is not None:
        return
    document.document_date = report_date.value
    document.document_date_precision = report_date.precision
    document.document_date_raw = report_date.raw


def record_extraction(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    observations: Sequence[ObservationRecord],
    versions: Mapping[str, str],
    reference_date: dt.date,
    abstentions: Mapping[str, int],
    report_date: ReportDate | None,
    duration_ms: int,
    detector: "ConflictDetector",
    factory: sessionmaker[Session] | None = None,
) -> ExtractionRecord:
    """Store a lab extraction: one succeeded ``extract`` run and all its facts, all or nothing.

    With the document row locked: the gate is re-checked, the document must be a lab report, and
    no succeeded or skipped extract run may exist. The report date fills ``document_date`` only
    while it is NULL. Conflicts are recomputed in the same transaction.

    Raises:
        ValueError: invalid observations, versions, counts or duration.
        DocumentProcessingError: ``document_not_found``, a gate refusal, ``not_applicable``,
            ``already_extracted``, ``reference_data_not_seeded``.
    """
    _check_observations(observations)
    clean_versions = _check_versions(versions)
    clean_abstentions = _check_counts(abstentions)
    _check_duration(duration_ms)
    reasons = _reason_counts(observations)
    needs_review = sum(o.review_status == ReviewStatus.NEEDS_REVIEW.value for o in observations)
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)
        refusal = _gate(session, patient_id, document)
        if refusal is not None:
            raise _error(refusal, f"Document {document_id} cannot be extracted ({refusal})")
        if not extraction_applies(document.doc_type):
            raise _error(NOT_APPLICABLE, f"Document {document_id} is not a lab report")
        if _has_run(session, patient_id, document_id, _COMPLETED):
            raise _error(ALREADY_EXTRACTED, f"Document {document_id} is already extracted")
        _check_page_numbers(session, patient_id, document_id, observations)
        run = _now_run(
            patient_id,
            document_id,
            RunStatus.SUCCEEDED,
            duration_ms,
            _component_versions(
                clean_versions,
                reference_date,
                len(observations),
                needs_review,
                clean_abstentions,
                reasons,
            ),
        )
        session.add(run)
        session.flush()
        _insert_observations(session, patient_id, document_id, run.id, observations, versions)
        _write_document_date(document, report_date)
        opened, closed = _recompute_conflicts(session, patient_id, detector, reason="member_added")
        record = ExtractionRecord(
            document_id,
            run.id,
            RunStatus.SUCCEEDED.value,
            len(observations),
            needs_review,
            conflicts_opened=opened,
            conflicts_closed=closed,
        )
    logger.info(
        "Extraction recorded: document_id=%s run_id=%s facts=%d needs_review=%d "
        "conflicts_opened=%d conflicts_closed=%d",
        document_id,
        record.run_id,
        record.facts,
        record.needs_review,
        opened,
        closed,
    )
    return record


def _reason_counts(observations: Sequence[ObservationRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for o in observations:
        for reason in o.review_reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def record_extraction_skipped(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    versions: Mapping[str, str],
    duration_ms: int,
    factory: sessionmaker[Session] | None = None,
) -> ExtractionRecord:
    """A ``skipped`` extract run for a document that is not a lab report (§9.2): no facts.

    Raises:
        ValueError: invalid versions or duration.
        DocumentProcessingError: ``document_not_found``, a gate refusal, ``already_extracted``,
            or ``not_applicable`` when the document *is* a lab report (it must be extracted).
    """
    clean_versions = _check_versions(versions)
    _check_duration(duration_ms)
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)
        refusal = _gate(session, patient_id, document)
        if refusal is not None:
            raise _error(refusal, f"Document {document_id} cannot be extracted ({refusal})")
        if extraction_applies(document.doc_type):
            raise _error(NOT_APPLICABLE, f"Document {document_id} is a lab report")
        if _has_run(session, patient_id, document_id, _COMPLETED):
            raise _error(ALREADY_EXTRACTED, f"Document {document_id} is already extracted")
        run = _now_run(
            patient_id,
            document_id,
            RunStatus.SKIPPED,
            duration_ms,
            {**clean_versions, "skip_reason": UNSUPPORTED_DOC_TYPE},
        )
        session.add(run)
        session.flush()
        record = ExtractionRecord(document_id, run.id, RunStatus.SKIPPED.value, 0, 0)
    logger.info("Extraction not applicable: document_id=%s run_id=%s", document_id, record.run_id)
    return record


def record_extraction_failure(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    error_kind: str,
    error_class: str | None,
    versions: Mapping[str, str],
    duration_ms: int,
    factory: sessionmaker[Session] | None = None,
) -> uuid.UUID:
    """Record a failed ``extract`` run (history only; the stage can be retried)."""
    if not _ERROR_KIND.fullmatch(error_kind):
        raise ValueError("error_kind must be a short lowercase identifier")
    if error_class is not None and not _ERROR_CLASS.fullmatch(error_class):
        raise ValueError("error_class must be a class name")
    clean_versions = _check_versions(versions)
    _check_duration(duration_ms)
    with session_scope(factory) as session:
        _lock_document(session, patient_id, document_id)
        run = _now_run(
            patient_id,
            document_id,
            RunStatus.FAILED,
            duration_ms,
            clean_versions,
            error_kind=error_kind,
            error_class=error_class,
        )
        session.add(run)
        session.flush()
        run_id = run.id
    logger.info(
        "Extraction failure recorded: document_id=%s run_id=%s error_kind=%s",
        document_id,
        run_id,
        error_kind,
    )
    return run_id


def reextract_and_supersede(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    observations: Sequence[ObservationRecord],
    versions: Mapping[str, str],
    reference_date: dt.date,
    abstentions: Mapping[str, int],
    report_date: ReportDate | None,
    duration_ms: int,
    detector: "ConflictDetector",
    factory: sessionmaker[Session] | None = None,
) -> ExtractionRecord:
    """Explicit re-extraction after a version change (§5.8), in one transaction.

    Refused with ``not_reextractable`` when no succeeded extract run exists or its versions equal
    the new ones, and with ``has_review_decisions`` when any current fact of the document has a
    human decision (D-7). Otherwise: a new run and its facts; the old current facts are
    superseded by the new run (same document and patient, enforced by a composite foreign key);
    their conflicts close as ``member_superseded``; conflicts are recomputed.
    """
    _check_observations(observations)
    clean_versions = _check_versions(versions)
    clean_abstentions = _check_counts(abstentions)
    _check_duration(duration_ms)
    reasons = _reason_counts(observations)
    needs_review = sum(o.review_status == ReviewStatus.NEEDS_REVIEW.value for o in observations)
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)
        refusal = _gate(session, patient_id, document)
        if refusal is not None:
            raise _error(refusal, f"Document {document_id} cannot be extracted ({refusal})")
        if not extraction_applies(document.doc_type):
            raise _error(NOT_APPLICABLE, f"Document {document_id} is not a lab report")
        previous = session.scalars(
            select(PipelineRun.component_versions)
            .where(
                PipelineRun.patient_id == patient_id,
                PipelineRun.document_id == document_id,
                PipelineRun.stage == EXTRACT_STAGE,
                PipelineRun.status == RunStatus.SUCCEEDED.value,
            )
            .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
            .limit(1)
        ).one_or_none()
        if previous is None or all(previous.get(k) == clean_versions[k] for k in VERSION_KEYS):
            raise _error(NOT_REEXTRACTABLE, f"Document {document_id} has nothing to replace")
        current = select(ExtractedFact.id).where(
            ExtractedFact.patient_id == patient_id,
            ExtractedFact.document_id == document_id,
            ExtractedFact.superseded_at.is_(None),
        )
        reviewed = session.scalar(
            select(
                exists().where(
                    ReviewDecision.patient_id == patient_id, ReviewDecision.fact_id.in_(current)
                )
            )
        )
        if reviewed:
            raise _error(HAS_REVIEW_DECISIONS, f"Document {document_id} has reviewed facts")
        _check_page_numbers(session, patient_id, document_id, observations)
        run = _now_run(
            patient_id,
            document_id,
            RunStatus.SUCCEEDED,
            duration_ms,
            _component_versions(
                clean_versions,
                reference_date,
                len(observations),
                needs_review,
                clean_abstentions,
                reasons,
            ),
        )
        session.add(run)
        session.flush()
        superseded = session.execute(
            update(ExtractedFact)
            .where(
                ExtractedFact.patient_id == patient_id,
                ExtractedFact.document_id == document_id,
                ExtractedFact.superseded_at.is_(None),
            )
            .values(superseded_at=func.now(), superseded_by_run_id=run.id)
        ).rowcount
        _insert_observations(session, patient_id, document_id, run.id, observations, versions)
        _write_document_date(document, report_date)
        opened, closed = _recompute_conflicts(
            session, patient_id, detector, reason="member_superseded"
        )
        record = ExtractionRecord(
            document_id,
            run.id,
            RunStatus.SUCCEEDED.value,
            len(observations),
            needs_review,
            superseded=int(superseded or 0),
            conflicts_opened=opened,
            conflicts_closed=closed,
        )
    logger.info(
        "Re-extraction recorded: document_id=%s run_id=%s facts=%d superseded=%d",
        document_id,
        record.run_id,
        record.facts,
        record.superseded,
    )
    return record


# --- document completion ----------------------------------------------------------------------


def complete_document_if_ready(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> bool:
    """``processing`` → ``processed`` when every stage is complete (§9.4); True if changed.

    Complete means: pages, succeeded classify and identity runs, no identity mismatch, chunks
    with a succeeded index run, and a succeeded or skipped extract run. Never touches any other
    status. Idempotent.

    Raises:
        DocumentProcessingError: ``document_not_found``.
    """
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)
        if document.processing_status != ProcessingStatus.PROCESSING.value:
            return False
        if _gate(session, patient_id, document) is not None:
            return False
        indexed = _has_succeeded_run(session, patient_id, document_id, INDEX_STAGE) and bool(
            session.scalar(
                select(
                    exists().where(Chunk.patient_id == patient_id, Chunk.document_id == document_id)
                )
            )
        )
        if not indexed or not _has_run(session, patient_id, document_id, _COMPLETED):
            return False
        document.processing_status = ProcessingStatus.PROCESSED.value
    logger.info("Document processed: document_id=%s", document_id)
    return True


# --- reading facts ----------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionRecord:
    id: uuid.UUID
    decision: str
    corrected_values: dict[str, Any] | None = field(repr=False)
    note: str | None = field(repr=False)
    decided_at: dt.datetime


@dataclass(frozen=True)
class FactRecord:
    """A stored observation with its provenance and review history. Evidence fields hidden."""

    id: uuid.UUID
    document_id: uuid.UUID
    extraction_run_id: uuid.UUID
    page_number: int
    source_locator: str
    source_excerpt: str = field(repr=False)
    char_start: int
    char_end: int
    review_status: str
    review_reasons: tuple[str, ...]
    quality_notes: tuple[str, ...]
    extractor_version: str
    normalizer_version: str
    reference_data_version: str
    superseded: bool
    analyte_raw: str = field(repr=False)
    analyte_id: int | None
    analyte_name: str | None = field(repr=False)
    value_raw: str = field(repr=False)
    value_num: Decimal | None
    comparator: str | None
    value_qualitative: str | None
    unit_raw: str | None = field(repr=False)
    unit_norm: str | None
    value_num_norm: Decimal | None
    ref_range_raw: str | None = field(repr=False)
    ref_low: Decimal | None
    ref_high: Decimal | None
    flag_as_printed: str | None = field(repr=False)
    ref_range_comparison: str | None
    specimen: str | None = field(repr=False)
    effective_date: dt.date | None
    date_precision: str | None
    date_raw: str | None = field(repr=False)
    effective_date_source: str | None
    date_page_number: int | None
    date_char_start: int | None
    date_char_end: int | None
    effective_time: dt.time | None
    time_raw: str | None = field(repr=False)
    decisions: tuple[DecisionRecord, ...] = ()


def _fact_rows(session: Session, patient_id: uuid.UUID, *conditions: Any) -> list[FactRecord]:
    rows = session.execute(
        select(Observation, AnalyteCatalog.canonical_name)
        .outerjoin(AnalyteCatalog, AnalyteCatalog.id == Observation.analyte_id)
        .where(Observation.patient_id == patient_id, *conditions)
        .order_by(Observation.document_id, Observation.page_number, Observation.char_start)
    ).all()
    facts = [row[0] for row in rows]
    decisions: dict[uuid.UUID, list[DecisionRecord]] = {}
    if facts:
        for d in session.scalars(
            select(ReviewDecision)
            .where(
                ReviewDecision.patient_id == patient_id,
                ReviewDecision.fact_id.in_([f.id for f in facts]),
            )
            .order_by(ReviewDecision.decided_at, ReviewDecision.id)
        ):
            decisions.setdefault(d.fact_id, []).append(
                DecisionRecord(d.id, d.decision, d.corrected_values, d.note, d.decided_at)
            )
    return [
        FactRecord(
            id=f.id,
            document_id=f.document_id,
            extraction_run_id=f.extraction_run_id,
            page_number=f.page_number,
            source_locator=f.source_locator,
            source_excerpt=f.source_excerpt,
            char_start=f.char_start or 0,
            char_end=f.char_end or 0,
            review_status=f.review_status,
            review_reasons=tuple(f.review_reasons),
            quality_notes=tuple(f.quality_notes),
            extractor_version=f.extractor_version,
            normalizer_version=f.normalizer_version,
            reference_data_version=f.reference_data_version,
            superseded=f.superseded_at is not None,
            analyte_raw=f.analyte_raw,
            analyte_id=f.analyte_id,
            analyte_name=name,
            value_raw=f.value_raw,
            value_num=f.value_num,
            comparator=f.comparator,
            value_qualitative=f.value_qualitative,
            unit_raw=f.unit_raw,
            unit_norm=f.unit_norm,
            value_num_norm=f.value_num_norm,
            ref_range_raw=f.ref_range_raw,
            ref_low=f.ref_low,
            ref_high=f.ref_high,
            flag_as_printed=f.flag_as_printed,
            ref_range_comparison=f.ref_range_comparison,
            specimen=f.specimen,
            effective_date=f.effective_date,
            date_precision=f.date_precision,
            date_raw=f.date_raw,
            effective_date_source=f.effective_date_source,
            date_page_number=f.date_page_number,
            date_char_start=f.date_char_start,
            date_char_end=f.date_char_end,
            effective_time=f.effective_time,
            time_raw=f.time_raw,
            decisions=tuple(decisions.get(f.id, ())),
        )
        for f, name in ((row[0], row[1]) for row in rows)
    ]


def _require_document(session: Session, patient_id: uuid.UUID, document_id: uuid.UUID) -> None:
    found = session.scalar(
        select(exists().where(Document.id == document_id, Document.patient_id == patient_id))
    )
    if not found:
        raise _error(DOCUMENT_NOT_FOUND, f"Document {document_id} not found for this patient")


def list_document_facts(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    include_superseded: bool = False,
    factory: sessionmaker[Session] | None = None,
) -> tuple[FactRecord, ...]:
    """The document's observations in page and position order, with their decisions."""
    with session_scope(factory) as session:
        _require_document(session, patient_id, document_id)
        conditions = [Observation.document_id == document_id]
        if not include_superseded:
            conditions.append(Observation.superseded_at.is_(None))
        return tuple(_fact_rows(session, patient_id, *conditions))


def get_fact(
    patient_id: uuid.UUID,
    fact_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> FactRecord:
    """One observation with its decisions. Another patient's fact is ``fact_not_found``."""
    with session_scope(factory) as session:
        rows = _fact_rows(session, patient_id, Observation.id == fact_id)
    if not rows:
        raise _error(FACT_NOT_FOUND, f"Fact {fact_id} not found for this patient")
    return rows[0]


@dataclass(frozen=True)
class ExtractionRunRecord:
    id: uuid.UUID
    status: str
    started_at: dt.datetime
    duration_ms: int | None
    component_versions: dict[str, Any]
    error_kind: str | None


def list_extraction_runs(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> tuple[ExtractionRunRecord, ...]:
    """The document's extract runs, newest first (versions, counts and codes only)."""
    with session_scope(factory) as session:
        _require_document(session, patient_id, document_id)
        runs = session.scalars(
            select(PipelineRun)
            .where(
                PipelineRun.patient_id == patient_id,
                PipelineRun.document_id == document_id,
                PipelineRun.stage == EXTRACT_STAGE,
            )
            .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
        ).all()
        return tuple(
            ExtractionRunRecord(
                r.id,
                r.status,
                r.started_at,
                r.duration_ms,
                dict(r.component_versions),
                r.error_kind,
            )
            for r in runs
        )


# --- review decisions -------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewRecord:
    fact_id: uuid.UUID
    decision_id: uuid.UUID
    review_status: str
    conflicts_opened: int
    conflicts_closed: int


def record_review_decision(
    patient_id: uuid.UUID,
    fact_id: uuid.UUID,
    *,
    decision: str,
    corrected_values: Mapping[str, Any] | None,
    note: str | None,
    detector: "ConflictDetector",
    factory: sessionmaker[Session] | None = None,
) -> ReviewRecord:
    """Append a human decision and set the fact's status from it, in one transaction (ADR-023).

    Only this function sets ``verified``, ``corrected`` or ``rejected``. The latest decision
    decides (§6.5): verify → verified, correct → corrected, reject → rejected; a rejected fact may
    be verified or corrected later. Fact value columns are never changed: a correction lives in
    ``corrected_values`` (validated by the review service before this call). Superseded facts
    cannot be reviewed. Conflicts are recomputed in the same transaction (§6.7).

    Raises:
        ValueError: unknown decision, corrected values given without ``correct`` (or missing
            with it), or a note that is too long or not storable.
        DocumentProcessingError: ``fact_not_found`` (also another patient's or a superseded fact).
    """
    if decision not in DECISION_STATUS:
        raise ValueError("decision must be verify, correct or reject")
    if (decision == "correct") != (corrected_values is not None):
        raise ValueError("corrected values go with a correct decision only")
    if corrected_values is not None and not isinstance(corrected_values, Mapping):
        raise ValueError("corrected values must be a mapping")
    if note is not None and (len(note) > MAX_NOTE_CHARS or "\x00" in note):
        raise ValueError("the note is too long or contains characters that cannot be stored")
    with session_scope(factory) as session:
        document_id = session.scalar(
            select(ExtractedFact.document_id).where(
                ExtractedFact.id == fact_id, ExtractedFact.patient_id == patient_id
            )
        )
        if document_id is None:
            raise _error(FACT_NOT_FOUND, f"Fact {fact_id} not found for this patient")
        _lock_document(session, patient_id, document_id)  # lock order: document, patient
        fact = session.scalars(
            select(ExtractedFact)
            .where(
                ExtractedFact.id == fact_id,
                ExtractedFact.patient_id == patient_id,
                ExtractedFact.superseded_at.is_(None),
            )
            .with_for_update()
        ).one_or_none()
        if fact is None:
            raise _error(FACT_NOT_FOUND, f"Fact {fact_id} not found for this patient")
        row = ReviewDecision(
            patient_id=patient_id,
            fact_id=fact_id,
            decision=decision,
            corrected_values=dict(corrected_values) if corrected_values is not None else None,
            note=note,
        )
        session.add(row)
        fact.review_status = DECISION_STATUS[decision]
        session.flush()
        opened, closed = _recompute_conflicts(
            session, patient_id, detector, reason=DECISION_CLOSE_REASON[decision]
        )
        record = ReviewRecord(fact_id, row.id, fact.review_status, opened, closed)
    logger.info(
        "Review decision recorded: fact_id=%s decision=%s status=%s conflicts_opened=%d "
        "conflicts_closed=%d",
        fact_id,
        decision,
        record.review_status,
        opened,
        closed,
    )
    return record


# --- conflicts --------------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupFact:
    """A usable observation as the conflict detector sees it (machine values plus decisions)."""

    fact_id: str
    review_status: str
    machine_status: str  # validated / needs_review, from review_reasons (kept after review)
    analyte_id: int | None
    analyte_name: str | None
    effective_date: dt.date | None
    date_precision: str | None
    effective_date_source: str | None
    effective_time: dt.time | None
    value_num_norm: Decimal | None
    comparator: str | None
    unit_norm: str | None
    value_qualitative: str | None
    decisions: tuple[tuple[str, str, dict[str, Any] | None], ...]  # (id, decision, corrected)


@dataclass(frozen=True)
class DetectedConflict:
    detection_key: str
    rule_version: str
    analyte_id: int
    effective_date: dt.date
    members: tuple[str, ...]  # fact ids


ConflictDetector = Callable[[Sequence[GroupFact]], Sequence[DetectedConflict]]


def _lock_patient(session: Session, patient_id: uuid.UUID) -> None:
    session.execute(select(Patient.id).where(Patient.id == patient_id).with_for_update())


def _usable_group_facts(
    session: Session, patient_id: uuid.UUID, exclude_document: uuid.UUID | None
) -> list[GroupFact]:
    conditions = [
        Observation.patient_id == patient_id,
        Observation.superseded_at.is_(None),
        Observation.review_status.in_(sorted(USABLE_STATUSES)),
    ]
    if exclude_document is not None:
        conditions.append(Observation.document_id != exclude_document)
    rows = session.execute(
        select(Observation, AnalyteCatalog.canonical_name)
        .outerjoin(AnalyteCatalog, AnalyteCatalog.id == Observation.analyte_id)
        .where(*conditions)
        .order_by(Observation.id)
    ).all()
    if not rows:
        return []
    decisions: dict[uuid.UUID, list[tuple[str, str, dict[str, Any] | None]]] = {}
    for d in session.scalars(
        select(ReviewDecision)
        .where(
            ReviewDecision.patient_id == patient_id,
            ReviewDecision.fact_id.in_([row[0].id for row in rows]),
        )
        .order_by(ReviewDecision.decided_at, ReviewDecision.id)
    ):
        decisions.setdefault(d.fact_id, []).append((str(d.id), d.decision, d.corrected_values))
    return [
        GroupFact(
            fact_id=str(o.id),
            review_status=o.review_status,
            machine_status=(
                ReviewStatus.NEEDS_REVIEW.value
                if o.review_reasons
                else ReviewStatus.VALIDATED.value
            ),
            analyte_id=o.analyte_id,
            analyte_name=name,
            effective_date=o.effective_date,
            date_precision=o.date_precision,
            effective_date_source=o.effective_date_source,
            effective_time=o.effective_time,
            value_num_norm=o.value_num_norm,
            comparator=o.comparator,
            unit_norm=o.unit_norm,
            value_qualitative=o.value_qualitative,
            decisions=tuple(decisions.get(o.id, ())),
        )
        for o, name in ((row[0], row[1]) for row in rows)
    ]


def _recompute_conflicts(
    session: Session,
    patient_id: uuid.UUID,
    detector: ConflictDetector,
    *,
    reason: str,
    exclude_document: uuid.UUID | None = None,
) -> tuple[int, int]:
    """Apply the detector's view of the patient's conflicts (§6.7). Returns (opened, closed)."""
    _lock_patient(session, patient_id)
    detected = {
        c.detection_key: c
        for c in detector(_usable_group_facts(session, patient_id, exclude_document))
    }
    open_rows = session.scalars(
        select(Conflict)
        .where(
            Conflict.patient_id == patient_id,
            Conflict.status != ConflictStatus.CLOSED.value,
            Conflict.detection_key.is_not(None),
        )
        .with_for_update()
    ).all()
    closed = 0
    for row in open_rows:
        if row.detection_key not in detected:
            row.status = ConflictStatus.CLOSED.value
            row.closed_at = func.now()
            row.closed_reason = reason
            closed += 1
    existing = (
        set(
            session.scalars(
                select(Conflict.detection_key).where(
                    Conflict.patient_id == patient_id, Conflict.detection_key.in_(sorted(detected))
                )
            )
        )
        if detected
        else set()
    )
    opened = 0
    recurrences = 0
    for key, conflict in sorted(detected.items()):
        if key in existing:
            still_open = any(r.detection_key == key and r.status != "closed" for r in open_rows)
            recurrences += not still_open  # a closed key is never reopened (must stay 0)
            continue
        row = Conflict(
            patient_id=patient_id,
            conflict_type="value_mismatch_same_date",
            description=CONFLICT_DESCRIPTION,
            detected_by_rule=conflict.rule_version,
            analyte_id=conflict.analyte_id,
            effective_date=conflict.effective_date,
            member_count_at_detection=len(conflict.members),
            detection_key=key,
            rule_version=conflict.rule_version,
        )
        session.add(row)
        session.flush()
        for member in conflict.members:
            session.add(
                ConflictMember(conflict_id=row.id, fact_id=uuid.UUID(member), patient_id=patient_id)
            )
        opened += 1
    session.flush()
    if recurrences:
        logger.warning(
            "Conflict recomputation: patient_id=%s closed_key_recurrence=%d",
            patient_id,
            recurrences,
        )
    return opened, closed


def close_conflicts_for_deleted_document(
    session: Session,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    detector: ConflictDetector | None,
) -> int:
    """Inside ``delete_document``'s transaction, before the cascade (§5.6, §6.7).

    With a detector: recompute without the document's facts (reason ``member_deleted``), so the
    remaining facts can open a new conflict. Without one: close every non-closed conflict that
    has a member in the document. Returns the number closed.
    """
    if detector is not None:
        return _recompute_conflicts(
            session, patient_id, detector, reason="member_deleted", exclude_document=document_id
        )[1]
    _lock_patient(session, patient_id)
    member_of_document = (
        select(ConflictMember.conflict_id)
        .join(
            ExtractedFact,
            (ExtractedFact.id == ConflictMember.fact_id)
            & (ExtractedFact.patient_id == ConflictMember.patient_id),
        )
        .where(ExtractedFact.patient_id == patient_id, ExtractedFact.document_id == document_id)
    )
    result = session.execute(
        update(Conflict)
        .where(
            Conflict.patient_id == patient_id,
            Conflict.status != ConflictStatus.CLOSED.value,
            Conflict.id.in_(member_of_document),
        )
        .values(
            status=ConflictStatus.CLOSED.value, closed_at=func.now(), closed_reason="member_deleted"
        )
    )
    return int(result.rowcount or 0)


@dataclass(frozen=True)
class ConflictRecord:
    id: uuid.UUID
    status: str
    conflict_type: str
    description: str  # a template code
    analyte_id: int | None
    analyte_name: str | None = field(repr=False)
    effective_date: dt.date | None
    member_count_at_detection: int | None
    closed_reason: str | None
    closed_at: dt.datetime | None
    created_at: dt.datetime
    member_fact_ids: tuple[uuid.UUID, ...]


def list_conflicts(
    patient_id: uuid.UUID,
    *,
    current: bool = True,
    factory: sessionmaker[Session] | None = None,
) -> tuple[ConflictRecord, ...]:
    """Current conflicts (open or acknowledged, every member current and usable) or history.

    ``current=False`` lists closed conflicts with their reason and any members that still exist.
    """
    with session_scope(factory) as session:
        statuses = (
            [ConflictStatus.OPEN.value, ConflictStatus.ACKNOWLEDGED.value]
            if current
            else [ConflictStatus.CLOSED.value]
        )
        rows = session.execute(
            select(Conflict, AnalyteCatalog.canonical_name)
            .outerjoin(AnalyteCatalog, AnalyteCatalog.id == Conflict.analyte_id)
            .where(Conflict.patient_id == patient_id, Conflict.status.in_(statuses))
            .order_by(Conflict.created_at.desc(), Conflict.id)
        ).all()
        result = []
        for conflict, name in rows:
            members = session.execute(
                select(ExtractedFact.id, ExtractedFact.review_status, ExtractedFact.superseded_at)
                .join(
                    ConflictMember,
                    (ConflictMember.fact_id == ExtractedFact.id)
                    & (ConflictMember.patient_id == ExtractedFact.patient_id),
                )
                .where(
                    ConflictMember.conflict_id == conflict.id,
                    ConflictMember.patient_id == patient_id,
                )
                .order_by(ExtractedFact.id)
            ).all()
            if current and any(m[2] is not None or m[1] not in USABLE_STATUSES for m in members):
                continue  # defensive: a stale row is never shown as current
            result.append(
                ConflictRecord(
                    id=conflict.id,
                    status=conflict.status,
                    conflict_type=conflict.conflict_type,
                    description=conflict.description,
                    analyte_id=conflict.analyte_id,
                    analyte_name=name,
                    effective_date=conflict.effective_date,
                    member_count_at_detection=conflict.member_count_at_detection,
                    closed_reason=conflict.closed_reason,
                    closed_at=conflict.closed_at,
                    created_at=conflict.created_at,
                    member_fact_ids=tuple(m[0] for m in members),
                )
            )
        return tuple(result)


def acknowledge_conflict(
    patient_id: uuid.UUID,
    conflict_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> str:
    """``open`` → ``acknowledged``; nothing else changes. Returns the resulting status.

    Raises:
        DocumentProcessingError: ``conflict_not_found`` (also another patient's or a closed one).
    """
    with session_scope(factory) as session:
        row = session.scalars(
            select(Conflict)
            .where(
                Conflict.id == conflict_id,
                Conflict.patient_id == patient_id,
                Conflict.status != ConflictStatus.CLOSED.value,
            )
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise _error(CONFLICT_NOT_FOUND, f"Conflict {conflict_id} not found for this patient")
        row.status = ConflictStatus.ACKNOWLEDGED.value
        status = row.status
    logger.info("Conflict acknowledged: conflict_id=%s", conflict_id)
    return status


# --- reference data (unscoped: no patient data) -----------------------------------------------


@dataclass(frozen=True)
class AnalyteSeed:
    canonical_name: str
    default_unit: str
    plausible_min: Decimal | None
    plausible_max: Decimal | None


@dataclass(frozen=True)
class SeedResult:
    analytes_added: int
    aliases_added: int
    conversions_added: int


def seed_reference_data(
    analytes: Sequence[AnalyteSeed],
    aliases: Sequence[tuple[str, str]],
    conversions: Sequence[tuple[str, str, str, Decimal, Decimal]],
    *,
    factory: sessionmaker[Session] | None = None,
) -> SeedResult:
    """Idempotently insert reference rows; never changes the meaning of an existing row.

    The only unscoped write function: it touches reference tables only. An existing analyte with
    another default unit or other bounds, an alias that maps elsewhere, or a conversion with
    another factor is refused with ``ValueError`` (nothing is written).
    """
    with session_scope(factory) as session:
        existing = {a.canonical_name: a for a in session.scalars(select(AnalyteCatalog))}
        added = 0
        for seed in analytes:
            row = existing.get(seed.canonical_name)
            if row is None:
                row = AnalyteCatalog(
                    canonical_name=seed.canonical_name,
                    default_unit=seed.default_unit,
                    plausible_min=seed.plausible_min,
                    plausible_max=seed.plausible_max,
                )
                session.add(row)
                existing[seed.canonical_name] = row
                added += 1
            elif (row.default_unit, row.plausible_min, row.plausible_max) != (
                seed.default_unit,
                seed.plausible_min,
                seed.plausible_max,
            ):
                raise ValueError("an existing analyte has a different definition")
        session.flush()
        ids = {name: row.id for name, row in existing.items()}
        alias_rows = {a.alias: a.analyte_id for a in session.scalars(select(AnalyteAlias))}
        aliases_added = 0
        for alias, name in aliases:
            if alias in alias_rows:
                if alias_rows[alias] != ids[name]:
                    raise ValueError("an existing alias maps to another analyte")
                continue
            session.add(AnalyteAlias(alias=alias, analyte_id=ids[name]))
            alias_rows[alias] = ids[name]
            aliases_added += 1
        conversion_rows = {
            (c.analyte_id, c.from_unit, c.to_unit): (c.factor, c.addend)
            for c in session.scalars(select(UnitConversion))
        }
        conversions_added = 0
        for name, from_unit, to_unit, factor, addend in conversions:
            key = (ids[name], from_unit, to_unit)
            if key in conversion_rows:
                if conversion_rows[key] != (factor, addend):
                    raise ValueError("an existing conversion has another factor")
                continue
            session.add(
                UnitConversion(
                    analyte_id=ids[name],
                    from_unit=from_unit,
                    to_unit=to_unit,
                    factor=factor,
                    addend=addend,
                )
            )
            conversions_added += 1
        result = SeedResult(added, aliases_added, conversions_added)
    logger.info(
        "Reference data seeded: analytes_added=%d aliases_added=%d conversions_added=%d",
        result.analytes_added,
        result.aliases_added,
        result.conversions_added,
    )
    return result


def reference_analyte_ids(
    names: Sequence[str], *, factory: sessionmaker[Session] | None = None
) -> dict[str, int]:
    """Catalogue ids of canonical analyte names (reference data only; unscoped, read-only).

    Raises:
        DocumentProcessingError: ``reference_data_not_seeded`` when a name is missing.
    """
    with session_scope(factory) as session:
        return _analyte_ids(session, set(names))


def count_current_decisions(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> int:
    """How many human decisions the document's current facts have (for the delete prompt)."""
    with session_scope(factory) as session:
        _require_document(session, patient_id, document_id)
        current = select(ExtractedFact.id).where(
            ExtractedFact.patient_id == patient_id,
            ExtractedFact.document_id == document_id,
            ExtractedFact.superseded_at.is_(None),
        )
        return int(
            session.scalar(
                select(func.count())
                .select_from(ReviewDecision)
                .where(ReviewDecision.patient_id == patient_id, ReviewDecision.fact_id.in_(current))
            )
            or 0
        )
