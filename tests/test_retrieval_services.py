"""Unit tests for the index and search services with fakes (no database, no model)."""

import datetime as dt
import logging
import uuid
from types import SimpleNamespace

import pytest

from app.core.errors import DocumentProcessingError, RetrievalError
from app.ingestion.ir import ParsedDocument, ParsedPage, ParseMethod, ParserInfo
from app.records.chunks import ChunkHit, IndexTarget, PageContext
from app.retrieval.evidence import AnswerStatus, RetrievalConfig, RetrievalMode
from app.services import index_document as index_svc
from app.services import search_evidence as search_svc

PATIENT = uuid.uuid4()
OTHER = uuid.uuid4()
DOC = uuid.uuid4()
QUESTION = "What was my Haemoglobin in the report dated 05/06/2024?"


class FakeEmbedder:
    model_name = "fake-embedder@0"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_passages(self, texts):
        return [tuple([0.5] + [0.0] * 383) for _ in texts]

    def embed_query(self, text):
        self.queries.append(text)
        return tuple([1.0] + [0.0] * 383)


class FakeReranker:
    model_name = "fake-reranker@0"

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def score(self, query, passages):
        return [self.scores.get(p, 0.0) for p in passages]


def _hit(content: str, page: int, score: float, patient=PATIENT, document=DOC) -> ChunkHit:
    return ChunkHit(uuid.uuid4(), patient, document, page, "text", None, content, score)


@pytest.fixture
def records(monkeypatch):
    """Stub the records layer used by the search service."""
    state = SimpleNamespace(dense=(), lexical=(), calls=[], identity={})

    def dense(patient_id, vector, *, limit):
        state.calls.append(("dense", patient_id, limit))
        return state.dense

    def lexical(patient_id, terms, *, limit):
        state.calls.append(("lexical", patient_id, tuple(terms)))
        return state.lexical

    def contexts(patient_id, pages):
        return {
            key: PageContext(
                key[0],
                key[1],
                "Secret Fictional Report.pdf",
                dt.date(2024, 6, 5),
                "unknown",
                "ocr" if key[1] == 2 else "native",
                state.identity.get(key, "match"),
            )
            for key in pages
        }

    monkeypatch.setattr(search_svc, "dense_search", dense)
    monkeypatch.setattr(search_svc, "lexical_search", lexical)
    monkeypatch.setattr(search_svc, "page_contexts", contexts)
    return state


def test_hybrid_rerank_groups_by_page_and_cites_each_page_once(records) -> None:
    records.dense = (_hit("Haemoglobin 13.5", 1, 0.9), _hit("Platelets", 2, 0.8))
    records.lexical = (_hit("Hb row", 1, 0.5), _hit("Platelets", 2, 0.4))
    reranker = FakeReranker({"Haemoglobin 13.5": 8.0, "Hb row": 9.0, "Platelets": 1.0})
    answer = search_svc.search_evidence(
        PATIENT,
        QUESTION,
        config=RetrievalConfig(top_k=5),
        embedder=FakeEmbedder(),
        reranker=reranker,
    )
    assert answer.status is AnswerStatus.ANSWERED
    assert [(p.page_number, p.excerpt) for p in answer.passages] == [
        (1, "Hb row"),
        (2, "Platelets"),
    ]
    assert answer.top_score == 9.0 and answer.threshold_calibrated is False
    assert answer.passages[1].ocr_warning is not None and answer.passages[0].ocr_warning is None
    assert {c[1] for c in records.calls} == {PATIENT}  # every query was scoped to the patient


