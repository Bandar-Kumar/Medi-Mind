"""Unit tests for the ``classify_document`` service and ``python -m app classify`` (Task 2.7).

The repository functions are replaced by stand-ins; no database is used. All text is fictional.
"""

import logging
import uuid
from decimal import Decimal

import pytest

from app import main
from app.core.errors import DocumentProcessingError
from app.core.logging import JsonFormatter
from app.ingestion.classification import CLASSIFIER_RULES_VERSION
from app.records.repositories import ClassificationRecord, PageRecord
from app.services import classify_document as svc

PATIENT_ID = uuid.uuid4()
DOCUMENT_ID = uuid.uuid4()
LAB_TEXT = (
    "Sunrise Diagnostics (Fictional)\nPatient Name: Asha Fictional\n"
    "Test\tResult\tUnit\tReference Range\nHaemoglobin\t12.5\tgm/dL\t13.0 - 17.0\n"
    "TLC\t6336\t/cumm\t4000 - 11000\nFBS\t103\tmg/dL\t70 - 100\n"
)


class Repo:
    def __init__(self, pages=None) -> None:
        self.pages = (
            (PageRecord(page_number=1, text=LAB_TEXT, parse_method="native", has_tables=True),)
            if pages is None
            else pages
        )
        self.stored: list[dict] = []
        self.failures: list[dict] = []
        self.store_error: BaseException | None = None
        self.failure_error: BaseException | None = None

    def list_pages(self, patient_id, document_id):
        assert (patient_id, document_id) == (PATIENT_ID, DOCUMENT_ID)
        return self.pages

    def record(self, patient_id, document_id, **kw):
        if self.store_error is not None:
            raise self.store_error
        self.stored.append(kw)
        return ClassificationRecord(
            document_id,
            uuid.uuid4(),
            kw["doc_type"],
            Decimal(str(kw["score"])),
            kw["rules_version"],
        )

    def record_failure(self, patient_id, document_id, **kw):
        if self.failure_error is not None:
            raise self.failure_error
        self.failures.append(kw)
        return uuid.uuid4()


@pytest.fixture
def repo(monkeypatch) -> Repo:
    fake = Repo()
    monkeypatch.setattr(svc, "list_document_pages", fake.list_pages)
    monkeypatch.setattr(svc, "record_classification", fake.record)
    monkeypatch.setattr(svc, "record_classification_failure", fake.record_failure)
    return fake


def test_a_parsed_document_is_classified_and_recorded(repo) -> None:
    result = svc.classify_document(PATIENT_ID, DOCUMENT_ID)
    assert result.doc_type == "lab_report" and result.rules_version == CLASSIFIER_RULES_VERSION
    (stored,) = repo.stored
    assert stored["doc_type"] == "lab_report" and stored["score"] == result.score
    assert stored["rules_version"] == CLASSIFIER_RULES_VERSION and stored["duration_ms"] >= 0
    assert repo.failures == []


def test_a_document_without_pages_is_blocked_not_failed(repo) -> None:
    repo.pages = ()
    with pytest.raises(DocumentProcessingError) as caught:
        svc.classify_document(PATIENT_ID, DOCUMENT_ID)
    assert caught.value.kind == svc.NOT_PARSED
    assert repo.stored == [] and repo.failures == []


@pytest.mark.parametrize("kind", ["already_classified", "not_parsed", "document_not_found"])
def test_expected_refusals_propagate_without_a_failed_run(repo, kind) -> None:
    repo.store_error = DocumentProcessingError("refused", kind=kind)
    with pytest.raises(DocumentProcessingError) as caught:
        svc.classify_document(PATIENT_ID, DOCUMENT_ID)
    assert caught.value.kind == kind and repo.failures == []


def test_malformed_pages_record_a_failed_run_and_reraise(repo) -> None:
    repo.pages = (PageRecord(page_number=1, text="x", parse_method="native", has_tables=False),) * 2
    with pytest.raises(ValueError):
        svc.classify_document(PATIENT_ID, DOCUMENT_ID)
    (failure,) = repo.failures
    assert failure["error_kind"] == svc.INVALID_PAGES and failure["error_class"] == "ValueError"
    assert failure["rules_version"] == CLASSIFIER_RULES_VERSION and repo.stored == []


def test_a_storage_error_records_a_failed_run_and_the_original_error_wins(repo) -> None:
    original = RuntimeError("boom")
    repo.store_error = original
    with pytest.raises(RuntimeError) as caught:
        svc.classify_document(PATIENT_ID, DOCUMENT_ID)
    assert caught.value is original
    assert repo.failures[0]["error_kind"] == svc.CLASSIFICATION_FAILED


def test_a_failure_while_recording_the_failure_never_masks_the_original(repo, caplog) -> None:
    original = RuntimeError("boom")
    repo.store_error, repo.failure_error = original, RuntimeError("database down")
    with pytest.raises(RuntimeError) as caught:
        svc.classify_document(PATIENT_ID, DOCUMENT_ID)
    assert caught.value is original
    assert "could not be recorded" in caplog.text and "database down" not in caplog.text


def test_an_interruption_is_recorded_and_reraised(repo) -> None:
    repo.store_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        svc.classify_document(PATIENT_ID, DOCUMENT_ID)
    assert repo.failures[0]["error_kind"] == svc.INTERRUPTED


def test_service_logs_never_contain_page_text(repo, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    svc.classify_document(PATIENT_ID, DOCUMENT_ID)
    formatted = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    for text in (caplog.text, formatted):
        assert str(DOCUMENT_ID) in text
        assert "Asha" not in text and "Haemoglobin" not in text


# --- CLI ----------------------------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch, isolated_environment):
    calls: list[tuple] = []

    def run(result=None, error=None):
        def fake(patient_id, document_id):
            calls.append((patient_id, document_id))
            if error is not None:
                raise error
            return result

        monkeypatch.setattr(svc, "classify_document", fake)
        argv = ["classify", "--patient", str(PATIENT_ID), "--document", str(DOCUMENT_ID)]
        return main.main(argv)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_cli_prints_the_result_without_text(cli, capsys) -> None:
    result = svc.ClassifyResult(
        DOCUMENT_ID, uuid.uuid4(), "lab_report", 0.769, ("lab.units",), "2", 3
    )
    assert cli(result=result) == main.EXIT_OK
    out = capsys.readouterr().out
    assert "lab_report" in out and "0.769" in out and "not a probability" in out
    assert "lab.units" in out and cli.calls == [(PATIENT_ID, DOCUMENT_ID)]


def test_cli_reports_already_classified_as_not_repeated(cli, capsys) -> None:
    error = DocumentProcessingError("x", kind=svc.ALREADY_CLASSIFIED)
    assert cli(error=error) == main.EXIT_OK
    assert "already classified; not repeated" in capsys.readouterr().out


def test_cli_fails_on_other_errors(cli) -> None:
    assert cli(error=DocumentProcessingError("x", kind="not_parsed")) == main.EXIT_FAILURE


def test_cli_requires_both_ids() -> None:
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["classify", "--patient", str(PATIENT_ID)])
