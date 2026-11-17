"""Integration tests for identity verification and the indexing gate on medimind_test (Task 2.8).

Only the guarded test database is used (``test_db_session_factory``; ``main_database_unchanged``).
All names and text are fictional. These tests need PostgreSQL (Docker); without it they are
skipped, never passed.
"""

import threading
import time
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, text

from app.core.config import Settings
from app.core.errors import DocumentProcessingError
from app.records import chunks as chunk_repo
from app.records import repositories as repo
from app.records.chunks import ChunkRecord
from app.records.repositories import PageRecord
from app.services import index_document as index_svc
from app.services.check_identity import check_document_identity
from app.services.classify_document import classify_document
from tests.integration._guards import assert_is_test_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

JOIN_TIMEOUT = 15
NAME = "Asha Fictional"
PAGE = f"Patient Name: {NAME}\nHaemoglobin 13.5 gm/dL"


def _named(patient: uuid.UUID) -> uuid.UUID:
    """Give the fixture patient a plain two-token name (its default contains the label word
    "Patient", which ends a printed name field)."""
    repo.update_patient(patient, display_name=NAME)
    return patient


@pytest.fixture
def sql(test_engine: Engine, app_settings: Settings) -> Callable[..., list]:
    def run(statement: str, **params: object) -> list:
        with test_engine.begin() as connection:
            assert_is_test_database(connection, app_settings.db_name)
            result = connection.execute(text(statement), params)
            return list(result) if result.returns_rows else []

    return run


def _parsed(patient: uuid.UUID, page: str = PAGE) -> uuid.UUID:
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    document = repo.create_document(
        patient,
        original_filename="fictional.pdf",
        storage_path=f"uploads/{patient}/{sha}.pdf",
        sha256=sha,
        size_bytes=10,
        page_count=1,
    ).document
    claim = repo.claim_document_for_parsing(patient, document.id)
    repo.persist_parsed_pages(
        patient, document.id, claim.run_id, (PageRecord(1, page, "native", False),)
    )
    return document.id


def _identity(patient, document, outcome="match", codes=("name_match",)):
    return repo.record_identity_check(
        patient, document, outcome=outcome, reason_codes=codes, rules_version="1", duration_ms=1
    )


def _classify(patient, document, doc_type="unknown"):
    return repo.record_classification(
        patient, document, doc_type=doc_type, score=0, rules_version="2", duration_ms=1
    )


def _vector() -> tuple[float, ...]:
    return (1.0,) + (0.0,) * 383


def _insert(patient, document) -> int:
    return chunk_repo.insert_document_chunks(
        patient,
        document,
        [ChunkRecord(1, "text", None, "Haemoglobin 13.5 gm/dL", _vector())],
        embedding_model="fake@0",
        component_versions={"chunker_version": "1"},
        duration_ms=1,
    )


def _expect(kind: str, call: Callable[[], object]) -> None:
    with pytest.raises(DocumentProcessingError) as caught:
        call()
    assert caught.value.kind == kind


def _state(sql, document) -> tuple:
    return sql("SELECT identity_check, processing_status FROM documents WHERE id = :d", d=document)[
        0
    ]


def _chunks(sql, document) -> int:
    return sql("SELECT count(*) FROM chunks WHERE document_id = :d", d=document)[0][0]


# --- identity persistence ------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["match", "unknown"])
def test_match_and_unknown_keep_the_status(test_patient, sql, outcome) -> None:
    document = _parsed(test_patient)
    _identity(test_patient, document, outcome)
    assert _state(sql, document) == (outcome, "processing")


def test_a_mismatch_sets_needs_review_atomically_with_its_run(test_patient, sql) -> None:
    document = _parsed(test_patient)
    record = _identity(test_patient, document, "mismatch", ("name_mismatch", "dob_not_printed"))
    assert _state(sql, document) == ("mismatch", "needs_review")
    runs = sql(
        "SELECT status, component_versions FROM pipeline_runs "
        "WHERE document_id = :d AND stage = 'identity'",
        d=document,
    )
    assert runs == [
        (
            "succeeded",
            {
                "identity_rules_version": "1",
                "identity_reason_codes": "dob_not_printed,name_mismatch",
            },
        )
    ]
    assert record.processing_status == "needs_review"


