"""Synthetic extraction dataset ``extract_v0`` (Phase 3; docs/PHASE_3_PLAN.md §12.1).

    uv run python -m evals.synthetic.extraction --out data/synthetic/extract_v0

Every document is fictional and every page carries the synthetic disclaimer (ADR-005). The
ground truth is fixed before any PDF is drawn (ADR-010), and the held-out digest is frozen at
build time, before any extraction rule is tuned. The patients and their clean reports come from
the ``lab_v0`` generator (its public ``generate``) with this dataset's own seed.

Per patient, seven documents (id ``<patient>-<kind>``):
- ``CL`` clean lab report (``lab_v0`` renderer: grid / plain / compact);
- ``HD`` hard lab report: collection date and time, a Previous Result column (trap), Indian
  digit grouping, unit spelling variants, similar names (Total / Direct Bilirubin), a comparator,
  an unmapped test, a pending value, qualitative urine results, numbered footnotes (traps);
- ``LN`` results printed as labelled lines (no table);
- ``FM`` one page naming two patients (a mother and a newborn): no fact may be extracted;
- ``RF`` a table of reference ranges only (no results): no fact may be extracted;
- ``RX`` a prescription (not a lab report): no fact may be extracted;
- ``RS`` the ``CL`` report rasterized with mild noise and no text layer (OCR path); its truth is
  the printed truth of ``CL`` (``source_fixture``), and every fact from it must be flagged for
  review (``ocr_unverified_source``).

Gold normalized units come from this module's own table (``_UNIT_TRUTH``), written separately
from the extractor's reference data, so the evaluation is not circular.
"""

import argparse
import datetime as dt
import io
import random
from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from evals.synthetic.build import _json_bytes, _sha256, heldout_digest
from evals.synthetic.catalog import BY_KEY
from evals.synthetic.generator import PatientTruth, ReportTruth, generate, split_patients
from evals.synthetic.render import DISCLAIMER, render_report

DATASET_NAME = "extract_v0"
GENERATOR_VERSION = "0.1"
MANIFEST_VERSION = 1
DEFAULT_SEED = 20261118
DEFAULT_PATIENTS = 16
REFERENCE_DATE = "2026-11-17"  # fixed future-date bound for the evaluation (date policy)
RASTER_DPI = 150

_W, _H = A4
_LEFT = 40

# Printed unit -> (canonical unit, multiplier to the catalogue default unit). Written by hand.
_UNIT_TRUTH: dict[str, tuple[str, str]] = {
    "gm/dL": ("g/dL", "1"),
    "g/dl": ("g/dL", "1"),
    "gm%": ("g/dL", "1"),
    "/cumm": ("/µL", "1"),
    "cells/cumm": ("/µL", "1"),
    "lakhs/cumm": ("/µL", "100000"),
    "million/cumm": ("10^6/µL", "1"),
    "%": ("%", "1"),
    "fL": ("fL", "1"),
    "mg/dL": ("mg/dL", "1"),
    "mg/dl": ("mg/dL", "1"),
    "mL/min/1.73m²": ("mL/min/1.73m²", "1"),
    "U/L": ("U/L", "1"),
    "IU/L": ("U/L", "1"),
    "µIU/mL": ("µIU/mL", "1"),
    "ng/dL": ("ng/dL", "1"),
    "µg/dL": ("µg/dL", "1"),
    "ng/mL": ("ng/mL", "1"),
    "pg/mL": ("pg/mL", "1"),
}
# lab_v0 analyte keys -> catalogue canonical names of the extractor's reference data
_CANONICAL = {
    "hb": "Haemoglobin",
    "tlc": "Total Leucocyte Count",
    "plt": "Platelet Count",
    "rbc": "RBC Count",
    "pcv": "Packed Cell Volume",
    "mcv": "MCV",
    "fbs": "Fasting Blood Sugar",
    "ppbs": "Post Prandial Blood Sugar",
    "hba1c": "HbA1c",
    "chol": "Total Cholesterol",
    "tg": "Triglycerides",
    "hdl": "HDL Cholesterol",
    "ldl": "LDL Cholesterol",
    "creat": "Serum Creatinine",
    "urea": "Blood Urea",
    "uric": "Uric Acid",
    "egfr": "eGFR",
    "sgot": "SGOT",
    "sgpt": "SGPT",
    "tbil": "Total Bilirubin",
    "alp": "Alkaline Phosphatase",
    "tsh": "TSH",
    "t3": "Total T3",
    "t4": "Total T4",
    "vitd": "Vitamin D (25-OH)",
    "b12": "Vitamin B12",
}


