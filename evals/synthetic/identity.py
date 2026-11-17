"""Synthetic identity-verification dataset ``identity_v0`` (Task 2.8, ADR-036 D4, D5).

    uv run python -m evals.synthetic.identity --out data/synthetic/identity_v0

Separate from ``lab_v0`` and ``doctype_v0``. Every patient, name and value is fictional, and every
page carries the synthetic disclaimer (ADR-005).

Patients are listed explicitly so the hard cases are guaranteed, not left to chance:
- families: patients who share a surname (a caregiver's typical profiles);
- namesakes: two patients with the same full name and different dates of birth.
The split is by patient and fixed: ``I01``-``I08`` dev, ``I09``-``I12`` held-out; each split has
its own families and its own namesake pair. The held-out digest is frozen when the dataset is
built (``evals.dataset.load_dataset`` re-checks it).

Per patient, one PDF per variant (``variant``: what is printed):
- ``lab_header``    ``Patient Name: First Last  Age/Sex: ...`` (no date of birth)
- ``reordered``     ``PATIENT NAME : Mr./Ms. LAST, FIRST`` (upper case, honorific, comma)
- ``dob_named``     full name and ``DOB: 07-Mar-1984`` (an unambiguous date)
- ``dob_numeric``   full name and ``D.O.B: DD/MM/YYYY`` (ambiguous when day and month <= 12)
- ``initials``      ``Patient Name: F. Last``
- ``first_only``    ``Patient: First``
- ``no_label``      a letter naming the patient in a sentence only
- ``two_names``     two ``Patient Name:`` fields with different people
- ``unreadable``    a name with a glyph the parser cannot map (Docling prints ``■``)
Plus, per patient, one text-level case ``fffd`` (``Patient Name:`` with U+FFFD in the name): the
reportlab/Docling path does not produce U+FFFD for names, so this case is stored as page text in
the ground truth and evaluated without parsing; reports keep it separate.

Every document is checked against every profile of its split. The expected outcome is fixed by
construction (``expected_outcome``) from what the document prints, not from the checker's output.
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
from evals.synthetic.doctype import Line, Page, _draw_page

DATASET_NAME = "identity_v0"
GENERATOR_VERSION = "0.1"
MANIFEST_VERSION = 1
DEFAULT_SEED = 20261118

MATCH, MISMATCH, UNKNOWN = "match", "mismatch", "unknown"
VARIANTS = (
    ("lab_header", "LH"),
    ("reordered", "RO"),
    ("dob_named", "DN"),
    ("dob_numeric", "DD"),
    ("initials", "IN"),
    ("first_only", "FO"),
    ("no_label", "NL"),
    ("two_names", "TN"),
    ("unreadable", "UR"),
)
TEXT_VARIANT = ("fffd", "FF")
FULL_NAME_VARIANTS = frozenset({"lab_header", "reordered", "dob_named", "dob_numeric"})
UNREADABLE_GLYPH = "₂"  # subscript two: not in the PDF font; Docling prints U+25A0

# (key, first, last, sex): families share a surname; I01/I02 and I09/I10 are namesakes.
PATIENTS = (
    ("I01", "Aarav", "Sampleton", "M"),
    ("I02", "Aarav", "Sampleton", "M"),
    ("I03", "Diya", "Testwala", "F"),
    ("I04", "Kabir", "Testwala", "M"),
    ("I05", "Meera", "Fictional", "F"),
    ("I06", "Rohan", "Fictional", "M"),
    ("I07", "Ananya", "Demorao", "F"),
    ("I08", "Ishita", "Mockrishnan", "F"),
    ("I09", "Vihaan", "Dummyjee", "M"),
    ("I10", "Vihaan", "Dummyjee", "M"),
    ("I11", "Kavya", "Placeholder", "F"),
    ("I12", "Tara", "Placeholder", "F"),
)
SPLIT = {
    "dev": [key for key, *_ in PATIENTS[:8]],
    "heldout": [key for key, *_ in PATIENTS[8:]],
}


@dataclass(frozen=True)
class Profile:
    patient_key: str
    first: str
    last: str
    sex: str
    date_of_birth: dt.date

    @property
    def display_name(self) -> str:
        return f"{self.first} {self.last}"


def profiles(seed: int = DEFAULT_SEED) -> tuple[Profile, ...]:
    rng = random.Random(seed)
    result = []
    for key, first, last, sex in PATIENTS:
        born = dt.date(rng.randrange(1950, 2005), rng.randrange(1, 13), rng.randrange(1, 29))
        result.append(Profile(key, first, last, sex, born))
    return tuple(result)


def dob_numeric_is_ambiguous(date: dt.date) -> bool:
    """ADR-025: a DD/MM/YYYY date is ambiguous when day and month are both <= 12 and differ."""
    return date.day <= 12 and date.month <= 12 and date.day != date.month


def _lines(variant: str, me: Profile, other: Profile) -> tuple[Line, ...]:
    report = dt.date(2024, 6, 15)
    age = report.year - me.date_of_birth.year
    honorific = "Mr." if me.sex == "M" else "Ms."
    head = (Line("Sunrise Diagnostics (Fictional)", 14, True),)
    if variant == "lab_header":
        body = (Line(f"Patient Name: {me.display_name}    Age/Sex: {age} Y / {me.sex}"),)
    elif variant == "reordered":
        body = (Line(f"PATIENT NAME : {honorific} {me.last.upper()}, {me.first.upper()}"),)
    elif variant == "dob_named":
        dob = me.date_of_birth.strftime("%d-%b-%Y")
        body = (Line(f"Patient Name: {me.display_name}"), Line(f"DOB: {dob}"))
    elif variant == "dob_numeric":
        dob = me.date_of_birth.strftime("%d/%m/%Y")
        body = (Line(f"Patient's Name: {me.display_name}    D.O.B: {dob}"),)
    elif variant == "initials":
        body = (Line(f"Patient Name: {me.first[0]}. {me.last}"),)
    elif variant == "first_only":
        body = (Line(f"Patient: {me.first}"),)
    elif variant == "no_label":
        head = (Line("Dr. Kiran Imaginary (Fictional)", 12, True),)
        body = (
            Line("Dear Doctor,"),
            Line(f"I am referring {me.display_name} for a routine review."),
            Line("Yours sincerely,"),
        )
    elif variant == "two_names":
        body = (
            Line(f"Patient Name: {me.display_name}"),
            Line(f"Patient Name: {other.display_name}"),
        )
    elif variant == "unreadable":
        garbled = me.first[:2] + UNREADABLE_GLYPH + me.first[2:]
        body = (Line(f"Patient Name: {garbled} {me.last}"),)
    else:
        raise ValueError(f"unknown variant {variant!r}")
    tail = (Line(f"Report Date: {report.strftime('%d/%m/%Y')}"), Line("Haemoglobin 13.1 gm/dL"))
    return head + body + tail


def fffd_text(me: Profile) -> str:
    """The text-level case: page text as a parser might return it, with U+FFFD in the name."""
    return f"Patient Name: {me.first[:2]}�{me.first[2:]} {me.last}\nReport Date: 15/06/2024"


def expected_outcome(variant: str, document_patient: Profile, profile: Profile) -> str:
    """The correct outcome by construction: what the document prints vs the profile (D4, D5)."""
    same_name = (document_patient.first, document_patient.last) == (profile.first, profile.last)
    same_person = document_patient.patient_key == profile.patient_key
    if variant not in FULL_NAME_VARIANTS:
        return UNKNOWN  # initials, first name only, no label, two names, unreadable, fffd
    if not same_name:
        return MISMATCH  # a different full name (families differ in the first name)
    if variant in ("dob_named", "dob_numeric"):
        if variant == "dob_numeric" and dob_numeric_is_ambiguous(document_patient.date_of_birth):
            return UNKNOWN  # the printed date cannot be read with certainty
        return MATCH if same_person else MISMATCH  # a namesake has another date of birth
    return MATCH  # same full name, no date printed: a namesake is indistinguishable


def documents(seed: int = DEFAULT_SEED) -> list[tuple[Profile, str, str, tuple[Page, ...]]]:
    """(patient, document id, variant, pages) for every PDF. ``two_names`` uses the next
    patient of the same split as the second name."""
    people = profiles(seed)
    result = []
    for split_keys in SPLIT.values():
        members = [p for p in people if p.patient_key in split_keys]
        for index, me in enumerate(members):
            other = members[(index + 2) % len(members)]  # never the namesake partner
            for variant, suffix in VARIANTS:
                pages = (_lines(variant, me, other),)
                result.append((me, f"{me.patient_key}-{suffix}", variant, pages))
    return result


def render(document_id: str, pages: tuple[Page, ...]) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.setTitle(f"Synthetic identity document {document_id}")
    for number, page in enumerate(pages, start=1):
        _draw_page(pdf, page, number, len(pages))
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def ground_truth(seed: int = DEFAULT_SEED) -> list[dict]:
    """Profiles and their documents: ids, variants, and the text-level U+FFFD case."""
    by_patient: dict[str, list[dict]] = {}
    for me, document_id, variant, _ in documents(seed):
        by_patient.setdefault(me.patient_key, []).append(
            {"document_id": document_id, "variant": variant, "source": "pdf"}
        )
    result = []
    for person in profiles(seed):
        docs = by_patient[person.patient_key]
        docs.append(
            {
                "document_id": f"{person.patient_key}-{TEXT_VARIANT[1]}",
                "variant": TEXT_VARIANT[0],
                "source": "text",
                "text": fffd_text(person),
            }
        )
        result.append(
            {
                "patient_key": person.patient_key,
                "display_name": person.display_name,
                "first": person.first,
                "last": person.last,
                "sex": person.sex,
                "date_of_birth": person.date_of_birth.isoformat(),
                "documents": docs,
            }
        )
    return result


def build_dataset(out: Path, *, seed: int = DEFAULT_SEED) -> dict:
    """Write ``identity_v0``; returns the manifest. Never overwrites a dataset."""
    if out.exists():
        raise FileExistsError(f"{out} already exists; a dataset is never overwritten")
    truth = ground_truth(seed)
    (out / "pdfs").mkdir(parents=True)
    files: dict[str, dict] = {}
    data = _json_bytes(truth)
    (out / "ground_truth.json").write_bytes(data)
    files["ground_truth.json"] = {"sha256": _sha256(data), "size": len(data)}
    for _, document_id, _, pages in documents(seed):
        pdf = render(document_id, pages)
        path = f"pdfs/{document_id}.pdf"
        (out / path).write_bytes(pdf)
        files[path] = {"sha256": _sha256(pdf), "size": len(pdf)}
    manifest = {
        "dataset": DATASET_NAME,
        "manifest_version": MANIFEST_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "patients": len(truth),
        "documents": sum(len(p["documents"]) for p in truth),
        "synthetic": True,
        "split": SPLIT,
        "heldout_sha256": heldout_digest(truth, files, SPLIT["heldout"]),
        "files": files,
    }
    (out / "manifest.json").write_bytes(_json_bytes(manifest))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic identity dataset.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    manifest = build_dataset(args.out, seed=args.seed)
    print(
        f"Dataset {manifest['dataset']}: {manifest['patients']} patients, "
        f"{manifest['documents']} documents; dev {len(manifest['split']['dev'])} / held-out "
        f"{len(manifest['split']['heldout'])} patients; heldout_sha256 {manifest['heldout_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
