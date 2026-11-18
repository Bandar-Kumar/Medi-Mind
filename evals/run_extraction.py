"""Offline evaluation of lab extraction on ``extract_v0`` (Phase 3; docs/PHASE_3_PLAN.md §12).

No database. Each PDF is validated and parsed by the project's Docling parser exactly as an
upload would be (IRs cached in ``evals/work/<dataset>/ir/``), and the pure extractor reads the IR
with the IR page texts as the stored texts.

    uv run python -m evals.run_extraction dev [--no-write]
    uv run python -m evals.run_extraction freeze
    uv run python -m evals.run_extraction heldout      # once, after freeze

Modes, both always computed:
- **component**: the extractor on every gold ``lab_report`` document;
- **system**: classifier v2, identity v2 against the patient's own profile and against a swapped
  profile (the next patient of the same split), the real extraction gate
  (``extraction_gate_refusal``), then the extractor. A swapped profile with outcome ``mismatch``
  must give zero facts; a swapped ``match`` or ``unknown`` (documented identity limits) is
  reported as ``identity_limit_extractions``.

Metrics are end to end: every gold fact counts, so a missed fact is a field error (§12.3). The
matched-pair accuracy is a diagnostic only. ``validated`` means only that automated checks passed;
the reports say so and claim no clinical accuracy. Reports hold ids, fields and codes; the
synthetic values are fictional.

Protocol: tune on dev only; ``freeze`` records the extractor, normalizer, reference and date
policy versions, the extractor source digest, the dataset version, the held-out digest, the dev
report hash and the pre-registered targets, and refuses if the hard gates fail on dev (§12.4);
``heldout`` refuses on any difference, writes its marker before evaluating, and never runs twice.
"""

import argparse
import datetime as dt
import hashlib
import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path

from app.extraction.lab.extract import extract_lab_observations
from app.extraction.normalize.numbers import text_key
from app.extraction.reference import load_reference
from app.extraction.schemas import NEEDS_REVIEW, VALIDATED, ObservationDraft
from app.extraction.versions import (
    DATE_POLICY_VERSION,
    EXTRACTOR_VERSION,
    NORMALIZER_VERSION,
)
from app.ingestion.classification import CLASSIFIER_RULES_VERSION, classify_pages
from app.ingestion.identity import IDENTITY_RULES_VERSION, check_identity
from app.ingestion.ir import ParsedDocument
from app.records.facts import extraction_applies, extraction_gate_refusal
from evals.dataset import Dataset, load_dataset

DEFAULT_DATASET = Path("data/synthetic/extract_v0")
REPORTS_DIR = Path("evals/reports")
WORK_DIR = Path("evals/work")
SELECTION = "extraction_selection.json"
HELDOUT_DONE = "extraction_heldout.done"
EXTRACTION_SOURCE = Path("app/extraction")

KEY_FIELDS = (
    "value_num",
    "comparator",
    "qualitative",
    "unit_raw",
    "unit_norm",
    "value_num_norm",
    "ref_low",
    "ref_high",
    "flag_as_printed",
    "effective_date",
)
ALL_FIELDS = (*KEY_FIELDS, "date_precision", "date_source", "effective_time")

HARD_GATES = ("unsupported_facts", "trap_extractions", "wrong_subject_facts", "mismatch_facts")
DEV_TARGETS = {  # pre-registered (D-12)
    "silent_error_rate": ("<=", 0.01),
    "entity_precision": (">=", 0.97),
    "native_entity_recall": (">=", 0.95),
    "native_fully_correct_recall": (">=", 0.90),
}
FOOTNOTE = (
    "Synthetic, fictional data; rules and data share one author; not a blind test; no clinical "
    "accuracy is claimed. 'validated' (auto-checked) means only that automated checks passed."
)

Parser = Callable[[bytes], ParsedDocument]


class EvaluationError(Exception):
    """The protocol refuses to run (missing freeze, changed versions or dataset, failed gates)."""


# --- parsing ----------------------------------------------------------------------------------


