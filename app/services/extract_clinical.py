"""The ``clinical_extract`` stage: classify and extract one non-lab document (Phase 6).

    1. get_clinical_target       the gate pre-check, the Phase 2 type, the upload date
    2. a Phase 2 lab report      ``skipped`` run (reason ``lab_report``: the lab stage owns it)
    3. verified parse outputs    manifest, producing run, IR verified against the manifest
    4. clinical-doctype-1        abstention → ``skipped`` run with its reason (no guess)
    5. clinical-rules-1          pure, deterministic drafts with exact spans
    6. record_clinical_extraction  one transaction: lock, re-check gate, run + facts + type

Outcomes ``document_not_found``, ``not_parsed``, the gate refusals and ``already_extracted``
propagate with no run written. Any other failure is recorded as a ``failed`` run (best effort) and
the original exception is re-raised; the stage can be retried. Logs: ids, counts and codes only.
"""

import logging
import re
import time
import uuid
from dataclasses import dataclass

from app.clinical.classify import classify_clinical
from app.clinical.extract import extract_clinical_facts
from app.clinical.lexicon import load_lexicon
from app.clinical.schemas import (
    AllergyDraft,
    ClinicalDraft,
    ClinicalExtractionOutput,
    EventDraft,
    MedicationDraft,
)
from app.clinical.versions import (
    CLINICAL_CLASSIFIER_VERSION,
    CLINICAL_EXTRACTOR_VERSION,
    CLINICAL_NORMALIZER_VERSION,
)
from app.core.config import Settings
from app.core.errors import DocumentProcessingError
from app.extraction.versions import DATE_POLICY_VERSION
from app.ingestion.ir import ParsedDocument
from app.ingestion.parse_outputs import load_verified_parse_outputs, read_published_manifest
from app.records.clinical import (
    LAB_REPORT,
    LAB_REPORT_REFUSAL,
    ClinicalFactRecord,
    get_clinical_target,
    record_clinical_extraction,
    record_clinical_failure,
    record_clinical_skipped,
)
from app.records.db import describe_db_error, is_database_error
from app.records.facts import ALREADY_EXTRACTED, EXTRACT_GATE_REFUSALS, NOT_PARSED
from app.records.repositories import (
    DOCUMENT_NOT_FOUND,
    ProcessingErrorKind,
    list_document_pages,
    parse_run_belongs_to_document,
)

logger = logging.getLogger(__name__)

EXTRACTED = "extracted"
SKIPPED = "skipped"
EXTRACTION_FAILED = "extraction_failed"
DATABASE_ERROR = "database_error"
INTERRUPTED = "interrupted"
NOT_RECORDED_KINDS = frozenset(
    {DOCUMENT_NOT_FOUND, NOT_PARSED, ALREADY_EXTRACTED, *EXTRACT_GATE_REFUSALS}
)
_SAFE_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")
_BASE_FIELDS = frozenset(ClinicalDraft.model_fields)


@dataclass(frozen=True)
class ClinicalExtractResult:
    """Identifiers, counts and codes only."""

    document_id: uuid.UUID
    outcome: str  # extracted / skipped
    run_id: uuid.UUID
    clinical_doc_type: str | None
    skip_reason: str | None
    facts: int
    needs_review: int
    abstentions: dict[str, int]
    duration_ms: int


def current_versions() -> dict[str, str]:
    return {
        "classifier_version": CLINICAL_CLASSIFIER_VERSION,
        "extractor_version": CLINICAL_EXTRACTOR_VERSION,
        "normalizer_version": CLINICAL_NORMALIZER_VERSION,
        "reference_data_version": load_lexicon().version,
        "date_policy_version": DATE_POLICY_VERSION,
    }


def to_record(draft: ClinicalDraft) -> ClinicalFactRecord:
    """A draft as the repository stores it (base fields plus the subtype's own values)."""
    if isinstance(draft, MedicationDraft):
        fact_type = "medication"
    elif isinstance(draft, AllergyDraft):
        fact_type = "allergy"
    elif isinstance(draft, EventDraft):
        fact_type = "event"
    else:
        raise TypeError("unknown draft type")
    data = draft.model_dump()
    values = {k: v for k, v in data.items() if k not in _BASE_FIELDS}
    return ClinicalFactRecord(
        fact_type=fact_type,
        page_number=draft.page_number,
        source_locator=draft.source_locator,
        source_excerpt=draft.source_excerpt,
        char_start=draft.char_start,
        char_end=draft.char_end,
        subject=draft.subject,
        negated=draft.negated,
        date=draft.date,
        date_precision=draft.date_precision,
        date_raw=draft.date_raw,
        date_source=draft.date_source,
        date_page_number=draft.date_page_number,
        date_char_start=draft.date_char_start,
        date_char_end=draft.date_char_end,
        review_status=draft.review_status,
        review_reasons=draft.review_reasons,
        quality_notes=draft.quality_notes,
        values=values,
    )


