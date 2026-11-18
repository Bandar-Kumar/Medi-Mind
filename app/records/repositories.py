"""Patient-scoped repositories for document ingestion and parsing (Task 2.5a; part of Task 1.8).

The records layer is the only database access path (ADR-016). Every function here:
- takes ``patient_id`` and filters every query by it, so one patient's call can never read or
  change another patient's rows (the composite foreign keys enforce the same in the database);
- runs in exactly one transaction (``session_scope``: commit on success, roll back on any error);
- returns plain frozen dataclasses, never ORM objects or sessions.

There are two exceptions to the first rule, both about the patient scope itself (ADR-016):
``create_patient`` (Task 2.6a) creates it, so there is no ``patient_id`` to take yet, and
``list_patients`` (Task 1.8a) lists the scopes a user chooses from. Every other patient function
(``patient_exists``, ``update_patient``) is scoped by its ``patient_id`` argument.

Parse protocol (agreed in the Task 2.5 design review, decisions D1-D8; not yet in the docs):
1. ``claim_document_for_parsing`` (Tx A) locks the document row, checks it may be parsed, sets it to
   ``processing`` and inserts a ``running`` parse run. The run's id is the **claim token**.
2. The caller parses and stores derived files with no transaction open (that takes minutes).
3. ``persist_parsed_pages`` (Tx B) locks the document again, checks that the token is still the
   active claim, inserts every page row exactly as given and marks the run ``succeeded``.
4. On any failure, ``record_parse_failure`` (Tx C) marks the run ``failed`` with a sanitized error
   kind and the document ``failed``, again only while the token is the active claim.

Rules:
- A claim is *active* when its run is the latest parse run of the document and still ``running``.
  A worker whose claim was recovered (marked ``abandoned``) can therefore never write afterwards.
- A ``running`` parse run older than ``STALE_CLAIM_SECONDS`` is stale. It is recovered only when the
  caller asks for it explicitly (``recover_stale=True``), never automatically.
- Page rows are never deleted, replaced or overwritten: facts and chunks cascade from them.
- Page text is stored exactly as given. Text PostgreSQL ``text`` cannot store (a NUL character, or
  a lone surrogate that cannot be encoded as UTF-8) is rejected before any database write, never
  modified.
- After a successful parse the document stays ``processing``: ``processed`` and ``needs_review``
  describe the whole pipeline and are set by later stages (ARCHITECTURE §4.1).
- Times come from the database clock (``now()``), never from the application host.

Errors: ``DocumentProcessingError`` with a ``ProcessingErrorKind`` (or ``document_not_found``) for
expected state problems. Messages contain identifiers, page numbers and counts only. Driver errors
propagate unchanged; entry points sanitize them (``describe_db_error``), and engines are created
with ``hide_parameters=True``, so SQL parameter values never reach a message.
"""

import datetime as dt
import logging
import math
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Final

from sqlalchemy import Integer, cast, delete, exists, extract, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import DocumentProcessingError, PatientError
from app.records.db import session_scope
from app.records.models import (
    DocType,
    Document,
    IdentityCheck,
    Page,
    ParseMethod,
    Patient,
    PipelineRun,
    ProcessingStatus,
    RunStatus,
)

logger = logging.getLogger(__name__)

PARSE_STAGE = "parse"
# Agreed stale-claim timeout: the default parse timeout (600 s) plus an equal grace period, because
# Docling's timeout is cooperative and one slow page can overrun it (ADR-028).
STALE_CLAIM_SECONDS = 1200
PDF_MIME_TYPE = "application/pdf"

_NUL = chr(0)
_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"
# A delete blocked by an ON DELETE RESTRICT key reports restrict_violation, not 23503
# (verified on PostgreSQL 18, 2026-11-18; see tests/integration/test_schema_constraints.py).
_RESTRICT_VIOLATION = "23001"
_FK_SQLSTATES = frozenset({_FOREIGN_KEY_VIOLATION, _RESTRICT_VIOLATION})
# documents.patient_id -> patients.id (models.NAMING_CONVENTION; migration 0001). Only this
# constraint means "unknown patient"; any other foreign-key violation propagates unchanged.
_DOCUMENT_PATIENT_FK = "fk_documents_patient_id_patients"
# The foreign keys that RESTRICT deleting a patient who still owns rows (migrations 0001, 0002).
# A violation of exactly one of these means "the patient is not empty" (Task 1.8b).
_PATIENT_RESTRICT_FKS = frozenset(
    {
        _DOCUMENT_PATIENT_FK,
        "fk_pipeline_runs_patient_id_patients",
        "fk_conflicts_patient_id_patients",
    }
)
# documents.(supersedes_document_id, patient_id) -> documents (migration 0001, RESTRICT): a newer
# version still points at the document being deleted (Task 1.8e).
_DOCUMENT_SUPERSEDES_FK = "fk_documents_supersedes_document_id_patient_id_documents"

PATIENT_NOT_FOUND = "patient_not_found"
PATIENT_NOT_EMPTY = "patient_not_empty"  # the patient still owns documents, runs or conflicts
INVALID_DISPLAY_NAME = "invalid_display_name"
INVALID_DATE_OF_BIRTH = "invalid_date_of_birth"
MAX_DISPLAY_NAME_CHARS = 200  # patients.display_name CHECK: length(btrim(..)) BETWEEN 1 AND 200
# Explicit bidirectional embeddings, overrides and isolates (Unicode format characters that can
# make displayed text differ from the stored text: "Trojan Source", CVE-2021-42574). Zero-width
# joiner and non-joiner (U+200D, U+200C) and the simple marks LRM/RLM stay allowed: legitimate
# names in Indic and other scripts need them.
_BIDI_CONTROLS = frozenset(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))
# Invisible characters with no legitimate use in a name: ZERO WIDTH SPACE (U+200B) and ZERO WIDTH
# NO-BREAK SPACE / byte-order mark (U+FEFF). Neither is whitespace to str.strip(). Other format
# characters (category Cf) are deliberately not rejected as a class.
_INVISIBLE_CHARACTERS = frozenset((chr(0x200B), chr(0xFEFF)))
_REJECTED_NAME_CHARACTERS = _BIDI_CONTROLS | _INVISIBLE_CHARACTERS
# Short, safe identifiers only: an error kind or class name can never carry message text.
_ERROR_KIND = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ERROR_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")


class ProcessingErrorKind(StrEnum):
    """The agreed short error kinds of the parse protocol."""

    ALREADY_RUNNING = "already_running"  # another worker holds an active (or unrecovered) claim
    ALREADY_PARSED = "already_parsed"  # the document already has pages, or is past parsing
    CLAIM_LOST = "claim_lost"  # this worker's token is no longer the active claim
    ABANDONED = "abandoned"  # recorded on a stale run when its claim is recovered
    TEXT_NOT_STORABLE = "text_not_storable"  # page text contains NUL or a lone surrogate
    INTEGRITY_MISMATCH = "integrity_mismatch"  # stored values disagree with the input


