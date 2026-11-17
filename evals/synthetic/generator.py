"""Ground-truth generation (Task 1.9): fictional patients and longitudinal lab reports.

The ground truth is created FIRST and fixes everything a PDF will show, including which page each
row is printed on; ``render.py`` then draws exactly that. Labels are therefore exact by
construction (ADR-010). Deterministic: the same seed gives the same ground truth.

All names, labs and values are fictional. Every patient is marked synthetic.
"""

import datetime as dt
import random
from dataclasses import asdict, dataclass

from evals.synthetic.catalog import BY_KEY, PANEL_BY_KEY, PANELS, reference_range

GENERATOR_VERSION = "0.1"
LAYOUTS = ("grid", "plain", "compact")
MAX_ROWS_PER_PAGE = 10
OPTIONAL_PANELS = tuple(panel.key for panel in PANELS if panel.key != "cbc")

_FIRST_NAMES = (
    "Aarav",
    "Diya",
    "Kabir",
    "Meera",
    "Rohan",
    "Ananya",
    "Vihaan",
    "Ishita",
    "Arjun",
    "Kavya",
    "Nikhil",
    "Pooja",
    "Sameer",
    "Tara",
    "Yash",
    "Lakshmi",
)
_LAST_NAMES = (
    "Fictional",
    "Sampleton",
    "Testwala",
    "Demorao",
    "Mockrishnan",
    "Placeholder",
    "Dummyjee",
)
_LABS = (
    ("Sunrise Diagnostics (Fictional)", "12 Imaginary Road, Pune"),
    ("Lotus Pathology Lab (Fictional)", "4 Sample Nagar, Chennai"),
    ("Neelkanth Health Labs (Fictional)", "88 Test Marg, Lucknow"),
)


@dataclass(frozen=True)
class Row:
    analyte_key: str
    printed_name: str
    value: str
    unit: str
    reference: str
    flag: str  # "H", "L" or ""


@dataclass(frozen=True)
class PageTruth:
    page_number: int
    panels: tuple[str, ...]
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class ReportTruth:
    report_id: str
    patient_key: str
    lab_name: str
    lab_address: str
    date: str  # ISO date
    date_printed: str  # DD/MM/YYYY, as printed
    date_ambiguous: bool  # day <= 12: also a valid MM/DD date
    layout: str
    sample_id: str
    pages: tuple[PageTruth, ...]


@dataclass(frozen=True)
class PatientTruth:
    patient_key: str
    display_name: str
    date_of_birth: str  # ISO
    sex: str
    panels: tuple[str, ...]  # panels this patient is ever tested for
    reports: tuple[ReportTruth, ...]


def _value(rng: random.Random, key: str) -> tuple[str, str]:
    analyte = BY_KEY[key]
    value = max(rng.gauss(analyte.mean, analyte.sd), analyte.mean * 0.2)
    flag = ""
    if analyte.high is not None and value > analyte.high:
        flag = "H"
    elif analyte.low is not None and value < analyte.low:
        flag = "L"
    return f"{value:.{analyte.decimals}f}", flag


def _paginate(panels: list[str]) -> list[list[str]]:
    """Whole panels per page, at most MAX_ROWS_PER_PAGE rows (a panel is never split)."""
    pages: list[list[str]] = []
    rows = 0
    for panel in panels:
        size = len(PANEL_BY_KEY[panel].analytes)
        if pages and rows + size <= MAX_ROWS_PER_PAGE:
            pages[-1].append(panel)
            rows += size
        else:
            pages.append([panel])
            rows = size
    return pages


def _report(
    rng: random.Random, patient_key: str, index: int, date: dt.date, panels: list[str]
) -> ReportTruth:
    lab_name, lab_address = _LABS[rng.randrange(len(_LABS))]
    pages = []
    for number, page_panels in enumerate(_paginate(panels), start=1):
        rows = []
        for panel in page_panels:
            for key in PANEL_BY_KEY[panel].analytes:
                analyte = BY_KEY[key]
                printed = analyte.name if rng.random() < 0.75 else rng.choice(analyte.aliases)
                value, flag = _value(rng, key)
                rows.append(Row(key, printed, value, analyte.unit, reference_range(analyte), flag))
        pages.append(PageTruth(number, tuple(page_panels), tuple(rows)))
    return ReportTruth(
        report_id=f"{patient_key}-R{index:02d}",
        patient_key=patient_key,
        lab_name=lab_name,
        lab_address=lab_address,
        date=date.isoformat(),
        date_printed=date.strftime("%d/%m/%Y"),
        date_ambiguous=date.day <= 12,
        layout=LAYOUTS[rng.randrange(len(LAYOUTS))],
        sample_id=f"SMP{rng.randrange(10**6):06d}",
        pages=tuple(pages),
    )


def generate(seed: int, patients: int) -> tuple[PatientTruth, ...]:
    """Deterministic ground truth: ``patients`` fictional patients with 3-5 reports each."""
    if patients < 2:
        raise ValueError("at least 2 patients are needed (cross-patient probes)")
    rng = random.Random(seed)
    result = []
    for number in range(1, patients + 1):
        key = f"P{number:02d}"
        name = f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}"
        birth = dt.date(rng.randrange(1950, 2000), rng.randrange(1, 13), rng.randrange(1, 29))
        own = sorted(rng.sample(OPTIONAL_PANELS, rng.randrange(2, 5)))
        dates = sorted(
            {
                dt.date(2022, 1, 1) + dt.timedelta(days=rng.randrange(0, 4 * 365))
                for _ in range(rng.randrange(3, 6))
            }
        )
        reports = []
        for index, date in enumerate(dates, start=1):
            chosen = sorted(rng.sample(own, rng.randrange(1, len(own) + 1)))
            reports.append(_report(rng, key, index, date, ["cbc", *chosen]))
        result.append(
            PatientTruth(
                key, name, birth.isoformat(), rng.choice("MF"), ("cbc", *own), tuple(reports)
            )
        )
    return tuple(result)


def split_patients(
    patient_keys: list[str], seed: int, heldout_share: float = 0.3
) -> dict[str, list[str]]:
    """Patients split into ``dev`` and ``heldout`` (deterministic, by patient, never by report)."""
    keys = sorted(patient_keys)
    random.Random(seed).shuffle(keys)
    heldout = max(1, round(len(keys) * heldout_share))
    return {"dev": sorted(keys[heldout:]), "heldout": sorted(keys[:heldout])}


def to_json(patients: tuple[PatientTruth, ...]) -> list[dict]:
    return [asdict(patient) for patient in patients]
