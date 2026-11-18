"""Patient-scoped clinical-fact repository (Phase 6; docs/PHASE_6_PLAN.md §4–§5; ADR-042).

The records layer is the only database access path (ADR-016). Every public function takes
``patient_id`` first; another patient's id behaves exactly like a missing one.

**Stage ``clinical_extract``** (separate from the frozen lab ``extract`` stage): the same
extraction gate as lab extraction (``extraction_gate_refusal``: pages, identity not ``mismatch``,
succeeded classify and identity runs), re-checked under the document row lock. A document whose
Phase 2 type is ``lab_report`` is refused (``lab_report``: the lab extractor owns it). A
``succeeded`` run is written in the same transaction as all its facts and the document's
``clinical_doc_type`` (all or nothing); an abstaining classification gives a ``skipped`` run with
its reason. Either completes the stage: a repeat is refused with ``already_extracted``. A failed
run is history only and the stage can be retried. Re-extraction is not offered in Phase 6.

**Review status**: extraction stores only ``validated`` or ``needs_review``. Human decisions go
through ``app.records.facts.record_review_decision`` (shared with Phase 3, unchanged), which
appends a ``review_decisions`` row; fact value columns are never updated.

The ``processing_status`` completion rule is not changed by this stage (backward compatible).
Logs carry identifiers, counts, statuses and codes only: never values, excerpts or names.
"""

import datetime as dt
import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import DocumentProcessingError
from app.records.db import session_scope
from app.records.facts import (
    ALREADY_EXTRACTED,
    FACT_NOT_FOUND,
    MACHINE_STATUSES,
    _gate,
)
from app.records.models import (
    AllergyMention,
    AssertionType,
    ClinicalDocType,
    ClinicalEvent,
    Document,
    ExtractedFact,
    FactType,
    MedicationMention,
    Page,
    PipelineRun,
    ReviewDecision,
    ReviewStatus,
    RunStatus,
)
from app.records.repositories import DOCUMENT_NOT_FOUND, _lock_document

logger = logging.getLogger(__name__)

CLINICAL_STAGE: Final = "clinical_extract"
LAB_REPORT: Final = "lab_report"
LAB_REPORT_REFUSAL: Final = "lab_report"  # the lab extractor owns lab reports
VERSION_KEYS: Final = (
    "classifier_version",
    "extractor_version",
    "normalizer_version",
    "reference_data_version",
    "date_policy_version",
)
SKIP_REASONS: Final = frozenset(
    {"unclassified", "ambiguous_type", "unsupported_type", "no_text", LAB_REPORT_REFUSAL}
)
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_CODE = re.compile(r"[a-z][a-z0-9_.]{0,63}")
_COMPLETED = frozenset({RunStatus.SUCCEEDED.value, RunStatus.SKIPPED.value})
_DATE_COLUMN: Final = {
    FactType.MEDICATION.value: "mention_date",
    FactType.ALLERGY.value: "mention_date",
    FactType.EVENT.value: "event_date",
}
# The subtype fields each fact type may carry (anything else is refused).
SUBTYPE_FIELDS: Final[dict[str, frozenset[str]]] = {
    FactType.MEDICATION.value: frozenset(
        {
            "name_raw",
            "name_norm",
            "name_match",
            "dosage_form",
            "strength_raw",
            "strength_value",
            "strength_unit",
            "dose_raw",
            "route_raw",
            "route_norm",
            "frequency_raw",
            "frequency_per_day",
            "as_needed",
            "duration_raw",
            "duration_value",
            "duration_unit",
            "instructions_raw",
            "mention_context",
        }
    ),
    FactType.ALLERGY.value: frozenset(
        {
            "substance_raw",
            "substance_norm",
            "reaction_raw",
            "severity_raw",
            "severity_norm",
            "allergy_status",
        }
    ),
    FactType.EVENT.value: frozenset({"event_type", "event_context", "description_raw"}),
}
_MODELS: Final = {
    FactType.MEDICATION.value: MedicationMention,
    FactType.ALLERGY.value: AllergyMention,
    FactType.EVENT.value: ClinicalEvent,
}


def _error(kind: str, message: str) -> DocumentProcessingError:
    return DocumentProcessingError(message, kind=kind)


