"""Unit tests for identity persistence and the indexing gate in the records layer (Task 2.8).

No database: validation must fail before any session exists, and stored-state rules run against a
fake session that answers the repository's queries in order and records every statement. The same
rules run against PostgreSQL in ``tests/integration/test_identity_integration.py``.
"""

import datetime as dt
import logging
import uuid
from types import SimpleNamespace

import pytest

from app.core.errors import DocumentProcessingError, PatientError
from app.core.logging import JsonFormatter
from app.records import chunks as chunk_repo
from app.records import repositories as repo
from app.records.chunks import ChunkRecord, IndexTarget
from app.records.models import ProcessingStatus, RunStatus

PATIENT_ID = uuid.uuid4()
DOCUMENT_ID = uuid.uuid4()
NAME = "Asha Fictional"


def _no_database():
    raise AssertionError("validation must fail before a database session is created")


class FakeSession:
    """``scalars()`` answers the lock (``one_or_none``) and page lists (``all``); ``scalar()``
    and ``execute()`` answer from queues. Every statement is recorded in order."""

    def __init__(self, document=None, answers=(), pages=(), rows=None):
        self.document = document
        self.answers = list(answers)
        self.pages = list(pages)
        self.rows = rows
        self.added: list = []
        self.statements: list = []
        self.committed = self.rolled_back = False

    def scalars(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(one_or_none=lambda: self.document, all=lambda: self.pages)

    def scalar(self, statement):
        self.statements.append(statement)
        return self.answers.pop(0)

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(one_or_none=lambda: self.rows)

    def add(self, obj) -> None:
        self.added.append(obj)

    def add_all(self, objs) -> None:
        self.added.extend(objs)

    def flush(self) -> None:
        for obj in self.added:
            obj.id = uuid.uuid4()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass


def _document(identity_check: str = "unknown") -> SimpleNamespace:
    return SimpleNamespace(
        id=DOCUMENT_ID,
        patient_id=PATIENT_ID,
        sha256="a" * 64,
        page_count=1,
        identity_check=identity_check,
        processing_status=ProcessingStatus.PROCESSING.value,
    )


def _scoped(session: FakeSession) -> None:
    for statement in session.statements:
        assert "patient_id" in str(statement) or "patients.id" in str(statement), str(statement)


def _record(session: FakeSession, **kw) -> repo.IdentityRecord:
    kw.setdefault("outcome", "match")
    kw.setdefault("reason_codes", ("name_match", "dob_not_printed"))
    kw.setdefault("rules_version", "1")
    kw.setdefault("duration_ms", 2)
    return repo.record_identity_check(PATIENT_ID, DOCUMENT_ID, factory=lambda: session, **kw)


# --- record_identity_check: validation ------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"outcome": "maybe"},
        {"outcome": "MATCH"},
        {"reason_codes": ()},
        {"reason_codes": "name_match"},
        {"reason_codes": ("Asha Fictional",)},  # never a name
        {"reason_codes": ("name_match", "name_match")},
        {"reason_codes": tuple(f"code_{c}" for c in "abcdefghi")},  # too many
        {"rules_version": ""},
        {"duration_ms": -1},
    ],
)
def test_invalid_input_is_rejected_before_the_database(change) -> None:
    kw = {"outcome": "match", "reason_codes": ("name_match",), "rules_version": "1"}
    kw |= {"duration_ms": 1} | change
    with pytest.raises(ValueError):
        repo.record_identity_check(PATIENT_ID, DOCUMENT_ID, factory=_no_database, **kw)


# --- record_identity_check: stored state ---------------------------------------------------------


@pytest.mark.parametrize("outcome", ["match", "unknown"])
def test_match_and_unknown_store_the_outcome_and_keep_the_status(outcome) -> None:
    session = FakeSession(_document(), [True, False])  # pages, no succeeded identity run
    record = _record(session, outcome=outcome)
    (run,) = session.added
    assert run.stage == repo.IDENTITY_STAGE == "identity" and run.status == RunStatus.SUCCEEDED
    assert run.component_versions == {
        repo.IDENTITY_VERSION_KEY: "1",
        repo.IDENTITY_REASONS_KEY: "dob_not_printed,name_match",
    }
    assert session.document.identity_check == outcome
    assert session.document.processing_status == ProcessingStatus.PROCESSING.value
    assert record.processing_status == "processing" and record.run_id == run.id
    assert "FOR UPDATE" in str(session.statements[0].compile())
    _scoped(session)


def test_a_mismatch_sets_needs_review_in_the_same_transaction() -> None:
    session = FakeSession(_document(), [True, False])
    record = _record(session, outcome="mismatch", reason_codes=("name_mismatch",))
    assert session.document.identity_check == "mismatch"
    assert session.document.processing_status == ProcessingStatus.NEEDS_REVIEW.value
    assert record.processing_status == "needs_review" and session.committed
    assert len(session.added) == 1  # one run, written with the status change


@pytest.mark.parametrize(
    ("answers", "kind"),
    [([False], repo.NOT_PARSED), ([True, True], repo.ALREADY_CHECKED)],
)
def test_refusals_write_nothing(answers, kind) -> None:
    session = FakeSession(_document(), answers)
    with pytest.raises(DocumentProcessingError) as caught:
        _record(session, outcome="mismatch")
    assert caught.value.kind == kind and session.added == []
    assert session.document.identity_check == "unknown" and session.rolled_back
    assert session.document.processing_status == ProcessingStatus.PROCESSING.value


def test_another_patients_document_is_not_found() -> None:
    session = FakeSession(None)
    with pytest.raises(DocumentProcessingError) as caught:
        _record(session)
    assert caught.value.kind == repo.DOCUMENT_NOT_FOUND
    _scoped(session)


