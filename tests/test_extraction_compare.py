"""Comparability, conflict components, detection keys and review state (§6.5-§6.7)."""

import datetime as dt
from decimal import Decimal

import pytest

from app.extraction.compare import (
    CONFLICT,
    CONSISTENT,
    EQUAL,
    INCOMPARABLE,
    MACHINE,
    ComparableObservation,
    Decision,
    apply_decisions,
    compare_pair,
    conflict_components,
    detection_key,
)
from app.extraction.reference import load_reference

REF = load_reference()
DATE = dt.date(2024, 3, 25)


def obs(
    fact_id: str,
    value: str | None = None,
    *,
    cmp: str | None = None,
    unit: str | None = "mg/dL",
    qual: str | None = None,
    analyte: str = "Fasting Blood Sugar",
    time: dt.time | None = None,
    token: str = MACHINE,
) -> ComparableObservation:
    return ComparableObservation(
        fact_id=fact_id,
        analyte_name=analyte,
        effective_time=time,
        value=Decimal(value) if value is not None else None,
        comparator=cmp,
        unit=unit,
        qualitative=qual,
        state_token=token,
    )


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (obs("a", "14.2"), obs("b", "14.20"), EQUAL),
        (obs("a", "100"), obs("b", "110"), CONFLICT),
        (obs("a", "5", cmp="<"), obs("b", "3"), CONSISTENT),
        (obs("a", "5", cmp="<"), obs("b", "7"), CONFLICT),
        (obs("a", "5", cmp="<="), obs("b", "5"), CONSISTENT),
        (obs("a", "5", cmp="<"), obs("b", "5"), CONFLICT),
        (obs("a", "5", cmp="<"), obs("b", "10", cmp="<"), CONSISTENT),
        (obs("a", "5", cmp="<"), obs("b", "6", cmp=">="), CONFLICT),
        (obs("a", "100"), obs("b", "5.5", unit="mmol/L"), INCOMPARABLE),
        (obs("a", "100", unit=None), obs("b", "100", unit=None), INCOMPARABLE),
        (obs("a", "100"), obs("b", qual="Positive", unit=None), INCOMPARABLE),
        (
            obs("a", "100", time=dt.time(8, 0)),
            obs("b", "150", time=dt.time(10, 0)),
            INCOMPARABLE,
        ),
    ],
)
def test_pairs(a, b, expected) -> None:
    assert compare_pair(a, b, REF) == expected
    assert compare_pair(b, a, REF) == expected


def _q(fact_id: str, token: str, analyte: str) -> ComparableObservation:
    return obs(fact_id, qual=token, unit=None, analyte=analyte)


def test_qualitative_needs_an_analyte_specific_scale() -> None:
    glucose = "Urine Glucose"
    assert compare_pair(_q("a", "Negative", glucose), _q("b", "Positive", glucose), REF) == CONFLICT
    assert compare_pair(_q("a", "Negative", glucose), _q("b", "Nil", glucose), REF) == EQUAL
    assert compare_pair(_q("a", "Trace", glucose), _q("b", "Negative", glucose), REF) == (
        INCOMPARABLE
    )
    hbsag = "HBsAg"
    assert compare_pair(_q("a", "Reactive", hbsag), _q("b", "Non-reactive", hbsag), REF) == CONFLICT
    # The same words for an analyte without a scale are never treated as opposites.
    other = "Haemoglobin"
    assert compare_pair(_q("a", "Negative", other), _q("b", "Positive", other), REF) == (
        INCOMPARABLE
    )
    # A token the analyte's scale does not map is incomparable.
    assert compare_pair(_q("a", "Reactive", glucose), _q("b", "Negative", glucose), REF) == (
        INCOMPARABLE
    )


def test_one_conflict_per_connected_component() -> None:
    a, b, c = obs("A", "5", cmp="<"), obs("B", "7"), obs("C", "4", cmp="<")
    assert compare_pair(a, c, REF) == CONSISTENT  # members need not all conflict directly
    components = conflict_components([c, a, b], REF)
    assert len(components) == 1
    assert components[0].members == ("A", "B", "C")
    assert components[0].direct_pairs == (("A", "B"), ("B", "C"))


def _key(members) -> str:
    return detection_key(
        analyte_id=7,
        effective_date=DATE,
        effective_date_source="collection",
        members=members,
        reference=REF,
    )


def test_detection_key_is_stable_and_changes_with_real_changes() -> None:
    a, b, c = obs("A", "5", cmp="<"), obs("B", "7"), obs("C", "4", cmp="<")
    assert _key([a, b, c]) == _key([c, b, a])
    assert _key([a, b, c]) == _key([obs("A", "5.0", cmp="<"), b, c])  # same value, other scale
    corrected_c = obs("C", "8", cmp="<", token="decision-1")
    assert conflict_components([a, b, corrected_c], REF)[0].members == ("A", "B")
    assert _key([a, b]) != _key([a, b, c])
    assert _key([a, obs("B", "7", token="decision-2")]) != _key([a, b])


def test_no_components_without_conflicts() -> None:
    assert conflict_components([obs("A", "5"), obs("B", "5.0")], REF) == ()


@pytest.mark.parametrize(
    ("machine", "decisions", "status", "token"),
    [
        ("validated", [], "validated", MACHINE),
        ("validated", [("d1", "verify")], "verified", MACHINE),  # no usability/display change
        ("needs_review", [("d1", "verify")], "verified", "d1"),  # becomes usable
        ("validated", [("d1", "correct")], "corrected", "d1"),
        ("validated", [("d1", "correct"), ("d2", "verify")], "verified", "d2"),  # back to machine
        ("validated", [("d1", "reject")], "rejected", "d1"),
        ("validated", [("d1", "reject"), ("d2", "verify")], "verified", "d2"),  # reversal
        ("validated", [("d1", "reject"), ("d2", "correct")], "corrected", "d2"),
        ("validated", [("d1", "verify"), ("d2", "verify")], "verified", MACHINE),
    ],
)
def test_latest_decision_decides_status_and_state_token(machine, decisions, status, token) -> None:
    result = apply_decisions(machine, [Decision(i, d) for i, d in decisions])
    assert result == (status, token)
