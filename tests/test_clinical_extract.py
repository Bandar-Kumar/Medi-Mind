"""Phase 6 pure extraction: classification, segmentation, items, allergies, events, guards.

All text is fictional (ADR-005). docs/PHASE_6_PLAN.md §3, §6.
"""

import datetime as dt
from decimal import Decimal

import pytest

from app.clinical import allergies as al
from app.clinical import medications as md
from app.clinical import segments as sg
from app.clinical.classify import classify_clinical
from app.clinical.extract import extract_clinical_facts
from app.clinical.lexicon import LexiconError, NameMatch, load_lexicon, parse_lexicon
from app.clinical.schemas import MedicationDraft
from tests.fixtures.extraction_ir import document, page, texts

REF = dt.date(2025, 1, 1)
PATIENT = "Patient Name: Asha Fictional   Age/Sex: 40 Y / F"


def _extract(clinical_type: str, *pages_text: str, ocr: bool = False):
    doc = document([page(n, [t], ocr=ocr) for n, t in enumerate(pages_text, start=1)])
    return extract_clinical_facts(doc, texts(doc), clinical_type, reference_date=REF)


def _page_text(text: str):
    return [page(1, [text])]


# --- classification ---------------------------------------------------------------------------

CLASSIFY_CASES = [
    ("Dr. A Fictional, MD\nDate: 15/03/2024\nRx\n1. Tab. Fictomol 500 mg 1-0-1", "prescription"),
    (
        "Lotus Care Hospital (Fictional) DISCHARGE SUMMARY\nDate of Admission: 14/03/2024",
        "discharge_summary",
    ),
    (
        "OPD CONSULTATION\nChief Complaints: fever\nRx:\n1. Tab. Fictomol 500 mg BD",
        "consultation_note",
    ),
    ("MEDICAL HISTORY RECORD\nPast Medical History:\nAsthma (2010)", "history_record"),
    ("ALLERGY RECORD\nAllergen\tReaction\tSeverity\tStatus", "allergy_record"),
    ("DEPARTMENT OF RADIOLOGY\nStudy: Ultrasound abdomen\nImpression: normal", "imaging_report"),
]


@pytest.mark.parametrize(("text", "expected"), CLASSIFY_CASES)
def test_each_supported_type_is_recognised(text, expected) -> None:
    result = classify_clinical(_page_text(text))
    assert result.clinical_type == expected
    assert result.abstention is None
    assert all("Fictional" not in rule for rule in result.rule_ids)  # rule ids only, no text


@pytest.mark.parametrize(
    ("text", "abstention"),
    [
        ("TAX INVOICE\nBill No: 1\nTotal Amount: Rs. 500", "unsupported_type"),
        ("Some unlabelled fictional note without any heading", "unclassified"),
        ("   ", "no_text"),
        # two titles, no margin: never silently resolved
        ("DISCHARGE SUMMARY\nALLERGY RECORD", "ambiguous_type"),
    ],
)
def test_uncertain_or_unsupported_documents_abstain(text, abstention) -> None:
    result = classify_clinical(_page_text(text))
    assert result.clinical_type is None
    assert result.abstention == abstention


def test_classification_refuses_malformed_input() -> None:
    with pytest.raises(ValueError):
        classify_clinical([])
    with pytest.raises(ValueError):
        classify_clinical("text")  # type: ignore[arg-type]


# --- segmentation -----------------------------------------------------------------------------


def test_labels_are_found_anywhere_in_a_merged_line() -> None:
    text = (
        "Date: 15/03/2024 Patient Name: X Y Diagnosis: Viral fever Follow-up: review after 5 days."
    )
    found = {s.key: s.value(text) for s in sg.segment_page(1, text)}
    assert found[sg.DATE] == "15/03/2024"
    assert found[sg.DIAGNOSIS] == "Viral fever"
    assert found[sg.FOLLOW_UP] == "review after 5 days."


def test_list_continuation_needs_an_item_marker_and_stops_at_a_label() -> None:
    text = (
        "Rx\n1. Tab. Fictomol 5 mg OD Follow-up: review.\nUnrelated line\n"
        "2. Tab. Mockcillin 5 mg OD"
    )
    segments = [s for s in sg.segment_page(1, text) if s.key == sg.MEDICATIONS]
    assert [s.value(text) for s in segments] == ["1. Tab. Fictomol 5 mg OD"]


