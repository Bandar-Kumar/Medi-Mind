"""Integration tests for the chunk repository and evidence search (Milestone R1) on medimind_test.

Only the guarded test database is used (``test_db_session_factory``: every connection is checked to
be ``medimind_test``; ``main_database_unchanged`` guards the application database). Models are
deterministic fakes: no model is loaded or downloaded. All text is fictional.
"""

import threading
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, text

from app.core.config import Settings
from app.core.errors import DocumentProcessingError
from app.ingestion.ir import ParsedDocument, ParsedPage, ParseMethod, ParserInfo
from app.ingestion.parsing import ParseResult
from app.ingestion.storage import sha256_hex
from app.ingestion.validation import ValidatedPdf
from app.records import chunks as chunk_repo
from app.records import repositories as repo
from app.records.chunks import ChunkRecord
from app.records.repositories import PageRecord
from app.retrieval.evidence import AnswerStatus, RetrievalConfig, RetrievalMode
from app.retrieval.query import query_terms
from app.services.check_identity import check_document_identity
from app.services.classify_document import classify_document
from app.services.index_document import index_document
from app.services.process_document import process_document
from app.services.search_evidence import search_evidence
from app.services.upload_document import upload_document
from tests.fixtures import pdfs
from tests.integration._guards import assert_is_test_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]

JOIN_TIMEOUT = 15


def _vector(position: int) -> tuple[float, ...]:
    values = [0.0] * 384
    values[position] = 1.0
    return tuple(values)


@pytest.fixture
def sql(test_engine: Engine, app_settings: Settings) -> Callable[..., list]:
    def run(statement: str, **params: object) -> list:
        with test_engine.begin() as connection:
            assert_is_test_database(connection, app_settings.db_name)
            result = connection.execute(text(statement), params)
            return list(result) if result.returns_rows else []

    return run


def _parsed(
    patient: uuid.UUID, texts: tuple[str, ...] = ("page one", "page two"), *, gated: bool = True
) -> uuid.UUID:
    """A parsed document; with ``gated`` also classified and identity-checked (``match``), so
    the indexing gate is open (Task 2.8, ADR-036 A1), as real ingestion does before indexing."""
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    document = repo.create_document(
        patient,
        original_filename="fictional-report.pdf",
        storage_path=f"uploads/{patient}/{sha}.pdf",
        sha256=sha,
        size_bytes=10,
        page_count=len(texts),
    ).document
    claim = repo.claim_document_for_parsing(patient, document.id)
    pages = tuple(
        PageRecord(page_number=n, text=t, parse_method="native", has_tables=False)
        for n, t in enumerate(texts, start=1)
    )
    repo.persist_parsed_pages(patient, document.id, claim.run_id, pages)
    if gated:
        repo.record_classification(
            patient, document.id, doc_type="lab_report", score=0.5, rules_version="2", duration_ms=1
        )
        repo.record_identity_check(
            patient,
            document.id,
            outcome="match",
            reason_codes=("name_match",),
            rules_version="1",
            duration_ms=1,
        )
    return document.id


def _records() -> list[ChunkRecord]:
    return [
        ChunkRecord(1, "text", None, "Haemoglobin 13.5 gm/dL", _vector(0)),
        ChunkRecord(
            2, "table_rows", "table 0, row 1", "Test: HbA1c | Result: 6.1 | Unit: %", _vector(1)
        ),
    ]


def _insert(patient: uuid.UUID, document: uuid.UUID, records=None) -> int:
    return chunk_repo.insert_document_chunks(
        patient,
        document,
        records if records is not None else _records(),
        embedding_model="fake@0",
        component_versions={"chunker_version": "1"},
        duration_ms=5,
    )


def _expect(kind: str, call: Callable[[], object]) -> None:
    with pytest.raises(DocumentProcessingError) as caught:
        call()
    assert caught.value.kind == kind


def _index_runs(sql, document: uuid.UUID) -> int:
    """Index-stage pipeline runs of the document, of any status."""
    return sql(
        "SELECT count(*) FROM pipeline_runs WHERE document_id = :d AND stage = 'index'",
        d=document,
    )[0][0]


def _not_found_message(
    operation: Callable[[uuid.UUID, uuid.UUID], object],
    patient: uuid.UUID,
    requested: uuid.UUID,
) -> str:
    """``document_not_found`` with no chained exception; the message with the id masked."""
    with pytest.raises(DocumentProcessingError) as caught:
        operation(patient, requested)
    assert caught.value.kind == repo.DOCUMENT_NOT_FOUND
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    return str(caught.value).replace(str(requested), "<id>")


