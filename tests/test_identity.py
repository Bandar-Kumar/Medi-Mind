"""Unit tests for patient identity verification rules (Task 2.8, ADR-036 D4, D5). Pure.

Every name and date below is fictional.
"""

import copy
import datetime as dt
from dataclasses import dataclass

import pytest

from app.ingestion import identity as ident
from app.ingestion.identity import (
    IDENTITY_RULES_VERSION,
    MATCH,
    MISMATCH,
    UNKNOWN,
    check_identity,
    normalize_name,
)

NAME = "Asha Fictional"
DOB = dt.date(1985, 3, 25)
FFFD = chr(0xFFFD)


@dataclass(frozen=True)
class Page:
    page_number: int
    text: str


def _check(text: str, name: str = NAME, dob: dt.date | None = DOB) -> ident.IdentityResult:
    return check_identity([Page(1, text)], name, dob)


# --- normalization ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "printed",
    [
        "Asha Fictional",
        "ASHA FICTIONAL",
        "  asha   fictional ",
        "Fictional, Asha",
        "Mrs. Asha Fictional",
        "Ms Asha Fictional",
        "Dr. FICTIONAL Asha",
        "Smt. Asha Fictional",
        "Asha-Fictional",
    ],
)
def test_case_spacing_punctuation_honorifics_and_order_are_normalized(printed) -> None:
    assert normalize_name(printed) == normalize_name(NAME) == frozenset({"asha", "fictional"})


def test_apostrophes_are_dropped_and_ambiguous_titles_are_kept() -> None:
    assert normalize_name("Asha D'Souza") == normalize_name("Asha DSouza")
    # Kumari / Kum / Sri are also given or middle names: never removed (strict matching)
    assert "kumari" in normalize_name("Kumari Asha Fictional")
    assert "sri" in normalize_name("Sri Asha")


# --- outcomes -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Patient Name: Asha Fictional",
        "Patient Name: Asha Fictional Age/Sex: 39 Y / F Date: 12/01/2024",  # merged by Docling
        "Patient: Mrs. FICTIONAL, Asha\tRef. By: Dr X",
        "Pt. Name : Fictional Asha | UHID 0001",
        "Patient's Name: Asha Fictional",
        "Name of Patient - Ms Asha Fictional  Ref. By: Dr X",
        "Doctor Name: Ravi Kumar\nPatient Name: Asha Fictional",  # another label is ignored
    ],
)
def test_the_same_full_name_is_a_match(text) -> None:
    result = _check(text)
    assert result.outcome == MATCH and "name_match" in result.reason_codes


@pytest.mark.parametrize(
    "text",
    [
        "Patient Name: Ravi Fictional Age/Sex: 9 Y / M",  # a family member: same surname
        "Patient Name: Asha Testwala",  # same first name, other surname
        "Patient Name: Kabir Demorao",
    ],
)
def test_a_clearly_different_full_name_is_a_mismatch(text) -> None:
    result = _check(text)
    assert result.outcome == MISMATCH and "name_mismatch" in result.reason_codes


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Patient Name: A. Fictional", "name_initials"),
        ("Patient Name: A Fictional", "name_initials"),
        ("Patient: Asha", "name_partial"),  # first name only
        ("Patient Name: Asha Kumari Fictional", "name_partial"),  # superset
        ("Patient Name: Ravi", "name_partial"),  # a single token is not a full name
        ("I am referring Asha Fictional for review", "name_not_found"),  # no label
        ("", "name_not_found"),
        ("Patient Name: Asha Fictional\nPatient Name: Ravi Kumar", "name_multiple"),
        (f"Patient Name: As{FFFD}ha Fictional", "name_unreadable"),
        ("Patient Name: As■ha Fictional", "name_unreadable"),  # Docling's unmapped glyph
        ("Patient Name: A B C D E F G", "name_ambiguous"),  # too many tokens
    ],
)
def test_missing_partial_ambiguous_or_unreadable_names_are_unknown(text, code) -> None:
    result = _check(text)
    assert result.outcome == UNKNOWN and code in result.reason_codes


def test_unreadable_wins_even_when_another_field_is_clean() -> None:
    text = f"Patient Name: Asha Fictional\nPatient Name: As{FFFD}ha Fictional"
    assert "name_unreadable" in _check(text).reason_codes


def test_accents_are_not_folded_strict_matching() -> None:
    assert _check("Patient Name: Méera Fictional", name="Meera Fictional").outcome == MISMATCH


