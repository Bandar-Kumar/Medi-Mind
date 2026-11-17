"""Synthetic document-type dataset ``doctype_v0`` for the rule-based classifier (Task 2.7, ADR-036).

    uv run python -m evals.synthetic.doctype --out data/synthetic/doctype_v0

Every document is fictional and every page carries the synthetic disclaimer (ADR-005). The label
of each document is fixed by construction (ADR-010): the generator decides what kind of document
it draws, so ``true_type`` is exact. Labels use the Phase 2 classifier's vocabulary (ADR-019,
ADR-036 D1): ``lab_report`` or ``other``. No document is labelled ``unknown``: ``unknown`` is the
classifier's abstention, counted separately in the evaluation.

Per patient, seven documents (one of each kind, variant chosen at random):
- ``lab_standard``  a normal lab report, drawn by the ``lab_v0`` renderer (grid / plain / compact);
- ``lab_hard``      a lab report without the usual table: narrative, key-value lines, or culture;
- ``prescription``  a clinic prescription, sometimes with tests advised;
- ``discharge``     a discharge summary, sometimes with a printed table of lab results;
- ``letter``        a referral letter, a fitness certificate, or a cover letter quoting lab values;
- ``invoice``       a bill listing lab tests and prices, sometimes from a diagnostics centre;
- ``ambiguous``     a sample-collection receipt or an appointment slip (true type ``other``).

Several kinds deliberately carry lab vocabulary (conflicting signals), so the dataset tests more
than keyword spotting. The patients and the standard lab reports come from the ``lab_v0``
generator (its public ``generate``) with this dataset's own seed.

The manifest, file hashes and held-out digest use the ``lab_v0`` conventions and are verified by
``evals.dataset.load_dataset``: the held-out split is frozen when the dataset is built, before any
classification rule is tuned on the dev split.
"""

import argparse
import datetime as dt
import io
import random
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from evals.synthetic.build import _json_bytes, _sha256, heldout_digest
from evals.synthetic.generator import PatientTruth, generate, split_patients
from evals.synthetic.render import DISCLAIMER, render_report

DATASET_NAME = "doctype_v0"
GENERATOR_VERSION = "0.1"
MANIFEST_VERSION = 1
DEFAULT_SEED = 20261117
DEFAULT_PATIENTS = 12

LAB_REPORT = "lab_report"
OTHER = "other"
KINDS: tuple[tuple[str, str, str], ...] = (  # (kind, document id suffix, true type)
    ("lab_standard", "LS", LAB_REPORT),
    ("lab_hard", "LH", LAB_REPORT),
    ("prescription", "RX", OTHER),
    ("discharge", "DS", OTHER),
    ("letter", "LT", OTHER),
    ("invoice", "IN", OTHER),
    ("ambiguous", "AM", OTHER),
)

_WIDTH, _HEIGHT = A4
_LEFT = 40
_CLINICS = (
    ("Dr. Kiran Imaginary (Fictional), MBBS, MD", "Sunbeam Family Clinic, 7 Pretend Lane, Nagpur"),
    ("Dr. Farah Notreal (Fictional), MBBS, DNB", "Harbour Health Clinic, 21 Mock Street, Kochi"),
)
_HOSPITALS = (
    "Riverside Multispeciality Hospital (Fictional)",
    "Greenfield General Hospital (Fictional)",
)
_LABS = (
    "Sunrise Diagnostics (Fictional)",
    "Lotus Pathology Lab (Fictional)",
    "Neelkanth Health Labs (Fictional)",
)
_DRUGS = ("Fictomol", "Placebozide", "Mockcillin", "Sampletrex", "Imaginol", "Demoprazole")
_TESTS = (
    ("Complete Blood Count (CBC)", 350),
    ("Lipid Profile", 600),
    ("HbA1c", 450),
    ("Liver Function Test", 700),
    ("Kidney Function Test", 650),
    ("Thyroid Profile", 550),
    ("Fasting Blood Sugar", 120),
)


@dataclass(frozen=True)
class Line:
    text: str
    size: int = 10
    bold: bool = False


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    columns: tuple[int, ...]  # x positions


Block = Line | Table
Page = tuple[Block, ...]


@dataclass(frozen=True)
class DocumentTruth:
    document_id: str
    kind: str
    variant: str
    true_type: str
    pages: tuple[Page, ...] | None  # None: drawn by the lab_v0 renderer (lab_standard)


def _date(rng: random.Random) -> dt.date:
    return dt.date(2023, 1, 1) + dt.timedelta(days=rng.randrange(0, 700))


def _dmy(date: dt.date) -> str:
    return date.strftime("%d/%m/%Y")


