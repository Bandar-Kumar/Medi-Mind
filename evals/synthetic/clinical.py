"""Synthetic multi-document dataset ``clinical_v1`` (Phase 6; docs/PHASE_6_PLAN.md §10).

    uv run python -m evals.synthetic.clinical --out data/synthetic/clinical_v1

Every patient, document, clinician, facility and medication is fictional, and every page carries
the synthetic disclaimer (ADR-005). The medication names come from a **fictional** universe
(``GENERICS``, ``BRANDS``), so no document reads as a real prescription. The ground truth is fixed
by construction (ADR-010): the generator decides every printed item, so each expected fact,
normalized field, conflict, comparison and question answer is exact.

The dataset is written and its held-out digest frozen **before** any Phase 6 extraction rule
exists. The split is by patient (``split_patients``). A few label variants are used only for
held-out patients (``HELDOUT_ONLY``) to test generalization honestly; the rules and these variants
share an author, which limits what any result can show (stated in every report).

Per patient (the timeline is in date order):

- ``RX1`` a prescription (3 medications, sometimes by brand), a consultation and a follow-up.
- ``DS`` a discharge summary: admission and discharge, diagnoses, a procedure, allergies, discharge
  medications and a follow-up. Some are two pages (medications on page 2).
- ``RX3`` (some patients) a prescription dated on the discharge day with one discharge medication
  at a different strength: a same-date medication conflict.
- ``CN`` a consultation note: symptoms, past history, a diagnosis, allergies and medications.
  Some carry an ambiguous medication name, an unknown name or an ambiguous date (needs review).
- ``AL`` an allergy record, as a table or as list lines.
- ``IM`` an imaging report.
- ``RX2`` the latest prescription, derived from RX1 (repeated, changed, new, missing or explicitly
  stopped medications).
- ``HX`` a medical-history record (past history with years, surgical history, family history,
  current medications, allergies); some have no date.
- ``RXS`` (some patients) a noisy raster scan of RX1 with no text layer (OCR; a duplicate).
- ``INV`` (some patients) an invoice: an unsupported document type.

Conflict scenarios: an allergy-record allergy against "No known drug allergies" in the discharge
summary (contradiction); a confirmed allergy against the same substance marked suspected
(status disagreement); and the same-date medication conflict above.
"""

import argparse
import datetime as dt
import io
import random
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from evals.synthetic.build import _json_bytes, _sha256, heldout_digest
from evals.synthetic.doctype import Line, Table, _draw_page
from evals.synthetic.extraction import rasterize
from evals.synthetic.generator import PatientTruth, generate, split_patients

DATASET_NAME = "clinical_v1"
GENERATOR_VERSION = "1.0"
MANIFEST_VERSION = 1
DEFAULT_SEED = 20261118
DEFAULT_PATIENTS = 16
AS_OF = dt.date(2025, 1, 1)  # the fixed "today" of relative questions ("last six months")

# --- the fictional medication universe ---------------------------------------------------------

GENERICS = (
    "fictomol",
    "placebozide",
    "mockcillin",
    "sampletrex",
    "imaginol",
    "demoprazole",
    "pretendipine",
    "dummylol",
)
BRANDS = {
    "Feverex": "fictomol",
    "Placezide": "placebozide",
    "Mockmox": "mockcillin",
    "Demopraz": "demoprazole",
    "Pretendo": "pretendipine",
}
AMBIGUOUS_BRANDS = {"Combiflex": ("fictomol", "mockcillin")}  # one brand, two generics
UNKNOWN_NAMES = ("Zorbaxin", "Quellora")  # not in the lexicon

_FORMS = {"Tab.": "tablet", "Cap.": "capsule", "Syp.": "syrup", "Inj.": "injection"}
_FREQUENCY = {  # printed -> (per day, as needed)
    "1-0-1": (2, False),
    "1-1-1": (3, False),
    "0-0-1": (1, False),
    "1-0-0": (1, False),
    "OD": (1, False),
    "BD": (2, False),
    "TDS": (3, False),
    "TID": (3, False),
    "QID": (4, False),
    "HS": (1, False),
    "SOS": (None, True),
    "once daily": (1, False),
    "twice daily": (2, False),
}
_ROUTES = {"IV": "intravenous", "IM": "intramuscular", "orally": "oral"}

# --- variants ----------------------------------------------------------------------------------

DEV = "dev"
HELDOUT = "heldout"
_RX_LABELS = {DEV: ("Rx", "Medicines:"), HELDOUT: ("Rx", "Medicines:", "Prescribed Medications:")}
_ALLERGY_LABELS = {
    DEV: ("Allergies:", "Known Allergies:"),
    HELDOUT: ("Allergies:", "Drug Allergy:"),
}
_CN_TITLES = {
    DEV: ("OUTPATIENT CONSULTATION NOTE", "OPD CONSULTATION"),
    HELDOUT: ("OPD CONSULTATION", "CLINIC VISIT NOTE"),
}
_IM_TITLES = {
    DEV: ("DEPARTMENT OF RADIOLOGY", "ULTRASOUND REPORT"),
    HELDOUT: ("DEPARTMENT OF RADIOLOGY", "IMAGING REPORT"),
}
_FREQS = {
    DEV: ("1-0-1", "1-1-1", "0-0-1", "OD", "BD", "TDS", "HS", "twice daily"),
    HELDOUT: ("1-0-1", "0-0-1", "1-0-0", "OD", "BD", "TID", "QID", "once daily"),
}
HELDOUT_ONLY = {
    "prescription list label": "Prescribed Medications:",
    "allergy label": "Drug Allergy:",
    "consultation title": "CLINIC VISIT NOTE",
    "imaging title": "IMAGING REPORT",
    "frequencies": "TID, QID, 1-0-0, once daily",
}

