"""Offline evaluation of the rule-based classifier on ``doctype_v0`` (Task 2.7, ADR-036).

No database. Each PDF is validated and parsed by the project's Docling parser, exactly as an
upload would be, and ``classify_pages`` reads the IR's pages (the same text and ``has_tables`` the
``pages`` rows hold).

    uv run python -m evals.run_classification dev
    uv run python -m evals.run_classification freeze      # after the owner reviews the dev report
    uv run python -m evals.run_classification heldout     # once, after freeze

Protocol (as for retrieval, R1.8):
- ``dev`` evaluates the dev split only and writes ``classification_dev_rules_v<N>.{json,md}``.
  The rules are developed on dev results only.
- ``freeze`` records the rules version, the dataset version and held-out digest, and the dev
  report's SHA-256 in ``classification_selection.json`` (written once).
- ``heldout`` refuses unless that selection matches the current rules version, the dataset and the
  dev report; it parses the held-out documents for the first time, writes the held-out report and
  marks the split as used (``classification_heldout.done``). It never runs twice.

Metrics (measured, never estimated): accuracy (``unknown`` counts as wrong), accuracy on decided
documents, ``lab_report`` precision and recall, the confusion matrix (true ``lab_report`` /
``other`` against predicted ``lab_report`` / ``other`` / ``unknown``), the ``unknown`` rate, and a
per-kind breakdown. Reports hold document ids, labels, predictions, scores and rule ids: never
page text.

Parsed IRs are cached in ``evals/work/<dataset>/ir/`` (git-ignored), keyed by the PDF's SHA-256 and
the parser configuration, so repeated dev runs do not re-parse.
"""

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.ingestion.classification import (
    CLASSIFIER_RULES_VERSION,
    LAB_REPORT,
    OTHER,
    UNKNOWN,
    ClassifiablePage,
    classify_pages,
)
from evals import metrics
from evals.dataset import Dataset, load_dataset

DEFAULT_DATASET = Path("data/synthetic/doctype_v0")
REPORTS_DIR = Path("evals/reports")
WORK_DIR = Path("evals/work")
SELECTION = "classification_selection.json"
HELDOUT_DONE = "classification_heldout.done"
TRUE_TYPES = (LAB_REPORT, OTHER)
PREDICTED_TYPES = (LAB_REPORT, OTHER, UNKNOWN)

PageParser = Callable[[bytes], Sequence[ClassifiablePage]]


class EvaluationError(Exception):
    """The evaluation protocol refuses to run (missing freeze, changed rules or dataset)."""


@dataclass(frozen=True)
class Outcome:
    document_id: str
    kind: str
    variant: str
    true_type: str
    predicted: str
    score: float
    rule_ids: tuple[str, ...]


def documents(dataset: Dataset, split: str) -> list[dict]:
    return [doc for patient in dataset.patients(split) for doc in patient["documents"]]


def evaluate(dataset: Dataset, split: str, parse: PageParser) -> list[Outcome]:
    """Classify every document of ``split``; the dataset was verified when it was loaded."""
    outcomes = []
    for doc in documents(dataset, split):
        pages = parse(dataset.pdf_path(doc["document_id"]).read_bytes())
        result = classify_pages(pages)
        outcomes.append(
            Outcome(
                doc["document_id"],
                doc["kind"],
                doc["variant"],
                doc["true_type"],
                result.doc_type,
                result.score,
                result.rule_ids,
            )
        )
    return outcomes


