"""Common Indian lab analytes for synthetic reports (ADR-025: Indian names and unit spellings).

Values are fictional test data generated around plausible means; reference ranges are typical
printed ranges used only to render realistic synthetic reports. MediMind never applies its own
ranges to a patient (ARCHITECTURE §8); these only shape the fictional documents.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Analyte:
    key: str
    name: str  # the name most layouts print
    aliases: tuple[str, ...]  # other names a report or a question may use
    unit: str  # printed unit (Indian spellings)
    low: float | None  # printed reference range; None = open-ended
    high: float | None
    mean: float
    sd: float
    decimals: int
    panel: str


@dataclass(frozen=True)
class Panel:
    key: str
    title: str  # printed panel heading
    analytes: tuple[str, ...]


ANALYTES: tuple[Analyte, ...] = (
    # Complete blood count
    Analyte("hb", "Haemoglobin", ("Hb", "Hemoglobin"), "gm/dL", 13.0, 17.0, 14.2, 1.4, 1, "cbc"),
    Analyte(
        "tlc",
        "Total Leucocyte Count",
        ("TLC", "WBC Count"),
        "/cumm",
        4000,
        11000,
        7200,
        1800,
        0,
        "cbc",
    ),
    Analyte("plt", "Platelet Count", ("Platelets",), "lakhs/cumm", 1.5, 4.5, 2.6, 0.6, 2, "cbc"),
    Analyte(
        "rbc", "RBC Count", ("Red Blood Cell Count",), "million/cumm", 4.5, 5.5, 4.9, 0.4, 2, "cbc"
    ),
    Analyte("pcv", "Packed Cell Volume", ("PCV", "Haematocrit"), "%", 40, 50, 43, 3.5, 1, "cbc"),
    Analyte("mcv", "MCV", ("Mean Corpuscular Volume",), "fL", 83, 101, 88, 5, 1, "cbc"),
    # Diabetes
    Analyte(
        "fbs",
        "Fasting Blood Sugar",
        ("FBS", "Fasting Glucose"),
        "mg/dL",
        70,
        100,
        98,
        18,
        0,
        "diabetes",
    ),
    Analyte(
        "ppbs", "Post Prandial Blood Sugar", ("PPBS",), "mg/dL", 70, 140, 132, 30, 0, "diabetes"
    ),
    Analyte(
        "hba1c",
        "HbA1c",
        ("Glycosylated Haemoglobin", "Glycated Hemoglobin"),
        "%",
        4.0,
        5.6,
        5.9,
        0.9,
        1,
        "diabetes",
    ),
    # Lipid profile
    Analyte(
        "chol", "Total Cholesterol", ("Serum Cholesterol",), "mg/dL", None, 200, 192, 32, 0, "lipid"
    ),
    Analyte("tg", "Triglycerides", ("TG",), "mg/dL", None, 150, 148, 45, 0, "lipid"),
    Analyte("hdl", "HDL Cholesterol", ("HDL",), "mg/dL", 40, None, 46, 8, 0, "lipid"),
    Analyte("ldl", "LDL Cholesterol", ("LDL",), "mg/dL", None, 100, 112, 28, 0, "lipid"),
    # Kidney function
    Analyte("creat", "Serum Creatinine", ("Creatinine",), "mg/dL", 0.7, 1.3, 0.95, 0.2, 2, "kft"),
    Analyte("urea", "Blood Urea", ("Urea",), "mg/dL", 15, 40, 27, 7, 0, "kft"),
    Analyte("uric", "Uric Acid", ("Serum Uric Acid",), "mg/dL", 3.5, 7.2, 5.6, 1.2, 1, "kft"),
    Analyte("egfr", "eGFR", ("Estimated GFR",), "mL/min/1.73m²", 90, None, 94, 14, 0, "kft"),
    # Liver function
    Analyte("sgot", "SGOT", ("AST", "Aspartate Aminotransferase"), "U/L", 5, 40, 29, 9, 0, "lft"),
    Analyte("sgpt", "SGPT", ("ALT", "Alanine Aminotransferase"), "U/L", 7, 56, 33, 12, 0, "lft"),
    Analyte(
        "tbil", "Total Bilirubin", ("Bilirubin Total",), "mg/dL", 0.2, 1.2, 0.7, 0.25, 2, "lft"
    ),
    Analyte("alp", "Alkaline Phosphatase", ("ALP",), "U/L", 44, 147, 92, 25, 0, "lft"),
    # Thyroid
    Analyte(
        "tsh", "TSH", ("Thyroid Stimulating Hormone",), "µIU/mL", 0.4, 4.5, 2.6, 1.3, 2, "thyroid"
    ),
    Analyte("t3", "Total T3", ("T3",), "ng/dL", 80, 200, 128, 24, 0, "thyroid"),
    Analyte("t4", "Total T4", ("T4",), "µg/dL", 5.0, 12.0, 8.1, 1.5, 1, "thyroid"),
    # Vitamins
    Analyte(
        "vitd",
        "Vitamin D (25-OH)",
        ("25-OH Vitamin D", "Vitamin D"),
        "ng/mL",
        30,
        100,
        24,
        10,
        1,
        "vitamins",
    ),
    Analyte("b12", "Vitamin B12", ("Cobalamin",), "pg/mL", 200, 900, 380, 140, 0, "vitamins"),
)

PANELS: tuple[Panel, ...] = (
    Panel("cbc", "COMPLETE BLOOD COUNT (CBC)", ("hb", "tlc", "plt", "rbc", "pcv", "mcv")),
    Panel("diabetes", "DIABETES PROFILE", ("fbs", "ppbs", "hba1c")),
    Panel("lipid", "LIPID PROFILE", ("chol", "tg", "hdl", "ldl")),
    Panel("kft", "KIDNEY FUNCTION TEST (KFT)", ("creat", "urea", "uric", "egfr")),
    Panel("lft", "LIVER FUNCTION TEST (LFT)", ("sgot", "sgpt", "tbil", "alp")),
    Panel("thyroid", "THYROID PROFILE", ("tsh", "t3", "t4")),
    Panel("vitamins", "VITAMIN PROFILE", ("vitd", "b12")),
)

BY_KEY = {analyte.key: analyte for analyte in ANALYTES}
PANEL_BY_KEY = {panel.key: panel for panel in PANELS}


def reference_range(analyte: Analyte) -> str:
    """The printed reference range, as an Indian report would show it."""
    fmt = f"{{:.{analyte.decimals}f}}"
    if analyte.low is not None and analyte.high is not None:
        return f"{fmt.format(analyte.low)} - {fmt.format(analyte.high)}"
    if analyte.high is not None:
        return f"< {fmt.format(analyte.high)}"
    assert analyte.low is not None
    return f"> {fmt.format(analyte.low)}"
