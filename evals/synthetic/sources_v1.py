"""New, never-evaluated source patients for the ``copilot_v1`` held-out split (Phase 5 remediation).

The ``copilot_v0`` held-out patients were evaluated and their failures inspected, so they are no
longer clean for a held-out test. ``copilot_v1`` therefore draws its held-out questions only from
patients generated here, with new seeds, by the existing generators (no generator logic changes):

- ``lab_v1``: the lab-report generator (``evals.synthetic.build``) with seed 20261119 and 6
  patients, written under the dataset name ``lab_v1`` (its own work and report directories). Both of
  its internal splits are ingested; **all** its patients belong to the copilot_v1 held-out split.
- ``longitudinal_v1``: 6 scenario patients from ``evals.synthetic.longitudinal.scenario_patient``
  with seed 20261119. Keys are renamed ``L``→``M`` (``M01``…) so they never collide with
  ``longitudinal_v0``. Stored through the same production write path as ``longitudinal_v0``
  (``evals.run_longitudinal.load_patient``).

Both are frozen at build (digests in their manifests) and never overwritten.

    uv run python -m evals.synthetic.sources_v1 build
    uv run python -m evals.run_retrieval ingest --dataset data/synthetic/lab_v1 --split dev
    uv run python -m evals.run_retrieval ingest --dataset data/synthetic/lab_v1 --split heldout
    uv run python -m evals.synthetic.sources_v1 load
"""

import argparse
import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any

LAB_DIR = Path("data/synthetic/lab_v1")
LONGITUDINAL_DIR = Path("data/synthetic/longitudinal_v1")
LONGITUDINAL_WORK = Path("evals/work/longitudinal_v1")
SEED = 20261119
PATIENTS = 6
LONGITUDINAL_DATASET = "longitudinal_v1"
_KEY = re.compile(r"\bL(\d{2})(?=-D|\b)")


def build_lab() -> dict:
    from evals.synthetic import build

    original = build.DATASET_NAME
    build.DATASET_NAME = "lab_v1"
    try:
        return build.build_dataset(LAB_DIR, seed=SEED, patients=PATIENTS)
    finally:
        build.DATASET_NAME = original


def _rename(value: Any) -> Any:
    """``L01``/``L01-D02``/``L01-D02#3`` -> ``M01``... in keys and values (not in page text)."""
    if isinstance(value, dict):
        return {_rename(k): (v if k == "page_text" else _rename(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename(v) for v in value]
    if isinstance(value, str):
        return _KEY.sub(r"M\1", value)
    return value


def build_longitudinal() -> dict:
    from evals.synthetic import longitudinal as gen

    if LONGITUDINAL_DIR.exists() and any(LONGITUDINAL_DIR.iterdir()):
        raise FileExistsError(f"{LONGITUDINAL_DIR} already exists; the dataset is frozen")
    rng = random.Random(SEED)
    patients = []
    for index in range(1, PATIENTS + 1):
        patient = gen.scenario_patient(index, rng)
        patient["truth"] = gen.derive_truth(patient)
        patients.append(_rename(patient))
    LONGITUDINAL_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"dataset": LONGITUDINAL_DATASET, "split": "heldout", "patients": patients},
        ensure_ascii=False,
        indent=1,
        sort_keys=True,
    ).encode("utf-8")
    (LONGITUDINAL_DIR / "heldout.json").write_bytes(payload)
    manifest = {
        "dataset": LONGITUDINAL_DATASET,
        "generator_version": gen.GENERATOR_VERSION,
        "seed": SEED,
        "reference_date": gen.REFERENCE_DATE.isoformat(),
        "split": {"heldout": [p["patient_key"] for p in patients]},
        "sha256": {"heldout": hashlib.sha256(payload).hexdigest()},
        "frozen_at_build": True,
    }
    (LONGITUDINAL_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def load_longitudinal() -> Path:
    """Store the patients through the production write path (once; reruns reuse the state)."""
    from app.extraction.reference import load_reference
    from app.services.reference_data import seed_reference
    from evals.run_longitudinal import load_patient
    from evals.synthetic.longitudinal import load_split

    path = LONGITUDINAL_WORK / "load_heldout.json"
    if path.exists():
        return path
    seed_reference()
    reference = load_reference()
    loaded = {
        p["patient_key"]: load_patient(p, reference)
        for p in load_split(LONGITUDINAL_DIR, "heldout")
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"patients": loaded}, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or load the copilot_v1 held-out sources.")
    parser.add_argument("stage", choices=("build", "load"))
    args = parser.parse_args(argv)
    if args.stage == "build":
        print(json.dumps({"lab_v1": build_lab()["heldout_sha256"],
                          "longitudinal_v1": build_longitudinal()["sha256"]}))  # fmt: skip
    else:
        print(load_longitudinal())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
