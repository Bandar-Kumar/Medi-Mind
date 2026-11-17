"""Unit tests for the rule-based document classifier (Task 2.7, ADR-036). Pure: no database.

Every text below is fictional and written for the test.
"""

import copy
from dataclasses import dataclass

import pytest

from app.ingestion import classification as cls
from app.ingestion.classification import (
    CLASSIFIER_RULES_VERSION,
    CONFLICT,
    LAB_REPORT,
    NO_TEXT,
    OTHER,
    REPLACEMENT_CHARACTER,
    UNKNOWN,
    WEAK,
    classify_pages,
)
from app.ingestion.ir import ParsedPage, ParseMethod
from app.records.repositories import PageRecord

FFFD = chr(0xFFFD)

LAB_TEXT = (
    "Sunrise Diagnostics (Fictional)\n"
    "Patient Name: Asha Fictional\n"
    "Sample ID: SMP000001\n"
    "COMPLETE BLOOD COUNT (CBC)\n"
    "Test\tResult\tUnit\tReference Range\n"
    "Haemoglobin\t12.5\tgm/dL\t13.0 - 17.0\n"
    "TLC\t6336\t/cumm\t4000 - 11000\n"
    "Fasting Blood Sugar\t103\tmg/dL\t70 - 100\n"
)
PRESCRIPTION_TEXT = (
    "Dr. Kiran Imaginary (Fictional)\nRx\n"
    "1. Tab. Fictomol 500 mg 1-0-1\n2. Tab. Imaginol 5 mg 0-0-1\n"
)
DISCHARGE_TEXT = (
    "DISCHARGE SUMMARY\nDate of Admission: 01/02/2024\nDate of Discharge: 04/02/2024\n"
    "Final Diagnosis: fictional case\n"
)
INVOICE_TEXT = "TAX INVOICE\nBill No: INV-00001\nCBC 350.00\nTotal Amount: Rs. 350.00\n"
LETTER_TEXT = "Dear Dr. Madeup,\nI am referring a fictional patient.\nYours sincerely,\n"


@dataclass(frozen=True)
class Page:
    page_number: int
    text: str
    has_tables: bool = False


def _one(text: str, has_tables: bool = False) -> cls.Classification:
    return classify_pages([Page(1, text, has_tables)])


# --- each outcome ----------------------------------------------------------------------------


def test_a_lab_report_is_classified_as_lab_report() -> None:
    result = _one(LAB_TEXT, has_tables=True)
    assert result.doc_type == LAB_REPORT
    assert {"lab.results_header", "lab.reference_range", "lab.units"} <= set(result.rule_ids)
    assert result.score == result.lab_evidence > 0


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (PRESCRIPTION_TEXT, "other.prescription"),
        (DISCHARGE_TEXT, "other.discharge"),
        (INVOICE_TEXT, "other.invoice"),
        (LETTER_TEXT, "other.letter"),
        ("Appointment Confirmation\nTests scheduled: Lipid Profile\n", "other.booking"),
    ],
)
def test_other_document_families_are_classified_as_other(text, rule) -> None:
    result = _one(text)
    assert result.doc_type == OTHER and rule in result.rule_ids
    assert result.score == result.other_evidence >= cls.OTHER_THRESHOLD


def test_weak_evidence_is_unknown_not_a_guess() -> None:
    result = _one("Lotus Pathology Lab (Fictional)\nHello\n")
    assert result.doc_type == UNKNOWN and WEAK in result.rule_ids and result.score == 0.0


def test_lab_cues_without_a_structural_cue_are_not_enough() -> None:
    # letterhead, section heading, units: no results-table header and no reference range
    text = "Lotus Pathology Lab\nHAEMATOLOGY\nHb 12 gm/dL, TLC 6000 /cumm, FBS 90 mg/dL\n"
    assert _one(text, has_tables=True).doc_type == UNKNOWN


def test_an_inline_reference_range_is_a_structural_cue() -> None:
    text = "Lab (Fictional)\nHaemoglobin : 14.7 gm/dL (13.0 - 17.0) TLC : 5466 /cumm (4000 - 11000)"
    text += " Platelet Count : 2.91 lakhs/cumm (1.50 - 4.50)"
    result = _one(text)
    assert result.doc_type == LAB_REPORT and "lab.reference_range" in result.rule_ids


def test_units_are_counted_per_occurrence_not_per_line() -> None:
    merged = "Hb 12 gm/dL TLC 6000 /cumm FBS 90 mg/dL"  # one line, as Docling may merge them
    assert "lab.units" in _one(merged).rule_ids
    assert "lab.units" not in _one("Hb 12 gm/dL TLC 6000 /cumm").rule_ids  # only two


# --- precedence and conflicting signals ------------------------------------------------------


def test_strong_evidence_on_both_sides_is_a_conflict_never_resolved() -> None:
    result = _one(DISCHARGE_TEXT + PRESCRIPTION_TEXT + LAB_TEXT, has_tables=True)
    assert result.lab_evidence >= cls.LAB_THRESHOLD and result.other_evidence >= 0.5
    assert result.doc_type == UNKNOWN and CONFLICT in result.rule_ids and result.score == 0.0


