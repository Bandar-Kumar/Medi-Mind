"""Offline evaluation of patient identity verification on ``identity_v0`` (Task 2.8, ADR-036).

    uv run python -m evals.run_identity dev
    uv run python -m evals.run_identity freeze      # after the owner reviews the dev report
    uv run python -m evals.run_identity heldout     # once, after freeze

No database. Each PDF is validated and parsed by the project's Docling parser (IR cached in
``evals/work/identity_v0/ir/``, git-ignored); the text-level U+FFFD cases are checked from their
stored text. Every document of the split is checked against **every** profile of the split:
its own profile and all swapped ones. The expected outcome of each pair is fixed by construction
(``evals.synthetic.identity.expected_outcome``).

Protocol (shared with ``evals.run_identity_v1``; as for classification, Task 2.7):
- ``dev`` evaluates the dev split only. The held-out split is never parsed by it.
- ``freeze`` re-scores dev with the current rules and refuses unless every pair equals the stored
  dev report; it then records the rules version, the SHA-256 of the rules source, the dataset
  version and held-out digest, and the dev report's SHA-256 in ``identity_selection.json``
  (written once, never changed by any command).
- ``heldout`` refuses unless that selection matches the current state exactly; it writes
  ``identity_heldout.done`` **before** evaluating (a failed run still counts as the one run), then
  parses the held-out documents for the first time and writes the held-out report, which states
  that it is not a blind or independent validation. It never runs twice and never changes rules.

Metrics (measured, never estimated):
- accuracy against the expected outcome, and the expected x actual confusion matrix;
- own-profile outcomes (match / unknown / mismatch), including false mismatches;
- swapped-profile outcomes and **false matches on swapped profiles** (target: exactly 0);
  namesake pairs whose document prints no date of birth are reported apart: the same full name
  with nothing else printed cannot be told apart, so ``match`` is the expected, documented limit;
- per-variant breakdown; the text-level cases are reported in their own row.
Reports hold dataset keys, document ids, variants, outcomes and reason codes: never a name, a
date of birth or page text.
"""

import argparse
import datetime as dt
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import app.ingestion.identity as identity_rules
from app.ingestion.identity import IDENTITY_RULES_VERSION, IdentityPage, check_identity
from evals.dataset import Dataset, load_dataset
from evals.run_classification import docling_page_parser
from evals.synthetic.identity import MATCH, MISMATCH, UNKNOWN, Profile, expected_outcome

DEFAULT_DATASET = Path("data/synthetic/identity_v0")
REPORTS_DIR = Path("evals/reports")
WORK_DIR = Path("evals/work")
OUTCOMES = (MATCH, UNKNOWN, MISMATCH)
SELECTION = "identity_selection.json"
HELDOUT_DONE = "identity_heldout.done"
NOT_BLIND = (
    "NOT a blind or independent validation. The identity rules, this dataset's generator, the "
    "held-out documents and their expected outcomes were written by the same developer, with "
    "the same templates and categories as dev. Read these numbers as a regression check of the "
    "frozen rules on unseen synthetic patients, not as evidence of real-world accuracy."
)

PageParser = Callable[[bytes], Sequence[IdentityPage]]


class EvaluationError(Exception):
    """The held-out protocol refused to run (already run, not frozen, or changed since freeze)."""


@dataclass(frozen=True)
class _TextPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class Pair:
    document_id: str
    variant: str
    source: str  # "pdf" or "text"
    document_patient: str
    profile: str
    own: bool
    namesake_without_dob: bool  # same full name, no date of birth printed
    expected: str
    actual: str
    reason_codes: tuple[str, ...]


def _profile(entry: dict) -> Profile:
    return Profile(
        entry["patient_key"],
        entry["first"],
        entry["last"],
        entry["sex"],
        dt.date.fromisoformat(entry["date_of_birth"]),
    )


def evaluate(dataset: Dataset, split: str, parse: PageParser) -> list[Pair]:
    """Check every document of ``split`` against every profile of ``split``."""
    patients = dataset.patients(split)
    people = {p["patient_key"]: _profile(p) for p in patients}
    pairs = []
    for patient in patients:
        me = people[patient["patient_key"]]
        for doc in patient["documents"]:
            if doc["source"] == "text":
                pages: Sequence[IdentityPage] = (_TextPage(1, doc["text"]),)
            else:
                pages = parse(dataset.pdf_path(doc["document_id"]).read_bytes())
            for key, profile in people.items():
                result = check_identity(pages, profile.display_name, profile.date_of_birth)
                same_name = (me.first, me.last) == (profile.first, profile.last)
                pairs.append(
                    Pair(
                        document_id=doc["document_id"],
                        variant=doc["variant"],
                        source=doc["source"],
                        document_patient=me.patient_key,
                        profile=key,
                        own=key == me.patient_key,
                        namesake_without_dob=(
                            key != me.patient_key
                            and same_name
                            and doc["variant"] in ("lab_header", "reordered")
                        ),
                        expected=expected_outcome(doc["variant"], me, profile),
                        actual=result.outcome,
                        reason_codes=result.reason_codes,
                    )
                )
    return pairs