# --- date of birth -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dob_text",
    ["DOB: 25/03/1985", "D.O.B: 25-03-1985", "Date of Birth: 25.03.1985", "DOB: 25-Mar-1985"]
    + ["Birth Date: 1985-03-25", "DOB: 25 March 1985"],
)
def test_a_matching_dob_in_any_format_keeps_the_match(dob_text) -> None:
    result = _check(f"Patient Name: Asha Fictional\n{dob_text}")
    assert result.outcome == MATCH and "dob_match" in result.reason_codes


def test_a_clearly_different_dob_is_a_mismatch_even_for_the_same_name() -> None:
    result = _check("Patient Name: Asha Fictional\nDOB: 26/03/1985")  # a namesake
    assert result.outcome == MISMATCH and "dob_mismatch" in result.reason_codes


@pytest.mark.parametrize(
    ("dob_text", "code"),
    [
        ("DOB: 03/04/1985", "dob_ambiguous"),  # day and month both <= 12
        ("DOB: 25/03/1985\nDOB: 25/03/1986", "dob_conflict"),
        ("DOB: 31/02/1985", "dob_unreadable"),
        ("DOB: unknown", "dob_unreadable"),
    ],
)
def test_ambiguous_conflicting_or_unreadable_dob_is_unknown(dob_text, code) -> None:
    result = _check(f"Patient Name: Asha Fictional\n{dob_text}", dob=dt.date(1985, 4, 3))
    assert result.outcome == UNKNOWN and code in result.reason_codes


def test_an_unambiguous_same_day_and_month_date_is_not_ambiguous() -> None:
    result = _check("Patient Name: Asha Fictional\nDOB: 05/05/1985", dob=dt.date(1985, 5, 5))
    assert result.outcome == MATCH


def test_a_dob_without_a_profile_dob_is_not_compared() -> None:
    result = _check("Patient Name: Asha Fictional\nDOB: 26/03/1985", dob=None)
    assert result.outcome == MATCH and "dob_not_in_profile" in result.reason_codes


def test_a_matching_dob_alone_never_makes_a_match() -> None:
    result = _check("DOB: 25/03/1985\nI am referring a patient")
    assert result.outcome == UNKNOWN and "dob_match" in result.reason_codes


def test_age_is_never_used_as_a_date_of_birth() -> None:
    wrong_age = _check("Patient Name: Asha Fictional    Age/Sex: 99 Y / F")
    assert wrong_age.outcome == MATCH and "dob_not_printed" in wrong_age.reason_codes


def test_a_name_mismatch_wins_over_an_ambiguous_dob() -> None:
    result = _check("Patient Name: Ravi Testwala\nDOB: 03/04/1985", dob=dt.date(1985, 4, 3))
    assert result.outcome == MISMATCH


# --- input checks, determinism, privacy ----------------------------------------------------------


@pytest.mark.parametrize(
    ("pages", "name", "dob", "error"),
    [
        ([], NAME, DOB, ValueError),
        ("text", NAME, DOB, ValueError),
        ([Page(0, "x")], NAME, DOB, ValueError),
        ([Page(1, "x"), Page(1, "y")], NAME, DOB, ValueError),
        ([Page(1, None)], NAME, DOB, TypeError),  # type: ignore[arg-type]
        ([Page(1, "x")], "  .  ", DOB, ValueError),
        ([Page(1, "x")], None, DOB, ValueError),
        ([Page(1, "x")], NAME, "1985-03-25", TypeError),
        ([Page(1, "x")], NAME, dt.datetime(1985, 3, 25), TypeError),
    ],
)
def test_malformed_input_is_rejected_without_echoing_it(pages, name, dob, error) -> None:
    with pytest.raises(error) as caught:
        check_identity(pages, name, dob)
    assert "Asha" not in str(caught.value) and "1985" not in str(caught.value)


def test_results_are_deterministic_versioned_and_hold_no_personal_data() -> None:
    pages = [Page(2, "DOB: 25/03/1985"), Page(1, "Patient Name: Asha Fictional")]
    before = copy.deepcopy(pages)
    first, second = check_identity(pages, NAME, DOB), check_identity(pages, NAME, DOB)
    assert first == second and pages == before  # never changes the pages
    assert first.rules_version == IDENTITY_RULES_VERSION and first.page_number == 1
    assert set(first.reason_codes) <= ident.REASON_CODES
    assert list(first.reason_codes) == sorted(first.reason_codes)
    text = repr(first)
    assert "Asha" not in text and "Fictional" not in text and "1985" not in text


