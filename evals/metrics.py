"""Retrieval and safety metrics (Task 1.10). Pure functions; each is unit-tested by hand.

Relevance is page-level: an item is a ``(document or report id, page_number)`` key. ``ranked`` is
best first, one entry per page (the system already groups chunks by page).
"""

import math
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass


def recall_at_k(ranked: Sequence[Hashable], relevant: Mapping[Hashable, int], k: int) -> float:
    """Share of the relevant pages found in the top ``k`` (relevant = grade > 0)."""
    wanted = {item for item, grade in relevant.items() if grade > 0}
    if not wanted:
        raise ValueError("recall needs at least one relevant item")
    return len(wanted & set(ranked[:k])) / len(wanted)


def hit_at_k(ranked: Sequence[Hashable], relevant: Mapping[Hashable, int], k: int) -> float:
    """1.0 if any relevant page is in the top ``k``, else 0.0."""
    return 1.0 if any(relevant.get(item, 0) > 0 for item in ranked[:k]) else 0.0


def reciprocal_rank(ranked: Sequence[Hashable], relevant: Mapping[Hashable, int]) -> float:
    """1 / rank of the first relevant page; 0.0 if none is retrieved."""
    for rank, item in enumerate(ranked, start=1):
        if relevant.get(item, 0) > 0:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[Hashable], relevant: Mapping[Hashable, int], k: int) -> float:
    """Normalized DCG with gain 2^grade - 1 and discount log2(rank + 1)."""

    def dcg(grades: Sequence[int]) -> float:
        return sum((2**g - 1) / math.log2(rank + 1) for rank, g in enumerate(grades, start=1))

    ideal = dcg(sorted((g for g in relevant.values() if g > 0), reverse=True)[:k])
    if ideal == 0:
        raise ValueError("nDCG needs at least one relevant item")
    return dcg([relevant.get(item, 0) for item in ranked[:k]]) / ideal


@dataclass(frozen=True)
class BinaryScores:
    precision: float
    recall: float
    f1: float
    true_positive: int
    false_positive: int
    false_negative: int


def binary_scores(expected: Sequence[bool], predicted: Sequence[bool]) -> BinaryScores:
    """Precision, recall and F1 of ``predicted`` against ``expected`` (e.g. "should abstain")."""
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have the same length")
    tp = sum(1 for e, p in zip(expected, predicted, strict=True) if e and p)
    fp = sum(1 for e, p in zip(expected, predicted, strict=True) if not e and p)
    fn = sum(1 for e, p in zip(expected, predicted, strict=True) if e and not p)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return BinaryScores(precision, recall, f1, tp, fp, fn)


def rate(flags: Sequence[bool]) -> float:
    """Share of True values (e.g. the refusal rate or the leak rate)."""
    if not flags:
        raise ValueError("a rate needs at least one value")
    return sum(1 for flag in flags if flag) / len(flags)


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("a mean needs at least one value")
    return sum(values) / len(values)


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100)."""
    if not values or not 0 <= p <= 100:
        raise ValueError("percentile needs values and 0 <= p <= 100")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]
