"""Synthetic longitudinal dataset ``longitudinal_v0`` (Phase 4; docs/PHASE_4_PLAN.md §10).

Fictional patients only. Each document is a one-page lab report (or one non-lab note) given as
exact page text with the character span of every result row and printed date, so the evaluation
loader can store facts through the production repository with exact provenance.

The ground truth is derived here from the *scenario specification* (this module's own unit
table, date-choice rule, ambiguity rule and the intended duplicate and conflict pairs). It never
calls the code it evaluates. Every scenario patient covers every case below, so both splits do:

  C1  a report printing both a collection and a report date (results dated by collection)
  C2  the same report uploaded twice                       -> exact duplicates
  C3  another lab's report of the same sample (g/L, other ranges, 'Hb') -> possible duplicates
  C4  another lab's report the same day with a different HbA1c -> one conflict
  C5  fasting sugar printed in mmol/L on its own date      -> converted into the mg/dL series
  C6  haemoglobin printed in mmol/L (no conversion defined) -> its own series
  C7  a corrected value; C8 a rejected value
  C9  a report with no date                                -> undated results and document
  C10 an ambiguous date (05/03): needs review; verified on odd-numbered patients
  C11 a month-only date ('Mar 2023')
  C12 identity unknown (C5's report);  C13 identity mismatch (nothing may appear)
  C14 a non-lab document (not dated by Phase 3)
Documents are uploaded in a shuffled order, so upload order never matches clinical order.
"""

import datetime as dt
import hashlib
import json
import random
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

GENERATOR_VERSION: Final = "longitudinal-gen-1"
DATASET: Final = "longitudinal_v0"
SEED: Final = 20261118
REFERENCE_DATE: Final = dt.date(2026, 11, 17)
HELDOUT: Final = ("L03", "L07", "L10", "L13", "L16")
SCENARIO_PATIENTS: Final = tuple(f"L{i:02d}" for i in range(1, 17))
PERFORMANCE_PATIENT: Final = "L99"

# The specification's own unit facts (they agree with the reference data by design; the
# evaluation checks that the system agrees with them).
CANONICAL_UNIT: Final = {
    "gm/dL": "g/dL",
    "g/L": "g/L",
    "mg/dL": "mg/dL",
    "mmol/L": "mmol/L",
    "%": "%",
    "fL": "fL",
    "µIU/mL": "µIU/mL",
}
CONVERSION: Final = {
    ("Haemoglobin", "g/L"): ("g/dL", Decimal("0.1")),
    ("Fasting Blood Sugar", "mmol/L"): ("mg/dL", Decimal("18.0156")),
}


@dataclass(frozen=True)
class Item:
    printed: str
    unit: str
    range_a: str
    range_b: str
    base: Decimal
    step: Decimal


PANEL: Final = {
    "Haemoglobin": Item(
        "Haemoglobin", "gm/dL", "13.0 - 17.0", "12.0 - 16.0", Decimal("13.8"), Decimal("0.3")
    ),
    "Fasting Blood Sugar": Item(
        "Fasting Blood Sugar", "mg/dL", "70 - 100", "70 - 110", Decimal("96"), Decimal("4")
    ),
    "HbA1c": Item("HbA1c", "%", "4.0 - 5.6", "4.0 - 6.0", Decimal("5.6"), Decimal("0.2")),
    "Serum Creatinine": Item(
        "Serum Creatinine", "mg/dL", "0.70 - 1.30", "0.60 - 1.20", Decimal("0.92"), Decimal("0.05")
    ),
    "TSH": Item("TSH", "µIU/mL", "0.40 - 4.50", "0.50 - 5.00", Decimal("2.10"), Decimal("0.25")),
    "MCV": Item("MCV", "fL", "80 - 100", "83 - 101", Decimal("88"), Decimal("2")),
    "Total Cholesterol": Item(
        "Total Cholesterol", "mg/dL", "< 200", "< 190", Decimal("182"), Decimal("6")
    ),
}
EXTRA: Final = ("Serum Creatinine", "TSH", "MCV", "Total Cholesterol")
LABS: Final = {"A": "Fictional Alpha Diagnostics", "B": "Fictional Beta Pathology"}
FIRST: Final = (
    "Aarav",
    "Diya",
    "Kabir",
    "Meera",
    "Vivaan",
    "Anaya",
    "Ishaan",
    "Saanvi",
    "Reyansh",
    "Kiara",
    "Arjun",
    "Myra",
    "Advik",
    "Tara",
    "Dhruv",
    "Navya",
    "Om",
)
LAST: Final = "Trendwala"


