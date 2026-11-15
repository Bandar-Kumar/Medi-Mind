"""Integration tests for ``upload_document`` (Task 2.6b) with real PostgreSQL (``medimind_test``).

Repository sessions are redirected by ``test_db_session_factory``, which checks every connection's
database; ``main_database_unchanged`` checks the application database. Files go to a temporary
data directory. Patients are fictional and removed with their rows afterwards.
"""

import logging
import os
import stat
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.errors import DocumentValidationError, PatientError
from app.ingestion.ir import ParsedDocument, ParsedPage, ParseMethod, ParserInfo
from app.ingestion.parsing import ParseResult
from app.ingestion.storage import sha256_hex
from app.ingestion.validation import ValidatedPdf
from app.services import process_document as process_svc
from app.services import upload_document as svc
from tests.fixtures import pdfs
from tests.integration._guards import TEST_DATABASE, assert_is_test_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

JOIN_TIMEOUT = 30
SECRET_NAME = "Secret Fictional Person lab report.pdf"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def sql(test_engine: Engine, app_settings: Settings) -> Callable[..., list]:
    def run(statement: str, **params: object) -> list:
        with test_engine.begin() as connection:
            assert_is_test_database(connection, app_settings.db_name)
            result = connection.execute(text(statement), params)
            return list(result) if result.returns_rows else []

    return run


def _upload(settings, patient_id, data, name="report.pdf") -> svc.UploadResult:
    return svc.upload_document(settings, patient_id, data, original_filename=name)


def _file(settings: Settings, patient_id: uuid.UUID, data: bytes) -> Path:
    return settings.uploads_dir / str(patient_id) / f"{sha256_hex(data)}.pdf"


def _documents(sql, patient_id: uuid.UUID) -> list:
    return sql(
        "SELECT id, original_filename, storage_path, sha256, mime_type, size_bytes, page_count, "
        "processing_status FROM documents WHERE patient_id = :p ORDER BY created_at",
        p=patient_id,
    )


def test_upload_creates_the_file_and_the_uploaded_record(
    settings, test_patient, sql, test_db_session_factory
) -> None:
    _, log = test_db_session_factory
    data = pdfs.native_pdf(2)
    result = _upload(settings, test_patient, data, name="../Fictional" + chr(0x202E) + ".pdf")
    digest = sha256_hex(data)
    assert result.duplicate is False and result.original_stored is True
    assert _documents(sql, test_patient) == [
        (
            result.document.id,
            "Fictional.pdf",
            f"uploads/{test_patient}/{digest}.pdf",
            digest,
            "application/pdf",
            len(data),
            2,
            "uploaded",
        )
    ]
    assert _file(settings, test_patient, data).read_bytes() == data
    assert sql("SELECT count(*) FROM pipeline_runs WHERE patient_id = :p", p=test_patient) == [
        (0,)
    ]  # upload never starts a pipeline run or parsing
    assert set(log.databases) == {TEST_DATABASE}


def test_duplicate_in_the_same_patient_returns_the_existing_record(
    settings, test_patient, sql
) -> None:
    data = pdfs.native_pdf(1)
    first = _upload(settings, test_patient, data)
    again = _upload(settings, test_patient, data, name="another-name.pdf")
    assert again.document == first.document
    assert again.duplicate is True and again.original_stored is False
    rows = _documents(sql, test_patient)
    assert len(rows) == 1 and rows[0][1] == "report.pdf"  # the first filename is kept


def test_same_bytes_for_another_patient_are_isolated(
    settings, test_patient, second_patient, sql
) -> None:
    data = pdfs.native_pdf(1)
    a = _upload(settings, test_patient, data)
    b = _upload(settings, second_patient, data)
    assert a.document.id != b.document.id
    assert b.duplicate is False and b.original_stored is True
    assert [r[0] for r in _documents(sql, test_patient)] == [a.document.id]
    assert [r[0] for r in _documents(sql, second_patient)] == [b.document.id]
    assert _file(settings, test_patient, data) != _file(settings, second_patient, data)
    assert _file(settings, second_patient, data).read_bytes() == data
    # Each patient's duplicate upload finds only its own record.
    assert _upload(settings, test_patient, data).document.id == a.document.id
    assert _upload(settings, second_patient, data).document.id == b.document.id