DOCUMENT_NOT_FOUND = "document_not_found"  # no such document for this patient
DOCUMENT_REFERENCED = "document_referenced"  # a newer version supersedes it (cannot be deleted)
INVALID_FILENAME = "invalid_filename"  # not a canonical display filename (rename)

MAX_FILENAME_CHARS = 255  # documents.original_filename CHECK: length 1..255
# A private copy of the device names the ingestion sanitizer prefixes with "_"; the records layer
# does not import ingestion. A unit test keeps the two sets equal.
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)

_PARSED_STATUSES = frozenset({ProcessingStatus.PROCESSED, ProcessingStatus.NEEDS_REVIEW})


@dataclass(frozen=True)
class DocumentRef:
    """Identifiers and processing state of a document. No filename or content."""

    id: uuid.UUID
    patient_id: uuid.UUID
    sha256: str
    storage_path: str
    page_count: int | None
    processing_status: str


@dataclass(frozen=True)
class CreatedDocument:
    document: DocumentRef
    created: bool  # False: the patient already had a document with these bytes


@dataclass(frozen=True)
class ParseClaim:
    run_id: uuid.UUID  # the claim token
    document: DocumentRef
    recovered_run_id: uuid.UUID | None = None  # a stale run marked abandoned by this claim


@dataclass(frozen=True)
class PageRecord:
    """One parsed page to store. ``text`` is stored exactly; it never appears in ``repr()``."""

    page_number: int
    text: str = field(repr=False)
    parse_method: str  # a ParseMethod value: "native" or "ocr"
    has_tables: bool


def _error(kind: str, message: str) -> DocumentProcessingError:
    return DocumentProcessingError(message, kind=kind)


def _ref(document: Document) -> DocumentRef:
    return DocumentRef(
        id=document.id,
        patient_id=document.patient_id,
        sha256=document.sha256,
        storage_path=document.storage_path,
        page_count=document.page_count,
        processing_status=document.processing_status,
    )


# --- patients (Task 2.6a; minimal part of Task 1.8) ------------------------------------------


@dataclass(frozen=True)
class PatientRef:
    """Identifier of a created patient. The display name is not returned: it is personal data."""

    id: uuid.UUID
    is_synthetic: bool


@dataclass(frozen=True)
class PatientSummary:
    """One patient profile for a selector (Task 1.8a). Personal data: never log or serialize it.

    ``repr=False`` keeps the display name and date of birth out of ``repr()`` and ``str()`` only;
    ``dataclasses.asdict()``, ``json.dumps`` or formatting a field would still expose them.
    """

    id: uuid.UUID
    display_name: str = field(repr=False)
    date_of_birth: dt.date | None = field(repr=False)
    is_synthetic: bool
    created_at: dt.datetime


class _Unset(Enum):
    """An ``update_patient`` argument that was not given (``None`` means "clear the date")."""

    UNSET = "unset"


_UNSET: Final = _Unset.UNSET


def _patient_error(kind: str, message: str) -> PatientError:
    return PatientError(message, kind=kind)


def _valid_display_name(display_name: object) -> str:
    """The display name without surrounding whitespace, or ``invalid_display_name``.

    Rejected: not a string; empty after stripping; longer than 200 characters; any control
    character (Unicode category Cc, including NUL, tab and newline); any lone surrogate (Cs, which
    cannot be encoded as UTF-8); bidirectional embedding, override and isolate controls; zero width
    space (U+200B) and the byte-order mark (U+FEFF). ZWJ, ZWNJ, LRM and RLM are allowed. No
    Unicode normalization: the accepted name is stored exactly as given, minus surrounding
    whitespace.
    """
    if not isinstance(display_name, str):
        raise _patient_error(INVALID_DISPLAY_NAME, "The display name must be text")
    name = display_name.strip()
    if not 1 <= len(name) <= MAX_DISPLAY_NAME_CHARS:
        raise _patient_error(
            INVALID_DISPLAY_NAME,
            f"The display name must be 1 to {MAX_DISPLAY_NAME_CHARS} characters",
        )
    for char in name:
        if unicodedata.category(char) in ("Cc", "Cs") or char in _REJECTED_NAME_CHARACTERS:
            raise _patient_error(
                INVALID_DISPLAY_NAME, "The display name contains a character that is not allowed"
            )
    return name


def _valid_date_of_birth(date_of_birth: object) -> dt.date | None:
    """``None`` or a calendar date that is not in the future; else ``invalid_date_of_birth``.

    A ``datetime`` is rejected (it is a ``date`` subclass): a date of birth has no time of day.
    "Future" uses the local calendar date of this machine.
    """
    if date_of_birth is None:
        return None
    if type(date_of_birth) is not dt.date:
        raise _patient_error(INVALID_DATE_OF_BIRTH, "The date of birth must be a calendar date")
    if date_of_birth > dt.date.today():
        raise _patient_error(INVALID_DATE_OF_BIRTH, "The date of birth is in the future")
    return date_of_birth


def create_patient(
    display_name: str,
    *,
    is_synthetic: bool,
    date_of_birth: dt.date | None = None,
    factory: sessionmaker[Session] | None = None,
) -> PatientRef:
    """Create a patient profile (USER_FLOWS F1) and return its identifier.

    ``is_synthetic`` is required and has no default (ADR-005: synthetic records are marked
    explicitly). Every input is validated before the database is touched. Errors never contain
    the display name or the date of birth; the log line names the new ``patient_id`` only.

    Raises:
        PatientError: ``invalid_display_name`` or ``invalid_date_of_birth``.
        TypeError: ``is_synthetic`` is not a ``bool``.
    """
    name = _valid_display_name(display_name)
    birth_date = _valid_date_of_birth(date_of_birth)
    if not isinstance(is_synthetic, bool):
        raise TypeError("is_synthetic must be a bool")
    with session_scope(factory) as session:
        patient = Patient(display_name=name, date_of_birth=birth_date, is_synthetic=is_synthetic)
        session.add(patient)
        session.flush()  # the id is generated by the database (uuidv7())
        ref = PatientRef(id=patient.id, is_synthetic=patient.is_synthetic)
    logger.info("Patient created: patient_id=%s synthetic=%s", ref.id, ref.is_synthetic)
    return ref


def patient_exists(patient_id: uuid.UUID, *, factory: sessionmaker[Session] | None = None) -> bool:
    """True if a patient with this id exists. Read-only: one ``SELECT EXISTS``, changes nothing."""
    with session_scope(factory) as session:
        return bool(session.scalar(select(exists().where(Patient.id == patient_id))))