def docling_parser(cache_dir: Path) -> Parser:
    from app.core.config import get_settings
    from app.ingestion.ir import dump_ir, load_ir
    from app.ingestion.parsing import DoclingParser
    from app.ingestion.validation import validate_pdf_bytes

    settings = get_settings()
    parser = DoclingParser.from_settings(settings)
    config = hashlib.sha256(parser.parser_info.model_dump_json().encode("utf-8")).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)

    def parse(data: bytes) -> ParsedDocument:
        path = cache_dir / f"{hashlib.sha256(data).hexdigest()}.{config[:12]}.ir.json"
        if path.is_file():
            return load_ir(path.read_bytes())
        pdf = validate_pdf_bytes(
            data, max_bytes=settings.max_upload_bytes, max_pages=settings.max_pages
        )
        document = parser.parse(pdf).document
        path.write_bytes(dump_ir(document))
        return document

    return parse


# --- matching and scoring ---------------------------------------------------------------------


def _dec(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def predicted_fields(o: ObservationDraft) -> dict:
    return {
        "value_num": o.value_num,
        "comparator": o.comparator,
        "qualitative": o.value_qualitative,
        "unit_raw": o.unit_raw,
        "unit_norm": o.unit_norm,
        "value_num_norm": o.value_num_norm,
        "ref_low": o.ref_low,
        "ref_high": o.ref_high,
        "flag_as_printed": o.flag_as_printed,
        "effective_date": o.effective_date.isoformat() if o.effective_date else None,
        "date_precision": o.date_precision,
        "date_source": o.effective_date_source,
        "effective_time": o.effective_time.isoformat() if o.effective_time else None,
    }


def gold_fields(g: dict) -> dict:
    return {
        "value_num": _dec(g["value_num"]),
        "comparator": g["comparator"],
        "qualitative": g["qualitative"],
        "unit_raw": g["unit_raw"],
        "unit_norm": g["unit_norm"],
        "value_num_norm": _dec(g["value_num_norm"]),
        "ref_low": _dec(g["ref_low"]),
        "ref_high": _dec(g["ref_high"]),
        "flag_as_printed": g["flag_as_printed"],
        "effective_date": g["effective_date"],
        "date_precision": g["date_precision"],
        "date_source": g["date_source"],
        "effective_time": g["effective_time"],
    }


def _key(page: int, analyte: str | None, raw: str) -> tuple[int, str]:
    return page, analyte or "raw:" + text_key(raw)


def match(gold: Sequence[dict], predicted: Sequence[ObservationDraft]) -> list[tuple]:
    """One-to-one pairs (gold index or None, predicted index or None); exact value first."""
    free = set(range(len(predicted)))
    pairs: list[tuple] = []
    for gi, g in enumerate(gold):
        key = _key(g["page"], g["analyte"], g["analyte_raw"])
        same = [
            pi
            for pi in sorted(free)
            if _key(
                predicted[pi].page_number, predicted[pi].analyte_name, predicted[pi].analyte_raw
            )
            == key
        ]
        best = next((pi for pi in same if predicted[pi].value_raw == g["value_raw"]), None)
        if best is None and same:
            best = same[0]
        if best is not None:
            free.discard(best)
        pairs.append((gi, best))
    pairs.extend((None, pi) for pi in sorted(free))
    return pairs


class Tally:
    """Counts for one group of documents (all, native, raster, or one kind)."""

    def __init__(self) -> None:
        self.c: Counter = Counter()
        self.field_correct: Counter = Counter()
        self.field_predicted: Counter = Counter()
        self.reasons: Counter = Counter()
        self.abstentions: Counter = Counter()

    def add(self, doc: dict, page_texts: dict[int, str], output) -> None:
        gold = doc["expected_facts"]
        predicted = list(output.observations)
        self.c["documents"] += 1
        self.c["gold_facts"] += len(gold)
        self.c["predicted_facts"] += len(predicted)
        self.abstentions.update(output.abstentions)
        for o in predicted:
            self.reasons.update(o.review_reasons)
            self.c[o.review_status] += 1
            if page_texts.get(o.page_number, "")[o.char_start : o.char_end] != o.source_excerpt:
                self.c["unsupported_facts"] += 1
            elif o.value_raw not in o.source_excerpt or o.analyte_raw not in o.source_excerpt:
                self.c["unsupported_facts"] += 1
        traps = {(t["page"], t["value_raw"]) for t in doc["traps"]}
        self.c["trap_extractions"] += sum((o.page_number, o.value_raw) in traps for o in predicted)
        if doc["kind"] == "FM":
            self.c["wrong_subject_facts"] += len(predicted)
        for item in doc["expected_abstentions"]:
            self.c["expected_abstentions"] += 1
            self.c["expected_abstentions_seen"] += item["reason"] in output.abstentions
        matched_keys: set = set()
        for gi, pi in match(gold, predicted):
            if gi is not None:
                g = gold[gi]
                expected = gold_fields(g)
                if pi is None:
                    self.c["missed_facts"] += 1
                    continue
                o = predicted[pi]
                got = predicted_fields(o)
                self.c["matched_facts"] += 1
                matched_keys.add((o.page_number, o.analyte_name, o.value_raw))
                correct = {f: got[f] == expected[f] for f in ALL_FIELDS}
                for f in ALL_FIELDS:
                    self.field_correct[f] += correct[f]
                    self.field_predicted[f] += 1
                key_ok = all(correct[f] for f in KEY_FIELDS)
                self.c["fully_correct_facts"] += key_ok
                self.c["provenance_correct"] += (
                    o.page_number == g["page"]
                    and page_texts.get(o.page_number, "")[o.char_start : o.char_end]
                    == o.source_excerpt
                    and g["value_raw"] in o.source_excerpt
                )
                if o.review_status == VALIDATED and not key_ok:
                    self.c["silent_errors"] += 1
                expected_reasons = set(g["expected_reasons"])
                self.c["expected_reasons"] += len(expected_reasons)
                self.c["expected_reasons_seen"] += len(expected_reasons & set(o.review_reasons))
            else:
                o = predicted[pi]
                self.c["false_extractions"] += 1
                if (o.page_number, o.analyte_name, o.value_raw) in matched_keys:
                    self.c["duplicates"] += 1
                if o.review_status == VALIDATED:
                    self.c["silent_errors"] += 1

    def metrics(self) -> dict:
        c = self.c

        def ratio(a: str | int, b: str | int) -> float | None:
            top = c[a] if isinstance(a, str) else a
            bottom = c[b] if isinstance(b, str) else b
            return round(top / bottom, 4) if bottom else None

        fields = {}
        for f in ALL_FIELDS:
            correct = self.field_correct[f]
            precision = ratio(correct, self.field_predicted[f])
            recall = ratio(correct, "gold_facts")
            f1 = (
                round(2 * precision * recall / (precision + recall), 4)
                if precision and recall
                else None
            )
            fields[f] = {
                "end_to_end_recall": recall,
                "precision": precision,
                "f1": f1,
                "matched_pair_accuracy": ratio(correct, "matched_facts"),
            }
        precision = ratio("matched_facts", "predicted_facts")
        recall = ratio("matched_facts", "gold_facts")
        return {
            "counts": dict(sorted(c.items())),
            "entity_precision": precision,
            "entity_recall": recall,
            "entity_f1": (
                round(2 * precision * recall / (precision + recall), 4)
                if precision and recall
                else None
            ),
            "fully_correct_recall": ratio("fully_correct_facts", "gold_facts"),
            "provenance_accuracy": ratio("provenance_correct", "matched_facts"),
            "silent_error_rate": ratio("silent_errors", VALIDATED),
            "false_extraction_rate": ratio("false_extractions", "predicted_facts"),
            "duplicate_rate": ratio("duplicates", "predicted_facts"),
            "needs_review_rate": ratio(NEEDS_REVIEW, "predicted_facts"),
            "expected_reason_recall": ratio("expected_reasons_seen", "expected_reasons"),
            "abstention_correctness": ratio("expected_abstentions_seen", "expected_abstentions"),
            "fields": fields,
            "review_reasons": dict(sorted(self.reasons.items())),
            "abstentions": dict(sorted(self.abstentions.items())),
        }


# --- evaluation -------------------------------------------------------------------------------


def evaluate(dataset: Dataset, split: str, parse: Parser) -> dict:
    reference = load_reference()
    reference_date = dt.date.fromisoformat(dataset.manifest["reference_date"])
    patients = sorted(dataset.patients(split), key=lambda p: p["patient_key"])
    groups: dict[str, Tally] = defaultdict(Tally)
    system: Counter = Counter()
    extract_seconds = 0.0
    pages_seen = 0
    per_document = []
    for index, patient in enumerate(patients):
        swapped = patients[(index + 1) % len(patients)]
        for doc in patient["documents"]:
            parsed = parse(dataset.pdf_path(doc["doc_id"]).read_bytes())
            texts = {p.page_number: p.text for p in parsed.pages}
            started = time.perf_counter()
            output = extract_lab_observations(
                parsed, texts, reference_date=reference_date, reference=reference
            )
            extract_seconds += time.perf_counter() - started
            pages_seen += len(parsed.pages)
            raster = doc["render_mode"] == "raster"
            if parsed.pages and raster:
                ocr = all(p.parse_method.value == "ocr" for p in parsed.pages)
                system["raster_documents"] += 1
                system["raster_not_ocr"] += not ocr
            # component mode: gold lab reports only
            if doc["doc_type"] == "lab_report":
                for name in ("all", "raster" if raster else "native", f"kind:{doc['kind']}"):
                    groups[name].add(doc, texts, output)
            # system mode: classifier, identity (own and swapped), gate, extractor
            doc_type = classify_pages(parsed.pages).doc_type
            system[f"classified:{doc['doc_type']}->{doc_type}"] += 1
            for profile, label in ((patient, "own"), (swapped, "swapped")):
                outcome = check_identity(
                    parsed.pages,
                    profile["display_name"],
                    dt.date.fromisoformat(profile["date_of_birth"]),
                ).outcome
                system[f"identity:{label}:{outcome}"] += 1
                refusal = extraction_gate_refusal(
                    has_pages=bool(parsed.pages),
                    classified=True,
                    identity_checked=True,
                    identity_check=outcome,
                )
                written = 0
                if refusal is None and extraction_applies(doc_type):
                    written = len(output.observations)
                if label == "swapped":
                    if outcome == "mismatch":
                        system["mismatch_documents"] += 1
                        system["mismatch_facts"] += written
                        system["mismatch_blocked"] += refusal is not None
                    else:
                        system["identity_limit_extractions"] += written
                else:
                    system["own_facts_written"] += written
                    if doc["doc_type"] == "lab_report":
                        system["own_gold_facts"] += len(doc["expected_facts"])
                        system["own_unknown_documents"] += outcome == "unknown"
                        if outcome == "unknown":
                            system["own_unknown_extracted"] += written > 0
                        if not extraction_applies(doc_type):
                            system["classifier_induced_misses"] += len(doc["expected_facts"])
                    elif written:
                        system["non_lab_facts_written"] += written
            per_document.append(
                {
                    "doc_id": doc["doc_id"],
                    "kind": doc["kind"],
                    "facts": len(output.observations),
                    "needs_review": output.needs_review_count,
                    "abstentions": output.abstentions,
                    "predicted_doc_type": doc_type,
                }
            )
    metrics = {name: tally.metrics() for name, tally in sorted(groups.items())}
    counts = metrics["all"]["counts"]
    hard = {
        "unsupported_facts": counts.get("unsupported_facts", 0),
        "trap_extractions": counts.get("trap_extractions", 0),
        "wrong_subject_facts": counts.get("wrong_subject_facts", 0),
        "mismatch_facts": system["mismatch_facts"],
    }
    targets = {
        "silent_error_rate": metrics["all"]["silent_error_rate"],
        "entity_precision": metrics["all"]["entity_precision"],
        "native_entity_recall": metrics.get("native", {}).get("entity_recall"),
        "native_fully_correct_recall": metrics.get("native", {}).get("fully_correct_recall"),
    }
    return {
        "dataset_version": dataset.version,
        "heldout_sha256": dataset.manifest["heldout_sha256"],
        "split": split,
        "versions": versions(),
        "reference_date": dataset.manifest["reference_date"],
        "metrics": metrics,
        "system": dict(sorted(system.items())),
        "hard_gates": hard,
        "hard_gates_pass": all(v == 0 for v in hard.values()),
        "targets": {
            k: {"value": v, "target": DEV_TARGETS[k], "met": _met(v, DEV_TARGETS[k])}
            for k, v in targets.items()
        },
        "extractor_seconds_per_page": round(extract_seconds / pages_seen, 5)
        if pages_seen
        else None,
        "documents": per_document,
        "note": FOOTNOTE,
    }


def _met(value: float | None, target: tuple[str, float]) -> bool:
    if value is None:
        return False
    op, bound = target
    return value <= bound if op == "<=" else value >= bound


def source_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted(EXTRACTION_SOURCE.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".csv"):
            digest.update(path.as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def versions() -> dict:
    return {
        "extractor_version": EXTRACTOR_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "date_policy_version": DATE_POLICY_VERSION,
        "reference_data_version": load_reference().version,
        "classifier_rules_version": CLASSIFIER_RULES_VERSION,
        "identity_rules_version": IDENTITY_RULES_VERSION,
        "extraction_source_sha256": source_digest(),
    }


# --- reports and protocol ---------------------------------------------------------------------


def _fmt(value: object) -> str:
    return "—" if value is None else str(value)


def to_markdown(report: dict) -> str:
    m = report["metrics"]
    lines = [
        f"# Extraction evaluation: {report['split']} ({report['dataset_version']})",
        "",
        f"> {report['note']}",
        "",
        f"Extractor `{report['versions']['extractor_version']}`, normalizer "
        f"`{report['versions']['normalizer_version']}`, reference "
        f"`{report['versions']['reference_data_version']}`, date policy "
        f"`{report['versions']['date_policy_version']}` (reference date "
        f"{report['reference_date']}).",
        "",
        "## Hard gates (must all be 0)",
        "",
        "| Gate | Value |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in report["hard_gates"].items()],
        "",
        "## Pre-registered targets",
        "",
        "| Metric | Value | Target | Met |",
        "|---|---|---|---|",
        *[
            f"| {k} | {_fmt(t['value'])} | {t['target'][0]} {t['target'][1]} | {t['met']} |"
            for k, t in report["targets"].items()
        ],
        "",
        "## Component mode (gold lab reports)",
        "",
        "| Group | Docs | Gold | Predicted | Entity P | Entity R | Fully correct R | "
        "Silent error | Needs review | Provenance |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, g in m.items():
        c = g["counts"]
        lines.append(
            f"| {name} | {c.get('documents', 0)} | {c.get('gold_facts', 0)} | "
            f"{c.get('predicted_facts', 0)} | {_fmt(g['entity_precision'])} | "
            f"{_fmt(g['entity_recall'])} | {_fmt(g['fully_correct_recall'])} | "
            f"{_fmt(g['silent_error_rate'])} | {_fmt(g['needs_review_rate'])} | "
            f"{_fmt(g['provenance_accuracy'])} |"
        )
    lines += [
        "",
        "## End-to-end field metrics (all gold lab reports; misses count as errors)",
        "",
        "| Field | End-to-end recall | Precision | F1 | Matched-pair accuracy (diagnostic) |",
        "|---|---|---|---|---|",
        *[
            f"| {f} | {_fmt(v['end_to_end_recall'])} | {_fmt(v['precision'])} | "
            f"{_fmt(v['f1'])} | {_fmt(v['matched_pair_accuracy'])} |"
            for f, v in m["all"]["fields"].items()
        ],
        "",
        "## System mode (classifier, identity gate, extractor)",
        "",
        "| Count | Value |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in report["system"].items()],
        "",
        "## Review reasons and abstentions (all gold lab reports)",
        "",
        f"- Review reasons: {m['all']['review_reasons']}",
        f"- Abstentions: {m['all']['abstentions']}",
        f"- Extractor seconds per page (excluding Docling): {report['extractor_seconds_per_page']}",
        "",
    ]
    return "\n".join(lines)


def _name(split: str) -> str:
    return f"extraction_{split}_{EXTRACTOR_VERSION}"


def write_report(report: dict, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    name = _name(report["split"])
    json_path, md_path = directory / f"{name}.json", directory / f"{name}.md"
    if json_path.exists() or md_path.exists():
        raise FileExistsError(f"report {name} already exists; bump the extractor version")
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(dataset: Dataset, directory: Path) -> Path:
    selection = directory / SELECTION
    if selection.exists():
        raise EvaluationError("the extraction configuration is already frozen")
    dev_report = directory / f"{_name('dev')}.json"
    if not dev_report.is_file():
        raise EvaluationError("no dev report for the current extractor version; run `dev`")
    report = json.loads(dev_report.read_text(encoding="utf-8"))
    if report["versions"] != versions():
        raise EvaluationError("the code or reference data changed since the dev report")
    if not report["hard_gates_pass"]:
        raise EvaluationError("hard gates fail on dev; held-out stays pending (§12.4)")
    payload = {
        "versions": versions(),
        "dataset_version": dataset.version,
        "heldout_sha256": dataset.manifest["heldout_sha256"],
        "dev_report_sha256": _sha256_file(dev_report),
        "targets": {k: list(v) for k, v in DEV_TARGETS.items()},
        "hard_gates": list(HARD_GATES),
    }
    selection.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return selection


def check_heldout_allowed(dataset: Dataset, directory: Path) -> None:
    if (directory / HELDOUT_DONE).exists():
        raise EvaluationError("the held-out split was already evaluated; it is never re-run")
    path = directory / SELECTION
    if not path.is_file():
        raise EvaluationError("no frozen configuration; run `freeze` first")
    selection = json.loads(path.read_text(encoding="utf-8"))
    dev_report = directory / f"{_name('dev')}.json"
    problems = []
    if selection["versions"] != versions():
        problems.append("the extractor code, versions or reference data changed since freeze")
    if selection["dataset_version"] != dataset.version:
        problems.append("the dataset version changed since freeze")
    if selection["heldout_sha256"] != dataset.manifest["heldout_sha256"]:
        problems.append("the held-out digest changed since freeze")
    if not dev_report.is_file() or _sha256_file(dev_report) != selection["dev_report_sha256"]:
        problems.append("the frozen dev report is missing or changed")
    if problems:
        raise EvaluationError("; ".join(problems))


def run_heldout(dataset: Dataset, directory: Path, parse: Parser) -> tuple[Path, Path]:
    check_heldout_allowed(dataset, directory)
    (directory / HELDOUT_DONE).write_text(EXTRACTOR_VERSION + "\n", encoding="utf-8")
    return write_report(evaluate(dataset, "heldout", parse), directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate lab-observation extraction.")
    parser.add_argument("command", choices=("dev", "freeze", "heldout"))
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--reports", type=Path, default=None)
    parser.add_argument("--no-write", action="store_true", help="dev: print, write no report")
    args = parser.parse_args(argv)
    dataset = load_dataset(args.dataset)
    name = dataset.manifest["dataset"]
    directory = args.reports or REPORTS_DIR / name
    try:
        if args.command == "freeze":
            print(f"Frozen: {freeze(dataset, directory)}")
            return 0
        parse = docling_parser(WORK_DIR / name / "ir")
        if args.command == "heldout":
            paths = run_heldout(dataset, directory, parse)
        else:
            report = evaluate(dataset, "dev", parse)
            if args.no_write:
                print(to_markdown(report))
                return 0
            paths = write_report(report, directory)
    except EvaluationError as exc:
        print(f"Refused: {exc}")
        return 1
    print("Reports: " + ", ".join(str(p) for p in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