def _num(text: str) -> str:
    return text.replace(",", "")


def _range(text: str) -> tuple[str | None, str | None]:
    if " - " in text:
        low, high = text.split(" - ")
        return low, high
    if text.startswith("< "):
        return None, text[2:]
    if text.startswith("> "):
        return text[2:], None
    return None, None


def _fact(
    page: int,
    canonical: str | None,
    analyte_raw: str,
    value_raw: str,
    *,
    unit_raw: str | None,
    range_raw: str | None,
    flag: str | None,
    date: dict,
    comparator: str | None = None,
    qualitative: str | None = None,
    reasons: tuple[str, ...] = (),
) -> dict:
    number = None
    if qualitative is None:
        number = _num(value_raw.lstrip("<>= ").split()[0])
    unit_norm = norm = None
    if unit_raw is not None and number is not None and unit_raw in _UNIT_TRUTH:
        unit_norm, factor = _UNIT_TRUTH[unit_raw]
        norm = str(Decimal(number) * Decimal(factor))
    low, high = _range(range_raw) if range_raw else (None, None)
    return {
        "page": page,
        "analyte": canonical,
        "analyte_raw": analyte_raw,
        "value_raw": value_raw,
        "value_num": number,
        "comparator": comparator,
        "qualitative": qualitative,
        "unit_raw": unit_raw,
        "unit_norm": unit_norm,
        "value_num_norm": norm,
        "ref_range_raw": range_raw,
        "ref_low": low,
        "ref_high": high,
        "flag_as_printed": flag or None,
        **date,
        "expected_reasons": sorted(reasons),
    }


def _date_fields(date: dt.date, source: str, time: str | None = None) -> dict:
    return {
        "effective_date": date.isoformat(),
        "date_precision": "day",
        "date_source": source,
        "effective_time": time,
    }


def _ambiguous(date: dt.date) -> tuple[str, ...]:
    return (
        ("ambiguous_date",)
        if date.day <= 12 and date.month <= 12 and date.day != date.month
        else ()
    )


# --- drawing helpers --------------------------------------------------------------------------


def _start(title: str) -> tuple[canvas.Canvas, io.BytesIO]:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.setTitle(title)
    return pdf, buffer


def _footer(pdf: canvas.Canvas, number: int, total: int) -> None:
    pdf.setFont("Helvetica", 8)
    pdf.drawString(_LEFT, 30, DISCLAIMER)
    pdf.drawRightString(_W - _LEFT, 30, f"Page {number} of {total}")


def _lines(pdf: canvas.Canvas, lines: list[tuple[str, int, bool]], y: float) -> float:
    for text, size, bold in lines:
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(_LEFT, y, text)
        y -= size + 6
    return y


def _grid(
    pdf: canvas.Canvas, header: tuple[str, ...], xs: tuple[int, ...], rows: list, y: float
) -> float:
    pdf.setFont("Helvetica-Bold", 9)
    for label, x in zip(header, xs, strict=True):
        pdf.drawString(x, y, label)
    pdf.line(_LEFT - 4, y + 12, _W - _LEFT, y + 12)
    pdf.line(_LEFT - 4, y - 5, _W - _LEFT, y - 5)
    y -= 18
    pdf.setFont("Helvetica", 9)
    for row in rows:
        for text, x in zip(row, xs, strict=True):
            pdf.drawString(x, y, text)
        pdf.line(_LEFT - 4, y - 5, _W - _LEFT, y - 5)
        y -= 18
    return y - 16


# --- document kinds ---------------------------------------------------------------------------


def _clean(patient: PatientTruth, report: ReportTruth) -> tuple[bytes, list[dict]]:
    date = dt.date.fromisoformat(report.date)
    facts = []
    for page in report.pages:
        for row in page.rows:
            facts.append(
                _fact(
                    page.page_number,
                    _CANONICAL[row.analyte_key],
                    row.printed_name,
                    f"{row.value} {row.flag}".strip() if report.layout != "grid" else row.value,
                    unit_raw=row.unit,
                    range_raw=row.reference,
                    flag=row.flag,
                    date=_date_fields(date, "report"),
                    reasons=_ambiguous(date),
                )
            )
    return render_report(patient, report), facts