def list_patients(*, factory: sessionmaker[Session] | None = None) -> tuple[PatientSummary, ...]:
    """Every patient profile, oldest first (``created_at``, then ``id``). Read-only.

    The second documented exception to "every function takes ``patient_id``" (ADR-016): it lists
    the patient scopes themselves. The result holds personal data; only the count is logged.
    """
    with session_scope(factory) as session:
        rows = session.execute(
            select(
                Patient.id,
                Patient.display_name,
                Patient.date_of_birth,
                Patient.is_synthetic,
                Patient.created_at,
            ).order_by(Patient.created_at, Patient.id)
        ).all()
        patients = tuple(
            PatientSummary(
                id=row.id,
                display_name=row.display_name,
                date_of_birth=row.date_of_birth,
                is_synthetic=row.is_synthetic,
                created_at=row.created_at,
            )
            for row in rows
        )
    logger.info("Patients listed: count=%d", len(patients))
    return patients


def get_patient(
    patient_id: uuid.UUID, *, factory: sessionmaker[Session] | None = None
) -> PatientSummary:
    """One patient profile (Task 2.8: the identity check compares against it). Read-only.

    The result holds personal data: never log it. Only the id is logged, on failure.

    Raises:
        PatientError: ``patient_not_found``.
    """
    with session_scope(factory) as session:
        row = session.execute(
            select(
                Patient.id,
                Patient.display_name,
                Patient.date_of_birth,
                Patient.is_synthetic,
                Patient.created_at,
            ).where(Patient.id == patient_id)
        ).one_or_none()
        if row is None:
            raise _patient_error(PATIENT_NOT_FOUND, f"Patient {patient_id} does not exist")
        return PatientSummary(
            id=row.id,
            display_name=row.display_name,
            date_of_birth=row.date_of_birth,
            is_synthetic=row.is_synthetic,
            created_at=row.created_at,
        )


def update_patient(
    patient_id: uuid.UUID,
    *,
    display_name: str | _Unset = _UNSET,
    date_of_birth: dt.date | None | _Unset = _UNSET,
    factory: sessionmaker[Session] | None = None,
) -> PatientRef:
    """Change a patient's display name and/or date of birth (Task 1.8a).

    Only the given arguments change; ``date_of_birth=None`` clears the date. Values are validated
    exactly as in ``create_patient``, before any database access. ``id``, ``is_synthetic``
    (ADR-005) and ``created_at`` cannot change: they are not parameters. Returns ``PatientRef``
    (no personal data). The log names the patient and the changed field names, never a value.

    Raises:
        ValueError: neither ``display_name`` nor ``date_of_birth`` was given.
        PatientError: ``invalid_display_name``, ``invalid_date_of_birth``, ``patient_not_found``.
    """
    if display_name is _UNSET and date_of_birth is _UNSET:
        raise ValueError("update_patient needs display_name or date_of_birth")
    values: dict[str, object] = {}
    if display_name is not _UNSET:
        values["display_name"] = _valid_display_name(display_name)
    if date_of_birth is not _UNSET:
        values["date_of_birth"] = _valid_date_of_birth(date_of_birth)
    with session_scope(factory) as session:
        row = session.execute(
            update(Patient)
            .where(Patient.id == patient_id)
            .values(**values)
            .returning(Patient.id, Patient.is_synthetic)
        ).one_or_none()
        if row is None:  # raised inside the scope: the (empty) transaction is rolled back
            raise _patient_error(PATIENT_NOT_FOUND, f"Patient {patient_id} does not exist")
        ref = PatientRef(id=row.id, is_synthetic=row.is_synthetic)
    logger.info("Patient updated: patient_id=%s fields=%s", ref.id, ",".join(sorted(values)))
    return ref


def delete_patient(patient_id: uuid.UUID, *, factory: sessionmaker[Session] | None = None) -> None:
    """Delete an empty patient profile (Task 1.8b). Only an empty patient can be deleted.

    The existing ``ON DELETE RESTRICT`` foreign keys are the authority, so the check is race-safe:
    a patient who still owns documents, pipeline runs or conflicts is refused with
    ``patient_not_empty``, mapped only from a violation of exactly one of those three constraints.
    Every other integrity error propagates unchanged. No file is touched (an orphan original from
    a failed upload stays under ``uploads/<patient_id>/``).

    Raises:
        PatientError: ``patient_not_found`` or ``patient_not_empty``.
    """
    with session_scope(factory) as session:
        not_empty = False
        deleted: uuid.UUID | None = None
        try:
            deleted = session.execute(
                delete(Patient).where(Patient.id == patient_id).returning(Patient.id)
            ).scalar_one_or_none()
        except IntegrityError as exc:
            if not _fk_violation(exc, _PATIENT_RESTRICT_FKS):
                raise
            not_empty = True
        # Raised outside the except block: no chained driver error (with SQL parameters) is kept.
        if not_empty:
            raise _patient_error(
                PATIENT_NOT_EMPTY,
                f"Patient {patient_id} still has documents, pipeline runs or conflicts",
            )
        if deleted is None:
            raise _patient_error(PATIENT_NOT_FOUND, f"Patient {patient_id} does not exist")
    logger.info("Patient deleted: patient_id=%s", patient_id)


def _fk_violation(exc: IntegrityError, names: frozenset[str]) -> bool:
    """True only for a foreign-key violation of one of the named constraints.

    SQLSTATE 23503 (insert or update with a missing parent) or 23001 (delete blocked by an
    ``ON DELETE RESTRICT`` key); the constraint name decides which key it was.
    """
    original = exc.orig
    if getattr(original, "sqlstate", None) not in _FK_SQLSTATES:
        return False
    diag = getattr(original, "diag", None)
    return getattr(diag, "constraint_name", None) in names


def _is_patient_fk_violation(exc: IntegrityError) -> bool:
    """True only for a violation of ``documents.patient_id -> patients.id`` (an unknown patient)."""
    return _fk_violation(exc, frozenset({_DOCUMENT_PATIENT_FK}))


# --- create_document -------------------------------------------------------------------------


def create_document(
    patient_id: uuid.UUID,
    *,
    original_filename: str,
    storage_path: str,
    sha256: str,
    size_bytes: int,
    page_count: int,
    mime_type: str = PDF_MIME_TYPE,
    factory: sessionmaker[Session] | None = None,
) -> CreatedDocument:
    """Create the document row for a stored original, or return the patient's existing one.

    The caller stores the original first (write-once, content-addressed), so a row never points at
    a missing file. ``original_filename`` must already be sanitized (``safe_display_filename``).
    The new row starts as ``uploaded``; ``page_count`` comes from validation and is checked against
    the parse later.

    Duplicates (same patient, same bytes) return the existing row with ``created=False``, also when
    a concurrent upload of the same bytes wins the race (unique violation on insert).

    Raises:
        DocumentProcessingError: ``integrity_mismatch`` if the existing row's storage path or size
            differs from this upload (the content address should make them identical).
        PatientError: ``patient_not_found`` if no patient has this id (only a violation of the
            ``documents.patient_id`` foreign key; any other integrity error propagates).
    """
    with session_scope(factory) as session:
        existing = _find_by_sha256(session, patient_id, sha256)
        if existing is not None:
            return _existing_document(existing, storage_path, size_bytes)
        document = Document(
            patient_id=patient_id,
            original_filename=original_filename,
            storage_path=storage_path,
            sha256=sha256,
            mime_type=mime_type,
            size_bytes=size_bytes,
            page_count=page_count,
            processing_status=ProcessingStatus.UPLOADED.value,
        )
        lost_race = False
        unknown_patient = False
        try:
            with session.begin_nested():  # a savepoint: a unique violation keeps the transaction
                session.add(document)
        except IntegrityError as exc:
            if _is_patient_fk_violation(exc):
                unknown_patient = True
            elif getattr(exc.orig, "sqlstate", None) != _UNIQUE_VIOLATION:
                raise
            else:
                lost_race = True
        # Raised outside the except block: no chained driver error (with SQL parameters) is kept.
        if unknown_patient:
            raise _patient_error(PATIENT_NOT_FOUND, f"Patient {patient_id} does not exist")
        if lost_race:
            existing = _find_by_sha256(session, patient_id, sha256)
            if existing is None:  # a different unique constraint (e.g. storage_path) was violated
                raise _error(
                    ProcessingErrorKind.INTEGRITY_MISMATCH,
                    "The storage path is already used by another document",
                )
            return _existing_document(existing, storage_path, size_bytes)
        ref = _ref(document)
    logger.info("Document created: document_id=%s pages=%s", ref.id, ref.page_count)
    return CreatedDocument(document=ref, created=True)


