"""Unit tests for ``upload_document`` (Task 2.6b). No database is used.

The two repository functions are replaced by in-memory fakes on the service module; validation,
hashing and write-once storage are the real implementations, writing to a temporary data
directory. Real PostgreSQL is covered in tests/integration/test_upload_document_integration.py.
"""

import ast
import logging
import os
import stat
import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import (
    DocumentProcessingError,
    DocumentStorageError,
    DocumentValidationError,
    PatientError,
)
from app.ingestion import storage
from app.records.repositories import CreatedDocument, DocumentRef
from app.services import upload_document as svc
from tests.fixtures import pdfs

SECRET_NAME = "Secret Fictional Person lab report.pdf"


class FakeRecords:
    """In-memory stand-ins for ``patient_exists`` and ``create_document``; records every call."""

    def __init__(self, patients: set[uuid.UUID]) -> None:
        self.patients = patients
        self.rows: dict[tuple[uuid.UUID, str], DocumentRef] = {}
        self.calls: list[tuple[str, uuid.UUID]] = []
        self.create_arguments: list[dict] = []
        self.fail_create: BaseException | None = None

    def patient_exists(self, patient_id: uuid.UUID) -> bool:
        self.calls.append(("patient_exists", patient_id))
        return patient_id in self.patients

    def create_document(self, patient_id: uuid.UUID, **kw) -> CreatedDocument:
        self.calls.append(("create_document", patient_id))
        self.create_arguments.append(kw)
        if self.fail_create is not None:
            raise self.fail_create
        key = (patient_id, kw["sha256"])
        if key in self.rows:
            return CreatedDocument(document=self.rows[key], created=False)
        ref = DocumentRef(
            id=uuid.uuid4(),
            patient_id=patient_id,
            sha256=kw["sha256"],
            storage_path=kw["storage_path"],
            page_count=kw["page_count"],
            processing_status="uploaded",
        )
        self.rows[key] = ref
        return CreatedDocument(document=ref, created=True)


@pytest.fixture
def patient() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_patient() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def records(monkeypatch, patient, other_patient) -> FakeRecords:
    fake = FakeRecords({patient, other_patient})
    monkeypatch.setattr(svc, "patient_exists", fake.patient_exists)
    monkeypatch.setattr(svc, "create_document", fake.create_document)
    return fake


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def _upload(settings, patient_id, data=None, name="report.pdf") -> svc.UploadResult:
    data = pdfs.native_pdf(2) if data is None else data
    return svc.upload_document(settings, patient_id, data, original_filename=name)


def _file(settings: Settings, patient_id: uuid.UUID, data: bytes) -> Path:
    return settings.uploads_dir / str(patient_id) / f"{storage.sha256_hex(data)}.pdf"


def _remove_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.unlink()


# --- happy path and result ---------------------------------------------------------------------


def test_upload_stores_the_exact_bytes_and_creates_the_record(settings, records, patient) -> None:
    data = pdfs.native_pdf(2)
    result = _upload(settings, patient, data)
    path = _file(settings, patient, data)
    assert path.read_bytes() == data
    assert not os.stat(path).st_mode & stat.S_IWRITE  # read-only safeguard
    assert result.duplicate is False and result.original_stored is True
    assert result.document.patient_id == patient
    [arguments] = records.create_arguments
    assert arguments == {
        "original_filename": "report.pdf",
        "storage_path": f"uploads/{patient}/{storage.sha256_hex(data)}.pdf",
        "sha256": storage.sha256_hex(data),
        "size_bytes": len(data),
        "page_count": 2,
    }


def test_upload_result_is_frozen(settings, records, patient) -> None:
    result = _upload(settings, patient)
    with pytest.raises(FrozenInstanceError):
        result.duplicate = True  # type: ignore[misc]


def test_steps_run_in_the_approved_order(settings, records, patient, monkeypatch) -> None:
    events: list[str] = []

    def spy(name, function):
        def wrapper(*args, **kw):
            events.append(name)
            return function(*args, **kw)

        return wrapper

    monkeypatch.setattr(svc, "validate_pdf_bytes", spy("validate", svc.validate_pdf_bytes))
    monkeypatch.setattr(svc, "patient_exists", spy("patient", svc.patient_exists))
    monkeypatch.setattr(svc, "store_original", spy("store", svc.store_original))
    monkeypatch.setattr(svc, "create_document", spy("create", svc.create_document))
    _upload(settings, patient)
    assert events == ["validate", "patient", "store", "create"]


