"""Real-model tests for the BGE embedder and MiniLM reranker (opt-in: ``-m models``).

They run only after the owner has approved and configured the pinned revisions
(``MEDIMIND_EMBEDDING_MODEL_REVISION``, ``MEDIMIND_RERANKER_MODEL_REVISION``) and the models are in
the local Hugging Face cache. They never download (``local_files_only``); a missing model skips.
"""

import math
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import RetrievalError
from app.retrieval.chunking import chunk_stats
from app.retrieval.models import BgeEmbedder, MiniLmReranker

pytestmark = pytest.mark.models

REPO_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _models():
    settings = Settings(_env_file=REPO_ENV_FILE)
    try:
        embedder = BgeEmbedder(settings.embedding_model_revision)
        reranker = MiniLmReranker(settings.reranker_model_revision)
        embedder.count_tokens("probe")
        reranker.score("probe", ["probe"])
    except RetrievalError as exc:
        pytest.skip(f"approved models not available locally ({exc.kind})")
    return embedder, reranker


def test_embeddings_are_384_dimensional_normalized_and_deterministic() -> None:
    embedder, _ = _models()
    first = embedder.embed_passages(["Haemoglobin 13.5 gm/dL", "Total Cholesterol 192 mg/dL"])
    again = embedder.embed_passages(["Haemoglobin 13.5 gm/dL", "Total Cholesterol 192 mg/dL"])
    assert all(len(v) == 384 for v in first)
    assert all(math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-4) for v in first)
    assert first == again
    query = embedder.embed_query("What was my haemoglobin?")
    similarity = [sum(a * b for a, b in zip(query, v, strict=True)) for v in first]
    assert similarity[0] > similarity[1]


def test_reranker_prefers_the_relevant_passage() -> None:
    _, reranker = _models()
    scores = reranker.score(
        "What was my HbA1c?", ["Test: HbA1c | Result: 6.1 | Unit: %", "Patient Name: Fictional"]
    )
    assert scores[0] > scores[1]


def test_token_counter_works_for_the_chunk_limit_check() -> None:
    embedder, _ = _models()
    from app.retrieval.chunking import ChunkDraft

    drafts = [ChunkDraft(1, "text", None, "word " * 600), ChunkDraft(1, "text", None, "short")]
    assert chunk_stats(drafts, count_tokens=embedder.count_tokens).over_token_limit == 1
