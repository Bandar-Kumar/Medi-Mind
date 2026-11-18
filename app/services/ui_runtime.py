"""Runtime objects for the local UI, built from settings (Task 2.9b, ADR-036 R3).

The UI imports services only, so this module builds what it needs from the core, ingestion and
retrieval layers: the settings, logging, the Docling parser, the embedding and reranking models,
the retrieval configuration, and patient-scoped evidence search. No rule, gate or SQL lives here.

- ``make_parser`` and the model factories construct objects only; Docling and the models load
  on first use (a model is never downloaded, ADR-035).
- Without an approved embedding revision, ``make_embedder`` returns a stand-in that raises
  ``revision_not_configured`` when indexing needs it, so parse, classify and identity still run
  and the index stage reports the reason (``failed``); nothing is indexed without the model.
- ``search`` builds the configuration exactly as ``python -m app ask`` does and calls
  ``search_evidence``; R1 retrieval behaviour is unchanged. It returns stored evidence only:
  nothing is generated.
- ``ask`` (Phase 5, ADR-040) runs the copilot: the generator comes from settings (the extractive
  composer unless an LLM provider is configured) and document passages use
  ``copilot_retrieval_mode`` (or the best configured mode). Every answer is citation-validated.
"""

import uuid
from collections.abc import Sequence
from typing import Final

from app.core.config import Settings, get_settings
from app.core.errors import RetrievalError
from app.core.logging import configure_logging
from app.ingestion.parsing import DoclingParser
from app.retrieval.evidence import EvidenceAnswer, RetrievalConfig, RetrievalMode
from app.retrieval.models import BgeEmbedder, Embedder, MiniLmReranker, Reranker
from app.services import copilot
from app.services.document_pipeline import error_kind
from app.services.search_evidence import search_evidence

__all__ = [
    "COPILOT_NOTES",
    "RETRIEVAL_MODES",
    "ask",
    "copilot_mode",
    "make_generator",
    "EvidenceAnswer",
    "Settings",
    "available_modes",
    "error_kind",
    "load_settings",
    "make_embedder",
    "make_parser",
    "make_reranker",
    "retrieval_config",
    "search",
    "setup_logging",
]

RETRIEVAL_MODES: Final = tuple(mode.value for mode in RetrievalMode)
REVISION_NOT_CONFIGURED: Final = "revision_not_configured"
COPILOT_NOTES: Final = copilot.NOTES


def load_settings() -> Settings:
    """The validated settings. Raises ``ConfigurationError`` (names the setting, never a value)."""
    return get_settings()


def setup_logging(settings: Settings) -> None:
    configure_logging(settings.log_level, settings.log_format)


def make_parser(settings: Settings) -> DoclingParser:
    """The configured Docling parser (models load on the first parse)."""
    return DoclingParser.from_settings(settings)


