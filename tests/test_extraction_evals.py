"""Extraction evaluation: dataset build checks, end-to-end scoring, protocol guards (§12).

No Docling: the scoring is tested on drafts built in code. All data is fictional.
"""

import datetime as dt
import json
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.extraction.schemas import ObservationDraft
from app.records import facts as repo
from evals import run_extraction as runner
from evals.dataset import load_dataset
from evals.synthetic import extraction as gen


def _gold(value="14.2", **kw):
    base = {
        "page": 1, "analyte": "Haemoglobin", "analyte_raw": "Haemoglobin", "value_raw": value,
        "value_num": value, "comparator": None, "qualitative": None, "unit_raw": "gm/dL",
        "unit_norm": "g/dL", "value_num_norm": value, "ref_range_raw": "13.0 - 17.0",
        "ref_low": "13.0", "ref_high": "17.0", "flag_as_printed": None,
        "effective_date": "2024-03-25", "date_precision": "day", "date_source": "report",
        "effective_time": None, "expected_reasons": [],
    }  # fmt: skip
    base.update(kw)
    return base


def _draft(value="14.2", *, status="validated", reasons=(), start=0, excerpt=None):
    excerpt = excerpt or f"Haemoglobin\t{value}\tgm/dL"
    return ObservationDraft(
        page_number=1, source_locator=f"p1:t0:r{start + 1}", source_excerpt=excerpt,
        char_start=start, char_end=start + len(excerpt), analyte_raw="Haemoglobin",
        analyte_name="Haemoglobin", analyte_match="alias_exact", value_raw=value,
        value_num=Decimal(value), unit_raw="gm/dL", unit_norm="g/dL",
        value_num_norm=Decimal(value), ref_range_raw="13.0 - 17.0", ref_low=Decimal("13.0"),
        ref_high=Decimal("17.0"), effective_date=dt.date(2024, 3, 25), date_precision="day",
        effective_date_source="report", review_status=status, review_reasons=tuple(reasons),
    )  # fmt: skip


def _doc(gold, kind="CL", traps=(), abstain=()):
    return {"doc_id": "P01-" + kind, "kind": kind, "expected_facts": gold, "traps": list(traps),
            "expected_abstentions": list(abstain)}  # fmt: skip


def _score(doc, drafts, text, abstentions=None):
    tally = runner.Tally()
    output = SimpleNamespace(observations=tuple(drafts), abstentions=abstentions or {})
    tally.add(doc, {1: text}, output)
    return tally.metrics()


def test_a_missed_fact_counts_against_every_field() -> None:
    text = "Haemoglobin\t14.2\tgm/dL"
    m = _score(_doc([_gold(), _gold("15.0", page=1, analyte="MCV", analyte_raw="MCV")]),
               [_draft()], text)  # fmt: skip
    assert m["entity_recall"] == 0.5 and m["entity_precision"] == 1.0
    assert m["fields"]["value_num"]["end_to_end_recall"] == 0.5
    assert m["fields"]["value_num"]["matched_pair_accuracy"] == 1.0  # diagnostic only
    assert m["fully_correct_recall"] == 0.5


def test_a_wrong_validated_value_is_a_silent_error() -> None:
    text = "Haemoglobin\t14.7\tgm/dL"
    m = _score(_doc([_gold("14.2")]), [_draft("14.7")], text)
    assert m["silent_error_rate"] == 1.0
    flagged = _score(_doc([_gold("14.2")]),
                     [_draft("14.7", status="needs_review", reasons=("ocr_unverified_source",))],
                     text)  # fmt: skip
    assert flagged["silent_error_rate"] is None  # no validated facts: nothing silent


def test_unlocated_text_is_unsupported_and_traps_are_counted() -> None:
    m = _score(_doc([_gold()]), [_draft()], "something else entirely")
    assert m["counts"]["unsupported_facts"] == 1
    trap = _score(_doc([], traps=[{"page": 1, "value_raw": "14.2"}]), [_draft()],
                  "Haemoglobin\t14.2\tgm/dL")  # fmt: skip
    assert trap["counts"]["trap_extractions"] == 1 and trap["counts"]["false_extractions"] == 1


