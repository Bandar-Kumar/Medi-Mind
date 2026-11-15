"""Write-once, verified storage of a document's parse outputs (Task 2.5b).

Layout: ``<data_dir>/parsed/<patient_id>/<document_id>/v1/`` holding three files:
- ``ir.v1.json``: the validated IR, serialized by ``dump_ir()`` (schema version 1);
- ``docling.json``: Docling's full JSON output, byte for byte as the parser returned it;
- ``manifest.json``: a ``ParseManifest`` binding both files (SHA-256 and size) to the document,
  the original's SHA-256 and the parse run that produced them.
The directory name is the IR schema version, so a future schema gets its own directory and nothing
is ever overwritten. No database table or column records these files: the path is derived from the
patient and document IDs, and the manifest makes the directory self-verifying.

Publishing (``publish_parse_outputs``), as one unit, never file by file:
1. check the inputs (the IR's SHA-256 and page count must match the document) before writing;
2. create a temporary directory ``.tmp-*`` next to the final one (same parent, so the same volume
   and inside ``data_dir``), write the IR, then the Docling JSON, then the manifest **last**; each
   file is created exclusively, ``fsync``-ed, re-read and compared by SHA-256;
3. publish with one ``os.rename(temp, final)``. On Windows this fails if ``final`` exists (file or
   directory), so it is an atomic "create only if absent"; ``os.replace`` is never used;
4. verify the published directory completely, then mark its files read-only.
A reader therefore sees either no ``v1`` directory or a complete one. Partial writes only ever
exist under a ``.tmp-`` name, which is never read; this call removes its own temporary directory on
failure, and a leftover from a crash stays inert.

If ``v1`` already exists (an earlier attempt, or a concurrent writer winning step 3), it is verified
and reused only if it matches the requested document, source SHA-256 and parse run; otherwise
``integrity_mismatch`` is raised and the existing directory is left untouched.

Verification (``load_verified_parse_outputs`` and before any reuse) checks, in order: exactly the
three expected regular files; the manifest parses and validates; document ID, source SHA-256 and
parse run ID match; each file's SHA-256 and size match the manifest; the IR loads with
``load_ir()``; the IR's SHA-256, schema version and page count match the manifest and the document.

Errors are ``DocumentStorageError``: ``integrity_mismatch`` for any verification failure,
``write_failed`` for I/O failures, ``outside_data_dir`` / ``invalid_key`` for unsafe paths. Messages
name only the failed check: never file contents, document text, patient data or OS error text, and
they are raised outside ``except`` blocks so no chained exception carries such details.

Limitations: directory entries are not ``fsync``-ed (not possible on Windows); read-only is a
safeguard, not a security boundary (as for originals). On POSIX, ``rename`` onto an *empty*
directory succeeds; this module never creates an empty final directory, so only an empty directory
made by someone else could be replaced, and it holds nothing.
"""

import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.config import Settings
from app.core.errors import DocumentParseError, DocumentStorageError
from app.ingestion.ir import IR_SCHEMA_VERSION, ParsedDocument, dump_ir, load_ir
from app.ingestion.storage import (
    StorageErrorKind,
    _check_contained,
    _ensure_read_only,
    _file_sha256,
    sha256_hex,
)

MANIFEST_VERSION = 1
IR_FILENAME = f"ir.v{IR_SCHEMA_VERSION}.json"
DOCLING_FILENAME = "docling.json"
MANIFEST_FILENAME = "manifest.json"
OUTPUT_DIRNAME = f"v{IR_SCHEMA_VERSION}"
ARTIFACT_FILENAMES = (IR_FILENAME, DOCLING_FILENAME)
_EXPECTED_ENTRIES = frozenset({*ARTIFACT_FILENAMES, MANIFEST_FILENAME})
TEMP_PREFIX = ".tmp-"

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


# --- manifest --------------------------------------------------------------------------------


class _Model(BaseModel):
    # Same conventions as the IR: immutable, unknown fields rejected, no input values in errors.
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


def _require_sha256(value: str) -> str:
    if not _SHA256_HEX.fullmatch(value):
        raise ValueError("must be 64 lowercase hexadecimal characters")
    return value


class ArtifactDigest(_Model):
    sha256: str
    size_bytes: int = Field(ge=0)

    @field_validator("sha256")
    @classmethod
    def _sha256_is_hex(cls, value: str) -> str:
        return _require_sha256(value)


