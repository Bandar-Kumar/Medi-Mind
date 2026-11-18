"""Cross-document duplicate candidates (docs/PHASE_4_PLAN.md §5; ADR-039).

Derived at read time from the usable facts; nothing is stored, merged or deleted, so the result
always follows the latest review decisions and is repeatable by construction.

A **candidate pair** is two usable observations from *different documents* with the same analyte,
the same day-precision date, and values that Phase 3's comparability rule calls ``equal``
(``app.extraction.compare.compare_pair``: same normalized value, comparator and unit, or the same
qualitative class; two different collection times are never equal). Month- or year-precision and
undated facts are never candidates: their date cannot show it is the same sample.

- **exact**: also the same printed test name, value, unit and range text, the same date source
  and time, and neither value comes from a human correction: the same printed result (e.g. one
  report uploaded twice, or a reissued copy). The timeline and trends show it once, with every
  source.
- **possible**: equal values on the same date, but the printed text, date source or a correction
  differs (e.g. another lab's report of the same sample, ``142 g/L`` vs ``14.2 g/dL``). Shown
  separately, linked to each other, with the reasons.

Groups are connected components of candidate pairs; a group is ``exact`` only when every pair in it
is exact. Every pair carries fixed reason codes.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.extraction.compare import EQUAL, ComparableObservation, compare_pair
from app.extraction.normalize.numbers import text_key
from app.extraction.reference import Reference
from app.longitudinal.model import DAY, MACHINE, LongitudinalObservation

EXACT: Final = "exact"
POSSIBLE: Final = "possible"

# Reason codes (shown through app/ui/texts.py; never free text)
SAME_TEST_DATE_VALUE: Final = "same_test_date_and_value"
SAME_PRINTED_TEXT: Final = "same_printed_text"
DIFFERENT_PRINTED_NAME: Final = "different_printed_name"
DIFFERENT_PRINTED_VALUE: Final = "different_printed_value"
DIFFERENT_PRINTED_UNIT: Final = "different_printed_unit"
DIFFERENT_PRINTED_RANGE: Final = "different_printed_range"
DIFFERENT_DATE_SOURCE: Final = "different_date_source"
DIFFERENT_TIME_INFORMATION: Final = "different_time_information"
CORRECTED_VALUE: Final = "corrected_value"

_STATUS_RANK = {"verified": 0, "corrected": 0, "validated": 1}


@dataclass(frozen=True)
class DuplicatePair:
    a: str  # fact ids, a < b
    b: str
    kind: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DuplicateGroup:
    members: tuple[str, ...]  # fact ids, sorted
    kind: str  # exact / possible
    reasons: tuple[str, ...]  # union of the pairs' reasons, sorted
    pairs: tuple[DuplicatePair, ...]
    representative: str  # the member shown first: human-reviewed before auto-checked, then id


def _comparable(o: LongitudinalObservation) -> ComparableObservation:
    return ComparableObservation(
        fact_id=o.fact_id,
        analyte_name=o.analyte_name or "",
        effective_time=o.effective_time,
        value=o.value,
        comparator=o.comparator,
        unit=o.unit,
        qualitative=o.qualitative,
    )


def _key(text: str | None) -> str:
    return text_key(text) if text else ""


def _pair(
    a: LongitudinalObservation, b: LongitudinalObservation, reference: Reference
) -> DuplicatePair | None:
    if a.document_id == b.document_id or a.analyte_id is None or a.analyte_id != b.analyte_id:
        return None
    if not (a.date_precision == DAY and b.date_precision == DAY):
        return None
    if a.effective_date is None or a.effective_date != b.effective_date:
        return None
    if compare_pair(_comparable(a), _comparable(b), reference) != EQUAL:
        return None
    differences = []
    if _key(a.analyte_raw) != _key(b.analyte_raw):
        differences.append(DIFFERENT_PRINTED_NAME)
    if _key(a.value_raw) != _key(b.value_raw):
        differences.append(DIFFERENT_PRINTED_VALUE)
    if _key(a.unit_raw) != _key(b.unit_raw):
        differences.append(DIFFERENT_PRINTED_UNIT)
    if _key(a.ref_range_raw) != _key(b.ref_range_raw):
        differences.append(DIFFERENT_PRINTED_RANGE)
    if a.date_source != b.date_source:
        differences.append(DIFFERENT_DATE_SOURCE)
    if a.effective_time != b.effective_time:
        differences.append(DIFFERENT_TIME_INFORMATION)  # one has a time, the other none
    if a.value_source != MACHINE or b.value_source != MACHINE:
        differences.append(CORRECTED_VALUE)
    kind = POSSIBLE if differences else EXACT
    reasons = (SAME_TEST_DATE_VALUE, *(differences or [SAME_PRINTED_TEXT]))
    first, second = sorted((a.fact_id, b.fact_id))
    return DuplicatePair(first, second, kind, tuple(sorted(reasons)))


def find_duplicates(
    observations: Sequence[LongitudinalObservation], reference: Reference
) -> tuple[DuplicateGroup, ...]:
    """Duplicate groups among the given usable observations, in a deterministic order."""
    by_id = {o.fact_id: o for o in observations}
    buckets: dict[tuple, list[LongitudinalObservation]] = {}
    for o in observations:  # only same analyte and same day can pair: bucket to avoid O(n^2)
        if o.analyte_id is not None and o.date_precision == DAY and o.effective_date is not None:
            buckets.setdefault((o.analyte_id, o.effective_date), []).append(o)
    pairs: list[DuplicatePair] = []
    for bucket in buckets.values():
        ordered = sorted(bucket, key=lambda o: o.fact_id)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                if (pair := _pair(a, b, reference)) is not None:
                    pairs.append(pair)

    parent = {fact_id: fact_id for pair in pairs for fact_id in (pair.a, pair.b)}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in pairs:
        parent[find(pair.a)] = find(pair.b)
    members_of: dict[str, list[str]] = {}
    for fact_id in sorted(parent):
        members_of.setdefault(find(fact_id), []).append(fact_id)

    groups = []
    for members in members_of.values():
        member_set = set(members)
        group_pairs = tuple(
            sorted((p for p in pairs if p.a in member_set), key=lambda p: (p.a, p.b))
        )
        # exact only if every pair of members was compared and found exact
        exact = len(group_pairs) == len(members) * (len(members) - 1) // 2 and all(
            p.kind == EXACT for p in group_pairs
        )
        reasons = tuple(sorted({r for p in group_pairs for r in p.reasons}))
        representative = min(
            members, key=lambda m: (_STATUS_RANK.get(by_id[m].review_status, 2), m)
        )
        groups.append(
            DuplicateGroup(
                members=tuple(members),
                kind=EXACT if exact else POSSIBLE,
                reasons=reasons,
                pairs=group_pairs,
                representative=representative,
            )
        )
    return tuple(sorted(groups, key=lambda g: g.members))