def test_a_second_check_is_refused_and_a_failed_run_can_be_retried(test_patient, sql) -> None:
    document = _parsed(test_patient)
    repo.record_identity_failure(
        test_patient, document, error_kind="invalid_input", rules_version="1", duration_ms=1
    )
    assert _state(sql, document) == ("unknown", "processing")  # a failure changes nothing
    _identity(test_patient, document)  # retry after a failure
    _expect("already_checked", lambda: _identity(test_patient, document, "mismatch"))
    assert _state(sql, document) == ("match", "processing")


def test_another_patients_document_is_not_found(test_patient, second_patient, sql) -> None:
    document = _parsed(test_patient)
    for call in (
        lambda: _identity(second_patient, document, "mismatch"),
        lambda: repo.record_identity_failure(
            second_patient, document, error_kind="x", rules_version="1", duration_ms=1
        ),
        lambda: check_document_identity(second_patient, document),
    ):
        _expect("document_not_found", call)
    assert _state(sql, document) == ("unknown", "processing")


def test_the_service_compares_with_the_stored_profile(test_patient, sql) -> None:
    _named(test_patient)
    matching = _parsed(test_patient)
    assert check_document_identity(test_patient, matching).outcome == "match"
    other = _parsed(test_patient, "Patient Name: Ravi Testwala")
    assert check_document_identity(test_patient, other).outcome == "mismatch"
    assert _state(sql, other) == ("mismatch", "needs_review")


# --- the indexing gate ----------------------------------------------------------------------------


def test_the_gate_needs_both_succeeded_runs(test_patient, sql) -> None:
    document = _parsed(test_patient)
    _expect("identity_not_checked", lambda: _insert(test_patient, document))
    _classify(test_patient, document)  # doc_type stays "unknown", now as a recorded result
    _expect("identity_not_checked", lambda: _insert(test_patient, document))
    only_identity = _parsed(test_patient)
    _identity(test_patient, only_identity, "unknown")
    _expect("not_classified", lambda: _insert(test_patient, only_identity))
    _identity(test_patient, document, "unknown")
    assert _insert(test_patient, document) == 1  # unknown + unknown, both recorded: allowed
    assert _chunks(sql, only_identity) == 0


def test_a_mismatch_is_never_indexed(test_patient, sql) -> None:
    document = _parsed(test_patient)
    _classify(test_patient, document, "lab_report")
    _identity(test_patient, document, "mismatch", ("name_mismatch",))
    _expect("identity_mismatch", lambda: _insert(test_patient, document))
    assert _chunks(sql, document) == 0
    assert sql(
        "SELECT count(*) FROM pipeline_runs WHERE document_id = :d AND stage = 'index'",
        d=document,
    ) == [(0,)]
    assert document in chunk_repo.list_unindexed_documents(test_patient)  # listed, not hidden


def test_the_gate_is_rechecked_under_the_lock_before_the_insert(
    test_patient, sql, test_engine, app_settings
) -> None:
    """A mismatch committed while the insert waits for the row lock must be seen by the insert."""
    document = _parsed(test_patient)
    _classify(test_patient, document)
    outcome: list[str] = []
    holder = test_engine.connect()
    try:
        assert_is_test_database(holder, app_settings.db_name)
        transaction = holder.begin()
        holder.execute(text("SELECT id FROM documents WHERE id = :d FOR UPDATE"), {"d": document})

        def insert() -> None:
            try:
                _insert(test_patient, document)
                outcome.append("indexed")
            except DocumentProcessingError as exc:
                outcome.append(str(exc.kind))

        worker = threading.Thread(target=insert)
        worker.start()
        time.sleep(1.0)  # the insert is now waiting for the row lock
        holder.execute(
            text("UPDATE documents SET identity_check = 'mismatch' WHERE id = :d"), {"d": document}
        )
        holder.execute(
            text(
                "INSERT INTO pipeline_runs (patient_id, document_id, stage, status, finished_at) "
                "VALUES (:p, :d, 'identity', 'succeeded', now())"
            ),
            {"p": test_patient, "d": document},
        )
        transaction.commit()
        worker.join(JOIN_TIMEOUT)
    finally:
        holder.close()
    assert outcome == ["identity_mismatch"]  # read after the lock, not before it
    assert _chunks(sql, document) == 0