def test_identity_unknown_evidence_is_labelled_without_changing_ranking(records) -> None:
    """ADR-036 R1: the label is presentation only; scores and order are unchanged."""
    records.dense = (_hit("Haemoglobin 13.5", 1, 0.9), _hit("Platelets", 3, 0.8))
    records.lexical = (_hit("Hb row", 1, 0.5), _hit("Platelets", 3, 0.4))
    reranker = FakeReranker({"Haemoglobin 13.5": 8.0, "Hb row": 9.0, "Platelets": 1.0})

    def search():
        return search_svc.search_evidence(
            PATIENT,
            QUESTION,
            config=RetrievalConfig(top_k=5),
            embedder=FakeEmbedder(),
            reranker=reranker,
        )

    confirmed = search()
    records.identity = {(DOC, 3): "unknown"}
    labelled = search()
    assert [(p.page_number, p.score, p.chunk_id is not None) for p in labelled.passages] == [
        (p.page_number, p.score, True) for p in confirmed.passages
    ]
    assert labelled.top_score == confirmed.top_score
    by_page = {p.page_number: p for p in labelled.passages}
    assert by_page[3].identity_warning == "identity not confirmed"
    assert by_page[1].identity_warning is None
    assert all(p.identity_warning is None for p in confirmed.passages)


def test_a_calibrated_threshold_abstains_below_it(records) -> None:
    records.dense = (_hit("weak", 1, 0.2),)
    config = RetrievalConfig(abstain_threshold=5.0)
    answer = search_svc.search_evidence(
        PATIENT,
        QUESTION,
        config=config,
        embedder=FakeEmbedder(),
        reranker=FakeReranker({"weak": 1.0}),
    )
    assert answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE and answer.passages == ()
    assert answer.message is not None


def test_nothing_found_abstains_even_without_calibration(records) -> None:
    answer = search_svc.search_evidence(
        PATIENT, QUESTION, config=RetrievalConfig(mode=RetrievalMode.LEXICAL)
    )
    assert answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE


def test_advice_is_refused_before_any_retrieval(records) -> None:
    embedder = FakeEmbedder()
    answer = search_svc.search_evidence(
        PATIENT,
        "Should I stop taking my tablets?",
        config=RetrievalConfig(),
        embedder=embedder,
        reranker=FakeReranker({}),
    )
    assert answer.status is AnswerStatus.OUT_OF_SCOPE and answer.passages == ()
    assert records.calls == [] and embedder.queries == []


def test_another_patients_hit_stops_the_search(records) -> None:
    records.lexical = (_hit("foreign", 1, 1.0, patient=OTHER),)
    with pytest.raises(RuntimeError):
        search_svc.search_evidence(
            PATIENT, QUESTION, config=RetrievalConfig(mode=RetrievalMode.LEXICAL)
        )


@pytest.mark.parametrize("mode", list(RetrievalMode))
def test_each_mode_uses_only_its_retrievers(records, mode) -> None:
    records.dense = (_hit("a", 1, 0.9),)
    records.lexical = (_hit("b", 2, 0.5),)
    answer = search_svc.search_evidence(
        PATIENT,
        QUESTION,
        config=RetrievalConfig(mode=mode),
        embedder=FakeEmbedder(),
        reranker=FakeReranker({"a": 1.0, "b": 2.0}),
    )
    used = {call[0] for call in records.calls}
    assert used == {"dense", "lexical"} - (
        {"lexical"} if mode is RetrievalMode.DENSE else set()
    ) - ({"dense"} if mode is RetrievalMode.LEXICAL else set())
    assert answer.status is AnswerStatus.ANSWERED


def test_missing_models_for_a_mode_are_refused() -> None:
    with pytest.raises(RetrievalError):
        search_svc.search_evidence(
            PATIENT, QUESTION, config=RetrievalConfig(mode=RetrievalMode.DENSE)
        )
    with pytest.raises(RetrievalError):
        search_svc.search_evidence(
            PATIENT, QUESTION, config=RetrievalConfig(), embedder=FakeEmbedder()
        )


