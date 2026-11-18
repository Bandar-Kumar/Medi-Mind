"""Copilot evaluation on ``copilot_v1`` (Phase 5 remediation; docs/PHASE_5_PLAN.md §12; ADR-041).

    uv run python -m evals.run_copilot_v1 prepare     # extract lab_v1 documents (idempotent)
    uv run python -m evals.run_copilot_v1 dev         # repeatable while developing
    uv run python -m evals.run_copilot_v1 freeze      # configuration + code digest, written once
    uv run python -m evals.run_copilot_v1 heldout     # ONCE, refuses if anything changed

Scoring, the independent claim check and the attack suite are exactly those of
``evals.run_copilot`` (unchanged). Only the sources differ:

- dev: ``longitudinal_v0`` (dev + held-out scenario patients) and ``lab_v0`` (both splits);
- held-out: ``longitudinal_v1`` and ``lab_v1`` (new patients, never evaluated before).

The ``copilot_v0`` artifacts are not read or written here.
"""

import argparse
import datetime as dt
import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from evals import run_copilot as base
from evals.synthetic import copilot as v0
from evals.synthetic import copilot_v1 as gen

DATASET_DIR = Path("data/synthetic") / gen.DATASET
REPORTS_DIR = Path("evals/reports") / gen.DATASET
SELECTION_FILE = REPORTS_DIR / "copilot_v1_selection.json"
HELDOUT_DONE = REPORTS_DIR / "copilot_v1_heldout.done"
CODE_FILES = (*base.CODE_FILES, "app/answering/vocabulary.py")
# Copilot retrieval configuration under test. The threshold is the lexical abstention threshold
# chosen on development data only (evals/reports/lab_v0/lexical_abstention_dev.md); None = none.
RETRIEVAL_MODE = "lexical"
LEXICAL_THRESHOLD: float | None = None

SOURCES = {
    "dev": {
        "longitudinal": [
            (v0.LONGITUDINAL, Path("data/synthetic/longitudinal_v0"), s,
             Path(f"evals/work/longitudinal_v0/load_{s}.json"))
            for s in ("dev", "heldout")
        ],
        "lab": [
            (v0.LAB, Path("data/synthetic/lab_v0"), s, Path(f"evals/work/lab_v0/ingest_{s}.json"))
            for s in ("dev", "heldout")
        ],
    },
    "heldout": {
        "longitudinal": [
            (gen.LONGITUDINAL_V1, Path("data/synthetic/longitudinal_v1"), "heldout",
             Path("evals/work/longitudinal_v1/load_heldout.json"))
        ],
        "lab": [
            (gen.LAB_V1, Path("data/synthetic/lab_v1"), s,
             Path(f"evals/work/lab_v1/ingest_{s}.json"))
            for s in ("dev", "heldout")
        ],
    },
}  # fmt: skip


class Context(base.Context):
    """The same key maps as ``run_copilot.Context``, built from the v1 sources of one split."""

    def __init__(self, split: str) -> None:  # noqa: D107 (does not call the v0 constructor)
        from evals.dataset import load_dataset
        from evals.synthetic.longitudinal import load_split

        self.patients = {}
        self.doc_key = {}
        self.owner = {}
        self.fact_key = {}
        self.long_truth = {}
        self.lab_reports = {}
        for source, directory, part, work in SOURCES[split]["longitudinal"]:
            state = json.loads(work.read_text(encoding="utf-8"))
            for key, entry in state["patients"].items():
                self.patients[(source, key)] = uuid.UUID(entry["patient_id"])
                for doc_key, doc_id in entry["documents"].items():
                    self.doc_key[doc_id] = doc_key
                    self.owner[doc_id] = (source, key)
                for fact_key, fact_id in entry["facts"].items():
                    self.fact_key[fact_id] = fact_key
            self.long_truth.update({p["patient_key"]: p for p in load_split(directory, part)})
        for source, directory, part, work in SOURCES[split]["lab"]:
            state = json.loads(work.read_text(encoding="utf-8"))
            for key, patient_id in state["patients"].items():
                self.patients[(source, key)] = uuid.UUID(patient_id)
            for report_id, entry in state["reports"].items():
                self.doc_key[entry["document_id"]] = report_id
                self.owner[entry["document_id"]] = (source, entry["patient_key"])
            dataset = load_dataset(directory)
            self.lab_reports.update(
                {r["report_id"]: r for p in dataset.patients(part) for r in p["reports"]}
            )