# --- target -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClinicalTarget:
    """What the stage needs before it reads any file. Identifiers and codes only."""

    document_id: uuid.UUID
    sha256: str
    page_count: int | None
    doc_type: str
    reference_date: dt.date  # the upload date (UTC): the fixed future-date bound
    blocked_reason: str | None
    extracted: bool  # a succeeded or skipped clinical_extract run exists


def _has_clinical_run(
    session: Session, patient_id: uuid.UUID, document_id: uuid.UUID, statuses: frozenset[str]
) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    PipelineRun.patient_id == patient_id,
                    PipelineRun.document_id == document_id,
                    PipelineRun.stage == CLINICAL_STAGE,
                    PipelineRun.status.in_(statuses),
                )
            )
        )
    )


def get_clinical_target(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> ClinicalTarget:
    """The document's clinical-extraction pre-check. Read-only.

    Raises:
        DocumentProcessingError: ``document_not_found`` (also for another patient's document).
    """
    with session_scope(factory) as session:
        document = session.scalars(
            select(Document).where(Document.id == document_id, Document.patient_id == patient_id)
        ).one_or_none()
        if document is None:
            raise _error(DOCUMENT_NOT_FOUND, f"Document {document_id} not found for this patient")
        return ClinicalTarget(
            document_id=document.id,
            sha256=document.sha256,
            page_count=document.page_count,
            doc_type=document.doc_type,
            reference_date=document.created_at.astimezone(dt.UTC).date(),
            blocked_reason=_gate(session, patient_id, document),
            extracted=_has_clinical_run(session, patient_id, document_id, _COMPLETED),
        )


# --- writing ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClinicalFactRecord:
    """One clinical fact to store. ``values`` holds the subtype fields (see SUBTYPE_FIELDS)."""

    fact_type: str
    page_number: int
    source_locator: str
    source_excerpt: str = field(repr=False)
    char_start: int
    char_end: int
    subject: str
    negated: bool
    date: dt.date | None
    date_precision: str | None
    date_raw: str | None = field(repr=False)
    date_source: str | None
    date_page_number: int | None
    date_char_start: int | None
    date_char_end: int | None
    review_status: str
    review_reasons: tuple[str, ...]
    quality_notes: tuple[str, ...]
    values: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True)
class ClinicalExtractionRecord:
    document_id: uuid.UUID
    run_id: uuid.UUID
    status: str  # succeeded / skipped
    clinical_doc_type: str | None
    facts: int
    needs_review: int
    skip_reason: str | None = None


def _check_versions(versions: Mapping[str, str]) -> dict[str, str]:
    if set(versions) != set(VERSION_KEYS):
        raise ValueError("versions must name exactly the clinical version keys")
    for value in versions.values():
        if not isinstance(value, str) or not _VERSION.fullmatch(value):
            raise ValueError("versions must be short identifiers")
    return dict(versions)


def _check_codes(codes: Sequence[str]) -> tuple[str, ...]:
    for code in codes:
        if not isinstance(code, str) or not _CODE.fullmatch(code):
            raise ValueError("codes must be fixed identifiers")
    return tuple(codes)


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


def _check_facts(facts: Sequence[ClinicalFactRecord]) -> None:
    locators: set[tuple[str, str]] = set()
    for record in facts:
        if record.fact_type not in SUBTYPE_FIELDS:
            raise ValueError("unknown clinical fact type")
        if set(record.values) - SUBTYPE_FIELDS[record.fact_type]:
            raise ValueError("unknown subtype field")
        if record.review_status not in MACHINE_STATUSES:
            raise ValueError("extraction stores only validated or needs_review")
        if (record.review_status == ReviewStatus.NEEDS_REVIEW.value) != bool(record.review_reasons):
            raise ValueError("needs_review exactly when there are reasons")
        _check_codes(record.review_reasons)
        _check_codes(record.quality_notes)
        key = (record.fact_type, record.source_locator)
        if key in locators:
            raise ValueError("duplicate source locator")
        locators.add(key)
        if not 0 <= record.char_start <= record.char_end:
            raise ValueError("invalid character span")
        if "\x00" in record.source_excerpt:
            raise ValueError("text that cannot be stored")
        if record.fact_type == FactType.ALLERGY.value and record.negated != (
            record.values.get("allergy_status") == "none_known"
        ):
            raise ValueError("a none-known allergy statement is exactly the negated one")