def _find_by_sha256(session: Session, patient_id: uuid.UUID, sha256: str) -> Document | None:
    return session.scalars(
        select(Document).where(Document.patient_id == patient_id, Document.sha256 == sha256)
    ).one_or_none()


def _existing_document(document: Document, storage_path: str, size_bytes: int) -> CreatedDocument:
    if document.storage_path != storage_path or document.size_bytes != size_bytes:
        raise _error(
            ProcessingErrorKind.INTEGRITY_MISMATCH,
            f"Document {document.id} exists with a different storage path or size",
        )
    return CreatedDocument(document=_ref(document), created=False)


# --- document reads, rename and delete (Tasks 1.8c-1.8e) ------------------------------------


@dataclass(frozen=True)
class DocumentSummary:
    """A document for the library and detail pages (Task 1.8c). Never log or serialize it.

    No storage path or hash: those stay internal (``DocumentRef``). ``original_filename`` can hold
    personal data; ``repr=False`` hides it from ``repr()`` and ``str()`` only, not from
    ``dataclasses.asdict()`` or ``json.dumps``.

    ``doc_type_confidence`` and ``identity_check`` (Task 2.9a) are read-only, for display. Their
    column defaults (``NULL``, ``unknown``) do not show whether a stage ran; the indexing gate
    reads the stage runs instead (ADR-036 A1).
    """

    id: uuid.UUID
    patient_id: uuid.UUID
    original_filename: str = field(repr=False)
    page_count: int | None
    processing_status: str
    doc_type: str
    document_date: dt.date | None
    document_date_precision: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    doc_type_confidence: Decimal | None  # a rule score in [0, 1], not a probability (D3)
    identity_check: str  # match / mismatch / unknown


@dataclass(frozen=True)
class DeletedDocument:
    """What a document deletion removed through the database cascade. Counts only."""

    document_id: uuid.UUID
    pages_deleted: int
    runs_deleted: int


def _summary(document: Document) -> DocumentSummary:
    return DocumentSummary(
        id=document.id,
        patient_id=document.patient_id,
        original_filename=document.original_filename,
        page_count=document.page_count,
        processing_status=document.processing_status,
        doc_type=document.doc_type,
        document_date=document.document_date,
        document_date_precision=document.document_date_precision,
        created_at=document.created_at,
        updated_at=document.updated_at,
        doc_type_confidence=document.doc_type_confidence,
        identity_check=document.identity_check,
    )


def _document_not_found(document_id: uuid.UUID) -> DocumentProcessingError:
    """The same error for a missing document and another patient's: no existence oracle."""
    return _error(DOCUMENT_NOT_FOUND, f"Document {document_id} not found for this patient")