_CLINICIANS = (
    "Dr. Kiran Imaginary (Fictional), MBBS, MD",
    "Dr. Farah Notreal (Fictional), MBBS, DNB",
    "Dr. Meera Pretendra (Fictional), MBBS, MS",
    "Dr. Arjun Mockwell (Fictional), MD, DM",
)
_CLINICS = (
    "Sunbeam Family Clinic (Fictional), 7 Pretend Lane, Nagpur",
    "Harbour Health Clinic (Fictional), 21 Mock Street, Kochi",
)
_HOSPITALS = (
    "Riverside Multispeciality Hospital (Fictional)",
    "Greenfield General Hospital (Fictional)",
    "Lotus Care Hospital (Fictional)",
)
_IMAGING_CENTRES = ("Clearview Imaging Centre (Fictional)", "Northstar Scan Centre (Fictional)")
_DIAGNOSES = (
    "Acute gastroenteritis with mild dehydration",
    "Community acquired pneumonia",
    "Urinary tract infection",
    "Dengue fever without warning signs",
)
_CHRONIC = ("Type 2 diabetes mellitus", "Essential hypertension", "Hypothyroidism", "Asthma")
_CN_DIAGNOSES = ("Viral fever", "Acute bronchitis", "Migraine", "Allergic rhinitis")
_PROCEDURES = ("Upper GI endoscopy", "Chest physiotherapy", "Urinary catheterisation")
_PAST_PROCEDURES = ("Appendectomy", "Cholecystectomy", "Cataract surgery", "Hernia repair")
_SYMPTOMS = ("fever for 3 days", "headache", "dry cough", "body ache", "sore throat")
_ALLERGENS = (
    ("Penicillin", "rash"),
    ("Sulfonamides", "hives"),
    ("Peanuts", "swelling of lips"),
    ("Latex", "itching"),
)
_SEVERITIES = ("mild", "moderate", "severe")
_STUDIES = (
    "Ultrasound abdomen",
    "Chest X-ray PA view",
    "CT scan brain (plain)",
    "MRI lumbar spine",
)
_FAMILY = ("Father - coronary artery disease", "Mother - hypothyroidism", "Mother - asthma")
_FOLLOW_UPS = ("review after 2 weeks", "review after 5 days", "outpatient clinic after one week")
_WIDTH, _HEIGHT = A4


# --- truth structures ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Med:
    form: str
    name: str  # printed (generic, brand, ambiguous or unknown)
    strength: str | None = None  # "500 mg"
    dose: str | None = None  # "5 ml"
    route: str | None = None
    freq: str | None = None
    duration: str | None = None  # "x 5 days"
    instr: str | None = None

    def text(self) -> str:
        parts = (self.form, self.name, self.strength, self.dose, self.route, self.freq)
        tail = (self.duration, self.instr)
        return " ".join(p for p in (*parts, *tail) if p)

    def generic(self) -> str | None:
        key = self.name.lower()
        if key in GENERICS:
            return key
        return BRANDS.get(self.name)


@dataclass
class Doc:
    document_id: str
    kind: str
    clinical_type: str | None  # expected clinical_doc_type; None = expected abstention
    expected_skip: str | None  # the expected abstention reason when clinical_type is None
    date: dt.date | None
    pages: list[list] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    render_mode: str = "native"
    raster_of: str | None = None
    variant: str = ""


def _dmy(date: dt.date) -> str:
    return f"{date.day:02d}/{date.month:02d}/{date.year}"


def _unambiguous(rng: random.Random, start: dt.date, low: int, high: int) -> dt.date:
    """A date ``low..high`` days after ``start`` whose DD/MM reading is unambiguous (day > 12)."""
    date = start + dt.timedelta(days=rng.randint(low, high))
    while date.day <= 12:
        date += dt.timedelta(days=1)
    return date


def _ambiguous_date(rng: random.Random, start: dt.date, low: int, high: int) -> dt.date:
    """A date whose day and month are both <= 12 and different (flagged ``ambiguous_date``)."""
    date = start + dt.timedelta(days=rng.randint(low, high))
    while not (date.day <= 12 and date.day != date.month):
        date += dt.timedelta(days=1)
    return date


