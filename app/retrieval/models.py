"""Embedding and reranking models, run locally on CPU with ``transformers`` (ADR-002, ADR-035).

- Embedder: ``BAAI/bge-small-en-v1.5`` (384 dimensions, CLS pooling, L2-normalized). Queries get
  BGE's retrieval instruction; passages do not.
- Reranker: ``cross-encoder/ms-marco-MiniLM-L-6-v2`` (one relevance logit per query-passage pair).

Owner approval gate (ADR-035): a model is loaded only at an exact, owner-approved commit revision
(40 hex characters), and by default ONLY from the local Hugging Face cache
(``local_files_only=True``). Nothing here downloads unless ``allow_download=True`` is passed
explicitly. ``torch`` and ``transformers`` are imported inside functions, so importing MediMind
never loads them (``tests/test_architecture.py``).

The ``Embedder`` and ``Reranker`` protocols let tests and the evaluation use other
implementations (deterministic fakes) without any model.
"""

import math
import re
from collections.abc import Sequence
from typing import Any, Protocol

from app.core.errors import RetrievalError

EMBEDDING_MODEL_ID = "BAAI/bge-small-en-v1.5"
RERANKER_MODEL_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"
EMBEDDING_DIMENSIONS = 384  # chunks.embedding is vector(384) (migration 0003)
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
MAX_SEQUENCE_TOKENS = 512
DEFAULT_BATCH_SIZE = 32
_REVISION = re.compile(r"[0-9a-f]{40}")


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    def embed_passages(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...

    def embed_query(self, text: str) -> tuple[float, ...]: ...


class Reranker(Protocol):
    @property
    def model_name(self) -> str: ...

    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


def require_revision(revision: str | None, setting: str) -> str:
    """An owner-approved pinned commit, or ``revision_not_configured`` (never a branch name)."""
    if revision is None or not _REVISION.fullmatch(revision):
        raise RetrievalError(
            f"No approved pinned model revision is configured ({setting}); see ADR-035",
            kind="revision_not_configured",
        )
    return revision


def check_embedding(vector: Sequence[float]) -> tuple[float, ...]:
    """A finite 384-dimension vector, or ``invalid_embedding``."""
    values = tuple(float(v) for v in vector)
    if len(values) != EMBEDDING_DIMENSIONS or not all(math.isfinite(v) for v in values):
        raise RetrievalError(
            f"An embedding must have {EMBEDDING_DIMENSIONS} finite values",
            kind="invalid_embedding",
        )
    return values


def _model_not_available(model_id: str) -> RetrievalError:
    return RetrievalError(
        f"Model {model_id} at the approved revision is not in the local cache; download it "
        "only after owner approval (ADR-035)",
        kind="model_not_available",
    )


class _Loaded:
    """Tokenizer and model, loaded once on first use."""

    def __init__(self, model_id: str, revision: str, allow_download: bool, kind: str) -> None:
        self.model_id = model_id
        self.revision = revision
        self.allow_download = allow_download
        self.kind = kind  # "embedding" or "reranker"
        self.tokenizer: Any = None
        self.model: Any = None

    def get(self) -> tuple[Any, Any]:
        if self.model is None:
            self.tokenizer, self.model = self._load()
        return self.tokenizer, self.model

    def _load(self) -> tuple[Any, Any]:
        import transformers  # lazy: heavy

        model_class = (
            transformers.AutoModel
            if self.kind == "embedding"
            else transformers.AutoModelForSequenceClassification
        )
        options = {"revision": self.revision, "local_files_only": not self.allow_download}
        missing = False
        try:
            tokenizer = transformers.AutoTokenizer.from_pretrained(self.model_id, **options)
            model = model_class.from_pretrained(self.model_id, **options)
        except OSError:  # not cached (or not downloadable): no library message is kept
            missing = True
        if missing:
            raise _model_not_available(self.model_id)
        model.eval()
        return tokenizer, model


class BgeEmbedder:
    """``BAAI/bge-small-en-v1.5`` on CPU."""

    def __init__(
        self,
        revision: str | None,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        allow_download: bool = False,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._revision = require_revision(revision, "MEDIMIND_EMBEDDING_MODEL_REVISION")
        self._batch_size = batch_size
        self._loaded = _Loaded(EMBEDDING_MODEL_ID, self._revision, allow_download, "embedding")

    @property
    def model_name(self) -> str:
        return f"{EMBEDDING_MODEL_ID}@{self._revision}"

    def embed_passages(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return self._encode(list(texts))

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._encode([BGE_QUERY_INSTRUCTION + text])[0]

    def count_tokens(self, text: str) -> int:
        """Tokens including special tokens, without truncation (for the 512-token check)."""
        tokenizer, _ = self._loaded.get()
        return len(tokenizer(text, add_special_tokens=True)["input_ids"])

    def _encode(self, texts: list[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        import torch  # lazy: heavy

        tokenizer, model = self._loaded.get()
        vectors: list[tuple[float, ...]] = []
        with torch.inference_mode():
            for start in range(0, len(texts), self._batch_size):
                batch = texts[start : start + self._batch_size]
                encoded = tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=MAX_SEQUENCE_TOKENS,
                    return_tensors="pt",
                )
                cls = model(**encoded).last_hidden_state[:, 0]  # BGE uses the CLS vector
                normalized = torch.nn.functional.normalize(cls, p=2, dim=1)
                vectors.extend(check_embedding(row) for row in normalized.tolist())
        return vectors


class MiniLmReranker:
    """``cross-encoder/ms-marco-MiniLM-L-6-v2`` on CPU. Scores are raw logits (higher = better)."""

    def __init__(
        self,
        revision: str | None,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        allow_download: bool = False,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._revision = require_revision(revision, "MEDIMIND_RERANKER_MODEL_REVISION")
        self._batch_size = batch_size
        self._loaded = _Loaded(RERANKER_MODEL_ID, self._revision, allow_download, "reranker")

    @property
    def model_name(self) -> str:
        return f"{RERANKER_MODEL_ID}@{self._revision}"

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        import torch  # lazy: heavy

        tokenizer, model = self._loaded.get()
        scores: list[float] = []
        with torch.inference_mode():
            for start in range(0, len(passages), self._batch_size):
                batch = list(passages[start : start + self._batch_size])
                encoded = tokenizer(
                    [query] * len(batch),
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=MAX_SEQUENCE_TOKENS,
                    return_tensors="pt",
                )
                logits = model(**encoded).logits[:, 0]
                scores.extend(float(v) for v in logits.tolist())
        if not all(math.isfinite(s) for s in scores):
            raise RetrievalError(
                "The reranker returned a non-finite score", kind="invalid_embedding"
            )
        return scores