def test_concurrent_identical_uploads_create_one_record_and_one_file(
    settings, test_patient, sql
) -> None:
    data = pdfs.native_pdf(2)
    barrier = threading.Barrier(4)
    results: list[svc.UploadResult] = []
    errors: list[BaseException] = []

    def upload() -> None:
        try:
            barrier.wait(timeout=JOIN_TIMEOUT)
            results.append(_upload(settings, test_patient, data))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=upload) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT)
    assert not errors and len(results) == 4
    assert len({r.document.id for r in results}) == 1
    assert sum(not r.duplicate for r in results) == 1  # exactly one call created the record
    assert sum(r.original_stored for r in results) == 1  # exactly one call created the file
    assert len(_documents(sql, test_patient)) == 1
    folder = settings.uploads_dir / str(test_patient)
    assert [p.name for p in folder.iterdir()] == [f"{sha256_hex(data)}.pdf"]  # no .part left


def test_unknown_patient_writes_nothing(settings, test_db_session_factory, sql) -> None:
    unknown = uuid.uuid4()
    with pytest.raises(PatientError) as caught:
        _upload(settings, unknown, pdfs.native_pdf(1))
    assert caught.value.kind == "patient_not_found"
    assert sql("SELECT count(*) FROM documents WHERE patient_id = :p", p=unknown) == [(0,)]
    assert not settings.data_dir.exists()


def test_invalid_pdf_writes_nothing(settings, test_patient, sql) -> None:
    with pytest.raises(DocumentValidationError):
        _upload(settings, test_patient, pdfs.user_password_pdf())
    assert _documents(sql, test_patient) == [] and not settings.data_dir.exists()


def test_database_failure_after_storing_keeps_the_file_and_retry_creates_the_record(
    settings, test_patient, sql, monkeypatch
) -> None:
    data = pdfs.native_pdf(1)
    real_create = svc.create_document
    failure = OperationalError("INSERT ...", {}, Exception("simulated connection loss"))

    def failing_create(*args, **kw):
        raise failure

    monkeypatch.setattr(svc, "create_document", failing_create)
    with pytest.raises(OperationalError) as caught:
        _upload(settings, test_patient, data)
    assert caught.value is failure
    assert _documents(sql, test_patient) == []
    assert _file(settings, test_patient, data).read_bytes() == data  # orphan, kept

    monkeypatch.setattr(svc, "create_document", real_create)
    retry = _upload(settings, test_patient, data)
    assert retry.duplicate is False and retry.original_stored is False
    assert [r[0] for r in _documents(sql, test_patient)] == [retry.document.id]


def test_missing_original_is_restored_for_the_existing_record(
    settings, test_patient, sql, caplog
) -> None:
    data = pdfs.native_pdf(2)
    first = _upload(settings, test_patient, data)
    path = _file(settings, test_patient, data)
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.unlink()
    caplog.set_level(logging.INFO)
    again = _upload(settings, test_patient, data, name=SECRET_NAME)
    assert again.document.id == first.document.id
    assert again.duplicate is True and again.original_stored is True
    assert path.read_bytes() == data and len(_documents(sql, test_patient)) == 1
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and str(first.document.id) in warnings[0]
    assert "Secret" not in caplog.text and str(settings.data_dir) not in caplog.text


class _FakeParser:
    @property
    def parser_info(self) -> ParserInfo:
        return ParserInfo(name="fake", version="1", options={})

    def parse(self, pdf: ValidatedPdf) -> ParseResult:
        pages = [
            ParsedPage(
                page_number=n,
                text=f"fictional page {n}",
                parse_method=ParseMethod.NATIVE,
                native_char_count=17,
            )
            for n in range(1, pdf.page_count + 1)
        ]
        document = ParsedDocument(
            sha256=sha256_hex(pdf.data),
            page_count=pdf.page_count,
            parser=self.parser_info,
            pages=pages,
        )
        return ParseResult(document=document, docling_json=b"{}", duration_ms=1)


def test_an_uploaded_document_can_be_processed_separately(settings, test_patient, sql) -> None:
    uploaded = _upload(settings, test_patient, pdfs.native_pdf(2))
    assert uploaded.document.processing_status == "uploaded"
    result = process_svc.process_document(
        settings, _FakeParser(), test_patient, uploaded.document.id
    )
    assert result.page_count == 2
    assert sql("SELECT count(*) FROM pages WHERE document_id = :d", d=uploaded.document.id) == [
        (2,)
    ]