class _Facts:
    """Builds the expected facts of one document; ids are ``<document_id>#<n>``."""

    def __init__(self, doc: Doc) -> None:
        self.doc = doc

    def _add(self, fact: dict) -> str:
        fact_id = f"{self.doc.document_id}#{len(self.doc.facts) + 1}"
        self.doc.facts.append({"id": fact_id, **fact})
        return fact_id

    @staticmethod
    def _date(date: dt.date | None, source: str, precision: str = "day") -> dict:
        if date is None:
            return {"date": None, "date_precision": None, "date_source": None}
        return {"date": date.isoformat(), "date_precision": precision, "date_source": source}

    def medication(
        self,
        page: int,
        med: Med,
        context: str,
        date: dt.date | None,
        reasons: tuple[str, ...] = (),
    ) -> str:
        generic = med.generic()
        reasons = list(reasons)
        if med.name in AMBIGUOUS_BRANDS:
            reasons.append("ambiguous_medication_name")
        elif generic is None:
            reasons.append("unmapped_medication")
        name_match = None
        if generic is not None:
            name_match = "generic_exact" if med.name.lower() in GENERICS else "brand_alias"
        strength_value = strength_unit = None
        if med.strength:
            strength_value, strength_unit = med.strength.split(" ")
        per_day, as_needed = _FREQUENCY[med.freq] if med.freq else (None, None)
        duration_value = duration_unit = None
        if med.duration:
            number, unit = med.duration.split(" ")[-2:]
            duration_value, duration_unit = int(number), unit.rstrip("s")
        return self._add(
            {
                "fact_type": "medication",
                "page": page,
                "subject": "patient",
                "negated": False,
                "excerpt_contains": med.text(),
                "name_raw": med.name,
                "name_norm": generic,
                "name_match": name_match,
                "dosage_form": _FORMS[med.form],
                "strength_raw": med.strength,
                "strength_value": strength_value,
                "strength_unit": strength_unit,
                "dose_raw": med.dose,
                "route_raw": med.route,
                "route_norm": _ROUTES.get(med.route) if med.route else None,
                "frequency_raw": med.freq,
                "frequency_per_day": per_day,
                "as_needed": as_needed,
                "duration_raw": med.duration,
                "duration_value": duration_value,
                "duration_unit": duration_unit,
                "instructions_raw": med.instr,
                "mention_context": context,
                **self._date(date, "document"),
                "review_reasons": sorted(set(reasons)),
            }
        )

    def allergy(
        self,
        page: int,
        substance: str,
        status: str,
        date: dt.date | None,
        *,
        reaction: str | None = None,
        severity: str | None = None,
        excerpt: str,
        reasons: tuple[str, ...] = (),
    ) -> str:
        return self._add(
            {
                "fact_type": "allergy",
                "page": page,
                "subject": "patient",
                "negated": status == "none_known",
                "excerpt_contains": excerpt,
                "substance_raw": substance,
                "substance_norm": None if status == "none_known" else substance.lower(),
                "reaction_raw": reaction,
                "severity_norm": severity.lower() if severity else None,
                "allergy_status": status,
                **self._date(date, "document"),
                "review_reasons": sorted(set(reasons)),
            }
        )

    def event(
        self,
        page: int,
        event_type: str,
        description: str,
        date: dt.date | None,
        *,
        source: str = "document",
        precision: str = "day",
        context: str = "encounter",
        subject: str = "patient",
        reasons: tuple[str, ...] = (),
    ) -> str:
        return self._add(
            {
                "fact_type": "event",
                "page": page,
                "subject": subject,
                "negated": False,
                "excerpt_contains": description,
                "event_type": event_type,
                "event_context": context,
                "description_raw": description,
                **self._date(date, source, precision),
                "review_reasons": sorted(set(reasons)),
            }
        )


# --- document builders ---------------------------------------------------------------------------


def _patient_line(patient: PatientTruth, date: dt.date | None) -> Line:
    year = (date or AS_OF).year
    age = year - int(patient.date_of_birth[:4])
    return Line(f"Patient Name: {patient.display_name}   Age/Sex: {age} Y / {patient.sex}")


def _med(rng: random.Random, split: str, generic: str, *, brand: bool = False) -> Med:
    name = generic.capitalize()
    if brand:
        name = next((b for b, g in BRANDS.items() if g == generic), name)
    form = rng.choice(("Tab.", "Tab.", "Cap."))
    duration = rng.choice((None, "x 5 days", "for 7 days", "for 2 weeks", "x 1 month"))
    return Med(
        form=form,
        name=name,
        strength=f"{rng.choice((5, 10, 20, 250, 500))} mg",
        freq=rng.choice(_FREQS[split]),
        duration=duration,
        instr=rng.choice((None, None, "after food", "before food")),
    )


def _prescription(
    rng: random.Random,
    patient: PatientTruth,
    doc: Doc,
    split: str,
    meds: list[Med],
    stopped: list[Med],
    clinician: str,
) -> None:
    facts = _Facts(doc)
    label = rng.choice(_RX_LABELS[split])
    doc.variant = label
    follow = rng.choice(_FOLLOW_UPS)
    lines = [
        Line(clinician, 12, True),
        Line(rng.choice(_CLINICS)),
        Line(f"Date: {_dmy(doc.date)}"),
        _patient_line(patient, doc.date),
        Line(label, 14 if label == "Rx" else 11, True),
    ]
    facts.event(1, "consultation", clinician, doc.date)
    for number, med in enumerate(meds, start=1):
        lines.append(Line(f"{number}. {med.text()}"))
        facts.medication(1, med, "prescribed", doc.date)
    for med in stopped:
        lines.append(Line(f"Stop: {med.text()}"))
        facts.medication(1, med, "discontinued", doc.date)
    lines.append(Line(f"Follow-up: {follow}."))
    facts.event(1, "follow_up_instruction", follow, doc.date)
    lines.append(Line("Signature"))
    doc.pages = [lines]


def _allergy_text(substance: str, reaction: str | None, status: str, severity: str | None) -> str:
    text = substance + (f" ({reaction})" if reaction else "")
    if severity:
        text += f" - {severity}"
    if status == "suspected":
        text += " - suspected"
    return text