class ParseManifest(_Model):
    """Binds the two parse artifacts to their document, source and run. Versioned on its own.

    Holds identifiers and digests only: no timestamps, patient data, filenames or text.
    """

    manifest_version: Literal[1] = MANIFEST_VERSION
    ir_schema_version: Literal[1] = IR_SCHEMA_VERSION
    document_id: uuid.UUID
    source_sha256: str  # SHA-256 of the original PDF (= documents.sha256)
    parse_run_id: uuid.UUID  # the pipeline run that produced the artifacts
    files: dict[str, ArtifactDigest]  # exactly ARTIFACT_FILENAMES

    @field_validator("source_sha256")
    @classmethod
    def _source_sha256_is_hex(cls, value: str) -> str:
        return _require_sha256(value)

    @model_validator(mode="after")
    def _files_are_exactly_the_artifacts(self) -> Self:
        if set(self.files) != set(ARTIFACT_FILENAMES):
            raise ValueError("files must list exactly the IR and Docling JSON artifacts")
        return self


def dump_manifest(manifest: ParseManifest) -> bytes:
    """Deterministic UTF-8 JSON: field order from the model, files in ARTIFACT_FILENAMES order."""
    return manifest.model_dump_json(indent=2).encode("utf-8")


# --- results and errors ----------------------------------------------------------------------


@dataclass(frozen=True)
class ParseOutputs:
    directory: Path
    manifest: ParseManifest
    document: ParsedDocument  # the verified IR, loaded back from disk
    created: bool  # False: a valid, matching directory already existed and was reused


def _error(kind: StorageErrorKind, message: str) -> DocumentStorageError:
    return DocumentStorageError(message, kind=kind)


def _mismatch(message: str) -> DocumentStorageError:
    return _error(StorageErrorKind.INTEGRITY_MISMATCH, message)


# --- paths -----------------------------------------------------------------------------------


def parse_output_dir(settings: Settings, patient_id: uuid.UUID, document_id: uuid.UUID) -> Path:
    """``<parsed_dir>/<patient_id>/<document_id>/v1``. Built from validated UUIDs only."""
    for value in (patient_id, document_id):
        if not isinstance(value, uuid.UUID):
            raise _error(StorageErrorKind.INVALID_KEY, "patient_id and document_id must be UUIDs")
    return settings.parsed_dir / str(patient_id) / str(document_id) / OUTPUT_DIRNAME


# --- publish ---------------------------------------------------------------------------------


def publish_parse_outputs(
    settings: Settings,
    *,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    parse_run_id: uuid.UUID,
    source_sha256: str,
    expected_page_count: int,
    document: ParsedDocument,
    docling_json: bytes,
) -> ParseOutputs:
    """Publish the IR, the Docling JSON and their manifest as one write-once directory.

    ``source_sha256`` and ``expected_page_count`` come from the document record. If the directory
    already exists it is verified against this document, source and ``parse_run_id`` and reused
    (``created=False``); it is never replaced.

    Raises:
        DocumentStorageError: ``integrity_mismatch`` (inputs disagree with the document, Docling
            JSON is not JSON, or an existing directory does not verify), ``write_failed``,
            ``outside_data_dir``, ``invalid_key``.
    """
    final = parse_output_dir(settings, patient_id, document_id)
    _check_publish_inputs(document_id, parse_run_id, source_sha256, expected_page_count)
    if document.sha256 != source_sha256:
        raise _mismatch("The IR was parsed from a different source file")
    if document.page_count != expected_page_count:
        raise _mismatch("The IR page count differs from the document's page count")
    if not _is_json(docling_json):
        raise _mismatch("The Docling output is not valid UTF-8 JSON")
    expected = _Expected(document_id, source_sha256, parse_run_id, expected_page_count)

    data_dir = settings.data_dir
    _check_contained(data_dir, final)
    if os.path.lexists(final):
        return _reuse(data_dir, final, expected)

    ir_bytes = dump_ir(document)
    manifest = ParseManifest(
        document_id=document_id,
        source_sha256=source_sha256,
        parse_run_id=parse_run_id,
        files={
            IR_FILENAME: _digest(ir_bytes),
            DOCLING_FILENAME: _digest(docling_json),
        },
    )
    contents = (
        (IR_FILENAME, ir_bytes),
        (DOCLING_FILENAME, docling_json),
        (MANIFEST_FILENAME, dump_manifest(manifest)),  # last: a complete manifest means all files
    )

    temp = _make_temp_dir(data_dir, final)
    published = False
    try:
        for name, data in contents:
            _write_new_verified(data_dir, temp / name, data)
        published = _rename_once(data_dir, temp, final)
    finally:
        if not published:
            _remove_temp_dir(data_dir, temp)
    if not published:  # a concurrent writer published first
        return _reuse(data_dir, final, expected)

    verified = _verify_directory(data_dir, final, expected)
    _make_files_read_only(final)
    return ParseOutputs(directory=final, manifest=verified[0], document=verified[1], created=True)