def _hard(rng: random.Random, patient: PatientTruth) -> tuple[bytes, list[dict], list[dict]]:
    collected = dt.date(2025, 1, 1) + dt.timedelta(days=rng.randrange(0, 600))
    reported = collected + dt.timedelta(days=1)
    hour, minute = rng.randrange(7, 11), rng.choice((0, 15, 30, 45))
    time_text = f"{hour:02d}:{minute:02d} AM"
    date_fields = _date_fields(collected, "collection", f"{hour:02d}:{minute:02d}:00")
    reasons = _ambiguous(collected)
    hb = f"{rng.uniform(10.5, 16.5):.1f}"
    tlc = rng.randrange(4200, 10800)
    tlc_text = f"{tlc // 1000},{tlc % 1000:03d}"
    tbil, dbil = f"{rng.uniform(0.4, 1.1):.2f}", f"{rng.uniform(0.05, 0.3):.2f}"
    hdl = str(rng.randrange(42, 70))
    prev = {k: f"{rng.uniform(1, 9):.1f}" for k in ("hb", "tbil", "hdl")}
    hb_unit = rng.choice(("g/dl", "gm%"))
    rows = [
        ("Haemoglobin", hb, hb_unit, "13.0 - 17.0", prev["hb"]),
        ("Total Leucocyte Count", tlc_text, "cells/cumm", "4000 - 11000", "6,100"),
        ("Total Bilirubin", tbil, "mg/dl", "0.20 - 1.20", prev["tbil"]),
        ("Direct Bilirubin", dbil, "mg/dl", "0.00 - 0.30", ""),
        ("HDL Cholesterol", hdl, "mg/dL", "> 40", prev["hdl"]),
        ("TSH", "< 0.01", "µIU/mL", "0.40 - 4.50", ""),
        ("Serum Zinc", f"{rng.randrange(70, 110)}", "µg/dL", "60 - 120", ""),
        ("Vitamin B12", "Pending", "pg/mL", "200 - 900", ""),
    ]
    urine = [("Urine Glucose", rng.choice(("Negative", "Nil"))), ("Urine Protein", "Trace")]

    pdf, buffer = _start(f"Synthetic hard lab report {patient.patient_key}")
    y = _lines(
        pdf,
        [
            ("Neelkanth Health Labs (Fictional)", 14, True),
            ("88 Test Marg, Lucknow", 9, False),
            (f"Patient Name: {patient.display_name}", 10, False),
            (f"Sample Collected On: {collected:%d/%m/%Y} {time_text}", 10, False),
            (f"Report Date: {reported:%d/%m/%Y}", 10, False),
            ("BIOCHEMISTRY AND HAEMATOLOGY", 11, True),
        ],
        _H - 50,
    )
    header = ("Test", "Result", "Unit", "Biological Reference Interval", "Previous Result")
    y = _grid(pdf, header, (40, 200, 270, 350, 480), rows, y - 10)
    y = _lines(pdf, [("URINE ROUTINE", 11, True)], y)
    y = _grid(pdf, ("Test", "Result"), (40, 200), urine, y - 10)
    _lines(
        pdf,
        [
            ("1. Results relate only to the sample tested.", 8, False),
            ("2. Repeat after 3 months if clinically indicated.", 8, False),
        ],
        y,
    )
    _footer(pdf, 1, 1)
    pdf.showPage()
    pdf.save()

    facts = [
        _fact(
            1,
            "Haemoglobin",
            "Haemoglobin",
            hb,
            unit_raw=hb_unit,
            range_raw="13.0 - 17.0",
            flag=None,
            date=date_fields,
            reasons=reasons,
        ),
        _fact(
            1,
            "Total Leucocyte Count",
            "Total Leucocyte Count",
            tlc_text,
            unit_raw="cells/cumm",
            range_raw="4000 - 11000",
            flag=None,
            date=date_fields,
            reasons=reasons,
        ),
        _fact(
            1,
            "Total Bilirubin",
            "Total Bilirubin",
            tbil,
            unit_raw="mg/dl",
            range_raw="0.20 - 1.20",
            flag=None,
            date=date_fields,
            reasons=reasons,
        ),
        _fact(
            1,
            "Direct Bilirubin",
            "Direct Bilirubin",
            dbil,
            unit_raw="mg/dl",
            range_raw="0.00 - 0.30",
            flag=None,
            date=date_fields,
            reasons=reasons,
        ),
        _fact(
            1,
            "HDL Cholesterol",
            "HDL Cholesterol",
            hdl,
            unit_raw="mg/dL",
            range_raw="> 40",
            flag=None,
            date=date_fields,
            reasons=reasons,
        ),
        _fact(
            1,
            "TSH",
            "TSH",
            "< 0.01",
            unit_raw="µIU/mL",
            range_raw="0.40 - 4.50",
            flag=None,
            date=date_fields,
            comparator="<",
            reasons=reasons,
        ),
        _fact(
            1,
            None,
            "Serum Zinc",
            rows[6][1],
            unit_raw="µg/dL",
            range_raw="60 - 120",
            flag=None,
            date=date_fields,
            reasons=(*reasons, "unmapped_analyte"),
        ),
        _fact(
            1,
            "Urine Glucose",
            "Urine Glucose",
            urine[0][1],
            unit_raw=None,
            range_raw=None,
            flag=None,
            date=date_fields,
            qualitative=urine[0][1].capitalize(),
            reasons=reasons,
        ),
        _fact(
            1,
            "Urine Protein",
            "Urine Protein",
            "Trace",
            unit_raw=None,
            range_raw=None,
            flag=None,
            date=date_fields,
            qualitative="Trace",
            reasons=reasons,
        ),
    ]
    traps = [{"page": 1, "value_raw": v} for v in (*prev.values(), "6,100", "Pending", "3")]
    return buffer.getvalue(), facts, traps


