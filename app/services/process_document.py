"""Parse one stored document into verified parse outputs and ``pages`` rows (Task 2.5d).

``process_document`` runs the agreed protocol, one step per transaction, never holding a database
transaction open while parsing or touching files:

    Tx A   claim_document_for_parsing: lock the document, claim it, insert a running parse run
           (its id is the claim token); already_running / already_parsed / document_not_found
           propagate unchanged, and no failure is recorded (no run of this call exists).
    --     verify the stored original: recomputed content-address path, containment, readable,
           SHA-256, valid PDF, page count equal to the recorded one (the record is authoritative)
    --     reuse published outputs or parse and publish them:
           reuse: pre-check the manifest (layout, document, source), confirm its producing run is a
                  parse run of this document, then fully verify artifacts and IR;
           parse: parser.parse(), then publish_parse_outputs(); if another (recovered) worker
                  published first, its outputs are reused through the same checks
    Tx B   persist_ir_pages: re-check the active claim under the row lock, insert every page or
           none, mark this run succeeded
    Tx C   on an ordinary exception or KeyboardInterrupt only: record_parse_failure (best effort;
           it writes nothing if the claim was lost), then re-raise the ORIGINAL exception.
           SystemExit and GeneratorExit are not handled: the claim goes stale and is recovered
           with ``recover_stale=True`` after STALE_CLAIM_SECONDS.

Provenance: this run's ``component_versions`` describe only the parser configured for this attempt
(``configured_*`` keys). When outputs are reused, the run that produced them is the manifest's
``parse_run_id``; that run's own ``pipeline_runs`` row keeps its versions unchanged. Nothing here
rewrites a manifest, a published file or a page row.

Errors and logs carry identifiers, counts, kinds and class names only: never patient data, page
text, filenames or filesystem paths.
"""

import logging
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings
from app.core.errors import (
    DocumentParseError,
    DocumentProcessingError,
    DocumentStorageError,
    DocumentValidationError,
)
from app.ingestion.ir import IR_SCHEMA_VERSION, ParserInfo
from app.ingestion.parse_outputs import (
    MANIFEST_VERSION,
    ParseManifest,
    ParseOutputs,
    load_verified_parse_outputs,
    publish_parse_outputs,
    read_published_manifest,
)
from app.ingestion.parsing import ParseResult
from app.ingestion.storage import (
    StorageErrorKind,
    _check_contained,
    original_path,
    sha256_hex,
    storage_path_for_db,
)
from app.ingestion.validation import ValidatedPdf, validate_pdf_bytes
from app.records.db import describe_db_error, is_database_error
from app.records.repositories import (
    DocumentRef,
    ParseClaim,
    ProcessingErrorKind,
    claim_document_for_parsing,
    parse_run_belongs_to_document,
    record_parse_failure,
)
from app.services.parsed_pages import persist_ir_pages

logger = logging.getLogger(__name__)

INTERRUPTED = "interrupted"
UNEXPECTED = "unexpected"
DATABASE_ERROR = "database_error"
# Same shapes the repository accepts; anything else is recorded as "unexpected" / no class.
_SAFE_KIND = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SAFE_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")


class Parser(Protocol):
    """What the service needs from a parser: ``DoclingParser`` (one per batch), or a test fake."""

    @property
    def parser_info(self) -> ParserInfo: ...

    def parse(self, pdf: ValidatedPdf) -> ParseResult: ...


@dataclass(frozen=True)
class ProcessResult:
    document_id: uuid.UUID
    run_id: uuid.UUID  # this call's claim: the run that persisted the pages
    producing_run_id: uuid.UUID  # the run named in the manifest, which produced the artifacts
    reused_outputs: bool  # True when the artifacts came from an earlier run
    page_count: int