def _counts(pairs: Sequence[Pair]) -> dict[str, int]:
    counter = Counter(p.actual for p in pairs)
    return {outcome: counter.get(outcome, 0) for outcome in OUTCOMES}


def score(pairs: Sequence[Pair]) -> dict:
    if not pairs:
        raise ValueError("no pairs were evaluated")
    own = [p for p in pairs if p.own]
    swapped = [p for p in pairs if not p.own]
    distinguishable = [p for p in swapped if not p.namesake_without_dob]
    namesakes = [p for p in swapped if p.namesake_without_dob]
    confusion = {e: {a: 0 for a in OUTCOMES} for e in OUTCOMES}
    for p in pairs:
        confusion[p.expected][p.actual] += 1
    variants: dict[str, dict] = {}
    for p in pairs:
        row = variants.setdefault(p.variant, {"own": Counter(), "swapped": Counter()})
        row["own" if p.own else "swapped"][p.actual] += 1
    return {
        "pairs": len(pairs),
        "documents": len({p.document_id for p in pairs}),
        "accuracy": sum(p.actual == p.expected for p in pairs) / len(pairs),
        "confusion_expected_by_actual": confusion,
        "own_profile": _counts(own),
        "own_profile_false_mismatches": sum(p.actual == MISMATCH for p in own),
        "swapped_profile": _counts(swapped),
        "false_matches_on_swapped_profiles": sum(p.actual == MATCH for p in distinguishable),
        "namesake_pairs_without_dob": {"pairs": len(namesakes), **_counts(namesakes)},
        "by_variant": {
            name: {side: dict(sorted(counts.items())) for side, counts in row.items()}
            for name, row in sorted(variants.items())
        },
    }


def to_markdown(dataset: Dataset, split: str, scores: dict, pairs: Sequence[Pair]) -> str:
    own, swapped, ns = (
        scores["own_profile"],
        scores["swapped_profile"],
        scores["namesake_pairs_without_dob"],
    )
    lines = [
        f"# Identity verification: {split} split",
        "",
        *([f"> **{NOT_BLIND}**", ""] if split == "heldout" else []),
        f"- Dataset: `{dataset.version}`, split `{split}`: {scores['documents']} documents x "
        f"profiles = {scores['pairs']} pairs",
        f"- Identity rules version: `{IDENTITY_RULES_VERSION}`",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| accuracy vs expected (by construction) | {scores['accuracy']:.4f} |",
        f"| own profile: match / unknown / mismatch | {own['match']} / {own['unknown']} / "
        f"{own['mismatch']} |",
        f"| own profile: false mismatches | {scores['own_profile_false_mismatches']} |",
        f"| swapped profiles: match / unknown / mismatch | {swapped['match']} / "
        f"{swapped['unknown']} / {swapped['mismatch']} |",
        f"| **false matches on swapped profiles** (distinguishable; target 0) | "
        f"**{scores['false_matches_on_swapped_profiles']}** |",
        f"| namesake pairs without a printed DOB: pairs (match / unknown / mismatch) | "
        f"{ns['pairs']} ({ns['match']} / {ns['unknown']} / {ns['mismatch']}) |",
        "",
        "Namesake pairs without a printed date of birth carry the same full name and nothing else:",
        "they cannot be told apart from the document, so `match` is expected (a documented limit).",
        "",
        "## Expected x actual",
        "",
        "| expected \\ actual | " + " | ".join(OUTCOMES) + " |",
        "|---|" + "---|" * len(OUTCOMES),
    ]
    for expected in OUTCOMES:
        row = scores["confusion_expected_by_actual"][expected]
        lines.append(f"| {expected} | " + " | ".join(str(row[a]) for a in OUTCOMES) + " |")
    lines += ["", "## By variant (own profile | swapped profiles)", ""]
    lines += ["| variant | own: m / u / x | swapped: m / u / x |", "|---|---|---|"]
    for name, row in scores["by_variant"].items():
        o = [row["own"].get(k, 0) for k in OUTCOMES]
        s = [row["swapped"].get(k, 0) for k in OUTCOMES]
        label = f"{name} (text-level)" if name == "fffd" else name
        lines.append(f"| {label} | {o[0]} / {o[1]} / {o[2]} | {s[0]} / {s[1]} / {s[2]} |")
    wrong = [p for p in pairs if p.actual != p.expected]
    lines += ["", f"## Differences from the expected outcome ({len(wrong)})", ""]
    lines += ["| document | profile | variant | expected | actual | reasons |"]
    lines += ["|---|---|---|---|---|---|"]
    lines += [
        f"| {p.document_id} | {p.profile} | {p.variant} | {p.expected} | {p.actual} | "
        f"{', '.join(p.reason_codes)} |"
        for p in wrong
    ]
    return "\n".join(lines) + "\n"