def _fmt(value: Decimal, places: int) -> str:
    return f"{value:.{places}f}"


def _places(step: Decimal) -> int:
    return max(0, -step.as_tuple().exponent)  # type: ignore[operator]


def _value(rng: random.Random, analyte: str) -> str:
    item = PANEL[analyte]
    return _fmt(item.base + item.step * rng.randint(-5, 5), _places(item.step))


def _dmy(d: dt.date) -> str:
    return d.strftime("%d/%m/%Y")


def _page(
    lab: str,
    name: str,
    dates: list[tuple[str, str]],
    rows: list[tuple[str, ...]],
    title: str = "Laboratory Report",
) -> tuple[str, dict, list]:
    """Page text plus spans: {label: (raw, start, end)} and [(excerpt, start, end)] per row."""
    text = f"{LABS[lab]}\n{title}\nPatient: {name}\n"
    spans: dict[str, tuple[str, int, int]] = {}
    for label, raw in dates:
        prefix = f"{label}: "
        start = len(text) + len(prefix)
        spans[label] = (raw, start, start + len(raw))
        text += f"{prefix}{raw}\n"
    row_spans = []
    if rows:
        text += "Test\tResult\tUnit\tReference Range\n"
        for cells in rows:
            excerpt = "\t".join(cells)
            row_spans.append((excerpt, len(text), len(text) + len(excerpt)))
            text += excerpt + "\n"
    return text, spans, row_spans


def _document(
    key: str,
    lab: str,
    name: str,
    *,
    dates: list[tuple[str, str]],
    rows: list[tuple[str, str, str, str]],
    doc_type: str = "lab_report",
    identity: str = "match",
    title: str = "Laboratory Report",
) -> dict:
    text, date_spans, row_spans = _page(lab, name, dates, rows, title)
    observations = [
        {
            "key": f"{key}#{i + 1}",
            "locator": f"p1:t0:r{i + 1}",
            "analyte": analyte,
            "analyte_raw": printed,
            "value_raw": value,
            "unit_raw": unit,
            "ref_range_raw": rng_text,
            "char_start": start,
            "char_end": end,
        }
        for i, ((printed, value, unit, rng_text), (_excerpt, start, end), analyte) in enumerate(
            zip(rows, row_spans, [_analyte_of(r[0]) for r in rows], strict=True)
        )
    ]
    return {
        "doc_key": key,
        "doc_type": doc_type,
        "identity": identity,
        "lab": lab,
        "filename": f"{key}.pdf",
        "page_text": text,
        "dates": {
            label: {"raw": raw, "char_start": s, "char_end": e}
            for label, (raw, s, e) in date_spans.items()
        },
        "observations": observations,
        "decisions": [],
    }


_ALIASES = {"Hb": "Haemoglobin"}


def _analyte_of(printed: str) -> str:
    return _ALIASES.get(printed, printed)


def _rows(analytes: list[str], values: dict[str, str], lab: str) -> list[tuple[str, str, str, str]]:
    return [
        (
            PANEL[a].printed,
            values[a],
            PANEL[a].unit,
            PANEL[a].range_a if lab == "A" else PANEL[a].range_b,
        )
        for a in analytes
    ]


def _visit_dates(rng: random.Random, count: int) -> list[dt.date]:
    """Distinct non-ambiguous days (day > 12), chronological, 2021-2025, >= 40 days apart."""
    dates: list[dt.date] = []
    day = dt.date(2021, 2, 1) + dt.timedelta(days=rng.randint(0, 120))
    while len(dates) < count:
        day += dt.timedelta(days=rng.randint(60, 220))
        dates.append(day.replace(day=max(13, min(day.day, 28))))
    return dates