def code_digest() -> str:
    import hashlib

    digest = hashlib.sha256()
    for name in CODE_FILES:
        digest.update(name.encode() + b"\0" + (base.ROOT / name).read_bytes() + b"\0")
    return digest.hexdigest()


def _retrieval(threshold: float | None) -> tuple[Any, dict[str, Any]]:
    from app.core.config import get_settings
    from app.retrieval.evidence import RetrievalConfig, RetrievalMode
    from app.services.copilot import RetrievalDeps

    settings = get_settings()
    config = RetrievalConfig(
        mode=RetrievalMode(RETRIEVAL_MODE),
        candidates=settings.retrieval_candidates,
        rrf_k=settings.retrieval_rrf_k,
        rerank_top=settings.retrieval_rerank_top,
        top_k=settings.retrieval_top_k,
        abstain_threshold=threshold,
    )
    return RetrievalDeps(config, None, None), {
        "retrieval_mode": RETRIEVAL_MODE,
        "retrieval_abstain_threshold": threshold,
        "retrieval_abstain_threshold_hex": None if threshold is None else float(threshold).hex(),
        "retrieval_top_k": config.top_k,
        "retrieval_candidates": config.candidates,
    }


def _config(described: dict[str, Any]) -> dict[str, Any]:
    from app.answering.compose import COMPOSER_VERSION
    from app.answering.context import PROMPT_VERSION
    from app.answering.vocabulary import VOCABULARY_VERSION
    from app.extraction.reference import load_reference

    return {
        "generator": "extractive",
        "composer_version": COMPOSER_VERSION,
        "prompt_version": PROMPT_VERSION,
        "vocabulary_version": VOCABULARY_VERSION,
        "reference_data_version": load_reference().version,
        "code_sha256": code_digest(),
        "today": base.TODAY.isoformat(),
        **described,
    }


def load_questions(split: str) -> list[dict]:
    return v0.load_questions(DATASET_DIR, split)  # digest-checked


def evaluate(split: str, threshold: float | None) -> dict[str, Any]:
    questions = load_questions(split)
    manifest = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))
    context = Context(split)
    retrieval, described = _retrieval(threshold)
    started = time.perf_counter()
    observed = base.run_questions(questions, context, retrieval=retrieval)
    seconds = round(time.perf_counter() - started, 1)
    scored = base.score(questions, observed)
    attacks = base.run_attacks(questions, context, retrieval)
    scored["metrics"]["invalid_citation_acceptance"] = attacks["invalid_citation_acceptance"]
    scored["metrics"]["unrecognised_abstention_rate"] = base._rate(
        [o.status == v0.INSUFFICIENT and not o.claims
         for q, o in zip(questions, observed, strict=True)
         if q["kind"] in ("unknown_test", "document_unknown")]
    )  # fmt: skip
    failures = [
        {
            "question_id": q["question_id"],
            "kind": q["kind"],
            "expect": [q["expect_status"], q["expect_route"], q["expect_intent"]],
            "got": [o.status, o.route, o.intent],
            "failed_checks": base._failed_checks(r),
        }
        for q, o, r in zip(questions, observed, scored["rows"], strict=True)
        if not r["correct"]
    ]
    return {
        "dataset": gen.DATASET,
        "split": split,
        "dataset_sha256": manifest["sha256"][split],
        "heldout_sha256": manifest["sha256"]["heldout"],
        "questions": len(questions),
        "config": _config(described),
        "metrics": scored["metrics"],
        "targets": base.target_results(scored["metrics"]),
        "by_kind": scored["by_kind"],
        "attacks": attacks,
        "tokens": {"input": 0, "output": 0, "note": "extractive generator: no model, no tokens"},
        "cost_usd": 0.0,
        "run_seconds": seconds,
        "failures": failures,
        "failure_kinds": dict(Counter(f["kind"] for f in failures)),
    }


