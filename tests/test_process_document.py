"""Unit tests for the parse orchestration (app/services/process_document.py).

No database and no Docling: repository calls are replaced by in-memory fakes and the parser by a
fake that builds IR from the validated PDF. Real files: the original is stored with
``store_original`` and the parse outputs use the real publish / verify code. Fictional data only.
"""

import json
import logging
import os
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.errors import (
    DocumentParseError,
    DocumentProcessingError,
    DocumentStorageError,
    DocumentValidationError,
)
from app.ingestion import parse_outputs as po
from app.ingestion.ir import ParsedDocument, ParsedPage, ParseMethod, ParserInfo
from app.ingestion.parsing import ParseResult
from app.ingestion.storage import StoredOriginal, store_original
from app.ingestion.validation import ValidatedPdf, validate_pdf_bytes
from app.records.repositories import DocumentRef, ParseClaim
from app.services import process_document as svc
from tests.fixtures import pdfs

SECRET = "Haemoglobin 13.5 g/dL Test Person (fictional)"
PARSER_INFO = ParserInfo(
    name="docling",
    version="2.150.1",
    options={"docling_core_version": "2.107.0", "ocr_engine": "rapidocr", "ocr_backend": "torch"},
)


class FakeParser:
    def __init__(self, stored: StoredOriginal, failure: Callable[[], None] | None = None) -> None:
        self.stored = stored
        self.failure = failure
        self.calls = 0

    @property
    def parser_info(self) -> ParserInfo:
        return PARSER_INFO

    def parse(self, pdf: ValidatedPdf) -> ParseResult:
        self.calls += 1
        if self.failure is not None:
            self.failure()
        pages = [
            ParsedPage(
                page_number=n,
                text=f"{SECRET} p{n}",
                parse_method=ParseMethod.NATIVE,
                native_char_count=30,
            )
            for n in range(1, pdf.page_count + 1)
        ]
        document = ParsedDocument(
            sha256=self.stored.sha256, page_count=pdf.page_count, parser=PARSER_INFO, pages=pages
        )
        return ParseResult(document=document, docling_json=b'{"texts": []}', duration_ms=1)


@dataclass
class FakeRecords:
    """In-memory stand-ins for the repository functions the service calls."""

    ref: DocumentRef
    belongs: bool = True
    claim_lost: bool = False
    record_raises: BaseException | None = None
    runs: list[uuid.UUID] = field(default_factory=list)
    versions: list[dict] = field(default_factory=list)
    recorded: list[tuple] = field(default_factory=list)
    persisted: list[tuple] = field(default_factory=list)
    belongs_checks: list[uuid.UUID] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(svc, "claim_document_for_parsing", self.claim)
        monkeypatch.setattr(svc, "persist_ir_pages", self.persist)
        monkeypatch.setattr(svc, "record_parse_failure", self.record)
        monkeypatch.setattr(svc, "parse_run_belongs_to_document", self.belongs_to)

    def claim(self, patient_id, document_id, *, component_versions, recover_stale):
        run = uuid.uuid4()
        self.runs.append(run)
        self.versions.append(dict(component_versions))
        return ParseClaim(run_id=run, document=self.ref)

    def persist(self, patient_id, document_id, run_id, document, *, expected_page_count):
        if self.claim_lost:
            raise DocumentProcessingError("Run lost the claim", kind="claim_lost")
        self.persisted.append((run_id, [p.text for p in document.pages]))
        return len(document.pages)

    def record(self, patient_id, document_id, run_id, *, error_kind, error_class):
        if self.record_raises is not None:
            raise self.record_raises
        self.recorded.append((run_id, error_kind, error_class))
        return not self.claim_lost

    def belongs_to(self, patient_id, document_id, run_id):
        self.belongs_checks.append(run_id)
        return self.belongs


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def patient() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def stored(settings: Settings, patient: uuid.UUID) -> StoredOriginal:
    return store_original(settings, patient, pdfs.native_pdf(2))