def score(outcomes: Sequence[Outcome]) -> dict:
    """Accuracy, lab precision/recall, confusion matrix, unknown rate, per-kind counts."""
    if not outcomes:
        raise ValueError("no documents were evaluated")
    unexpected = {o.true_type for o in outcomes} - set(TRUE_TYPES)
    if unexpected:
        raise ValueError(f"unexpected true types: {sorted(unexpected)}")
    correct = [o.predicted == o.true_type for o in outcomes]
    decided = [o for o in outcomes if o.predicted != UNKNOWN]
    lab = metrics.binary_scores(
        [o.true_type == LAB_REPORT for o in outcomes], [o.predicted == LAB_REPORT for o in outcomes]
    )
    confusion = {
        true: {pred: 0 for pred in PREDICTED_TYPES} for true in TRUE_TYPES
    }  # true -> predicted -> count
    for o in outcomes:
        confusion[o.true_type][o.predicted] += 1
    by_kind: dict[str, Counter] = {}
    for o in outcomes:
        by_kind.setdefault(o.kind, Counter())[o.predicted] += 1
    return {
        "documents": len(outcomes),
        "accuracy": metrics.rate(correct),
        "decided_accuracy": (
            metrics.rate([o.predicted == o.true_type for o in decided]) if decided else None
        ),
        "unknown_rate": metrics.rate([o.predicted == UNKNOWN for o in outcomes]),
        "lab_report_precision": lab.precision if lab.true_positive + lab.false_positive else None,
        "lab_report_recall": lab.recall,
        "confusion": confusion,
        "by_kind": {kind: dict(sorted(counts.items())) for kind, counts in sorted(by_kind.items())},
    }


