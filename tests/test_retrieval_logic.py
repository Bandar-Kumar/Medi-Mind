"""Unit tests for query terms, the guardrail, RRF, page grouping, abstention and the model gate."""

import sys
import uuid

import pytest

from app.core.errors import RetrievalError
from app.retrieval import models
from app.retrieval.evidence import (
    RetrievalConfig,
    RetrievalMode,
    ScoredChunk,
    best_per_page,
    calibrate_threshold,
    should_abstain,
)
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.query import QuestionGate, classify_question, query_terms
from evals.questions import ADVICE_QUESTIONS, INJECTION_QUESTIONS

# --- query terms ---------------------------------------------------------------------------------


def test_query_terms_drop_question_words_and_keep_codes_dates_and_decimals() -> None:
    terms = query_terms("What was my HbA1c in the report dated 05/06/2024? Was it 6.1?")
    assert terms == ("hba1c", "dated", "05/06/2024", "6.1")


def test_query_terms_keep_order_without_duplicates_and_respect_the_limit() -> None:
    assert query_terms("eGFR eGFR egfr creatinine") == ("egfr", "creatinine")
    assert len(query_terms(" ".join(f"word{n}" for n in range(40)))) == 24


def test_only_punctuation_or_stopwords_gives_no_terms() -> None:
    assert query_terms("what is my ?? -- ...") == ()


# --- guardrail -----------------------------------------------------------------------------------


@pytest.mark.parametrize("question", ADVICE_QUESTIONS)
def test_advice_questions_are_out_of_scope(question) -> None:
    assert classify_question(question) is QuestionGate.ADVICE


@pytest.mark.parametrize("question", INJECTION_QUESTIONS)
def test_injection_questions_are_out_of_scope(question) -> None:
    assert classify_question(question) is QuestionGate.INJECTION


@pytest.mark.parametrize(
    "question",
    [
        "What was my Haemoglobin in the report dated 05/06/2024?",
        "When was my TSH last tested?",
        "Which laboratory issued the report dated 12/03/2023?",
        "Show the Vitamin D (25-OH) result from 01/02/2024.",
        "What reference range does my report print for HDL?",
    ],
)
def test_factual_record_questions_are_allowed(question) -> None:
    assert classify_question(question) is QuestionGate.ALLOWED


def test_empty_or_overlong_questions_are_invalid() -> None:
    assert classify_question("   ") is QuestionGate.INVALID
    assert classify_question("x" * 501) is QuestionGate.INVALID


# --- fusion, grouping, abstention ----------------------------------------------------------------


def test_rrf_scores_by_position_and_breaks_ties_deterministically() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d"]], k=60)
    scores = dict(fused)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61)
    assert [item for item, _ in fused] == ["b", "a", "d", "c"]  # a 1/61 > d 1/62 > c 1/63
    assert reciprocal_rank_fusion([["x", "y"], ["y", "x"]], k=60)[0][0] == "x"  # tie -> by name


def test_rrf_counts_a_repeated_item_once_per_ranking_and_rejects_bad_k() -> None:
    assert dict(reciprocal_rank_fusion([["a", "a"]], k=1))["a"] == pytest.approx(0.5)
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], k=0)


def _chunk(document: uuid.UUID, page: int, score: float) -> ScoredChunk:
    return ScoredChunk(uuid.uuid4(), document, page, "text", None, "content", score)


def test_best_per_page_keeps_the_first_chunk_of_each_page() -> None:
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    ranked = [
        _chunk(doc_a, 1, 0.9),
        _chunk(doc_a, 1, 0.8),
        _chunk(doc_b, 1, 0.7),
        _chunk(doc_a, 2, 0.6),
    ]
    assert [(c.document_id, c.page_number, c.score) for c in best_per_page(ranked)] == [
        (doc_a, 1, 0.9),
        (doc_b, 1, 0.7),
        (doc_a, 2, 0.6),
    ]


def test_abstention_rules() -> None:
    assert should_abstain(None, None) is True  # nothing found
    assert should_abstain(-5.0, None) is False  # uncalibrated: never on score
    assert should_abstain(1.0, 2.0) is True
    assert should_abstain(2.0, 2.0) is False


def test_calibration_picks_the_best_f1_threshold_and_prefers_fewer_abstentions() -> None:
    result = calibrate_threshold([5.0, 6.0, 7.0], [1.0, 2.0])
    assert result is not None
    assert result.threshold == 5.0 and result.f1 == 1.0  # abstain below 5: both, and no answerable
    # Hand-computed: t=1.5 -> F1 0.667; t=2.0 -> 0.5; t=6.0 -> P 2/3, R 1, F1 0.8 (best).
    overlapping = calibrate_threshold([1.5, 6.0], [1.0, 2.0])
    assert overlapping is not None
    assert overlapping.threshold == 6.0 and overlapping.f1 == pytest.approx(0.8)
    assert calibrate_threshold([1.0], []) is None


def test_config_validation() -> None:
    assert RetrievalConfig().mode is RetrievalMode.HYBRID_RERANK
    with pytest.raises(ValueError):
        RetrievalConfig(top_k=0)
    with pytest.raises(ValueError):
        RetrievalConfig(abstain_threshold=float("nan"))
    assert RetrievalMode.LEXICAL.uses_dense is False and RetrievalMode.DENSE.uses_lexical is False
    assert RetrievalMode.HYBRID.uses_reranker is False


# --- model gate (no model is loaded or downloaded) -----------------------------------------------


@pytest.mark.parametrize("revision", [None, "main", "v1.5", "A" * 40, "a" * 39])
def test_models_refuse_anything_but_a_pinned_commit(revision) -> None:
    for model_class in (models.BgeEmbedder, models.MiniLmReranker):
        with pytest.raises(RetrievalError) as caught:
            model_class(revision)
        assert caught.value.kind == "revision_not_configured"


def test_constructing_a_model_loads_nothing_and_never_allows_downloads_by_default() -> None:
    embedder = models.BgeEmbedder("0" * 40)
    reranker = models.MiniLmReranker("1" * 40)
    assert embedder.model_name == f"{models.EMBEDDING_MODEL_ID}@{'0' * 40}"
    assert reranker.model_name == f"{models.RERANKER_MODEL_ID}@{'1' * 40}"
    assert embedder._loaded.allow_download is False and reranker._loaded.allow_download is False
    assert embedder._loaded.model is None  # lazy


def test_a_missing_cached_model_is_model_not_available(monkeypatch) -> None:
    class FakeTransformers:
        class AutoTokenizer:
            @staticmethod
            def from_pretrained(*args, **kwargs):
                assert kwargs["local_files_only"] is True
                raise OSError("not cached: /secret/path")

        AutoModel = AutoTokenizer
        AutoModelForSequenceClassification = AutoTokenizer

    monkeypatch.setitem(sys.modules, "transformers", FakeTransformers)
    with pytest.raises(RetrievalError) as caught:
        models.BgeEmbedder("0" * 40).count_tokens("text")
    assert caught.value.kind == "model_not_available"
    assert "/secret/path" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_embeddings_must_have_384_finite_values() -> None:
    assert len(models.check_embedding([0.0] * 384)) == 384
    for bad in ([0.0] * 383, [float("inf")] + [0.0] * 383):
        with pytest.raises(RetrievalError):
            models.check_embedding(bad)
