"""Extract lab observations from one parsed document (Phase 3; docs/PHASE_3_PLAN.md §9).

    1. get_extraction_target      the gate pre-check (pages, identity, classification), the doc
                                  type, the upload date (the fixed date-policy reference)
    2. not a lab report           record a ``skipped`` run (outcome ``not_applicable``)
    3. verified parse outputs     manifest, producing run, IR verified against the manifest
    4. list_document_pages        the stored page texts (the extractor checks they equal the IR)
    5. extract_lab_observations   pure, deterministic (``lab-rules-1``)
    6. record_extraction          one transaction: lock, re-check gate, run + facts, document
                                  date, conflict recomputation
    7. complete_document_if_ready ``processing`` → ``processed`` when every stage is complete

Independent of indexing: both read the verified IR; neither waits for the other.

Outcomes: ``document_not_found``, ``not_parsed``, the gate refusals (``identity_mismatch``,
``identity_not_checked``, ``not_classified``) and ``already_extracted`` propagate with no run
written. Any other failure is recorded as a ``failed`` extract run (best effort) and the original
exception is re-raised; the stage can be retried. Logs carry ids, counts and kinds only.
"""

import logging
import re
import time
import uuid
from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import DocumentProcessingError
from app.extraction.lab.extract import extract_lab_observations
from app.extraction.reference import load_reference
from app.extraction.schemas import ExtractionOutput, ObservationDraft
from app.extraction.versions import DATE_POLICY_VERSION, EXTRACTOR_VERSION, NORMALIZER_VERSION
from app.ingestion.parse_outputs import load_verified_parse_outputs, read_published_manifest
from app.records.db import describe_db_error, is_database_error
from app.records.facts import (
    ALREADY_EXTRACTED,
    EXTRACT_GATE_REFUSALS,
    NOT_PARSED,
    ObservationRecord,
    ReportDate,
    complete_document_if_ready,
    extraction_applies,
    get_extraction_target,
    record_extraction,
    record_extraction_failure,
    record_extraction_skipped,
    reextract_and_supersede,
)
from app.records.repositories import (
    DOCUMENT_NOT_FOUND,
    ProcessingErrorKind,
    list_document_pages,
    parse_run_belongs_to_document,
)
from app.services.fact_conflicts import build_detector

logger = logging.getLogger(__name__)

EXTRACTED = "extracted"
NOT_APPLICABLE = "not_applicable"
EXTRACTION_FAILED = "extraction_failed"
DATABASE_ERROR = "database_error"
INTERRUPTED = "interrupted"
NOT_RECORDED_KINDS = frozenset(
    {DOCUMENT_NOT_FOUND, NOT_PARSED, ALREADY_EXTRACTED, *EXTRACT_GATE_REFUSALS}
)
_SAFE_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")


@dataclass(frozen=True)
class ExtractResult:
    """Identifiers, counts and codes only."""

    document_id: uuid.UUID
    outcome: str  # extracted / not_applicable
    run_id: uuid.UUID
    facts: int
    needs_review: int
    abstentions: dict[str, int]
    superseded: int
    conflicts_opened: int
    conflicts_closed: int
    completed: bool  # the document became ``processed`` in this call
    duration_ms: int


def current_versions() -> dict[str, str]:
    return {
        "extractor_version": EXTRACTOR_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "reference_data_version": load_reference().version,
        "date_policy_version": DATE_POLICY_VERSION,
    }


def to_record(draft: ObservationDraft) -> ObservationRecord:
    return ObservationRecord(
        page_number=draft.page_number,
        source_locator=draft.source_locator,
        source_excerpt=draft.source_excerpt,
        char_start=draft.char_start,
        char_end=draft.char_end,
        analyte_raw=draft.analyte_raw,
        analyte_name=draft.analyte_name,
        analyte_match=draft.analyte_match,
        value_raw=draft.value_raw,
        value_num=draft.value_num,
        comparator=draft.comparator,
        value_qualitative=draft.value_qualitative,
        unit_raw=draft.unit_raw,
        unit_norm=draft.unit_norm,
        value_num_norm=draft.value_num_norm,
        ref_range_raw=draft.ref_range_raw,
        ref_low=draft.ref_low,
        ref_high=draft.ref_high,
        flag_as_printed=draft.flag_as_printed,
        ref_range_comparison=draft.ref_range_comparison,
        specimen=draft.specimen,
        effective_date=draft.effective_date,
        date_precision=draft.date_precision,
        date_raw=draft.date_raw,
        effective_date_source=draft.effective_date_source,
        date_page_number=draft.date_page_number,
        date_char_start=draft.date_char_start,
        date_char_end=draft.date_char_end,
        effective_time=draft.effective_time,
        time_raw=draft.time_raw,
        review_status=draft.review_status,
        review_reasons=draft.review_reasons,
        quality_notes=draft.quality_notes,
    )


def _report_date(output: ExtractionOutput) -> ReportDate | None:
    found = output.report_date
    if found is None or found.value is None or found.precision is None:
        return None
    return ReportDate(found.value, found.precision, found.raw)


