"""Longitudinal evaluation (Phase 4; docs/PHASE_4_PLAN.md §10): load, evaluate, report.

    uv run python -m evals.run_longitudinal build      # once: writes and freezes the dataset
    uv run python -m evals.run_longitudinal dev        # load dev patients (once), evaluate
    uv run python -m evals.run_longitudinal heldout    # ONCE, after development is complete

**Loading** stores each fictional patient through the production write path: patient and document
repositories, page persistence, classification and identity records, ``record_extraction`` with
facts normalized by the production normalizers (``norm-1``, ``dates-1``), and ``review_fact`` for
the scenario's decisions. The rule-based extractor itself is not exercised (measured in Phase 3):
this evaluation isolates the longitudinal layer. Identity-mismatch documents go through the real
extraction gate, which must refuse them. Documents are uploaded in a shuffled order.

**Evaluation** calls only the longitudinal services, maps every fact id back to its dataset key,
and compares with the ground truth derived by the generator from its specification.

The database is the one configured in ``.env``; loading only adds synthetic patients
(``is_synthetic=True``), recorded in ``evals/work/longitudinal_v0/`` so a rerun reuses them.
"""

import argparse
import datetime as dt
import hashlib
import json
import statistics
import time
import uuid
from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.errors import DocumentProcessingError, PatientError
from app.extraction.normalize.analytes import map_analyte
from app.extraction.normalize.dates import parse_date
from app.extraction.normalize.numbers import parse_value, text_key
from app.extraction.normalize.ranges import compare_to_range, parse_range
from app.extraction.normalize.units import normalize_unit
from app.extraction.reference import Reference, load_reference
from app.extraction.versions import DATE_POLICY_VERSION, NORMALIZER_VERSION
from app.longitudinal.model import LONGITUDINAL_VERSION
from app.records import facts as repo
from app.records import repositories as base
from app.services import library, review_fact
from app.services import longitudinal as service
from app.services.fact_conflicts import build_detector
from app.services.reference_data import seed_reference
from evals.synthetic import longitudinal as gen

DATASET_DIR = Path("data/synthetic") / gen.DATASET
REPORTS_DIR = Path("evals/reports") / gen.DATASET
WORK_DIR = Path("evals/work") / gen.DATASET
HELDOUT_DONE = REPORTS_DIR / "longitudinal_heldout.done"
LOADER_VERSION = "longitudinal-loader-1"

# Pre-registered before the first dev run (docs/PHASE_4_PLAN.md §10.4). Ratios; leaks are counts.
TARGETS: dict[str, tuple[str, float]] = {
    "timeline_entry_precision": (">=", 1.0),
    "timeline_entry_recall": (">=", 1.0),
    "timeline_order_accuracy": (">=", 1.0),
    "date_source_accuracy": (">=", 1.0),
    "trend_point_accuracy": (">=", 1.0),
    "trend_change_accuracy": (">=", 1.0),
    "unit_series_accuracy": (">=", 1.0),
    "duplicate_precision": (">=", 0.95),
    "duplicate_recall": (">=", 0.95),
    "false_duplicate_rate": ("<=", 0.05),
    "conflict_precision": (">=", 1.0),
    "conflict_recall": (">=", 1.0),
    "provenance_completeness": (">=", 1.0),
    "corrected_value_accuracy": (">=", 1.0),
    "identity_uncertainty_shown": (">=", 1.0),
    "patient_isolation_leaks": ("<=", 0),
    "rejected_shown": ("<=", 0),
    "needs_review_shown": ("<=", 0),
    "mismatch_shown": ("<=", 0),
}


# --- loading -------------------------------------------------------------------------------------


