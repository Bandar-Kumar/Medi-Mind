"""Normalizers and reference data (docs/PHASE_3_PLAN.md §7). Pure; fictional values only."""

import datetime as dt
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from app.extraction.normalize.analytes import map_analyte
from app.extraction.normalize.dates import DAY, MONTH, parse_date, parse_time
from app.extraction.normalize.numbers import is_placeholder, parse_number, parse_value
from app.extraction.normalize.ranges import (
    ABOVE,
    BELOW,
    WITHIN,
    compare_to_range,
    parse_range,
)
from app.extraction.normalize.units import normalize_unit
from app.extraction.reasons import QualityNote, ReviewReason
from app.extraction.reference import (
    REFERENCE_DIR,
    ReferenceDataError,
    compute_version,
    load_reference,
    load_reference_from,
)

REF = load_reference()
TODAY = dt.date(2026, 11, 17)  # a fixed reference date: the clock is never read


# --- numbers and values ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14.2", Decimal("14.2")),
        ("1,50,000", Decimal("150000")),
        ("150,000", Decimal("150000")),
        ("1,500", Decimal("1500")),
        (".5", Decimal(".5")),
        ("1,5", None),
        ("1,50,00", None),
        ("12a", None),
    ],
)
def test_parse_number(raw: str, expected: Decimal | None) -> None:
    assert parse_number(raw) == expected


def test_decimal_keeps_printed_precision() -> None:
    assert str(parse_value("14.20").value_num) == "14.20"


@pytest.mark.parametrize(
    ("raw", "num", "cmp", "qual", "flag", "reason"),
    [
        ("14.2", "14.2", None, None, None, None),
        ("< 0.01", "0.01", "<", None, None, None),
        (">=90", "90", ">=", None, None, None),
        ("14.2 H", "14.2", None, None, "H", None),
        ("4.49 L", "4.49", None, None, "L", None),
        ("Negative", None, None, "Negative", None, None),
        ("NON-REACTIVE", None, None, "Non-reactive", None, None),
        ("Positive H", None, None, "Positive", "H", None),
        ("Trace", None, None, "Trace", None, None),
        ("14.2 mg", None, None, None, None, ReviewReason.UNPARSED_VALUE),
        ("1,5", None, None, None, None, ReviewReason.UNPARSED_VALUE),
        ("Borderline", None, None, None, None, ReviewReason.UNPARSED_VALUE),
    ],
)
def test_parse_value(raw, num, cmp, qual, flag, reason) -> None:
    parsed = parse_value(raw)
    assert parsed.value_num == (Decimal(num) if num else None)
    assert (parsed.comparator, parsed.qualitative, parsed.flag, parsed.reason) == (
        cmp,
        qual,
        flag,
        reason,
    )


def test_negative_is_never_turned_positive() -> None:
    assert parse_value("Negative").qualitative == "Negative"
    assert parse_value("Not detected").qualitative == "Not detected"


@pytest.mark.parametrize("raw", ["--", "NA", "Pending", "see note", " - "])
def test_placeholders(raw: str) -> None:
    assert is_placeholder(raw)


# --- dates -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value", "precision", "reasons"),
    [
        ("25/03/2024", dt.date(2024, 3, 25), DAY, ()),
        ("25-03-2024", dt.date(2024, 3, 25), DAY, ()),
        ("25.03.2024", dt.date(2024, 3, 25), DAY, ()),
        ("03/04/2024", dt.date(2024, 4, 3), DAY, (ReviewReason.AMBIGUOUS_DATE,)),
        ("05/05/2024", dt.date(2024, 5, 5), DAY, ()),  # same either way: not ambiguous
        ("12-Mar-2024", dt.date(2024, 3, 12), DAY, ()),
        ("12 March 2024", dt.date(2024, 3, 12), DAY, ()),
        ("2024-03-12", dt.date(2024, 3, 12), DAY, ()),
        ("Mar 2024", dt.date(2024, 3, 1), MONTH, ()),
        ("03/2024", dt.date(2024, 3, 1), MONTH, ()),
        ("12/03/24", None, None, (ReviewReason.TWO_DIGIT_YEAR,)),
        ("12 Mar", None, None, (ReviewReason.UNPARSED_DATE,)),
        ("31/02/2024", None, None, (ReviewReason.UNPARSED_DATE,)),
        ("15/01/1890", dt.date(1890, 1, 15), DAY, (ReviewReason.IMPLAUSIBLE_DATE,)),
        ("18/11/2026", dt.date(2026, 11, 18), DAY, (ReviewReason.IMPLAUSIBLE_DATE,)),
        ("17/11/2026", dt.date(2026, 11, 17), DAY, ()),
    ],
)
def test_parse_date(raw, value, precision, reasons) -> None:
    parsed = parse_date(raw, TODAY)
    assert (parsed.value, parsed.precision, parsed.reasons) == (value, precision, reasons)