def _check_pages(
    session: Session,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    facts: Sequence[ClinicalFactRecord],
) -> None:
    pages = set(
        session.scalars(
            select(Page.page_number).where(
                Page.patient_id == patient_id, Page.document_id == document_id
            )
        )
    )
    for record in facts:
        if record.page_number not in pages:
            raise ValueError("a fact cites a page the document does not have")
        if record.date_page_number is not None and record.date_page_number not in pages:
            raise ValueError("a date cites a page the document does not have")


def _run(
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
        stage=CLINICAL_STAGE,
        status=status.value,
        started_at=func.now() - dt.timedelta(milliseconds=duration_ms),
        finished_at=func.now(),
        duration_ms=duration_ms,
        component_versions=component_versions,
        **extra,
    )


def _locked_target(
    session: Session,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    lab_report_expected: bool = False,
) -> Document:
    document = _lock_document(session, patient_id, document_id)
    refusal = _gate(session, patient_id, document)
    if refusal is not None:
        raise _error(refusal, f"Document {document_id} cannot be extracted ({refusal})")
    if (document.doc_type == LAB_REPORT) != lab_report_expected:
        raise _error(LAB_REPORT_REFUSAL, f"Document {document_id}: lab-report rule refused")
    if _has_clinical_run(session, patient_id, document_id, _COMPLETED):
        raise _error(ALREADY_EXTRACTED, f"Document {document_id} is already extracted")
    return document


def record_clinical_extraction(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    clinical_doc_type: str,
    facts: Sequence[ClinicalFactRecord],
    versions: Mapping[str, str],
    reference_date: dt.date,
    rule_ids: Sequence[str],
    abstentions: Mapping[str, int],
    duration_ms: int,
    factory: sessionmaker[Session] | None = None,
) -> ClinicalExtractionRecord:
    """Store a clinical extraction: one succeeded run, all its facts and the document's
    ``clinical_doc_type``, in one transaction (all or nothing).

    Raises:
        ValueError: invalid facts, versions, counts, codes or duration.
        DocumentProcessingError: ``document_not_found``, a gate refusal, ``lab_report``,
            ``already_extracted``.
    """
    if clinical_doc_type not in ClinicalDocType._value2member_map_:
        raise ValueError("unknown clinical document type")
    _check_facts(facts)
    clean_versions = _check_versions(versions)
    clean_abstentions = _check_counts(abstentions)
    clean_rules = _check_codes(rule_ids)
    _check_duration(duration_ms)
    needs_review = sum(f.review_status == ReviewStatus.NEEDS_REVIEW.value for f in facts)
    with session_scope(factory) as session:
        document = _locked_target(session, patient_id, document_id)
        _check_pages(session, patient_id, document_id, facts)
        run = _run(
            patient_id,
            document_id,
            RunStatus.SUCCEEDED,
            duration_ms,
            {
                **clean_versions,
                "reference_date": reference_date.isoformat(),
                "clinical_doc_type": clinical_doc_type,
                "rule_ids": list(clean_rules),
                "facts_written": len(facts),
                "facts_needs_review": needs_review,
                "abstentions": clean_abstentions,
            },
        )
        session.add(run)
        session.flush()
        for record in facts:
            session.add(_fact_row(patient_id, document_id, run.id, record, clean_versions))
        document.clinical_doc_type = clinical_doc_type
        session.flush()
        result = ClinicalExtractionRecord(
            document_id,
            run.id,
            RunStatus.SUCCEEDED.value,
            clinical_doc_type,
            len(facts),
            needs_review,
        )
    logger.info(
        "Clinical extraction recorded: document_id=%s run_id=%s type=%s facts=%d needs_review=%d",
        document_id,
        result.run_id,
        clinical_doc_type,
        result.facts,
        result.needs_review,
    )
    return result