def write_report(
    dataset: Dataset, split: str, pairs: Sequence[Pair], directory: Path
) -> tuple[Path, Path]:
    """Write ``identity_<split>_rules_v<N>.{json,md}``; never overwrites a report."""
    scores = score(pairs)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"identity_{split}_rules_v{IDENTITY_RULES_VERSION}"
    json_path, md_path = directory / f"{name}.json", directory / f"{name}.md"
    if json_path.exists() or md_path.exists():
        raise FileExistsError(f"report {name} already exists")
    payload = {
        "dataset_version": dataset.version,
        "heldout_sha256": dataset.manifest["heldout_sha256"],
        "split": split,
        "rules_version": IDENTITY_RULES_VERSION,
        "metrics": scores,
        "pairs": [asdict(p) | {"reason_codes": list(p.reason_codes)} for p in pairs],
    }
    if split == "heldout":
        payload["notice"] = NOT_BLIND
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(dataset, split, scores, pairs), encoding="utf-8")
    return json_path, md_path


# --- freeze and the one-time held-out run (shared with evals.run_identity_v1) -------------------

Evaluate = Callable[[Dataset, str, PageParser], Sequence]
WriteReport = Callable[[Dataset, str, Sequence, Path], tuple[Path, Path]]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rules_source_sha256() -> str:
    """SHA-256 of the identity rules module: any code change is detected, not only a version."""
    return _sha256_file(Path(identity_rules.__file__))


def _dev_report(directory: Path, rules_version: str) -> Path:
    return directory / f"identity_dev_rules_v{rules_version}.json"


def freeze(dataset: Dataset, directory: Path, dev_pairs: Sequence) -> Path:
    """Record the frozen configuration once, after proving the current rules reproduce dev.

    ``dev_pairs``: the dev split re-scored now with the current rules (from the parse cache).
    """
    selection = directory / SELECTION
    if selection.exists():
        raise EvaluationError("the identity rules are already frozen for this dataset")
    dev_report = _dev_report(directory, IDENTITY_RULES_VERSION)
    if not dev_report.is_file():
        raise EvaluationError("no dev report for the current rules version; run `dev` first")
    stored = json.loads(dev_report.read_text(encoding="utf-8"))
    current = [asdict(p) | {"reason_codes": list(p.reason_codes)} for p in dev_pairs]
    if stored["pairs"] != current:
        raise EvaluationError("the current rules do not reproduce the dev report; re-run `dev`")
    payload = {
        "rules_version": IDENTITY_RULES_VERSION,
        "rules_source_sha256": rules_source_sha256(),
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
    dev_report = _dev_report(directory, selection["rules_version"])
    problems = []
    if selection["rules_version"] != IDENTITY_RULES_VERSION:
        problems.append("the identity rules version changed since freeze")
    if selection["rules_source_sha256"] != rules_source_sha256():
        problems.append("the identity rules source changed since freeze")
    if selection["dataset_version"] != dataset.version:
        problems.append("the dataset version changed since freeze")
    if selection["heldout_sha256"] != dataset.manifest["heldout_sha256"]:
        problems.append("the held-out digest changed since freeze")
    if not dev_report.is_file() or _sha256_file(dev_report) != selection["dev_report_sha256"]:
        problems.append("the frozen dev report is missing or changed")
    if problems:
        raise EvaluationError("; ".join(problems))


def run_heldout(
    dataset: Dataset,
    directory: Path,
    parse: PageParser,
    evaluate_split: Evaluate,
    write: WriteReport,
) -> tuple[Path, Path]:
    """The one held-out run. The marker is written first: a failure still uses the run up."""
    check_heldout_allowed(dataset, directory)
    marker = (
        f"rules_version={IDENTITY_RULES_VERSION}\nrules_source_sha256={rules_source_sha256()}\n"
    )
    (directory / HELDOUT_DONE).write_text(marker, encoding="utf-8")
    return write(dataset, "heldout", evaluate_split(dataset, "heldout", parse), directory)


def run_command(
    argv: list[str] | None,
    *,
    default_dataset: Path,
    description: str,
    evaluate_split: Evaluate,
    write: WriteReport,
) -> int:
    """``dev``, ``freeze`` or ``heldout`` for one identity dataset."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("command", choices=("dev", "freeze", "heldout"))
    parser.add_argument("--dataset", type=Path, default=default_dataset)
    parser.add_argument("--reports", type=Path, default=None, help="report directory")
    args = parser.parse_args(argv)
    dataset = load_dataset(args.dataset)  # verifies every file hash and the held-out digest
    name = dataset.manifest["dataset"]
    directory = args.reports or REPORTS_DIR / name
    parse = docling_page_parser(WORK_DIR / name / "ir")
    try:
        if args.command == "freeze":
            print(f"Frozen: {freeze(dataset, directory, evaluate_split(dataset, 'dev', parse))}")
            return 0
        if args.command == "heldout":
            paths = run_heldout(dataset, directory, parse, evaluate_split, write)
        else:
            paths = write(dataset, "dev", evaluate_split(dataset, "dev", parse), directory)
    except EvaluationError as exc:
        print(f"Refused: {exc}")
        return 1
    print("Reports: " + ", ".join(str(path) for path in paths))
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_command(
        argv,
        default_dataset=DEFAULT_DATASET,
        description="Evaluate identity verification on identity_v0.",
        evaluate_split=evaluate,
        write=write_report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
