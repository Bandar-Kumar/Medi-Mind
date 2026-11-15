"""Upload one PDF for a patient: validate, store the original write-once, create its record.

Task 2.6b. The steps run in this order, and each one is a separate unit; no database transaction is
ever open while the file system is touched:

    0. type checks (patient_id, data, original_filename), before any I/O
    1. validate_pdf_bytes        pure, in memory: no file and no database access
    2. patient_exists            one read-only transaction; an unknown patient stops here, before
                                 any upload directory is created
    3. store_original            hash + write-once, content-addressed publish (never overwrites)
    4. safe_display_filename     the filename becomes metadata only; it never builds a path
    5. create_document           one transaction; returns the patient's existing row for the same
                                 bytes (UNIQUE(patient_id, sha256) is the duplicate authority)

The original is stored before the row is created, so a row never points to a missing file. If step
5 fails, the stored file stays (read-only, content-addressed, harmless) and a retry finds it.
Nothing here deletes or rewrites a file, and nothing starts parsing: callers run
``process_document`` separately.

Result flags (both may be True at once):
- ``duplicate``: an existing document record was returned; no new record was created;
- ``original_stored``: this call created the original file. Together with ``duplicate``, this call
  published the identical, hash-verified bytes for a record that already existed (never an
  overwrite). That happens when a missing original is restored, and also when concurrent uploads
  of the same bytes race (one call writes the file, another creates the record), so it is logged
  as a warning that does not claim which of the two occurred.

Logs and errors carry identifiers, counts and flags only: never the filename, a path or content.
"""

import logging
import uuid
from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import PatientError
from app.ingestion.storage import safe_display_filename, store_original
from app.ingestion.validation import validate_pdf_bytes
from app.records.repositories import (
    PATIENT_NOT_FOUND,
    DocumentRef,
    create_document,
    patient_exists,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadResult:
    document: DocumentRef
    duplicate: bool  # an existing document record was returned (no new record)
    original_stored: bool  # this call created the original file


def upload_document(
    settings: Settings, patient_id: uuid.UUID, data: bytes, *, original_filename: str
) -> UploadResult:
    """Validate and store one uploaded PDF for ``patient_id``; return its document record.

    Raises:
        TypeError: an argument has the wrong type (checked before any I/O).
        DocumentValidationError: the PDF was rejected (``reason``); nothing was written.
        PatientError: ``patient_not_found``; nothing was written.
        DocumentStorageError: the original could not be stored (``kind``); no record was created.
        DocumentProcessingError: ``integrity_mismatch`` from ``create_document``.
        Database errors propagate unchanged; entry points sanitize them.
    """
    if not isinstance(patient_id, uuid.UUID):
        raise TypeError("patient_id must be a UUID")
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if not isinstance(original_filename, str):
        raise TypeError("original_filename must be a str")

    pdf = validate_pdf_bytes(
        data, max_bytes=settings.max_upload_bytes, max_pages=settings.max_pages
    )
    if not patient_exists(patient_id):
        raise PatientError(f"Patient {patient_id} does not exist", kind=PATIENT_NOT_FOUND)
    stored = store_original(settings, patient_id, pdf.data)
    created = create_document(
        patient_id,
        original_filename=safe_display_filename(original_filename),
        storage_path=stored.storage_path,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        page_count=pdf.page_count,
    )
    result = UploadResult(
        document=created.document,
        duplicate=not created.created,
        original_stored=stored.created,
    )
    if result.duplicate and result.original_stored:
        # A restored missing original, or a concurrent upload of the same bytes for the same
        # patient (this call wrote the file, another created the record): not distinguishable here.
        logger.warning(
            "Original file published for an existing document record: patient_id=%s document_id=%s",
            patient_id,
            result.document.id,
        )
    else:
        logger.info(
            "Document upload: patient_id=%s document_id=%s duplicate=%s original_stored=%s "
            "pages=%s size_bytes=%s",
            patient_id,
            result.document.id,
            result.duplicate,
            result.original_stored,
            pdf.page_count,
            stored.size_bytes,
        )
    return result