def _check_publish_inputs(
    document_id: uuid.UUID, parse_run_id: uuid.UUID, source_sha256: str, page_count: int
) -> None:
    if not isinstance(parse_run_id, uuid.UUID) or not isinstance(document_id, uuid.UUID):
        raise _error(StorageErrorKind.INVALID_KEY, "document_id and parse_run_id must be UUIDs")
    if not isinstance(source_sha256, str) or not _SHA256_HEX.fullmatch(source_sha256):
        raise _error(
            StorageErrorKind.INVALID_KEY,
            "source_sha256 must be 64 lowercase hexadecimal characters",
        )
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
        raise _error(StorageErrorKind.INVALID_KEY, "expected_page_count must be a positive integer")


def _is_json(data: bytes) -> bool:
    try:
        json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return True


def _digest(data: bytes) -> ArtifactDigest:
    return ArtifactDigest(sha256=sha256_hex(data), size_bytes=len(data))


def _make_temp_dir(data_dir: Path, final: Path) -> Path:
    failed = False
    temp: Path | None = None
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
        _check_contained(data_dir, final.parent)  # again, after creating it
        temp = Path(tempfile.mkdtemp(dir=final.parent, prefix=TEMP_PREFIX))
    except OSError:
        failed = True
    if failed or temp is None:
        raise _error(StorageErrorKind.WRITE_FAILED, "Could not create the parse output directory")
    _check_contained(data_dir, temp)
    return temp


def _write_new_verified(data_dir: Path, path: Path, data: bytes) -> None:
    """Create ``path`` exclusively, write and ``fsync`` it, then re-read and compare its hash."""
    _check_contained(data_dir, path)
    failed = False
    try:
        with open(path, "xb") as handle:  # "x": never opens an existing file
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        matches = _file_sha256(path) == sha256_hex(data)
    except OSError:
        failed = True
    if failed:
        raise _error(StorageErrorKind.WRITE_FAILED, "Could not write a parse output file")
    if not matches:
        raise _mismatch("A parse output file differs from the data written")


def _rename_once(data_dir: Path, temp: Path, final: Path) -> bool:
    """Atomically rename ``temp`` to ``final`` unless ``final`` exists. True if published."""
    _check_contained(data_dir, final)  # immediately before publishing
    failed = False
    try:
        os.rename(temp, final)
    except FileExistsError:
        return False
    except OSError:
        failed = True  # POSIX reports a non-empty target as ENOTEMPTY/EEXIST, not FileExistsError
    if failed:
        if os.path.lexists(final):
            return False
        raise _error(StorageErrorKind.WRITE_FAILED, "Could not publish the parse output directory")
    return True


def _remove_temp_dir(data_dir: Path, temp: Path) -> None:
    """Best effort: remove only this call's own ``.tmp-`` directory, never anything else."""
    if not temp.name.startswith(TEMP_PREFIX):
        return
    try:
        _check_contained(data_dir, temp)
        shutil.rmtree(temp)
    except (OSError, DocumentStorageError):
        pass  # a leftover ".tmp-" directory is never read


def _make_files_read_only(directory: Path) -> None:
    for name in sorted(_EXPECTED_ENTRIES):
        _ensure_read_only(directory / name)


# --- verify and reuse ------------------------------------------------------------------------


@dataclass(frozen=True)
class _Expected:
    document_id: uuid.UUID
    source_sha256: str
    parse_run_id: uuid.UUID
    page_count: int


def load_verified_parse_outputs(
    settings: Settings,
    *,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    parse_run_id: uuid.UUID,
    source_sha256: str,
    expected_page_count: int,
) -> ParseOutputs | None:
    """Load and fully verify a published directory; None if nothing has been published.

    ``parse_run_id`` is the run expected to have produced the outputs (e.g. a recovered run).

    Raises:
        DocumentStorageError: ``integrity_mismatch`` if the directory exists but does not verify,
            ``write_failed`` if it cannot be read, ``outside_data_dir``, ``invalid_key``.
    """
    final = parse_output_dir(settings, patient_id, document_id)
    _check_publish_inputs(document_id, parse_run_id, source_sha256, expected_page_count)
    _check_contained(settings.data_dir, final)
    if not os.path.lexists(final):
        return None
    expected = _Expected(document_id, source_sha256, parse_run_id, expected_page_count)
    return _reuse(settings.data_dir, final, expected)