def _observation(o: dict, doc: dict, reference: Reference) -> repo.ObservationRecord:
    analyte = map_analyte(o["analyte_raw"], reference)
    value = parse_value(o["value_raw"])
    unit = normalize_unit(o["unit_raw"], value.value_num, analyte, reference)
    printed = parse_range(o["ref_range_raw"], unit_key=text_key(o["unit_raw"]))
    reasons = [*unit.reasons] + ([value.reason] if value.reason else [])
    notes = [*unit.notes]
    if analyte is None:
        reasons.append("unmapped_analyte")
    dates = doc["dates"]
    chosen, source = (
        (dates["Collected"], "collection")
        if "Collected" in dates
        else (dates.get("Report Date"), "report")
    )
    date = precision = None
    if chosen is not None:
        parsed = parse_date(chosen["raw"], gen.REFERENCE_DATE)
        date, precision = parsed.value, parsed.precision
        reasons += parsed.reasons
        if source == "report":
            notes.append("effective_date_from_report")
    else:
        notes.append("no_effective_date")
    codes = sorted({str(r) for r in reasons})
    return repo.ObservationRecord(
        page_number=1,
        source_locator=o["locator"],
        source_excerpt=doc["page_text"][o["char_start"] : o["char_end"]],
        char_start=o["char_start"],
        char_end=o["char_end"],
        analyte_raw=o["analyte_raw"],
        analyte_name=analyte.canonical_name if analyte else None,
        analyte_match="alias_exact" if analyte else None,
        value_raw=o["value_raw"],
        value_num=value.value_num,
        comparator=value.comparator,
        value_qualitative=value.qualitative,
        unit_raw=o["unit_raw"],
        unit_norm=unit.unit_norm,
        value_num_norm=unit.value_num_norm,
        ref_range_raw=o["ref_range_raw"],
        ref_low=printed.low,
        ref_high=printed.high,
        flag_as_printed=None,
        ref_range_comparison=compare_to_range(value.value_num, value.comparator, printed),
        specimen=None,
        effective_date=date,
        date_precision=precision,
        date_raw=chosen["raw"] if chosen and date else None,
        effective_date_source=source if date else None,
        date_page_number=1 if date else None,
        date_char_start=chosen["char_start"] if chosen and date else None,
        date_char_end=chosen["char_end"] if chosen and date else None,
        effective_time=None,
        time_raw=None,
        review_status="needs_review" if codes else "validated",
        review_reasons=tuple(codes),
        quality_notes=tuple(sorted({str(n) for n in notes})),
    )


