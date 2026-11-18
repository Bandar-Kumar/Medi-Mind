"""Phase 6 evaluation on ``clinical_v1`` (docs/PHASE_6_PLAN.md §10; docs/PHASE_6_EVALUATION.md).

No database. Each PDF is parsed by the project's Docling parser exactly as an upload would be
(IRs cached in ``evals/work/clinical_v1/ir/``). Then, per document, the real system path:

    Phase 2 classifier v2 → identity v2 (own profile, and a swapped profile) → the extraction
    gate → clinical-doctype-1 → clinical-rules-1

and, per patient, ``clinical-compare-1`` (document comparison, conflicts) and ``clinical-qa-1``
(routing, answers, citations) over the extracted facts (machine statuses; no human review).

    uv run python -m evals.run_clinical dev [--no-write]
    uv run python -m evals.run_clinical freeze
    uv run python -m evals.run_clinical heldout      # once, after freeze

Matching: a predicted fact matches a gold fact of the same document and type with the same key
(medication: printed name and context; allergy: printed substance; event: type and printed
description), one to one in page order. Metrics are reported separately for native and raster
(OCR) documents. ``validated`` means only that automated checks passed: no clinical accuracy is
claimed. Reports hold ids, fields, codes and fictional values only.

Protocol (as Phases 3–5): tune on dev only; ``freeze`` records the versions, the Phase 6 source
digest, the dataset version, the held-out digest, the dev report hash and the pre-registered
targets, and refuses when a hard gate fails on dev; ``heldout`` refuses on any difference, writes
its marker before evaluating, and never runs twice.
"""

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.answering.guardrails import input_gate
from app.clinical.answering import answer as clinical_answer
from app.clinical.answering import route
from app.clinical.classify import classify_clinical
from app.clinical.compare import ClinicalFact, compare_documents, detect_conflicts
from app.clinical.extract import extract_clinical_facts
from app.clinical.lexicon import load_lexicon
from app.clinical.schemas import AllergyDraft, EventDraft, MedicationDraft
from app.clinical.versions import (
    CLINICAL_CLASSIFIER_VERSION,
    CLINICAL_COMPARE_VERSION,
    CLINICAL_EXTRACTOR_VERSION,
    CLINICAL_NORMALIZER_VERSION,
    CLINICAL_QA_VERSION,
)
from app.extraction.versions import DATE_POLICY_VERSION
from app.ingestion.classification import CLASSIFIER_RULES_VERSION, classify_pages
from app.ingestion.identity import IDENTITY_RULES_VERSION, check_identity
from app.ingestion.ir import ParsedDocument
from app.records.facts import extraction_gate_refusal
from app.services.clinical_records import clinical_route
from evals.dataset import Dataset, load_dataset

DEFAULT_DATASET = Path("data/synthetic/clinical_v1")
REPORTS_DIR = Path("evals/reports")
WORK_DIR = Path("evals/work")
SELECTION = "clinical_selection.json"
HELDOUT_DONE = "clinical_heldout.done"
SOURCES = (Path("app/clinical"),)
SOURCE_FILES = (
    Path("app/services/clinical_records.py"),
    Path("app/services/extract_clinical.py"),
)
REFERENCE_DATE = dt.date(2025, 1, 1)  # the extraction plausibility bound (the dataset's as_of)

# Pre-registered targets (set in this file before its first scored dev run, 2026-11-18).
TARGETS: dict[str, tuple[str, float]] = {
    "classification_accuracy": (">=", 0.95),
    "native_entity_precision": (">=", 0.95),
    "native_entity_recall": (">=", 0.95),
    "native_field_exact_match": (">=", 0.95),
    "native_date_accuracy": (">=", 0.95),
    "native_review_status_agreement": (">=", 0.95),
    "native_provenance_precision": (">=", 0.95),
    "native_provenance_recall": (">=", 0.95),
    "conflict_precision": (">=", 0.90),
    "conflict_recall": (">=", 0.90),
    "comparison_accuracy": (">=", 0.90),
    "qa_routing_accuracy": (">=", 0.95),
    "qa_correctness": (">=", 0.90),
}
# Hard gates: must be exactly 0 on dev before freeze, and are reported on held-out.
HARD_GATES = (
    "ungrounded_facts",
    "mismatch_profile_facts",
    "refusal_probes_answered",
    "invalid_citations",
)

