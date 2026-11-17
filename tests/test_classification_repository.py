"""Unit tests for classification persistence (Task 2.7, ADR-036). No database is used.

Input validation must fail before any session exists (``_no_database``). The stored-state rules
(lock, not parsed, already classified, the run row, status unchanged) run against a small fake
session that answers the repository's queries in order and records what would be written; every
statement is checked to filter by the patient. The same rules run against PostgreSQL in
``tests/integration/test_classification_integration.py``.
"""

import logging
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import DocumentProcessingError
from app.core.logging import JsonFormatter
from app.records import repositories as repo
from app.records.models import ProcessingStatus, RunStatus

PATIENT_ID = uuid.uuid4()
DOCUMENT_ID = uuid.uuid4()
SECRET = "Asha Fictional page text"


def _no_database():
    raise AssertionError("validation must fail before a database session is created")


class FakeSession:
    """Answers ``scalars().one_or_none()`` with the document and ``scalar()`` from a queue."""

    def __init__(self, document: object | None, answers: list[bool], rows: list | None = None):
        self.document = document
        self.answers = list(answers)
        self.rows = rows or []
        self.added: list = []
        self.statements: list = []
        self.committed = self.rolled_back = False

    def scalars(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(one_or_none=lambda: self.document)

    def scalar(self, statement):
        self.statements.append(statement)
        return self.answers.pop(0)

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(all=lambda: self.rows)

    def add(self, obj) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        for obj in self.added:
            obj.id = uuid.uuid4()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass


def _document() -> SimpleNamespace:
    return SimpleNamespace(
        id=DOCUMENT_ID,
        patient_id=PATIENT_ID,
        doc_type="unknown",
        doc_type_confidence=None,
        processing_status=ProcessingStatus.PROCESSING.value,
    )


def _record(session: FakeSession, **kw) -> repo.ClassificationRecord:
    kw.setdefault("doc_type", "lab_report")
    kw.setdefault("score", 0.769)
    kw.setdefault("rules_version", "2")
    kw.setdefault("duration_ms", 3)
    return repo.record_classification(PATIENT_ID, DOCUMENT_ID, factory=lambda: session, **kw)


def _scoped(session: FakeSession) -> None:
    """Every statement filters by this patient (no unscoped query)."""
    for statement in session.statements:
        assert "patient_id" in str(statement), str(statement)


# --- input validation, before any session ------------------------------------------------------


@pytest.mark.parametrize("doc_type", ["consultation_note", "diagnostic_report", "", "LAB_REPORT"])
def test_unknown_document_types_are_rejected(doc_type) -> None:
    with pytest.raises(ValueError):
        repo.record_classification(
            PATIENT_ID,
            DOCUMENT_ID,
            doc_type=doc_type,
            score=0.5,
            rules_version="2",
            duration_ms=1,
            factory=_no_database,
        )


@pytest.mark.parametrize(
    "score",
    [-0.001, 1.001, float("nan"), float("inf"), True, "0.5", None, 0.1234, Decimal("0.0005")],
)
def test_invalid_scores_are_rejected(score) -> None:
    with pytest.raises(ValueError):
        repo.record_classification(
            PATIENT_ID,
            DOCUMENT_ID,
            doc_type="other",
            score=score,
            rules_version="2",
            duration_ms=1,
            factory=_no_database,
        )


@pytest.mark.parametrize("version", ["", " 2", "2 ", "v" * 33, "2\n", 2, None])
def test_invalid_rules_versions_are_rejected(version) -> None:
    for call in (repo.record_classification, repo.record_classification_failure):
        kw = {"doc_type": "other", "score": 0.5} if call is repo.record_classification else {}
        if call is repo.record_classification_failure:
            kw["error_kind"] = "invalid_pages"
        with pytest.raises(ValueError):
            call(
                PATIENT_ID,
                DOCUMENT_ID,
                rules_version=version,
                duration_ms=1,
                factory=_no_database,
                **kw,
            )


@pytest.mark.parametrize("duration", [-1, True, 1.5, None])
def test_invalid_durations_are_rejected(duration) -> None:
    with pytest.raises(ValueError):
        repo.record_classification(
            PATIENT_ID,
            DOCUMENT_ID,
            doc_type="other",
            score=0.5,
            rules_version="2",
            duration_ms=duration,
            factory=_no_database,
        )


@pytest.mark.parametrize(
    ("kind", "error_class"),
    [("Bad Kind", None), ("x" * 65, None), ("ok_kind", "Not a class!"), ("", None)],
)
def test_failure_kinds_and_classes_must_be_plain_identifiers(kind, error_class) -> None:
    with pytest.raises(ValueError):
        repo.record_classification_failure(
            PATIENT_ID,
            DOCUMENT_ID,
            error_kind=kind,
            error_class=error_class,
            rules_version="2",
            duration_ms=1,
            factory=_no_database,
        )


@pytest.mark.parametrize("score", [0, 1, 0.5, 0.769, Decimal("0.250"), 1.0])
def test_valid_scores_are_stored_exactly(score) -> None:
    session = FakeSession(_document(), [True, False])  # has pages, no succeeded run
    record = _record(session, score=score)
    assert record.doc_type_confidence == Decimal(str(score)).quantize(Decimal("0.001"))
    assert session.document.doc_type_confidence == record.doc_type_confidence


# --- stored-state rules (fake session) ---------------------------------------------------------


def test_a_classification_stores_the_type_score_and_one_succeeded_run() -> None:
    session = FakeSession(_document(), [True, False])
    record = _record(session)
    (run,) = session.added
    assert run.stage == repo.CLASSIFY_STAGE == "classify"
    assert run.status == RunStatus.SUCCEEDED.value and run.duration_ms == 3
    assert run.component_versions == {repo.CLASSIFIER_VERSION_KEY: "2"}
    assert run.patient_id == PATIENT_ID and run.document_id == DOCUMENT_ID
    assert run.error_kind is None and run.error_class is None
    assert (session.document.doc_type, session.document.doc_type_confidence) == (
        "lab_report",
        Decimal("0.769"),
    )
    assert session.document.processing_status == ProcessingStatus.PROCESSING.value  # unchanged
    assert record == repo.ClassificationRecord(
        DOCUMENT_ID, run.id, "lab_report", Decimal("0.769"), "2"
    )
    assert session.committed
    _scoped(session)
    assert "FOR UPDATE" in str(session.statements[0].compile())  # the row lock comes first


def test_a_second_classification_is_refused_and_writes_nothing() -> None:
    session = FakeSession(_document(), [True, True])  # a succeeded run already exists
    with pytest.raises(DocumentProcessingError) as caught:
        _record(session)
    assert caught.value.kind == repo.ALREADY_CLASSIFIED
    assert session.added == [] and session.document.doc_type == "unknown"
    assert session.rolled_back and not session.committed


def test_a_document_without_pages_is_not_parsed_and_writes_nothing() -> None:
    session = FakeSession(_document(), [False])
    with pytest.raises(DocumentProcessingError) as caught:
        _record(session)
    assert caught.value.kind == repo.NOT_PARSED == "not_parsed"
    assert session.added == [] and session.rolled_back


def test_another_patients_document_is_not_found() -> None:
    for call in (
        lambda s: _record(s),
        lambda s: repo.record_classification_failure(
            PATIENT_ID,
            DOCUMENT_ID,
            error_kind="invalid_pages",
            rules_version="2",
            duration_ms=1,
            factory=lambda: s,
        ),
    ):
        session = FakeSession(None, [])  # the scoped lock finds no row
        with pytest.raises(DocumentProcessingError) as caught:
            call(session)
        assert caught.value.kind == repo.DOCUMENT_NOT_FOUND and session.added == []
        _scoped(session)


def test_a_failure_records_a_failed_run_and_leaves_the_document_alone() -> None:
    session = FakeSession(_document(), [])
    run_id = repo.record_classification_failure(
        PATIENT_ID,
        DOCUMENT_ID,
        error_kind="invalid_pages",
        error_class="ValueError",
        rules_version="2",
        duration_ms=4,
        factory=lambda: session,
    )
    (run,) = session.added
    assert run.id == run_id and run.status == RunStatus.FAILED.value
    assert run.status != RunStatus.SUCCEEDED.value
    assert (run.error_kind, run.error_class) == ("invalid_pages", "ValueError")
    assert session.document.doc_type == "unknown" and session.document.doc_type_confidence is None
    assert session.document.processing_status == ProcessingStatus.PROCESSING.value


def test_list_document_pages_returns_records_in_order_with_text_hidden() -> None:
    rows = [(1, SECRET, "native", True), (2, "", "ocr", False)]
    session = FakeSession(None, [True], rows)
    pages = repo.list_document_pages(PATIENT_ID, DOCUMENT_ID, factory=lambda: session)
    assert [p.page_number for p in pages] == [1, 2] and pages[0].text == SECRET
    assert SECRET not in repr(pages)
    assert "ORDER BY pages.page_number" in str(session.statements[1])
    _scoped(session)


def test_list_document_pages_of_an_unknown_document_is_not_found() -> None:
    session = FakeSession(None, [False])
    with pytest.raises(DocumentProcessingError) as caught:
        repo.list_document_pages(PATIENT_ID, DOCUMENT_ID, factory=lambda: session)
    assert caught.value.kind == repo.DOCUMENT_NOT_FOUND


def test_logs_hold_identifiers_and_kinds_only(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _record(FakeSession(_document(), [True, False]))
    repo.list_document_pages(
        PATIENT_ID,
        DOCUMENT_ID,
        factory=lambda: FakeSession(None, [True], [(1, SECRET, "native", False)]),
    )
    formatted = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    for text in (caplog.text, formatted):
        assert str(DOCUMENT_ID) in text and "lab_report" in text
        assert SECRET not in text and "Asha" not in text


def test_the_not_parsed_kind_matches_the_chunk_repository() -> None:
    from app.records.chunks import NOT_PARSED

    assert repo.NOT_PARSED == NOT_PARSED