def test_pages_are_read_in_page_order() -> None:
    result = check_identity(
        [Page(2, "Patient Name: Ravi Kumar"), Page(1, "Patient Name: Asha Fictional")], NAME, DOB
    )
    assert result.outcome == UNKNOWN and result.page_number == 1  # two names: never guessed


# --- rules v2 regressions (review of v1: false matches and false mismatches) --------------------


def test_rules_version_is_2() -> None:
    assert IDENTITY_RULES_VERSION == "2"


@pytest.mark.parametrize(
    ("text", "profile", "code"),
    [
        # a newborn's record is not the mother's: "Baby" is a meaningful token, not a title
        ("Patient Name: Baby Priya Sharma", "Priya Sharma", "name_partial"),
        ("Patient Name: Mrs. Priya Sharma", "Baby Priya Sharma", "name_partial"),
        # a single token is never a full name, on either side
        ("Patient Name: Mr. Sharma", "Baby Sharma", "name_partial"),
        ("Patient Name: Sharma", "Sharma", "profile_name_incomplete"),
        ("Patient Name: Selvi", "Selvi", "profile_name_incomplete"),
        # an initial on either side can never match
        ("Patient Name: R. Lakshmi", "R. Lakshmi", "name_initials"),
        ("Patient Name: R Lakshmi", "R. Lakshmi", "name_initials"),
    ],
)
def test_no_false_match_without_a_full_name_on_both_sides(text, profile, code) -> None:
    result = _check(text, name=profile, dob=None)
    assert result.outcome == UNKNOWN and code in result.reason_codes


def test_baby_is_kept_and_matches_only_the_newborn_profile() -> None:
    assert "baby" in normalize_name("Baby Priya Sharma")
    result = _check("Patient Name: Baby Priya Sharma", name="Baby Priya Sharma", dob=None)
    assert result.outcome == MATCH


@pytest.mark.parametrize(
    ("text", "profile"),
    [
        ("Patient Name: Ramesh Lakshmi", "R. Lakshmi"),  # profile initial: not a full name
        ("Patient Name: Kabir Testwala", "Sharma"),  # single-token profile
        ("Patient Name: Kabir Testwala", "Selvi"),
        ("Patient Name: Asha Fictional", "Asha F."),
    ],
)
def test_an_incomplete_profile_name_gives_unknown_never_mismatch(text, profile) -> None:
    result = _check(text, name=profile, dob=None)
    assert result.outcome == UNKNOWN and "profile_name_incomplete" in result.reason_codes


def test_a_profile_name_with_a_format_character_is_incomplete() -> None:
    result = _check("Patient Name: Asha Fictional", name="Asha Fic­tional", dob=None)
    assert result.outcome == UNKNOWN and "profile_name_incomplete" in result.reason_codes


def test_an_incomplete_profile_still_shows_a_clear_dob_mismatch() -> None:
    result = _check("Patient Name: Selvi\nDOB: 26/03/1985", name="Selvi")
    assert result.outcome == MISMATCH and "dob_mismatch" in result.reason_codes


@pytest.mark.parametrize(
    "dob_text",
    [
        "Baby DOB: 15/06/2024",
        "Baby's Date of Birth: 15-Jun-2024",
        "Father's DOB: 01/01/1960",
        "Father’s D.O.B: 01/01/1960",  # curly apostrophe
        "Mother DOB: 01-Jan-1962",
        "Father / Guardian DOB: 01/01/1960",
        "Spouse Date of Birth: 1960-01-01",
        "Date of Birth of Baby: 15/06/2024",
        "DOB (Mother): 01/01/1962",
    ],
)
def test_another_persons_dob_is_never_the_patients(dob_text) -> None:
    result = _check(f"Patient Name: Asha Fictional\n{dob_text}")
    assert result.outcome == MATCH and "dob_not_printed" in result.reason_codes


def test_the_patients_dob_is_still_read_next_to_another_persons() -> None:
    text = "Patient Name: Asha Fictional\nDOB: 26/03/1985\nBaby DOB: 25/03/1985"
    result = _check(text)  # the patient's own DOB differs: still a namesake mismatch
    assert result.outcome == MISMATCH and "dob_mismatch" in result.reason_codes