def _fact_row(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    run_id: uuid.UUID,
    record: ClinicalFactRecord,
    versions: Mapping[str, str],
) -> ExtractedFact:
    model = _MODELS[record.fact_type]
    return model(
        patient_id=patient_id,
        document_id=document_id,
        page_number=record.page_number,
        source_excerpt=record.source_excerpt,
        char_start=record.char_start,
        char_end=record.char_end,
        assertion_type=AssertionType.DIRECT_FACT.value,
        negated=record.negated,
        subject=record.subject,
        review_status=record.review_status,
        review_reasons=list(record.review_reasons),
        extractor_version=versions["extractor_version"],
        prompt_version="none",
        model_id="none",
        extraction_run_id=run_id,
        source_locator=record.source_locator,
        normalizer_version=versions["normalizer_version"],
        reference_data_version=versions["reference_data_version"],
        quality_notes=list(record.quality_notes),
        date_precision=record.date_precision,
        date_raw=record.date_raw,
        date_source=record.date_source,
        date_page_number=record.date_page_number,
        date_char_start=record.date_char_start,
        date_char_end=record.date_char_end,
        **{_DATE_COLUMN[record.fact_type]: record.date},
        **dict(record.values),
    )


def record_clinical_skipped(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    reason: str,
    versions: Mapping[str, str],
    rule_ids: Sequence[str],
    duration_ms: int,
    factory: sessionmaker[Session] | None = None,
) -> ClinicalExtractionRecord:
    """A ``skipped`` run, no facts, type left NULL: the Phase 6 classifier named no type, or
    (reason ``lab_report``) the document is a lab report, which the lab extractor owns.

    Raises:
        ValueError: unknown reason, invalid versions, codes or duration.
        DocumentProcessingError: ``document_not_found``, a gate refusal, ``lab_report`` (the
            reason does not fit the document's Phase 2 type), ``already_extracted``.
    """
    if reason not in SKIP_REASONS:
        raise ValueError("unknown skip reason")
    clean_versions = _check_versions(versions)
    clean_rules = _check_codes(rule_ids)
    _check_duration(duration_ms)
    with session_scope(factory) as session:
        _locked_target(
            session,
            patient_id,
            document_id,
            lab_report_expected=reason == LAB_REPORT_REFUSAL,
        )
        run = _run(
            patient_id,
            document_id,
            RunStatus.SKIPPED,
            duration_ms,
            {**clean_versions, "skip_reason": reason, "rule_ids": list(clean_rules)},
        )
        session.add(run)
        session.flush()
        result = ClinicalExtractionRecord(
            document_id, run.id, RunStatus.SKIPPED.value, None, 0, 0, reason
        )
    logger.info(
        "Clinical extraction skipped: document_id=%s run_id=%s reason=%s",
        document_id,
        result.run_id,
        reason,
    )
    return result