def read_published_manifest(
    settings: Settings,
    *,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    source_sha256: str,
) -> ParseManifest | None:
    """Pre-check a published directory and return its manifest; None if nothing is published.

    Checks, before the manifest's producing run may be used for anything: containment; exactly
    the three expected regular files; the manifest parses and validates; it names this document
    and this source SHA-256. It does not check the artifacts: callers that reuse the outputs must
    then confirm the producing run and call ``load_verified_parse_outputs`` (full verification).

    Raises:
        DocumentStorageError: ``integrity_mismatch``, ``write_failed`` (unreadable),
            ``outside_data_dir``, ``invalid_key``.
    """
    final = parse_output_dir(settings, patient_id, document_id)
    if not isinstance(source_sha256, str) or not _SHA256_HEX.fullmatch(source_sha256):
        raise _error(
            StorageErrorKind.INVALID_KEY,
            "source_sha256 must be 64 lowercase hexadecimal characters",
        )
    data_dir = settings.data_dir
    _check_contained(data_dir, final)
    if not os.path.lexists(final):
        return None
    if _list_regular_files(final) != _EXPECTED_ENTRIES:
        raise _mismatch("The parse output directory does not hold exactly the expected files")
    manifest = _load_manifest(_read(data_dir, final / MANIFEST_FILENAME))
    if manifest.document_id != document_id:
        raise _mismatch("The parse outputs belong to a different document")
    if manifest.source_sha256 != source_sha256:
        raise _mismatch("The parse outputs were made from a different source file")
    return manifest


def _reuse(data_dir: Path, final: Path, expected: _Expected) -> ParseOutputs:
    manifest, document = _verify_directory(data_dir, final, expected)
    _make_files_read_only(final)  # an earlier attempt may have stopped before this step
    return ParseOutputs(directory=final, manifest=manifest, document=document, created=False)


def _verify_directory(
    data_dir: Path, directory: Path, expected: _Expected
) -> tuple[ParseManifest, ParsedDocument]:
    _check_contained(data_dir, directory)
    entries = _list_regular_files(directory)
    if entries != _EXPECTED_ENTRIES:
        raise _mismatch("The parse output directory does not hold exactly the expected files")

    manifest = _load_manifest(_read(data_dir, directory / MANIFEST_FILENAME))
    if manifest.document_id != expected.document_id:
        raise _mismatch("The parse outputs belong to a different document")
    if manifest.source_sha256 != expected.source_sha256:
        raise _mismatch("The parse outputs were made from a different source file")
    if manifest.parse_run_id != expected.parse_run_id:
        raise _mismatch("The parse outputs were made by a different parse run")

    contents: dict[str, bytes] = {}
    for name in ARTIFACT_FILENAMES:
        data = _read(data_dir, directory / name)
        digest = manifest.files[name]
        if len(data) != digest.size_bytes or sha256_hex(data) != digest.sha256:
            raise _mismatch(f"{name} does not match its manifest entry")
        contents[name] = data

    document = _load_ir_or_mismatch(contents[IR_FILENAME])
    if document.sha256 != manifest.source_sha256:
        raise _mismatch("The IR was parsed from a different source file than the manifest names")
    if document.schema_version != manifest.ir_schema_version:
        raise _mismatch("The IR schema version differs from the manifest")
    if document.page_count != expected.page_count:
        raise _mismatch("The IR page count differs from the document's page count")
    return manifest, document


def _list_regular_files(directory: Path) -> frozenset[str]:
    """Names of the regular files in ``directory`` (no symlinks); fails if it is no directory."""
    failed = False
    names: set[str] = set()
    irregular = False
    try:
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or directory.is_junction():
            irregular = True
        else:
            for entry in os.scandir(directory):
                if entry.is_file(follow_symlinks=False):
                    names.add(entry.name)
                else:
                    irregular = True
    except OSError:
        failed = True
    if failed:
        raise _error(StorageErrorKind.WRITE_FAILED, "Could not read the parse output directory")
    if irregular:
        raise _mismatch("The parse output directory contains unexpected entries")
    return frozenset(names)


def _read(data_dir: Path, path: Path) -> bytes:
    _check_contained(data_dir, path)
    failed = False
    data = b""
    try:
        data = path.read_bytes()
    except OSError:
        failed = True
    if failed:
        raise _error(StorageErrorKind.WRITE_FAILED, "Could not read a parse output file")
    return data


def _load_manifest(data: bytes) -> ParseManifest:
    invalid = False
    manifest: ParseManifest | None = None
    try:
        manifest = ParseManifest.model_validate_json(data)
    except ValidationError:
        invalid = True
    if invalid or manifest is None:
        raise _mismatch("The parse output manifest is invalid")
    return manifest


def _load_ir_or_mismatch(data: bytes) -> ParsedDocument:
    invalid = False
    document: ParsedDocument | None = None
    try:
        document = load_ir(data)
    except DocumentParseError:
        invalid = True
    if invalid or document is None:
        raise _mismatch("The stored IR is not valid IR")
    return document
