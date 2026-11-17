"""Classify one parsed document with the versioned rules (Task 2.7, ADR-036).

    1. list_document_pages      the stored pages, exactly as parsed (scoped by patient)
    2. classify_pages           pure, deterministic rules (``app.ingestion.classification``)
    3. record_classification    one transaction: lock, re-check, store type + rule score + run

Independent of the identity check (ADR-036 R2): only the parsed pages are read.

Outcomes:
- ``document_not_found`` and ``not_parsed`` (no pages: the stage is blocked, not failed) and
  ``already_classified`` (a succeeded run exists; never repeated automatically, A2) propagate
  unchanged. No run is written for them.
- Any other failure (malformed pages, a rule error, a database error while storing) is recorded
  as a ``failed`` classify run, best effort, and the ORIGINAL exception is re-raised. A failed run
  never counts as a classification; the stage can be retried.

``processing_status`` is never changed. Logs carry identifiers, the type and kinds only: never
page text.
"""

import logging
import re
import time
import uuid
from dataclasses import dataclass

from app.core.errors import DocumentProcessingError
from app.ingestion.classification import CLASSIFIER_RULES_VERSION, classify_pages
from app.records.db import describe_db_error, is_database_error
from app.records.repositories import (
    ALREADY_CLASSIFIED,
    DOCUMENT_NOT_FOUND,
    NOT_PARSED,
    list_document_pages,
    record_classification,
    record_classification_failure,
)

logger = logging.getLogger(__name__)

INVALID_PAGES = "invalid_pages"
CLASSIFICATION_FAILED = "classification_failed"
DATABASE_ERROR = "database_error"
INTERRUPTED = "interrupted"
NOT_RECORDED_KINDS = frozenset({DOCUMENT_NOT_FOUND, NOT_PARSED, ALREADY_CLASSIFIED})
_SAFE_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")


@dataclass(frozen=True)
class ClassifyResult:
    """A stored classification. Rule ids and numbers only: never page text."""

    document_id: uuid.UUID
    run_id: uuid.UUID
    doc_type: str
    score: float  # rule score in [0, 1], not a probability (ADR-036 D3)
    rule_ids: tuple[str, ...]
    rules_version: str
    duration_ms: int


def classify_document(patient_id: uuid.UUID, document_id: uuid.UUID) -> ClassifyResult:
    """Classify one parsed document of this patient and record the result.

    Raises:
        DocumentProcessingError: ``document_not_found``, ``not_parsed``, ``already_classified``
            (nothing recorded); anything raised while classifying or storing (recorded as a
            failed run, then re-raised unchanged).
    """
    started = time.perf_counter()
    pages = list_document_pages(patient_id, document_id)
    if not pages:
        raise DocumentProcessingError(
            f"Document {document_id} has no parsed pages", kind=NOT_PARSED
        )
    classified = False
    try:
        classification = classify_pages(pages)
        classified = True
        duration_ms = _elapsed_ms(started)
        record = record_classification(
            patient_id,
            document_id,
            doc_type=classification.doc_type,
            score=classification.score,
            rules_version=classification.rules_version,
            duration_ms=duration_ms,
        )
    except (Exception, KeyboardInterrupt) as exc:
        if not (isinstance(exc, DocumentProcessingError) and exc.kind in NOT_RECORDED_KINDS):
            kind = _failure_kind(exc, classified=classified)
            _record_failure(patient_id, document_id, exc, kind, _elapsed_ms(started))
        raise
    logger.info(
        "Classification stored: document_id=%s doc_type=%s rules=%d duration_ms=%d",
        document_id,
        record.doc_type,
        len(classification.rule_ids),
        duration_ms,
    )
    return ClassifyResult(
        document_id=document_id,
        run_id=record.run_id,
        doc_type=record.doc_type,
        score=classification.score,
        rule_ids=classification.rule_ids,
        rules_version=record.rules_version,
        duration_ms=duration_ms,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _failure_kind(exc: BaseException, *, classified: bool) -> str:
    """A fixed category: malformed pages (classify step), database, interruption, or other."""
    if isinstance(exc, KeyboardInterrupt):
        return INTERRUPTED
    if is_database_error(exc):
        return DATABASE_ERROR
    if not classified and isinstance(exc, TypeError | ValueError):
        return INVALID_PAGES
    return CLASSIFICATION_FAILED


def _record_failure(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    exc: BaseException,
    kind: str,
    duration_ms: int,
) -> None:
    """Best effort. NEVER raises, so the caller always re-raises the original exception."""
    name = type(exc).__name__
    try:
        record_classification_failure(
            patient_id,
            document_id,
            error_kind=kind,
            error_class=name if _SAFE_CLASS.fullmatch(name) else None,
            rules_version=CLASSIFIER_RULES_VERSION,
            duration_ms=duration_ms,
        )
    except BaseException as failure:  # deliberate: must never replace the original exception
        try:
            reason = (
                describe_db_error(failure) if is_database_error(failure) else type(failure).__name__
            )
            logger.warning(
                "Classification failure could not be recorded: document_id=%s (%s)",
                document_id,
                reason,
            )
        except BaseException:  # deliberate: logging must not replace the original either
            pass