def test_a_heading_label_alone_opens_a_block_of_unmarked_items() -> None:
    # Docling may drop list bullets; under a heading the plain lines are items.
    text = (
        "Past Medical History:\nAsthma since 2008\nType 2 diabetes mellitus since 2011\n"
        "Surgical History: Hernia repair (2001)"
    )
    values = [s.value(text) for s in sg.segment_page(1, text) if s.key == sg.PAST_HISTORY]
    assert values == ["", "Asthma since 2008", "Type 2 diabetes mellitus since 2011"]


def test_an_unmarked_history_line_with_a_colon_is_not_an_item() -> None:
    text = "Past Medical History:\nRemarks: none\n"
    values = [s.value(text) for s in sg.segment_page(1, text) if s.key == sg.PAST_HISTORY]
    assert values == [""]


def test_item_markers_match_at_a_position_not_only_at_the_string_start() -> None:
    # Regression: "^" never matches at pos > 0 in Pattern.match(text, pos).
    text = "Medicines:\n- Tab. Fictomol 5 mg OD"
    assert [s.continuation for s in sg.segment_page(1, text) if s.key == sg.MEDICATIONS] == [
        False,
        True,
    ]


# --- medication items ---------------------------------------------------------------------------


def _item(text: str) -> md.ParsedItem:
    (span,) = md.item_spans(text, 0, len(text))
    item = md.parse_item(text, *span)
    assert item is not None
    return item


def test_every_part_keeps_its_exact_span() -> None:
    text = "Tab. Fictomol 500 mg 1-0-1 x 5 days after food"
    item = _item(text)
    for part, raw in (
        (item.name, "Fictomol"),
        (item.strength, "500 mg"),
        (item.frequency, "1-0-1"),
        (item.duration, "x 5 days"),
        (item.instructions, "after food"),
    ):
        assert part is not None and part.text == raw == text[part.start : part.end]
    assert item.route is None and item.dose is None and not item.leftover


def test_merged_items_are_split_at_each_dosage_form() -> None:
    text = "1. Cap. Pretendipine 10 mg 1-0-1 x 5 days 2. Tab. Placebozide 5 mg OD for 7 days"
    spans = md.item_spans(text, 0, len(text))
    assert [text[s:e] for s, e in spans] == [
        "Cap. Pretendipine 10 mg 1-0-1 x 5 days",
        "Tab. Placebozide 5 mg OD for 7 days",
    ]


def test_a_form_glued_to_the_name_is_still_an_item() -> None:
    assert _item("Tab.Mockcillin 500 mg BD").name.text == "Mockcillin"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1-0-1", (2, False)),
        ("1-1-1", (3, False)),
        ("OD", (1, False)),
        ("BID", (2, False)),
        ("TDS", (3, False)),
        ("QID", (4, False)),
        ("HS", (1, False)),
        ("SOS", (None, True)),
        ("twice daily", (2, False)),
    ],
)
def test_frequency_normalization(raw, expected) -> None:
    assert md.frequency(raw) == expected


def test_normalization_never_invents_or_converts() -> None:
    assert md.duration("x 1 month") == (1, "month")  # a month is never turned into days
    assert md.strength("500 mg") == (Decimal("500"), "mg")
    assert md.strength("125 mg/5 ml") == (Decimal("125"), "mg/5ml")
    assert md.route("IV") == "intravenous"
    assert md.dosage_form("Cap.") == "capsule"
    item = _item("Tab. Fictomol 500 mg")
    assert item.frequency is None and item.duration is None  # missing stays missing


# --- lexicon ------------------------------------------------------------------------------------


def test_lexicon_matches_exactly_and_flags_ambiguous_brands() -> None:
    lexicon = load_lexicon()
    assert lexicon.lookup("Fictomol").match is NameMatch.GENERIC_EXACT
    assert lexicon.lookup("FEVEREX").generic == "fictomol"
    assert lexicon.lookup("Combiflex").ambiguous and lexicon.lookup("Combiflex").generic is None
    assert lexicon.lookup("Fictomoll").generic is None  # no fuzzy correction
    assert lexicon.version.startswith("lex-")


@pytest.mark.parametrize(
    "data",
    [
        b"name,generic\nx,x\n",
        b"name,generic,kind\nX,x,generic\n",
        b"name,generic,kind\nbrandy,ghost,brand\n",
        b"name,generic,kind\nx,y,generic\ny,y,generic\n",
        b"name,generic,kind\nx,x,other\n",
    ],
)
def test_a_malformed_lexicon_is_refused(data) -> None:
    with pytest.raises(LexiconError):
        parse_lexicon(data)


