"""Integration tests for classification persistence and the service on medimind_test (Task 2.7).

Only the guarded test database is used (``test_db_session_factory`` checks every connection is
``medimind_test``; ``main_database_unchanged`` guards the application database). All text is
fictional. These tests need PostgreSQL (Docker); without it they are skipped, not passed.
"""

import threading
import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.errors import DocumentProcessingError
from app.records import repositories as repo
from app.records.repositories import PageRecord
from app.services.classify_document import classify_document
from tests.integration._guards import assert_is_test_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

JOIN_TIMEOUT = 15
LAB_TEXT = (
    "Sunrise Diagnostics (Fictional)\nTest\tResult\tUnit\tReference Range\n"
    "Haemoglobin\t12.5\tgm/dL\t13.0 - 17.0\nTLC\t6336\t/cumm\t4000 - 11000\n"
    "FBS\t103\tmg/dL\t70 - 100\n"
)


@pytest.fixture
def sql(test_engine: Engine, app_settings: Settings) -> Callable[..., list]:
    def run(statement: str, **params: object) -> list:
        with test_engine.begin() as connection:
            assert_is_test_database(connection, app_settings.db_name)
            result = connection.execute(text(statement), params)
            return list(result) if result.returns_rows else []

    return run


def _document(patient: uuid.UUID, texts: tuple[str, ...] | None = (LAB_TEXT,)) -> uuid.UUID:
    """A document of this patient; parsed with ``texts`` unless ``texts`` is None."""
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    document = repo.create_document(
        patient,
        original_filename="fictional-report.pdf",
        storage_path=f"uploads/{patient}/{sha}.pdf",
        sha256=sha,
        size_bytes=10,
        page_count=len(texts) if texts else 1,
    ).document
    if texts is not None:
        claim = repo.claim_document_for_parsing(patient, document.id)
        pages = tuple(
            PageRecord(page_number=n, text=t, parse_method="native", has_tables=True)
            for n, t in enumerate(texts, start=1)
        )
        repo.persist_parsed_pages(patient, document.id, claim.run_id, pages)
    return document.id


def _classify(patient: uuid.UUID, document: uuid.UUID, **kw) -> repo.ClassificationRecord:
    kw.setdefault("doc_type", "lab_report")
    kw.setdefault("score", 0.769)
    kw.setdefault("rules_version", "2")
    kw.setdefault("duration_ms", 2)
    return repo.record_classification(patient, document, **kw)


def _runs(sql, document: uuid.UUID) -> list:
    return sql(
        "SELECT status, component_versions, error_kind FROM pipeline_runs "
        "WHERE document_id = :d AND stage = 'classify' ORDER BY started_at, id",
        d=document,
    )


def _state(sql, document: uuid.UUID) -> tuple:
    return sql(
        "SELECT doc_type, doc_type_confidence, processing_status FROM documents WHERE id = :d",
        d=document,
    )[0]


def test_a_classification_is_stored_with_its_run_and_keeps_the_status(test_patient, sql) -> None:
    document = _document(test_patient)
    record = _classify(test_patient, document)
    assert _state(sql, document) == ("lab_report", Decimal("0.769"), "processing")
    assert _runs(sql, document) == [("succeeded", {"classifier_rules_version": "2"}, None)]
    assert record.doc_type_confidence == Decimal("0.769")


def test_a_second_classification_is_refused_and_changes_nothing(test_patient, sql) -> None:
    document = _document(test_patient)
    _classify(test_patient, document)
    with pytest.raises(DocumentProcessingError) as caught:
        _classify(test_patient, document, doc_type="other", score=0.5)
    assert caught.value.kind == repo.ALREADY_CLASSIFIED
    assert _state(sql, document)[:2] == ("lab_report", Decimal("0.769"))
    assert len(_runs(sql, document)) == 1


def test_an_unparsed_document_is_not_parsed_and_gets_no_run(test_patient, sql) -> None:
    document = _document(test_patient, texts=None)
    with pytest.raises(DocumentProcessingError) as caught:
        _classify(test_patient, document)
    assert caught.value.kind == repo.NOT_PARSED and _runs(sql, document) == []
    assert repo.list_document_pages(test_patient, document) == ()


def test_another_patients_document_is_not_found(test_patient, second_patient, sql) -> None:
    document = _document(test_patient)
    calls = (
        lambda: repo.list_document_pages(second_patient, document),
        lambda: _classify(second_patient, document),
        lambda: repo.record_classification_failure(
            second_patient, document, error_kind="invalid_pages", rules_version="2", duration_ms=1
        ),
        lambda: classify_document(second_patient, document),
    )
    for call in calls:
        with pytest.raises(DocumentProcessingError) as caught:
            call()
        assert caught.value.kind == repo.DOCUMENT_NOT_FOUND
    assert _state(sql, document)[0] == "unknown" and _runs(sql, document) == []


def test_a_failed_run_is_history_and_the_stage_can_be_retried(test_patient, sql) -> None:
    document = _document(test_patient)
    repo.record_classification_failure(
        test_patient,
        document,
        error_kind="invalid_pages",
        error_class="ValueError",
        rules_version="2",
        duration_ms=1,
    )
    assert _state(sql, document)[:2] == ("unknown", None)
    assert [row[0] for row in _runs(sql, document)] == ["failed"]
    _classify(test_patient, document)  # a failed run never blocks a retry
    assert [row[0] for row in _runs(sql, document)] == ["failed", "succeeded"]


def test_the_service_classifies_stored_pages_end_to_end(test_patient, sql) -> None:
    document = _document(test_patient, texts=(LAB_TEXT, "Page 2 of 2"))
    result = classify_document(test_patient, document)
    assert result.doc_type == "lab_report"
    assert _state(sql, document) == ("lab_report", Decimal(str(result.score)), "processing")
    assert repo.list_document_pages(test_patient, document)[0].text == LAB_TEXT  # unchanged


def test_concurrent_classifiers_have_exactly_one_winner(test_patient, sql) -> None:
    document = _document(test_patient)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def classify() -> None:
        barrier.wait()
        try:
            _classify(test_patient, document)
            outcomes.append("ok")
        except DocumentProcessingError as exc:
            outcomes.append(exc.kind)

    threads = [threading.Thread(target=classify) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT)
    assert sorted(outcomes) == ["already_classified", "ok"]
    assert [row[0] for row in _runs(sql, document)] == ["succeeded"]


def test_the_database_rejects_an_out_of_range_confidence(test_patient, sql) -> None:
    document = _document(test_patient)
    with pytest.raises(IntegrityError):
        sql("UPDATE documents SET doc_type_confidence = 1.5 WHERE id = :d", d=document)
    with pytest.raises(IntegrityError):
        sql("UPDATE documents SET doc_type = 'consultation_note' WHERE id = :d", d=document)
