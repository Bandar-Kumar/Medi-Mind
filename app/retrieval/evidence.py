"""Evidence results, page grouping, abstention and threshold calibration (Milestone R1, ADR-035).

R1 answers with stored evidence only: no text is generated, so a citation is correct by
construction (the excerpt *is* the stored chunk of that page). Evidence objects hold document text
and the original filename; ``repr=False`` hides them from ``repr()`` only, so they must never be
logged or serialized into logs.
"""

import datetime as dt
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

OUT_OF_SCOPE_MESSAGE = (
    "MediMind only shows what your own records say. It cannot give medical advice, recommend or "
    "change treatment, or interpret results as a diagnosis. Please ask your doctor."
)
INSUFFICIENT_EVIDENCE_MESSAGE = (
    "Not enough evidence in this patient's documents to answer. Nothing is guessed."
)
OCR_WARNING = "OCR text - unverified; compare with the original page"
# Shown on evidence from a document whose identity check did not confirm the patient (ADR-036 R1).
# Presentation only: it is attached after ranking and never changes a score or the order.
IDENTITY_WARNING = "identity not confirmed"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    OUT_OF_SCOPE = "out_of_scope"


class RetrievalMode(StrEnum):
    DENSE = "dense"
    LEXICAL = "lexical"
    HYBRID = "hybrid"  # dense + lexical, fused by RRF
    HYBRID_RERANK = "hybrid_rerank"  # RRF, then the cross-encoder on the top candidates

    @property
    def uses_dense(self) -> bool:
        return self is not RetrievalMode.LEXICAL

    @property
    def uses_lexical(self) -> bool:
        return self is not RetrievalMode.DENSE

    @property
    def uses_reranker(self) -> bool:
        return self is RetrievalMode.HYBRID_RERANK


@dataclass(frozen=True)
class RetrievalConfig:
    mode: RetrievalMode = RetrievalMode.HYBRID_RERANK
    candidates: int = 50  # per retriever
    rrf_k: int = 60
    rerank_top: int = 20
    top_k: int = 5  # pages returned
    # Abstain when the top score (of this mode) is below it. None: not calibrated, never abstains
    # on score (it still abstains when nothing at all is found).
    abstain_threshold: float | None = None

    def __post_init__(self) -> None:
        for name in ("candidates", "rrf_k", "rerank_top", "top_k"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.abstain_threshold is not None and not math.isfinite(self.abstain_threshold):
            raise ValueError("abstain_threshold must be finite")


@dataclass(frozen=True)
class ScoredChunk:
    """A candidate chunk with the score of the current mode (similarity, RRF or reranker)."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    page_number: int
    chunk_type: str
    section: str | None
    content: str = field(repr=False)
    score: float


@dataclass(frozen=True)
class Evidence:
    """One cited page: the best chunk of that page and where it comes from."""

    document_id: uuid.UUID
    page_number: int
    chunk_id: uuid.UUID
    chunk_type: str
    section: str | None
    excerpt: str = field(repr=False)
    score: float
    original_filename: str = field(repr=False)
    document_date: dt.date | None
    parse_method: str  # "native" or "ocr"
    identity_check: str  # the document's identity outcome: "match", "unknown" or "mismatch"

    @property
    def ocr_warning(self) -> str | None:
        return OCR_WARNING if self.parse_method == "ocr" else None

    @property
    def identity_warning(self) -> str | None:
        """ "identity not confirmed" unless the identity check found a match."""
        return None if self.identity_check == "match" else IDENTITY_WARNING


@dataclass(frozen=True)
class EvidenceAnswer:
    status: AnswerStatus
    passages: tuple[Evidence, ...]
    top_score: float | None
    mode: RetrievalMode
    threshold_calibrated: bool
    timings_ms: dict[str, int] = field(default_factory=dict)

    @property
    def message(self) -> str | None:
        if self.status is AnswerStatus.OUT_OF_SCOPE:
            return OUT_OF_SCOPE_MESSAGE
        if self.status is AnswerStatus.INSUFFICIENT_EVIDENCE:
            return INSUFFICIENT_EVIDENCE_MESSAGE
        return None


def best_per_page(ranked: Sequence[ScoredChunk]) -> list[ScoredChunk]:
    """Keep the first (best) chunk of each (document, page); ``ranked`` is best first."""
    seen: set[tuple[uuid.UUID, int]] = set()
    pages = []
    for chunk in ranked:
        key = (chunk.document_id, chunk.page_number)
        if key not in seen:
            seen.add(key)
            pages.append(chunk)
    return pages


def should_abstain(top_score: float | None, threshold: float | None) -> bool:
    """No candidate at all always abstains; otherwise only below a calibrated threshold."""
    if top_score is None:
        return True
    if threshold is None:
        return False
    return top_score < threshold


@dataclass(frozen=True)
class Calibration:
    threshold: float
    precision: float
    recall: float
    f1: float


def calibrate_threshold(
    answerable_scores: Sequence[float], unanswerable_scores: Sequence[float]
) -> Calibration | None:
    """The threshold that best separates answerable from unanswerable questions (by F1).

    Positives are unanswerable questions; "abstain" means top score < threshold. Candidates are
    every observed score and one value above the maximum. Ties keep the lowest threshold (fewer
    abstentions on answerable questions). Use development data only, never held-out.
    """
    if not unanswerable_scores:
        return None
    observed = sorted({*answerable_scores, *unanswerable_scores})
    candidates = [*observed, observed[-1] + 1.0]
    best: Calibration | None = None
    for threshold in candidates:
        true_pos = sum(1 for s in unanswerable_scores if s < threshold)
        false_pos = sum(1 for s in answerable_scores if s < threshold)
        precision = true_pos / (true_pos + false_pos) if true_pos + false_pos else 0.0
        recall = true_pos / len(unanswerable_scores)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if best is None or f1 > best.f1:
            best = Calibration(threshold, precision, recall, f1)
    return best