def _cell(value: float | int | None) -> str:
    if value is None:
        return "not measured"
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def to_markdown(dataset: Dataset, split: str, scores: dict, outcomes: Sequence[Outcome]) -> str:
    lines = [
        f"# Document classification: {split} split",
        "",
        f"- Dataset: `{dataset.version}`, split `{split}`, {scores['documents']} documents",
        f"- Classifier rules version: `{CLASSIFIER_RULES_VERSION}`",
        "- Score: deterministic rule score, not a probability (ADR-036 D3)",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for name in (
        "accuracy",
        "decided_accuracy",
        "unknown_rate",
        "lab_report_precision",
        "lab_report_recall",
    ):
        lines.append(f"| {name} | {_cell(scores[name])} |")
    lines += ["", "## Confusion matrix (rows: true, columns: predicted)", ""]
    lines += ["| true \\ predicted | " + " | ".join(PREDICTED_TYPES) + " |"]
    lines += ["|---|" + "---|" * len(PREDICTED_TYPES)]
    for true in TRUE_TYPES:
        row = scores["confusion"][true]
        lines.append(f"| {true} | " + " | ".join(str(row[p]) for p in PREDICTED_TYPES) + " |")
    lines += ["", "## By document kind (predicted counts)", ""]
    lines += ["| kind | " + " | ".join(PREDICTED_TYPES) + " |"]
    lines += ["|---|" + "---|" * len(PREDICTED_TYPES)]
    for kind, counts in scores["by_kind"].items():
        lines.append(
            f"| {kind} | " + " | ".join(str(counts.get(p, 0)) for p in PREDICTED_TYPES) + " |"
        )
    errors = [o for o in outcomes if o.predicted != o.true_type]
    lines += ["", f"## Misclassified or unknown ({len(errors)})", ""]
    lines += ["| document | kind / variant | true | predicted | rules |", "|---|---|---|---|---|"]
    lines += [
        f"| {o.document_id} | {o.kind} / {o.variant} | {o.true_type} | {o.predicted} | "
        f"{', '.join(o.rule_ids)} |"
        for o in errors
    ]
    return "\n".join(lines) + "\n"


def write_report(
    dataset: Dataset, split: str, outcomes: Sequence[Outcome], directory: Path
) -> tuple[Path, Path]:
    """Write ``classification_<split>_rules_v<N>.{json,md}``; never overwrites a report."""
    scores = score(outcomes)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"classification_{split}_rules_v{CLASSIFIER_RULES_VERSION}"
    json_path, md_path = directory / f"{name}.json", directory / f"{name}.md"
    if json_path.exists() or md_path.exists():
        raise FileExistsError(f"report {name} already exists")
    payload = {
        "dataset_version": dataset.version,
        "heldout_sha256": dataset.manifest["heldout_sha256"],
        "split": split,
        "rules_version": CLASSIFIER_RULES_VERSION,
        "metrics": scores,
        "outcomes": [o.__dict__ | {"rule_ids": list(o.rule_ids)} for o in outcomes],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(dataset, split, scores, outcomes), encoding="utf-8")
    return json_path, md_path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(dataset: Dataset, directory: Path) -> Path:
    """Record the dev-selected configuration once (rules version, dataset, dev report hash)."""
    selection = directory / SELECTION
    if selection.exists():
        raise EvaluationError("the classifier configuration is already frozen")
    dev_report = directory / f"classification_dev_rules_v{CLASSIFIER_RULES_VERSION}.json"
    if not dev_report.is_file():
        raise EvaluationError("no dev report for the current rules version; run `dev` first")
    payload = {
        "rules_version": CLASSIFIER_RULES_VERSION,
        "dataset_version": dataset.version,
        "heldout_sha256": dataset.manifest["heldout_sha256"],
        "dev_report_sha256": _sha256_file(dev_report),
    }
    selection.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return selection


def check_heldout_allowed(dataset: Dataset, directory: Path) -> None:
    """Refuse the held-out run unless the frozen selection matches the current state exactly."""
    if (directory / HELDOUT_DONE).exists():
        raise EvaluationError("the held-out split was already evaluated; it is never re-run")
    selection_path = directory / SELECTION
    if not selection_path.is_file():
        raise EvaluationError("no frozen configuration; run `freeze` after reviewing dev")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    dev_report = directory / f"classification_dev_rules_v{selection['rules_version']}.json"
    problems = []
    if selection["rules_version"] != CLASSIFIER_RULES_VERSION:
        problems.append("the classifier rules version changed since freeze")
    if selection["dataset_version"] != dataset.version:
        problems.append("the dataset version changed since freeze")
    if selection["heldout_sha256"] != dataset.manifest["heldout_sha256"]:
        problems.append("the held-out digest changed since freeze")
    if not dev_report.is_file() or _sha256_file(dev_report) != selection["dev_report_sha256"]:
        problems.append("the frozen dev report is missing or changed")
    if problems:
        raise EvaluationError("; ".join(problems))


def run_heldout(dataset: Dataset, directory: Path, parse: PageParser) -> tuple[Path, Path]:
    check_heldout_allowed(dataset, directory)
    (directory / HELDOUT_DONE).write_text(CLASSIFIER_RULES_VERSION + "\n", encoding="utf-8")
    return write_report(dataset, "heldout", evaluate(dataset, "heldout", parse), directory)


def docling_page_parser(cache_dir: Path) -> PageParser:
    """Validate and parse with the project's Docling parser; cache the IR per PDF and config."""
    from app.core.config import get_settings
    from app.ingestion.ir import dump_ir, load_ir
    from app.ingestion.parsing import DoclingParser
    from app.ingestion.validation import validate_pdf_bytes

    settings = get_settings()
    parser = DoclingParser.from_settings(settings)
    config = hashlib.sha256(parser.parser_info.model_dump_json().encode("utf-8")).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)

    def parse(data: bytes) -> Sequence[ClassifiablePage]:
        key = hashlib.sha256(data).hexdigest()
        path = cache_dir / f"{key}.{config[:12]}.ir.json"
        if path.is_file():
            return load_ir(path.read_bytes()).pages
        pdf = validate_pdf_bytes(
            data, max_bytes=settings.max_upload_bytes, max_pages=settings.max_pages
        )
        document = parser.parse(pdf).document
        path.write_bytes(dump_ir(document))
        return document.pages

    return parse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the rule-based document classifier.")
    parser.add_argument("command", choices=("dev", "freeze", "heldout"))
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--reports", type=Path, default=None, help="report directory")
    args = parser.parse_args(argv)
    dataset = load_dataset(args.dataset)  # verifies every file hash and the held-out digest
    name = dataset.manifest["dataset"]
    directory = args.reports or REPORTS_DIR / name
    try:
        if args.command == "freeze":
            print(f"Frozen: {freeze(dataset, directory)}")
            return 0
        parse = docling_page_parser(WORK_DIR / name / "ir")
        if args.command == "heldout":
            paths = run_heldout(dataset, directory, parse)
        else:
            paths = write_report(dataset, "dev", evaluate(dataset, "dev", parse), directory)
    except EvaluationError as exc:
        print(f"Refused: {exc}")
        return 1
    print("Reports: " + ", ".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