def process_document(
    settings: Settings,
    parser: Parser,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    recover_stale: bool = False,
) -> ProcessResult:
    """Parse (or reuse earlier verified outputs for) one document and store its pages.

    Raises:
        DocumentProcessingError: from the claim (``already_running``, ``already_parsed``,
            ``document_not_found``), ``integrity_mismatch``, ``text_not_storable``,
            ``claim_lost``.
        DocumentStorageError: unsafe or mismatched paths, unreadable files, outputs that do not
            verify (``integrity_mismatch``).
        DocumentValidationError: the stored original is no longer a valid PDF.
        DocumentParseError: the parser failed (any ``ParseErrorKind``).
        Database errors propagate unchanged; entry points sanitize them.
        After a claim, every ordinary exception is recorded (Tx C) and then re-raised unchanged.
    """
    started = time.perf_counter()
    claim = claim_document_for_parsing(  # Tx A
        patient_id,
        document_id,
        component_versions=_configured_versions(parser),
        recover_stale=recover_stale,
    )
    try:
        expected = _recorded_page_count(claim.document)
        pdf = _verified_original(settings, patient_id, claim.document, expected)
        outputs = _reuse_or_parse(settings, parser, patient_id, claim, pdf, expected)
        page_count = persist_ir_pages(  # Tx B: verifies the active claim before inserting
            patient_id, document_id, claim.run_id, outputs.document, expected_page_count=expected
        )
    except Exception as exc:
        _record_failure(patient_id, document_id, claim.run_id, exc)  # Tx C; never raises
        raise  # the original exception, with its original traceback
    except KeyboardInterrupt as exc:
        _record_failure(patient_id, document_id, claim.run_id, exc)  # Tx C; never raises
        raise
    producing_run_id = outputs.manifest.parse_run_id
    result = ProcessResult(
        document_id=document_id,
        run_id=claim.run_id,
        producing_run_id=producing_run_id,
        reused_outputs=producing_run_id != claim.run_id,
        page_count=page_count,
    )
    logger.info(
        "Document parsed: document_id=%s run_id=%s producing_run_id=%s reused_outputs=%s "
        "pages=%d duration_ms=%d",
        document_id,
        claim.run_id,
        producing_run_id,
        result.reused_outputs,
        page_count,
        round((time.perf_counter() - started) * 1000),
    )
    return result


# --- Tx A inputs -----------------------------------------------------------------------------


def _configured_versions(parser: Parser) -> Mapping[str, str]:
    """Versions of the parser configured for THIS attempt (not of any earlier producing run)."""
    info = parser.parser_info
    versions = {
        "configured_parser": info.name,
        "configured_parser_version": info.version,
        "ir_schema_version": str(IR_SCHEMA_VERSION),
        "manifest_version": str(MANIFEST_VERSION),
    }
    for option, key in (
        ("docling_core_version", "configured_docling_core_version"),
        ("ocr_engine", "configured_ocr_engine"),
        ("ocr_backend", "configured_ocr_backend"),
    ):
        value = info.options.get(option)
        if isinstance(value, str) and value:
            versions[key] = value
    return versions


# --- the stored original ---------------------------------------------------------------------


def _recorded_page_count(document: DocumentRef) -> int:
    """The document record's page count is authoritative; a missing one is an integrity error."""
    if document.page_count is None:
        raise DocumentProcessingError(
            f"Document {document.id} has no recorded page count",
            kind=ProcessingErrorKind.INTEGRITY_MISMATCH,
        )
    return document.page_count


def _verified_original(
    settings: Settings, patient_id: uuid.UUID, document: DocumentRef, expected_page_count: int
) -> ValidatedPdf:
    """Read and check the authoritative original. Runs before parsing AND before reuse."""
    path = original_path(settings, patient_id, document.sha256)  # validates both keys
    if storage_path_for_db(settings, path) != document.storage_path:
        raise DocumentStorageError(
            f"Document {document.id}: the recorded storage path is not the original's "
            "content address",
            kind=StorageErrorKind.INTEGRITY_MISMATCH,
        )
    _check_contained(settings.data_dir, path)  # inside data_dir, no symlink or junction
    unreadable = False
    data = b""
    try:
        data = path.read_bytes()
    except OSError:
        unreadable = True
    if unreadable:  # raised outside the except block: no OS error text is chained
        raise DocumentStorageError(
            f"Document {document.id}: the stored original could not be read",
            kind=StorageErrorKind.WRITE_FAILED,
        )
    if sha256_hex(data) != document.sha256:
        raise DocumentStorageError(
            f"Document {document.id}: the stored original differs from its recorded SHA-256",
            kind=StorageErrorKind.INTEGRITY_MISMATCH,
        )
    pdf = validate_pdf_bytes(
        data, max_bytes=settings.max_upload_bytes, max_pages=settings.max_pages
    )
    if pdf.page_count != expected_page_count:
        raise DocumentProcessingError(
            f"Document {document.id}: the stored original has {pdf.page_count} pages; "
            f"the document record has {expected_page_count}",
            kind=ProcessingErrorKind.INTEGRITY_MISMATCH,
        )
    return pdf


# --- reuse or parse --------------------------------------------------------------------------