def test_any_fact_from_a_family_page_is_a_wrong_subject() -> None:
    m = _score(_doc([], kind="FM"), [_draft()], "Haemoglobin\t14.2\tgm/dL")
    assert m["counts"]["wrong_subject_facts"] == 1


def test_expected_abstentions_are_checked() -> None:
    m = _score(_doc([], abstain=[{"page": 1, "reason": "multiple_subjects"}]), [], "",
               {"multiple_subjects": 1})  # fmt: skip
    assert m["abstention_correctness"] == 1.0


def test_duplicates_are_counted() -> None:
    text = "Haemoglobin\t14.2\tgm/dL\nHaemoglobin\t14.2\tgm/dL"
    m = _score(_doc([_gold()]), [_draft(), _draft(start=23)], text)
    assert m["counts"]["duplicates"] == 1


# --- dataset ----------------------------------------------------------------------------------


def test_generation_is_deterministic_and_raster_has_no_text_layer() -> None:
    truth_a, pdfs_a = gen.generate_dataset(7, 2)
    truth_b, pdfs_b = gen.generate_dataset(7, 2)
    assert truth_a == truth_b and pdfs_a == pdfs_b
    gen.verify_sources(truth_a, pdfs_a)  # native values in the text layer; raster has none
    raster = [d for p in truth_a for d in p["documents"] if d["render_mode"] == "raster"]
    assert raster and all(d["source_fixture"].endswith("-CL") for d in raster)
    assert all(gen.text_layer_chars(pdfs_a[d["doc_id"]]) == 0 for d in raster)


def test_verification_refuses_a_value_missing_from_the_pdf() -> None:
    truth, pdfs = gen.generate_dataset(7, 2)
    doc = truth[0]["documents"][0]
    doc["expected_facts"][0]["value_raw"] = "999.99"
    with pytest.raises(ValueError):
        gen.verify_sources(truth, pdfs)


def test_the_built_dataset_loads_with_its_frozen_digest() -> None:
    dataset = load_dataset(Path(__file__).resolve().parent.parent / "data/synthetic/extract_v0")
    assert dataset.manifest["reference_date"] == "2026-11-17"
    dev, held = set(dataset.manifest["split"]["dev"]), set(dataset.manifest["split"]["heldout"])
    assert dev and held and not dev & held


# --- protocol guards ------------------------------------------------------------------------


def test_heldout_refuses_without_a_freeze_and_after_it_ran(tmp_path) -> None:
    dataset = SimpleNamespace(version="x@1", manifest={"heldout_sha256": "h"})
    with pytest.raises(runner.EvaluationError):
        runner.check_heldout_allowed(dataset, tmp_path)
    (tmp_path / runner.HELDOUT_DONE).write_text("done")
    with pytest.raises(runner.EvaluationError, match="never re-run"):
        runner.check_heldout_allowed(dataset, tmp_path)


def test_heldout_refuses_when_versions_changed(tmp_path, monkeypatch) -> None:
    dataset = SimpleNamespace(version="x@1", manifest={"heldout_sha256": "h"})
    report = tmp_path / f"{runner._name('dev')}.json"
    report.write_text("{}")
    selection = {
        "versions": {"extractor_version": "old"},
        "dataset_version": "x@1",
        "heldout_sha256": "h",
        "dev_report_sha256": runner._sha256_file(report),
    }
    (tmp_path / runner.SELECTION).write_text(json.dumps(selection))
    with pytest.raises(runner.EvaluationError, match="changed since freeze"):
        runner.check_heldout_allowed(dataset, tmp_path)


def test_freeze_refuses_when_hard_gates_fail(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runner, "versions", lambda: {"v": "1"})
    report = {"versions": {"v": "1"}, "hard_gates_pass": False}
    (tmp_path / f"{runner._name('dev')}.json").write_text(json.dumps(report))
    dataset = SimpleNamespace(version="x@1", manifest={"heldout_sha256": "h"})
    with pytest.raises(runner.EvaluationError, match="hard gates"):
        runner.freeze(dataset, tmp_path)


