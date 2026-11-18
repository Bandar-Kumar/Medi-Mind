"""Lab extractor rules (``lab-rules-1``; docs/PHASE_3_PLAN.md §4). IR built in code, fictional."""

import datetime as dt
from decimal import Decimal

from app.extraction.lab.extract import extract_lab_observations
from app.extraction.reasons import Abstention, QualityNote, ReviewReason
from app.extraction.schemas import NEEDS_REVIEW, VALIDATED
from app.ingestion.ir import ReviewReason as PageFlag
from tests.fixtures.extraction_ir import (
    GRID_HEADER,
    document,
    lab_page,
    page,
    table,
    texts,
)

REF_DATE = dt.date(2026, 11, 17)


def run(doc, stored=None):
    return extract_lab_observations(doc, stored or texts(doc), reference_date=REF_DATE)


def test_grid_rows_become_grounded_validated_facts() -> None:
    doc = document(
        [
            lab_page(
                rows=[
                    ("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", ""),
                    ("Platelet Count", "2.6", "lakhs/cumm", "1.50 - 4.50", ""),
                ]
            )
        ]
    )
    out = run(doc)
    assert [o.analyte_name for o in out.observations] == ["Haemoglobin", "Platelet Count"]
    hb = out.observations[0]
    text = doc.pages[0].text
    assert text[hb.char_start : hb.char_end] == hb.source_excerpt
    assert hb.source_locator == "p1:t0:r1"
    assert hb.review_status == VALIDATED and hb.review_reasons == ()
    assert (hb.value_num, hb.unit_raw, hb.unit_norm) == (Decimal("14.2"), "gm/dL", "g/dL")
    assert (hb.ref_low, hb.ref_high, hb.ref_range_comparison) == (
        Decimal("13.0"),
        Decimal("17.0"),
        "within",
    )
    assert (hb.effective_date, hb.effective_date_source) == (dt.date(2024, 3, 25), "report")
    assert QualityNote.EFFECTIVE_DATE_FROM_REPORT.value in hb.quality_notes
    assert text[hb.date_char_start : hb.date_char_end] == "25/03/2024"
    assert out.observations[1].value_num_norm == Decimal("260000")
    assert out.report_date is not None and out.report_date.value == dt.date(2024, 3, 25)


def test_compact_layout_flag_inside_the_value_cell() -> None:
    rows = [
        ("Parameter", "Value", "Normal Range", "Unit"),
        ("RBC Count", "4.49 L", "4.50 - 5.50", "million/cumm"),
    ]
    doc = document([page(1, ["Report Date: 25/03/2024", table(rows)])])
    rbc = run(doc).observations[0]
    assert rbc.value_raw == "4.49 L"
    assert (rbc.value_num, rbc.flag_as_printed, rbc.ref_range_comparison) == (
        Decimal("4.49"),
        "L",
        "below",
    )
    assert rbc.review_status == VALIDATED