def _mismatch(document_id: uuid.UUID, message: str) -> DocumentProcessingError:
    return DocumentProcessingError(
        f"Document {document_id}: {message}", kind=ProcessingErrorKind.INTEGRITY_MISMATCH
    )


def extract_document(
    settings: Settings,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    reextract: bool = False,
) -> ExtractResult:
    """Extract (or, with ``reextract``, re-extract) one document of this patient.

    Raises:
        DocumentProcessingError: ``document_not_found``, ``not_parsed``, a gate refusal,
            ``already_extracted`` (nothing recorded); ``not_reextractable``,
            ``has_review_decisions``, ``integrity_mismatch``, ``reference_data_not_seeded`` or
            anything raised while extracting or storing (recorded as a failed run, re-raised).
    """
    started = time.perf_counter()
    target = get_extraction_target(patient_id, document_id)
    if target.blocked_reason is not None:
        raise DocumentProcessingError(
            f"Document {document_id} may not be extracted ({target.blocked_reason})",
            kind=target.blocked_reason,
        )
    if target.extracted and not reextract:
        raise DocumentProcessingError(
            f"Document {document_id} is already extracted", kind=ALREADY_EXTRACTED
        )
    versions = current_versions()
    try:
        if not extraction_applies(target.doc_type):
            record = record_extraction_skipped(
                patient_id, document_id, versions=versions, duration_ms=_elapsed_ms(started)
            )
            output = None
        else:
            output = _extract(settings, patient_id, document_id, target)
            write = reextract_and_supersede if reextract else record_extraction
            record = write(
                patient_id,
                document_id,
                observations=[to_record(d) for d in output.observations],
                versions=versions,
                reference_date=target.reference_date,
                abstentions=output.abstentions,
                report_date=_report_date(output),
                duration_ms=_elapsed_ms(started),
                detector=build_detector(),
            )
    except (Exception, KeyboardInterrupt) as exc:
        if not (isinstance(exc, DocumentProcessingError) and exc.kind in NOT_RECORDED_KINDS):
            _record_failure(patient_id, document_id, exc, _failure_kind(exc), versions, started)
        raise
    completed = complete_document_if_ready(patient_id, document_id)
    result = ExtractResult(
        document_id=document_id,
        outcome=EXTRACTED if output is not None else NOT_APPLICABLE,
        run_id=record.run_id,
        facts=record.facts,
        needs_review=record.needs_review,
        abstentions=dict(output.abstentions) if output is not None else {},
        superseded=record.superseded,
        conflicts_opened=record.conflicts_opened,
        conflicts_closed=record.conflicts_closed,
        completed=completed,
        duration_ms=_elapsed_ms(started),
    )
    logger.info(
        "Extraction done: document_id=%s outcome=%s facts=%d needs_review=%d completed=%s "
        "duration_ms=%d",
        document_id,
        result.outcome,
        result.facts,
        result.needs_review,
        completed,
        result.duration_ms,
    )
    return result


def _extract(settings: Settings, patient_id, document_id, target) -> ExtractionOutput:
    if target.page_count is None:
        raise _mismatch(document_id, "no recorded page count")
    manifest = read_published_manifest(
        settings, patient_id=patient_id, document_id=document_id, source_sha256=target.sha256
    )
    if manifest is None:
        raise _mismatch(document_id, "no published parse outputs")
    if not parse_run_belongs_to_document(patient_id, document_id, manifest.parse_run_id):
        raise _mismatch(document_id, "the parse outputs name a run of another document")
    outputs = load_verified_parse_outputs(
        settings,
        patient_id=patient_id,
        document_id=document_id,
        parse_run_id=manifest.parse_run_id,
        source_sha256=target.sha256,
        expected_page_count=target.page_count,
    )
    if outputs is None:
        raise _mismatch(document_id, "the published parse outputs are missing")
    stored = {page.page_number: page.text for page in list_document_pages(patient_id, document_id)}
    return extract_lab_observations(outputs.document, stored, reference_date=target.reference_date)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _failure_kind(exc: BaseException) -> str:
    if isinstance(exc, KeyboardInterrupt):
        return INTERRUPTED
    if isinstance(exc, DocumentProcessingError) and exc.kind:
        return str(exc.kind)
    if is_database_error(exc):
        return DATABASE_ERROR
    return EXTRACTION_FAILED


def _record_failure(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    exc: BaseException,
    kind: str,
    versions: dict[str, str],
    started: float,
) -> None:
    """Best effort. NEVER raises, so the caller always re-raises the original exception."""
    name = type(exc).__name__
    try:
        record_extraction_failure(
            patient_id,
            document_id,
            error_kind=kind,
            error_class=name if _SAFE_CLASS.fullmatch(name) else None,
            versions=versions,
            duration_ms=_elapsed_ms(started),
        )
    except BaseException as failure:  # deliberate: must never replace the original exception
        try:
            reason = (
                describe_db_error(failure) if is_database_error(failure) else type(failure).__name__
            )
            logger.warning(
                "Extraction failure could not be recorded: document_id=%s (%s)",
                document_id,
                reason,
            )
        except BaseException:  # deliberate: logging must not replace the original either
            pass