def test_search_logs_never_contain_the_question_or_excerpts(records, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    records.lexical = (_hit("Secret excerpt 13.5", 1, 1.0),)
    search_svc.search_evidence(
        PATIENT, QUESTION, config=RetrievalConfig(mode=RetrievalMode.LEXICAL)
    )
    assert str(PATIENT) in caplog.text
    assert "Haemoglobin" not in caplog.text and "Secret" not in caplog.text


# --- index service -------------------------------------------------------------------------------


def _ir() -> ParsedDocument:
    return ParsedDocument(
        sha256="b" * 64,
        page_count=1,
        parser=ParserInfo(name="fake", version="1"),
        pages=(
            ParsedPage(
                page_number=1,
                text="Haemoglobin 13.5 gm/dL",
                parse_method=ParseMethod.NATIVE,
                native_char_count=20,
            ),
        ),
    )


def _target(
    page_rows: int = 1,
    chunks: int = 0,
    *,
    classified: bool = True,
    identity_checked: bool = True,
    identity_check: str = "match",
) -> IndexTarget:
    return IndexTarget(
        DOC, "b" * 64, 1, page_rows, chunks, classified, identity_checked, identity_check
    )


@pytest.fixture
def index_stubs(monkeypatch):
    state = SimpleNamespace(target=_target(), inserted=None, belongs=True, manifest=True)
    monkeypatch.setattr(index_svc, "get_index_target", lambda p, d: state.target)
    monkeypatch.setattr(
        index_svc,
        "read_published_manifest",
        lambda settings, **kw: (
            SimpleNamespace(parse_run_id=uuid.uuid4()) if state.manifest else None
        ),
    )
    monkeypatch.setattr(index_svc, "parse_run_belongs_to_document", lambda *a: state.belongs)
    monkeypatch.setattr(
        index_svc,
        "load_verified_parse_outputs",
        lambda settings, **kw: SimpleNamespace(document=_ir()),
    )

    def insert(patient_id, document_id, records, **kw):
        state.inserted = (patient_id, document_id, list(records), kw)
        return len(records)

    monkeypatch.setattr(index_svc, "insert_document_chunks", insert)
    state.completed = []  # Phase 3: the shared completion rule runs after a successful insert
    monkeypatch.setattr(
        index_svc, "complete_document_if_ready", lambda p, d: state.completed.append((p, d))
    )
    return state


def test_index_chunks_embeds_and_inserts_with_versions(index_stubs) -> None:
    result = index_svc.index_document(SimpleNamespace(), FakeEmbedder(), PATIENT, DOC)
    patient_id, document_id, records, kw = index_stubs.inserted
    assert (patient_id, document_id, result.chunks, result.text_chunks) == (PATIENT, DOC, 1, 1)
    assert records[0].content == "Haemoglobin 13.5 gm/dL" and len(records[0].embedding) == 384
    assert kw["embedding_model"] == "fake-embedder@0"
    assert kw["component_versions"]["chunker_version"] == "1"
    assert index_stubs.completed == [(PATIENT, DOC)]


@pytest.mark.parametrize(
    ("change", "kind"),
    [
        ({"target": _target(page_rows=0)}, "not_parsed"),
        ({"target": _target(chunks=3)}, "already_indexed"),
        ({"target": _target(identity_check="mismatch")}, "identity_mismatch"),
        ({"target": _target(identity_checked=False)}, "identity_not_checked"),
        ({"target": _target(classified=False)}, "not_classified"),
        # "unknown" column values without their succeeded runs are refused (ADR-036 A1)
        (
            {"target": _target(classified=False, identity_checked=False, identity_check="unknown")},
            "identity_not_checked",
        ),
        ({"manifest": False}, "integrity_mismatch"),
        ({"belongs": False}, "integrity_mismatch"),
    ],
)
def test_index_refuses_documents_it_cannot_index_safely(index_stubs, change, kind) -> None:
    for name, value in change.items():
        setattr(index_stubs, name, value)
    with pytest.raises(DocumentProcessingError) as caught:
        index_svc.index_document(SimpleNamespace(), FakeEmbedder(), PATIENT, DOC)
    assert caught.value.kind == kind and index_stubs.inserted is None