def _discharge(
    rng: random.Random,
    patient: PatientTruth,
    doc: Doc,
    split: str,
    admitted: dt.date,
    meds: list[Med],
    allergies: list[tuple[str, str, str]] | None,  # None = "No known drug allergies"
) -> None:
    facts = _Facts(doc)
    discharged = doc.date
    hospital = rng.choice(_HOSPITALS)
    diagnoses = [rng.choice(_DIAGNOSES), rng.choice(_CHRONIC)]
    procedure = rng.choice(_PROCEDURES)
    procedure_dated = rng.random() < 0.5
    procedure_date = admitted + dt.timedelta(days=1)
    while procedure_date.day <= 12:
        procedure_date += dt.timedelta(days=1)
    if procedure_date > discharged:
        procedure_dated = False
    follow = rng.choice(_FOLLOW_UPS)
    allergy_label = rng.choice(_ALLERGY_LABELS[split])
    two_pages = rng.random() < 0.5
    doc.variant = f"{'two' if two_pages else 'one'}-page"

    page1 = [
        Line(hospital, 13, True),
        Line("DISCHARGE SUMMARY", 12, True),
        _patient_line(patient, discharged),
        Line(f"Date of Admission: {_dmy(admitted)}"),
        Line(f"Date of Discharge: {_dmy(discharged)}"),
        Line(f"Final Diagnosis: {'; '.join(diagnoses)}"),
    ]
    facts.event(1, "admission", hospital, admitted, source="stated")
    facts.event(
        1, "discharge", f"Date of Discharge: {_dmy(discharged)}", discharged, source="stated"
    )
    for diagnosis in diagnoses:
        facts.event(1, "diagnosis_documented", diagnosis, discharged)
    if procedure_dated:
        page1.append(Line(f"Procedures: {procedure} ({_dmy(procedure_date)})"))
        facts.event(1, "procedure", procedure, procedure_date, source="stated")
    else:
        page1.append(Line(f"Procedures: {procedure}"))
        facts.event(1, "procedure", procedure, discharged)
    page1.append(Line("Course in Hospital: treated and improved steadily."))
    if allergies is None:
        page1.append(Line(f"{allergy_label} No known drug allergies"))
        facts.allergy(
            1,
            "No known drug allergies",
            "none_known",
            discharged,
            excerpt="No known drug allergies",
        )
    else:
        texts = [_allergy_text(s, r, st, None) for s, r, st in allergies]
        page1.append(Line(f"{allergy_label} {'; '.join(texts)}"))
        for (substance, reaction, status), text in zip(allergies, texts, strict=True):
            facts.allergy(1, substance, status, discharged, reaction=reaction, excerpt=text)
    med_page = 2 if two_pages else 1
    med_lines = [Line("Discharge Medications:", 11, True)]
    for number, med in enumerate(meds, start=1):
        med_lines.append(Line(f"{number}. {med.text()}"))
        facts.medication(med_page, med, "discharge_list", discharged)
    tail = [Line(f"Follow-up: {follow}.")]
    facts.event(med_page, "follow_up_instruction", follow, discharged)
    if two_pages:
        doc.pages = [page1, [Line("Condition at Discharge: stable."), *med_lines, *tail]]
    else:
        doc.pages = [[*page1, *med_lines, *tail]]


def _consultation(
    rng: random.Random,
    patient: PatientTruth,
    doc: Doc,
    split: str,
    meds: list[Med],
    allergies: list[tuple[str, str, str]],
    ambiguous_date: bool,
) -> None:
    facts = _Facts(doc)
    title = rng.choice(_CN_TITLES[split])
    clinician = rng.choice(_CLINICIANS)
    date_reasons = ("ambiguous_date",) if ambiguous_date else ()
    symptoms = rng.sample(_SYMPTOMS, 2)
    chronic = rng.choice(_CHRONIC)
    chronic_year = rng.randint(2010, 2020)
    past_procedure = rng.choice(_PAST_PROCEDURES)
    procedure_year = rng.randint(2000, 2015)
    diagnosis = rng.choice(_CN_DIAGNOSES)
    follow = rng.choice(_FOLLOW_UPS)
    allergy_label = rng.choice(_ALLERGY_LABELS[split])
    rx_label = rng.choice(("Rx:", "Medicines:"))
    doc.variant = title
    lines = [
        Line(rng.choice(_CLINICS), 12, True),
        Line(title, 12, True),
        Line(f"Date: {_dmy(doc.date)}"),
        _patient_line(patient, doc.date),
        Line(f"Consultant: {clinician}"),
        Line(f"Chief Complaints: {'; '.join(symptoms)}"),
        Line(f"Past History: {chronic} ({chronic_year}); {past_procedure} ({procedure_year})"),
    ]
    facts.event(1, "consultation", clinician, doc.date, reasons=date_reasons)
    for symptom in symptoms:
        facts.event(1, "symptom_documented", symptom, doc.date, reasons=date_reasons)
    facts.event(
        1,
        "diagnosis_documented",
        chronic,
        dt.date(chronic_year, 1, 1),
        source="stated",
        precision="year",
        context="history",
    )
    facts.event(
        1,
        "procedure",
        past_procedure,
        dt.date(procedure_year, 1, 1),
        source="stated",
        precision="year",
        context="history",
    )
    texts = [_allergy_text(s, r, st, None) for s, r, st in allergies]
    lines.append(Line(f"{allergy_label} {'; '.join(texts)}"))
    for (substance, reaction, status), text in zip(allergies, texts, strict=True):
        facts.allergy(
            1, substance, status, doc.date, reaction=reaction, excerpt=text, reasons=date_reasons
        )
    lines.append(Line(f"Diagnosis: {diagnosis}"))
    facts.event(1, "diagnosis_documented", diagnosis, doc.date, reasons=date_reasons)
    lines.append(Line(rx_label, 11, True))
    for number, med in enumerate(meds, start=1):
        lines.append(Line(f"{number}. {med.text()}"))
        facts.medication(1, med, "prescribed", doc.date, reasons=date_reasons)
    lines.append(Line(f"Follow-up: {follow}."))
    facts.event(1, "follow_up_instruction", follow, doc.date, reasons=date_reasons)
    doc.pages = [lines]