def load_patient(patient: dict, reference: Reference) -> dict[str, Any]:
    """Store one patient; returns ids by dataset key and the gate refusals observed."""
    ref = library.create_patient(
        patient["display_name"],
        is_synthetic=True,
        date_of_birth=dt.date.fromisoformat(patient["date_of_birth"]),
    )
    pid = ref.id
    versions = {
        "extractor_version": LOADER_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "reference_data_version": reference.version,
        "date_policy_version": DATE_POLICY_VERSION,
    }
    detector = build_detector(reference)
    docs = {d["doc_key"]: d for d in patient["documents"]}
    doc_ids: dict[str, str] = {}
    refusals: list[str] = []
    for key in patient["upload_order"]:  # shuffled: upload order is not clinical order
        doc = docs[key]
        text = doc["page_text"]
        sha = hashlib.sha256(f"{key}\n{text}".encode()).hexdigest()
        created = base.create_document(
            pid,
            original_filename=doc["filename"],
            storage_path=f"uploads/{pid}/{sha}.pdf",
            sha256=sha,
            size_bytes=len(text.encode("utf-8")),
            page_count=1,
        ).document
        claim = base.claim_document_for_parsing(pid, created.id)
        base.persist_parsed_pages(
            pid,
            created.id,
            claim.run_id,
            (base.PageRecord(1, text, "native", bool(doc["observations"])),),
        )
        base.record_classification(
            pid, created.id, doc_type=doc["doc_type"], score=0.9, rules_version="2", duration_ms=1
        )
        code = {"match": "name_match", "mismatch": "name_mismatch", "unknown": "name_not_found"}
        base.record_identity_check(
            pid,
            created.id,
            outcome=doc["identity"],
            reason_codes=(code[doc["identity"]],),
            rules_version="2",
            duration_ms=1,
        )
        observations = [_observation(o, doc, reference) for o in doc["observations"]]
        report = doc["dates"].get("Report Date")
        report_date = None
        if report:
            parsed = parse_date(report["raw"], gen.REFERENCE_DATE)
            if parsed.value is not None and not parsed.reasons:
                report_date = repo.ReportDate(
                    parsed.value, parsed.precision or "day", report["raw"]
                )
        try:
            if doc["doc_type"] != "lab_report":
                repo.record_extraction_skipped(pid, created.id, versions=versions, duration_ms=1)
            else:
                repo.record_extraction(
                    pid,
                    created.id,
                    observations=observations,
                    versions=versions,
                    reference_date=gen.REFERENCE_DATE,
                    abstentions={},
                    report_date=report_date,
                    duration_ms=1,
                    detector=detector,
                )
        except DocumentProcessingError as exc:
            refusals.append(f"{key}:{exc.kind}")
        doc_ids[key] = str(created.id)
    fact_ids: dict[str, str] = {}
    for key, doc in docs.items():
        if not doc["observations"]:
            continue
        stored = {
            f.source_locator: f.id for f in repo.list_document_facts(pid, uuid.UUID(doc_ids[key]))
        }
        for o in doc["observations"]:
            if o["locator"] in stored:
                fact_ids[o["key"]] = str(stored[o["locator"]])
    for doc in patient["documents"]:  # decisions in document order, as specified
        for decision in doc["decisions"]:
            correction = (
                review_fact.Correction(value=decision["value"])
                if decision["decision"] == "correct"
                else None
            )
            review_fact.review_fact(
                pid,
                uuid.UUID(fact_ids[decision["key"]]),
                decision["decision"],
                correction=correction,
            )
    return {"patient_id": str(pid), "documents": doc_ids, "facts": fact_ids, "refusals": refusals}


def load_split(split: str, patients: list[dict]) -> dict[str, Any]:
    work = WORK_DIR / f"load_{split}.json"
    if work.exists():
        loaded = json.loads(work.read_text(encoding="utf-8"))
        try:
            for item in loaded["patients"].values():
                library.get_patient(uuid.UUID(item["patient_id"]))
            return loaded
        except PatientError:
            raise SystemExit(
                f"{work} names patients that no longer exist; remove it to reload"
            ) from None
    seed_reference()
    reference = load_reference()
    started = time.perf_counter()
    loaded = {"patients": {p["patient_key"]: load_patient(p, reference) for p in patients}}
    loaded["load_seconds"] = round(time.perf_counter() - started, 3)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    work.write_text(json.dumps(loaded, indent=1), encoding="utf-8")
    return loaded


# --- scoring -------------------------------------------------------------------------------------


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def _pairs_from_groups(groups, key_of) -> dict[str, set[frozenset[str]]]:
    found: dict[str, set[frozenset[str]]] = {"exact": set(), "possible": set()}
    for view in groups:
        for pair in view.group.pairs:
            found[pair.kind].add(frozenset({key_of[pair.a], key_of[pair.b]}))
    return found


def _gt_changes(name: str, points: list[dict]) -> set[tuple]:
    """Consecutive differences per unit, skipping dates that hold several points (no times)."""
    changes = set()
    by_unit: dict[str, list[dict]] = {}
    for p in points:
        by_unit.setdefault(p["unit"], []).append(p)
    for unit, series in by_unit.items():
        series.sort(key=lambda p: p["date"])
        crowded = {d for d, n in Counter(p["date"] for p in series).items() if n > 1}
        for a, b in zip(series, series[1:], strict=False):
            if a["date"] in crowded or b["date"] in crowded:
                continue
            changes.add(
                (name, unit, a["date"], b["date"], Decimal(b["value"]) - Decimal(a["value"]))
            )
    return changes