def _lines_doc(rng: random.Random, patient: PatientTruth) -> tuple[bytes, list[dict]]:
    collected = dt.date(2024, 6, 1) + dt.timedelta(days=rng.randrange(0, 300))
    date_fields = _date_fields(collected, "collection")
    keys = rng.sample(("hb", "fbs", "creat", "urea", "tsh", "hba1c"), 4)
    lines: list[tuple[str, int, bool]] = [
        ("Sunrise Diagnostics (Fictional)", 14, True),
        (f"Patient Name: {patient.display_name}", 10, False),
        (f"Collected On: {collected:%d-%b-%Y}", 10, False),
        ("RESULTS", 11, True),
    ]
    facts = []
    for key in keys:
        analyte = BY_KEY[key]
        value = (
            f"{max(rng.gauss(analyte.mean, analyte.sd), analyte.mean * 0.3):.{analyte.decimals}f}"
        )
        low, high = analyte.low, analyte.high
        fmt = f"{{:.{analyte.decimals}f}}"
        range_raw = f"{fmt.format(low)} - {fmt.format(high)}" if low is not None and high else None
        text = f"{analyte.name}: {value} {analyte.unit}"
        if range_raw:
            text += f" ({range_raw})"
        lines.append((text, 10, False))
        facts.append(
            _fact(
                1,
                _CANONICAL[key],
                analyte.name,
                value,
                unit_raw=analyte.unit,
                range_raw=range_raw,
                flag=None,
                date=date_fields,
            )
        )
    pdf, buffer = _start(f"Synthetic line report {patient.patient_key}")
    _lines(pdf, lines, _H - 50)
    _footer(pdf, 1, 1)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue(), facts