# --- repository input checks (before any database access) -----------------------------------


def _record(**kw):
    base = dict(
        page_number=1, source_locator="p1:t0:r1", source_excerpt="Hb\t14.2", char_start=0,
        char_end=7, analyte_raw="Hb", analyte_name="Haemoglobin", analyte_match="alias_exact",
        value_raw="14.2", value_num=Decimal("14.2"), comparator=None, value_qualitative=None,
        unit_raw=None, unit_norm=None, value_num_norm=None, ref_range_raw=None, ref_low=None,
        ref_high=None, flag_as_printed=None, ref_range_comparison=None, specimen=None,
        effective_date=None, date_precision=None, date_raw=None, effective_date_source=None,
        date_page_number=None, date_char_start=None, date_char_end=None, effective_time=None,
        time_raw=None, review_status="validated", review_reasons=(), quality_notes=(),
    )  # fmt: skip
    base.update(kw)
    return repo.ObservationRecord(**base)


VERSIONS = {"extractor_version": "lab-rules-2", "normalizer_version": "norm-1",
            "reference_data_version": "ref-x", "date_policy_version": "dates-1"}  # fmt: skip


def _write(observations, versions=VERSIONS):
    def no_database(*_a, **_kw):
        raise AssertionError("the database must not be reached")

    return repo.record_extraction(
        uuid.uuid4(), uuid.uuid4(), observations=observations, versions=versions,
        reference_date=dt.date(2026, 11, 17), abstentions={}, report_date=None, duration_ms=1,
        detector=lambda facts: [], factory=no_database,  # type: ignore[arg-type]
    )  # fmt: skip


@pytest.mark.parametrize(
    "bad",
    [
        _record(review_status="verified"),  # only the review service sets human statuses
        _record(review_status="extracted"),  # transient, never stored
        _record(review_status="validated", review_reasons=("ambiguous_date",)),
        _record(review_status="needs_review"),  # needs reasons
        _record(review_reasons=("Free text!",), review_status="needs_review"),
        _record(char_start=9, char_end=3),
    ],
)
def test_extraction_writes_refuse_invalid_records_before_the_database(bad) -> None:
    with pytest.raises(ValueError):
        _write([bad])


def test_duplicate_locators_and_bad_versions_are_refused() -> None:
    with pytest.raises(ValueError):
        _write([_record(), _record()])
    with pytest.raises(ValueError):
        _write([_record()], versions={"extractor_version": "x"})


@pytest.mark.parametrize(
    ("decision", "values"),
    [("approve", None), ("correct", None), ("verify", {"x": 1}), ("reject", {"x": 1})],
)
def test_review_writes_refuse_invalid_decisions_before_the_database(decision, values) -> None:
    with pytest.raises(ValueError):
        repo.record_review_decision(
            uuid.uuid4(), uuid.uuid4(), decision=decision, corrected_values=values, note=None,
            detector=lambda facts: [],
        )  # fmt: skip


def test_gate_precedence() -> None:
    gate = repo.extraction_gate_refusal
    assert gate(has_pages=False, classified=False, identity_checked=False,
                identity_check="mismatch") == "not_parsed"  # fmt: skip
    assert gate(has_pages=True, classified=False, identity_checked=False,
                identity_check="mismatch") == "identity_mismatch"  # fmt: skip
    assert gate(has_pages=True, classified=False, identity_checked=False,
                identity_check="unknown") == "identity_not_checked"  # fmt: skip
    assert gate(has_pages=True, classified=False, identity_checked=True,
                identity_check="unknown") == "not_classified"  # fmt: skip
    assert gate(has_pages=True, classified=True, identity_checked=True,
                identity_check="unknown") is None  # fmt: skip
    assert repo.extraction_applies("lab_report") and not repo.extraction_applies("unknown")


def test_record_reprs_hide_evidence() -> None:
    shown = repr(_record(source_excerpt="Asha Fictional Hb 14.2", analyte_raw="Asha"))
    assert "Asha" not in shown