def _reuse_or_parse(
    settings: Settings,
    parser: Parser,
    patient_id: uuid.UUID,
    claim: ParseClaim,
    pdf: ValidatedPdf,
    expected_page_count: int,
) -> ParseOutputs:
    document = claim.document
    manifest = read_published_manifest(
        settings, patient_id=patient_id, document_id=document.id, source_sha256=document.sha256
    )
    if manifest is not None:
        return _reuse(settings, patient_id, document, manifest, expected_page_count)

    result = parser.parse(pdf)  # minutes; no database transaction is open
    lost_race: DocumentStorageError | None = None
    try:
        return publish_parse_outputs(
            settings,
            patient_id=patient_id,
            document_id=document.id,
            parse_run_id=claim.run_id,
            source_sha256=document.sha256,
            expected_page_count=expected_page_count,
            document=result.document,
            docling_json=result.docling_json,
        )
    except DocumentStorageError as exc:
        if exc.kind != StorageErrorKind.INTEGRITY_MISMATCH:
            raise
        lost_race = exc
    # Another worker (a recovered stale claim) may have published between our check and our
    # publish. Reuse its outputs through the same checks; otherwise keep the original error.
    manifest = read_published_manifest(
        settings, patient_id=patient_id, document_id=document.id, source_sha256=document.sha256
    )
    if manifest is None or manifest.parse_run_id == claim.run_id:
        raise lost_race
    return _reuse(settings, patient_id, document, manifest, expected_page_count)


def _reuse(
    settings: Settings,
    patient_id: uuid.UUID,
    document: DocumentRef,
    manifest: ParseManifest,
    expected_page_count: int,
) -> ParseOutputs:
    """Reuse outputs whose manifest passed the pre-check: confirm the run, then verify fully."""
    if not parse_run_belongs_to_document(patient_id, document.id, manifest.parse_run_id):
        raise DocumentStorageError(
            f"Document {document.id}: the parse outputs name a run that is not a parse run "
            "of this document",
            kind=StorageErrorKind.INTEGRITY_MISMATCH,
        )
    outputs = load_verified_parse_outputs(
        settings,
        patient_id=patient_id,
        document_id=document.id,
        parse_run_id=manifest.parse_run_id,
        source_sha256=document.sha256,
        expected_page_count=expected_page_count,
    )
    if outputs is None:  # write-once: a published directory does not disappear
        raise DocumentStorageError(
            f"Document {document.id}: the published parse outputs are missing",
            kind=StorageErrorKind.INTEGRITY_MISMATCH,
        )
    logger.info(
        "Reusing parse outputs: document_id=%s producing_run_id=%s",
        document.id,
        manifest.parse_run_id,
    )
    return outputs


# --- Tx C ------------------------------------------------------------------------------------


def _record_failure(
    patient_id: uuid.UUID, document_id: uuid.UUID, run_id: uuid.UUID, exc: BaseException
) -> None:
    """Best-effort Tx C. NEVER raises, so the caller always re-raises the original exception.

    The broad handler is confined to this helper: it also swallows a second KeyboardInterrupt
    (or any other error) that arrives while the failure is being recorded. If recording fails,
    the claim stays running and is recovered later through the stale-claim policy.
    """
    try:
        record_parse_failure(
            patient_id,
            document_id,
            run_id,
            error_kind=_failure_kind(exc),
            error_class=_failure_class(exc),
        )
    except BaseException as failure:  # deliberate: must never replace the original exception
        try:
            reason = (
                describe_db_error(failure) if is_database_error(failure) else _class_of(failure)
            )
            logger.warning(
                "Parse failure could not be recorded: document_id=%s run_id=%s (%s)",
                document_id,
                run_id,
                reason,
            )
        except BaseException:  # deliberate: logging must not replace the original either
            pass


def _failure_kind(exc: BaseException) -> str:
    if isinstance(exc, KeyboardInterrupt):
        kind = INTERRUPTED
    elif isinstance(exc, DocumentParseError | DocumentStorageError | DocumentProcessingError):
        kind = str(exc.kind)
    elif isinstance(exc, DocumentValidationError):
        kind = str(exc.reason)
    elif is_database_error(exc):
        kind = DATABASE_ERROR
    else:
        kind = UNEXPECTED
    return kind if _SAFE_KIND.fullmatch(kind) else UNEXPECTED


def _failure_class(exc: BaseException) -> str | None:
    name = _class_of(exc)
    return name if _SAFE_CLASS.fullmatch(name) else None


def _class_of(exc: BaseException) -> str:
    return type(exc).__name__
