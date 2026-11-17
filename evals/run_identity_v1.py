"""Offline evaluation of identity verification on the edge-case dataset ``identity_v1``.

    uv run python -m evals.run_identity_v1 dev
    uv run python -m evals.run_identity_v1 freeze      # after the owner reviews the dev report
    uv run python -m evals.run_identity_v1 heldout     # once, after freeze

``identity_v1`` is a dataset version (``evals.synthetic.identity_v1``), not a rules version. No
database. PDFs are parsed by the project's Docling parser (IR cached in
``evals/work/identity_v1/ir/``, git-ignored); the text-level cases are checked from their stored
text. Every document of the split is checked against every profile of the split.

Scored against the truth (whose document it is), not against reason codes:
- **false matches on swapped profiles**: ``match`` for a profile that is not the document's
  subject, excluding the pairs marked by hand as indistinguishable (reported apart, by kind);
- **false mismatches on own profiles**: ``mismatch`` for the subject's own profile, excluding the
  documented limits marked by hand (``own_limit``, reported apart);
- own-profile agreement with the hand-written ``own_expected``, with every difference listed;
- unknown rate, reason-code distribution and a per-category breakdown.

``dev`` never parses the held-out split. ``freeze`` and ``heldout`` follow the protocol of
``evals.run_identity`` (one frozen selection, one held-out run guarded by a marker written before
the evaluation; the held-out report states that it is not a blind validation). Reports hold
dataset keys, document ids, categories, outcomes and reason codes: never a name, a date of birth
or page text.
"""

import datetime as dt
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from app.ingestion.identity import IDENTITY_RULES_VERSION, IdentityPage, check_identity
from evals.dataset import Dataset
from evals.run_identity import NOT_BLIND, OUTCOMES, PageParser, _TextPage, run_command
from evals.synthetic.identity_v1 import MATCH, MISMATCH, UNKNOWN

DEFAULT_DATASET = Path("data/synthetic/identity_v1")


@dataclass(frozen=True)
class Pair:
    document_id: str
    category: str
    source: str  # "pdf" or "text"
    subject: str
    profile: str
    own: bool
    expected: str | None  # own profile: the hand-written outcome; swapped: None ("not match")
    limit: str | None  # a documented limit: own_limit, or "indistinguishable"
    actual: str
    reason_codes: tuple[str, ...]


def evaluate(dataset: Dataset, split: str, parse: PageParser) -> list[Pair]:
    """Check every document of ``split`` against every profile of ``split``."""
    patients = dataset.patients(split)
    pairs = []
    for patient in patients:
        for doc in patient["documents"]:
            if doc["source"] == "text":
                pages: Sequence[IdentityPage] = (_TextPage(1, doc["text"]),)
            else:
                pages = parse(dataset.pdf_path(doc["document_id"]).read_bytes())
            for profile in patients:
                born = profile["date_of_birth"]
                result = check_identity(
                    pages, profile["display_name"], dt.date.fromisoformat(born) if born else None
                )
                own = profile["patient_key"] == patient["patient_key"]
                if own:
                    expected, limit = doc["own_expected"], doc["own_limit"]
                else:
                    indistinguishable = profile["patient_key"] in doc["indistinguishable_from"]
                    expected, limit = None, "indistinguishable" if indistinguishable else None
                pairs.append(
                    Pair(
                        document_id=doc["document_id"],
                        category=doc["category"],
                        source=doc["source"],
                        subject=patient["patient_key"],
                        profile=profile["patient_key"],
                        own=own,
                        expected=expected,
                        limit=limit,
                        actual=result.outcome,
                        reason_codes=result.reason_codes,
                    )
                )
    return pairs


def _counts(pairs: Sequence[Pair]) -> dict[str, int]:
    counter = Counter(p.actual for p in pairs)
    return {outcome: counter.get(outcome, 0) for outcome in OUTCOMES}