def test_upload_never_starts_parsing() -> None:
    tree = ast.parse(Path(svc.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any("process_document" in name or "parsing" in name for name in imported)


# --- input types --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("patient_id", "data", "name"),
    [
        ("not-a-uuid", b"%PDF-", "a.pdf"),
        (None, b"%PDF-", "a.pdf"),
        (uuid.uuid4(), bytearray(b"%PDF-"), "a.pdf"),
        (uuid.uuid4(), memoryview(b"%PDF-"), "a.pdf"),
        (uuid.uuid4(), "%PDF-", "a.pdf"),
        (uuid.uuid4(), None, "a.pdf"),
        (uuid.uuid4(), b"%PDF-", None),
        (uuid.uuid4(), b"%PDF-", b"a.pdf"),
    ],
)
def test_wrong_types_are_rejected_before_any_io(settings, records, patient_id, data, name) -> None:
    with pytest.raises(TypeError):
        svc.upload_document(settings, patient_id, data, original_filename=name)
    assert records.calls == [] and not settings.data_dir.exists()


# --- validation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "limits", "reason"),
    [
        (b"", {}, "empty"),
        (pdfs.native_pdf(1), {"max_upload_bytes": 100}, "too_large"),
        (pdfs.png_bytes(), {}, "not_pdf"),
        (pdfs.truncated_pdf(), {}, "unreadable"),
        (pdfs.user_password_pdf(), {}, "encrypted"),
        (pdfs.owner_only_encrypted_pdf(), {}, "encrypted"),
        (pdfs.native_pdf(3), {"max_pages": 2}, "too_many_pages"),
    ],
)
def test_invalid_pdfs_write_nothing_and_touch_no_records(
    tmp_path, records, patient, data, limits, reason
) -> None:
    settings = Settings(data_dir=tmp_path / "data", **limits)
    with pytest.raises(DocumentValidationError) as caught:
        _upload(settings, patient, data, name=SECRET_NAME)
    assert caught.value.reason == reason
    assert "Secret" not in str(caught.value)
    assert records.calls == [] and not settings.data_dir.exists()


def test_the_pdf_is_validated_before_the_patient_is_checked(settings, records) -> None:
    with pytest.raises(DocumentValidationError):
        _upload(settings, uuid.uuid4(), pdfs.png_bytes())
    assert records.calls == []


# --- patient ------------------------------------------------------------------------------------


def test_unknown_patient_creates_no_directory_and_no_record(settings, records) -> None:
    unknown = uuid.uuid4()
    with pytest.raises(PatientError) as caught:
        _upload(settings, unknown, name=SECRET_NAME)
    assert caught.value.kind == "patient_not_found"
    assert str(unknown) in str(caught.value) and "Secret" not in str(caught.value)
    assert records.calls == [("patient_exists", unknown)]
    assert not settings.data_dir.exists()


def test_same_bytes_for_two_patients_are_stored_and_recorded_separately(
    settings, records, patient, other_patient
) -> None:
    data = pdfs.native_pdf(1)
    first = _upload(settings, patient, data)
    second = _upload(settings, other_patient, data)
    assert first.document.id != second.document.id
    assert second.duplicate is False and second.original_stored is True
    assert _file(settings, patient, data).read_bytes() == data
    assert _file(settings, other_patient, data).read_bytes() == data
    assert first.document.storage_path != second.document.storage_path