def _write(report: dict[str, Any], name: str, title: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{name}.json"
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    (REPORTS_DIR / f"{name}.md").write_text(base._markdown(report, title), encoding="utf-8")
    return path


def prepare() -> dict[str, int]:
    """Production rule-based extraction on every lab_v1 document (idempotent; counts only)."""
    from app.core.config import get_settings
    from app.core.errors import DocumentProcessingError
    from app.services.extract_document import extract_document

    settings = get_settings()
    counts: Counter[str] = Counter()
    for part in ("dev", "heldout"):
        state = json.loads(
            Path(f"evals/work/lab_v1/ingest_{part}.json").read_text(encoding="utf-8")
        )
        for _, entry in sorted(state["reports"].items()):
            patient_id = uuid.UUID(state["patients"][entry["patient_key"]])
            try:
                extract_document(settings, patient_id, uuid.UUID(entry["document_id"]))
                counts["extracted"] += 1
            except DocumentProcessingError as exc:
                counts[exc.kind] += 1
    return dict(counts)


def dev() -> Path:
    if HELDOUT_DONE.exists():
        raise SystemExit(
            "The copilot_v1 held-out split was already evaluated; dev runs are closed."
        )
    return _write(
        evaluate("dev", LEXICAL_THRESHOLD), "copilot_v1_dev", "Phase 5 copilot: copilot_v1 dev"
    )


def freeze() -> Path:
    if SELECTION_FILE.exists():
        raise SystemExit("copilot_v1_selection.json already exists; it is never replaced.")
    dev_path = REPORTS_DIR / "copilot_v1_dev.json"
    data = dev_path.read_bytes()
    report = json.loads(data)
    if report["config"]["code_sha256"] != code_digest():
        raise SystemExit("The code changed since the last dev run; rerun `dev` before freezing.")
    selection = {
        "config": report["config"],
        "dev_report_sha256": base._sha256(data),
        "dataset_heldout_sha256": report["heldout_sha256"],
        "dev_metrics": report["metrics"],
        "frozen_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    with open(SELECTION_FILE, "x", encoding="utf-8") as handle:
        handle.write(json.dumps(selection, indent=2, sort_keys=True, default=str) + "\n")
    return SELECTION_FILE


def heldout() -> Path:
    if HELDOUT_DONE.exists():
        raise SystemExit("The copilot_v1 held-out split was already evaluated once.")
    selection_bytes = SELECTION_FILE.read_bytes()
    selection = json.loads(selection_bytes)
    manifest = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))
    if manifest["sha256"]["heldout"] != selection["dataset_heldout_sha256"]:
        raise SystemExit("The held-out questions differ from the frozen digest.")
    hexed = selection["config"]["retrieval_abstain_threshold_hex"]
    threshold = None if hexed is None else float.fromhex(hexed)
    current = _config(_retrieval(threshold)[1])
    changed = [k for k, v in selection["config"].items() if current.get(k) != v]
    if changed:
        raise SystemExit("Configuration or code changed since the freeze: " + ", ".join(changed))
    report = evaluate("heldout", threshold)
    report["config"]["copilot_v1_selection_sha256"] = base._sha256(selection_bytes)
    path = _write(report, "copilot_v1_heldout", "Phase 5 copilot: copilot_v1 held-out (single run)")
    HELDOUT_DONE.write_text(
        json.dumps({"copilot_v1_selection_sha256": base._sha256(selection_bytes)}) + "\n",
        encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 5 copilot evaluation on copilot_v1.")
    parser.add_argument("stage", choices=("prepare", "dev", "freeze", "heldout"))
    args = parser.parse_args(argv)
    action = {"prepare": prepare, "dev": dev, "freeze": freeze, "heldout": heldout}[args.stage]
    result = action()
    print(json.dumps(result, sort_keys=True) if isinstance(result, dict) else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