FIELDS = {
    "medication": (
        "name_norm",
        "name_match",
        "dosage_form",
        "strength_value",
        "strength_unit",
        "dose_raw",
        "route_norm",
        "frequency_raw",
        "frequency_per_day",
        "as_needed",
        "duration_value",
        "duration_unit",
        "instructions_raw",
        "mention_context",
    ),
    "allergy": (
        "substance_norm",
        "reaction_raw",
        "severity_norm",
        "allergy_status",
        "negated",
    ),
    "event": ("event_context", "subject", "description_raw"),
}
DATE_FIELDS = ("date", "date_precision", "date_source")


class EvaluationError(Exception):
    """The protocol refuses to run."""


# --- parsing and per-document pipeline ----------------------------------------------------------


Parser = Callable[[bytes], ParsedDocument]


def docling_parser(cache_dir: Path) -> Parser:
    from evals.run_extraction import docling_parser as phase3_parser

    return phase3_parser(cache_dir)


def _key(fact_type: str, item: Any) -> tuple:
    get = (lambda k: item[k]) if isinstance(item, dict) else (lambda k: getattr(item, k))
    if fact_type == "medication":
        return ("medication", get("name_raw"), get("mention_context"))
    if fact_type == "allergy":
        return ("allergy", get("substance_raw"))
    return ("event", get("event_type"), get("description_raw"))


def _type(draft: Any) -> str:
    if isinstance(draft, MedicationDraft):
        return "medication"
    if isinstance(draft, AllergyDraft):
        return "allergy"
    if isinstance(draft, EventDraft):
        return "event"
    raise TypeError("unknown draft")


