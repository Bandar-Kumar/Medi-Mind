"""Patient-isolation matrix for every patient-scoped repository operation (Task 1.8f, ADR-016).

Patients A and B each own a parsed document made from the same bytes. Every scoped operation is
called as B on A's document, and as A on a random id: each must behave exactly like "not found"
(same error kind, same message shape), and A's rows must be identical afterwards.

The composite foreign keys that make cross-patient child rows impossible in the database are
proven in ``test_schema_constraints.py`` and are not repeated here. Only ``medimind_test`` is used
(``test_db_session_factory``); ``main_database_unchanged`` guards the application database.
"""

import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, text

from app.core.config import Settings
from app.core.errors import DocumentProcessingError, PatientError
from app.records import repositories as repo
from app.records.repositories import PageRecord
from tests.integration._guards import assert_is_test_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

_SNAPSHOT_QUERIES = (
    "SELECT id, patient_id, original_filename, storage_path, sha256, size_bytes, page_count,"
    " processing_status, created_at, updated_at FROM documents WHERE patient_id = :p ORDER BY id",
    "SELECT id, document_id, page_number, text, parse_method FROM pages WHERE patient_id = :p"
    " ORDER BY id",
    "SELECT id, document_id, stage, status, error_kind FROM pipeline_runs WHERE patient_id = :p"
    " ORDER BY id",
    "SELECT id, display_name, date_of_birth, is_synthetic FROM patients WHERE id = :p",
)


@pytest.fixture
def sql(test_engine: Engine, app_settings: Settings) -> Callable[..., list]:
    def run(statement: str, **params: object) -> list:
        with test_engine.begin() as connection:
            assert_is_test_database(connection, app_settings.db_name)
            result = connection.execute(text(statement), params)
            return list(result) if result.returns_rows else []

    return run


def _snapshot(sql, patient: uuid.UUID) -> tuple:
    return tuple(tuple(sql(query, p=patient)) for query in _SNAPSHOT_QUERIES)


def _parsed_document(patient: uuid.UUID, sha: str) -> tuple[uuid.UUID, uuid.UUID]:
    document = repo.create_document(
        patient,
        original_filename="fictional-report.pdf",
        storage_path=f"uploads/{patient}/{sha}.pdf",
        sha256=sha,
        size_bytes=1234,
        page_count=1,
    ).document
    claim = repo.claim_document_for_parsing(patient, document.id)
    page = PageRecord(page_number=1, text="fictional", parse_method="native", has_tables=False)
    repo.persist_parsed_pages(patient, document.id, claim.run_id, (page,))
    return document.id, claim.run_id


def _not_found_message(
    operation: Callable[[uuid.UUID, uuid.UUID], object],
    patient: uuid.UUID,
    requested: uuid.UUID,
) -> str:
    with pytest.raises(DocumentProcessingError) as caught:
        operation(patient, requested)
    assert caught.value.kind == repo.DOCUMENT_NOT_FOUND
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    return str(caught.value).replace(str(requested), "<id>")


def _scoped_document_operations(
    run_id: uuid.UUID,
) -> dict[str, Callable[[uuid.UUID, uuid.UUID], object]]:
    """Every public document operation that raises ``document_not_found``, as (patient, doc)."""
    page = PageRecord(page_number=1, text="intruder", parse_method="native", has_tables=False)
    return {
        "get_document": repo.get_document,
        "rename_document": lambda p, d: repo.rename_document(p, d, "Intruder.pdf"),
        "delete_document": repo.delete_document,
        "claim_document_for_parsing": repo.claim_document_for_parsing,
        "persist_parsed_pages": lambda p, d: repo.persist_parsed_pages(p, d, run_id, (page,)),
        "record_parse_failure": lambda p, d: repo.record_parse_failure(
            p, d, run_id, error_kind="intrusion"
        ),
    }


def test_every_document_operation_treats_a_foreign_id_exactly_like_a_missing_one(
    test_patient, second_patient, sql
) -> None:
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    document, run_id = _parsed_document(test_patient, sha)
    _parsed_document(second_patient, sha)  # B owns the same bytes as its own document
    before = _snapshot(sql, test_patient)

    for name, operation in _scoped_document_operations(run_id).items():
        missing = uuid.uuid4()
        foreign = _not_found_message(operation, second_patient, document)
        absent = _not_found_message(operation, test_patient, missing)
        assert foreign == absent, name

    assert not repo.parse_run_belongs_to_document(second_patient, document, run_id)
    assert document not in {d.id for d in repo.list_documents(second_patient)}
    assert _snapshot(sql, test_patient) == before  # nothing of A was read into B or changed


def test_patient_operations_change_only_their_own_patient(
    test_patient, second_patient, sql
) -> None:
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    _parsed_document(test_patient, sha)
    before = _snapshot(sql, test_patient)

    repo.update_patient(second_patient, display_name="Changed Fictional B", date_of_birth=None)
    repo.delete_patient(second_patient)  # B owns nothing, so it is deletable; A is untouched
    assert repo.patient_exists(second_patient) is False
    assert test_patient in {p.id for p in repo.list_patients()}
    assert _snapshot(sql, test_patient) == before

    with pytest.raises(PatientError) as caught:  # A itself is not empty
        repo.delete_patient(test_patient)
    assert caught.value.kind == repo.PATIENT_NOT_EMPTY
    assert _snapshot(sql, test_patient) == before