def test_printed_flag_contradicting_the_range_needs_review() -> None:
    doc = document([lab_page(rows=[("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "H")])])
    hb = run(doc).observations[0]
    assert hb.review_status == NEEDS_REVIEW
    assert hb.review_reasons == (ReviewReason.FLAG_RANGE_DISAGREEMENT.value,)


def test_historical_column_is_never_read() -> None:
    rows = [("Test", "Result", "Previous Result", "Unit"), ("Haemoglobin", "14.2", "11.0", "gm/dL")]
    out = run(document([page(1, ["Report Date: 25/03/2024", table(rows)])]))
    assert [o.value_raw for o in out.observations] == ["14.2"]
    assert all("11.0" not in o.value_raw for o in out.observations)
    assert out.abstentions[Abstention.HISTORICAL_COLUMN.value] == 1


def test_duplicated_roles_or_missing_value_column_are_unrecognized() -> None:
    side_by_side = [("Test", "Result", "Test", "Result"), ("Hb", "14.2", "MCV", "88")]
    no_value = [("Test", "Unit"), ("Hb", "gm/dL")]
    for rows in (side_by_side, no_value):
        out = run(document([page(1, [table(rows)])]))
        assert out.observations == ()
        assert out.abstentions[Abstention.TABLE_UNRECOGNIZED.value] == 1


def test_reordered_table_text_abstains_and_skips_lines_on_that_page() -> None:
    t = table([GRID_HEADER, ("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")])
    base = page(1, ["Report Date: 25/03/2024", t, "Haemoglobin: 11.0 gm/dL"])
    shuffled = base.model_copy(
        update={"text": "Report Date: 25/03/2024\nResult\tTest\nHaemoglobin: 11.0 gm/dL"}
    )
    out = run(document([shuffled]))
    assert out.observations == ()
    assert out.abstentions[Abstention.TABLE_BLOCK_NOT_FOUND.value] == 1


def test_placeholder_empty_and_section_rows() -> None:
    rows = [
        ("LIVER FUNCTION TEST", "", "", "", ""),
        ("SGOT", "Pending", "U/L", "5 - 40", ""),
        ("SGPT", "", "U/L", "7 - 56", ""),
        ("", "12", "U/L", "", ""),
        ("Alkaline Phosphatase", "92", "U/L", "44 - 147", ""),
    ]
    out = run(document([lab_page(rows=rows)]))
    assert [o.analyte_name for o in out.observations] == ["Alkaline Phosphatase"]
    assert out.abstentions == {
        Abstention.EMPTY_ANALYTE.value: 1,
        Abstention.EMPTY_VALUE.value: 1,
        Abstention.PLACEHOLDER_VALUE.value: 1,
    }


def test_unmapped_unknown_unit_missing_unit_and_implausible_need_review() -> None:
    rows = [
        ("Serum Zinc", "90", "ug/dL", "60 - 120", ""),
        ("Haemoglobin", "14.2", "furlongs", "", ""),
        ("Haemoglobin", "14.2", "", "", ""),
        ("MCV", "880", "fL", "83 - 101", ""),
    ]
    obs = run(document([lab_page(rows=rows)])).observations
    reasons = [o.review_reasons for o in obs]
    assert ReviewReason.UNMAPPED_ANALYTE.value in reasons[0]
    assert ReviewReason.UNKNOWN_UNIT.value in reasons[1]
    assert ReviewReason.MISSING_UNIT.value in reasons[2]
    assert ReviewReason.IMPLAUSIBLE_VALUE.value in reasons[3]
    assert all(o.review_status == NEEDS_REVIEW for o in obs)
    assert obs[0].analyte_raw == "Serum Zinc" and obs[0].analyte_name is None


def test_conflicting_values_in_one_document_need_review() -> None:
    rows = [
        ("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", ""),
        ("Hb", "12.1", "gm/dL", "13.0 - 17.0", "L"),
    ]
    obs = run(document([lab_page(rows=rows)])).observations
    assert all(ReviewReason.CONFLICTING_VALUE_IN_DOCUMENT.value in o.review_reasons for o in obs)
    assert len({o.source_locator for o in obs}) == 2


def test_repeated_identical_rows_keep_their_own_spans() -> None:
    rows = [
        ("Blood Urea", "32", "mg/dL", "15 - 40", ""),
        ("Blood Urea", "32", "mg/dL", "15 - 40", ""),
    ]
    obs = run(document([lab_page(rows=rows)])).observations
    assert len(obs) == 2 and obs[0].char_start != obs[1].char_start
    assert all(o.review_status == VALIDATED for o in obs)


def test_page_with_two_patients_yields_nothing() -> None:
    two = page(
        1,
        [
            "Patient Name: Asha Fictional",
            "Patient Name: Ravi Fictional",
            "Report Date: 25/03/2024",
            table([GRID_HEADER, ("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")]),
        ],
    )
    out = run(document([two]))
    assert out.observations == ()
    assert out.abstentions[Abstention.MULTIPLE_SUBJECTS.value] == 1


def test_relative_names_are_not_a_second_subject() -> None:
    one = page(
        1,
        [
            "Patient Name: Asha Fictional   Age: 30",
            "Father's Name: Ravi Fictional",
            "Report Date: 25/03/2024",
            table([GRID_HEADER, ("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")]),
        ],
    )
    assert len(run(document([one])).observations) == 1


def test_collection_date_and_time_win_over_report_date() -> None:
    p = page(
        1,
        [
            "Sample Collected On: 24/03/2024 08:15 AM",
            "Report Date: 25/03/2024",
            table([GRID_HEADER, ("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")]),
        ],
    )
    hb = run(document([p])).observations[0]
    assert (hb.effective_date, hb.effective_date_source) == (dt.date(2024, 3, 24), "collection")
    assert (hb.effective_time, hb.time_raw) == (dt.time(8, 15), "08:15 AM")


def test_two_collection_dates_are_never_chosen_between() -> None:
    p = page(
        1,
        [
            "Collected: 24/03/2024",
            "Collected: 23/03/2024",
            table([GRID_HEADER, ("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")]),
        ],
    )
    hb = run(document([p])).observations[0]
    assert hb.effective_date is None
    assert ReviewReason.CONFLICTING_DATES.value in hb.review_reasons


def test_ambiguous_and_two_digit_dates_need_review() -> None:
    for raw, reason in (("03/04/2024", "ambiguous_date"), ("25/03/24", "two_digit_year")):
        p = page(1, [f"Report Date: {raw}", table([GRID_HEADER, ("Hb", "14.2", "gm/dL", "", "")])])
        out = run(document([p]))
        assert reason in out.observations[0].review_reasons
        assert out.report_date is None  # never written to documents.document_date


def test_page_flags_send_facts_to_review() -> None:
    p = lab_page(rows=[("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")], ocr=True)
    hb = run(document([p])).observations[0]
    assert hb.review_reasons == (ReviewReason.OCR_UNVERIFIED_SOURCE.value,)
    p = lab_page(
        rows=[("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")],
        flags=[PageFlag.DOCLING_NORMALIZED_TEXT],
    )
    assert run(document([p])).observations[0].review_reasons == (
        ReviewReason.SOURCE_TEXT_NORMALIZED.value,
    )


def test_labelled_lines_need_an_exact_alias() -> None:
    p = page(
        1,
        [
            "Report Date: 25/03/2024",
            "Haemoglobin: 14.2 gm/dL (13.0 - 17.0)",
            "Serum Zinc: 90 ug/dL",
            "Page 1 of 2",
            "The haemoglobin was 14.2 on review.",
        ],
    )
    out = run(document([p]))
    assert [o.analyte_name for o in out.observations] == ["Haemoglobin"]
    hb = out.observations[0]
    assert hb.source_locator == "p1:l1"
    assert p.text[hb.char_start : hb.char_end] == "Haemoglobin: 14.2 gm/dL (13.0 - 17.0)"
    assert (hb.unit_raw, hb.ref_range_raw) == ("gm/dL", "13.0 - 17.0")
    # "Serum Zinc: 90 ug/dL" and "Page 1 of 2" fit the line shape; neither name maps.
    assert out.abstentions[Abstention.LINE_ANALYTE_UNMAPPED.value] == 2


def test_table_rows_are_not_read_again_as_lines() -> None:
    doc = document([lab_page(rows=[("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")])])
    assert len(run(doc).observations) == 1


def test_stored_text_must_equal_the_ir_text() -> None:
    doc = document([lab_page(rows=[("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")])])
    out = run(doc, {1: doc.pages[0].text + " "})
    assert out.observations == ()
    assert out.abstentions == {Abstention.PAGE_TEXT_MISMATCH.value: 1}


def test_qualitative_results() -> None:
    rows = [("Urine Glucose", "Negative", "", "", ""), ("Urine Protein", "Trace", "", "", "")]
    obs = run(document([lab_page(rows=rows)])).observations
    assert [o.value_qualitative for o in obs] == ["Negative", "Trace"]
    assert all(o.review_status == VALIDATED for o in obs)  # unitless: no missing-unit flag


def test_output_repr_has_no_document_text() -> None:
    doc = document([lab_page(rows=[("Haemoglobin", "14.2", "gm/dL", "13.0 - 17.0", "")])])
    out = run(doc)
    shown = repr(out)
    assert "Haemoglobin" not in shown and "Asha" not in shown and "14.2\t" not in shown


def test_joined_lines_are_read_segment_by_segment_only_when_they_tile_the_line() -> None:
    joined = (
        "Haemoglobin: 14.1 gm/dL (13.0 - 17.0) Fasting Blood Sugar: 75 mg/dL (70 - 100) "
        "Serum Creatinine: 1.07 mg/dL (0.70 - 1.30)"
    )
    p = page(1, ["Collected On: 07-Dec-2024", joined])
    out = run(document([p]))
    assert [o.analyte_name for o in out.observations] == [
        "Haemoglobin",
        "Fasting Blood Sugar",
        "Serum Creatinine",
    ]
    assert [o.source_locator for o in out.observations] == ["p1:l1:s0", "p1:l1:s1", "p1:l1:s2"]
    for o in out.observations:
        assert p.text[o.char_start : o.char_end] == o.source_excerpt
        assert o.value_raw in o.source_excerpt and o.source_excerpt.endswith(")")
    assert out.observations[1].ref_range_raw == "70 - 100"


def test_a_joined_line_with_one_unmapped_segment_is_skipped_entirely() -> None:
    joined = "Haemoglobin: 14.1 gm/dL (13.0 - 17.0) Serum Zinc: 90 ug/dL (60 - 120)"
    out = run(document([page(1, ["Collected On: 07-Dec-2024", joined])]))
    assert out.observations == ()
    assert out.abstentions[Abstention.LINE_ANALYTE_UNMAPPED.value] == 1


def test_a_joined_line_that_does_not_tile_is_not_split() -> None:
    joined = "Haemoglobin: 14.1 gm/dL see comment Fasting Blood Sugar: 75 mg/dL"
    out = run(document([page(1, ["Collected On: 07-Dec-2024", joined])]))
    assert out.observations == ()
