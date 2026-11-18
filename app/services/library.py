"""Patient and document library operations for the UI (Task 2.9a, ADR-036 R3).

Thin, patient-scoped wrappers over the records layer, so that ``app/ui/`` imports services only
and never ``app.records`` (ADR-016). They add no behaviour: each call is one repository call, with
the same arguments, results and errors. Uploading is ``app.services.upload_document`` and
processing is ``app.services.document_pipeline``.

Results can hold personal data (a display name, a date of birth, a filename): callers must never
log or serialize them. Errors carry fixed kinds and identifiers only.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Final

from app.core.errors import PatientError
from app.records.chunks import get_index_target
from app.records.facts import get_extraction_target, list_document_facts
from app.records.repositories import (
    PATIENT_NOT_FOUND,
    DeletedDocument,
    DocumentSummary,
    PatientRef,
    PatientSummary,
)
from app.records.repositories import create_patient as _create_patient
from app.records.repositories import delete_document as _delete_document
from app.records.repositories import delete_patient as _delete_patient
from app.records.repositories import get_document as _get_document
from app.records.repositories import get_patient as _get_patient
from app.records.repositories import list_documents as _list_documents
from app.records.repositories import list_patients as _list_patients
from app.records.repositories import rename_document as _rename_document
from app.records.repositories import update_patient as _update_patient

__all__ = [
    "KEEP",
    "DeletedDocument",
    "DocumentState",
    "DocumentSummary",
    "PatientRef",
    "PatientSummary",
    "create_patient",
    "delete_document",
    "delete_patient",
    "get_document",
    "get_document_state",
    "get_patient",
    "list_documents",
    "list_patients",
    "rename_document",
    "update_patient",
]


class _Keep(Enum):
    KEEP = "keep"


KEEP: Final = _Keep.KEEP  # ``update_patient``: leave this field unchanged


# --- patients ------------------------------------------------------------------------------------


def create_patient(
    display_name: str, *, is_synthetic: bool, date_of_birth: dt.date | None = None
) -> PatientRef:
    """Create a patient profile. Raises ``PatientError`` (``invalid_display_name``, ...)."""
    return _create_patient(display_name, is_synthetic=is_synthetic, date_of_birth=date_of_birth)


def list_patients(*, synthetic_only: bool = False) -> tuple[PatientSummary, ...]:
    """Every patient profile (the profile selector); only synthetic ones in demo mode. Read-only."""
    patients = _list_patients()
    return tuple(p for p in patients if p.is_synthetic) if synthetic_only else patients


def get_patient(patient_id: uuid.UUID, *, synthetic_only: bool = False) -> PatientSummary:
    """One patient. Raises ``PatientError`` (``patient_not_found``).

    With ``synthetic_only`` (demo mode), a real patient is ``patient_not_found`` too: the same
    error as a missing one, so demo mode never reveals or shows real data.
    """
    patient = _get_patient(patient_id)
    if synthetic_only and not patient.is_synthetic:
        raise PatientError(f"Patient {patient_id} does not exist", kind=PATIENT_NOT_FOUND)
    return patient


def update_patient(
    patient_id: uuid.UUID,
    *,
    display_name: str | _Keep = KEEP,
    date_of_birth: dt.date | None | _Keep = KEEP,
) -> PatientRef:
    """Change the given fields only (``KEEP`` leaves one unchanged; ``None`` clears the DOB).

    Stored identity results are not re-checked after a profile edit (ADR-036 D6).
    """
    changes: dict[str, object] = {}
    if display_name is not KEEP:
        changes["display_name"] = display_name
    if date_of_birth is not KEEP:
        changes["date_of_birth"] = date_of_birth
    return _update_patient(patient_id, **changes)  # type: ignore[arg-type]


def delete_patient(patient_id: uuid.UUID) -> None:
    """Delete an empty patient. Raises ``PatientError`` (``patient_not_empty``, ...)."""
    _delete_patient(patient_id)


# --- documents -----------------------------------------------------------------------------------


def list_documents(patient_id: uuid.UUID) -> tuple[DocumentSummary, ...]:
    """The patient's documents, newest first. Raises ``PatientError`` (``patient_not_found``)."""
    return _list_documents(patient_id)


def get_document(patient_id: uuid.UUID, document_id: uuid.UUID) -> DocumentSummary:
    """One document of this patient; ``document_not_found`` also for another patient's."""
    return _get_document(patient_id, document_id)


def rename_document(
    patient_id: uuid.UUID, document_id: uuid.UUID, original_filename: str
) -> DocumentSummary:
    """Change the display filename (already sanitized; ``invalid_filename`` otherwise)."""
    return _rename_document(patient_id, document_id, original_filename)


def delete_document(patient_id: uuid.UUID, document_id: uuid.UUID) -> DeletedDocument:
    """Delete one document of this patient; its derived rows go by database cascade.

    No file is touched: the original PDF and the parse outputs stay on disk (Task 1.8e).
    Conflicts with a member in the document close as ``member_deleted`` and stay as history; the
    remaining facts are compared again (Phase 3, docs/PHASE_3_PLAN.md §6.7).
    """
    from app.services.fact_conflicts import build_detector

    return _delete_document(patient_id, document_id, conflict_detector=build_detector())


@dataclass(frozen=True)
class DocumentState:
    """What the library shows about a document's stages. Read-only; counts and fixed kinds only.

    ``index_blocked_reason`` is the indexing gate's own answer (ADR-036 A1), not recomputed here.
    """

    document_id: uuid.UUID
    processing_status: str
    page_rows: int
    chunk_count: int
    classified: bool  # a succeeded classify run exists
    identity_checked: bool  # a succeeded identity run exists
    identity_check: str
    index_blocked_reason: str | None
    extracted: bool = False  # a succeeded or skipped extract run exists (Phase 3)
    extract_blocked_reason: str | None = None
    facts_current: int = 0
    facts_needs_review: int = 0

    @property
    def parsed(self) -> bool:
        return self.page_rows > 0

    @property
    def indexed(self) -> bool:
        return self.chunk_count > 0

    @property
    def parse_unfinished(self) -> bool:
        """Claimed for parsing (``processing``) but no pages stored: a parse running elsewhere,
        or one that crashed and left a claim. Not recovered automatically (ADR-036 A2)."""
        return self.processing_status == "processing" and self.page_rows == 0


def get_document_state(patient_id: uuid.UUID, document_id: uuid.UUID) -> DocumentState:
    """The document's stage state; ``document_not_found`` also for another patient's."""
    summary = _get_document(patient_id, document_id)
    target = get_index_target(patient_id, document_id)
    extraction = get_extraction_target(patient_id, document_id)
    facts = list_document_facts(patient_id, document_id)
    return DocumentState(
        document_id=document_id,
        processing_status=summary.processing_status,
        page_rows=target.page_rows,
        chunk_count=target.chunk_count,
        classified=target.classified,
        identity_checked=target.identity_checked,
        identity_check=target.identity_check,
        index_blocked_reason=target.blocked_reason,
        extracted=extraction.extracted,
        extract_blocked_reason=extraction.blocked_reason,
        facts_current=len(facts),
        facts_needs_review=sum(f.review_status == "needs_review" for f in facts),
    )