def _mismatch(document_id: uuid.UUID, message: str) -> DocumentProcessingError:
    return DocumentProcessingError(
        f"Document {document_id}: {message}", kind=ProcessingErrorKind.INTEGRITY_MISMATCH
    )


def extract_clinical_document(
    settings: Settings, patient_id: uuid.UUID, document_id: uuid.UUID
) -> ClinicalExtractResult:
    """Run the ``clinical_extract`` stage for one document of this patient.

    Raises:
        DocumentProcessingError: ``document_not_found``, ``not_parsed``, a gate refusal or
            ``already_extracted`` (nothing recorded); ``integrity_mismatch`` or anything raised
            while extracting or storing (recorded as a failed run, then re-raised).
    """
    started = time.perf_counter()
    target = get_clinical_target(patient_id, document_id)
    if target.blocked_reason is not None:
        raise DocumentProcessingError(
            f"Document {document_id} may not be extracted ({target.blocked_reason})",
            kind=target.blocked_reason,
        )
    if target.extracted:
        raise DocumentProcessingError(
            f"Document {document_id} is already extracted", kind=ALREADY_EXTRACTED
        )
    versions = current_versions()
    output: ClinicalExtractionOutput | None = None
    try:
        if target.doc_type == LAB_REPORT:
            record = record_clinical_skipped(
                patient_id,
                document_id,
                reason=LAB_REPORT_REFUSAL,
                versions=versions,
                rule_ids=(),
                duration_ms=_elapsed_ms(started),
            )
        else:
            document = _verified_ir(settings, patient_id, document_id, target)
            classification = classify_clinical(document.pages)
            if classification.clinical_type is None:
                record = record_clinical_skipped(
                    patient_id,
                    document_id,
                    reason=str(classification.abstention),
                    versions=versions,
                    rule_ids=classification.rule_ids,
                    duration_ms=_elapsed_ms(started),
                )
            else:
                stored = {
                    page.page_number: page.text
                    for page in list_document_pages(patient_id, document_id)
                }
                output = extract_clinical_facts(
                    document,
                    stored,
                    classification.clinical_type,
                    reference_date=target.reference_date,
                )
                record = record_clinical_extraction(
                    patient_id,
                    document_id,
                    clinical_doc_type=classification.clinical_type,
                    facts=[to_record(d) for d in output.drafts],
                    versions=versions,
                    reference_date=target.reference_date,
                    rule_ids=classification.rule_ids,
                    abstentions=output.abstentions,
                    duration_ms=_elapsed_ms(started),
                )
    except (Exception, KeyboardInterrupt) as exc:
        if not (isinstance(exc, DocumentProcessingError) and exc.kind in NOT_RECORDED_KINDS):
            _record_failure(patient_id, document_id, exc, versions, started)
        raise
    result = ClinicalExtractResult(
        document_id=document_id,
        outcome=EXTRACTED if record.status == "succeeded" else SKIPPED,
        run_id=record.run_id,
        clinical_doc_type=record.clinical_doc_type,
        skip_reason=record.skip_reason,
        facts=record.facts,
        needs_review=record.needs_review,
        abstentions=dict(output.abstentions) if output is not None else {},
        duration_ms=_elapsed_ms(started),
    )
    logger.info(
        "Clinical extraction done: document_id=%s outcome=%s type=%s facts=%d needs_review=%d "
        "duration_ms=%d",
        document_id,
        result.outcome,
        result.clinical_doc_type,
        result.facts,
        result.needs_review,
        result.duration_ms,
    )
    return result


def _verified_ir(settings: Settings, patient_id, document_id, target) -> ParsedDocument:
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
    return outputs.document


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
    versions: dict[str, str],
    started: float,
) -> None:
    """Best effort. NEVER raises, so the caller always re-raises the original exception."""
    name = type(exc).__name__
    try:
        record_clinical_failure(
            patient_id,
            document_id,
            error_kind=_failure_kind(exc),
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
                "Clinical failure could not be recorded: document_id=%s (%s)", document_id, reason
            )
        except BaseException:  # deliberate: logging must not replace the original either
            pass
