"""AppTest tests of the Phase 6 Clinical records page and the clinical answers on the Copilot page.

No database: the clinical services are replaced by stand-ins returning pure-layer objects built
from fictional facts. docs/PHASE_6_PLAN.md §9.
"""

import datetime as dt
import uuid
from dataclasses import replace

import pytest

from app.clinical.answering import answer, route
from app.clinical.compare import compare_documents, detect_conflicts
from app.core.errors import DocumentProcessingError
from app.records.clinical import ClinicalDocumentView
from app.services import clinical_records, review_clinical, ui_runtime
from tests.test_clinical_compare_answering import allergy, event, med
from tests.test_ui import SECRET, _app, _texts
from tests.test_ui import backend as ui_backend  # noqa: F401 (fixture)

DOC_A, DOC_B = str(uuid.uuid4()), str(uuid.uuid4())


def _facts():
    facts = [
        med(DOC_A, "Fictomol", date=dt.date(2024, 3, 15)),
        med(DOC_B, "Fictomol", strength="250", date=dt.date(2024, 3, 15)),
        med(DOC_B, "Zorbaxin", generic="", status="needs_review"),
        allergy(DOC_A, "Penicillin", "confirmed"),
        allergy(DOC_B, "No known drug allergies", "none_known"),
        event(DOC_A, "admission", "Lotus Care Hospital (Fictional)", dt.date(2024, 3, 13)),
        event(DOC_A, "diagnosis_documented", "Father - asthma", None, subject="family"),
    ]
    return [replace(f, id=str(uuid.uuid4())) for f in facts]  # stored ids are UUIDs


def _view(document_id, name, clinical_type, status, reason, facts, review):
    return ClinicalDocumentView(
        document_id, name, "other", clinical_type, status, reason, None, facts, review
    )


def _documents():
    return (
        _view(uuid.UUID(DOC_A), "rx-march.pdf", "prescription", "succeeded", None, 3, 0),
        _view(uuid.UUID(DOC_B), "ds-march.pdf", "discharge_summary", "succeeded", None, 4, 1),
        _view(uuid.uuid4(), "bill.pdf", None, "skipped", "unsupported_type", 0, 0),
    )


@pytest.fixture
def clinical(ui_backend, monkeypatch):  # noqa: F811 (the shared UI backend fixture)
    backend = ui_backend()
    state = {"facts": _facts(), "reviews": [], "fail": None}

    def facts(patient_id):
        if state["fail"] == "facts":
            raise DocumentProcessingError(SECRET, kind="database_error")
        return tuple(state["facts"])

    monkeypatch.setattr(clinical_records, "clinical_facts", facts)
    monkeypatch.setattr(clinical_records, "clinical_documents", lambda p: _documents())
    monkeypatch.setattr(
        clinical_records,
        "document_comparison",
        lambda p, a, b: compare_documents(state["facts"], str(a), str(b)),
    )
    monkeypatch.setattr(
        clinical_records, "clinical_conflicts", lambda p: detect_conflicts(state["facts"])
    )
    monkeypatch.setattr(
        review_clinical,
        "review_clinical_fact",
        lambda p, f, d, **kw: state["reviews"].append((f, d, kw)) or "saved",
    )
    return backend, state


def test_the_page_shows_documents_medications_allergies_and_timeline(clinical) -> None:
    at = _app("Clinical records")
    assert not at.exception
    tables = [repr(d.value.to_dict("records")) for d in at.dataframe]
    assert any("Not a supported document" in t for t in tables)
    medications = next(t for t in tables if "Name as printed" in t)
    assert "Fictomol" in medications and "500 mg" in medications
    assert any("Zorbaxin" in t and "needs review" in t for t in tables)  # kept apart, marked
    assert any("Penicillin" in t for t in tables)
    timeline = next(t for t in tables if "As written" in t)
    assert "Lotus Care Hospital" in timeline and "Father" not in timeline  # family not shown
    assert "not a current medication list" in _texts(at)


def test_unknown_allergy_status_is_never_shown_as_none(clinical) -> None:
    _, state = clinical
    state["facts"] = [f for f in _facts() if f.fact_type != "allergy"]
    at = _app("Clinical records")
    assert "Allergy status unknown" in _texts(at)
    assert "does not mean there are none" in _texts(at)


def test_conflicts_are_listed_unresolved_with_both_sources(clinical) -> None:
    at = _app("Clinical records")
    text = _texts(at)
    assert "does not decide which is right" in text
    assert "Tab. Fictomol 500 mg" in text and "Tab. Fictomol 250 mg" in text