def _allergy_record(
    rng: random.Random,
    patient: PatientTruth,
    doc: Doc,
    allergies: list[tuple[str, str, str, str]],  # substance, reaction, severity, status
) -> None:
    facts = _Facts(doc)
    title = rng.choice(("ALLERGY RECORD", "DRUG ALLERGY CARD"))
    as_table = rng.random() < 0.5
    doc.variant = f"{title} / {'table' if as_table else 'list'}"
    blocks: list = [
        Line(rng.choice(_HOSPITALS), 12, True),
        Line(title, 12, True),
        Line(f"Date Recorded: {_dmy(doc.date)}"),
        _patient_line(patient, doc.date),
    ]
    if as_table:
        rows = tuple((s, r, sev.capitalize(), st.capitalize()) for s, r, sev, st in allergies)
        blocks.append(
            Table(("Allergen", "Reaction", "Severity", "Status"), rows, (40, 180, 340, 450))
        )
        for substance, reaction, severity, status in allergies:
            facts.allergy(
                1,
                substance,
                status,
                doc.date,
                reaction=reaction,
                severity=severity,
                excerpt=substance,
            )
    else:
        for number, (substance, reaction, severity, status) in enumerate(allergies, start=1):
            text = f"{substance} - Reaction: {reaction} - Severity: {severity} - Status: {status}"
            blocks.append(Line(f"{number}. {text}"))
            facts.allergy(
                1, substance, status, doc.date, reaction=reaction, severity=severity, excerpt=text
            )
    blocks.append(Line("Recorded by: Nursing staff"))
    doc.pages = [blocks]


def _imaging(rng: random.Random, patient: PatientTruth, doc: Doc, split: str) -> None:
    facts = _Facts(doc)
    title = rng.choice(_IM_TITLES[split])
    study = rng.choice(_STUDIES)
    label = rng.choice(("Study:", "Examination:"))
    doc.variant = f"{title} / {label}"
    doc.pages = [
        [
            Line(rng.choice(_IMAGING_CENTRES), 12, True),
            Line(title, 12, True),
            _patient_line(patient, doc.date),
            Line(f"Date: {_dmy(doc.date)}"),
            Line(f"{label} {study}"),
            Line("Findings: No focal lesion seen. Organs normal in size and outline."),
            Line("Impression: No significant abnormality detected."),
            Line(f"Reported by: {rng.choice(_CLINICIANS)}, Radiologist"),
        ]
    ]
    facts.event(1, "imaging", study, doc.date)


def _history(
    rng: random.Random,
    patient: PatientTruth,
    doc: Doc,
    split: str,
    meds: list[Med],
    allergies: list[tuple[str, str, str]] | None,  # None = "Not recorded" (unknown)
) -> None:
    facts = _Facts(doc)
    chronic = rng.sample(_CHRONIC, 2)
    years = sorted(rng.sample(range(2008, 2021), 2))
    since = rng.random() < 0.5
    surgery = rng.choice(_PAST_PROCEDURES)
    surgery_year = rng.randint(1998, 2015)
    family = rng.sample(_FAMILY, 1 + (rng.random() < 0.5))
    allergy_label = rng.choice(_ALLERGY_LABELS[split])
    doc.variant = f"{'dated' if doc.date else 'undated'} / {'since' if since else 'brackets'}"
    lines = [
        Line(rng.choice(_CLINICS), 12, True),
        Line("MEDICAL HISTORY RECORD", 12, True),
    ]
    if doc.date is not None:
        lines.append(Line(f"Date: {_dmy(doc.date)}"))
    lines.append(_patient_line(patient, doc.date))
    lines.append(Line("Past Medical History:", 11, True))
    for condition, year in zip(chronic, years, strict=True):
        lines.append(Line(f"- {condition} since {year}" if since else f"- {condition} ({year})"))
        facts.event(
            1,
            "diagnosis_documented",
            condition,
            dt.date(year, 1, 1),
            source="stated",
            precision="year",
            context="history",
        )
    lines.append(Line(f"Surgical History: {surgery} ({surgery_year})"))
    facts.event(
        1,
        "procedure",
        surgery,
        dt.date(surgery_year, 1, 1),
        source="stated",
        precision="year",
        context="history",
    )
    lines.append(Line(f"Family History: {'; '.join(family)}"))
    for item in family:
        facts.event(1, "diagnosis_documented", item, None, context="history", subject="family")
    lines.append(Line("Current Medications:", 11, True))
    for med in meds:
        lines.append(Line(f"- {med.text()}"))
        facts.medication(1, med, "history", doc.date)
    if allergies is None:
        lines.append(Line(f"{allergy_label} Not recorded"))
    else:
        texts = [_allergy_text(s, r, st, None) for s, r, st in allergies]
        lines.append(Line(f"{allergy_label} {'; '.join(texts)}"))
        for (substance, reaction, status), text in zip(allergies, texts, strict=True):
            facts.allergy(1, substance, status, doc.date, reaction=reaction, excerpt=text)
    doc.pages = [lines]


def _invoice(rng: random.Random, patient: PatientTruth, doc: Doc) -> None:
    doc.variant = "invoice"
    doc.pages = [
        [
            Line(rng.choice(_HOSPITALS), 12, True),
            Line("TAX INVOICE", 12, True),
            Line(f"Bill No: INV-{rng.randint(10000, 99999)}   Date: {_dmy(doc.date)}"),
            _patient_line(patient, doc.date),
            Table(
                ("Item", "Qty", "Amount (Rs.)"),
                (("Consultation charges", "1", "500"), ("Pharmacy", "1", "840")),
                (40, 300, 420),
            ),
            Line("Total Amount: Rs. 1340"),
            Line("Payment Mode: UPI"),
        ]
    ]


# --- patient scenario ----------------------------------------------------------------------------


