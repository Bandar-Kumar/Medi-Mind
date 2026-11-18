"""Unit tests of the longitudinal evaluation runner's pure parts (no database)."""

import random
from collections import Counter
from decimal import Decimal

from app.extraction.reference import load_reference
from evals import run_longitudinal as runner
from evals.synthetic import longitudinal as gen

REF = load_reference()


def _doc(patient: dict, suffix: str) -> dict:
    return next(d for d in patient["documents"] if d["doc_key"].endswith(suffix))


def test_loader_facts_follow_the_production_normalizers() -> None:
    patient = gen.scenario_patient(2, random.Random(1))
    ambiguous = _doc(patient, "D11")
    (record,) = [runner._observation(o, ambiguous, REF) for o in ambiguous["observations"]]
    assert record.review_status == "needs_review" and record.review_reasons == ("ambiguous_date",)
    assert ambiguous["page_text"][record.char_start : record.char_end] == record.source_excerpt
    converted = _doc(patient, "D09")
    sugar, hb = (runner._observation(o, converted, REF) for o in converted["observations"])
    assert sugar.unit_norm == "mg/dL"
    assert sugar.value_num_norm == Decimal(sugar.value_raw) * Decimal("18.0156")
    assert hb.unit_norm == "mmol/L" and "unit_not_convertible" in hb.quality_notes
    reported = _doc(patient, "D01")
    first = runner._observation(reported["observations"][0], reported, REF)
    assert first.effective_date_source == "report"
    assert "effective_date_from_report" in first.quality_notes
    undated = _doc(patient, "D10")
    loose = runner._observation(undated["observations"][0], undated, REF)
    assert loose.effective_date is None and loose.quality_notes == ("no_effective_date",)


def test_truth_changes_skip_crowded_dates() -> None:
    points = [
        {"unit": "g/dL", "date": "2024-01-01", "value": "14.0", "keys": ["a"]},
        {"unit": "g/dL", "date": "2024-02-01", "value": "13.0", "keys": ["b"]},
        {"unit": "g/dL", "date": "2024-02-01", "value": "15.0", "keys": ["c"]},
        {"unit": "g/dL", "date": "2024-03-01", "value": "14.5", "keys": ["d"]},
        {"unit": "g/dL", "date": "2024-04-01", "value": "14.0", "keys": ["e"]},
    ]
    assert runner._gt_changes("Hb", points) == {
        ("Hb", "g/dL", "2024-03-01", "2024-04-01", Decimal("-0.5"))
    }


def test_summary_ratios_and_targets() -> None:
    counts = Counter(
        timeline_system=10, timeline_truth=10, timeline_matched=9, dup_found=4, dup_truth=5,
        dup_correct=4, conflict_found=1, conflict_truth=1, conflict_correct=1,
    )  # fmt: skip
    metrics = runner.summarize(counts)
    assert metrics["timeline_entry_precision"] == 0.9
    assert metrics["duplicate_recall"] == 0.8 and metrics["false_duplicate_rate"] == 0.0
    assert metrics["timeline_order_accuracy"] is None  # nothing to compare is not a pass
    met = runner.check_targets(metrics)
    assert met["timeline_entry_precision"] is False and met["conflict_recall"] is True
    assert met["timeline_order_accuracy"] is None
    assert met["patient_isolation_leaks"] is True
