"""Deterministic grounding (ADR-008 as refined by D-4; docs/PHASE_3_PLAN.md §6.2).

A candidate passes only when its excerpt is exactly ``page_text[start:end]`` and every quoted part
(analyte, value, unit, range, date) is exactly the page text at its own recorded offsets and lies
inside the excerpt. A candidate that fails is never stored: nothing unlocated reaches the database.
"""

from collections.abc import Iterable


def span_matches(page_text: str, start: int, end: int, text: str) -> bool:
    return 0 <= start <= end <= len(page_text) and page_text[start:end] == text


def grounded(
    page_text: str,
    excerpt: str,
    start: int,
    end: int,
    parts: Iterable[tuple[str, int, int]],
) -> bool:
    """The excerpt and every (text, start, end) part are exact slices; parts lie inside it."""
    if not excerpt.strip() or not span_matches(page_text, start, end, excerpt):
        return False
    for text, part_start, part_end in parts:
        if not span_matches(page_text, part_start, part_end, text):
            return False
        if part_start < start or part_end > end:
            return False
    return True