def _patient_line(patient: PatientTruth, date: dt.date) -> Line:
    age = date.year - int(patient.date_of_birth[:4])
    return Line(f"Patient Name: {patient.display_name}    Age/Sex: {age} Y / {patient.sex}")


def _lab_hard(rng: random.Random, patient: PatientTruth) -> tuple[str, tuple[Page, ...]]:
    date, lab = _date(rng), rng.choice(_LABS)
    head = (Line(lab, 14, True), _patient_line(patient, date), Line(f"Date: {_dmy(date)}"))
    variant = rng.choice(("narrative", "key_value", "culture"))
    if variant == "narrative":
        value = f"{rng.uniform(5.2, 7.8):.1f}"
        body = (
            Line(f"Glycated haemoglobin was measured at {value} % on the submitted specimen."),
            Line("Expected values for adults without diabetes: 4.0 - 5.6 %."),
            Line("Method: HPLC. Verified by Dr. Meena Unreal (Fictional), Pathologist."),
        )
    elif variant == "key_value":
        body = (
            Line("HAEMATOLOGY", 11, True),
            Line(f"Haemoglobin : {rng.uniform(10.5, 16.5):.1f} gm/dL   (13.0 - 17.0)"),
            Line(f"Total Leucocyte Count : {rng.randrange(4000, 11000)} /cumm   (4000 - 11000)"),
            Line(f"Platelet Count : {rng.uniform(1.5, 4.0):.2f} lakhs/cumm   (1.50 - 4.50)"),
            Line(f"ESR : {rng.randrange(2, 30)} mm/hr   (0 - 20)"),
        )
    else:
        body = (
            Line("MICROBIOLOGY", 11, True),
            Line("Specimen: Urine (midstream)"),
            Line(f"Sample Collected: {_dmy(date)}"),
            Line("Culture: No growth of pathogenic organisms after 48 hours of incubation."),
            Line(f"Reported on: {_dmy(date + dt.timedelta(days=2))}"),
        )
    return variant, (head + body,)


def _prescription(rng: random.Random, patient: PatientTruth) -> tuple[str, tuple[Page, ...]]:
    date = _date(rng)
    doctor, clinic = rng.choice(_CLINICS)
    variant = rng.choice(("rx", "plain", "with_tests"))
    lines = [Line(doctor, 12, True), Line(clinic), Line(f"Date: {_dmy(date)}")]
    lines.append(_patient_line(patient, date))
    lines.append(Line("Rx", 14, True) if variant == "rx" else Line("Medicines:", 11, True))
    for number, drug in enumerate(rng.sample(_DRUGS, 3), start=1):
        dose = rng.choice(("1-0-1", "1-0-0", "0-0-1", "1-1-1"))
        lines.append(Line(f"{number}. Tab. {drug} {rng.choice((5, 10, 20, 500))} mg  {dose}"))
    if variant == "with_tests":
        tests = ", ".join(name for name, _ in rng.sample(_TESTS, 3))
        lines.append(Line(f"Investigations advised: {tests}"))
    lines += [Line("Advice: plenty of fluids; review after 2 weeks."), Line("Signature")]
    return variant, (tuple(lines),)


def _discharge(rng: random.Random, patient: PatientTruth) -> tuple[str, tuple[Page, ...]]:
    admitted = _date(rng)
    discharged = admitted + dt.timedelta(days=rng.randrange(2, 7))
    variant = rng.choice(("inline_results", "with_lab_table"))
    first: list[Block] = [
        Line(rng.choice(_HOSPITALS), 14, True),
        Line("DISCHARGE SUMMARY", 12, True),
        _patient_line(patient, admitted),
        Line(f"Date of Admission: {_dmy(admitted)}"),
        Line(f"Date of Discharge: {_dmy(discharged)}"),
        Line("Final Diagnosis: Acute gastroenteritis with mild dehydration (fictional case)"),
        Line("Course in Hospital: treated with intravenous fluids; improved steadily."),
    ]
    hb, tlc = f"{rng.uniform(10.0, 14.0):.1f}", str(rng.randrange(6000, 14000))
    if variant == "inline_results":
        first.append(Line(f"Investigations: Haemoglobin {hb} gm/dL, TLC {tlc} /cumm."))
    else:
        first.append(Line("Investigations:", 11, True))
        first.append(
            Table(
                ("Test", "Result", "Unit", "Reference Range"),
                (
                    ("Haemoglobin", hb, "gm/dL", "13.0 - 17.0"),
                    ("Total Leucocyte Count", tlc, "/cumm", "4000 - 11000"),
                    ("Serum Creatinine", f"{rng.uniform(0.6, 1.4):.2f}", "mg/dL", "0.70 - 1.30"),
                ),
                (40, 230, 320, 410),
            )
        )
    tail: list[Block] = [
        Line("Condition at Discharge: stable."),
        Line(f"Advice on Discharge: Tab. {rng.choice(_DRUGS)} 500 mg 1-0-1 for 5 days."),
        Line("Follow-up: outpatient clinic after one week."),
    ]
    if variant == "with_lab_table":  # two pages: the lab table on page 1, the advice on page 2
        return variant, (tuple(first), tuple(tail))
    return variant, (tuple(first + tail),)