def _timed(timings: dict[str, list[float]], name: str, call: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    result = call()
    timings.setdefault(name, []).append((time.perf_counter() - started) * 1000)
    return result


def evaluate_patient(
    patient: dict, loaded: dict, other_patient_id: str | None, timings: dict[str, list[float]]
) -> Counter:
    truth = patient["truth"]
    pid = uuid.UUID(loaded["patient_id"])
    key_of = {v: k for k, v in loaded["facts"].items()}
    doc_key_of = {v: k for k, v in loaded["documents"].items()}
    gt_facts = truth["facts"]
    c: Counter = Counter()
    seen: set[str] = set()

    def note_seen(fact_id: str) -> str | None:
        seen.add(fact_id)
        if fact_id not in key_of:
            c["patient_isolation_leaks"] += 1
            return None
        return key_of[fact_id]

    # timeline (all pages, oldest first)
    entries, page = [], 1
    while True:
        view = _timed(
            timings, "timeline_page", lambda p=page: service.timeline(pid, page=p, page_size=200)
        )
        entries += view.page.entries
        if page >= view.page.pages:
            break
        page += 1
    expected = {frozenset(e["keys"]): e for e in truth["timeline"]}
    system: list[tuple[frozenset[str], Any]] = []
    for e in entries:
        if e.kind == "document":
            system.append((frozenset({doc_key_of.get(e.entry_id, "?")}), e))
        else:
            keys = frozenset(k for s in e.sources if (k := note_seen(s.fact_id)))
            system.append((keys, e))
    matched = [(k, e) for k, e in system if k in expected]
    c["timeline_system"] += len(system)
    c["timeline_truth"] += len(expected)
    c["timeline_matched"] += len(matched)
    for keys, e in matched:
        gt = expected[keys]
        c["date_source_total"] += 1
        same = (e.date.isoformat(), e.precision, e.date_source) == (
            gt["date"],
            gt["precision"],
            gt["date_source"],
        )
        c["date_source_correct"] += same
        if any(gt_facts[k]["identity_unknown"] for k in keys if k in gt_facts):
            c["identity_unknown_entries"] += 1
            c["identity_unknown_flagged"] += e.identity_uncertain
    for i in range(len(matched)):
        for j in range(i + 1, len(matched)):
            a, b = expected[matched[i][0]]["date"], expected[matched[j][0]]["date"]
            if a != b:
                c["order_pairs"] += 1
                c["order_correct"] += a < b
    undated = {note_seen(o.fact_id) for o in view.page.undated_observations}
    c["undated_correct"] += undated == set(truth["undated_facts"])
    c["undated_checked"] += 1
    undated_docs = {doc_key_of.get(d.document_id) for d in view.page.undated_documents}
    c["undated_documents_correct"] += undated_docs == set(truth["undated_documents"])
    c["mismatch_count_correct"] += (
        view.page.excluded_mismatch_documents == truth["excluded_mismatch_documents"]
    )

    # trends: every analyte the truth or the system knows
    names = set(truth["trends"]) | {
        a.name for a in _timed(timings, "analytes", lambda: service.analytes(pid))
    }
    system_changes: set[tuple] = set()
    for name in sorted(names):
        tv = _timed(timings, "trend", lambda n=name: service.trend(pid, n))
        gt_points = {frozenset(p["keys"]): p for p in truth["trends"].get(name, [])}
        c["trend_truth_points"] += len(gt_points)
        for series in tv.trend.series:
            for point in series.points:
                keys = frozenset(k for s in point.sources if (k := note_seen(s.fact_id)))
                for k in keys:
                    c["unit_series_total"] += 1
                    c["unit_series_correct"] += gt_facts.get(k, {}).get("unit") == series.unit
                if point.precision != "day":
                    continue
                c["trend_system_points"] += 1
                gt = gt_points.get(keys)
                if gt and (point.date.isoformat(), series.unit, point.observation.value) == (
                    gt["date"],
                    gt["unit"],
                    Decimal(gt["value"]),
                ):
                    c["trend_correct_points"] += 1
            for change in series.changes:
                system_changes.add(
                    (
                        name,
                        series.unit,
                        change.from_date.isoformat(),
                        change.to_date.isoformat(),
                        change.delta,
                    )
                )
        for conflict in tv.conflicts:  # presented conflicts must show stored members only
            for m in conflict.members:
                note_seen(str(m.view.fact.id))
    gt_changes = set()
    for name, points in truth["trends"].items():
        gt_changes |= _gt_changes(name, points)
    c["change_truth"] += len(gt_changes)
    c["change_system"] += len(system_changes)
    c["change_correct"] += len(gt_changes & system_changes)

    # duplicates and conflicts
    groups = _timed(timings, "duplicates", lambda: service.duplicates(pid))
    for g in groups:
        for m in g.members:
            note_seen(m.fact_id)
    found = _pairs_from_groups(groups, key_of)
    gt_exact = {frozenset(p) for p in truth["exact_pairs"]}
    gt_possible = {frozenset(p) for p in truth["possible_pairs"]}
    every_found = found["exact"] | found["possible"]
    every_gt = gt_exact | gt_possible
    c["dup_found"] += len(every_found)
    c["dup_truth"] += len(every_gt)
    c["dup_correct"] += len(every_found & every_gt)
    c["dup_kind_correct"] += len(found["exact"] & gt_exact) + len(found["possible"] & gt_possible)
    c["exact_found"] += len(found["exact"])
    c["exact_correct"] += len(found["exact"] & gt_exact)
    c["possible_found"] += len(found["possible"])
    c["possible_correct"] += len(found["possible"] & gt_possible)
    conflicts = _timed(timings, "conflicts", lambda: service.conflicts(pid))
    found_conflicts = {
        frozenset({key_of.get(str(a)), key_of.get(str(b))})
        for x in conflicts
        for a, b, _ in x.direct_pairs
    }
    gt_conflicts = {frozenset(p) for p in truth["conflict_pairs"]}
    c["conflict_found"] += len(found_conflicts)
    c["conflict_truth"] += len(gt_conflicts)
    c["conflict_correct"] += len(found_conflicts & gt_conflicts)
    _timed(timings, "overview", lambda: service.overview(pid))

    # safety: excluded facts never shown; corrected values shown; provenance exact
    for fact_id in seen:
        key = key_of.get(fact_id)
        excluded = gt_facts.get(key, {}).get("excluded") if key else None
        if excluded == "rejected":
            c["rejected_shown"] += 1
        elif excluded == "needs_review":
            c["needs_review_shown"] += 1
        elif excluded == "mismatch":
            c["mismatch_shown"] += 1
    mismatch_docs = {k for k, d in truth["documents"].items() if d["excluded"]}
    c["mismatch_shown"] += sum(
        1 for keys, e in system if e.kind == "document" and keys & mismatch_docs
    )
    pages: dict[str, str] = {}
    for _keys, e in system:
        for s in e.sources:
            c["provenance_total"] += 1
            if s.document_id not in pages:
                (page_record,) = base.list_document_pages(pid, uuid.UUID(s.document_id))
                pages[s.document_id] = page_record.text
            ev = service.evidence(pid, uuid.UUID(s.fact_id))
            f = ev.view.fact
            exact = pages[s.document_id][f.char_start : f.char_end] == f.source_excerpt
            complete = exact and f.page_number == 1 and f.source_locator and f.extraction_run_id
            c["provenance_complete"] += bool(complete)
            key = key_of.get(s.fact_id)
            if key and gt_facts[key]["status"] == "corrected":
                c["corrected_total"] += 1
                c["corrected_correct"] += (s.value, s.unit) == (
                    Decimal(gt_facts[key]["value"]),
                    gt_facts[key]["unit"],
                )
    if other_patient_id is not None and key_of:  # another patient's id never reaches this data
        try:
            service.evidence(uuid.UUID(other_patient_id), uuid.UUID(next(iter(key_of))))
            c["patient_isolation_leaks"] += 1
        except DocumentProcessingError:
            pass
        c["patient_isolation_leaks"] += sum(
            1
            for e in service.timeline(uuid.UUID(other_patient_id), page_size=200).page.entries
            for s in e.sources
            if s.fact_id in key_of
        )
    c["gate_refusals_mismatch"] += sum(r.endswith(":identity_mismatch") for r in loaded["refusals"])
    return c


def summarize(c: Counter) -> dict[str, Any]:
    return {
        "timeline_entry_precision": _ratio(c["timeline_matched"], c["timeline_system"]),
        "timeline_entry_recall": _ratio(c["timeline_matched"], c["timeline_truth"]),
        "timeline_order_accuracy": _ratio(c["order_correct"], c["order_pairs"]),
        "date_source_accuracy": _ratio(c["date_source_correct"], c["date_source_total"]),
        "trend_point_accuracy": _ratio(
            c["trend_correct_points"], max(c["trend_truth_points"], c["trend_system_points"])
        ),
        "trend_change_accuracy": _ratio(
            c["change_correct"], max(c["change_truth"], c["change_system"])
        ),
        "unit_series_accuracy": _ratio(c["unit_series_correct"], c["unit_series_total"]),
        "duplicate_precision": _ratio(c["dup_correct"], c["dup_found"]),
        "duplicate_recall": _ratio(c["dup_correct"], c["dup_truth"]),
        "duplicate_kind_accuracy": _ratio(c["dup_kind_correct"], c["dup_correct"]),
        "exact_duplicate_precision": _ratio(c["exact_correct"], c["exact_found"]),
        "possible_duplicate_precision": _ratio(c["possible_correct"], c["possible_found"]),
        "false_duplicate_rate": _ratio(c["dup_found"] - c["dup_correct"], c["dup_found"]),
        "conflict_precision": _ratio(c["conflict_correct"], c["conflict_found"]),
        "conflict_recall": _ratio(c["conflict_correct"], c["conflict_truth"]),
        "provenance_completeness": _ratio(c["provenance_complete"], c["provenance_total"]),
        "corrected_value_accuracy": _ratio(c["corrected_correct"], c["corrected_total"]),
        "identity_uncertainty_shown": _ratio(
            c["identity_unknown_flagged"], c["identity_unknown_entries"]
        ),
        "undated_lists_correct": _ratio(
            c["undated_correct"] + c["undated_documents_correct"], 2 * c["undated_checked"]
        ),
        "mismatch_count_correct": _ratio(c["mismatch_count_correct"], c["undated_checked"]),
        "patient_isolation_leaks": c["patient_isolation_leaks"],
        "rejected_shown": c["rejected_shown"],
        "needs_review_shown": c["needs_review_shown"],
        "mismatch_shown": c["mismatch_shown"],
        "mismatch_gate_refusals": c["gate_refusals_mismatch"],
    }


def check_targets(metrics: dict[str, Any]) -> dict[str, bool | None]:
    result: dict[str, bool | None] = {}
    for name, (op, bound) in TARGETS.items():
        value = metrics.get(name)
        result[name] = None if value is None else (value >= bound if op == ">=" else value <= bound)
    return result


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
        "max_ms": round(ordered[-1], 2),
    }


