"""Load and verify a synthetic dataset (Task 1.10).

Loading recomputes the SHA-256 of every listed file and the held-out digest; any difference stops
the run. A tuned configuration can therefore never be evaluated on a silently changed held-out set.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from evals.synthetic.build import heldout_digest


class DatasetError(Exception):
    """The dataset is missing, changed or inconsistent."""


@dataclass(frozen=True)
class Dataset:
    root: Path
    manifest: dict
    ground_truth: list[dict]

    @property
    def version(self) -> str:
        return f"{self.manifest['dataset']}@{self.manifest['heldout_sha256'][:12]}"

    def patients(self, split: str) -> list[dict]:
        if split not in self.manifest["split"]:
            raise DatasetError(f"unknown split {split!r}")
        keys = set(self.manifest["split"][split])
        return [p for p in self.ground_truth if p["patient_key"] in keys]

    def pdf_path(self, report_id: str) -> Path:
        return self.root / "pdfs" / f"{report_id}.pdf"


def load_dataset(root: Path) -> Dataset:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise DatasetError(f"no manifest.json in {root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, entry in manifest["files"].items():
        path = root / relative
        if not path.is_file():
            raise DatasetError(f"missing file {relative}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["size"]:
            raise DatasetError(f"file {relative} differs from the manifest")
    ground_truth = json.loads((root / "ground_truth.json").read_text(encoding="utf-8"))
    digest = heldout_digest(ground_truth, manifest["files"], manifest["split"]["heldout"])
    if digest != manifest["heldout_sha256"]:
        raise DatasetError("the held-out split differs from its frozen digest")
    dev, heldout = set(manifest["split"]["dev"]), set(manifest["split"]["heldout"])
    if dev & heldout:
        raise DatasetError("a patient is in both splits")
    return Dataset(root, manifest, ground_truth)