@pytest.mark.parametrize(
    "text",
    [
        "Patient Name: Asha Dobriyal",  # a surname starting with "Dob"
        "Patient Name: Asha Dobriyal\nStarted dobutamine 5 mcg/kg/min on 12/01/2024",
        "Patient Name: Asha Dobriyal  Age/DOB: 33 Y",  # an age, not a date
        "Patient Name: Asha Dobriyal  DOB/Age: 39 Yrs",
        "Patient Name: Asha Dobriyal\nDobbs ward note",
        # Docling merges printed lines: a later date belongs to another field (found on dev)
        "Patient Name: Asha Dobriyal  Age/DOB: 38 Y Report Date: 15/06/2024",
        "Patient Name: Asha Dobriyal Age/DOB: 41 Y Dobutamine stopped. Report Date: 15/06/2024",
        "Patient Name: Asha Dobriyal  Age/DOB: 33 Y Date 15/06/2024",
    ],
)
def test_dob_labels_need_whole_words_and_an_age_is_never_a_dob(text) -> None:
    result = _check(text, name="Asha Dobriyal")
    assert result.outcome == MATCH and "dob_not_printed" in result.reason_codes


@pytest.mark.parametrize(
    ("dob_text", "outcome", "code"),
    [
        ("Age/DOB: 39 Y / 25-03-1985", MATCH, "dob_match"),
        ("DOB/Age: 25-Mar-1985 (39 Y)", MATCH, "dob_match"),
        ("Age/DOB: 39 Y / 26-03-1985", MISMATCH, "dob_mismatch"),  # a namesake is still caught
    ],
)
def test_a_date_in_a_combined_age_dob_field_is_used(dob_text, outcome, code) -> None:
    result = _check(f"Patient Name: Asha Fictional  {dob_text}")
    assert result.outcome == outcome and code in result.reason_codes


@pytest.mark.parametrize("mark", ["­", "‍", "‌", "​", "﻿"])
@pytest.mark.parametrize("profile", [NAME, "Asha Fictionl", "Ravi Kumar"])
def test_format_characters_in_a_name_are_unreadable_never_match_or_mismatch(mark, profile) -> None:
    result = _check(f"Patient Name: Asha Fic{mark}tional", name=profile, dob=None)
    assert result.outcome == UNKNOWN and "name_unreadable" in result.reason_codes


@pytest.mark.parametrize(
    "label", ["Patient’s Name:", "PATIENT’S NAME :", "Patient's Name:", "Patient‘s Name:"]
)
def test_straight_and_curly_apostrophe_patient_labels_are_read(label) -> None:
    result = _check(f"{label} Asha Fictional")
    if "‘" in label:  # a left quote is not an apostrophe: never guessed
        assert result.outcome == UNKNOWN
    else:
        assert result.outcome == MATCH and result.page_number == 1


@pytest.mark.parametrize(
    "text",
    [
        "Name: Dr. Kiran Imaginary",  # the doctor
        "Guardian Details\nName: Asha Fictional",  # the guardian may be a profile too
        "Name: Asha Fictional",  # even the patient: a generic label is never evidence
        "Name : Ravi Kumar\nRelation: Father",
    ],
)
def test_a_generic_name_field_alone_never_gives_match_or_mismatch(text) -> None:
    result = _check(text, dob=None)
    assert result.outcome == UNKNOWN and "name_generic_label" in result.reason_codes
    assert result.page_number is None


@pytest.mark.parametrize(
    ("text", "outcome"),
    [
        ("Patient Name: Asha Fictional\nName: Dr. Kiran Imaginary", MATCH),
        ("Name: Ravi Kumar\nPatient Name: Asha Fictional", MATCH),  # not name_multiple
        ("Patient Name: Ravi Testwala\nName: Asha Fictional", MISMATCH),  # never rescued
    ],
)
def test_a_generic_name_field_is_ignored_next_to_a_patient_label(text, outcome) -> None:
    assert _check(text, dob=None).outcome == outcome


def test_a_guardian_name_on_a_newborn_record_never_matches_the_guardian() -> None:
    text = "Patient Name: Baby Asha Fictional\nGuardian Details\nName: Asha Fictional"
    result = _check(text, dob=None)
    assert result.outcome == UNKNOWN and "name_partial" in result.reason_codes


@pytest.mark.parametrize(
    ("text", "profile"),
    [
        ("Patient Name: Aarthi Demoraj", "Aarthy Demoraj"),  # a different spelling: no fuzzy
        ("Patient Name: Sai-Krishna Mockdev", "Saikrishna Mockdev"),
        ("Patient Name: Méera Fictional", "Meera Fictional"),  # no accent folding
    ],
)
def test_strict_matching_is_kept_near_misses_are_mismatches(text, profile) -> None:
    assert _check(text, name=profile, dob=None).outcome == MISMATCH  # documented limit (D5)