def test_insert_stores_chunks_records_the_run_and_keeps_the_status(test_patient, sql) -> None:
    document = _parsed(test_patient)
    assert _insert(test_patient, document) == 2
    rows = sql(
        "SELECT chunk_index, page_number, chunk_type, content, embedding_model FROM chunks"
        " WHERE document_id = :d ORDER BY chunk_index",
        d=document,
    )
    assert [tuple(r) for r in rows] == [
        (0, 1, "text", "Haemoglobin 13.5 gm/dL", "fake@0"),
        (1, 2, "table_rows", "Test: HbA1c | Result: 6.1 | Unit: %", "fake@0"),
    ]
    runs = sql(
        "SELECT status, duration_ms, component_versions->>'chunker_version' FROM pipeline_runs"
        " WHERE document_id = :d AND stage = 'index'",
        d=document,
    )
    assert [tuple(r) for r in runs] == [("succeeded", 5, "1")]
    assert sql("SELECT processing_status FROM documents WHERE id = :d", d=document) == [
        ("processing",)
    ]
    target = chunk_repo.get_index_target(test_patient, document)
    assert (target.page_rows, target.chunk_count) == (2, 2)


def test_indexing_twice_or_unparsed_or_bad_pages_writes_nothing(test_patient, sql) -> None:
    document = _parsed(test_patient)
    _insert(test_patient, document)
    _expect("already_indexed", lambda: _insert(test_patient, document))
    unparsed = repo.create_document(
        test_patient,
        original_filename="x.pdf",
        storage_path=f"uploads/{test_patient}/{'c' * 64}.pdf",
        sha256="c" * 64,
        size_bytes=1,
        page_count=1,
    ).document.id
    _expect("not_parsed", lambda: _insert(test_patient, unparsed))
    other = _parsed(test_patient, ("only page",))
    bad = [ChunkRecord(3, "text", None, "no such page", _vector(0))]
    _expect("integrity_mismatch", lambda: _insert(test_patient, other, bad))
    assert sql(
        "SELECT count(*) FROM chunks WHERE document_id IN (:a, :b)", a=unparsed, b=other
    ) == [(0,)]


def test_concurrent_indexers_have_exactly_one_winner(test_patient, sql) -> None:
    document = _parsed(test_patient)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def index() -> None:
        barrier.wait()
        try:
            _insert(test_patient, document)
            outcomes.append("ok")
        except DocumentProcessingError as exc:
            outcomes.append(exc.kind)

    threads = [threading.Thread(target=index) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT)
    assert sorted(outcomes) == ["already_indexed", "ok"]
    assert sql("SELECT count(*) FROM chunks WHERE document_id = :d", d=document) == [(2,)]
    assert _index_runs(sql, document) == 1  # the losing indexer recorded no run


def test_another_patients_document_is_not_found_for_chunk_operations(
    test_patient, second_patient, sql
) -> None:
    """Patient B naming A's document behaves exactly like a missing id, and writes nothing."""
    mine = _parsed(test_patient)
    operations: dict[str, Callable[[uuid.UUID, uuid.UUID], object]] = {
        "insert_document_chunks": _insert,
        "get_index_target": chunk_repo.get_index_target,
    }
    for name, operation in operations.items():
        missing = uuid.uuid4()
        foreign = _not_found_message(operation, second_patient, mine)
        absent = _not_found_message(operation, test_patient, missing)
        assert foreign == absent, name

    assert sql("SELECT count(*) FROM chunks WHERE document_id = :d", d=mine) == [(0,)]
    assert _index_runs(sql, mine) == 0
    assert chunk_repo.list_unindexed_documents(second_patient) == ()
    assert chunk_repo.list_unindexed_documents(test_patient) == (mine,)
    # A can still index its own document afterwards: nothing was left locked or half-written.
    assert _insert(test_patient, mine) == 2