def _rate(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def score(pairs: Sequence[Pair]) -> dict:
    if not pairs:
        raise ValueError("no pairs were evaluated")
    own = [p for p in pairs if p.own]
    swapped = [p for p in pairs if not p.own]
    categories: dict[str, dict] = {}
    for p in pairs:
        row = categories.setdefault(p.category, {"own": Counter(), "swapped": Counter()})
        row["own" if p.own else "swapped"][p.actual] += 1
    reasons: dict[str, Counter] = {"own": Counter(), "swapped": Counter()}
    for p in pairs:
        reasons["own" if p.own else "swapped"].update(p.reason_codes)
    unknown = sum(p.actual == UNKNOWN for p in pairs)
    return {
        "pairs": len(pairs),
        "documents": len({p.document_id for p in pairs}),
        "false_matches_on_swapped_profiles": sum(
            p.actual == MATCH and p.limit is None for p in swapped
        ),
        "indistinguishable_swapped_pairs": {
            "pairs": sum(p.limit is not None for p in swapped),
            **_counts([p for p in swapped if p.limit is not None]),
        },
        "own_profile_false_mismatches": sum(p.actual == MISMATCH and p.limit is None for p in own),
        "own_profile_documented_limit_mismatches": sum(
            p.actual == MISMATCH and p.limit is not None for p in own
        ),
        "own_profile_agreement": _rate(sum(p.actual == p.expected for p in own), len(own)),
        "own_profile": _counts(own),
        "swapped_profile": _counts(swapped),
        "unknown_rate": {
            "all": _rate(unknown, len(pairs)),
            "own": _rate(sum(p.actual == UNKNOWN for p in own), len(own)),
            "swapped": _rate(sum(p.actual == UNKNOWN for p in swapped), len(swapped)),
        },
        "reason_codes": {side: dict(sorted(c.items())) for side, c in reasons.items()},
        "by_category": {
            name: {side: dict(sorted(counts.items())) for side, counts in row.items()}
            for name, row in sorted(categories.items())
        },
    }


def _row(counts: dict[str, int]) -> str:
    return " / ".join(str(counts.get(k, 0)) for k in OUTCOMES)


def to_markdown(dataset: Dataset, split: str, scores: dict, pairs: Sequence[Pair]) -> str:
    rate, ind = scores["unknown_rate"], scores["indistinguishable_swapped_pairs"]
    lines = [
        f"# Identity verification (edge cases): {split} split",
        "",
        *([f"> **{NOT_BLIND}**", ""] if split == "heldout" else []),
        f"- Dataset: `{dataset.version}`, split `{split}`: {scores['documents']} documents x "
        f"profiles = {scores['pairs']} pairs",
        f"- Identity rules version: `{IDENTITY_RULES_VERSION}`",
        "- Scored against the truth (whose document it is) and hand-written own-profile "
        "expectations, not against reason codes.",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| **false matches on swapped profiles** (excluding indistinguishable; target 0) | "
        f"**{scores['false_matches_on_swapped_profiles']}** |",
        f"| **own profile: false mismatches** (excluding documented limits; target 0) | "
        f"**{scores['own_profile_false_mismatches']}** |",
        f"| own profile: documented-limit mismatches | "
        f"{scores['own_profile_documented_limit_mismatches']} |",
        f"| indistinguishable swapped pairs: pairs (match / unknown / mismatch) | "
        f"{ind['pairs']} ({_row(ind)}) |",
        f"| own profile: agreement with the hand-written expectation | "
        f"{scores['own_profile_agreement']:.4f} |",
        f"| own profile: match / unknown / mismatch | {_row(scores['own_profile'])} |",
        f"| swapped profiles: match / unknown / mismatch | {_row(scores['swapped_profile'])} |",
        f"| unknown rate: all / own / swapped | {rate['all']:.4f} / {rate['own']:.4f} / "
        f"{rate['swapped']:.4f} |",
        "",
        "## By category (own profile | swapped profiles; match / unknown / mismatch)",
        "",
        "| category | own | swapped |",
        "|---|---|---|",
    ]
    for name, row in scores["by_category"].items():
        lines.append(f"| {name} | {_row(row['own'])} | {_row(row['swapped'])} |")
    lines += ["", "## Reason codes (count of pairs carrying each code)", ""]
    lines += ["| code | own | swapped |", "|---|---|---|"]
    codes = sorted(set(scores["reason_codes"]["own"]) | set(scores["reason_codes"]["swapped"]))
    for code in codes:
        own_count = scores["reason_codes"]["own"].get(code, 0)
        lines.append(f"| {code} | {own_count} | {scores['reason_codes']['swapped'].get(code, 0)} |")
    notable = [
        p
        for p in pairs
        if (p.own and p.actual != p.expected)
        or (not p.own and p.actual == MATCH)
        or (p.own and p.limit is not None)
    ]
    lines += ["", f"## Own-profile differences, limits and every swapped match ({len(notable)})"]
    lines += ["", "| document | profile | category | expected | actual | limit | reasons |"]
    lines += ["|---|---|---|---|---|---|---|"]
    lines += [
        f"| {p.document_id} | {p.profile} | {p.category} | "
        f"{p.expected if p.own else 'not match'} | {p.actual} | {p.limit or ''} | "
        f"{', '.join(p.reason_codes)} |"
        for p in notable
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


def main(argv: list[str] | None = None) -> int:
    return run_command(
        argv,
        default_dataset=DEFAULT_DATASET,
        description="Evaluate identity verification on the edge cases of identity_v1.",
        evaluate_split=evaluate,
        write=write_report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