def _norm(value: Any) -> Any:
    if isinstance(value, Decimal):  # "500" stays "500" (normalize() would give "5E+2")
        return str(int(value)) if value == value.to_integral() else str(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def match(gold: list[dict], drafts: list[Any]) -> tuple[list[tuple[dict, Any]], list[dict], list]:
    """(pairs, unmatched gold, unmatched predictions), one to one by key in page order."""
    by_key: dict[tuple, list] = defaultdict(list)
    for draft in sorted(drafts, key=lambda d: (d.page_number, d.char_start)):
        by_key[_key(_type(draft), draft)].append(draft)
    pairs, missed = [], []
    for fact in gold:
        candidates = by_key.get(_key(fact["fact_type"], fact), [])
        if candidates:
            pairs.append((fact, candidates.pop(0)))
        else:
            missed.append(fact)
    extra = [d for remaining in by_key.values() for d in remaining]
    return pairs, missed, extra


def to_fact(document: dict, draft: Any, fact_id: str, name: str) -> ClinicalFact:
    fact_type = _type(draft)
    base = set(type(draft).__mro__[1].model_fields)
    values = {k: v for k, v in draft.model_dump().items() if k not in base}
    return ClinicalFact(
        id=fact_id,
        fact_type=fact_type,
        document_id=document["document_id"],
        document_name=name,
        clinical_doc_type=document.get("predicted_type"),
        document_date=None,
        page_number=draft.page_number,
        source_excerpt=draft.source_excerpt,
        subject=draft.subject,
        negated=draft.negated,
        date=draft.date,
        date_precision=draft.date_precision,
        date_source=draft.date_source,
        review_status=draft.review_status,
        review_reasons=draft.review_reasons,
        values=values,
    )


# --- metrics ------------------------------------------------------------------------------------


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


class Scores:
    def __init__(self) -> None:
        self.c: Counter = Counter()
        self.errors: list[dict] = []

    def add(self, key: str, n: int = 1) -> None:
        self.c[key] += n

    def error(self, **entry: Any) -> None:
        if len(self.errors) < 400:
            self.errors.append(entry)


def _grounded(draft: Any, texts: dict[int, str]) -> bool:
    text = texts.get(draft.page_number, "")
    if text[draft.char_start : draft.char_end] != draft.source_excerpt:
        return False
    if draft.date_page_number is not None:
        date_text = texts.get(draft.date_page_number, "")
        if date_text[draft.date_char_start : draft.date_char_end] != draft.date_raw:
            return False
    return True


def evaluate(dataset: Dataset, split: str, parse: Parser) -> dict:
    s = Scores()
    patients = dataset.patients(split)
    profiles = [(p["display_name"], dt.date.fromisoformat(p["date_of_birth"])) for p in patients]
    started = time.perf_counter()
    lexicon = load_lexicon()
    for index, patient in enumerate(patients):
        swapped = profiles[(index + 1) % len(profiles)]
        facts: list[ClinicalFact] = []
        gold_of: dict[str, str] = {}  # predicted fact id -> gold fact id
        texts_by_doc: dict[str, dict[int, str]] = {}
        for doc in patient["documents"]:
            document_id = doc["document_id"]
            group = doc["render_mode"]
            ir = parse(dataset.pdf_path(document_id).read_bytes())
            texts = {p.page_number: p.text for p in ir.pages}
            texts_by_doc[document_id] = texts
            # --- system path: classify, identity (own + swapped), gate, Phase 6 type ---
            phase2 = classify_pages(ir.pages).doc_type
            own = check_identity(ir.pages, patient["display_name"], profiles[index][1]).outcome
            s.add(f"identity_own:{own}")
            other = check_identity(ir.pages, *swapped).outcome
            s.add(f"identity_swapped:{other}")
            classification = classify_clinical(ir.pages)
            predicted = None if phase2 == "lab_report" else classification.clinical_type
            expected = doc["clinical_type"]
            outcome = predicted or (
                "lab_report" if phase2 == "lab_report" else classification.abstention
            )
            want = expected or doc["expected_skip"]
            s.add("classification_total")
            if outcome == want:
                s.add("classification_correct")
            else:
                s.error(kind="classification", document=document_id, expected=want, got=outcome)
            s.add(f"classification:{want}->{outcome}")
            doc_record = {**doc, "predicted_type": predicted}
            # a swapped-profile mismatch must extract nothing (the gate refuses)
            if other == "mismatch":
                refusal = extraction_gate_refusal(
                    has_pages=True, classified=True, identity_checked=True, identity_check=other
                )
                if refusal is None:
                    s.add("mismatch_profile_facts", 1)
            gate = extraction_gate_refusal(
                has_pages=True, classified=True, identity_checked=True, identity_check=own
            )
            if gate is not None:
                s.add(f"gate_refused:{gate}")
            drafts: list[Any] = []
            if predicted is not None and gate is None:
                out = extract_clinical_facts(
                    ir, texts, predicted, reference_date=REFERENCE_DATE, lexicon=lexicon
                )
                drafts = list(out.drafts)
                for code, n in out.abstentions.items():
                    s.add(f"abstention:{code}", n)
            gold = doc["facts"] if expected else []
            # --- extraction scoring ---
            pairs, missed, extra = match(gold, drafts)
            for draft in drafts:
                if not _grounded(draft, texts):
                    s.add("ungrounded_facts")
            s.add(f"{group}:gold", len(gold))
            s.add(f"{group}:predicted", len(drafts))
            s.add(f"{group}:matched", len(pairs))
            for fact in missed:
                s.error(
                    kind="missed", document=document_id, key=list(_key(fact["fact_type"], fact))
                )
            for draft in extra:
                s.error(kind="extra", document=document_id, key=list(_key(_type(draft), draft)))
            for g, p in pairs:
                fact_type = g["fact_type"]
                s.add(f"{group}:{fact_type}:matched")
                for name in FIELDS[fact_type]:
                    s.add(f"{group}:field_total")
                    if _norm(getattr(p, name)) == g.get(name):
                        s.add(f"{group}:field_correct")
                    else:
                        s.add(f"{group}:field_error:{fact_type}.{name}")
                        s.error(
                            kind="field",
                            document=document_id,
                            field=f"{fact_type}.{name}",
                            expected=g.get(name),
                            got=_norm(getattr(p, name)),
                        )
                s.add(f"{group}:date_total")
                if all(_norm(getattr(p, f)) == g.get(f) for f in DATE_FIELDS):
                    s.add(f"{group}:date_correct")
                else:
                    s.error(
                        kind="date",
                        document=document_id,
                        key=list(_key(fact_type, g)),
                        expected=[g.get(f) for f in DATE_FIELDS],
                        got=[_norm(getattr(p, f)) for f in DATE_FIELDS],
                    )
                s.add(f"{group}:status_total")
                if sorted(g["review_reasons"]) == sorted(set(p.review_reasons)):
                    s.add(f"{group}:status_correct")
                else:
                    s.error(
                        kind="review",
                        document=document_id,
                        key=list(_key(fact_type, g)),
                        expected=g["review_reasons"],
                        got=list(p.review_reasons),
                    )
                provenance_ok = (
                    p.page_number == g["page"]
                    and g["excerpt_contains"] in p.source_excerpt
                    and _grounded(p, texts)
                )
                if provenance_ok:
                    s.add(f"{group}:provenance_correct")
            for n, draft in enumerate(drafts):
                fact_id = f"{document_id}@{draft.source_locator}#{n}"
                facts.append(to_fact(doc_record, draft, fact_id, f"{document_id}.pdf"))
            for g, p in pairs:
                n = drafts.index(p)
                gold_of[f"{document_id}@{p.source_locator}#{n}"] = g["id"]
        _score_patient(s, patient, facts, gold_of, texts_by_doc)
    s.add("duration_ms", round((time.perf_counter() - started) * 1000))
    return _metrics(s, split, dataset, len(patients))


def _score_patient(
    s: Scores,
    patient: dict,
    facts: list[ClinicalFact],
    gold_of: dict[str, str],
    texts: dict[str, dict[int, str]],
) -> None:
    gold_ids = lambda ids: {gold_of.get(i, f"unmatched:{i}") for i in ids}  # noqa: E731
    # --- conflicts ---
    predicted = {
        (c.conflict_type, tuple(sorted(gold_ids(c.member_ids)))) for c in detect_conflicts(facts)
    }
    expected = {(c["type"], tuple(sorted(c["members"]))) for c in patient["conflicts"]}
    s.add("conflict_tp", len(predicted & expected))
    s.add("conflict_fp", len(predicted - expected))
    s.add("conflict_fn", len(expected - predicted))
    for item in sorted(predicted - expected):
        s.error(kind="conflict_fp", patient=patient["patient_key"], conflict=list(item))
    for item in sorted(expected - predicted):
        s.error(kind="conflict_fn", patient=patient["patient_key"], conflict=list(item))
    # --- document comparison (gold: first vs latest prescription, medications) ---
    for gold in patient["comparisons"]:
        result = compare_documents(facts, gold["a"], gold["b"]).statuses("medication")
        got = {k.split(":", 1)[1]: v for k, v in result.items()}
        for name, status in gold["medications"].items():
            s.add("comparison_total")
            if got.get(name) == status:
                s.add("comparison_correct")
            else:
                s.error(
                    kind="comparison",
                    patient=patient["patient_key"],
                    item=name,
                    expected=status,
                    got=got.get(name),
                )
        s.add("comparison_extra", len(set(got) - set(gold["medications"])))
    # --- questions ---
    for question in patient["questions"]:
        _score_question(s, patient, question, facts, gold_ids, texts)


def _score_question(s, patient, question, facts, gold_ids, texts) -> None:
    text, intent = question["text"], question["intent"]
    s.add("qa_total")
    s.add(f"qa_kind_total:{question['kind']}")
    refusal, _ = input_gate(text)
    dispatched = clinical_route(text)
    if intent == "refused":
        routed_ok = refusal is not None and not dispatched
        s.add("refusal_probes_answered", int(dispatched))
        correct = routed_ok
    else:
        plan = route(text)
        routed_ok = dispatched and plan.intent == intent
        correct = False
        if routed_ok:
            result = clinical_answer(plan, facts, as_of=REFERENCE_DATE)
            for item in (i for sec in result.sections for i in sec.items):
                for citation in item.citations:
                    page = texts.get(citation.document_id, {}).get(citation.page_number, "")
                    if citation.excerpt not in page or not citation.excerpt:
                        s.add("invalid_citations")
            correct = _answer_correct(question, result, gold_ids)
    s.add("qa_routed_correct", int(routed_ok))
    s.add("qa_correct", int(correct))
    s.add(f"qa_kind_correct:{question['kind']}", int(correct))
    if not correct:
        s.error(kind="qa", question=question["id"], qkind=question["kind"], routed=routed_ok)


def _answer_correct(question: dict, result, gold_ids) -> bool:
    expected = question["expected"]
    kind = question["intent"]
    if kind == "abstain":
        return result.status == "abstained"
    if kind in ("medications", "allergies", "admissions", "diagnoses", "history_summary"):
        documented = gold_ids(result.fact_ids("documented") | result.fact_ids("derived"))
        uncertain = gold_ids(result.fact_ids("uncertain"))
        return documented == set(expected["documented"]) and uncertain == set(expected["uncertain"])
    if kind == "medication_mentions":
        return result.document_ids("documented") == set(expected["documents"])
    if kind == "prescriptions":
        return result.document_ids("derived") == set(expected["documents"])
    if kind == "changes":
        gold = expected["comparison"]["medications"]
        got: dict[str, str] = {}
        for item in result.section("derived"):
            name = item.text.split(":", 1)[0]
            got[name] = item.text.split(":", 1)[1].strip().split(" ")[0]
        # map printed names to generics through the lexicon, as the comparison does
        lexicon = load_lexicon()
        mapped = {(lexicon.lookup(k).generic or k.casefold()): v for k, v in got.items()}
        return mapped == gold
    if kind == "medication_conflicts":
        want = {(c["type"], tuple(sorted(c["members"]))) for c in expected["conflicts"]}
        have = {
            ("medication_same_date_mismatch", tuple(sorted(gold_ids(i.fact_ids))))
            for i in result.section("conflicting")
        }
        return want == have
    return False


def _metrics(s: Scores, split: str, dataset: Dataset, patients: int) -> dict:
    c = s.c
    groups = {}
    for group in ("native", "raster"):
        groups[group] = {
            "gold": c[f"{group}:gold"],
            "predicted": c[f"{group}:predicted"],
            "matched": c[f"{group}:matched"],
            "entity_precision": _ratio(c[f"{group}:matched"], c[f"{group}:predicted"]),
            "entity_recall": _ratio(c[f"{group}:matched"], c[f"{group}:gold"]),
            "field_exact_match": _ratio(c[f"{group}:field_correct"], c[f"{group}:field_total"]),
            "date_accuracy": _ratio(c[f"{group}:date_correct"], c[f"{group}:date_total"]),
            "review_status_agreement": _ratio(
                c[f"{group}:status_correct"], c[f"{group}:status_total"]
            ),
            "provenance_precision": _ratio(
                c[f"{group}:provenance_correct"], c[f"{group}:predicted"]
            ),
            "provenance_recall": _ratio(c[f"{group}:provenance_correct"], c[f"{group}:gold"]),
            "matched_by_type": {
                t: c[f"{group}:{t}:matched"] for t in ("medication", "allergy", "event")
            },
            "field_errors": {
                k.split(":", 2)[2]: v
                for k, v in sorted(c.items())
                if k.startswith(f"{group}:field_error:")
            },
        }
    tp, fp, fn = c["conflict_tp"], c["conflict_fp"], c["conflict_fn"]
    metrics = {
        "classification_accuracy": _ratio(c["classification_correct"], c["classification_total"]),
        "native_entity_precision": groups["native"]["entity_precision"],
        "native_entity_recall": groups["native"]["entity_recall"],
        "native_field_exact_match": groups["native"]["field_exact_match"],
        "native_date_accuracy": groups["native"]["date_accuracy"],
        "native_review_status_agreement": groups["native"]["review_status_agreement"],
        "native_provenance_precision": groups["native"]["provenance_precision"],
        "native_provenance_recall": groups["native"]["provenance_recall"],
        "conflict_precision": _ratio(tp, tp + fp),
        "conflict_recall": _ratio(tp, tp + fn),
        "comparison_accuracy": _ratio(c["comparison_correct"], c["comparison_total"]),
        "qa_routing_accuracy": _ratio(c["qa_routed_correct"], c["qa_total"]),
        "qa_correctness": _ratio(c["qa_correct"], c["qa_total"]),
    }
    kinds = sorted(k.split(":", 1)[1] for k in c if k.startswith("qa_kind_total:"))
    return {
        "dataset": dataset.version,
        "split": split,
        "patients": patients,
        "documents": c["classification_total"],
        "versions": versions(),
        "metrics": metrics,
        "targets": {k: list(v) for k, v in TARGETS.items()},
        "targets_met": {k: _met(metrics[k], TARGETS[k]) for k in TARGETS},
        "hard_gates": {g: c[g] for g in HARD_GATES},
        "groups": groups,
        "conflicts": {"tp": tp, "fp": fp, "fn": fn},
        "comparison_extra_items": c["comparison_extra"],
        "qa_by_kind": {
            k: {"total": c[f"qa_kind_total:{k}"], "correct": c[f"qa_kind_correct:{k}"]}
            for k in kinds
        },
        "classification_confusion": {
            k.split(":", 1)[1]: v for k, v in sorted(c.items()) if k.startswith("classification:")
        },
        "identity": {k: v for k, v in sorted(c.items()) if k.startswith("identity_")},
        "gate_refusals": {k: v for k, v in sorted(c.items()) if k.startswith("gate_refused:")},
        "abstentions": {k: v for k, v in sorted(c.items()) if k.startswith("abstention:")},
        "duration_ms": c["duration_ms"],
        "errors": s.errors,
    }


def _met(value: float | None, target: tuple[str, float]) -> bool:
    if value is None:
        return False
    op, bound = target
    return value <= bound if op == "<=" else value >= bound


def source_digest() -> str:
    digest = hashlib.sha256()
    paths = sorted(
        [
            p
            for root in SOURCES
            for p in root.rglob("*")
            if p.is_file() and p.suffix in (".py", ".csv")
        ]
        + list(SOURCE_FILES)
    )
    for path in paths:
        digest.update(path.as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def versions() -> dict:
    return {
        "phase2_classifier_rules_version": CLASSIFIER_RULES_VERSION,
        "identity_rules_version": IDENTITY_RULES_VERSION,
        "classifier_version": CLINICAL_CLASSIFIER_VERSION,
        "extractor_version": CLINICAL_EXTRACTOR_VERSION,
        "normalizer_version": CLINICAL_NORMALIZER_VERSION,
        "compare_version": CLINICAL_COMPARE_VERSION,
        "qa_version": CLINICAL_QA_VERSION,
        "date_policy_version": DATE_POLICY_VERSION,
        "lexicon_version": load_lexicon().version,
        "clinical_source_sha256": source_digest(),
    }


# --- reports and protocol -----------------------------------------------------------------------


def _fmt(value: object) -> str:
    return "—" if value is None else f"{value}"


def to_markdown(report: dict) -> str:
    m = report["metrics"]
    lines = [
        f"# Phase 6 clinical evaluation — {report['split']}",
        "",
        f"Dataset `{report['dataset']}`; {report['patients']} fictional patients, "
        f"{report['documents']} documents. Synthetic and not blind (the generator, the rules and "
        "the held-out-only variants share an author). `validated` means only that automated "
        "checks passed. No clinical accuracy is claimed.",
        "",
        "## Pre-registered targets",
        "",
        "| Metric | Value | Target | Met |",
        "|---|---|---|---|",
    ]
    for key, (op, bound) in TARGETS.items():
        lines.append(
            f"| {key} | {_fmt(m[key])} | {op} {bound} | "
            f"{'yes' if report['targets_met'][key] else 'NO'} |"
        )
    lines += ["", "## Hard gates (must be 0)", "", "| Gate | Count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in report["hard_gates"].items()]
    lines += ["", "## Extraction by render mode", ""]
    lines += [
        "| Group | Gold | Predicted | Matched | Entity P | Entity R | Field EM | Date acc. | "
        "Review agr. | Prov. P | Prov. R |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for group, g in report["groups"].items():
        lines.append(
            f"| {group} | {g['gold']} | {g['predicted']} | {g['matched']} | "
            f"{_fmt(g['entity_precision'])} | {_fmt(g['entity_recall'])} | "
            f"{_fmt(g['field_exact_match'])} | {_fmt(g['date_accuracy'])} | "
            f"{_fmt(g['review_status_agreement'])} | {_fmt(g['provenance_precision'])} | "
            f"{_fmt(g['provenance_recall'])} |"
        )
    for group, g in report["groups"].items():
        if g["field_errors"]:
            lines.append(
                f"\nField errors ({group}): "
                + ", ".join(f"`{k}` {v}" for k, v in g["field_errors"].items())
            )
    conflicts = report["conflicts"]
    lines += [
        "",
        "## Conflicts and comparison",
        "",
        f"Conflicts: TP {conflicts['tp']}, FP {conflicts['fp']}, FN {conflicts['fn']} "
        f"(precision {_fmt(m['conflict_precision'])}, recall {_fmt(m['conflict_recall'])}).",
        f"Comparison accuracy (first vs latest prescription, per medication): "
        f"{_fmt(m['comparison_accuracy'])}; extra compared items: "
        f"{report['comparison_extra_items']}.",
        "",
        "## Questions",
        "",
        f"Routing {_fmt(m['qa_routing_accuracy'])}, correctness {_fmt(m['qa_correctness'])}.",
        "",
        "| Kind | Correct / total |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v['correct']} / {v['total']} |" for k, v in report["qa_by_kind"].items()]
    lines += ["", "## Classification", "", "| Expected → predicted | Count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in report["classification_confusion"].items()]
    lines += ["", "## Identity and gate", ""]
    lines += [f"- {k}: {v}" for k, v in {**report["identity"], **report["gate_refusals"]}.items()]
    lines += ["", "## Abstentions", ""]
    lines += [f"- {k}: {v}" for k, v in report["abstentions"].items()] or ["- none"]
    lines += ["", "## Versions", ""]
    lines += [f"- {k}: `{v}`" for k, v in report["versions"].items()]
    lines += ["", f"Errors listed in the JSON report: {len(report['errors'])}.", ""]
    return "\n".join(lines)


def write_report(report: dict, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    name = f"clinical_{report['split']}"
    json_path = directory / f"{name}.json"
    md_path = directory / f"{name}.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(dataset: Dataset, directory: Path) -> Path:
    dev_report = directory / "clinical_dev.json"
    if not dev_report.is_file():
        raise EvaluationError("run `dev` first: there is no dev report to freeze")
    report = json.loads(dev_report.read_text(encoding="utf-8"))
    if report["versions"] != versions():
        raise EvaluationError("the dev report was produced by other versions; re-run `dev`")
    failed = {k: v for k, v in report["hard_gates"].items() if v}
    if failed:
        raise EvaluationError(f"hard gates fail on dev: {sorted(failed)}")
    selection = directory / SELECTION
    if selection.exists():
        raise EvaluationError(f"{selection} exists; a selection is frozen once")
    selection.write_text(
        json.dumps(
            {
                "frozen_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "dataset": dataset.version,
                "heldout_sha256": dataset.manifest["heldout_sha256"],
                "versions": versions(),
                "dev_report_sha256": _sha256_file(dev_report),
                "targets": {k: list(v) for k, v in TARGETS.items()},
                "hard_gates": list(HARD_GATES),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return selection


def check_heldout_allowed(dataset: Dataset, directory: Path) -> None:
    selection = directory / SELECTION
    if not selection.is_file():
        raise EvaluationError("run `freeze` first")
    frozen = json.loads(selection.read_text(encoding="utf-8"))
    if (directory / HELDOUT_DONE).exists():
        raise EvaluationError("the held-out split was already evaluated; it runs once")
    if (
        frozen["dataset"] != dataset.version
        or frozen["heldout_sha256"] != (dataset.manifest["heldout_sha256"])
    ):
        raise EvaluationError("the dataset differs from the frozen selection")
    if frozen["versions"] != versions():
        raise EvaluationError("versions or Phase 6 sources changed since freeze")


def run_heldout(dataset: Dataset, directory: Path, parse: Parser) -> tuple[Path, Path]:
    check_heldout_allowed(dataset, directory)
    (directory / HELDOUT_DONE).write_text(
        dt.datetime.now(dt.UTC).isoformat(timespec="seconds") + "\n", encoding="utf-8"
    )
    return write_report(evaluate(dataset, "heldout", parse), directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Phase 6 clinical intelligence.")
    parser.add_argument("command", choices=("dev", "freeze", "heldout"))
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--no-write", action="store_true", help="dev: print, write no report")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the Windows console default is cp1252
    dataset = load_dataset(args.dataset)
    name = dataset.manifest["dataset"]
    directory = REPORTS_DIR / name
    if args.command == "freeze":
        print(f"Frozen: {freeze(dataset, directory)}")
        return 0
    parse = docling_parser(WORK_DIR / name / "ir")
    if args.command == "heldout":
        json_path, md_path = run_heldout(dataset, directory, parse)
        print(f"Held-out report: {md_path}")
        return 0
    report = evaluate(dataset, "dev", parse)
    if args.no_write:
        print(to_markdown(report))
    else:
        json_path, md_path = write_report(report, directory)
        print(f"Dev report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