# --- allergies ----------------------------------------------------------------------------------


def test_allergy_status_words() -> None:
    assert al.is_none_known("No known drug allergies")
    assert al.is_none_known("NKDA")
    assert al.is_unknown("Not recorded") and not al.is_none_known("Not recorded")
    text = "?Sulfonamides (hives) - moderate"
    item = al.parse_item(text, 0, len(text))
    assert item is not None
    assert (item.substance.text, item.reaction.text, item.status) == (
        "Sulfonamides",
        "hives",
        "suspected",
    )
    assert item.severity is not None and item.severity.text == "moderate"


def test_labelled_allergy_item() -> None:
    text = "Latex - Reaction: itching - Severity: severe - Status: confirmed"
    item = al.parse_item(text, 0, len(text))
    assert item is not None
    assert (item.substance.text, item.reaction.text, item.severity.text, item.status) == (
        "Latex",
        "itching",
        "severe",
        "confirmed",
    )


# --- documents end to end -----------------------------------------------------------------------

PRESCRIPTION = (
    "Dr. Kiran Imaginary (Fictional), MBBS, MD\nSunbeam Family Clinic (Fictional)\n"
    f"Date: 15/03/2024\n{PATIENT}\nRx\n"
    "1. Tab. Fictomol 500 mg 1-0-1 x 5 days 2. Cap. Combiflex 250 mg BD 3. Tab. Zorbaxin 10 mg OD\n"
    "Stop: Tab. Placebozide 20 mg\nFollow-up: review after 2 weeks.\nSignature"
)


def test_a_prescription_gives_medications_a_consultation_and_a_follow_up() -> None:
    out = _extract("prescription", PRESCRIPTION)
    meds = {m.name_raw: m for m in out.medications}
    assert set(meds) == {"Fictomol", "Combiflex", "Zorbaxin", "Placebozide"}
    fictomol = meds["Fictomol"]
    assert (fictomol.name_norm, fictomol.strength_value, fictomol.frequency_per_day) == (
        "fictomol",
        Decimal("500"),
        2,
    )
    assert fictomol.review_status == "validated"
    assert (fictomol.date, fictomol.date_source) == (dt.date(2024, 3, 15), "document")
    assert meds["Combiflex"].review_reasons == ("ambiguous_medication_name",)
    assert meds["Combiflex"].name_norm is None
    assert meds["Zorbaxin"].review_reasons == ("unmapped_medication",)
    assert meds["Placebozide"].mention_context == "discontinued"
    events = {e.event_type: e for e in out.events}
    assert events["consultation"].description_raw == "Dr. Kiran Imaginary (Fictional), MBBS, MD"
    assert events["follow_up_instruction"].description_raw == "review after 2 weeks"


def test_every_excerpt_and_date_is_an_exact_slice_of_the_page() -> None:
    out = _extract("prescription", PRESCRIPTION)
    for draft in out.drafts:
        assert PRESCRIPTION[draft.char_start : draft.char_end] == draft.source_excerpt
        if draft.date_raw is not None:
            assert PRESCRIPTION[draft.date_char_start : draft.date_char_end] == draft.date_raw
    locators = [(type(d).__name__, d.source_locator) for d in out.drafts]
    assert len(locators) == len(set(locators))


def test_a_discharge_summary_separates_stated_and_document_dates() -> None:
    page_1 = (
        "Lotus Care Hospital (Fictional) DISCHARGE SUMMARY\n"
        f"{PATIENT}\nDate of Admission: 14/03/2024\nDate of Discharge: 18/03/2024\n"
        "Final Diagnosis: Viral fever; Type 2 diabetes mellitus\n"
        "Procedures: Upper GI endoscopy (15/03/2024)\nAllergies: No known drug allergies"
    )
    page_2 = (
        "Discharge Medications:\n1. Tab. Fictomol 500 mg 1-0-1\nFollow-up: review after 5 days."
    )
    out = _extract("discharge_summary", page_1, page_2)
    events = {(e.event_type, e.description_raw): e for e in out.events}
    admission = events[("admission", "Lotus Care Hospital (Fictional)")]
    assert (admission.date, admission.date_source) == (dt.date(2024, 3, 14), "stated")
    assert admission.page_number == 1
    procedure = events[("procedure", "Upper GI endoscopy")]
    assert (procedure.date, procedure.date_source) == (dt.date(2024, 3, 15), "stated")
    diagnosis = events[("diagnosis_documented", "Viral fever")]
    assert (diagnosis.date, diagnosis.date_source) == (dt.date(2024, 3, 18), "document")
    (med,) = out.medications
    assert (med.page_number, med.mention_context, med.date) == (
        2,
        "discharge_list",
        dt.date(2024, 3, 18),
    )
    (allergy,) = out.allergies
    assert (allergy.allergy_status, allergy.negated, allergy.substance_norm) == (
        "none_known",
        True,
        None,
    )