def list_documents(
    patient_id: uuid.UUID, *, factory: sessionmaker[Session] | None = None
) -> tuple[DocumentSummary, ...]:
    """The patient's documents, newest first (``created_at``, then ``id``). Read-only.

    An existing patient with no documents gets ``()``; an unknown patient is ``patient_not_found``
    (checked only when the list is empty, in the same transaction). Logs the count only.

    Raises:
        PatientError: ``patient_not_found``.
    """
    with session_scope(factory) as session:
        documents = session.scalars(
            select(Document)
            .where(Document.patient_id == patient_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
        ).all()
        summaries = tuple(_summary(document) for document in documents)
        if not summaries and not session.scalar(select(exists().where(Patient.id == patient_id))):
            raise _patient_error(PATIENT_NOT_FOUND, f"Patient {patient_id} does not exist")
    logger.info("Documents listed: patient_id=%s count=%d", patient_id, len(summaries))
    return summaries


def get_document(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> DocumentSummary:
    """One document of this patient. Read-only; filtered by both ids.

    Raises:
        DocumentProcessingError: ``document_not_found``, also for another patient's document.
    """
    with session_scope(factory) as session:
        document = session.scalars(
            select(Document).where(Document.id == document_id, Document.patient_id == patient_id)
        ).one_or_none()
        if document is None:
            raise _document_not_found(document_id)
        return _summary(document)


def _valid_display_filename(name: object) -> str:
    """``name`` unchanged if it is a canonical display filename; else ``invalid_filename``.

    Canonical means the ingestion sanitizer (``safe_display_filename``) would return it unchanged.
    This layer only checks and never rewrites: callers sanitize first. Rules: 1-255 characters; no
    slash or backslash; NFC; no control, format or surrogate character (a surrogate cannot even be
    stored); whitespace only as single spaces; no leading or trailing space or dot; not a Windows
    device name. A unit test checks these rules against the sanitizer.
    """
    if not isinstance(name, str):
        raise TypeError("original_filename must be a str")
    canonical = (
        1 <= len(name) <= MAX_FILENAME_CHARS
        and "/" not in name
        and "\\" not in name
        and all(unicodedata.category(char) not in ("Cc", "Cf", "Cs") for char in name)
        and unicodedata.is_normalized("NFC", name)
        and " ".join(name.split()) == name
        and name.strip(" .") == name
        and name.split(".", 1)[0].rstrip(" ").upper() not in _WINDOWS_DEVICE_NAMES
    )
    if not canonical:
        raise _error(INVALID_FILENAME, "The filename is not a valid display filename")
    return name


def rename_document(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    original_filename: str,
    *,
    factory: sessionmaker[Session] | None = None,
) -> DocumentSummary:
    """Change a document's display filename, the only editable document field (Task 1.8d).

    The name must already be sanitized (``safe_display_filename``); anything else is rejected
    with ``invalid_filename`` before any database work, never rewritten. The document row is
    locked like the parse protocol's, so a rename serializes with claims; it changes no field the
    protocol reads. Allowed in every processing status. Logs the document id only.

    Raises:
        TypeError: ``original_filename`` is not a ``str``.
        DocumentProcessingError: ``invalid_filename`` or ``document_not_found``.
    """
    name = _valid_display_filename(original_filename)
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)
        document.original_filename = name
        session.flush()  # the ORM's onupdate sets updated_at when the name changed
        summary = _summary(document)
    logger.info("Document renamed: document_id=%s", document_id)
    return summary


def delete_document(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    stale_after_seconds: int = STALE_CLAIM_SECONDS,
    conflict_detector: Any = None,
    factory: sessionmaker[Session] | None = None,
) -> DeletedDocument:
    """Hard-delete a document row; the database cascades its derived rows (Task 1.8e).

    The cascade removes the document's pages, pipeline runs (its processing history), chunks,
    facts, review decisions and conflict memberships. Parent ``conflicts`` rows have no foreign
    key to documents and stay, possibly with no members. **No file is touched**: the original PDF
    and the parse outputs stay exactly as they are.

    With the row locked (like the parse protocol): a non-stale running parse run means a live
    worker, so the deletion is refused with ``already_running``. Stale runs do not block; such a
    worker can never write afterwards (its Tx B finds no document). A newer document version that
    supersedes this one is ``document_referenced`` (exactly that foreign key; any other integrity
    error propagates). Logs ids and counts only.

    Phase 3 (docs/PHASE_3_PLAN.md §5.6, §6.7): before the cascade, conflicts with a member in this
    document are closed as ``member_deleted`` and kept as history. With ``conflict_detector``,
    the patient's conflicts are recomputed without this document's facts, so remaining facts can
    open a new conflict; without it, the affected conflicts are only closed.

    Raises:
        ValueError: ``stale_after_seconds`` is not positive.
        DocumentProcessingError: ``document_not_found``, ``already_running``,
            ``document_referenced``.
    """
    if stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be positive")
    with session_scope(factory) as session:
        _lock_document(session, patient_id, document_id)
        running = _running_parse_runs(session, patient_id, document_id, stale_after_seconds)
        if any(not stale for _, stale in running):
            raise _error(
                ProcessingErrorKind.ALREADY_RUNNING,
                f"Document {document_id} is being parsed by another worker",
            )
        pages = session.scalar(
            select(func.count())
            .select_from(Page)
            .where(Page.patient_id == patient_id, Page.document_id == document_id)
        )
        runs = session.scalar(
            select(func.count())
            .select_from(PipelineRun)
            .where(PipelineRun.patient_id == patient_id, PipelineRun.document_id == document_id)
        )
        from app.records.facts import close_conflicts_for_deleted_document  # no import cycle

        close_conflicts_for_deleted_document(session, patient_id, document_id, conflict_detector)
        referenced = False
        try:
            session.execute(
                delete(Document).where(
                    Document.id == document_id, Document.patient_id == patient_id
                )
            )
        except IntegrityError as exc:
            if not _fk_violation(exc, frozenset({_DOCUMENT_SUPERSEDES_FK})):
                raise
            referenced = True
        if referenced:  # raised outside the except block: no chained driver error
            raise _error(
                DOCUMENT_REFERENCED,
                f"Document {document_id} is superseded by a newer version and cannot be deleted",
            )
        result = DeletedDocument(
            document_id=document_id, pages_deleted=int(pages or 0), runs_deleted=int(runs or 0)
        )
    logger.info(
        "Document deleted: document_id=%s pages_deleted=%d runs_deleted=%d",
        document_id,
        result.pages_deleted,
        result.runs_deleted,
    )
    return result


# --- claim_document_for_parsing (Tx A) -------------------------------------------------------


def claim_document_for_parsing(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    component_versions: Mapping[str, str] | None = None,
    recover_stale: bool = False,
    stale_after_seconds: int = STALE_CLAIM_SECONDS,
    factory: sessionmaker[Session] | None = None,
) -> ParseClaim:
    """Claim a document for parsing; returns the claim token (the new parse run's id).

    Eligible: ``uploaded`` or ``failed`` (a retry), with no page rows. A document in
    ``processing`` is claimable only when its active run is stale and ``recover_stale`` is True;
    that run is then marked ``failed`` with error kind ``abandoned``. The document row is locked
    (``SELECT ... FOR UPDATE``), so concurrent claims are serialized and exactly one wins.

    Raises:
        DocumentProcessingError: ``document_not_found``; ``already_parsed`` (pages exist or the
            document is past parsing); ``already_running`` (an active claim exists, or a stale one
            exists and ``recover_stale`` is False).
    """
    if stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be positive")
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)
        status = ProcessingStatus(document.processing_status)
        if status in _PARSED_STATUSES or _has_pages(session, document_id):
            raise _error(
                ProcessingErrorKind.ALREADY_PARSED, f"Document {document_id} is already parsed"
            )
        running = _running_parse_runs(session, patient_id, document_id, stale_after_seconds)
        recovered: uuid.UUID | None = None
        if status is ProcessingStatus.PROCESSING or running:
            if any(not stale for _, stale in running):
                raise _error(
                    ProcessingErrorKind.ALREADY_RUNNING,
                    f"Document {document_id} is being parsed by another worker",
                )
            # Only stale runs remain (or none: a crash left `processing` without a run).
            if not recover_stale:
                raise _error(
                    ProcessingErrorKind.ALREADY_RUNNING,
                    f"Document {document_id} has a stale claim; retry with recovery to reclaim it",
                )
            for run_id, _ in running:
                _finish_run(session, run_id, RunStatus.FAILED, ProcessingErrorKind.ABANDONED.value)
                recovered = run_id
        document.processing_status = ProcessingStatus.PROCESSING.value
        run = PipelineRun(
            patient_id=patient_id,
            document_id=document_id,
            stage=PARSE_STAGE,
            status=RunStatus.RUNNING.value,
            component_versions=dict(component_versions or {}),
        )
        session.add(run)
        session.flush()  # assigns the run id (uuidv7, RETURNING) and the document's updated_at
        claim = ParseClaim(run_id=run.id, document=_ref(document), recovered_run_id=recovered)
    logger.info(
        "Parse claimed: document_id=%s run_id=%s recovered_run_id=%s",
        document_id,
        claim.run_id,
        claim.recovered_run_id,
    )
    return claim