def test_index_document_rechecks_even_after_a_stale_pre_check(
    test_patient, sql, monkeypatch, tmp_path
) -> None:
    """The service pre-check is an optimisation; the insert's check under the lock decides."""
    document = _parsed(test_patient)
    _classify(test_patient, document, "lab_report")
    _identity(test_patient, document, "mismatch", ("name_mismatch",))
    real = chunk_repo.get_index_target(test_patient, document)
    stale = chunk_repo.IndexTarget(
        real.document_id, real.sha256, real.page_count, real.page_rows, 0, True, True, "match"
    )
    monkeypatch.setattr(index_svc, "get_index_target", lambda p, d: stale)
    monkeypatch.setattr(index_svc, "read_published_manifest", lambda *a, **k: _Manifest())
    monkeypatch.setattr(index_svc, "parse_run_belongs_to_document", lambda *a: True)
    monkeypatch.setattr(index_svc, "load_verified_parse_outputs", lambda *a, **k: _Outputs())
    with pytest.raises(DocumentProcessingError) as caught:
        index_svc.index_document(Settings(data_dir=tmp_path), _Embedder(), test_patient, document)
    assert caught.value.kind == "identity_mismatch" and _chunks(sql, document) == 0


def test_bulk_indexing_reports_every_document(test_patient, monkeypatch) -> None:
    open_gate = _parsed(test_patient)
    _classify(test_patient, open_gate)
    _identity(test_patient, open_gate, "unknown")
    mismatch = _parsed(test_patient)
    _classify(test_patient, mismatch)
    _identity(test_patient, mismatch, "mismatch", ("name_mismatch",))
    unchecked = _parsed(test_patient)

    def fake_index(settings, embedder, patient_id, document_id):
        target = chunk_repo.get_index_target(patient_id, document_id)
        if target.blocked_reason:
            raise DocumentProcessingError("blocked", kind=target.blocked_reason)
        _insert(patient_id, document_id)
        return index_svc.IndexResult(document_id, 1, 1, 0, 1)

    monkeypatch.setattr(index_svc, "index_document", fake_index)
    outcomes = {o.document_id: o for o in index_svc.index_patient(None, None, test_patient)}  # type: ignore[arg-type]
    assert set(outcomes) == {open_gate, mismatch, unchecked}
    assert outcomes[open_gate].status == "indexed"
    assert (outcomes[mismatch].status, outcomes[mismatch].reason) == (
        "blocked",
        "identity_mismatch",
    )
    assert outcomes[unchecked].reason == "identity_not_checked"


def test_classification_and_identity_run_independently(test_patient, sql) -> None:
    """ADR-036 R2: either stage succeeds while the other has not run (or failed)."""
    _named(test_patient)
    document = _parsed(test_patient)
    repo.record_classification_failure(
        test_patient, document, error_kind="invalid_pages", rules_version="2", duration_ms=1
    )
    assert check_document_identity(test_patient, document).outcome == "match"
    assert classify_document(test_patient, document).rules_version == "2"


class _Manifest:
    parse_run_id = uuid.uuid4()


class _Outputs:
    @property
    def document(self):
        from app.ingestion.ir import ParsedDocument, ParsedPage, ParseMethod, ParserInfo

        return ParsedDocument(
            sha256="0" * 64,
            page_count=1,
            parser=ParserInfo(name="fake", version="1", options={}),
            pages=[
                ParsedPage(
                    page_number=1,
                    text="Haemoglobin 13.5 gm/dL",
                    parse_method=ParseMethod.NATIVE,
                    native_char_count=20,
                )
            ],
        )


class _Embedder:
    model_name = "fake@0"

    def embed_passages(self, texts):
        return [_vector() for _ in texts]