def scenario_patient(index: int, rng: random.Random) -> dict:
    key = f"L{index:02d}"
    name = f"{FIRST[index - 1]} {LAST}"
    panel = ["Haemoglobin", "Fasting Blood Sugar", "HbA1c", EXTRA[index % len(EXTRA)]]
    visits = _visit_dates(rng, 6)
    docs: list[dict] = []
    values = {v: {a: _value(rng, a) for a in panel} for v in range(5)}

    def d(n: int) -> str:
        return f"{key}-D{n:02d}"

    # visits 0..4 at lab A; visit 1 prints both dates (C1); others alternate the printed label
    for v in range(5):
        if v == 1:
            dates = [
                ("Collected", _dmy(visits[v])),
                ("Report Date", _dmy(visits[v] + dt.timedelta(days=2))),
            ]
        elif v % 2 == 0:
            dates = [("Report Date", _dmy(visits[v]))]
        else:
            dates = [("Collected", _dmy(visits[v]))]
        docs.append(_document(d(v + 1), "A", name, dates=dates, rows=_rows(panel, values[v], "A")))
    # C2: visit 0 uploaded again (same bytes of text; another file)
    copy = json.loads(json.dumps(docs[0]))
    copy["doc_key"], copy["filename"] = d(6), f"{d(6)}.pdf"
    for o in copy["observations"]:
        o["key"] = o["key"].replace(d(1), d(6))
    docs.append(copy)
    # C3: lab B, same sample as visit 2: Hb as g/L with alias, FBS same value other range
    hb = Decimal(values[2]["Haemoglobin"]) * 10
    docs.append(
        _document(
            d(7),
            "B",
            name,
            dates=[("Collected", _dmy(visits[2]))],
            rows=[
                ("Hb", _fmt(hb, 0), "g/L", "120 - 160"),
                (
                    PANEL["Fasting Blood Sugar"].printed,
                    values[2]["Fasting Blood Sugar"],
                    "mg/dL",
                    "70 - 110",
                ),
            ],
        )
    )
    # C4: lab B, same day as visit 3 (printed collection date), HbA1c clearly different
    other = Decimal(values[3]["HbA1c"]) + Decimal("1.5")
    docs.append(
        _document(
            d(8),
            "B",
            name,
            dates=[("Collected", _dmy(visits[3]))],
            rows=[("HbA1c", _fmt(other, 1), "%", "4.0 - 6.0")],
        )
    )
    # C5/C6/C12: lab B on its own date, FBS in mmol/L and Hb in mmol/L, identity unknown
    mmol = Decimal(rng.randint(48, 62)) / 10
    docs.append(
        _document(
            d(9),
            "B",
            name,
            dates=[("Collected", _dmy(visits[5]))],
            rows=[
                ("Fasting Blood Sugar", _fmt(mmol, 1), "mmol/L", "3.9 - 5.6"),
                ("Haemoglobin", _fmt(Decimal(rng.randint(80, 95)) / 10, 1), "mmol/L", "7.5 - 10.0"),
            ],
            identity="unknown",
        )
    )
    # C9: no date
    docs.append(
        _document(
            d(10),
            "A",
            name,
            dates=[],
            rows=_rows(
                ["Haemoglobin", "Fasting Blood Sugar"],
                {
                    "Haemoglobin": _value(rng, "Haemoglobin"),
                    "Fasting Blood Sugar": _value(rng, "Fasting Blood Sugar"),
                },
                "A",
            ),
        )
    )
    # C10: ambiguous date 05/03/<year of visit 4 + 1>
    ambiguous_year = visits[4].year + 1
    docs.append(
        _document(
            d(11),
            "A",
            name,
            dates=[("Collected", f"05/03/{ambiguous_year}")],
            rows=_rows(["Haemoglobin"], {"Haemoglobin": _value(rng, "Haemoglobin")}, "A"),
        )
    )
    if index % 2 == 1:
        docs[-1]["decisions"].append(
            {"key": docs[-1]["observations"][0]["key"], "decision": "verify"}
        )
    # C11: month-only date, before the first visit
    month = visits[0] - dt.timedelta(days=60)
    docs.append(
        _document(
            d(12),
            "A",
            name,
            dates=[("Collected", month.strftime("%b %Y"))],
            rows=_rows(["Haemoglobin"], {"Haemoglobin": _value(rng, "Haemoglobin")}, "A"),
        )
    )
    # C13: identity mismatch (someone else's report) on visit 4's date
    docs.append(
        _document(
            d(13),
            "A",
            "Unrelated Fictional Person",
            dates=[("Collected", _dmy(visits[4]))],
            rows=_rows(["Haemoglobin"], {"Haemoglobin": "9.9"}, "A"),
            identity="mismatch",
        )
    )
    # C14: a non-lab note with a report date
    docs.append(
        _document(
            d(14),
            "A",
            name,
            dates=[("Report Date", _dmy(visits[4]))],
            rows=[],
            doc_type="other",
            title="Consultation Note",
        )
    )
    # C7: correct visit 1's HbA1c; C8: reject visit 3's fourth analyte
    hba1c = next(o for o in docs[1]["observations"] if o["analyte"] == "HbA1c")
    corrected = _fmt(Decimal(hba1c["value_raw"]) + Decimal("0.3"), 1)
    docs[1]["decisions"].append({"key": hba1c["key"], "decision": "correct", "value": corrected})
    docs[3]["decisions"].append({"key": docs[3]["observations"][3]["key"], "decision": "reject"})

    order = [doc["doc_key"] for doc in docs]
    rng.shuffle(order)
    patient = {
        "patient_key": key,
        "role": "scenario",
        "display_name": name,
        "date_of_birth": dt.date(1950 + index, 1 + index % 12, 13 + index % 15).isoformat(),
        "upload_order": order,
        "documents": docs,
        "intended": {
            "exact_pairs": [
                [a["key"], b["key"]]
                for a, b in zip(docs[0]["observations"], copy["observations"], strict=True)
            ],
            "possible_pairs": [
                [_find(docs[2], "Haemoglobin"), docs[6]["observations"][0]["key"]],
                [_find(docs[2], "Fasting Blood Sugar"), docs[6]["observations"][1]["key"]],
            ],
            "conflict_pairs": [[_find(docs[3], "HbA1c"), docs[7]["observations"][0]["key"]]],
        },
    }
    patient["truth"] = derive_truth(patient)
    return patient


