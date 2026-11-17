"""Reciprocal Rank Fusion (Cormack et al., 2009): combine rankings without comparing their scores.

score(item) = sum over rankings of 1 / (k + rank), rank starting at 1. Dense similarities and
full-text ranks are on different scales, so only positions are used. Deterministic: ties are
broken by the item's string form.
"""

from collections.abc import Hashable, Sequence


def reciprocal_rank_fusion[T: Hashable](
    rankings: Sequence[Sequence[T]], *, k: int = 60
) -> list[tuple[T, float]]:
    """Fused (item, score) pairs, best first. An item repeated within one ranking counts once."""
    if k < 1:
        raise ValueError("k must be positive")
    scores: dict[T, float] = {}
    for ranking in rankings:
        seen: set[T] = set()
        for rank, item in enumerate(ranking, start=1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], str(pair[0])))
