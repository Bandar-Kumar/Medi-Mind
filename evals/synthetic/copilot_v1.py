"""Copilot question set ``copilot_v1`` (Phase 5 remediation; docs/PHASE_5_PLAN.md §12).

Built and frozen **before** any remediation code (vocabulary, unrecognised-test handling,
abstention) was written, so the held-out labels cannot be shaped by the fixes.

Splits (patient-disjoint; the held-out patients have never been evaluated or inspected):

- **dev:** every ``longitudinal_v0`` scenario patient (its dev and its spent held-out patients;
  L99 excluded) and every ``lab_v0`` patient (both splits). All of them were seen in Phase 5.
- **held-out:** the new ``longitudinal_v1`` (M01–M06) and ``lab_v1`` (6 patients) sources
  (``evals.synthetic.sources_v1``).

Phrasing: the v0 question kinds (``evals.synthetic.copilot``, unchanged) with a **split-specific
pool** of patient phrasings for test names: dev reuses the v0 pool; held-out uses a pool written
separately, mostly different words, to measure vocabulary generalisation. New kinds:

- ``unknown_test``: a test name absent from the reference data (disjoint pools per split) in a
  latest / history / dated / comparison question -> must abstain (no "all tests" fallback);
- ``partial_unknown``: one known test and one unknown -> partial;
- ``document_unknown``: "What did my report dated … say about <unknown>?" -> must abstain;
- ``all_on_date`` / ``all_in_year``: questions that really ask for every result -> every usable
  result of that date / year.

Labels come only from the source datasets' truth. Same author as the system: not a blind test.
"""

import hashlib
import json
import random
from contextlib import contextmanager
from pathlib import Path

from evals.synthetic import copilot as v0

DATASET = "copilot_v1"
GENERATOR_VERSION = "copilot-gen-2"
SEED = 20261119
LONGITUDINAL_V1 = "longitudinal_v1"
LAB_V1 = "lab_v1"

DEV_TERMS = v0.SYNONYMS
HELDOUT_TERMS = {
    "Haemoglobin": ("HGB", "hemoglobin", "haemoglobin"),
    "Fasting Blood Sugar": ("fasting glucose", "fasting blood glucose", "FBG"),
    "HbA1c": ("hba1c", "glycosylated hemoglobin", "haemoglobin A1c"),
    "TSH": ("thyrotropin", "TSH"),
    "MCV": ("mean corpuscular volume", "MCV"),
    "Serum Creatinine": ("S. creatinine", "creatinine"),
    "Total Cholesterol": ("serum cholesterol", "cholesterol"),
}
# Names of tests that are not in the reference data (aliases_v1.csv). Disjoint per split.
DEV_UNKNOWN = ("ferritin", "vitamin K", "CRP", "homocysteine")
HELDOUT_UNKNOWN = ("zinc", "PSA", "lipase", "troponin")
_UNKNOWN_TEMPLATES = (
    ("What was my latest {n} result?", "structured_facts", "latest"),
    ("Show my {n} results over time.", "longitudinal", "history"),
    ("Compare my latest {n} with the previous result.", "longitudinal", "compare_previous"),
)


@contextmanager
def _terms(pool: dict):
    saved = v0.SYNONYMS
    v0.SYNONYMS = pool
    try:
        yield
    finally:
        v0.SYNONYMS = saved


def _extra_longitudinal(patient: dict, rng: random.Random, unknown: tuple[str, ...]) -> list[dict]:
    truth = v0._Truth(patient)
    out: list[dict] = []

    def add(kind: str, question: dict) -> None:
        out.append(
            {"kind": kind, "source": patient["_source"], "patient_key": truth.key, **question}
        )

    for name in rng.sample(unknown, 2):
        template, route, intent = rng.choice(_UNKNOWN_TEMPLATES)
        add("unknown_test", v0._question(
            text=template.format(n=name), expect_status=v0.INSUFFICIENT, expect_route=route,
            expect_intent=intent,
        ))  # fmt: skip
    analytes = truth.analytes()
    have = rng.choice(analytes)
    dated = [k for k in truth.analyte_keys(have) if truth.date(k)]
    last = max(truth.date(k) for k in dated)  # type: ignore[type-var]
    add("partial_unknown", v0._fact_question(
        truth, [k for k in dated if truth.date(k) == last],
        text=f"What were my latest {have} and {rng.choice(unknown)} results?",
        expect_status=v0.PARTIAL, expect_route="structured_facts", expect_intent="latest",
    ))  # fmt: skip
    days = sorted({truth.date(k) for k, f in truth.usable.items() if f["precision"] == "day"})  # type: ignore[type-var]
    day = rng.choice(days)
    keys = [k for k, f in truth.usable.items() if f["precision"] == "day" and truth.date(k) == day]
    add("all_on_date", v0._fact_question(
        truth, keys, text=f"What were my results on {v0._dmy(day)}?", expect_status=v0.ANSWERED,
        expect_route="structured_facts", expect_intent="on_date",
    ))  # fmt: skip
    year = rng.choice(sorted({d.year for d in days}))
    keys = [k for k in truth.usable if truth.date(k) and truth.date(k).year == year]  # type: ignore[union-attr]
    if len(truth.groups(keys)) <= 20:  # the history tool returns at most 20 results
        add("all_in_year", v0._fact_question(
            truth, keys, text=f"Show all my results from {year}.", expect_status=v0.ANSWERED,
            expect_route="longitudinal", expect_intent="history",
        ))  # fmt: skip
    return out