def _lock_document(session: Session, patient_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    document = session.scalars(
        select(Document)
        .where(Document.id == document_id, Document.patient_id == patient_id)
        .with_for_update()
    ).one_or_none()
    if document is None:
        raise _error(DOCUMENT_NOT_FOUND, f"Document {document_id} not found for this patient")
    return document


def _has_pages(session: Session, document_id: uuid.UUID) -> bool:
    return bool(session.scalar(select(exists().where(Page.document_id == document_id))))


def _running_parse_runs(
    session: Session, patient_id: uuid.UUID, document_id: uuid.UUID, stale_after_seconds: int
) -> list[tuple[uuid.UUID, bool]]:
    """(run id, is stale) for every running parse run of the document, by the database clock."""
    stale = PipelineRun.started_at < func.now() - dt.timedelta(seconds=stale_after_seconds)
    rows = session.execute(
        select(PipelineRun.id, stale).where(
            PipelineRun.patient_id == patient_id,
            PipelineRun.document_id == document_id,
            PipelineRun.stage == PARSE_STAGE,
            PipelineRun.status == RunStatus.RUNNING.value,
        )
    ).all()
    return [(row[0], bool(row[1])) for row in rows]


def _finish_run(
    session: Session,
    run_id: uuid.UUID,
    status: RunStatus,
    error_kind: str | None = None,
    error_class: str | None = None,
) -> None:
    """End a run: status, database-clock finish time and duration, sanitized error fields."""
    session.execute(
        update(PipelineRun)
        .where(PipelineRun.id == run_id, PipelineRun.status == RunStatus.RUNNING.value)
        .values(
            status=status.value,
            finished_at=func.now(),
            duration_ms=cast(
                func.greatest(extract("epoch", func.now() - PipelineRun.started_at) * 1000, 0),
                Integer,
            ),
            error_kind=error_kind,
            error_class=error_class,
        )
    )


def _active_claim(
    session: Session, patient_id: uuid.UUID, document_id: uuid.UUID, run_id: uuid.UUID
) -> bool:
    """True if ``run_id`` is the document's latest parse run and still running."""
    latest = session.execute(
        select(PipelineRun.id, PipelineRun.status)
        .where(
            PipelineRun.patient_id == patient_id,
            PipelineRun.document_id == document_id,
            PipelineRun.stage == PARSE_STAGE,
        )
        .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
        .limit(1)
    ).one_or_none()
    return latest is not None and latest[0] == run_id and latest[1] == RunStatus.RUNNING.value


# --- persist_parsed_pages (Tx B) -------------------------------------------------------------


def persist_parsed_pages(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    run_id: uuid.UUID,
    pages: Sequence[PageRecord],
    *,
    factory: sessionmaker[Session] | None = None,
) -> int:
    """Insert every page row of a parsed document and complete the claim; returns the page count.

    The input is checked before any database work: pages numbered 1..n in order, a known parse
    method, and no NUL in any text. Then, with the document row locked: the claim must still be
    active, the document must have no pages yet, and n must equal the document's page count. All
    pages are inserted in one transaction, or none are. The document stays ``processing``.

    Raises:
        DocumentProcessingError: ``text_not_storable`` (NUL in a page's text; the page number is
            named, the text never), ``integrity_mismatch`` (bad numbering, unknown parse method,
            page count differs), ``claim_lost``, ``already_parsed``, ``document_not_found``.
    """
    _check_pages(pages)
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)
        if document.processing_status != ProcessingStatus.PROCESSING.value or not _active_claim(
            session, patient_id, document_id, run_id
        ):
            raise _error(
                ProcessingErrorKind.CLAIM_LOST,
                f"Run {run_id} no longer holds the parse claim on document {document_id}",
            )
        if _has_pages(session, document_id):
            raise _error(
                ProcessingErrorKind.ALREADY_PARSED, f"Document {document_id} already has pages"
            )
        if document.page_count is None:
            document.page_count = len(pages)
        elif document.page_count != len(pages):
            raise _error(
                ProcessingErrorKind.INTEGRITY_MISMATCH,
                f"Document {document_id} has {document.page_count} pages; {len(pages)} were given",
            )
        session.add_all(
            Page(
                patient_id=patient_id,
                document_id=document_id,
                page_number=page.page_number,
                text=page.text,
                parse_method=page.parse_method,
                has_tables=page.has_tables,
            )
            for page in pages
        )
        session.flush()
        _finish_run(session, run_id, RunStatus.SUCCEEDED)
    logger.info(
        "Pages persisted: document_id=%s run_id=%s pages=%d", document_id, run_id, len(pages)
    )
    return len(pages)


def _check_pages(pages: Sequence[PageRecord]) -> None:
    if not pages:
        raise _error(ProcessingErrorKind.INTEGRITY_MISMATCH, "No pages were given")
    numbers = [page.page_number for page in pages]
    if numbers != list(range(1, len(pages) + 1)):
        raise _error(ProcessingErrorKind.INTEGRITY_MISMATCH, "Pages must be numbered 1..n in order")
    methods = {method.value for method in ParseMethod}
    for page in pages:
        if page.parse_method not in methods:
            raise _error(
                ProcessingErrorKind.INTEGRITY_MISMATCH,
                f"Page {page.page_number} has an unknown parse method",
            )
        if _NUL in page.text:
            raise _error(
                ProcessingErrorKind.TEXT_NOT_STORABLE,
                f"Page {page.page_number} contains a NUL character, which cannot be stored",
            )
        if not _encodes_as_utf8(page.text):
            raise _error(
                ProcessingErrorKind.TEXT_NOT_STORABLE,
                f"Page {page.page_number} contains invalid Unicode (a lone surrogate), "
                "which cannot be stored",
            )