def test_history_records_keep_family_history_off_the_patient() -> None:
    text = (
        f"MEDICAL HISTORY RECORD\n{PATIENT}\nPast Medical History:\n"
        "- Asthma (2010) - Hernia repair "
        "(2001)\nFamily History: Father - coronary artery disease\nAllergies: Not recorded"
    )
    out = _extract("history_record", text)
    by = {e.description_raw: e for e in out.events}
    assert by["Asthma"].event_type == "diagnosis_documented"
    assert (by["Asthma"].date, by["Asthma"].date_precision) == (dt.date(2010, 1, 1), "year")
    assert by["Hernia repair"].event_type == "procedure"
    family = by["Father - coronary artery disease"]
    assert (family.subject, family.date) == ("family", None)
    assert out.allergies == ()  # "Not recorded" is unknown, never "no allergies"
    assert out.abstentions == {"allergy_status_unknown": 1}
    assert all("no_document_date" in d.quality_notes for d in out.medications)


def test_an_allergy_record_table_and_numbered_items() -> None:
    table = (
        "ALLERGY RECORD\nDate Recorded: 20/03/2024\nAllergen\tReaction\tSeverity\tStatus\n"
        "Penicillin\trash\tModerate\tConfirmed"
    )
    out = _extract("allergy_record", table)
    (row,) = out.allergies
    assert (row.substance_norm, row.reaction_raw, row.severity_norm, row.allergy_status) == (
        "penicillin",
        "rash",
        "moderate",
        "confirmed",
    )
    merged = (
        "DRUG ALLERGY CARD\nDate Recorded: 20/03/2024 Age/Sex: 40 Y / F 1. Latex - Reaction: "
        "itching - Severity: mild - Status: suspected Recorded by: staff"
    )
    (item,) = _extract("allergy_record", merged).allergies
    assert (item.substance_raw, item.allergy_status, item.severity_norm) == (
        "Latex",
        "suspected",
        "mild",
    )


def test_an_ambiguous_document_date_puts_every_dated_fact_in_review() -> None:
    out = _extract("prescription", PRESCRIPTION.replace("15/03/2024", "03/04/2024"))
    dated = [d for d in out.drafts if d.date_source == "document"]
    assert dated and all("ambiguous_date" in d.review_reasons for d in dated)


def test_two_different_document_dates_conflict_and_no_date_is_chosen() -> None:
    out = _extract("prescription", PRESCRIPTION + "\nDate: 16/03/2024")
    meds = [m for m in out.medications if m.name_raw == "Fictomol"]
    assert meds[0].date is None and "conflicting_dates" in meds[0].review_reasons


def test_ocr_pages_put_every_fact_in_review() -> None:
    out = _extract("prescription", PRESCRIPTION, ocr=True)
    assert out.drafts and all("ocr_unverified_source" in d.review_reasons for d in out.drafts)


def test_two_patients_on_one_page_extract_nothing() -> None:
    out = _extract("prescription", PRESCRIPTION + "\nPatient Name: Ravi Otherperson")
    assert out.drafts == () and out.abstentions == {"multiple_subjects": 1}


def test_stored_text_that_differs_from_the_ir_extracts_nothing() -> None:
    doc = document([page(1, [PRESCRIPTION])])
    out = extract_clinical_facts(doc, {1: PRESCRIPTION + " "}, "prescription", reference_date=REF)
    assert out.drafts == () and out.abstentions == {"page_text_mismatch": 1}


def test_an_empty_document_extracts_nothing() -> None:
    out = _extract("prescription", "Date: 15/03/2024")
    assert out.drafts == ()


def test_drafts_refuse_inconsistent_states_without_echoing_values() -> None:
    with pytest.raises(ValueError) as error:
        MedicationDraft(
            page_number=1,
            source_locator="p1:l0:s1",
            source_excerpt="SECRET-EXCERPT",
            char_start=0,
            char_end=5,
            review_status="needs_review",  # no reasons
            name_raw="SECRET-NAME",
            mention_context="prescribed",
        )
    assert "SECRET" not in str(error.value)