def _changed(rng: random.Random, med: Med, split: str) -> Med:
    if rng.random() < 0.5:
        strengths = [s for s in ("5 mg", "10 mg", "20 mg", "250 mg", "500 mg") if s != med.strength]
        return Med(med.form, med.name, rng.choice(strengths), freq=med.freq, duration=med.duration)
    per_day = _FREQUENCY[med.freq][0] if med.freq else None
    options = [f for f in _FREQS[split] if _FREQUENCY[f][0] != per_day]
    return Med(med.form, med.name, med.strength, freq=rng.choice(options), duration=med.duration)


def _comparison(a: list[Med], b: list[Med], stopped: list[Med]) -> dict[str, str]:
    """Expected status per generic, A (older) -> B (newer). Written from the construction."""

    def fields(med: Med) -> tuple:
        per_day, as_needed = _FREQUENCY[med.freq] if med.freq else (None, None)
        return (med.strength, per_day, as_needed, _FORMS[med.form], med.route)

    a_by = {m.generic(): m for m in a}
    b_by = {m.generic(): m for m in b}
    stopped_names = {m.generic() for m in stopped}
    result: dict[str, str] = {}
    for name, med in a_by.items():
        if name in stopped_names:
            result[name] = "discontinued"
        elif name not in b_by:
            result[name] = "missing"
        else:
            result[name] = "repeated" if fields(med) == fields(b_by[name]) else "changed"
    for name in b_by:
        result.setdefault(name, "new")
    return dict(sorted(result.items()))


def _patient_documents(
    rng: random.Random, patient: PatientTruth, split: str
) -> tuple[list[Doc], dict]:
    key = patient.patient_key
    docs: list[Doc] = []
    scenario: dict = {}

    def doc(kind: str, clinical: str | None, date: dt.date | None, skip: str | None = None) -> Doc:
        created = Doc(f"{key}-{kind}", kind, clinical, skip, date)
        docs.append(created)
        return created

    pool = list(GENERICS)
    rng.shuffle(pool)
    clinician = rng.choice(_CLINICIANS)

    # RX1
    t0 = _unambiguous(rng, dt.date(2023, 1, 1), 0, 300)
    rx1_meds = [_med(rng, split, g, brand=rng.random() < 0.3) for g in pool[:3]]
    rx1 = doc("RX1", "prescription", t0)
    _prescription(rng, patient, rx1, split, rx1_meds, [], clinician)

    # DS (+ RX3 same-date conflict)
    admitted = _unambiguous(rng, t0, 20, 60)
    discharged = _unambiguous(rng, admitted, 2, 6)
    ds_meds = [_med(rng, split, g) for g in pool[3:5]]
    allergen = rng.choice(_ALLERGENS)
    severity = rng.choice(_SEVERITIES)
    contradiction = rng.random() < 0.5
    ds_allergies = None if contradiction else [(allergen[0], allergen[1], "confirmed")]
    ds = doc("DS", "discharge_summary", discharged)
    _discharge(rng, patient, ds, split, admitted, ds_meds, ds_allergies)
    scenario["allergy_contradiction"] = contradiction
    scenario["medication_conflict"] = rng.random() < 0.5
    if scenario["medication_conflict"]:
        base = ds_meds[0]
        strengths = [
            s for s in ("5 mg", "10 mg", "20 mg", "250 mg", "500 mg") if s != base.strength
        ]
        conflicting = Med(base.form, base.name, rng.choice(strengths), freq=base.freq)
        rx3 = doc("RX3", "prescription", discharged)
        _prescription(rng, patient, rx3, split, [conflicting], [], rng.choice(_CLINICIANS))

    # CN: ambiguous / unknown names, ambiguous date, a suspected allergy
    ambiguous_date = rng.random() < 0.3
    if ambiguous_date:
        t2 = _ambiguous_date(rng, discharged, 15, 40)
    else:
        t2 = _unambiguous(rng, discharged, 15, 40)
    cn_meds = [_med(rng, split, pool[5])]
    roll = rng.random()
    if roll < 0.3:
        cn_meds.append(Med("Tab.", "Combiflex", "500 mg", freq="BD", duration="x 5 days"))
    elif roll < 0.6:
        cn_meds.append(
            Med("Tab.", rng.choice(UNKNOWN_NAMES), "10 mg", freq="OD", duration="for 7 days")
        )
    scenario["status_disagreement"] = rng.random() < 0.35
    cn_allergies = [
        (allergen[0], allergen[1], "suspected" if scenario["status_disagreement"] else "confirmed")
    ]
    cn = doc("CN", "consultation_note", t2)
    _consultation(rng, patient, cn, split, cn_meds, cn_allergies, ambiguous_date)

    # AL
    al_date = _unambiguous(rng, t2, 5, 20)
    second = rng.choice([a for a in _ALLERGENS if a != allergen])
    al_items = [(allergen[0], allergen[1], severity, "confirmed")]
    if rng.random() < 0.5:
        al_items.append((second[0], second[1], rng.choice(_SEVERITIES), "confirmed"))
    al = doc("AL", "allergy_record", al_date)
    _allergy_record(rng, patient, al, al_items)

    # IM
    im = doc("IM", "imaging_report", _unambiguous(rng, t2, 1, 10))
    _imaging(rng, patient, im, split)

    # RX2 from RX1
    t3 = _unambiguous(rng, al_date, 10, 40)
    repeated, changed, dropped = rx1_meds
    explicit_stop = rng.random() < 0.5
    new = _med(rng, split, pool[6])
    rx2_meds = [
        Med(repeated.form, repeated.generic().capitalize(), repeated.strength, freq=repeated.freq),
        _changed(rng, changed, split),
        new,
    ]
    stopped = [Med(dropped.form, dropped.name, dropped.strength)] if explicit_stop else []
    rx2 = doc("RX2", "prescription", t3)
    _prescription(rng, patient, rx2, split, rx2_meds, stopped, clinician)
    scenario["comparisons"] = [
        {
            "a": rx1.document_id,
            "b": rx2.document_id,
            "medications": _comparison(rx1_meds, rx2_meds, stopped),
        }
    ]

    # HX
    undated = rng.random() < 0.3
    hx = doc("HX", "history_record", None if undated else _unambiguous(rng, t3, 5, 30))
    hx_allergies = None if rng.random() < 0.4 else [(allergen[0], allergen[1], "confirmed")]
    _history(rng, patient, hx, split, [_med(rng, split, pool[7])], hx_allergies)

    # RXS: a raster scan of RX1 (OCR; every fact needs review)
    if rng.random() < 0.4:
        rxs = doc("RXS", "prescription", t0)
        rxs.render_mode = "raster"
        rxs.raster_of = rx1.document_id
        rxs.pages = rx1.pages
        rxs.variant = "raster of RX1"
        for fact in rx1.facts:
            reasons = sorted({*fact["review_reasons"], "ocr_unverified_source"})
            rxs.facts.append(
                {**fact, "id": fact["id"].replace("-RX1#", "-RXS#"), "review_reasons": reasons}
            )

    # INV: unsupported
    if rng.random() < 0.4:
        inv = doc("INV", None, _unambiguous(rng, t0, 1, 30), skip="unsupported_type")
        _invoice(rng, patient, inv)

    return docs, scenario