def test_future_check_uses_only_the_reference_date() -> None:
    assert parse_date("18/11/2026", dt.date(2027, 1, 1)).reasons == ()
    assert parse_date("18/11/2026", dt.date(2026, 11, 17)).reasons == (
        ReviewReason.IMPLAUSIBLE_DATE,
    )


def test_extraction_code_never_reads_the_clock() -> None:
    root = Path("app/extraction")
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("today(", ".now(", "utcnow(", "time.time(", "localtime("):
            assert forbidden not in source, f"{path} reads the clock ({forbidden})"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10:30 AM", dt.time(10, 30)),
        ("12:05 am", dt.time(0, 5)),
        ("10.30 pm", dt.time(22, 30)),
        ("22:15", dt.time(22, 15)),
        ("25:00", None),
        ("13:00 PM", None),
    ],
)
def test_parse_time(raw: str, expected: dt.time | None) -> None:
    assert parse_time(raw) == expected


# --- units -------------------------------------------------------------------------------------


def _norm(unit: str | None, value: str | None, analyte: str | None):
    return normalize_unit(
        unit,
        Decimal(value) if value is not None else None,
        REF.analytes[analyte] if analyte else None,
        REF,
    )


def test_spelling_only() -> None:
    result = _norm("gm/dL", "13.2", "Haemoglobin")
    assert (result.unit_norm, result.value_num_norm) == ("g/dL", Decimal("13.2"))


def test_lakh_scale_is_exact() -> None:
    result = _norm("lakhs/cumm", "2.6", "Platelet Count")
    assert result.unit_norm == "/µL" and result.value_num_norm == Decimal("260000")


def test_analyte_specific_conversion() -> None:
    result = _norm("mmol/L", "5.5", "Fasting Blood Sugar")
    assert result.unit_norm == "mg/dL"
    assert result.value_num_norm == Decimal("5.5") * Decimal("18.0156")


def test_no_rule_keeps_own_canonical_unit_with_a_note() -> None:
    result = _norm("mmol/L", "5.5", "HbA1c")
    assert result.unit_norm == "mmol/L" and result.value_num_norm == Decimal("5.5")
    assert result.notes == (QualityNote.UNIT_NOT_CONVERTIBLE,)


def test_unknown_and_missing_units_need_review() -> None:
    assert _norm("furlongs", "1", "Haemoglobin").reasons == (ReviewReason.UNKNOWN_UNIT,)
    assert _norm(None, "13.2", "Haemoglobin").reasons == (ReviewReason.MISSING_UNIT,)
    assert _norm(None, None, "Urine Glucose").reasons == ()


def test_micro_sign_variants_are_one_unit() -> None:
    assert _norm("µIU/mL", "2.1", "TSH").unit_norm == _norm("uIU/mL", "2.1", "TSH").unit_norm