def record_clinical_failure(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    error_kind: str,
    error_class: str | None,
    versions: Mapping[str, str],
    duration_ms: int,
    factory: sessionmaker[Session] | None = None,
) -> uuid.UUID:
    """A ``failed`` run (history only; the stage can be retried). Codes and class names only."""
    if not _CODE.fullmatch(error_kind):
        raise ValueError("error_kind must be a fixed code")
    clean_versions = _check_versions(versions)
    _check_duration(duration_ms)
    with session_scope(factory) as session:
        _lock_document(session, patient_id, document_id)
        run = _run(
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
        return run.id


# --- reading ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClinicalDecision:
    id: uuid.UUID
    decision: str
    corrected_values: dict[str, Any] | None = field(repr=False)
    decided_at: dt.datetime


@dataclass(frozen=True)
class ClinicalFactView:
    """A stored clinical fact with its provenance, document and decisions (newest last)."""

    id: uuid.UUID
    fact_type: str
    document_id: uuid.UUID
    document_name: str = field(repr=False)
    clinical_doc_type: str | None
    document_date: dt.date | None
    page_number: int
    source_locator: str
    source_excerpt: str = field(repr=False)
    char_start: int
    char_end: int
    subject: str
    negated: bool
    date: dt.date | None
    date_precision: str | None
    date_raw: str | None = field(repr=False)
    date_source: str | None
    date_page_number: int | None
    date_char_start: int | None
    date_char_end: int | None
    review_status: str
    review_reasons: tuple[str, ...]
    quality_notes: tuple[str, ...]
    values: dict[str, Any] = field(repr=False)
    decisions: tuple[ClinicalDecision, ...] = ()
    identity_check: str = "unknown"  # the source document's identity outcome (acceptance fix)

    @property
    def corrected_values(self) -> dict[str, Any] | None:
        """The latest decision's corrected values when that decision is a correction."""
        if self.decisions and self.decisions[-1].decision == "correct":
            return self.decisions[-1].corrected_values
        return None


def _views(session: Session, patient_id: uuid.UUID, *conditions: Any) -> list[ClinicalFactView]:
    views: list[ClinicalFactView] = []
    for fact_type, model in _MODELS.items():
        rows = session.execute(
            select(
                model,
                Document.original_filename,
                Document.clinical_doc_type,
                Document.identity_check,
            )
            .join(
                Document,
                (Document.id == model.document_id) & (Document.patient_id == model.patient_id),
            )
            .where(model.patient_id == patient_id, model.superseded_at.is_(None), *conditions)
        ).all()
        for fact, name, clinical_type, identity in rows:
            values = {k: getattr(fact, k) for k in sorted(SUBTYPE_FIELDS[fact_type])}
            views.append(
                ClinicalFactView(
                    id=fact.id,
                    fact_type=fact_type,
                    document_id=fact.document_id,
                    document_name=name,
                    clinical_doc_type=clinical_type,
                    document_date=None,
                    page_number=fact.page_number,
                    source_locator=fact.source_locator,
                    source_excerpt=fact.source_excerpt,
                    char_start=fact.char_start or 0,
                    char_end=fact.char_end or 0,
                    subject=fact.subject,
                    negated=fact.negated,
                    date=getattr(fact, _DATE_COLUMN[fact_type]),
                    date_precision=fact.date_precision,
                    date_raw=fact.date_raw,
                    date_source=fact.date_source,
                    date_page_number=fact.date_page_number,
                    date_char_start=fact.date_char_start,
                    date_char_end=fact.date_char_end,
                    review_status=fact.review_status,
                    review_reasons=tuple(fact.review_reasons),
                    quality_notes=tuple(fact.quality_notes),
                    values=values,
                    identity_check=identity,
                )
            )
    if not views:
        return []
    decisions: dict[uuid.UUID, list[ClinicalDecision]] = {}
    for d in session.scalars(
        select(ReviewDecision)
        .where(
            ReviewDecision.patient_id == patient_id,
            ReviewDecision.fact_id.in_([v.id for v in views]),
        )
        .order_by(ReviewDecision.decided_at, ReviewDecision.id)
    ):
        decisions.setdefault(d.fact_id, []).append(
            ClinicalDecision(d.id, d.decision, d.corrected_values, d.decided_at)
        )
    dates = _document_dates(session, patient_id, {v.document_id for v in views})
    result = [
        _with(v, decisions=tuple(decisions.get(v.id, ())), document_date=dates.get(v.document_id))
        for v in views
    ]
    return sorted(result, key=lambda v: (str(v.document_id), v.page_number, v.char_start))


def _with(view: ClinicalFactView, **changes: Any) -> ClinicalFactView:
    from dataclasses import replace

    return replace(view, **changes)


def _document_dates(
    session: Session, patient_id: uuid.UUID, document_ids: set[uuid.UUID]
) -> dict[uuid.UUID, dt.date | None]:
    """The document date of each document: the date its own clinical facts were dated by."""
    found: dict[uuid.UUID, dt.date | None] = {}
    for model in _MODELS.values():
        date_column = getattr(model, _DATE_COLUMN[model.__mapper_args__["polymorphic_identity"]])
        for document_id, value in session.execute(
            select(model.document_id, func.min(date_column))
            .where(
                model.patient_id == patient_id,
                model.document_id.in_(document_ids),
                model.date_source == "document",
                model.superseded_at.is_(None),
            )
            .group_by(model.document_id)
        ):
            if value is not None and (found.get(document_id) is None or value < found[document_id]):
                found[document_id] = value
    return found


def list_clinical_facts(
    patient_id: uuid.UUID,
    *,
    document_id: uuid.UUID | None = None,
    factory: sessionmaker[Session] | None = None,
) -> tuple[ClinicalFactView, ...]:
    """The patient's current clinical facts (every review status), with decisions.

    Raises:
        DocumentProcessingError: ``document_not_found`` when ``document_id`` names a document
            this patient does not have.
    """
    with session_scope(factory) as session:
        conditions: list[Any] = []
        if document_id is not None:
            if not session.scalar(
                select(
                    exists().where(Document.id == document_id, Document.patient_id == patient_id)
                )
            ):
                raise _error(DOCUMENT_NOT_FOUND, f"Document {document_id} not found")
            conditions.append(ExtractedFact.document_id == document_id)
        return tuple(_views(session, patient_id, *conditions))


def get_clinical_fact(
    patient_id: uuid.UUID,
    fact_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> ClinicalFactView:
    """One current clinical fact. Another patient's, a superseded or a lab fact: not found."""
    with session_scope(factory) as session:
        views = _views(session, patient_id, ExtractedFact.id == fact_id)
    if not views:
        raise _error(FACT_NOT_FOUND, f"Fact {fact_id} not found for this patient")
    return views[0]


@dataclass(frozen=True)
class ClinicalDocumentView:
    """One document's Phase 6 state. Identifiers, names and codes only."""

    document_id: uuid.UUID
    document_name: str = field(repr=False)
    doc_type: str
    clinical_doc_type: str | None
    run_status: str | None  # latest clinical_extract run: succeeded / skipped / failed / None
    skip_reason: str | None
    error_kind: str | None
    facts: int
    needs_review: int


def list_clinical_documents(
    patient_id: uuid.UUID, *, factory: sessionmaker[Session] | None = None
) -> tuple[ClinicalDocumentView, ...]:
    """Every document of the patient with its latest ``clinical_extract`` outcome."""
    with session_scope(factory) as session:
        documents = session.scalars(
            select(Document)
            .where(Document.patient_id == patient_id)
            .order_by(Document.created_at, Document.id)
        ).all()
        runs: dict[uuid.UUID, PipelineRun] = {}
        for run in session.scalars(
            select(PipelineRun)
            .where(PipelineRun.patient_id == patient_id, PipelineRun.stage == CLINICAL_STAGE)
            .order_by(PipelineRun.started_at, PipelineRun.id)
        ):
            if run.document_id is not None:
                runs[run.document_id] = run
        counts: dict[uuid.UUID, tuple[int, int]] = {}
        for document_id, total, review in session.execute(
            select(
                ExtractedFact.document_id,
                func.count(),
                func.count().filter(ExtractedFact.review_status == "needs_review"),
            )
            .where(
                ExtractedFact.patient_id == patient_id,
                ExtractedFact.fact_type != FactType.OBSERVATION.value,
                ExtractedFact.superseded_at.is_(None),
            )
            .group_by(ExtractedFact.document_id)
        ):
            counts[document_id] = (total, review)
        result = []
        for document in documents:
            run = runs.get(document.id)
            total, review = counts.get(document.id, (0, 0))
            result.append(
                ClinicalDocumentView(
                    document_id=document.id,
                    document_name=document.original_filename,
                    doc_type=document.doc_type,
                    clinical_doc_type=document.clinical_doc_type,
                    run_status=run.status if run else None,
                    skip_reason=(run.component_versions or {}).get("skip_reason") if run else None,
                    error_kind=run.error_kind if run else None,
                    facts=total,
                    needs_review=review,
                )
            )
        return tuple(result)


def stored_page_texts(
    patient_id: uuid.UUID,
    keys: Sequence[tuple[uuid.UUID, int]],
    *,
    factory: sessionmaker[Session] | None = None,
) -> dict[tuple[str, int], str]:
    """The stored text of the requested (document, page) pairs of THIS patient only.

    Read-only; used to check clinical citations at answer time. A pair of another patient, or a
    page that does not exist, is simply absent from the result.
    """
    documents = sorted({document_id for document_id, _ in keys})
    if not documents:
        return {}
    wanted = {(str(d), n) for d, n in keys}
    with session_scope(factory) as session:
        rows = session.execute(
            select(Page.document_id, Page.page_number, Page.text).where(
                Page.patient_id == patient_id, Page.document_id.in_(documents)
            )
        ).all()
    return {
        (str(document_id), number): text
        for document_id, number, text in rows
        if (str(document_id), number) in wanted
    }