def test_a_review_decision_goes_through_the_review_service(clinical) -> None:
    _, state = clinical
    at = _app("Clinical records")
    (form_radio,) = [r for r in at.radio if r.label == "Your review"]
    form_radio.set_value("reject")
    (button,) = [b for b in at.button if b.label == "Save review"]
    button.click().run()
    assert not at.exception
    ((fact_id, decision, kwargs),) = state["reviews"]
    assert decision == "reject" and kwargs["correction"] is None
    assert uuid.UUID(str(fact_id))  # an id, never text
    assert "Review saved" in _texts(at)


def test_a_service_failure_shows_a_fixed_message_only(clinical) -> None:
    _, state = clinical
    state["fail"] = "facts"
    at = _app("Clinical records")
    assert SECRET not in _texts(at)
    assert not at.exception


def test_the_copilot_box_answers_records_questions_from_clinical_facts(clinical, monkeypatch):
    _, state = clinical

    def ask_records(patient_id, question, *, copilot, as_of=None):
        assert not hasattr(copilot, "never")  # the Phase 5 path is bound, not called here
        result = answer(route(question), state["facts"], as_of=dt.date(2025, 1, 1))
        return clinical_records.RecordsReply(clinical=result)

    monkeypatch.setattr(clinical_records, "ask_records", ask_records)
    monkeypatch.setattr(ui_runtime, "ask", lambda *a, **kw: pytest.fail("copilot not used"))
    at = _app("Copilot")
    at.text_input[0].set_value("What allergies are recorded?")
    (ask,) = [b for b in at.button if b.label == "Ask"]
    ask.click().run()
    assert not at.exception
    text = _texts(at)
    assert "Assembled by fixed rules from the stored records" in text
    assert "Penicillin" in text
    assert "not resolved" in text


# --- acceptance fixes: identity labels, refusals --------------------------------------------------


def test_identity_is_labelled_per_fact_compare_row_and_conflict_member(clinical) -> None:
    _, state = clinical
    state["facts"] = [
        replace(f, identity_check="match" if f.document_id == DOC_A else "unknown")
        for f in state["facts"]
    ]
    at = _app("Clinical records")
    assert not at.exception
    medications = next(
        d.value.to_dict("records") for d in at.dataframe if "Name as printed" in d.value.columns
    )
    by_source = {row["Source"].split(" ")[0]: row["Identity"] for row in medications}
    assert by_source == {f"{DOC_A}.pdf": "Confirmed", f"{DOC_B}.pdf": "Identity not confirmed"}
    compare = next(d.value.to_dict("records") for d in at.dataframe if "In A" in d.value.columns)
    assert any(row["Identity"] == "Identity not confirmed" for row in compare)
    assert "Identity not confirmed for this document" in _texts(at)  # conflicts and review


def test_confirmed_identity_is_never_labelled_uncertain(clinical) -> None:
    _, state = clinical
    state["facts"] = [replace(f, identity_check="match") for f in state["facts"]]
    at = _app("Clinical records")
    assert "Identity not confirmed" not in _texts(at)
    for frame in at.dataframe:
        assert "Identity not confirmed" not in repr(frame.value.to_dict("records"))


def _ask_on_copilot_page(monkeypatch, state, question: str, reply_status: str | None = None):
    def ask_records(patient_id, question, *, copilot, as_of=None):
        if reply_status == "refused":
            from app.clinical.answering import refusal_answer

            return clinical_records.RecordsReply(clinical=refusal_answer("medications"))
        result = answer(route(question), state["facts"], as_of=dt.date(2025, 1, 1))
        return clinical_records.RecordsReply(clinical=result)

    monkeypatch.setattr(clinical_records, "ask_records", ask_records)
    monkeypatch.setattr(ui_runtime, "ask", lambda *a, **kw: pytest.fail("copilot not used"))
    at = _app("Copilot")
    at.text_input[0].set_value(question)
    (ask,) = [b for b in at.button if b.label == "Ask"]
    ask.click().run()
    assert not at.exception
    return _texts(at)


def test_copilot_citations_warn_when_identity_is_not_confirmed(clinical, monkeypatch) -> None:
    _, state = clinical
    text = _ask_on_copilot_page(monkeypatch, state, "What medications are documented?")
    assert "Identity not confirmed for this document" in text
    assert "[Identity not confirmed]" in text


def test_a_refused_records_question_shows_only_the_fixed_refusal(clinical, monkeypatch) -> None:
    _, state = clinical
    text = _ask_on_copilot_page(
        monkeypatch, state, "Which documented medications are for my diabetes?", "refused"
    )
    assert "cannot say whether medicines interact" in text
    assert "Fictomol" not in text