def _ref(stored: StoredOriginal, **overrides: object) -> DocumentRef:
    fields: dict[str, object] = {
        "id": uuid.uuid4(),
        "patient_id": uuid.uuid4(),
        "sha256": stored.sha256,
        "storage_path": stored.storage_path,
        "page_count": 2,
        "processing_status": "processing",
    } | overrides
    return DocumentRef(**fields)  # type: ignore[arg-type]


def _run(
    settings: Settings,
    patient: uuid.UUID,
    records: FakeRecords,
    parser: FakeParser,
    monkeypatch: pytest.MonkeyPatch,
) -> svc.ProcessResult:
    records.install(monkeypatch)
    return svc.process_document(settings, parser, patient, records.ref.id)


def _raise(exc: BaseException) -> Callable[[], None]:
    def fail() -> None:
        raise exc

    return fail


# --- success and reuse ---------------------------------------------------------------------------


def test_fresh_document_is_parsed_published_and_persisted(
    settings, patient, stored, monkeypatch
) -> None:
    records, parser = FakeRecords(_ref(stored)), FakeParser(stored)
    result = _run(settings, patient, records, parser, monkeypatch)
    assert result.page_count == 2 and result.reused_outputs is False
    assert result.run_id == result.producing_run_id == records.runs[0]
    assert parser.calls == 1 and records.recorded == []
    assert records.persisted == [(records.runs[0], [f"{SECRET} p1", f"{SECRET} p2"])]
    assert records.versions[0] == {
        "configured_parser": "docling",
        "configured_parser_version": "2.150.1",
        "ir_schema_version": "1",
        "manifest_version": "1",
        "configured_docling_core_version": "2.107.0",
        "configured_ocr_engine": "rapidocr",
        "configured_ocr_backend": "torch",
    }
    assert po.load_verified_parse_outputs(
        settings,
        patient_id=patient,
        document_id=records.ref.id,
        parse_run_id=records.runs[0],
        source_sha256=stored.sha256,
        expected_page_count=2,
    )


def test_retry_reuses_an_earlier_runs_outputs_without_parsing(
    settings, patient, stored, monkeypatch
) -> None:
    records = FakeRecords(_ref(stored))
    first = _run(settings, patient, records, FakeParser(stored), monkeypatch)
    parser = FakeParser(stored, failure=_raise(AssertionError("the parser must not run")))
    second = _run(settings, patient, records, parser, monkeypatch)
    assert parser.calls == 0
    assert second.reused_outputs is True and second.producing_run_id == first.run_id
    assert second.run_id == records.runs[1] != first.run_id
    assert records.belongs_checks == [first.run_id]
    manifest = po.read_published_manifest(
        settings, patient_id=patient, document_id=records.ref.id, source_sha256=stored.sha256
    )
    assert manifest is not None and manifest.parse_run_id == first.run_id  # never rewritten


def test_lost_publish_race_reuses_the_winners_outputs(
    settings, patient, stored, monkeypatch
) -> None:
    records = FakeRecords(_ref(stored))
    winner_run = uuid.uuid4()

    def publish_first() -> None:
        winner = FakeParser(stored).parse(
            validate_pdf_bytes(stored.path.read_bytes(), max_bytes=10**8, max_pages=50)
        )
        po.publish_parse_outputs(
            settings,
            patient_id=patient,
            document_id=records.ref.id,
            parse_run_id=winner_run,
            source_sha256=stored.sha256,
            expected_page_count=2,
            document=winner.document,
            docling_json=winner.docling_json,
        )

    result = _run(settings, patient, records, FakeParser(stored, publish_first), monkeypatch)
    assert result.reused_outputs is True and result.producing_run_id == winner_run
    assert records.belongs_checks == [winner_run]


# --- reuse refused -------------------------------------------------------------------------------


def _publish_once(settings, patient, stored, records, monkeypatch) -> uuid.UUID:
    first = _run(settings, patient, records, FakeParser(stored), monkeypatch)
    records.persisted.clear()
    return first.run_id