def _encodes_as_utf8(text: str) -> bool:
    """False for text PostgreSQL cannot receive as UTF-8 (a lone surrogate). Never repairs it."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


# --- record_parse_failure (Tx C) -------------------------------------------------------------


def record_parse_failure(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    error_kind: str,
    error_class: str | None = None,
    factory: sessionmaker[Session] | None = None,
) -> bool:
    """Mark the claim's run ``failed`` and the document ``failed``; returns True if recorded.

    ``error_kind`` is a short code: a ``ProcessingErrorKind`` value or another error's stable kind
    (e.g. a parse error's ``timeout``). ``error_class`` is an exception class name only. Both are
    checked to be plain identifiers, so no message text can be stored.

    Only the active claim is changed. If the claim was lost (recovered by another worker, or the
    run already finished), nothing is written and False is returned: another worker's state is
    never overwritten.

    Raises:
        ValueError: if ``error_kind`` or ``error_class`` is not a plain identifier.
        DocumentProcessingError: ``document_not_found``.
    """
    if not _ERROR_KIND.fullmatch(error_kind):
        raise ValueError("error_kind must be a short lowercase identifier")
    if error_class is not None and not _ERROR_CLASS.fullmatch(error_class):
        raise ValueError("error_class must be a class name")
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)
        if not _active_claim(session, patient_id, document_id, run_id):
            recorded = False
        else:
            _finish_run(session, run_id, RunStatus.FAILED, error_kind, error_class)
            document.processing_status = ProcessingStatus.FAILED.value
            recorded = True
    logger.info(
        "Parse failure %s: document_id=%s run_id=%s error_kind=%s",
        "recorded" if recorded else "not recorded (claim lost)",
        document_id,
        run_id,
        error_kind,
    )
    return recorded


# --- parse run provenance (read-only) --------------------------------------------------------


def parse_run_belongs_to_document(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    parse_run_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> bool:
    """True if ``parse_run_id`` is a parse run of this patient's document, in any status.

    Used to accept parse outputs published by an earlier run (named in their manifest) before they
    are reused. Any status counts: the producing run may since have been marked ``failed`` or
    ``abandoned`` (it stopped after publishing). Read-only; changes nothing.
    """
    with session_scope(factory) as session:
        return bool(
            session.scalar(
                select(
                    exists().where(
                        PipelineRun.id == parse_run_id,
                        PipelineRun.patient_id == patient_id,
                        PipelineRun.document_id == document_id,
                        PipelineRun.stage == PARSE_STAGE,
                    )
                )
            )
        )


# --- classification (Task 2.7, ADR-036) ----------------------------------------------------------

CLASSIFY_STAGE = "classify"
ALREADY_CLASSIFIED = "already_classified"  # a succeeded classify run exists (ADR-036 A2)
NOT_PARSED = "not_parsed"  # the document has no page rows yet (same value as app.records.chunks)
CLASSIFIER_VERSION_KEY = "classifier_rules_version"  # key in the run's component_versions
_RULES_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
_DOC_TYPES = frozenset(member.value for member in DocType)
_SCORE_PLACES = Decimal("0.001")  # documents.doc_type_confidence is NUMERIC(4, 3)


@dataclass(frozen=True)
class ClassificationRecord:
    """A stored classification: identifiers and values only, no page text."""

    document_id: uuid.UUID
    run_id: uuid.UUID
    doc_type: str
    doc_type_confidence: Decimal  # rule score, not a probability (ADR-036 D3)
    rules_version: str


def _has_succeeded_run(
    session: Session, patient_id: uuid.UUID, document_id: uuid.UUID, stage: str
) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    PipelineRun.patient_id == patient_id,
                    PipelineRun.document_id == document_id,
                    PipelineRun.stage == stage,
                    PipelineRun.status == RunStatus.SUCCEEDED.value,
                )
            )
        )
    )


def _check_duration(duration_ms: int) -> None:
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
        raise ValueError("duration_ms must be a non-negative integer")


def _check_rules_version(rules_version: object) -> str:
    if not isinstance(rules_version, str) or not _RULES_VERSION.fullmatch(rules_version):
        raise ValueError("rules_version must be a short version identifier")
    return rules_version


def _valid_score(score: object) -> Decimal:
    """The rule score as stored: finite, in [0, 1], at most 3 decimals (never rounded here)."""
    if isinstance(score, bool) or not isinstance(score, int | float | Decimal):
        raise ValueError("score must be a number")
    if isinstance(score, float) and not math.isfinite(score):
        raise ValueError("score must be finite")
    value = Decimal(str(score))
    if not value.is_finite() or not Decimal(0) <= value <= Decimal(1):
        raise ValueError("score must be between 0 and 1")
    if value != value.quantize(_SCORE_PLACES):
        raise ValueError("score must have at most 3 decimal places (the stored precision)")
    return value.quantize(_SCORE_PLACES)


def list_document_pages(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    factory: sessionmaker[Session] | None = None,
) -> tuple[PageRecord, ...]:
    """The document's page rows in page order, exactly as stored. Read-only.

    Returns ``()`` for a document with no pages yet. ``text`` is evidence: it is hidden from
    ``repr()`` and never logged.

    Raises:
        DocumentProcessingError: ``document_not_found`` (also for another patient's document).
    """
    with session_scope(factory) as session:
        found = session.scalar(
            select(exists().where(Document.id == document_id, Document.patient_id == patient_id))
        )
        if not found:
            raise _document_not_found(document_id)
        rows = session.execute(
            select(Page.page_number, Page.text, Page.parse_method, Page.has_tables)
            .where(Page.patient_id == patient_id, Page.document_id == document_id)
            .order_by(Page.page_number)
        ).all()
    return tuple(
        PageRecord(page_number=row[0], text=row[1], parse_method=row[2], has_tables=row[3])
        for row in rows
    )


def record_classification(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    doc_type: str,
    score: float | Decimal,
    rules_version: str,
    duration_ms: int,
    factory: sessionmaker[Session] | None = None,
) -> ClassificationRecord:
    """Store a document's classification and its succeeded ``classify`` run, all or nothing.

    Input is checked before any database work: ``doc_type`` a ``DocType`` value; ``score`` a rule
    score in [0, 1] with at most 3 decimals (stored exactly, never rounded); ``rules_version`` a
    short identifier, recorded in the run's ``component_versions``. Then, with the document row
    locked (serializing concurrent classifiers): the document must have pages, and no succeeded
    ``classify`` run may exist (ADR-036 A2: a successful stage is never repeated automatically).
    ``processing_status`` is never changed (ADR-036). Logs identifiers and the type only.

    Raises:
        ValueError: invalid ``doc_type``, ``score``, ``rules_version`` or ``duration_ms``.
        DocumentProcessingError: ``document_not_found``, ``not_parsed``, ``already_classified``.
    """
    if doc_type not in _DOC_TYPES:
        raise ValueError("doc_type must be a DocType value")
    confidence = _valid_score(score)
    version = _check_rules_version(rules_version)
    _check_duration(duration_ms)
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)
        has_pages = session.scalar(
            select(exists().where(Page.patient_id == patient_id, Page.document_id == document_id))
        )
        if not has_pages:
            raise _error(NOT_PARSED, f"Document {document_id} has no parsed pages")
        if _has_succeeded_run(session, patient_id, document_id, CLASSIFY_STAGE):
            raise _error(ALREADY_CLASSIFIED, f"Document {document_id} is already classified")
        document.doc_type = doc_type
        document.doc_type_confidence = confidence
        run = PipelineRun(
            patient_id=patient_id,
            document_id=document_id,
            stage=CLASSIFY_STAGE,
            status=RunStatus.SUCCEEDED.value,
            started_at=func.now() - dt.timedelta(milliseconds=duration_ms),
            finished_at=func.now(),
            duration_ms=duration_ms,
            component_versions={CLASSIFIER_VERSION_KEY: version},
        )
        session.add(run)
        session.flush()
        record = ClassificationRecord(document_id, run.id, doc_type, confidence, version)
    logger.info(
        "Document classified: document_id=%s run_id=%s doc_type=%s rules_version=%s",
        document_id,
        record.run_id,
        doc_type,
        version,
    )
    return record


def record_classification_failure(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    error_kind: str,
    error_class: str | None = None,
    rules_version: str,
    duration_ms: int,
    factory: sessionmaker[Session] | None = None,
) -> uuid.UUID:
    """Record a failed ``classify`` run; returns its id. The document itself is not changed.

    A failed run is history only: it never counts as a classification, and the stage can be
    retried (ADR-036 A2). ``error_kind`` and ``error_class`` are checked to be plain identifiers,
    so no message text can be stored. The document row is locked like every stage write.

    Raises:
        ValueError: ``error_kind``, ``error_class``, ``rules_version`` or ``duration_ms`` invalid.
        DocumentProcessingError: ``document_not_found``.
    """
    if not _ERROR_KIND.fullmatch(error_kind):
        raise ValueError("error_kind must be a short lowercase identifier")
    if error_class is not None and not _ERROR_CLASS.fullmatch(error_class):
        raise ValueError("error_class must be a class name")
    version = _check_rules_version(rules_version)
    _check_duration(duration_ms)
    with session_scope(factory) as session:
        _lock_document(session, patient_id, document_id)
        run = PipelineRun(
            patient_id=patient_id,
            document_id=document_id,
            stage=CLASSIFY_STAGE,
            status=RunStatus.FAILED.value,
            started_at=func.now() - dt.timedelta(milliseconds=duration_ms),
            finished_at=func.now(),
            duration_ms=duration_ms,
            component_versions={CLASSIFIER_VERSION_KEY: version},
            error_kind=error_kind,
            error_class=error_class,
        )
        session.add(run)
        session.flush()
        run_id = run.id
    logger.info(
        "Classification failure recorded: document_id=%s run_id=%s error_kind=%s",
        document_id,
        run_id,
        error_kind,
    )
    return run_id


# --- identity verification (Task 2.8, ADR-036) ---------------------------------------------------

IDENTITY_STAGE = "identity"
ALREADY_CHECKED = "already_checked"  # a succeeded identity run exists (ADR-036 A2)
IDENTITY_VERSION_KEY = "identity_rules_version"  # keys in the run's component_versions
IDENTITY_REASONS_KEY = "identity_reason_codes"  # fixed codes only, comma-separated
_OUTCOMES = frozenset(member.value for member in IdentityCheck)
_REASON_CODE = re.compile(r"[a-z][a-z_]{0,31}")
MAX_REASON_CODES = 8


@dataclass(frozen=True)
class IdentityRecord:
    """A stored identity outcome: identifiers, the outcome and fixed codes only (no PII)."""

    document_id: uuid.UUID
    run_id: uuid.UUID
    outcome: str
    reason_codes: tuple[str, ...]
    rules_version: str
    processing_status: str  # ``needs_review`` after a mismatch; otherwise unchanged


def _check_reason_codes(reason_codes: object) -> tuple[str, ...]:
    if isinstance(reason_codes, str) or not isinstance(reason_codes, Sequence):
        raise ValueError("reason_codes must be a sequence of fixed codes")
    codes = tuple(reason_codes)
    if not 1 <= len(codes) <= MAX_REASON_CODES or len(set(codes)) != len(codes):
        raise ValueError(f"reason_codes must hold 1..{MAX_REASON_CODES} distinct codes")
    if not all(isinstance(code, str) and _REASON_CODE.fullmatch(code) for code in codes):
        raise ValueError("reason_codes must be short lowercase identifiers")
    return tuple(sorted(codes))


def record_identity_check(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    outcome: str,
    reason_codes: Sequence[str],
    rules_version: str,
    duration_ms: int,
    factory: sessionmaker[Session] | None = None,
) -> IdentityRecord:
    """Store a document's identity outcome and its succeeded ``identity`` run, all or nothing.

    Input is checked before any database work: ``outcome`` an ``IdentityCheck`` value;
    ``reason_codes`` fixed lowercase codes (never names or dates); ``rules_version`` a short
    identifier. Both are recorded in the run's ``component_versions``. Then, with the document row
    locked: the document must have pages, and no succeeded ``identity`` run may exist (ADR-036 A2).
    ``mismatch`` also sets ``processing_status`` to ``needs_review`` in the same transaction; no
    other outcome changes the status (ADR-036). Logs identifiers and the outcome only.

    Raises:
        ValueError: invalid ``outcome``, ``reason_codes``, ``rules_version`` or ``duration_ms``.
        DocumentProcessingError: ``document_not_found``, ``not_parsed``, ``already_checked``.
    """
    if outcome not in _OUTCOMES:
        raise ValueError("outcome must be an IdentityCheck value")
    codes = _check_reason_codes(reason_codes)
    version = _check_rules_version(rules_version)
    _check_duration(duration_ms)
    with session_scope(factory) as session:
        document = _lock_document(session, patient_id, document_id)
        has_pages = session.scalar(
            select(exists().where(Page.patient_id == patient_id, Page.document_id == document_id))
        )
        if not has_pages:
            raise _error(NOT_PARSED, f"Document {document_id} has no parsed pages")
        if _has_succeeded_run(session, patient_id, document_id, IDENTITY_STAGE):
            raise _error(ALREADY_CHECKED, f"Document {document_id} is already identity-checked")
        document.identity_check = outcome
        if outcome == IdentityCheck.MISMATCH.value:
            document.processing_status = ProcessingStatus.NEEDS_REVIEW.value
        run = PipelineRun(
            patient_id=patient_id,
            document_id=document_id,
            stage=IDENTITY_STAGE,
            status=RunStatus.SUCCEEDED.value,
            started_at=func.now() - dt.timedelta(milliseconds=duration_ms),
            finished_at=func.now(),
            duration_ms=duration_ms,
            component_versions={
                IDENTITY_VERSION_KEY: version,
                IDENTITY_REASONS_KEY: ",".join(codes),
            },
        )
        session.add(run)
        session.flush()
        record = IdentityRecord(
            document_id, run.id, outcome, codes, version, document.processing_status
        )
    logger.info(
        "Identity checked: document_id=%s run_id=%s outcome=%s rules_version=%s",
        document_id,
        record.run_id,
        outcome,
        version,
    )
    return record


def record_identity_failure(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    error_kind: str,
    error_class: str | None = None,
    rules_version: str,
    duration_ms: int,
    factory: sessionmaker[Session] | None = None,
) -> uuid.UUID:
    """Record a failed ``identity`` run; returns its id. The document itself is not changed.

    A failed run never counts as a check; the stage can be retried (ADR-036 A2) and indexing
    stays blocked (``identity_not_checked``) until a check succeeds.

    Raises:
        ValueError: ``error_kind``, ``error_class``, ``rules_version`` or ``duration_ms`` invalid.
        DocumentProcessingError: ``document_not_found``.
    """
    if not _ERROR_KIND.fullmatch(error_kind):
        raise ValueError("error_kind must be a short lowercase identifier")
    if error_class is not None and not _ERROR_CLASS.fullmatch(error_class):
        raise ValueError("error_class must be a class name")
    version = _check_rules_version(rules_version)
    _check_duration(duration_ms)
    with session_scope(factory) as session:
        _lock_document(session, patient_id, document_id)
        run = PipelineRun(
            patient_id=patient_id,
            document_id=document_id,
            stage=IDENTITY_STAGE,
            status=RunStatus.FAILED.value,
            started_at=func.now() - dt.timedelta(milliseconds=duration_ms),
            finished_at=func.now(),
            duration_ms=duration_ms,
            component_versions={IDENTITY_VERSION_KEY: version},
            error_kind=error_kind,
            error_class=error_class,
        )
        session.add(run)
        session.flush()
        run_id = run.id
    logger.info(
        "Identity failure recorded: document_id=%s run_id=%s error_kind=%s",
        document_id,
        run_id,
        error_kind,
    )
    return run_id