def test_failed_indexing_attempts_record_no_index_run(test_patient, sql) -> None:
    indexed = _parsed(test_patient)
    _insert(test_patient, indexed)
    _expect("already_indexed", lambda: _insert(test_patient, indexed))
    unparsed = repo.create_document(
        test_patient,
        original_filename="x.pdf",
        storage_path=f"uploads/{test_patient}/{'d' * 64}.pdf",
        sha256="d" * 64,
        size_bytes=1,
        page_count=1,
    ).document.id
    _expect("not_parsed", lambda: _insert(test_patient, unparsed))
    bad_page = _parsed(test_patient, ("only page",))
    _expect(
        "integrity_mismatch",
        lambda: _insert(test_patient, bad_page, [ChunkRecord(3, "text", None, "x", _vector(0))]),
    )
    assert _index_runs(sql, indexed) == 1  # only the first, successful attempt
    assert _index_runs(sql, unparsed) == 0
    assert _index_runs(sql, bad_page) == 0


class _InjectedFailure(RuntimeError):
    """Raised by the test after the chunk rows were sent to the database."""


def test_a_database_failure_mid_insert_rolls_back_chunks_and_the_run(
    test_patient, test_db_session_factory, sql
) -> None:
    """Rows already written inside the transaction disappear when the transaction fails."""
    document = _parsed(test_patient)
    factory, _ = test_db_session_factory
    written: list[tuple[int, int]] = []

    def fail_after_flush(session, _context) -> None:
        connection = session.connection()
        written.append(
            (
                connection.execute(
                    text("SELECT count(*) FROM chunks WHERE document_id = :d"), {"d": document}
                ).scalar_one(),
                connection.execute(
                    text(
                        "SELECT count(*) FROM pipeline_runs"
                        " WHERE document_id = :d AND stage = 'index'"
                    ),
                    {"d": document},
                ).scalar_one(),
            )
        )
        raise _InjectedFailure("injected after flush")

    event.listen(factory, "after_flush", fail_after_flush)
    try:
        with pytest.raises(_InjectedFailure):
            _insert(test_patient, document)
    finally:
        event.remove(factory, "after_flush", fail_after_flush)

    assert written == [(2, 1)]  # inside the transaction, both chunks and the run existed
    assert sql("SELECT count(*) FROM chunks WHERE document_id = :d", d=document) == [(0,)]
    assert _index_runs(sql, document) == 0
    assert chunk_repo.get_index_target(test_patient, document).chunk_count == 0
    assert _insert(test_patient, document) == 2  # the document is indexable afterwards


def test_search_is_scoped_even_when_another_patient_has_identical_chunks(
    test_patient, second_patient
) -> None:
    mine = _parsed(test_patient)
    theirs = _parsed(second_patient)
    _insert(test_patient, mine)
    _insert(second_patient, theirs)
    dense = chunk_repo.dense_search(test_patient, _vector(0), limit=10)
    lexical = chunk_repo.lexical_search(test_patient, ["hba1c", "haemoglobin"], limit=10)
    for hits in (dense, lexical):
        assert hits and {h.document_id for h in hits} == {mine}
        assert {h.patient_id for h in hits} == {test_patient}
    assert dense[0].content == "Haemoglobin 13.5 gm/dL" and dense[0].score == pytest.approx(1.0)
    assert chunk_repo.page_contexts(second_patient, [(mine, 1)]) == {}
    context = chunk_repo.page_contexts(test_patient, [(mine, 2)])[(mine, 2)]
    assert (context.page_number, context.parse_method) == (2, "native")


def test_lexical_search_keeps_codes_and_ors_terms(test_patient) -> None:
    document = _parsed(test_patient)
    _insert(test_patient, document)
    assert [h.page_number for h in chunk_repo.lexical_search(test_patient, ["hba1c"], limit=5)] == [
        2
    ]
    either = chunk_repo.lexical_search(test_patient, ["haemoglobin", "unrelated"], limit=5)
    assert [h.page_number for h in either] == [1]
    assert chunk_repo.lexical_search(test_patient, ["nothing"], limit=5) == ()


def test_lexical_search_finds_egfr_from_the_question(test_patient) -> None:
    document = _parsed(test_patient)
    records = [
        ChunkRecord(1, "text", None, "Haemoglobin 13.5 gm/dL", _vector(0)),
        ChunkRecord(
            2,
            "table_rows",
            "table 0, row 3",
            "Test: eGFR | Result: 92 | Unit: mL/min/1.73m2",
            _vector(1),
        ),
    ]
    _insert(test_patient, document, records)
    terms = query_terms("What was my eGFR?")
    assert terms == ("egfr",)
    for searched in (terms, ["eGFR"]):  # lower-cased by query_terms, or as written
        hits = chunk_repo.lexical_search(test_patient, searched, limit=5)
        assert [(h.document_id, h.page_number, h.section) for h in hits] == [
            (document, 2, "table 0, row 3")
        ]
        assert hits[0].content == "Test: eGFR | Result: 92 | Unit: mL/min/1.73m2"
        assert hits[0].score > 0