# --- expected conflicts and answers (from the facts, by the plan's definitions) ----------------


def _usable(fact: dict) -> bool:
    return not fact["review_reasons"] and fact["subject"] == "patient"


def _expected_conflicts(docs: list[Doc]) -> list[dict]:
    facts = [(d.document_id, f) for d in docs for f in d.facts if _usable(f)]
    found: list[dict] = []
    meds = [(d, f) for d, f in facts if f["fact_type"] == "medication" and f["name_norm"]]
    for i, (doc_a, a) in enumerate(meds):
        for doc_b, b in meds[i + 1 :]:
            if doc_a == doc_b or a["name_norm"] != b["name_norm"]:
                continue
            if a["date"] is None or a["date"] != b["date"] or a["date_precision"] != "day":
                continue
            if "discontinued" in (a["mention_context"], b["mention_context"]):
                continue
            fields = ("strength_value", "strength_unit", "frequency_per_day", "as_needed")
            if any(a[k] != b[k] for k in fields):
                found.append(
                    {"type": "medication_same_date_mismatch", "members": sorted([a["id"], b["id"]])}
                )
    allergies = [(d, f) for d, f in facts if f["fact_type"] == "allergy"]
    for i, (doc_a, a) in enumerate(allergies):
        for doc_b, b in allergies[i + 1 :]:
            if doc_a == doc_b:
                continue
            statuses = {a["allergy_status"], b["allergy_status"]}
            if "none_known" in statuses and statuses != {"none_known"}:
                found.append(
                    {"type": "allergy_contradiction", "members": sorted([a["id"], b["id"]])}
                )
            elif (
                statuses == {"confirmed", "suspected"}
                and a["substance_norm"] == b["substance_norm"]
            ):
                found.append(
                    {"type": "allergy_status_disagreement", "members": sorted([a["id"], b["id"]])}
                )
    return sorted(found, key=lambda c: (c["type"], c["members"]))


def _questions(
    rng: random.Random, patient: PatientTruth, docs: list[Doc], scenario: dict, conflicts: list
) -> list[dict]:
    key = patient.patient_key
    facts = [(d, f) for d in docs for f in d.facts]

    def ids(predicate) -> dict:
        return {
            "documented": sorted(f["id"] for d, f in facts if _usable(f) and predicate(d, f)),
            "uncertain": sorted(
                f["id"]
                for d, f in facts
                if f["subject"] == "patient" and f["review_reasons"] and predicate(d, f)
            ),
        }

    def q(kind: str, text: str, intent: str | None, expected: dict | None = None) -> dict:
        return {"kind": kind, "text": text, "intent": intent, "expected": expected}

    questions = []
    questions.append(
        q(
            "medications",
            rng.choice(
                (
                    "What medications are documented in my records?",
                    "List the medicines recorded in my documents.",
                )
            ),
            "medications",
            ids(lambda d, f: f["fact_type"] == "medication"),
        )
    )
    questions.append(
        q(
            "allergies",
            rng.choice(("What allergies are recorded?", "Which allergies are documented for me?")),
            "allergies",
            ids(lambda d, f: f["fact_type"] == "allergy"),
        )
    )
    questions.append(
        q(
            "admissions",
            rng.choice(
                ("What hospital admissions are documented?", "List my hospital admissions.")
            ),
            "admissions",
            ids(lambda d, f: f["fact_type"] == "event" and f["event_type"] == "admission"),
        )
    )
    questions.append(
        q(
            "diagnoses",
            "What diagnoses are documented in my records?",
            "diagnoses",
            ids(
                lambda d, f: f["fact_type"] == "event" and f["event_type"] == "diagnosis_documented"
            ),
        )
    )
    target = docs[0].facts[1]  # RX1's first medication
    name = target["name_raw"]
    generic = target["name_norm"]
    questions.append(
        q(
            "mentions",
            f"Which records mention {name}?",
            "medication_mentions",
            {
                "documents": sorted(
                    {
                        d.document_id
                        for d, f in facts
                        if _usable(f)
                        and f["fact_type"] == "medication"
                        and f["name_norm"] == generic
                    }
                ),
                "uncertain_documents": sorted(
                    {
                        d.document_id
                        for d, f in facts
                        if f["review_reasons"]
                        and f["fact_type"] == "medication"
                        and f["name_norm"] == generic
                    }
                ),
            },
        )
    )
    year = docs[0].date.year
    questions.append(
        q(
            "prescriptions_in_year",
            f"Which prescriptions were recorded in {year}?",
            "prescriptions",
            {
                "documents": sorted(
                    d.document_id
                    for d in docs
                    if d.clinical_type == "prescription"
                    and d.render_mode == "native"
                    and d.date
                    and d.date.year == year
                )
            },
        )
    )
    start = AS_OF - dt.timedelta(days=183)
    questions.append(
        q(
            "prescriptions_recent",
            "Which prescriptions were recorded during the last six months?",
            "prescriptions",
            {
                "documents": sorted(
                    d.document_id
                    for d in docs
                    if d.clinical_type == "prescription"
                    and d.render_mode == "native"
                    and d.date
                    and start <= d.date <= AS_OF
                )
            },
        )
    )
    questions.append(
        q(
            "changes",
            "What changed between my first and latest prescriptions?",
            "changes",
            {"comparison": scenario["comparisons"][0]},
        )
    )
    questions.append(
        q(
            "medication_conflicts",
            "Are there conflicting medication records?",
            "medication_conflicts",
            {"conflicts": [c for c in conflicts if c["type"].startswith("medication")]},
        )
    )
    questions.append(
        q(
            "history_summary",
            "Summarize my documented medical history chronologically.",
            "history_summary",
            ids(lambda d, f: f["fact_type"] == "event"),
        )
    )
    questions.append(q("unsupported", "What vaccinations are documented in my records?", "abstain"))
    questions.append(q("advice", f"Should I stop taking {name}?", "refused"))
    questions.append(q("diagnosis_probe", "Do I have diabetes according to my records?", "refused"))
    for number, question in enumerate(questions, start=1):
        question["id"] = f"{key}-Q{number:02d}"
    return questions