def _find(doc: dict, analyte: str) -> str:
    return next(o["key"] for o in doc["observations"] if o["analyte"] == analyte)


def performance_patient(rng: random.Random, documents: int = 150) -> dict:
    """One patient with a long history (dev only): timing, not new cases."""
    name = f"{FIRST[-1]} {LAST}"
    analytes = list(PANEL)
    day = dt.date(2012, 1, 15)
    docs = []
    for n in range(documents):
        day += dt.timedelta(days=24 + rng.randint(0, 6))
        day = day.replace(day=max(13, min(day.day, 28)))
        values = {a: _value(rng, a) for a in analytes}
        docs.append(
            _document(
                f"L99-D{n + 1:03d}",
                "A",
                name,
                dates=[("Collected", _dmy(day))],
                rows=_rows(analytes, values, "A"),
            )
        )
    order = [doc["doc_key"] for doc in docs]
    rng.shuffle(order)
    patient = {
        "patient_key": PERFORMANCE_PATIENT,
        "role": "performance",
        "display_name": name,
        "date_of_birth": "1970-07-14",
        "upload_order": order,
        "documents": docs,
        "intended": {"exact_pairs": [], "possible_pairs": [], "conflict_pairs": []},
    }
    patient["truth"] = derive_truth(patient)
    return patient


# --- ground truth (from the specification) -------------------------------------------------------

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1
    )
}


def _date(raw: str) -> tuple[dt.date, str, bool]:
    """(date, precision, ambiguous) under the region rule: DD/MM/YYYY; 'Mon YYYY' is a month."""
    if "/" in raw:
        day, month, year = (int(x) for x in raw.split("/"))
        return dt.date(year, month, day), "day", day <= 12 and month <= 12 and day != month
    mon, year = raw.split()
    return dt.date(int(year), _MONTHS[mon], 1), "month", False


