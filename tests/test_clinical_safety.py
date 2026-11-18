"""Phase 6 acceptance fixes: dispatcher allow-list, identity labelling, citation integrity, stage
label. No database (services faked). Fictional content only.

The probe list (with hand-written expected decisions) lives in ``evals/run_clinical_safety.py``
so the durable report and these tests can never disagree.
"""

import datetime as dt
import uuid
from dataclasses import replace

import pytest

from app.clinical.answering import (
    CLINICAL_REFUSAL,
    IDENTITY_NOT_CONFIRMED,
    answer,
    citation_issues,
    normalize_question,
    plain_lookup,
    route,
)
from app.records.clinical import ClinicalFactView
from app.services import clinical_records as records
from app.services import document_pipeline as pipe
from app.ui import texts
from evals.run_clinical_safety import PROBES, frozen_questions

PATIENT = uuid.uuid4()
DOC_OK, DOC_UNKNOWN, DOC_BAD = (str(uuid.uuid4()) for _ in range(3))
PAGE = "Rx\nTab. Fictomol 500 mg 1-0-1\nTab. Mockcillin 250 mg BD"


@pytest.mark.parametrize(("category", "question", "expected"), PROBES)
def test_every_probe_gets_its_expected_decision(category, question, expected) -> None:
    assert records.dispatch(question) == expected


def test_no_audit_bypass_example_is_answered() -> None:
    audit = [q for c, q, _ in PROBES if c == "audit_bypass"]
    assert len(audit) == 12
    assert [q for q in audit if records.clinical_route(q)] == []


def test_all_926_frozen_phase5_questions_still_go_to_phase5() -> None:
    questions, _ = frozen_questions()
    assert len(questions) == 926
    assert {records.dispatch(q) for q in questions} == {records.PHASE5}


@pytest.mark.parametrize(
    "question",
    [
        "What medications are documented in my records that interact",  # no "?"
        "WHAT MEDICATIONS ARE DOCUMENTED IN MY RECORDS FOR MY HEART?",
        "What medications are documented in my records — are they safe?",
        "What medications are documented in my records and which can I stop?",
        "What medications are documented in my records? Which are risky?",
        "What medications are documented in my records (for my diabetes)?",
        "What medications are documented in my records\nthat I am allergic to?",
        "Which diagnoses are documented in my records that cause fatigue?",
        "Which records mention fictomol side effects?",
        "List all my medications documented in my records so I can choose one",
    ],
)
def test_trailing_clauses_and_formatting_tricks_never_pass_the_allow_list(question) -> None:
    assert plain_lookup(question) is None
    assert records.dispatch(question) != records.CLINICAL


def test_normalization_only_removes_case_spacing_and_trailing_punctuation() -> None:
    assert normalize_question("  What   MEDICATIONS are documented?!? ") == (
        "what medications are documented"
    )
    assert normalize_question("Are they safe? Yes?") == "are they safe? yes"  # inner "?" kept


def test_the_router_must_agree_with_the_allow_list() -> None:
    assert plain_lookup("What allergies are recorded?") == "allergies"
    assert plain_lookup("What vaccinations are documented in my records?") == "abstain"
    assert plain_lookup(None) is None  # type: ignore[arg-type]


# --- dispatcher through the service -------------------------------------------------------------


def _view(
    document_id: str, identity: str, *, excerpt="Tab. Fictomol 500 mg 1-0-1", name="Fictomol"
):
    start = PAGE.index(excerpt)
    return ClinicalFactView(
        id=uuid.uuid4(), fact_type="medication", document_id=uuid.UUID(document_id),
        document_name=f"{identity}.pdf", clinical_doc_type="prescription", document_date=None,
        page_number=1, source_locator="p1:l1:s1", source_excerpt=excerpt, char_start=start,
        char_end=start + len(excerpt), subject="patient", negated=False,
        date=dt.date(2024, 3, 15), date_precision="day", date_raw="15/03/2024",
        date_source="document", date_page_number=1, date_char_start=0, date_char_end=2,
        review_status="validated", review_reasons=(), quality_notes=(),
        values={"name_raw": name, "name_norm": name.lower(), "mention_context": "prescribed"},
        identity_check=identity,
    )  # fmt: skip


@pytest.fixture
def wired(monkeypatch):
    state = {
        "views": [
            _view(DOC_OK, "match"),
            _view(DOC_UNKNOWN, "unknown", excerpt="Tab. Mockcillin 250 mg BD", name="Mockcillin"),
            _view(DOC_BAD, "mismatch"),
        ],
        "pages": {(DOC_OK, 1): PAGE, (DOC_UNKNOWN, 1): PAGE},
        "page_reads": [],
    }
    monkeypatch.setattr(records, "list_clinical_facts", lambda p: tuple(state["views"]))

    def pages(patient_id, keys):
        state["page_reads"].append((patient_id, list(keys)))
        wanted = {(str(d), n) for d, n in keys}
        return {k: v for k, v in state["pages"].items() if k in wanted}

    monkeypatch.setattr(records, "stored_page_texts", pages)
    return state