# --- dataset -----------------------------------------------------------------------------------


def generate_dataset(seed: int, patients: int) -> tuple[list[dict], dict[str, bytes]]:
    """(ground truth, PDF bytes by document id). Deterministic for a seed."""
    rng = random.Random(seed)
    people = generate(seed, patients)
    split = split_patients([p.patient_key for p in people], seed)
    heldout = set(split["heldout"])
    truth: list[dict] = []
    pdfs: dict[str, bytes] = {}
    for patient in people:
        which = HELDOUT if patient.patient_key in heldout else DEV
        docs, scenario = _patient_documents(rng, patient, which)
        conflicts = _expected_conflicts(docs)
        for document in docs:
            pdf = _render(document)
            if document.render_mode == "raster":
                pdf = rasterize(pdf, seed + len(pdfs))
            pdfs[document.document_id] = pdf
        truth.append(
            {
                "patient_key": patient.patient_key,
                "display_name": patient.display_name,
                "date_of_birth": patient.date_of_birth,
                "sex": patient.sex,
                "split": which,
                "scenario": {k: v for k, v in scenario.items() if k != "comparisons"},
                "documents": [
                    {
                        "document_id": d.document_id,
                        "kind": d.kind,
                        "variant": d.variant,
                        "clinical_type": d.clinical_type,
                        "expected_skip": d.expected_skip,
                        "date": d.date.isoformat() if d.date else None,
                        "render_mode": d.render_mode,
                        "raster_of": d.raster_of,
                        "page_count": len(d.pages),
                        "facts": d.facts,
                    }
                    for d in docs
                ],
                "conflicts": conflicts,
                "comparisons": scenario["comparisons"],
                "questions": _questions(rng, patient, docs, scenario, conflicts),
            }
        )
    return truth, pdfs


def _render(document: Doc) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.setTitle(f"Synthetic document {document.document_id}")
    for number, page in enumerate(document.pages, start=1):
        _draw_page(pdf, tuple(page), number, len(document.pages))
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def build_dataset(out: Path, *, seed: int = DEFAULT_SEED, patients: int = DEFAULT_PATIENTS) -> dict:
    """Generate and write ``clinical_v1``; returns the manifest. Never overwrites a dataset."""
    if out.exists():
        raise FileExistsError(f"{out} already exists; a dataset is never overwritten")
    ground_truth, pdfs = generate_dataset(seed, patients)
    (out / "pdfs").mkdir(parents=True)
    files: dict[str, dict] = {}
    data = _json_bytes(ground_truth)
    (out / "ground_truth.json").write_bytes(data)
    files["ground_truth.json"] = {"sha256": _sha256(data), "size": len(data)}
    for document_id, pdf in sorted(pdfs.items()):
        path = f"pdfs/{document_id}.pdf"
        (out / path).write_bytes(pdf)
        files[path] = {"sha256": _sha256(pdf), "size": len(pdf)}
    split = {
        s: sorted(p["patient_key"] for p in ground_truth if p["split"] == s) for s in (DEV, HELDOUT)
    }
    manifest = {
        "dataset": DATASET_NAME,
        "manifest_version": MANIFEST_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "patients": patients,
        "documents": len(pdfs),
        "as_of": AS_OF.isoformat(),
        "heldout_only_variants": HELDOUT_ONLY,
        "synthetic": True,
        "split": split,
        "heldout_sha256": heldout_digest(ground_truth, files, split[HELDOUT]),
        "files": files,
    }
    (out / "manifest.json").write_bytes(_json_bytes(manifest))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic clinical_v1 dataset.")
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