def _extra_lab(patient: dict, rng: random.Random, unknown: tuple[str, ...], source: str) -> list:
    reports = [r for r in patient["reports"] if not r["date_ambiguous"]]
    if not reports:
        return []
    report = rng.choice(reports)
    return [{
        "kind": "document_unknown", "source": source, "patient_key": patient["patient_key"],
        **v0._question(
            text=(
                f"What did my report dated {report['date_printed']} say about "
                f"{rng.choice(unknown)}?"
            ),
            expect_status=v0.INSUFFICIENT, expect_route="document_search",
            expect_intent="document_lookup",
        ),
    }]  # fmt: skip


def _relabel(questions: list[dict], source_map: dict[str, str]) -> list[dict]:
    for q in questions:
        q["source"] = source_map.get(q["source"], q["source"])
    return questions


def generate(
    longitudinal: list[dict], lab: list[dict], split: str, *, long_source: str, lab_source: str
) -> list[dict]:
    rng = random.Random(f"{SEED}:{split}")
    terms = DEV_TERMS if split == "dev" else HELDOUT_TERMS
    unknown = DEV_UNKNOWN if split == "dev" else HELDOUT_UNKNOWN
    scenario = [p for p in longitudinal if p["patient_key"] not in v0.EXCLUDED_PATIENTS]
    questions: list[dict] = []
    with _terms(terms):
        for index, patient in enumerate(sorted(scenario, key=lambda p: p["patient_key"])):
            others = {
                f["analyte"] for q in scenario if q is not patient
                for f in q["truth"]["facts"].values() if f["excluded"] is None
            }  # fmt: skip
            patient = {**patient, "_source": long_source}
            questions += _relabel(
                v0.longitudinal_questions(patient, rng, others, index),
                {v0.LONGITUDINAL: long_source},
            )
            questions += _extra_longitudinal(patient, rng, unknown)
    for patient in sorted(lab, key=lambda p: p["patient_key"]):
        others = {
            row["analyte_key"] for q in lab if q is not patient
            for r in q["reports"] for p in r["pages"] for row in p["rows"]
        }  # fmt: skip
        questions += _relabel(v0.lab_questions(patient, rng, others), {v0.LAB: lab_source})
        questions += _extra_lab(patient, rng, unknown, lab_source)
    return [{"question_id": f"{split}-{i:04d}", **q} for i, q in enumerate(questions)]


def build(directory: Path) -> dict:
    """Write and freeze ``copilot_v1``. Refuses to overwrite."""
    from evals.dataset import load_dataset
    from evals.synthetic.longitudinal import load_split

    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"{directory} already exists; the dataset is frozen")
    long_v0 = Path("data/synthetic/longitudinal_v0")
    long_v1 = Path("data/synthetic/longitudinal_v1")
    lab_v0 = load_dataset(Path("data/synthetic/lab_v0"))
    lab_v1 = load_dataset(Path("data/synthetic/lab_v1"))
    sources = {
        "dev": (
            load_split(long_v0, "dev") + load_split(long_v0, "heldout"),
            lab_v0.patients("dev") + lab_v0.patients("heldout"),
            v0.LONGITUDINAL,
            v0.LAB,
        ),
        "heldout": (
            load_split(long_v1, "heldout"),
            lab_v1.patients("dev") + lab_v1.patients("heldout"),
            LONGITUDINAL_V1,
            LAB_V1,
        ),
    }
    directory.mkdir(parents=True, exist_ok=True)
    digests, splits = {}, {}
    for split, (longitudinal, lab, long_source, lab_source) in sources.items():
        questions = generate(
            longitudinal, lab, split, long_source=long_source, lab_source=lab_source
        )
        payload = json.dumps(
            {"dataset": DATASET, "split": split, "questions": questions},
            ensure_ascii=False, indent=1, sort_keys=True,
        ).encode("utf-8")  # fmt: skip
        (directory / f"{split}.json").write_bytes(payload)
        digests[split] = hashlib.sha256(payload).hexdigest()
        splits[split] = sorted({f"{q['source']}:{q['patient_key']}" for q in questions})
    long_manifest = json.loads((long_v1 / "manifest.json").read_text(encoding="utf-8"))
    manifest = {
        "dataset": DATASET,
        "generator_version": GENERATOR_VERSION,
        "seed": SEED,
        "sources": {
            "longitudinal_v0": json.loads((long_v0 / "manifest.json").read_text(encoding="utf-8"))[
                "sha256"
            ],
            "lab_v0": lab_v0.manifest["heldout_sha256"],
            LONGITUDINAL_V1: long_manifest["sha256"],
            LAB_V1: lab_v1.manifest["heldout_sha256"],
        },
        "split": splits,
        "sha256": digests,
        "frozen_at_build": True,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
