"""Build a versioned synthetic dataset: ground truth, PDFs, split and manifest (Task 1.9).

    uv run python -m evals.synthetic.build --out data/synthetic/lab_v0

Writes (refuses to overwrite an existing dataset directory):
- ``ground_truth.json``  every patient, report, page and row (written before any PDF);
- ``pdfs/<report_id>.pdf``  one PDF per report;
- ``manifest.json``  dataset name and version, generator version, seed, the SHA-256 and size of
  every file, the patient split, and ``heldout_sha256``: one hash over the held-out patients'
  ground truth and PDF hashes. It is computed here, before any tuning, and re-checked whenever the
  dataset is loaded, so the held-out set is frozen.
"""

import argparse
import hashlib
import json
from pathlib import Path

from evals.synthetic.generator import (
    GENERATOR_VERSION,
    PatientTruth,
    generate,
    split_patients,
    to_json,
)
from evals.synthetic.render import render_report

DATASET_NAME = "lab_v0"
MANIFEST_VERSION = 1
DEFAULT_SEED = 20261116
DEFAULT_PATIENTS = 12


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def heldout_digest(ground_truth: list[dict], files: dict[str, dict], heldout: list[str]) -> str:
    """One hash over the held-out patients' ground truth and their PDFs' hashes."""
    selected = {
        "patients": [p for p in ground_truth if p["patient_key"] in set(heldout)],
        "pdfs": {
            path: entry["sha256"]
            for path, entry in sorted(files.items())
            if path.startswith("pdfs/") and Path(path).stem.split("-")[0] in set(heldout)
        },
    }
    return _sha256(_json_bytes(selected))


def build_dataset(out: Path, *, seed: int = DEFAULT_SEED, patients: int = DEFAULT_PATIENTS) -> dict:
    """Generate and write the dataset; returns the manifest."""
    if out.exists():
        raise FileExistsError(f"{out} already exists; a dataset is never overwritten")
    truth: tuple[PatientTruth, ...] = generate(seed, patients)
    ground_truth = to_json(truth)
    (out / "pdfs").mkdir(parents=True)
    files: dict[str, dict] = {}

    data = _json_bytes(ground_truth)  # ground truth first, then the PDFs drawn from it
    (out / "ground_truth.json").write_bytes(data)
    files["ground_truth.json"] = {"sha256": _sha256(data), "size": len(data)}
    for patient in truth:
        for report in patient.reports:
            pdf = render_report(patient, report)
            path = f"pdfs/{report.report_id}.pdf"
            (out / path).write_bytes(pdf)
            files[path] = {"sha256": _sha256(pdf), "size": len(pdf)}

    split = split_patients([p.patient_key for p in truth], seed)
    manifest = {
        "dataset": DATASET_NAME,
        "manifest_version": MANIFEST_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "patients": patients,
        "reports": sum(len(p.reports) for p in truth),
        "pages": sum(len(r.pages) for p in truth for r in p.reports),
        "synthetic": True,
        "split": split,
        "heldout_sha256": heldout_digest(ground_truth, files, split["heldout"]),
        "files": files,
    }
    (out / "manifest.json").write_bytes(_json_bytes(manifest))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic lab-report dataset.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--patients", type=int, default=DEFAULT_PATIENTS)
    args = parser.parse_args(argv)
    manifest = build_dataset(args.out, seed=args.seed, patients=args.patients)
    print(
        f"Dataset {manifest['dataset']}: {manifest['patients']} patients, "
        f"{manifest['reports']} reports, {manifest['pages']} pages; "
        f"dev {len(manifest['split']['dev'])} / held-out {len(manifest['split']['heldout'])} "
        f"patients; heldout_sha256 {manifest['heldout_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
