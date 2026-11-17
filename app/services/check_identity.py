"""Verify that a parsed document belongs to the selected patient (Task 2.8, ADR-036).

    1. list_document_pages     the stored pages, exactly as parsed (scoped by patient)
    2. get_patient             the profile's display name and date of birth (scoped)
    3. check_identity          pure rules (``app.ingestion.identity``): outcome + fixed codes
    4. record_identity_check   one transaction: lock, re-check, store outcome + identity run;
                               ``mismatch`` also sets ``needs_review`` (ADR-036 R1)

Independent of classification (ADR-036 R2): only the parsed pages and the profile are read.

Outcomes:
- ``document_not_found``, ``patient_not_found``, ``not_parsed`` (the stage is blocked, not
  failed) and ``already_checked`` (never repeated automatically, A2) propagate unchanged. No run
  is written for them.
- Any other failure is recorded as a ``failed`` identity run, best effort, and the ORIGINAL
  exception is re-raised. A failed run never counts as a check, so indexing stays blocked
  (``identity_not_checked``) until a retry succeeds.

Logs carry identifiers, the outcome and kinds only: never the name, the date of birth or text.
"""

import logging
import re
import time
import uuid
from dataclasses import dataclass

from app.core.errors import DocumentProcessingError, PatientError
from app.ingestion.identity import IDENTITY_RULES_VERSION, check_identity
from app.records.db import describe_db_error, is_database_error
from app.records.repositories import (
    ALREADY_CHECKED,
    DOCUMENT_NOT_FOUND,
    NOT_PARSED,
    get_patient,
    list_document_pages,
    record_identity_check,
    record_identity_failure,
)

logger = logging.getLogger(__name__)

INVALID_INPUT = "invalid_input"
IDENTITY_FAILED = "identity_failed"
DATABASE_ERROR = "database_error"
INTERRUPTED = "interrupted"
NOT_RECORDED_KINDS = frozenset({DOCUMENT_NOT_FOUND, NOT_PARSED, ALREADY_CHECKED})
_SAFE_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")


@dataclass(frozen=True)
class IdentityCheckResult:
    """A stored identity outcome: identifiers, the outcome and fixed codes (no PII)."""

    document_id: uuid.UUID
    run_id: uuid.UUID
    outcome: str
    reason_codes: tuple[str, ...]
    rules_version: str
    processing_status: str
    duration_ms: int


def check_document_identity(patient_id: uuid.UUID, document_id: uuid.UUID) -> IdentityCheckResult:
    """Check one parsed document of this patient against the patient's profile and record it.

    Raises:
        DocumentProcessingError: ``document_not_found``, ``not_parsed``, ``already_checked``
            (nothing recorded); anything raised while checking or storing (recorded as a failed
            run, then re-raised unchanged).
        PatientError: ``patient_not_found`` (nothing recorded).
    """
    started = time.perf_counter()
    pages = list_document_pages(patient_id, document_id)  # document_not_found first
    if not pages:
        raise DocumentProcessingError(
            f"Document {document_id} has no parsed pages", kind=NOT_PARSED
        )
    profile = get_patient(patient_id)
    checked = False
    try:
        result = check_identity(pages, profile.display_name, profile.date_of_birth)
        checked = True
        duration_ms = _elapsed_ms(started)
        record = record_identity_check(
            patient_id,
            document_id,
            outcome=result.outcome,
            reason_codes=result.reason_codes,
            rules_version=result.rules_version,
            duration_ms=duration_ms,
        )
    except (Exception, KeyboardInterrupt) as exc:
        expected = isinstance(exc, PatientError) or (
            isinstance(exc, DocumentProcessingError) and exc.kind in NOT_RECORDED_KINDS
        )
        if not expected:
            kind = _failure_kind(exc, checked=checked)
            _record_failure(patient_id, document_id, exc, kind, _elapsed_ms(started))
        raise
    logger.info(
        "Identity check stored: document_id=%s outcome=%s status=%s duration_ms=%d",
        document_id,
        record.outcome,
        record.processing_status,
        duration_ms,
    )
    return IdentityCheckResult(
        document_id=document_id,
        run_id=record.run_id,
        outcome=record.outcome,
        reason_codes=record.reason_codes,
        rules_version=record.rules_version,
        processing_status=record.processing_status,
        duration_ms=duration_ms,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _failure_kind(exc: BaseException, *, checked: bool) -> str:
    """A fixed category: malformed input (check step), database, interruption, or other."""
    if isinstance(exc, KeyboardInterrupt):
        return INTERRUPTED
    if is_database_error(exc):
        return DATABASE_ERROR
    if not checked and isinstance(exc, TypeError | ValueError):
        return INVALID_INPUT
    return IDENTITY_FAILED


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
        record_identity_failure(
            patient_id,
            document_id,
            error_kind=kind,
            error_class=name if _SAFE_CLASS.fullmatch(name) else None,
            rules_version=IDENTITY_RULES_VERSION,
            duration_ms=duration_ms,
        )
    except BaseException as failure:  # deliberate: must never replace the original exception
        try:
            reason = (
                describe_db_error(failure) if is_database_error(failure) else type(failure).__name__
            )
            logger.warning(
                "Identity failure could not be recorded: document_id=%s (%s)", document_id, reason
            )
        except BaseException:  # deliberate: logging must not replace the original either
            pass
