"""Patient-scoped repositories for document ingestion and parsing (Task 2.5a; part of Task 1.8).

The records layer is the only database access path (ADR-016). Every function here:
- takes ``patient_id`` and filters every query by it, so one patient's call can never read or
  change another patient's rows (the composite foreign keys enforce the same in the database);
- runs in exactly one transaction (``session_scope``: commit on success, roll back on any error);
- returns plain frozen dataclasses, never ORM objects or sessions.

The one exception to the first rule is ``create_patient`` (Task 2.6a): it creates the patient scope
itself, so there is no ``patient_id`` to take yet. ``patient_exists`` is scoped by its argument.

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
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import Integer, cast, exists, extract, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import DocumentProcessingError, PatientError
from app.records.db import session_scope
from app.records.models import (
    Document,
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
# documents.patient_id -> patients.id (models.NAMING_CONVENTION; migration 0001). Only this
# constraint means "unknown patient"; any other foreign-key violation propagates unchanged.
_DOCUMENT_PATIENT_FK = "fk_documents_patient_id_patients"

PATIENT_NOT_FOUND = "patient_not_found"
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


def _is_patient_fk_violation(exc: IntegrityError) -> bool:
    """True only for a violation of ``documents.patient_id -> patients.id`` (an unknown patient)."""
    original = exc.orig
    if getattr(original, "sqlstate", None) != _FOREIGN_KEY_VIOLATION:
        return False
    diag = getattr(original, "diag", None)
    return getattr(diag, "constraint_name", None) == _DOCUMENT_PATIENT_FK


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