class _UnconfiguredEmbedder:
    """Stands in when no embedding revision is approved: every use raises the fixed kind."""

    model_name = "not-configured"

    def _refuse(self) -> RetrievalError:
        return RetrievalError(
            "No approved pinned embedding model revision is configured; see ADR-035",
            kind=REVISION_NOT_CONFIGURED,
        )

    def embed_passages(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        raise self._refuse()

    def embed_query(self, text: str) -> tuple[float, ...]:
        raise self._refuse()


def make_embedder(settings: Settings) -> Embedder:
    """The pinned BGE embedder, or a stand-in that refuses with ``revision_not_configured``."""
    if settings.embedding_model_revision is None:
        return _UnconfiguredEmbedder()
    return BgeEmbedder(settings.embedding_model_revision)


def make_reranker(settings: Settings) -> Reranker | None:
    """The pinned MiniLM reranker, or ``None`` when its revision is not configured."""
    if settings.reranker_model_revision is None:
        return None
    return MiniLmReranker(settings.reranker_model_revision)


def available_modes(settings: Settings) -> tuple[str, ...]:
    """Retrieval modes whose models are configured; ``lexical`` needs none. Best first."""
    has_embedder = settings.embedding_model_revision is not None
    has_reranker = settings.reranker_model_revision is not None
    return tuple(
        mode.value
        for mode in (
            RetrievalMode.HYBRID_RERANK,
            RetrievalMode.HYBRID,
            RetrievalMode.DENSE,
            RetrievalMode.LEXICAL,
        )
        if (has_embedder or not mode.uses_dense) and (has_reranker or not mode.uses_reranker)
    )


def retrieval_config(settings: Settings, mode: str) -> RetrievalConfig:
    """The retrieval configuration for ``mode``, from settings (as ``python -m app ask``)."""
    return RetrievalConfig(
        mode=RetrievalMode(mode),
        candidates=settings.retrieval_candidates,
        rrf_k=settings.retrieval_rrf_k,
        rerank_top=settings.retrieval_rerank_top,
        top_k=settings.retrieval_top_k,
        abstain_threshold=settings.retrieval_abstain_threshold,
    )


def search(
    settings: Settings,
    patient_id: uuid.UUID,
    question: str,
    mode: str,
    *,
    embedder: Embedder | None,
    reranker: Reranker | None,
) -> EvidenceAnswer:
    """Page-cited stored evidence for one patient, or an abstention (no text is generated).

    Raises:
        RetrievalError: ``invalid_query``, or a model the mode needs is not available.
    """
    config = retrieval_config(settings, mode)
    return search_evidence(
        patient_id,
        question,
        config=config,
        embedder=embedder if config.mode.uses_dense else None,
        reranker=reranker if config.mode.uses_reranker else None,
    )


# --- copilot (Phase 5, ADR-040) ------------------------------------------------------------------


def make_generator(settings: Settings) -> object:
    """The configured answer generator: extractive (default) or an LLM adapter (lazy import).

    Raises:
        LlmError: ``not_configured`` or ``external_not_allowed`` for an invalid LLM setup.
    """
    from app.answering.generate import ExtractiveGenerator, LlmGenerator

    if settings.llm_provider == "none":
        return ExtractiveGenerator()
    from app.llm.openai_compatible import OpenAICompatibleProvider
    from app.llm.provider import LlmError

    if not settings.llm_base_url or not settings.llm_model:
        raise LlmError(
            "MEDIMIND_LLM_BASE_URL and MEDIMIND_LLM_MODEL are required", kind="not_configured"
        )
    return LlmGenerator(
        OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            model_name=settings.llm_model,
            api_key=settings.llm_api_key,
            allow_external=settings.llm_allow_external,
            timeout_seconds=float(settings.llm_timeout_seconds),
        )
    )


def copilot_mode(settings: Settings) -> str | None:
    """The retrieval mode for copilot passages, or None when no mode can run."""
    modes = available_modes(settings)
    if settings.copilot_retrieval_mode is not None:
        return settings.copilot_retrieval_mode if settings.copilot_retrieval_mode in modes else None
    return modes[0] if modes else None


def ask(
    settings: Settings,
    patient_id: uuid.UUID,
    question: str,
    *,
    embedder: Embedder | None,
    reranker: Reranker | None,
    generator: object | None = None,
) -> copilot.CopilotAnswer:
    """A citation-validated copilot answer for one patient (or a fixed refusal / abstention)."""
    mode = copilot_mode(settings)
    retrieval = None
    if mode is not None:
        config = retrieval_config(settings, mode)
        retrieval = copilot.RetrievalDeps(
            config=config,
            embedder=embedder if config.mode.uses_dense else None,
            reranker=reranker if config.mode.uses_reranker else None,
        )
    return copilot.ask_copilot(
        patient_id,
        question,
        generator=generator or make_generator(settings),  # type: ignore[arg-type]
        retrieval=retrieval,
    )