def evaluate(split: str, patients: list[dict], loaded: dict) -> dict[str, Any]:
    totals: Counter = Counter()
    performance: dict[str, Any] = {}
    timings: dict[str, list[float]] = {}
    keys = [p["patient_key"] for p in patients]
    for i, patient in enumerate(patients):
        item = loaded["patients"][patient["patient_key"]]
        other = loaded["patients"][keys[i - 1]]["patient_id"] if len(keys) > 1 else None
        if patient["role"] == "performance":
            perf_timings: dict[str, list[float]] = {}
            counts = evaluate_patient(patient, item, other, perf_timings)
            performance = {
                "patient": patient["patient_key"],
                "documents": len(patient["documents"]),
                "facts": len(patient["truth"]["facts"]),
                "timings": {k: _percentiles(v) for k, v in perf_timings.items()},
                "metrics": summarize(counts),
            }
            continue
        totals += evaluate_patient(patient, item, other, timings)
    metrics = summarize(totals)
    return {
        "dataset": gen.DATASET,
        "split": split,
        "versions": {
            "longitudinal": LONGITUDINAL_VERSION,
            "loader": LOADER_VERSION,
            "generator": gen.GENERATOR_VERSION,
            "normalizer": NORMALIZER_VERSION,
        },
        "patients": [k for k in keys],
        "metrics": metrics,
        "counts": dict(sorted(totals.items())),
        "targets": {k: list(v) for k, v in TARGETS.items()},
        "targets_met": check_targets(metrics),
        "timings": {k: _percentiles(v) for k, v in timings.items()},
        "performance_patient": performance,
        "load_seconds": loaded.get("load_seconds"),
        "note": "Synthetic, fictional data; rules and data by the same author; not a blind test; "
        "no clinical accuracy is claimed.",
    }


