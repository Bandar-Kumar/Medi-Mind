"""Observation comparability, conflict grouping and detection keys.

See docs/PHASE_3_PLAN.md §6.6 and §6.7.

Pure (COMPARE_VERSION). The records layer gathers the usable facts of one group (patient,
analyte, effective date, date source), passes each fact's *display* values (machine-normalized, or
the latest human correction) and its review ``state_token``; this module decides which pairs
conflict, groups them into connected components and computes each component's detection key.

Pair outcomes:
- ``equal``         same value (``14.2`` and ``14.20``), or same qualitative class;
- ``consistent``    overlapping intervals (``< 5`` and ``3``);
- ``conflict``      disjoint intervals, or a declared opposite pair of qualitative classes;
- ``incomparable``  different or missing units, numeric vs qualitative, a token or analyte
  without a reference scale, or two different collection times.
Only ``conflict`` creates an edge. One conflict per connected component: members need not all
conflict directly with each other; the direct pairs are reported separately.
"""

import datetime as dt
import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.extraction.normalize.ranges import value_interval
from app.extraction.reference import Reference
from app.extraction.versions import COMPARE_VERSION

EQUAL = "equal"
CONSISTENT = "consistent"
CONFLICT = "conflict"
INCOMPARABLE = "incomparable"

MACHINE = "machine"  # state token of a fact whose usability and display no decision changed
USABLE_STATUSES = frozenset({"validated", "verified", "corrected"})


@dataclass(frozen=True)
class ComparableObservation:
    """One usable fact's display values. No text beyond canonical tokens and units."""

    fact_id: str
    analyte_name: str
    effective_time: dt.time | None
    value: Decimal | None  # normalized numeric value (value_num_norm, or the corrected one)
    comparator: str | None
    unit: str | None  # unit_norm (canonical)
    qualitative: str | None  # canonical token
    state_token: str = MACHINE


def _disjoint(a: ComparableObservation, b: ComparableObservation) -> bool:
    assert a.value is not None and b.value is not None
    a_low, a_low_inc, a_high, a_high_inc = value_interval(a.value, a.comparator)
    b_low, b_low_inc, b_high, b_high_inc = value_interval(b.value, b.comparator)

    def before(high: Decimal | None, high_inc: bool, low: Decimal | None, low_inc: bool) -> bool:
        if high is None or low is None:
            return False
        return high < low or (high == low and not (high_inc and low_inc))

    return before(a_high, a_high_inc, b_low, b_low_inc) or before(
        b_high, b_high_inc, a_low, a_low_inc
    )


def compare_pair(a: ComparableObservation, b: ComparableObservation, reference: Reference) -> str:
    if a.analyte_name != b.analyte_name:
        return INCOMPARABLE
    if a.effective_time and b.effective_time and a.effective_time != b.effective_time:
        return INCOMPARABLE  # two collections on the same day
    if a.value is not None and b.value is not None:
        if a.qualitative or b.qualitative or a.unit is None or a.unit != b.unit:
            return INCOMPARABLE
        if a.comparator == b.comparator and a.value == b.value:
            return EQUAL
        return CONFLICT if _disjoint(a, b) else CONSISTENT
    if a.qualitative and b.qualitative and a.value is None and b.value is None:
        scale = reference.qualitative.get(a.analyte_name)
        if scale is None:
            return INCOMPARABLE
        class_a, class_b = scale.classes.get(a.qualitative), scale.classes.get(b.qualitative)
        if class_a is None or class_b is None:
            return INCOMPARABLE
        if class_a == class_b:
            return EQUAL
        return CONFLICT if frozenset({class_a, class_b}) == scale.opposite else INCOMPARABLE
    return INCOMPARABLE


@dataclass(frozen=True)
class ConflictComponent:
    members: tuple[str, ...]  # fact ids, sorted
    direct_pairs: tuple[tuple[str, str], ...]  # sorted pairs that conflict directly


def conflict_components(
    facts: Sequence[ComparableObservation], reference: Reference
) -> tuple[ConflictComponent, ...]:
    """Connected components (2+ facts) of the conflict graph, in a deterministic order."""
    ordered = sorted(facts, key=lambda f: f.fact_id)
    parent = {f.fact_id: f.fact_id for f in ordered}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges: list[tuple[str, str]] = []
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if compare_pair(a, b, reference) == CONFLICT:
                edges.append((a.fact_id, b.fact_id))
                parent[find(a.fact_id)] = find(b.fact_id)
    groups: dict[str, list[str]] = {}
    for f in ordered:
        groups.setdefault(find(f.fact_id), []).append(f.fact_id)
    components = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_set = set(members)
        pairs = tuple(sorted(e for e in edges if e[0] in member_set))
        components.append(ConflictComponent(tuple(sorted(members)), pairs))
    return tuple(sorted(components, key=lambda c: c.members))


def display_fingerprint(fact: ComparableObservation, reference: Reference) -> str:
    """Canonical text of the compared display values (part of the detection key)."""
    value = format(fact.value.normalize(), "f") if fact.value is not None else ""
    token = fact.qualitative or ""
    scale = reference.qualitative.get(fact.analyte_name)
    if token and scale is not None:
        token = scale.classes.get(token, token)
    return "|".join((fact.comparator or "", value, fact.unit or "", token))


def detection_key(
    *,
    analyte_id: int,
    effective_date: dt.date,
    effective_date_source: str,
    members: Iterable[ComparableObservation],
    reference: Reference,
    rule_version: str = COMPARE_VERSION,
) -> str:
    """Stable for the same component, values and review states; new after any real change."""
    parts = sorted(
        f"{m.fact_id}:{display_fingerprint(m, reference)}:{m.state_token}" for m in members
    )
    text = "\n".join(
        [rule_version, str(analyte_id), effective_date.isoformat(), effective_date_source, *parts]
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Decision:
    decision_id: str
    decision: str  # verify / correct / reject


def apply_decisions(machine_status: str, decisions: Sequence[Decision]) -> tuple[str, str]:
    """(current review_status, state_token) after the decisions, oldest first (§6.5, §6.7).

    The latest decision decides the status. A decision changes the state token when it changes
    the fact's usability (usable statuses: validated, verified, corrected) or its display values
    (every ``correct``; a ``verify`` that follows a correction returns to the machine values).
    """
    status = machine_status
    display = MACHINE
    token = MACHINE
    for item in decisions:
        if item.decision == "verify":
            new_status, new_display = "verified", MACHINE
        elif item.decision == "correct":
            new_status, new_display = "corrected", item.decision_id
        elif item.decision == "reject":
            new_status, new_display = "rejected", display
        else:
            raise ValueError("unknown decision")
        usability_changed = (status in USABLE_STATUSES) != (new_status in USABLE_STATUSES)
        if usability_changed or new_display != display:
            token = item.decision_id
        status, display = new_status, new_display
    return status, token