# --- ranges and the derived comparison ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "low", "high", "parsed"),
    [
        ("13.0 - 17.0", "13.0", "17.0", True),
        ("13.0-17.0", "13.0", "17.0", True),
        ("4000 to 11000", "4000", "11000", True),
        ("< 200", None, "200", True),
        ("Up to 40", None, "40", True),
        ("> 40", "40", None, True),
        ("13.0 - 17.0 gm/dL", "13.0", "17.0", True),
        ("13.0 - 17.0 mmol/L", None, None, False),  # a unit other than the row's
        ("Desirable: <200; Borderline: 200-239", None, None, False),
        ("M: 13-17 F: 12-15", None, None, False),
        ("17 - 13", None, None, False),
    ],
)
def test_parse_range(raw, low, high, parsed) -> None:
    result = parse_range(raw, unit_key="gm/dl")
    assert (result.low, result.high, result.parsed) == (
        Decimal(low) if low else None,
        Decimal(high) if high else None,
        parsed,
    )


def test_missing_range_stays_missing() -> None:
    assert parse_range(None, unit_key=None).parsed is False
    assert compare_to_range(Decimal("5"), None, parse_range(None, unit_key=None)) is None


@pytest.mark.parametrize(
    ("value", "cmp", "range_raw", "expected"),
    [
        ("12.0", None, "13.0 - 17.0", BELOW),
        ("13.0", None, "13.0 - 17.0", WITHIN),
        ("17.5", None, "13.0 - 17.0", ABOVE),
        ("199", None, "< 200", WITHIN),
        ("200", None, "< 200", ABOVE),  # an exclusive printed bound
        ("0.01", "<", "0 - 1", None),  # the interval straddles the low bound
        ("0.5", "<", "1 - 2", BELOW),
        ("95", None, "> 90", WITHIN),
        ("90", None, "> 90", BELOW),
    ],
)
def test_compare_to_printed_range(value, cmp, range_raw, expected) -> None:
    printed = parse_range(range_raw, unit_key=None)
    assert compare_to_range(Decimal(value), cmp, printed) == expected


# --- analytes and reference data ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("Haemoglobin", "Haemoglobin"),
        ("HEMOGLOBIN:", "Haemoglobin"),
        ("Hb", "Haemoglobin"),
        ("Glycosylated  Haemoglobin", "HbA1c"),
        ("Vitamin D (25-OH)", "Vitamin D (25-OH)"),
        ("Direct Bilirubin", "Direct Bilirubin"),
        ("Total Bilirubin", "Total Bilirubin"),
        ("Bilirubin", None),  # no substring or fuzzy matching
        ("Haemoglobn", None),
    ],
)
def test_exact_alias_mapping(raw: str, name: str | None) -> None:
    analyte = map_analyte(raw, REF)
    assert (analyte.canonical_name if analyte else None) == name


def test_reference_version_changes_with_content(tmp_path: Path) -> None:
    copy = tmp_path / "ref"
    shutil.copytree(REFERENCE_DIR, copy)
    assert compute_version(copy) == REF.version
    with (copy / "aliases_v1.csv").open("a", encoding="utf-8") as handle:
        handle.write("haemoglobin level,Haemoglobin\n")
    assert compute_version(copy) != REF.version


def test_reference_rejects_a_conversion_without_source(tmp_path: Path) -> None:
    copy = tmp_path / "ref"
    shutil.copytree(REFERENCE_DIR, copy)
    with (copy / "unit_conversions_v1.csv").open("a", encoding="utf-8") as handle:
        handle.write("Haemoglobin,mg/dL,g/dL,0.001,0,\n")
    with pytest.raises(ReferenceDataError):
        load_reference_from(copy)


def test_reference_rejects_an_alias_for_two_analytes(tmp_path: Path) -> None:
    copy = tmp_path / "ref"
    shutil.copytree(REFERENCE_DIR, copy)
    with (copy / "aliases_v1.csv").open("a", encoding="utf-8") as handle:
        handle.write("hb,MCV\n")
    with pytest.raises(ReferenceDataError):
        load_reference_from(copy)


def test_every_numeric_analyte_has_sanity_bounds_and_a_unit() -> None:
    for analyte in REF.analytes.values():
        if analyte.unitless:
            assert analyte.default_unit == ""
        else:
            assert analyte.default_unit and analyte.plausible_max is not None
            assert analyte.default_unit in REF.unit_synonyms.values()
