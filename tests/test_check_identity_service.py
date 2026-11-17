"""Unit tests for ``check_document_identity``, ``python -m app identity`` and bulk indexing
results (Task 2.8). Repository functions are replaced by stand-ins; no database is used.
All names are fictional.
"""

import argparse
import datetime as dt
import logging
import uuid

import pytest

from app import main
from app.core.errors import DocumentProcessingError, PatientError
from app.core.logging import JsonFormatter
from app.ingestion.identity import IDENTITY_RULES_VERSION
from app.records.repositories import IdentityRecord, PageRecord, PatientSummary
from app.services import check_identity as svc
from app.services import index_document as index_svc

PATIENT_ID = uuid.uuid4()
DOCUMENT_ID = uuid.uuid4()
NAME = "Asha Fictional"


class Repo:
    def __init__(self) -> None:
        self.pages = (
            PageRecord(
                page_number=1,
                text=f"Patient Name: {NAME}\nDOB: 25/03/1985",
                parse_method="native",
                has_tables=False,
            ),
        )
        self.profile_error: BaseException | None = None
        self.stored: list[dict] = []
        self.failures: list[dict] = []
        self.store_error: BaseException | None = None
        self.failure_error: BaseException | None = None

    def list_pages(self, patient_id, document_id):
        return self.pages

    def get_patient(self, patient_id):
        if self.profile_error is not None:
            raise self.profile_error
        return PatientSummary(
            PATIENT_ID, NAME, dt.date(1985, 3, 25), True, dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        )

    def record(self, patient_id, document_id, **kw):
        if self.store_error is not None:
            raise self.store_error
        self.stored.append(kw)
        status = "needs_review" if kw["outcome"] == "mismatch" else "processing"
        return IdentityRecord(
            document_id,
            uuid.uuid4(),
            kw["outcome"],
            tuple(sorted(kw["reason_codes"])),
            kw["rules_version"],
            status,
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
    monkeypatch.setattr(svc, "get_patient", fake.get_patient)
    monkeypatch.setattr(svc, "record_identity_check", fake.record)
    monkeypatch.setattr(svc, "record_identity_failure", fake.record_failure)
    return fake


def test_a_matching_document_is_recorded_with_fixed_codes(repo) -> None:
    result = svc.check_document_identity(PATIENT_ID, DOCUMENT_ID)
    assert result.outcome == "match" and result.processing_status == "processing"
    (stored,) = repo.stored
    assert stored["reason_codes"] == ("dob_match", "name_match")
    assert stored["rules_version"] == IDENTITY_RULES_VERSION and repo.failures == []


def test_a_mismatch_is_recorded_and_reports_needs_review(repo) -> None:
    repo.pages = (PageRecord(1, "Patient Name: Ravi Testwala", "native", False),)
    result = svc.check_document_identity(PATIENT_ID, DOCUMENT_ID)
    assert result.outcome == "mismatch" and result.processing_status == "needs_review"


def test_unparsed_documents_are_blocked_without_a_run(repo) -> None:
    repo.pages = ()
    with pytest.raises(DocumentProcessingError) as caught:
        svc.check_document_identity(PATIENT_ID, DOCUMENT_ID)
    assert caught.value.kind == "not_parsed" and repo.stored == repo.failures == []


@pytest.mark.parametrize("kind", ["already_checked", "not_parsed", "document_not_found"])
def test_expected_refusals_write_no_misleading_run(repo, kind) -> None:
    repo.store_error = DocumentProcessingError("refused", kind=kind)
    with pytest.raises(DocumentProcessingError):
        svc.check_document_identity(PATIENT_ID, DOCUMENT_ID)
    assert repo.failures == []


def test_an_unknown_patient_writes_no_run(repo) -> None:
    repo.profile_error = PatientError("no", kind="patient_not_found")
    with pytest.raises(PatientError):
        svc.check_document_identity(PATIENT_ID, DOCUMENT_ID)
    assert repo.stored == repo.failures == []


def test_malformed_pages_record_a_failed_run_and_reraise(repo) -> None:
    repo.pages = (PageRecord(1, "x", "native", False),) * 2  # duplicate page numbers
    with pytest.raises(ValueError):
        svc.check_document_identity(PATIENT_ID, DOCUMENT_ID)
    assert repo.failures[0]["error_kind"] == svc.INVALID_INPUT
    assert repo.failures[0]["rules_version"] == IDENTITY_RULES_VERSION


def test_an_unexpected_error_is_recorded_and_the_original_wins(repo, caplog) -> None:
    original = RuntimeError("boom")
    repo.store_error, repo.failure_error = original, RuntimeError("database down")
    with pytest.raises(RuntimeError) as caught:
        svc.check_document_identity(PATIENT_ID, DOCUMENT_ID)
    assert caught.value is original and "could not be recorded" in caplog.text


def test_an_interruption_is_recorded_and_reraised(repo) -> None:
    repo.store_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        svc.check_document_identity(PATIENT_ID, DOCUMENT_ID)
    assert repo.failures[0]["error_kind"] == svc.INTERRUPTED


def test_service_logs_never_contain_the_name_or_date(repo, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    svc.check_document_identity(PATIENT_ID, DOCUMENT_ID)
    formatted = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    for text in (caplog.text, formatted):
        assert str(DOCUMENT_ID) in text and "Asha" not in text and "1985" not in text


# --- CLI ----------------------------------------------------------------------------------------


def _cli(monkeypatch, result=None, error=None) -> int:
    def fake(patient_id, document_id):
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(svc, "check_document_identity", fake)
    return main.main(["identity", "--patient", str(PATIENT_ID), "--document", str(DOCUMENT_ID)])


@pytest.mark.parametrize(
    ("outcome", "status", "note"),
    [
        ("match", "processing", None),
        ("unknown", "processing", "Identity not confirmed"),
        ("mismatch", "needs_review", "will not be indexed"),
    ],
)
def test_cli_prints_the_outcome_and_codes_only(monkeypatch, capsys, outcome, status, note) -> None:
    result = svc.IdentityCheckResult(
        DOCUMENT_ID, uuid.uuid4(), outcome, ("dob_not_printed", "name_match"), "1", status, 2
    )
    assert _cli(monkeypatch, result=result) == main.EXIT_OK
    out = capsys.readouterr().out
    assert outcome in out and "dob_not_printed, name_match" in out and status in out
    assert note is None or note in out
    assert "Asha" not in out


def test_cli_reports_already_checked_as_not_repeated(monkeypatch, capsys) -> None:
    error = DocumentProcessingError("x", kind=svc.ALREADY_CHECKED)
    assert _cli(monkeypatch, error=error) == main.EXIT_OK
    assert "already identity-checked; not repeated" in capsys.readouterr().out


def test_cli_fails_on_other_errors(monkeypatch) -> None:
    error = DocumentProcessingError("x", kind="not_parsed")
    assert _cli(monkeypatch, error=error) == main.EXIT_FAILURE


# --- bulk indexing: one explicit result per document (ADR-036 A1) -------------------------------


def test_index_patient_returns_a_result_for_every_document_and_continues(monkeypatch) -> None:
    documents = [uuid.uuid4() for _ in range(5)]
    kinds = {
        documents[1]: "identity_mismatch",
        documents[2]: "identity_not_checked",
        documents[3]: "not_classified",
    }

    def index(settings, embedder, patient_id, document_id):
        if document_id in kinds:
            raise DocumentProcessingError("blocked", kind=kinds[document_id])
        return index_svc.IndexResult(document_id, 2, 1, 1, 5)

    monkeypatch.setattr(index_svc, "list_unindexed_documents", lambda patient_id: documents)
    monkeypatch.setattr(index_svc, "index_document", index)
    outcomes = index_svc.index_patient(object(), object(), PATIENT_ID)  # type: ignore[arg-type]
    assert [o.document_id for o in outcomes] == documents  # every document, in order
    assert [o.status for o in outcomes] == ["indexed", "blocked", "blocked", "blocked", "indexed"]
    assert [o.reason for o in outcomes[1:4]] == [
        "identity_mismatch",
        "identity_not_checked",
        "not_classified",
    ]
    assert outcomes[0].result is not None and outcomes[1].result is None


def test_index_patient_reports_a_lost_race_and_propagates_real_failures(monkeypatch) -> None:
    documents = [uuid.uuid4(), uuid.uuid4()]

    def index(settings, embedder, patient_id, document_id):
        kind = "already_indexed" if document_id == documents[0] else "integrity_mismatch"
        raise DocumentProcessingError("x", kind=kind)

    monkeypatch.setattr(index_svc, "list_unindexed_documents", lambda patient_id: documents)
    monkeypatch.setattr(index_svc, "index_document", index)
    with pytest.raises(DocumentProcessingError) as caught:
        index_svc.index_patient(object(), object(), PATIENT_ID)  # type: ignore[arg-type]
    assert caught.value.kind == "integrity_mismatch"  # a failure, not a block: never swallowed


def test_index_cli_prints_blocked_documents(monkeypatch, capsys) -> None:
    import app.retrieval.models as models

    blocked = uuid.uuid4()
    outcomes = (
        index_svc.IndexOutcome(blocked, index_svc.BLOCKED, reason="identity_mismatch"),
        index_svc.IndexOutcome(
            DOCUMENT_ID, index_svc.INDEXED, result=index_svc.IndexResult(DOCUMENT_ID, 3, 1, 2, 4)
        ),
    )
    monkeypatch.setattr(models, "BgeEmbedder", lambda revision: object())
    monkeypatch.setattr(index_svc, "index_patient", lambda s, e, p: outcomes)
    settings = argparse.Namespace(embedding_model_revision="a" * 40)
    code = main.run_index(settings, argparse.Namespace(patient=PATIENT_ID, document=None))  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert code == main.EXIT_OK
    assert f"Not indexed: document {blocked}: identity_mismatch" in out
    assert "1 document(s) indexed, 1 not indexed" in out
