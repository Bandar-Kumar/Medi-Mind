"""Answer a question with page-cited evidence from one patient's documents (Milestone R1).

Flow (ARCHITECTURE §4.2, document route only; no LLM in R1):

    classify_question    deterministic gate: advice or injection -> fixed out-of-scope response
    dense_search         query embedding vs chunk embeddings (pgvector, patient-scoped)
    lexical_search       content words OR-ed in Postgres full text ('simple'), patient-scoped
    RRF                  fuse the two rankings by position (k from the config)
    rerank               cross-encoder on the top candidates (hybrid_rerank mode)
    best_per_page        one passage per (document, page)
    should_abstain       below the calibrated threshold, or nothing found -> insufficient evidence
    page_contexts        filename, date and parse method of each cited page

Patient isolation: every query is scoped in the records layer. As a second check, any hit whose
patient differs from the requested one stops the search with an error instead of being shown.
The question text and the excerpts are never logged: only ids, counts, scores and timings.
"""

import logging
import time
import uuid
from collections.abc import Callable, Sequence

from app.core.errors import RetrievalError
from app.records.chunks import ChunkHit, dense_search, lexical_search, page_contexts
from app.retrieval.evidence import (
    AnswerStatus,
    Evidence,
    EvidenceAnswer,
    RetrievalConfig,
    RetrievalMode,
    ScoredChunk,
    best_per_page,
    should_abstain,
)
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.models import Embedder, Reranker
from app.retrieval.query import QuestionGate, classify_question, query_terms

logger = logging.getLogger(__name__)


def _scored(hit: ChunkHit, score: float) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        page_number=hit.page_number,
        chunk_type=hit.chunk_type,
        section=hit.section,
        content=hit.content,
        score=score,
    )


def _check_scope(patient_id: uuid.UUID, hits: Sequence[ChunkHit]) -> None:
    """Defence in depth: the records layer already filters by patient."""
    if any(hit.patient_id != patient_id for hit in hits):
        raise RuntimeError("A search returned another patient's chunk; the search was stopped")


def _timed[R](timings: dict[str, int], name: str, call: Callable[[], R]) -> R:
    started = time.perf_counter()
    result = call()
    timings[name] = round((time.perf_counter() - started) * 1000)
    return result


def search_evidence(
    patient_id: uuid.UUID,
    question: str,
    *,
    config: RetrievalConfig,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
) -> EvidenceAnswer:
    """Ranked page-cited evidence for ``question``, or an abstention / out-of-scope answer.

    Raises:
        RetrievalError: ``invalid_query`` (not a ``str``), or a model the mode needs is missing.
    """
    if not isinstance(question, str):
        raise RetrievalError("The question must be text", kind="invalid_query")
    mode = config.mode
    if mode.uses_dense and embedder is None:
        raise RetrievalError(f"Mode {mode} needs an embedder", kind="invalid_query")
    if mode.uses_reranker and reranker is None:
        raise RetrievalError(f"Mode {mode} needs a reranker", kind="invalid_query")
    calibrated = config.abstain_threshold is not None
    started = time.perf_counter()
    timings: dict[str, int] = {}

    gate = classify_question(question)
    if gate is not QuestionGate.ALLOWED:
        logger.info("Question refused: patient_id=%s gate=%s", patient_id, gate)
        return EvidenceAnswer(AnswerStatus.OUT_OF_SCOPE, (), None, mode, calibrated, timings)

    dense: tuple[ChunkHit, ...] = ()
    lexical: tuple[ChunkHit, ...] = ()
    if mode.uses_dense:
        query_embedder = embedder
        assert query_embedder is not None  # checked above
        vector = _timed(timings, "embed_query", lambda: query_embedder.embed_query(question))
        dense = _timed(
            timings,
            "dense_search",
            lambda: dense_search(patient_id, vector, limit=config.candidates),
        )
    terms = query_terms(question)
    if mode.uses_lexical and terms:
        lexical = _timed(
            timings,
            "lexical_search",
            lambda: lexical_search(patient_id, terms, limit=config.candidates),
        )
    _check_scope(patient_id, (*dense, *lexical))

    by_id = {hit.chunk_id: hit for hit in (*dense, *lexical)}
    if mode is RetrievalMode.DENSE:
        ranked = [_scored(hit, hit.score) for hit in dense]
    elif mode is RetrievalMode.LEXICAL:
        ranked = [_scored(hit, hit.score) for hit in lexical]
    else:
        fused = reciprocal_rank_fusion(
            [[hit.chunk_id for hit in dense], [hit.chunk_id for hit in lexical]], k=config.rrf_k
        )
        ranked = [_scored(by_id[chunk_id], score) for chunk_id, score in fused]
        if mode is RetrievalMode.HYBRID_RERANK and ranked:
            cross_encoder = reranker
            assert cross_encoder is not None  # checked above
            top = ranked[: config.rerank_top]
            scores = _timed(
                timings, "rerank", lambda: cross_encoder.score(question, [c.content for c in top])
            )
            if len(scores) != len(top):
                raise RetrievalError(
                    "The reranker returned a wrong number of scores", kind="invalid_embedding"
                )
            rescored = [_scored(by_id[c.chunk_id], s) for c, s in zip(top, scores, strict=True)]
            ranked = sorted(rescored, key=lambda c: (-c.score, str(c.chunk_id)))

    pages = best_per_page(ranked)[: config.top_k]
    top_score = pages[0].score if pages else None
    if should_abstain(top_score, config.abstain_threshold):
        status, passages = AnswerStatus.INSUFFICIENT_EVIDENCE, ()
    else:
        contexts = page_contexts(patient_id, [(p.document_id, p.page_number) for p in pages])
        passages = tuple(
            Evidence(
                document_id=page.document_id,
                page_number=page.page_number,
                chunk_id=page.chunk_id,
                chunk_type=page.chunk_type,
                section=page.section,
                excerpt=page.content,
                score=page.score,
                original_filename=contexts[(page.document_id, page.page_number)].original_filename,
                document_date=contexts[(page.document_id, page.page_number)].document_date,
                parse_method=contexts[(page.document_id, page.page_number)].parse_method,
                # a label only, attached after ranking: never part of a score (ADR-036 R1)
                identity_check=contexts[(page.document_id, page.page_number)].identity_check,
            )
            for page in pages
            if (page.document_id, page.page_number) in contexts
        )
        status = AnswerStatus.ANSWERED if passages else AnswerStatus.INSUFFICIENT_EVIDENCE
    timings["total"] = round((time.perf_counter() - started) * 1000)
    logger.info(
        "Evidence search: patient_id=%s mode=%s status=%s dense=%d lexical=%d pages=%d "
        "top_score=%s total_ms=%d",
        patient_id,
        mode,
        status,
        len(dense),
        len(lexical),
        len(passages),
        None if top_score is None else round(top_score, 4),
        timings["total"],
    )
    return EvidenceAnswer(status, passages, top_score, mode, calibrated, timings)