def _letter(rng: random.Random, patient: PatientTruth) -> tuple[str, tuple[Page, ...]]:
    date = _date(rng)
    doctor, clinic = rng.choice(_CLINICS)
    variant = rng.choice(("referral", "fitness", "lab_cover"))
    lines: list[Block] = [Line(doctor, 12, True), Line(clinic), Line(f"Date: {_dmy(date)}")]
    if variant == "referral":
        lines += [
            Line("Dear Dr. Arun Madeup (Fictional),"),
            Line(f"I am referring {patient.display_name} for evaluation of recurrent headaches."),
            Line("Kindly assess and advise further management."),
        ]
    elif variant == "fitness":
        lines += [
            Line("TO WHOM IT MAY CONCERN", 12, True),
            Line(f"This is to certify that {patient.display_name} was examined on this date"),
            Line("and is fit to resume regular duties."),
        ]
    else:
        lines += [
            Line("Dear Dr. Arun Madeup (Fictional),"),
            Line(f"Please find enclosed the recent laboratory reports of {patient.display_name}."),
            Line(f"The HbA1c was {rng.uniform(5.5, 8.0):.1f} % and haemoglobin was"),
            Line(f"{rng.uniform(10.5, 15.0):.1f} gm/dL. I would value your opinion."),
        ]
    lines += [Line("Yours sincerely,"), Line(doctor)]
    return variant, (tuple(lines),)


def _invoice(rng: random.Random, patient: PatientTruth) -> tuple[str, tuple[Page, ...]]:
    date = _date(rng)
    variant = rng.choice(("hospital_bill", "lab_bill"))
    issuer = rng.choice(_HOSPITALS) if variant == "hospital_bill" else rng.choice(_LABS)
    tests = rng.sample(_TESTS, 3)
    rows = tuple(
        (str(number), name, "1", f"{price:.2f}")
        for number, (name, price) in enumerate(tests, start=1)
    )
    total = sum(price for _, price in tests)
    lines: tuple[Block, ...] = (
        Line(issuer, 14, True),
        Line("TAX INVOICE", 12, True),
        Line(f"Bill No: INV-{rng.randrange(10**5):05d}    Date: {_dmy(date)}"),
        Line(f"Patient Name: {patient.display_name}"),
        Table(("Sl.", "Test / Service", "Qty", "Amount (Rs.)"), rows, (40, 80, 330, 400)),
        Line(f"Total Amount: Rs. {total:,.2f}"),
        Line("GSTIN: 00FICTN0000F0Z0 (fictional)    Payment Mode: UPI"),
    )
    return variant, (lines,)


def _ambiguous(rng: random.Random, patient: PatientTruth) -> tuple[str, tuple[Page, ...]]:
    date = _date(rng)
    variant = rng.choice(("sample_receipt", "appointment"))
    lab = rng.choice(_LABS)
    tests = ", ".join(name for name, _ in rng.sample(_TESTS, 2))
    if variant == "sample_receipt":
        lines: tuple[Block, ...] = (
            Line(lab, 14, True),
            Line("Sample Collection Acknowledgement", 12, True),
            Line(f"Patient Name: {patient.display_name}"),
            Line(f"Sample ID: SMP{rng.randrange(10**6):06d}    Collected on: {_dmy(date)}"),
            Line(f"Tests booked: {tests}"),
            Line("Reports will be available after 6 PM."),
        )
    else:
        lines = (
            Line(lab, 14, True),
            Line("Appointment Confirmation", 12, True),
            Line(f"Patient Name: {patient.display_name}"),
            Line(f"Department: Pathology    Date: {_dmy(date)}    Time: 08:30"),
            Line(f"Tests scheduled: {tests}"),
            Line("Please come fasting for at least 10 hours."),
        )
    return variant, (lines,)


_BUILDERS = {
    "lab_hard": _lab_hard,
    "prescription": _prescription,
    "discharge": _discharge,
    "letter": _letter,
    "invoice": _invoice,
    "ambiguous": _ambiguous,
}