def write_report(report: dict, name: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{name}.json").write_text(
        json.dumps(report, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    lines = [
        f"# Longitudinal evaluation: {report['split']} ({report['dataset']})",
        "",
        f"> {report['note']}",
        "",
        f"Patients: {', '.join(report['patients'])}. Versions: {report['versions']}.",
        "",
        "| Metric | Value | Target | Met |",
        "|---|---|---|---|",
    ]
    for name_, value in report["metrics"].items():
        target = TARGETS.get(name_)
        met = report["targets_met"].get(name_)
        lines.append(
            f"| {name_} | {value} | {' '.join(map(str, target)) if target else ''} | "
            f"{'' if met is None else ('yes' if met else '**no**')} |"
        )
    lines += [
        "",
        "## Timings (scenario patients)",
        "",
        "| Call | n | median ms | p95 ms | max ms |",
        "|---|---|---|---|---|",
    ]
    for call, t in report["timings"].items():
        lines.append(f"| {call} | {t['n']} | {t['median_ms']} | {t['p95_ms']} | {t['max_ms']} |")
    perf = report["performance_patient"]
    if perf:
        lines += [
            "",
            f"## Performance patient ({perf['documents']} documents, {perf['facts']} facts)",
            "",
            "| Call | n | median ms | p95 ms | max ms |",
            "|---|---|---|---|---|",
        ]
        for call, t in perf["timings"].items():
            lines.append(
                f"| {call} | {t['n']} | {t['median_ms']} | {t['p95_ms']} | {t['max_ms']} |"
            )
    lines += ["", "## Counts", "", "```", json.dumps(report["counts"], indent=1), "```", ""]
    (REPORTS_DIR / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("build", "dev", "heldout"))
    args = parser.parse_args(argv)
    if args.command == "build":
        manifest = gen.build(DATASET_DIR)
        print(f"Built {gen.DATASET}; held-out sha256 {manifest['sha256']['heldout']}")
        return 0
    if args.command == "heldout" and HELDOUT_DONE.exists():
        raise SystemExit("The held-out split was already evaluated once; it is not rerun.")
    patients = gen.load_split(DATASET_DIR, args.command)
    loaded = load_split(args.command, patients)
    report = evaluate(args.command, patients, loaded)
    write_report(report, f"longitudinal_{args.command}")
    if args.command == "heldout":
        HELDOUT_DONE.write_text(dt.datetime.now(dt.UTC).isoformat() + "\n", encoding="utf-8")
    failed = [k for k, ok in report["targets_met"].items() if ok is False]
    print(json.dumps(report["metrics"], indent=1))
    print("targets not met:", failed or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
