"""Integration tests for ``process_document`` (Task 2.5d) with real PostgreSQL and a fake parser.

The service calls the repositories without a session factory; ``test_db_session_factory``
redirects them to ``medimind_test`` and checks every connection's database. Files go to a
temporary data directory. Docling itself is exercised in test_process_document_docling.py.
"""

import json
import logging
import os
import stat
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.errors import DocumentParseError, DocumentProcessingError, DocumentStorageError
from app.ingestion import parse_outputs as po
from app.ingestion.ir import ParsedDocument, ParsedPage, ParseMethod, ParserInfo
from app.ingestion.parsing import ParseResult
from app.ingestion.storage import StoredOriginal, store_original
from app.ingestion.validation import ValidatedPdf
from app.records import repositories as repo
from app.services import process_document as svc
from tests.fixtures import pdfs
from tests.integration._guards import assert_is_test_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

SECRET = "Haemoglobin 13.5 g/dL Test Person (fictional)"
PARSER_INFO = ParserInfo(
    name="docling", version="2.150.1", options={"ocr_engine": "rapidocr", "ocr_backend": "torch"}
)


class FakeParser:
    def __init__(self, fail: BaseException | None = None) -> None:
        self.fail = fail
        self.calls = 0

    @property
    def parser_info(self) -> ParserInfo:
        return PARSER_INFO

    def parse(self, pdf: ValidatedPdf) -> ParseResult:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        pages = [
            ParsedPage(
                page_number=n,
                text=f"{SECRET} page {n}",
                parse_method=ParseMethod.NATIVE,
                native_char_count=30,
            )
            for n in range(1, pdf.page_count + 1)
        ]
        document = ParsedDocument(
            sha256=pdf_sha(pdf), page_count=pdf.page_count, parser=PARSER_INFO, pages=pages
        )
        return ParseResult(document=document, docling_json=b'{"texts": []}', duration_ms=1)


def pdf_sha(pdf: ValidatedPdf) -> str:
    return po.sha256_hex(pdf.data)


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


@pytest.fixture
def stored_document(
    settings: Settings, test_patient: uuid.UUID
) -> tuple[StoredOriginal, uuid.UUID]:
    stored = store_original(settings, test_patient, pdfs.native_pdf(2))
    created = repo.create_document(
        test_patient,
        original_filename="fictional-report.pdf",
        storage_path=stored.storage_path,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        page_count=2,
    )
    return stored, created.document.id


def _runs(sql, document_id: uuid.UUID) -> list:
    return sql(
        "SELECT id, status, error_kind, error_class, component_versions FROM pipeline_runs "
        "WHERE document_id = :d ORDER BY started_at, id",
        d=document_id,
    )


def _pages(sql, document_id: uuid.UUID) -> list:
    return sql(
        "SELECT page_number, text, parse_method FROM pages WHERE document_id = :d "
        "ORDER BY page_number",
        d=document_id,
    )


def _status(sql, document_id: uuid.UUID) -> str:
    return sql("SELECT processing_status FROM documents WHERE id = :d", d=document_id)[0][0]


def test_fresh_document_is_parsed_and_persisted(
    settings, test_patient, stored_document, sql, test_db_session_factory
) -> None:
    stored, document_id = stored_document
    parser = FakeParser()
    result = svc.process_document(settings, parser, test_patient, document_id)
    assert parser.calls == 1 and result.page_count == 2 and result.reused_outputs is False
    assert result.producing_run_id == result.run_id
    assert _pages(sql, document_id) == [
        (1, f"{SECRET} page 1", "native"),
        (2, f"{SECRET} page 2", "native"),
    ]
    (run,) = _runs(sql, document_id)
    assert run[:4] == (result.run_id, "succeeded", None, None)
    assert run[4]["configured_parser"] == "docling"
    assert _status(sql, document_id) == "processing"  # parsing done; the pipeline is not
    outputs = po.load_verified_parse_outputs(
        settings,
        patient_id=test_patient,
        document_id=document_id,
        parse_run_id=result.run_id,
        source_sha256=stored.sha256,
        expected_page_count=2,
    )
    assert outputs is not None and [p.text for p in outputs.document.pages] == [
        row[1] for row in _pages(sql, document_id)
    ]
    _, log = test_db_session_factory
    assert set(log.databases) == {"medimind_test"}


