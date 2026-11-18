"""Patient vocabulary and unrecognised test names (Phase 5 remediation; ADR-041). Pure."""

import datetime as dt

import pytest

from app.answering.model import Intent, Route
from app.answering.router import route_question, unrecognised_terms
from app.answering.vocabulary import AMBIGUOUS_TERMS, LAY_TERMS, question_vocabulary
from app.extraction.reference import load_reference

REFERENCE = load_reference()
ALIASES = REFERENCE.aliases
TODAY = dt.date(2026, 11, 17)


def plan(question: str):
    return route_question(question, aliases=ALIASES, today=TODAY)


def test_every_vocabulary_entry_maps_to_a_reference_analyte() -> None:
    for term, canonical in LAY_TERMS.items():
        assert canonical in REFERENCE.analytes, term
        assert term == term.casefold().strip(), term
    for term, options in AMBIGUOUS_TERMS.items():
        assert len(options) >= 2, term
        assert set(options) <= set(REFERENCE.analytes), term


def test_printed_aliases_win_over_lay_terms() -> None:
    single, ambiguous = question_vocabulary(dict(ALIASES))
    for alias, canonical in ALIASES.items():
        assert single[alias] == canonical
        assert alias not in ambiguous


@pytest.mark.parametrize(
    ("question", "analyte"),
    [
        ("Latest A1c", "HbA1c"),
        ("What was my glycated haemoglobin on 13/02/2022?", "HbA1c"),  # not "Haemoglobin"
        ("Show my fasting sugar results from 2022.", "Fasting Blood Sugar"),
        ("Latest fasting glucose", "Fasting Blood Sugar"),
        ("Compare my latest cholesterol with the previous result.", "Total Cholesterol"),
        ("What was my good cholesterol?", "HDL Cholesterol"),
        ("Latest HGB", "Haemoglobin"),
        ("What was my latest vitamin D result?", "Vitamin D (25-OH)"),
    ],
)
def test_everyday_names_are_recognised(question, analyte) -> None:
    assert plan(question).analytes == (analyte,)


def test_an_ambiguous_name_keeps_every_candidate_and_is_labelled() -> None:
    result = plan("What was my blood sugar?")
    assert set(result.analytes) == {"Fasting Blood Sugar", "Post Prandial Blood Sugar"}
    assert "ambiguous_test_name" in result.reasons and result.ambiguous


@pytest.mark.parametrize(
    ("question", "route", "intent"),
    [
        ("What was my latest ferritin result?", Route.STRUCTURED_FACTS, Intent.LATEST),
        ("Show my CRP results over time.", Route.LONGITUDINAL, Intent.HISTORY),
        ("Compare my latest zinc with the previous result.", Route.LONGITUDINAL,
         Intent.COMPARE_PREVIOUS),
        ("What was my troponin on 13/02/2022?", Route.STRUCTURED_FACTS, Intent.ON_DATE),
        ("Are there conflicting ferritin results?", Route.LONGITUDINAL, Intent.CONFLICTS),
        ("What was my haemaglobin?", Route.STRUCTURED_FACTS, Intent.LATEST),  # misspelt
    ],
)  # fmt: skip
def test_unknown_test_names_are_never_widened_to_all_tests(question, route, intent) -> None:
    result = plan(question)
    assert result.analytes == ()
    assert result.unrecognised
    assert (result.route, result.intent) == (route, intent)
    assert "all_analytes" not in result.reasons


def test_questions_that_ask_for_everything_still_work() -> None:
    assert plan("What were my results on 13/07/2022?").unrecognised == ()
    everything = plan("Show all my results from 2021.")
    assert everything.unrecognised == () and "all_analytes" in everything.reasons
    latest = plan("What was my latest result?")
    assert latest.intent is Intent.LIST_ANALYTES


def test_a_known_and_an_unknown_test_keep_the_known_one() -> None:
    result = plan("What were my latest HbA1c and vitamin K results?")
    assert result.analytes == ("HbA1c",)
    assert result.unrecognised == ("vitamin k",)


def test_unknown_words_form_whole_phrases() -> None:
    assert unrecognised_terms("my vitamin k and zinc") == ("vitamin k", "zinc")
    assert unrecognised_terms("results on 13/02/2022 from 2022") == ()
    assert unrecognised_terms("what is the latest") == ()


def test_safety_still_runs_before_vocabulary() -> None:
    assert plan("Should I stop my A1c tablets?").route is Route.REFUSAL
    assert plan("Is my fasting sugar normal?").route is Route.REFUSAL