def test_lab_vocabulary_in_another_document_does_not_make_it_a_lab_report() -> None:
    text = PRESCRIPTION_TEXT + "Investigations advised: CBC, HbA1c, Lipid Profile\n"
    assert _one(text).doc_type == OTHER


def test_a_lab_bill_with_test_names_is_other() -> None:
    text = (
        "Sunrise Diagnostics\nTAX INVOICE\nSl.\tTest / Service\tQty\tAmount (Rs.)\n1\tCBC\t1\t350"
    )
    assert _one(text, has_tables=True).doc_type == OTHER


def test_the_first_matching_precedence_rule_wins() -> None:
    lab_only = _one(LAB_TEXT)
    assert lab_only.doc_type == LAB_REPORT
    weak_other = _one(LAB_TEXT + "Diagnosis: fictional\n")  # diagnosis alone is not strong
    assert weak_other.doc_type == LAB_REPORT and "other.diagnosis" in weak_other.rule_ids


# --- empty, malformed and unusual pages ------------------------------------------------------


@pytest.mark.parametrize("texts", [("",), ("   \n\t",), ("", "\n\n")])
def test_pages_without_text_are_unknown(texts) -> None:
    pages = [Page(n, t) for n, t in enumerate(texts, start=1)]
    result = classify_pages(pages)
    assert result.doc_type == UNKNOWN and result.rule_ids == (NO_TEXT,) and result.score == 0.0


@pytest.mark.parametrize(
    ("pages", "error"),
    [
        ([], ValueError),
        ("not pages", TypeError),
        ([Page(0, "x")], ValueError),
        ([Page(1, "x"), Page(1, "y")], ValueError),
        ([Page(True, "x")], ValueError),  # type: ignore[arg-type]
        ([Page(1, None)], TypeError),  # type: ignore[arg-type]
        ([Page(1, "x", None)], TypeError),  # type: ignore[arg-type]
        ([object()], ValueError),
    ],
)
def test_malformed_pages_are_rejected(pages, error) -> None:
    with pytest.raises(error):
        classify_pages(pages)


def test_error_messages_never_contain_page_text() -> None:
    secret = "Asha Fictional secret text"
    with pytest.raises(ValueError) as caught:
        classify_pages([Page(1, secret), Page(1, secret)])
    assert "Asha" not in str(caught.value) and "secret" not in str(caught.value)


def test_a_replacement_character_is_flagged_and_never_matches() -> None:
    result = _one(f"{FFFD}{FFFD}{FFFD}\nunreadable")
    assert result.doc_type == UNKNOWN and REPLACEMENT_CHARACTER in result.rule_ids
    lab = _one(LAB_TEXT.replace("Unit", f"Un{FFFD}t"))
    assert lab.doc_type == LAB_REPORT and REPLACEMENT_CHARACTER in lab.rule_ids


def test_pages_are_read_in_page_order_and_across_pages() -> None:
    split = [Page(2, "Haemoglobin\t12\tgm/dL\t13 - 17\nTLC 6000 /cumm\nFBS 90 mg/dL"),
             Page(1, "Test\tResult\tUnit\tReference Range", True)]  # fmt: skip
    assert classify_pages(split).doc_type == LAB_REPORT


# --- determinism, bounds, version, no mutation, no text in the result -------------------------


@pytest.mark.parametrize(
    "text", [LAB_TEXT, PRESCRIPTION_TEXT, DISCHARGE_TEXT, INVOICE_TEXT, LETTER_TEXT, "", "x"]
)
def test_results_are_deterministic_bounded_and_versioned(text) -> None:
    first, second = _one(text, True), _one(text, True)
    assert first == second
    for value in (first.score, first.lab_evidence, first.other_evidence):
        assert 0.0 <= value <= 1.0 and round(value, 3) == value
    assert first.rules_version == CLASSIFIER_RULES_VERSION
    assert list(first.rule_ids) == sorted(set(first.rule_ids))


def test_classification_never_changes_the_pages() -> None:
    pages = [Page(1, LAB_TEXT, True), Page(2, DISCHARGE_TEXT)]
    before = copy.deepcopy(pages)
    classify_pages(pages)
    assert pages == before


def test_the_result_holds_rule_ids_not_text() -> None:
    result = _one(LAB_TEXT, True)
    known = {rule.rule_id for rule in cls.RULES} | {NO_TEXT, CONFLICT, WEAK, REPLACEMENT_CHARACTER}
    assert set(result.rule_ids) <= known
    assert "Asha" not in repr(result) and "Haemoglobin" not in repr(result)


def test_stored_pages_and_ir_pages_give_the_same_result() -> None:
    record = PageRecord(page_number=1, text=LAB_TEXT, parse_method="native", has_tables=False)
    ir_page = ParsedPage(
        page_number=1, text=LAB_TEXT, parse_method=ParseMethod.NATIVE, native_char_count=10
    )
    assert classify_pages([record]) == classify_pages([ir_page]) == _one(LAB_TEXT)


def test_rule_ids_are_unique_and_weights_positive() -> None:
    ids = [rule.rule_id for rule in cls.RULES]
    assert len(ids) == len(set(ids))
    assert all(rule.weight > 0 and rule.side in (LAB_REPORT, OTHER) for rule in cls.RULES)
    assert {rule.rule_id for rule in cls.RULES if rule.structural} == {
        "lab.results_header",
        "lab.reference_range",
    }
