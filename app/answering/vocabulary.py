"""Patient vocabulary for test names in questions (Phase 5 remediation; ADR-041). Pure, versioned.

The router first uses the extraction alias table (``aliases_v1.csv``: names *printed on reports*).
Patients also use everyday names ("A1c", "fasting sugar", "good cholesterol"). This table adds
those, for **questions only**: extraction and its ``reference_data_version`` are unchanged.

- ``LAY_TERMS``: an unambiguous everyday name -> one canonical analyte.
- ``AMBIGUOUS_TERMS``: a name that can mean several tests ("sugar", "thyroid", "bilirubin"). The
  router keeps every candidate and labels the answer, so each result is shown under its own test
  name; it never picks one silently.

Every entry is a deliberate mapping to a canonical analyte of the reference data (checked by a
test). Misspellings are not added: an unrecognised name is reported as unrecognised, not guessed.
"""

from typing import Final

VOCABULARY_VERSION: Final = "vocab-1"

LAY_TERMS: Final[dict[str, str]] = {
    # haemoglobin and blood counts
    "hgb": "Haemoglobin",
    "haemoglobin level": "Haemoglobin",
    "hemoglobin level": "Haemoglobin",
    "wbc": "Total Leucocyte Count",
    "white cell count": "Total Leucocyte Count",
    "white blood cell count": "Total Leucocyte Count",
    "white blood cells": "Total Leucocyte Count",
    "platelets": "Platelet Count",
    "rbc": "RBC Count",
    "red cell count": "RBC Count",
    "red blood cell count": "RBC Count",
    "haematocrit": "Packed Cell Volume",
    "hematocrit": "Packed Cell Volume",
    "mean corpuscular volume": "MCV",
    # diabetes
    "a1c": "HbA1c",
    "hb a1c": "HbA1c",
    "haemoglobin a1c": "HbA1c",
    "hemoglobin a1c": "HbA1c",
    "glycated haemoglobin": "HbA1c",
    "glycated hemoglobin": "HbA1c",
    "glycosylated haemoglobin": "HbA1c",
    "glycosylated hemoglobin": "HbA1c",
    "fasting sugar": "Fasting Blood Sugar",
    "fasting glucose": "Fasting Blood Sugar",
    "fasting blood glucose": "Fasting Blood Sugar",
    "fasting plasma glucose": "Fasting Blood Sugar",
    "fbg": "Fasting Blood Sugar",
    "fpg": "Fasting Blood Sugar",
    "pp sugar": "Post Prandial Blood Sugar",
    "post meal sugar": "Post Prandial Blood Sugar",
    "after meal sugar": "Post Prandial Blood Sugar",
    "postprandial glucose": "Post Prandial Blood Sugar",
    "post prandial glucose": "Post Prandial Blood Sugar",
    "ppbg": "Post Prandial Blood Sugar",
    # lipids
    "cholesterol": "Total Cholesterol",
    "serum cholesterol": "Total Cholesterol",
    "good cholesterol": "HDL Cholesterol",
    "bad cholesterol": "LDL Cholesterol",
    "tg": "Triglycerides",
    # kidney
    "creatinine": "Serum Creatinine",
    "s. creatinine": "Serum Creatinine",
    "urea": "Blood Urea",
    "bun": "Blood Urea",
    "gfr": "eGFR",
    # liver
    "ast": "SGOT",
    "alt": "SGPT",
    "alk phos": "Alkaline Phosphatase",
    # thyroid
    "thyrotropin": "TSH",
    "t3": "Total T3",
    "t4": "Total T4",
    # vitamins
    "vitamin d": "Vitamin D (25-OH)",
    "vit d": "Vitamin D (25-OH)",
    "vitamin d3": "Vitamin D (25-OH)",
    "b12": "Vitamin B12",
    "vit b12": "Vitamin B12",
    # urine and serology
    "urine sugar": "Urine Glucose",
    "hepatitis b surface antigen": "HBsAg",
}

AMBIGUOUS_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "sugar": ("Fasting Blood Sugar", "Post Prandial Blood Sugar"),
    "blood sugar": ("Fasting Blood Sugar", "Post Prandial Blood Sugar"),
    "glucose": ("Fasting Blood Sugar", "Post Prandial Blood Sugar"),
    "blood glucose": ("Fasting Blood Sugar", "Post Prandial Blood Sugar"),
    "thyroid": ("TSH", "Total T3", "Total T4"),
    "bilirubin": ("Total Bilirubin", "Direct Bilirubin"),
    "liver enzymes": ("SGOT", "SGPT"),
}


def question_vocabulary(
    aliases: dict[str, str],
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """``(single, ambiguous)``: reference aliases plus lay terms (lay terms never override)."""
    single = dict(LAY_TERMS)
    single.update(aliases)  # a printed alias wins over a lay term with the same spelling
    ambiguous = {k: v for k, v in AMBIGUOUS_TERMS.items() if k not in single}
    return single, ambiguous