# --- filenames ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "stored_name"),
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\x\\report.pdf", "report.pdf"),
        ("evil" + chr(0x202E) + "fdp.exe", "evilfdp.exe"),
        ("CON.pdf", "_CON.pdf"),
        ("", "unnamed.pdf"),
        ("   ...   ", "unnamed.pdf"),
        ("a" * 300 + ".pdf", "a" * 251 + ".pdf"),
    ],
)
def test_filenames_are_sanitized_metadata_and_never_build_the_path(
    settings, records, patient, name, stored_name
) -> None:
    data = pdfs.native_pdf(1)
    _upload(settings, patient, data, name=name)
    [arguments] = records.create_arguments
    assert arguments["original_filename"] == stored_name
    assert arguments["storage_path"] == f"uploads/{patient}/{storage.sha256_hex(data)}.pdf"
    assert [p.name for p in (settings.uploads_dir / str(patient)).iterdir()] == [
        f"{storage.sha256_hex(data)}.pdf"
    ]


# --- duplicates, restoration and retries ------------------------------------------------------


def test_duplicate_returns_the_existing_record_and_leaves_the_file_alone(
    settings, records, patient, caplog
) -> None:
    data = pdfs.native_pdf(2)
    first = _upload(settings, patient, data)
    path = _file(settings, patient, data)
    before = os.stat(path)
    caplog.set_level(logging.INFO)
    again = _upload(settings, patient, data, name="renamed.pdf")
    after = os.stat(path)
    assert again.document == first.document
    assert again.duplicate is True and again.original_stored is False
    assert (after.st_ino, after.st_mtime_ns, after.st_size) == (
        before.st_ino,
        before.st_mtime_ns,
        before.st_size,
    )
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_missing_original_is_restored_and_logged_as_a_warning(
    settings, records, patient, caplog
) -> None:
    data = pdfs.native_pdf(2)
    first = _upload(settings, patient, data)
    path = _file(settings, patient, data)
    _remove_read_only(path)
    caplog.set_level(logging.INFO)
    again = _upload(settings, patient, data, name=SECRET_NAME)
    assert again.document == first.document
    assert again.duplicate is True and again.original_stored is True
    assert path.read_bytes() == data and not os.stat(path).st_mode & stat.S_IWRITE
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    message = warning.getMessage()
    # The same flags also arise from concurrent uploads, so the warning must not claim a restore.
    assert message.startswith("Original file published for an existing document record:")
    assert "restored" not in message.lower() and "missing" not in message.lower()
    assert str(patient) in message and str(first.document.id) in message
    assert "Secret" not in message and str(settings.data_dir) not in message


def test_record_failure_keeps_the_file_and_a_retry_succeeds(settings, records, patient) -> None:
    data = pdfs.native_pdf(1)
    boom = RuntimeError("database unavailable (simulated)")
    records.fail_create = boom
    with pytest.raises(RuntimeError) as caught:
        _upload(settings, patient, data)
    assert caught.value is boom  # the original exception, unchanged
    path = _file(settings, patient, data)
    assert path.read_bytes() == data and records.rows == {}
    records.fail_create = None
    retry = _upload(settings, patient, data)
    assert retry.duplicate is False and retry.original_stored is False


def test_different_bytes_at_the_content_address_are_never_overwritten(
    settings, records, patient
) -> None:
    data = pdfs.native_pdf(1)
    path = _file(settings, patient, data)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"%PDF-1.7 planted different bytes")
    with pytest.raises(DocumentStorageError) as caught:
        _upload(settings, patient, data)
    assert caught.value.kind == "integrity_mismatch"
    assert path.read_bytes() == b"%PDF-1.7 planted different bytes"
    assert ("create_document", patient) not in records.calls


def test_record_integrity_mismatch_propagates(settings, records, patient) -> None:
    records.fail_create = DocumentProcessingError("mismatch", kind="integrity_mismatch")
    with pytest.raises(DocumentProcessingError) as caught:
        _upload(settings, patient)
    assert caught.value.kind == "integrity_mismatch"


# --- logging ------------------------------------------------------------------------------------


def test_logs_hold_identifiers_and_counts_only(settings, records, patient, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    result = _upload(settings, patient, pdfs.native_pdf(2), name=SECRET_NAME)
    assert str(result.document.id) in caplog.text and str(patient) in caplog.text
    assert "Secret" not in caplog.text
    assert "Test Person" not in caplog.text  # text inside the fixture PDF
    assert str(settings.data_dir) not in caplog.text and result.document.sha256 not in caplog.text