def test_producer_that_is_not_this_documents_run_is_refused(
    settings, patient, stored, monkeypatch
) -> None:
    records = FakeRecords(_ref(stored))
    _publish_once(settings, patient, stored, records, monkeypatch)
    records.belongs = False
    with pytest.raises(DocumentStorageError) as caught:
        _run(settings, patient, records, FakeParser(stored), monkeypatch)
    assert caught.value.kind == "integrity_mismatch"
    assert records.persisted == []
    assert records.recorded == [(records.runs[1], "integrity_mismatch", "DocumentStorageError")]


@pytest.mark.parametrize("field_name", ["document_id", "source_sha256"])
def test_manifest_for_another_document_or_source_fails_before_the_producer_check(
    settings, patient, stored, monkeypatch, field_name
) -> None:
    records = FakeRecords(_ref(stored))
    _publish_once(settings, patient, stored, records, monkeypatch)
    path = po.parse_output_dir(settings, patient, records.ref.id) / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest[field_name] = str(uuid.uuid4()) if field_name == "document_id" else "f" * 64
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    records.belongs_checks.clear()
    parser = FakeParser(stored)
    with pytest.raises(DocumentStorageError) as caught:
        _run(settings, patient, records, parser, monkeypatch)
    assert caught.value.kind == "integrity_mismatch"
    assert records.belongs_checks == [] and parser.calls == 0


# --- the original is verified before parsing -----------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "error", "kind"),
    [
        ({"page_count": 3}, DocumentProcessingError, "integrity_mismatch"),
        ({"page_count": None}, DocumentProcessingError, "integrity_mismatch"),
        ({"storage_path": "uploads/elsewhere.pdf"}, DocumentStorageError, "integrity_mismatch"),
    ],
)
def test_record_and_original_must_agree_before_parsing(
    settings, patient, stored, monkeypatch, overrides, error, kind
) -> None:
    records, parser = FakeRecords(_ref(stored, **overrides)), FakeParser(stored)
    with pytest.raises(error) as caught:
        _run(settings, patient, records, parser, monkeypatch)
    assert caught.value.kind == kind and parser.calls == 0
    assert records.recorded == [(records.runs[0], kind, error.__name__)]


def test_missing_original_is_write_failed_before_parsing(
    settings, patient, stored, monkeypatch
) -> None:
    other_sha = "a" * 64
    ref = _ref(stored, sha256=other_sha, storage_path=f"uploads/{patient}/{other_sha}.pdf")
    records, parser = FakeRecords(ref), FakeParser(stored)
    with pytest.raises(DocumentStorageError) as caught:
        _run(settings, patient, records, parser, monkeypatch)
    assert caught.value.kind == "write_failed" and parser.calls == 0
    assert str(settings.data_dir) not in str(caught.value)


def test_changed_original_is_refused_before_parsing(settings, patient, stored, monkeypatch) -> None:
    os.chmod(stored.path, stat.S_IREAD | stat.S_IWRITE)
    stored.path.write_bytes(stored.path.read_bytes() + b"%")
    records, parser = FakeRecords(_ref(stored)), FakeParser(stored)
    with pytest.raises(DocumentStorageError) as caught:
        _run(settings, patient, records, parser, monkeypatch)
    assert caught.value.kind == "integrity_mismatch" and parser.calls == 0


def test_original_is_verified_on_the_reuse_path_too(settings, patient, stored, monkeypatch) -> None:
    records = FakeRecords(_ref(stored))
    _publish_once(settings, patient, stored, records, monkeypatch)
    os.chmod(stored.path, stat.S_IREAD | stat.S_IWRITE)
    stored.path.unlink()
    with pytest.raises(DocumentStorageError) as caught:
        _run(settings, patient, records, FakeParser(stored), monkeypatch)
    assert caught.value.kind == "write_failed" and records.persisted == []


# --- failures, Tx C and exception safety ---------------------------------------------------------