def test_retry_after_a_failure_between_publish_and_pages_reuses_the_outputs(
    settings, test_patient, stored_document, sql, monkeypatch
) -> None:
    _, document_id = stored_document
    real_persist = svc.persist_ir_pages

    def crash_once(*args, **kwargs):
        monkeypatch.setattr(svc, "persist_ir_pages", real_persist)
        raise RuntimeError("simulated crash after publishing")

    monkeypatch.setattr(svc, "persist_ir_pages", crash_once)
    with pytest.raises(RuntimeError):
        svc.process_document(settings, FakeParser(), test_patient, document_id)
    (failed,) = _runs(sql, document_id)
    assert failed[1:4] == ("failed", "unexpected", "RuntimeError")
    assert _status(sql, document_id) == "failed" and _pages(sql, document_id) == []
    producer_versions = failed[4]

    spy = FakeParser(fail=AssertionError("Docling must not run on a retry with outputs"))
    result = svc.process_document(settings, spy, test_patient, document_id)
    assert spy.calls == 0
    assert result.reused_outputs is True and result.producing_run_id == failed[0]
    runs = _runs(sql, document_id)
    assert [r[1] for r in runs] == ["failed", "succeeded"]
    assert runs[0][4] == producer_versions  # the producer's provenance is untouched
    assert runs[1][0] == result.run_id
    assert set(runs[1][4]) == {
        "configured_parser",
        "configured_parser_version",
        "configured_ocr_engine",
        "configured_ocr_backend",
        "ir_schema_version",
        "manifest_version",
    }
    manifest = json.loads(
        (po.parse_output_dir(settings, test_patient, document_id) / "manifest.json").read_bytes()
    )
    assert manifest["parse_run_id"] == str(failed[0])
    assert len(_pages(sql, document_id)) == 2


def test_stale_worker_is_recovered_explicitly_and_cannot_write_afterwards(
    settings, test_patient, stored_document, sql
) -> None:
    _, document_id = stored_document
    old = repo.claim_document_for_parsing(test_patient, document_id, component_versions={})
    sql(
        "UPDATE pipeline_runs SET started_at = now() - make_interval(secs => 1201) WHERE id = :r",
        r=old.run_id,
    )
    with pytest.raises(DocumentProcessingError) as busy:
        svc.process_document(settings, FakeParser(), test_patient, document_id)
    assert busy.value.kind == "already_running"
    result = svc.process_document(
        settings, FakeParser(), test_patient, document_id, recover_stale=True
    )
    assert result.page_count == 2
    with pytest.raises(DocumentProcessingError) as lost:
        repo.persist_parsed_pages(
            test_patient,
            document_id,
            old.run_id,
            (repo.PageRecord(1, "x", "native", False), repo.PageRecord(2, "y", "native", False)),
        )
    assert lost.value.kind == "claim_lost"
    statuses = {r[0]: (r[1], r[2]) for r in _runs(sql, document_id)}
    assert statuses[old.run_id] == ("failed", "abandoned")
    assert statuses[result.run_id] == ("succeeded", None)


def test_a_failing_tx_c_reraises_the_original_and_leaves_the_claim_recoverable(
    settings, test_patient, stored_document, sql, monkeypatch
) -> None:
    _, document_id = stored_document
    original = DocumentParseError("Docling failed", kind="parser_failed")

    def database_down(*args, **kwargs):
        raise OperationalError("UPDATE pipeline_runs", {}, Exception("connection lost"))

    monkeypatch.setattr(svc, "record_parse_failure", database_down)
    with pytest.raises(DocumentParseError) as caught:
        svc.process_document(settings, FakeParser(fail=original), test_patient, document_id)
    assert caught.value is original
    (run,) = _runs(sql, document_id)
    assert run[1] == "running" and _status(sql, document_id) == "processing"


def test_a_changed_original_fails_before_parsing(
    settings, test_patient, stored_document, sql
) -> None:
    stored, document_id = stored_document
    os.chmod(stored.path, stat.S_IREAD | stat.S_IWRITE)
    stored.path.write_bytes(stored.path.read_bytes() + b"%")
    parser = FakeParser()
    with pytest.raises(DocumentStorageError) as caught:
        svc.process_document(settings, parser, test_patient, document_id)
    assert caught.value.kind == "integrity_mismatch" and parser.calls == 0
    (run,) = _runs(sql, document_id)
    assert run[1:4] == ("failed", "integrity_mismatch", "DocumentStorageError")
    assert _status(sql, document_id) == "failed"


def test_a_wrong_recorded_page_count_fails_before_parsing(
    settings, test_patient, stored_document, sql
) -> None:
    _, document_id = stored_document
    sql("UPDATE documents SET page_count = 3 WHERE id = :d", d=document_id)
    parser = FakeParser()
    with pytest.raises(DocumentProcessingError) as caught:
        svc.process_document(settings, parser, test_patient, document_id)
    assert caught.value.kind == "integrity_mismatch" and parser.calls == 0
    assert _pages(sql, document_id) == []


def test_errors_and_logs_are_sanitized(
    settings, test_patient, stored_document, sql, caplog
) -> None:
    stored, document_id = stored_document
    caplog.set_level(logging.DEBUG)
    svc.process_document(settings, FakeParser(), test_patient, document_id)
    with pytest.raises(DocumentProcessingError) as caught:
        svc.process_document(settings, FakeParser(), test_patient, document_id)
    assert caught.value.kind == "already_parsed"
    for forbidden in (
        "Haemoglobin",
        "Test Person",
        "fictional-report.pdf",
        str(settings.data_dir),
        stored.storage_path,
    ):
        assert forbidden not in caplog.text and forbidden not in str(caught.value)