def generate_documents(
    seed: int, patients: int
) -> tuple[tuple[PatientTruth, tuple[DocumentTruth, ...]], ...]:
    """Deterministic patients (from the lab_v0 generator) and their seven labelled documents."""
    rng = random.Random(seed)
    result = []
    for patient in generate(seed, patients):
        documents = []
        for kind, suffix, true_type in KINDS:
            document_id = f"{patient.patient_key}-{suffix}"
            if kind == "lab_standard":
                variant, pages = patient.reports[0].layout, None
            else:
                variant, pages = _BUILDERS[kind](rng, patient)
            documents.append(DocumentTruth(document_id, kind, variant, true_type, pages))
        result.append((patient, tuple(documents)))
    return tuple(result)


def _draw_page(pdf: canvas.Canvas, page: Page, number: int, total: int) -> None:
    y = _HEIGHT - 50
    for block in page:
        if isinstance(block, Line):
            pdf.setFont("Helvetica-Bold" if block.bold else "Helvetica", block.size)
            pdf.drawString(_LEFT, y, block.text)
            y -= block.size + 8
            continue
        pdf.setFont("Helvetica-Bold", 9)
        for label, x in zip(block.header, block.columns, strict=True):
            pdf.drawString(x, y, label)
        pdf.line(_LEFT - 4, y - 5, _WIDTH - _LEFT, y - 5)
        y -= 18
        pdf.setFont("Helvetica", 9)
        for row in block.rows:
            for cell, x in zip(row, block.columns, strict=True):
                pdf.drawString(x, y, cell)
            pdf.line(_LEFT - 4, y - 5, _WIDTH - _LEFT, y - 5)
            y -= 18
        y -= 10
    pdf.setFont("Helvetica", 8)
    pdf.drawString(_LEFT, 30, DISCLAIMER)
    pdf.drawRightString(_WIDTH - _LEFT, 30, f"Page {number} of {total}")


def render_document(patient: PatientTruth, document: DocumentTruth) -> bytes:
    """The PDF bytes of one document (deterministic: reportlab ``invariant=1``)."""
    if document.pages is None:
        return render_report(patient, patient.reports[0])
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.setTitle(f"Synthetic document {document.document_id}")
    for number, page in enumerate(document.pages, start=1):
        _draw_page(pdf, page, number, len(document.pages))
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _page_count(patient: PatientTruth, document: DocumentTruth) -> int:
    return len(patient.reports[0].pages) if document.pages is None else len(document.pages)


def to_json(
    generated: tuple[tuple[PatientTruth, tuple[DocumentTruth, ...]], ...],
) -> list[dict]:
    """Ground truth: patient identity fields and per-document labels (no page content)."""
    return [
        {
            "patient_key": patient.patient_key,
            "display_name": patient.display_name,
            "date_of_birth": patient.date_of_birth,
            "sex": patient.sex,
            "documents": [
                {
                    "document_id": d.document_id,
                    "kind": d.kind,
                    "variant": d.variant,
                    "true_type": d.true_type,
                    "page_count": _page_count(patient, d),
                }
                for d in documents
            ],
        }
        for patient, documents in generated
    ]


def build_dataset(out: Path, *, seed: int = DEFAULT_SEED, patients: int = DEFAULT_PATIENTS) -> dict:
    """Generate and write ``doctype_v0``; returns the manifest. Never overwrites a dataset."""
    if out.exists():
        raise FileExistsError(f"{out} already exists; a dataset is never overwritten")
    generated = generate_documents(seed, patients)
    ground_truth = to_json(generated)
    (out / "pdfs").mkdir(parents=True)
    files: dict[str, dict] = {}
    data = _json_bytes(ground_truth)  # labels first, then the PDFs drawn from them
    (out / "ground_truth.json").write_bytes(data)
    files["ground_truth.json"] = {"sha256": _sha256(data), "size": len(data)}
    for patient, documents in generated:
        for document in documents:
            pdf = render_document(patient, document)
            path = f"pdfs/{document.document_id}.pdf"
            (out / path).write_bytes(pdf)
            files[path] = {"sha256": _sha256(pdf), "size": len(pdf)}
    split = split_patients([patient.patient_key for patient, _ in generated], seed)
    manifest = {
        "dataset": DATASET_NAME,
        "manifest_version": MANIFEST_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "patients": patients,
        "documents": sum(len(documents) for _, documents in generated),
        "synthetic": True,
        "labels": [LAB_REPORT, OTHER],
        "split": split,
        "heldout_sha256": heldout_digest(ground_truth, files, split["heldout"]),
        "files": files,
    }
    (out / "manifest.json").write_bytes(_json_bytes(manifest))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic document-type dataset.")
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