def test_claim_errors_propagate_without_a_failure_record(
    settings, patient, stored, monkeypatch
) -> None:
    records = FakeRecords(_ref(stored))
    records.install(monkeypatch)

    def busy(*args, **kwargs):
        raise DocumentProcessingError("busy", kind="already_running")

    monkeypatch.setattr(svc, "claim_document_for_parsing", busy)
    with pytest.raises(DocumentProcessingError) as caught:
        svc.process_document(settings, FakeParser(stored), patient, records.ref.id)
    assert caught.value.kind == "already_running" and records.recorded == []


def test_claim_lost_at_persist_is_reraised_and_recording_is_attempted(
    settings, patient, stored, monkeypatch
) -> None:
    records = FakeRecords(_ref(stored), claim_lost=True)
    with pytest.raises(DocumentProcessingError) as caught:
        _run(settings, patient, records, FakeParser(stored), monkeypatch)
    assert caught.value.kind == "claim_lost"
    # The real repository writes nothing when the claim is lost (returns False).
    assert records.recorded == [(records.runs[0], "claim_lost", "DocumentProcessingError")]


@pytest.mark.parametrize(
    "tx_c_error",
    [
        RuntimeError("database down"),
        OperationalError("SELECT 1", {}, Exception("connection refused")),
        KeyboardInterrupt(),  # a second Ctrl+C while recording
    ],
)
def test_a_failing_tx_c_never_masks_the_original_exception(
    settings, patient, stored, monkeypatch, caplog, tx_c_error
) -> None:
    original = DocumentParseError("Docling failed", kind="parser_failed")
    records = FakeRecords(_ref(stored), record_raises=tx_c_error)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(DocumentParseError) as caught:
        _run(settings, patient, records, FakeParser(stored, _raise(original)), monkeypatch)
    assert caught.value is original
    assert original.__context__ is None and original.__cause__ is None
    assert "Parse failure could not be recorded" in caplog.text


def test_keyboard_interrupt_is_recorded_as_interrupted(
    settings, patient, stored, monkeypatch
) -> None:
    records = FakeRecords(_ref(stored))
    with pytest.raises(KeyboardInterrupt):
        _run(
            settings, patient, records, FakeParser(stored, _raise(KeyboardInterrupt())), monkeypatch
        )
    assert records.recorded == [(records.runs[0], "interrupted", "KeyboardInterrupt")]


@pytest.mark.parametrize("exit_exception", [SystemExit(3), GeneratorExit()])
def test_system_exit_and_generator_exit_skip_tx_c(
    settings, patient, stored, monkeypatch, exit_exception
) -> None:
    records = FakeRecords(_ref(stored))
    with pytest.raises(type(exit_exception)):
        _run(settings, patient, records, FakeParser(stored, _raise(exit_exception)), monkeypatch)
    assert records.recorded == []


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (KeyboardInterrupt(), "interrupted"),
        (DocumentParseError("x", kind="timeout"), "timeout"),
        (DocumentStorageError("x", kind="write_failed"), "write_failed"),
        (DocumentProcessingError("x", kind="text_not_storable"), "text_not_storable"),
        (DocumentValidationError("x", reason="encrypted"), "encrypted"),
        (OperationalError("SELECT 1", {}, Exception("boom")), "database_error"),
        (ValueError("Test Person"), "unexpected"),
        (DocumentParseError("x", kind="Not A Kind!"), "unexpected"),
    ],
)
def test_failure_kinds_are_short_sanitized_codes(exc: BaseException, kind: str) -> None:
    assert svc._failure_kind(exc) == kind
    assert svc._failure_class(exc) == type(exc).__name__


def test_logs_never_contain_text_paths_or_filenames(
    settings, patient, stored, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    records = FakeRecords(_ref(stored))
    _run(settings, patient, records, FakeParser(stored), monkeypatch)
    _run(settings, patient, records, FakeParser(stored), monkeypatch)  # reuse path
    failing = FakeRecords(_ref(stored, page_count=3))
    with pytest.raises(DocumentProcessingError):
        _run(settings, patient, failing, FakeParser(stored), monkeypatch)
    for forbidden in ("Haemoglobin", "Test Person", str(settings.data_dir), stored.storage_path):
        assert forbidden not in caplog.text