def _family(rng: random.Random, patient: PatientTruth) -> bytes:
    child = f"Baby {patient.display_name.split()[0]} {patient.display_name.split()[-1]}"
    pdf, buffer = _start(f"Synthetic family report {patient.patient_key}")
    y = _lines(
        pdf,
        [
            ("Lotus Pathology Lab (Fictional)", 14, True),
            (f"Patient Name: {patient.display_name}", 10, False),
            ("Report Date: 21/09/2024", 10, False),
        ],
        _H - 50,
    )
    rows = [("Haemoglobin", f"{rng.uniform(11, 14):.1f}", "gm/dL", "12.0 - 15.0")]
    y = _grid(pdf, ("Test", "Result", "Unit", "Reference Range"), (40, 230, 320, 410), rows, y)
    y = _lines(pdf, [(f"Patient Name: {child}", 10, False)], y)
    rows = [("Haemoglobin", f"{rng.uniform(14, 19):.1f}", "gm/dL", "14.0 - 24.0")]
    _grid(pdf, ("Test", "Result", "Unit", "Reference Range"), (40, 230, 320, 410), rows, y)
    _footer(pdf, 1, 1)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _reference_only(patient: PatientTruth) -> bytes:
    pdf, buffer = _start(f"Synthetic reference card {patient.patient_key}")
    y = _lines(
        pdf,
        [
            ("Sunrise Diagnostics (Fictional)", 14, True),
            ("REFERENCE INTERVALS USED BY THIS LABORATORY", 11, True),
        ],
        _H - 50,
    )
    rows = [
        (BY_KEY[k].name, f"{BY_KEY[k].low} - {BY_KEY[k].high}", BY_KEY[k].unit)
        for k in ("hb", "fbs", "creat", "urea")
    ]
    _grid(pdf, ("Test", "Biological Reference Interval", "Unit"), (40, 230, 420), rows, y)
    _footer(pdf, 1, 1)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _prescription(rng: random.Random, patient: PatientTruth) -> bytes:
    pdf, buffer = _start(f"Synthetic prescription {patient.patient_key}")
    _lines(
        pdf,
        [
            ("Dr. Fictional Clinic", 14, True),
            (f"Patient Name: {patient.display_name}", 10, False),
            ("Date: 14/08/2024", 10, False),
            ("Rx", 14, True),
            (f"1. Tab. Placebomin {rng.choice((250, 500))} mg  1-0-1 x 5 days", 10, False),
            ("2. Syp. Imaginol 5 ml  0-0-1", 10, False),
            ("Advice: Haemoglobin test after 1 month.", 10, False),
        ],
        _H - 50,
    )
    _footer(pdf, 1, 1)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def rasterize(pdf_bytes: bytes, seed: int, *, dpi: int = RASTER_DPI) -> bytes:
    """Each page drawn as a noisy greyscale image; the result has no text layer."""
    import pypdfium2 as pdfium

    source = pdfium.PdfDocument(pdf_bytes)
    rng = random.Random(seed)
    pdf, buffer = _start("Synthetic scanned lab report")
    for index in range(len(source)):
        image = source[index].render(scale=dpi / 72).to_pil().convert("L")
        pixels = image.load()
        width, height = image.size
        for _ in range(width * height // 400):  # mild speckle noise, deterministic
            pixels[rng.randrange(width), rng.randrange(height)] = rng.randrange(120, 256)
        pdf.drawImage(ImageReader(image), 0, 0, width=_W, height=_H)
        pdf.showPage()
    pdf.save()
    source.close()
    return buffer.getvalue()


def text_layer_chars(pdf_bytes: bytes) -> int:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        return sum(
            len(document[i].get_textpage().get_text_range().strip()) for i in range(len(document))
        )
    finally:
        document.close()


def native_text(pdf_bytes: bytes) -> str:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        return "\n".join(document[i].get_textpage().get_text_range() for i in range(len(document)))
    finally:
        document.close()


# --- build ------------------------------------------------------------------------------------


def generate_dataset(seed: int, patients: int) -> tuple[list[dict], dict[str, bytes]]:
    rng = random.Random(seed)
    truth = generate(seed, patients)
    ground_truth: list[dict] = []
    pdfs: dict[str, bytes] = {}
    for patient in truth:
        key = patient.patient_key
        docs: list[dict] = []

        def add(
            kind,
            data,
            *,
            doc_type,
            facts=(),
            traps=(),
            abstain=(),
            mode="native",
            fixture=None,
            key=key,
            docs=docs,
        ):
            doc_id = f"{key}-{kind}"
            pdfs[doc_id] = data
            docs.append(
                {
                    "doc_id": doc_id,
                    "kind": kind,
                    "doc_type": doc_type,
                    "render_mode": mode,
                    "source_fixture": fixture,
                    "expected_facts": list(facts),
                    "traps": list(traps),
                    "expected_abstentions": list(abstain),
                }
            )

        clean_pdf, clean_facts = _clean(patient, patient.reports[0])
        add("CL", clean_pdf, doc_type="lab_report", facts=clean_facts)
        hard_pdf, hard_facts, traps = _hard(rng, patient)
        add(
            "HD",
            hard_pdf,
            doc_type="lab_report",
            facts=hard_facts,
            traps=traps,
            abstain=[{"page": 1, "reason": "placeholder_value"}],
        )
        lines_pdf, line_facts = _lines_doc(rng, patient)
        add("LN", lines_pdf, doc_type="lab_report", facts=line_facts)
        add(
            "FM",
            _family(rng, patient),
            doc_type="lab_report",
            abstain=[{"page": 1, "reason": "multiple_subjects"}],
        )
        add(
            "RF",
            _reference_only(patient),
            doc_type="other",
            abstain=[{"page": 1, "reason": "table_unrecognized"}],
        )
        add("RX", _prescription(rng, patient), doc_type="other")
        raster_facts = [
            {**f, "expected_reasons": sorted({*f["expected_reasons"], "ocr_unverified_source"})}
            for f in clean_facts
        ]
        add(
            "RS",
            rasterize(clean_pdf, seed + int(key[1:])),
            doc_type="lab_report",
            facts=raster_facts,
            mode="raster",
            fixture=f"{key}-CL",
        )
        ground_truth.append(
            {
                "patient_key": key,
                "display_name": patient.display_name,
                "date_of_birth": patient.date_of_birth,
                "sex": patient.sex,
                "documents": docs,
            }
        )
    return ground_truth, pdfs


def verify_sources(ground_truth: list[dict], pdfs: dict[str, bytes]) -> None:
    """Native PDFs: every raw value is in the visible text layer. Raster PDFs: no text layer,
    and their fixture passes the native check (§12.1)."""
    by_id = {d["doc_id"]: d for p in ground_truth for d in p["documents"]}
    for doc_id, doc in by_id.items():
        data = pdfs[doc_id]
        if doc["render_mode"] == "raster":
            if text_layer_chars(data) != 0:
                raise ValueError(f"{doc_id}: a rasterized PDF must have no text layer")
            fixture = doc["source_fixture"]
            if fixture not in by_id or by_id[fixture]["render_mode"] != "native":
                raise ValueError(f"{doc_id}: invalid source fixture")
            continue
        text = native_text(data)
        for fact in doc["expected_facts"]:
            for field in ("analyte_raw", "value_raw", "unit_raw", "ref_range_raw"):
                if fact[field] and fact[field] not in text:
                    raise ValueError(f"{doc_id}: {field} not in the PDF text layer")


def build_dataset(out: Path, *, seed: int = DEFAULT_SEED, patients: int = DEFAULT_PATIENTS) -> dict:
    if out.exists():
        raise FileExistsError(f"{out} already exists; a dataset is never overwritten")
    ground_truth, pdfs = generate_dataset(seed, patients)
    verify_sources(ground_truth, pdfs)
    (out / "pdfs").mkdir(parents=True)
    files: dict[str, dict] = {}
    data = _json_bytes(ground_truth)
    (out / "ground_truth.json").write_bytes(data)
    files["ground_truth.json"] = {"sha256": _sha256(data), "size": len(data)}
    for doc_id, pdf in sorted(pdfs.items()):
        path = f"pdfs/{doc_id}.pdf"
        (out / path).write_bytes(pdf)
        files[path] = {"sha256": _sha256(pdf), "size": len(pdf)}
    split = split_patients([p["patient_key"] for p in ground_truth], seed)
    manifest = {
        "dataset": DATASET_NAME,
        "manifest_version": MANIFEST_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "patients": patients,
        "documents": len(pdfs),
        "reference_date": REFERENCE_DATE,
        "synthetic": True,
        "split": split,
        "heldout_sha256": heldout_digest(ground_truth, files, split["heldout"]),
        "files": files,
    }
    (out / "manifest.json").write_bytes(_json_bytes(manifest))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic extraction dataset.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--patients", type=int, default=DEFAULT_PATIENTS)
    args = parser.parse_args(argv)
    manifest = build_dataset(args.out, seed=args.seed, patients=args.patients)
    print(
        f"Dataset {manifest['dataset']}: {manifest['patients']} patients, "
        f"{manifest['documents']} documents; dev {len(manifest['split']['dev'])} / held-out "
        f"{len(manifest['split']['heldout'])} patients; heldout_sha256 {manifest['heldout_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