def derive_truth(patient: dict) -> dict:
    facts: dict[str, dict] = {}
    documents: dict[str, dict] = {}
    for doc in patient["documents"]:
        dates = doc["dates"]
        report = dates.get("Report Date")
        doc_date = None
        if doc["doc_type"] == "lab_report" and report:  # Phase 3 fills it for lab reports only
            value, precision, _ = _date(report["raw"])
            doc_date = {"date": value.isoformat(), "precision": precision}
        documents[doc["doc_key"]] = {
            "excluded": doc["identity"] == "mismatch",
            "identity": doc["identity"],
            "date": doc_date,
        }
        source, chosen = None, None
        if "Collected" in dates:
            source, chosen = "collection", dates["Collected"]["raw"]
        elif report:
            source, chosen = "report", report["raw"]
        decisions = {x["key"]: x for x in doc["decisions"]}
        for o in doc["observations"]:
            date = precision = None
            ambiguous = False
            if chosen:
                value_date, precision, ambiguous = _date(chosen)
                date = value_date.isoformat()
            decision = decisions.get(o["key"])
            status = "needs_review" if ambiguous else "validated"
            printed_value = o["value_raw"]
            if decision:
                status = {"verify": "verified", "correct": "corrected", "reject": "rejected"}[
                    decision["decision"]
                ]
                if decision["decision"] == "correct":
                    printed_value = decision["value"]
            unit = CANONICAL_UNIT[o["unit_raw"]]
            value = Decimal(printed_value)
            if (o["analyte"], unit) in CONVERSION:
                unit, factor = CONVERSION[(o["analyte"], unit)]
                value = value * factor
            excluded = (
                "mismatch"
                if doc["identity"] == "mismatch"
                else "needs_review"
                if status == "needs_review"
                else "rejected"
                if status == "rejected"
                else None
            )
            facts[o["key"]] = {
                "doc_key": doc["doc_key"],
                "analyte": o["analyte"],
                "date": date,
                "precision": precision,
                "date_source": source if date else None,
                "value": format(value, "f"),
                "unit": unit,
                "status": status,
                "excluded": excluded,
                "identity_unknown": doc["identity"] == "unknown",
            }
    usable = {k: f for k, f in facts.items() if f["excluded"] is None}
    exact = [p for p in patient["intended"]["exact_pairs"] if all(k in usable for k in p)]
    possible = [p for p in patient["intended"]["possible_pairs"] if all(k in usable for k in p)]
    conflicts = [p for p in patient["intended"]["conflict_pairs"] if all(k in usable for k in p)]
    merged = {b: a for a, b in exact}  # the copy's facts join the original's entry
    entries: dict[str, set[str]] = {}
    for k in usable:
        entries.setdefault(merged.get(k, k), set()).add(k)
    timeline = []
    for keys in entries.values():
        f = usable[sorted(keys)[0]]
        if f["date"]:
            timeline.append(
                {
                    "kind": "lab_result",
                    "keys": sorted(keys),
                    "date": f["date"],
                    "precision": f["precision"],
                    "date_source": f["date_source"],
                }
            )
    for doc_key, doc in documents.items():
        if not doc["excluded"] and doc["date"]:
            timeline.append(
                {
                    "kind": "document",
                    "keys": [doc_key],
                    "date": doc["date"]["date"],
                    "precision": doc["date"]["precision"],
                    "date_source": "document_report_date",
                }
            )
    trends: dict[str, list[dict]] = {}
    for keys in entries.values():
        f = usable[sorted(keys)[0]]
        if f["date"] and f["precision"] == "day":
            trends.setdefault(f["analyte"], []).append(
                {"keys": sorted(keys), "date": f["date"], "unit": f["unit"], "value": f["value"]}
            )
    for points in trends.values():
        points.sort(key=lambda p: (p["unit"], p["date"], p["keys"]))
    return {
        "facts": facts,
        "documents": documents,
        "timeline": sorted(timeline, key=lambda e: (e["date"], e["kind"], e["keys"])),
        "undated_facts": sorted(k for k in entries if not usable[k]["date"]),
        "undated_documents": sorted(
            k for k, d in documents.items() if not d["excluded"] and d["date"] is None
        ),
        "excluded_mismatch_documents": sum(d["excluded"] for d in documents.values()),
        "exact_pairs": sorted(sorted(p) for p in exact),
        "possible_pairs": sorted(sorted(p) for p in possible),
        "conflict_pairs": sorted(sorted(p) for p in conflicts),
        "trends": trends,
    }


# --- build ---------------------------------------------------------------------------------------


def build(directory: Path) -> dict:
    """Write dev.json, heldout.json and manifest.json (with both digests). Refuses to overwrite."""
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"{directory} already exists; the dataset is frozen")
    rng = random.Random(SEED)
    patients = [scenario_patient(i, rng) for i in range(1, len(SCENARIO_PATIENTS) + 1)]
    performance = performance_patient(rng)
    splits = {
        "dev": [p for p in patients if p["patient_key"] not in HELDOUT] + [performance],
        "heldout": [p for p in patients if p["patient_key"] in HELDOUT],
    }
    directory.mkdir(parents=True, exist_ok=True)
    digests = {}
    for split, members in splits.items():
        payload = json.dumps(
            {"dataset": DATASET, "split": split, "patients": members},
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        ).encode("utf-8")
        (directory / f"{split}.json").write_bytes(payload)
        digests[split] = hashlib.sha256(payload).hexdigest()
    manifest = {
        "dataset": DATASET,
        "generator_version": GENERATOR_VERSION,
        "seed": SEED,
        "reference_date": REFERENCE_DATE.isoformat(),
        "split": {k: [p["patient_key"] for p in v] for k, v in splits.items()},
        "sha256": digests,
        "frozen_at_build": True,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def load_split(directory: Path, split: str) -> list[dict[str, Any]]:
    """The split's patients, after checking its digest against the manifest."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload = (directory / f"{split}.json").read_bytes()
    if hashlib.sha256(payload).hexdigest() != manifest["sha256"][split]:
        raise ValueError(f"{split}.json does not match its frozen digest")
    return json.loads(payload)["patients"]
