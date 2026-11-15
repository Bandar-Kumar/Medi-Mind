"""End-to-end: real Docling + real PostgreSQL (``medimind_test``) through ``process_document``.

Opt-in twice over: needs the database and Docling's cached models. Run offline (Windows CMD):

    set HF_HUB_OFFLINE=1
    uv run pytest -m "integration and docling" tests/integration/test_process_document_docling.py

All documents are the fictional synthetic fixtures.
"""

import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.core.config import Settings
from app.ingestion import parse_outputs as po
from app.ingestion.ir import ParseMethod, ReviewReason
from app.ingestion.parsing import DoclingParser, ParseResult, ParserOptions
from app.ingestion.storage import store_original
from app.ingestion.validation import ValidatedPdf
from app.records import repositories as repo
from app.services import process_document as svc
from tests.fixtures import pdfs
from tests.integration._guards import assert_is_test_database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docling,
    pytest.mark.usefixtures("main_database_unchanged"),
]

OPTIONS = ParserOptions(timeout_seconds=600, max_pages=20, max_bytes=20 * 1024 * 1024)


@pytest.fixture(scope="module")
def parser() -> DoclingParser:
    instance = DoclingParser(OPTIONS)
    instance.warm_up()
    return instance


class SpyParser:
    """Wraps the real parser and counts calls; can refuse to parse."""

    def __init__(self, real: DoclingParser, allow: bool = True) -> None:
        self.real, self.allow, self.calls = real, allow, 0

    @property
    def parser_info(self):
        return self.real.parser_info

    def parse(self, pdf: ValidatedPdf) -> ParseResult:
        self.calls += 1
        assert self.allow, "Docling must not run when verified outputs can be reused"
        return self.real.parse(pdf)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def sql(test_engine: Engine, app_settings: Settings) -> Callable[..., list]:
    def run(statement: str, **params: object) -> list:
        with test_engine.begin() as connection:
            assert_is_test_database(connection, app_settings.db_name)
            return list(connection.execute(text(statement), params))

    return run


def _upload(settings: Settings, patient: uuid.UUID, data: bytes, pages: int) -> uuid.UUID:
    stored = store_original(settings, patient, data)
    return repo.create_document(
        patient,
        original_filename="fictional-report.pdf",
        storage_path=stored.storage_path,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        page_count=pages,
    ).document.id


def _verified_ir(settings: Settings, patient: uuid.UUID, document_id: uuid.UUID, run_id, sha):
    outputs = po.load_verified_parse_outputs(
        settings,
        patient_id=patient,
        document_id=document_id,
        parse_run_id=run_id,
        source_sha256=sha,
        expected_page_count=1,
    )
    assert outputs is not None
    return outputs.document


@pytest.mark.parametrize(
    ("fixture", "method", "flags"),
    [
        (pdfs.lab_table_pdf, ParseMethod.NATIVE, {ReviewReason.DOCLING_NORMALIZED_TEXT}),
        (
            pdfs.scanned_pdf,
            ParseMethod.OCR,
            {ReviewReason.OCR_TEXT_UNVERIFIED, ReviewReason.LOW_OCR_CONFIDENCE},
        ),
    ],
    ids=["native", "scanned"],
)
def test_real_documents_are_parsed_into_exact_page_rows(
    parser, settings, test_patient, sql, test_db_session_factory, fixture, method, flags
) -> None:
    data = fixture()
    document_id = _upload(settings, test_patient, data, pages=1)
    result = svc.process_document(settings, parser, test_patient, document_id)
    assert result.page_count == 1 and result.reused_outputs is False
    ir = _verified_ir(settings, test_patient, document_id, result.run_id, po.sha256_hex(data))
    rows = sql(
        "SELECT page_number, text, parse_method, has_tables FROM pages WHERE document_id = :d",
        d=document_id,
    )
    page = ir.pages[0]
    assert rows == [(1, page.text, method.value, page.has_tables)]  # text byte for byte
    assert page.parse_method is method and set(page.review_flags) == flags
    run = sql("SELECT status FROM pipeline_runs WHERE id = :r", r=result.run_id)
    assert run == [("succeeded",)]
    _, log = test_db_session_factory
    assert set(log.databases) == {"medimind_test"}


def test_retry_reuses_real_outputs_without_running_docling_again(
    parser, settings, test_patient, sql, monkeypatch
) -> None:
    data = pdfs.lab_table_pdf()
    document_id = _upload(settings, test_patient, data, pages=1)
    real_persist = svc.persist_ir_pages

    def crash_once(*args, **kwargs):
        monkeypatch.setattr(svc, "persist_ir_pages", real_persist)
        raise RuntimeError("simulated crash after publishing")

    monkeypatch.setattr(svc, "persist_ir_pages", crash_once)
    first = SpyParser(parser)
    with pytest.raises(RuntimeError):
        svc.process_document(settings, first, test_patient, document_id)
    assert first.calls == 1
    refuse = SpyParser(parser, allow=False)
    result = svc.process_document(settings, refuse, test_patient, document_id)
    assert refuse.calls == 0 and result.reused_outputs is True
    ir = _verified_ir(
        settings, test_patient, document_id, result.producing_run_id, po.sha256_hex(data)
    )
    rows = sql("SELECT text FROM pages WHERE document_id = :d", d=document_id)
    assert rows == [(ir.pages[0].text,)]
