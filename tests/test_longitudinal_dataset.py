"""Tests of the synthetic longitudinal dataset generator (evals/synthetic/longitudinal.py)."""

import json
import random
from pathlib import Path

import pytest

from evals.synthetic import longitudinal as gen


def _patients() -> list[dict]:
    rng = random.Random(gen.SEED)
    return [gen.scenario_patient(i, rng) for i in range(1, 17)]


def test_generation_is_deterministic() -> None:
    assert json.dumps(_patients(), sort_keys=True) == json.dumps(_patients(), sort_keys=True)


def test_every_span_is_exact() -> None:
    for patient in _patients():
        for doc in patient["documents"]:
            text = doc["page_text"]
            for o in doc["observations"]:
                row = "\t".join(
                    (o["analyte_raw"], o["value_raw"], o["unit_raw"], o["ref_range_raw"])
                )
                assert text[o["char_start"] : o["char_end"]] == row
            for d in doc["dates"].values():
                assert text[d["char_start"] : d["char_end"]] == d["raw"]


def test_every_scenario_patient_covers_every_case() -> None:
    for p in _patients():
        t = p["truth"]
        facts = t["facts"].values()
        assert len(t["exact_pairs"]) == 4 and len(t["possible_pairs"]) == 2
        assert len(t["conflict_pairs"]) == 1
        assert t["excluded_mismatch_documents"] == 1
        assert {"rejected", "corrected"} <= {f["status"] for f in facts}
        assert any(f["precision"] == "month" for f in facts)
        assert any(f["date"] is None for f in facts) and t["undated_facts"]
        assert any(f["identity_unknown"] for f in facts)
        assert any(f["date_source"] == "report" for f in facts)
        assert any(f["date_source"] == "collection" for f in facts)
        ambiguous = [f for f in facts if f["doc_key"].endswith("D11")]
        expected = "verified" if int(p["patient_key"][1:]) % 2 else "needs_review"
        assert [f["status"] for f in ambiguous] == [expected]
        units = {(pt["unit"]) for pt in t["trends"]["Haemoglobin"]}
        assert units == {"g/dL", "mmol/L"}  # the non-convertible unit is its own series
        assert {d["doc_type"] for d in p["documents"]} == {"lab_report", "other"}
        assert p["upload_order"] != [d["doc_key"] for d in p["documents"]]


def test_conversion_values_are_exact_decimals() -> None:
    p = _patients()[0]
    converted = [f for f in p["truth"]["facts"].values() if f["doc_key"].endswith("D09")]
    sugar = next(f for f in converted if f["analyte"] == "Fasting Blood Sugar")
    raw = next(o for o in p["documents"][8]["observations"] if o["unit_raw"] == "mmol/L")
    assert sugar["unit"] == "mg/dL"
    from decimal import Decimal

    assert Decimal(sugar["value"]) == Decimal(raw["value_raw"]) * Decimal("18.0156")


def test_build_freezes_digests_and_refuses_to_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "ds"
    manifest = gen.build(target)
    assert set(manifest["split"]["heldout"]) == set(gen.HELDOUT)
    assert not set(manifest["split"]["dev"]) & set(manifest["split"]["heldout"])
    assert gen.PERFORMANCE_PATIENT in manifest["split"]["dev"]
    assert len(gen.load_split(target, "heldout")) == 5
    with pytest.raises(FileExistsError):
        gen.build(target)
    tampered = (target / "heldout.json").read_bytes().replace(b"Trendwala", b"Trendwalx", 1)
    (target / "heldout.json").write_bytes(tampered)
    with pytest.raises(ValueError):
        gen.load_split(target, "heldout")