def _ask(question: str):
    return records.ask_records(
        PATIENT,
        question,
        copilot=lambda p, q: ("phase5", q),
        as_of=dt.date(2025, 1, 1),
    )


def test_a_refused_clinical_question_reads_no_records(wired, monkeypatch) -> None:
    monkeypatch.setattr(records, "list_clinical_facts", lambda p: pytest.fail("no facts read"))
    reply = _ask("Which documented medications are for my diabetes?")
    assert reply.clinical is not None and reply.clinical.status == "refused"
    assert reply.clinical.summary == CLINICAL_REFUSAL and reply.clinical.sections == ()


def test_a_phase5_refusal_reaches_the_copilot_unchanged(wired) -> None:
    question = "Is my documented diagnosis curable?"
    assert _ask(question).copilot == ("phase5", question)


def test_identity_is_shown_and_mismatch_facts_never_answer(wired) -> None:
    reply = _ask("What medications are documented in my records?")
    result = reply.clinical
    assert result is not None and result.status == "answered"
    items = {i.citations[0].document_id: i for i in result.section("documented")}
    assert set(items) == {DOC_OK, DOC_UNKNOWN}  # unknown kept (not hidden); mismatch dropped
    assert items[DOC_UNKNOWN].text.endswith(f"[{IDENTITY_NOT_CONFIRMED}]")
    assert not items[DOC_UNKNOWN].citations[0].identity_confirmed
    assert IDENTITY_NOT_CONFIRMED not in items[DOC_OK].text
    assert items[DOC_OK].citations[0].identity_confirmed
    assert [f.identity_check for f in records.clinical_facts(PATIENT)] == ["match", "unknown"]


def test_comparison_and_conflicts_carry_identity(wired) -> None:
    facts = records.clinical_facts(PATIENT)
    assert {f.document_id: f.identity_confirmed for f in facts} == {
        DOC_OK: True,
        DOC_UNKNOWN: False,
    }
    comparison = records.document_comparison(PATIENT, uuid.UUID(DOC_OK), uuid.UUID(DOC_UNKNOWN))
    flags = {
        f.document_id: f.identity_confirmed for i in comparison.items for f in i.a_facts + i.b_facts
    }
    assert flags == {DOC_OK: True, DOC_UNKNOWN: False}


def test_citations_are_checked_against_this_patients_stored_pages(wired) -> None:
    result = _ask("What medications are documented in my records?").clinical
    assert result is not None and result.status == "answered"
    ((patient_id, keys),) = wired["page_reads"]
    assert patient_id == PATIENT
    assert {str(d) for d, _ in keys} == {DOC_OK, DOC_UNKNOWN}


@pytest.mark.parametrize(
    "tamper",
    [
        lambda s: s["pages"].update({(DOC_OK, 1): PAGE.replace("500", "250")}),  # altered text
        lambda s: s["pages"].pop((DOC_UNKNOWN, 1)),  # missing source page
    ],
)
def test_an_inexact_citation_withholds_the_whole_answer(wired, tamper) -> None:
    tamper(wired)
    result = _ask("What medications are documented in my records?").clinical
    assert result is not None and result.status == "withheld" and result.sections == ()


def test_citation_issue_codes() -> None:
    fact = records.to_fact(_view(DOC_OK, "match"))
    plan = route("What medications are documented in my records?")
    result = answer(plan, [fact], as_of=dt.date(2025, 1, 1))
    assert citation_issues(result, lambda d, p: PAGE) == ()
    assert citation_issues(result, lambda d, p: PAGE.upper()) == ("excerpt_mismatch",)
    assert citation_issues(result, lambda d, p: None) == ("source_missing",)
    no_offsets = answer(plan, [replace(fact, char_start=None)], as_of=dt.date(2025, 1, 1))
    assert citation_issues(no_offsets, lambda d, p: PAGE) == ("offsets_missing",)
    empty = answer(plan, [replace(fact, source_excerpt="")], as_of=dt.date(2025, 1, 1))
    assert citation_issues(empty, lambda d, p: PAGE) == ("excerpt_missing",)


# --- stage label --------------------------------------------------------------------------------


def test_every_pipeline_stage_has_a_display_label() -> None:
    for stage in pipe.STAGES:
        assert texts.stage_name(stage) != stage
    assert texts.stage_name(pipe.CLINICAL_EXTRACT) == "Extract clinical records"