def test_a_failure_is_a_failed_run_and_never_a_check() -> None:
    session = FakeSession(_document())
    run_id = repo.record_identity_failure(
        PATIENT_ID,
        DOCUMENT_ID,
        error_kind="invalid_input",
        error_class="ValueError",
        rules_version="1",
        duration_ms=1,
        factory=lambda: session,
    )
    (run,) = session.added
    assert run.id == run_id and run.status == RunStatus.FAILED.value
    assert run.stage == "identity" and run.error_kind == "invalid_input"
    assert session.document.identity_check == "unknown"


def test_identity_logs_hold_identifiers_and_outcomes_only(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _record(FakeSession(_document(), [True, False]), outcome="mismatch")
    formatted = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    for text in (caplog.text, formatted):
        assert str(DOCUMENT_ID) in text and "mismatch" in text and "Asha" not in text


# --- get_patient ---------------------------------------------------------------------------------


def test_get_patient_returns_one_scoped_profile_with_pii_hidden() -> None:
    row = SimpleNamespace(
        id=PATIENT_ID,
        display_name=NAME,
        date_of_birth=dt.date(1985, 3, 25),
        is_synthetic=True,
        created_at=dt.datetime(2026, 11, 17, tzinfo=dt.UTC),
    )
    session = FakeSession(rows=row)
    profile = repo.get_patient(PATIENT_ID, factory=lambda: session)
    assert profile.display_name == NAME and NAME not in repr(profile)
    assert "WHERE patients.id" in str(session.statements[0])


def test_get_patient_of_an_unknown_id_is_patient_not_found() -> None:
    with pytest.raises(PatientError) as caught:
        repo.get_patient(PATIENT_ID, factory=lambda: FakeSession(rows=None))
    assert caught.value.kind == repo.PATIENT_NOT_FOUND


# --- the indexing gate (ADR-036 A1) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("classified", "checked", "column", "refusal"),
    [
        (True, True, "match", None),
        (True, True, "unknown", None),  # unknown is allowed after a succeeded check
        (True, True, "mismatch", "identity_mismatch"),
        (True, False, "unknown", "identity_not_checked"),  # the column default alone is not enough
        (True, False, "match", "identity_not_checked"),
        (False, True, "match", "not_classified"),  # doc_type default without a classify run
        (False, False, "unknown", "identity_not_checked"),
        (False, True, "mismatch", "identity_mismatch"),
    ],
)
def test_the_gate_rule(classified, checked, column, refusal) -> None:
    target = IndexTarget(DOCUMENT_ID, "a" * 64, 1, 1, 0, classified, checked, column)
    assert target.blocked_reason == refusal
    assert refusal is None or refusal in chunk_repo.GATE_REFUSALS


def test_get_index_target_reports_the_succeeded_runs_and_the_column() -> None:
    session = FakeSession(_document("unknown"), [1, 0, True, False])  # pages, chunks, runs
    target = chunk_repo.get_index_target(PATIENT_ID, DOCUMENT_ID, factory=lambda: session)
    assert (target.classified, target.identity_checked, target.identity_check) == (
        True,
        False,
        "unknown",
    )
    assert target.blocked_reason == "identity_not_checked"
    _scoped(session)


def _chunk() -> ChunkRecord:
    return ChunkRecord(1, "text", None, "Haemoglobin 13.5 gm/dL", (0.1,) * 384)


def _insert(session: FakeSession) -> int:
    return chunk_repo.insert_document_chunks(
        PATIENT_ID,
        DOCUMENT_ID,
        [_chunk()],
        embedding_model="fake@0",
        component_versions={"chunker_version": "1"},
        duration_ms=1,
        factory=lambda: session,
    )


@pytest.mark.parametrize(
    ("answers", "column", "refusal"),
    [
        ([False, False, False], "unknown", "identity_not_checked"),
        ([False, False, True], "unknown", "not_classified"),
        ([False, True, True], "mismatch", "identity_mismatch"),
        ([False, True, False], "match", "identity_not_checked"),
    ],
)
def test_the_insert_rechecks_the_gate_under_the_lock_and_writes_nothing(
    answers, column, refusal
) -> None:
    """``answers``: no chunks yet, classify run, identity run (as seen under the row lock)."""
    session = FakeSession(_document(column), answers, pages=[1])
    with pytest.raises(DocumentProcessingError) as caught:
        _insert(session)
    assert caught.value.kind == refusal
    assert session.added == [] and session.rolled_back and not session.committed
    assert "FOR UPDATE" in str(session.statements[0].compile())  # the lock comes first
    gate_statements = [str(s) for s in session.statements[3:5]]
    assert all("pipeline_runs" in s for s in gate_statements)  # re-checked after the lock
    _scoped(session)


def test_the_insert_passes_the_gate_with_both_runs_and_unknown_identity() -> None:
    session = FakeSession(_document("unknown"), [False, True, True], pages=[1])
    assert _insert(session) == 1
    assert any(getattr(obj, "stage", None) == "index" for obj in session.added)
    assert session.committed


def test_gated_documents_stay_listed_as_unindexed() -> None:
    statement_text = []

    class ListingSession(FakeSession):
        def scalars(self, statement):
            statement_text.append(str(statement))
            return SimpleNamespace(all=lambda: [DOCUMENT_ID])

    session = ListingSession(answers=[True])
    assert chunk_repo.list_unindexed_documents(PATIENT_ID, factory=lambda: session) == (
        DOCUMENT_ID,
    )
    assert "pipeline_runs" not in statement_text[0]  # no gate filter: nothing is hidden