def test_dense_search_skips_chunks_without_embeddings(test_patient) -> None:
    without = _parsed(test_patient)
    with_embeddings = _parsed(test_patient)
    chunk_repo.insert_document_chunks(
        test_patient,
        without,
        [ChunkRecord(1, "text", None, "Haemoglobin 13.5 gm/dL", None)],
        embedding_model=None,
        component_versions={"chunker_version": "1"},
        duration_ms=5,
    )
    _insert(test_patient, with_embeddings)
    dense = chunk_repo.dense_search(test_patient, _vector(0), limit=10)
    assert {h.document_id for h in dense} == {with_embeddings}
    assert len(dense) == 2
    assert [h.score for h in dense] == sorted((h.score for h in dense), reverse=True)
    assert dense[0].page_number == 1 and dense[0].score == pytest.approx(1.0)
    # The chunk without an embedding is stored and still found lexically.
    lexical = chunk_repo.lexical_search(test_patient, ["haemoglobin"], limit=10)
    assert {h.document_id for h in lexical} == {without, with_embeddings}


def test_deleting_a_document_removes_its_chunks(test_patient, sql) -> None:
    document = _parsed(test_patient)
    _insert(test_patient, document)
    assert chunk_repo.list_unindexed_documents(test_patient) == ()
    repo.delete_document(test_patient, document)
    assert sql("SELECT count(*) FROM chunks WHERE document_id = :d", d=document) == [(0,)]


# --- end to end with fake models -----------------------------------------------------------------


class _FakeParser:
    @property
    def parser_info(self) -> ParserInfo:
        return ParserInfo(name="fake", version="1", options={})

    def parse(self, pdf: ValidatedPdf) -> ParseResult:
        pages = [
            ParsedPage(
                page_number=n,
                text="Haemoglobin 13.5 gm/dL" if n == 1 else "Total Cholesterol 192 mg/dL",
                parse_method=ParseMethod.NATIVE,
                native_char_count=20,
            )
            for n in range(1, pdf.page_count + 1)
        ]
        document = ParsedDocument(
            sha256=sha256_hex(pdf.data),
            page_count=pdf.page_count,
            parser=self.parser_info,
            pages=pages,
        )
        return ParseResult(document=document, docling_json=b"{}", duration_ms=1)


class _FakeEmbedder:
    model_name = "fake@0"

    def embed_passages(self, texts):
        return [_vector(0 if "Haemoglobin" in t else 1) for t in texts]

    def embed_query(self, text):
        return _vector(0 if "aemoglobin" in text else 1)


class _FakeReranker:
    model_name = "fake-rerank@0"

    def score(self, query, passages):
        return [5.0 if "Haemoglobin" in p else -5.0 for p in passages]


def test_upload_process_index_and_ask_end_to_end(tmp_path: Path, test_patient) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    uploaded = upload_document(
        settings, test_patient, pdfs.native_pdf(2), original_filename="report.pdf"
    )
    process_document(settings, _FakeParser(), test_patient, uploaded.document.id)
    # The indexing gate (ADR-036 A1): both stages run first, independently of each other.
    classify_document(test_patient, uploaded.document.id)
    identity = check_document_identity(test_patient, uploaded.document.id)
    assert identity.outcome == "unknown"  # the fake pages print no patient name
    result = index_document(settings, _FakeEmbedder(), test_patient, uploaded.document.id)
    assert result.chunks == 2
    answer = search_evidence(
        test_patient,
        "What was my haemoglobin?",
        config=RetrievalConfig(mode=RetrievalMode.HYBRID_RERANK, abstain_threshold=0.0),
        embedder=_FakeEmbedder(),
        reranker=_FakeReranker(),
    )
    assert answer.status is AnswerStatus.ANSWERED
    assert answer.passages[0].page_number == 1
    assert answer.passages[0].excerpt == "Haemoglobin 13.5 gm/dL"
    assert answer.passages[0].original_filename == "report.pdf"
    assert answer.passages[0].identity_warning == "identity not confirmed"  # label only
    refused = search_evidence(
        test_patient,
        "What was my eGFR?",
        config=RetrievalConfig(mode=RetrievalMode.LEXICAL, abstain_threshold=None),
    )
    assert refused.status is AnswerStatus.INSUFFICIENT_EVIDENCE
