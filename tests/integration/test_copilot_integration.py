"""PostgreSQL integration tests of the Phase 5 copilot (medimind_test; ADR-040).

All text is fictional. Two patients each get a parsed, classified, identity-checked document with
stored facts (through the production write path) and a stored chunk. The copilot, its tools and
the citation catalog must stay inside the selected patient, exclude rejected facts, keep
corrections, and fail closed when another patient's evidence is injected.
"""

import datetime as dt
import uuid

import pytest

from app.answering.model import CopilotStatus, Route
from app.records import chunks as chunk_repo
from app.records.chunks import ChunkRecord
from app.services import copilot, review_fact
from app.services import copilot_tools as tools
from app.services.reference_data import seed_reference
from tests.integration.test_longitudinal_integration import _document, _extract, _facts

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("main_database_unchanged")]
TODAY = dt.date(2026, 11, 17)


@pytest.fixture(autouse=True)
def seeded(test_db_session_factory) -> None:
    seed_reference()


@pytest.fixture(autouse=True)
def conflicts_cleanup(test_engine, app_settings, test_patient, second_patient):
    yield
    from sqlalchemy import text

    from tests.integration._guards import assert_is_test_database

    with test_engine.begin() as connection:
        assert_is_test_database(connection, app_settings.db_name)
        for patient in (test_patient, second_patient):
            connection.execute(text("DELETE FROM conflicts WHERE patient_id = :p"), {"p": patient})


def _patient_with_results(patient, rows, *, date="25/03/2024", day=dt.date(2024, 3, 25)):
    document, page = _document(patient, rows, date=date)
    _extract(patient, document, page, rows, date=day)
    chunk_repo.insert_document_chunks(
        patient,
        document,
        [ChunkRecord(1, "text", None, page.splitlines()[1], tuple([1.0] + [0.0] * 383))],
        embedding_model="fake@0",
        component_versions={"chunker_version": "1"},
        duration_ms=1,
    )
    return document, page


def ask(patient, question):
    return copilot.ask_copilot(patient, question, today=TODAY)


def test_tools_and_answers_stay_inside_the_selected_patient(test_patient, second_patient) -> None:
    mine, _ = _patient_with_results(test_patient, [("Haemoglobin", "12.9")])
    theirs, _ = _patient_with_results(second_patient, [("Haemoglobin", "15.5")])
    result = tools.run_tool(test_patient, "latest_result", {"analyte": "Haemoglobin"})
    assert [f.observation.document_id for f in result.facts] == [str(mine)]
    answer = ask(test_patient, "What was my latest haemoglobin?")
    assert answer.status is CopilotStatus.ANSWERED
    assert {s.document_id for s in answer.sources} == {str(mine)}
    assert "12.9" in answer.claims[0].text and "15.5" not in answer.claims[0].text
    other = ask(second_patient, "What was my latest haemoglobin?")
    assert {s.document_id for s in other.sources} == {str(theirs)}


def test_the_catalog_and_page_reads_are_patient_scoped(test_patient, second_patient) -> None:
    theirs, page = _patient_with_results(second_patient, [("Haemoglobin", "15.5")])
    chunk = next(
        iter(
            chunk_repo.stored_chunks(
                second_patient, [c for c in _chunk_ids(second_patient, theirs)]
            ).values()
        )
    )
    assert chunk_repo.stored_chunks(test_patient, [chunk.chunk_id]) == {}
    assert copilot.source_page(second_patient, str(theirs), 1) == page
    assert copilot.source_page(test_patient, str(theirs), 1) is None


def _chunk_ids(patient, document):
    found = chunk_repo.lexical_search(patient, ("haemoglobin",), limit=10)
    return [h.chunk_id for h in found if h.document_id == document]


def test_injected_foreign_evidence_fails_closed(test_patient, second_patient, monkeypatch) -> None:
    _patient_with_results(test_patient, [("Haemoglobin", "12.9")])
    _patient_with_results(second_patient, [("Haemoglobin", "15.5")])
    foreign = tools.run_tool(second_patient, "latest_result", {"analyte": "Haemoglobin"}).facts
    original = tools.run_tool

    def compromised(patient_id, name, arguments):
        result = original(patient_id, name, arguments)
        return tools.ToolResult(result.tool, result.args, (*foreign, *result.facts), (), 0)

    monkeypatch.setattr(tools, "run_tool", compromised)
    answer = ask(test_patient, "What was my latest haemoglobin?")
    assert answer.status is CopilotStatus.VALIDATION_FAILED
    assert answer.sources == () and answer.claims == ()
    assert {i.code for i in answer.issues} == {"source_not_found"}


def test_rejected_facts_never_appear_and_corrections_are_shown(test_patient) -> None:
    document, _ = _patient_with_results(test_patient, [("Haemoglobin", "12.9"), ("HbA1c", "6.1")])
    ids = _facts(test_patient, document)
    review_fact.review_fact(test_patient, ids["HbA1c"], "reject")
    review_fact.review_fact(
        test_patient, ids["Haemoglobin"], "correct", correction=review_fact.Correction(value="13.4")
    )
    rejected = ask(test_patient, "What was my HbA1c on 25/03/2024?")
    assert rejected.status is CopilotStatus.INSUFFICIENT_EVIDENCE
    assert all(s.fact_id != str(ids["HbA1c"]) for s in rejected.sources)
    corrected = ask(test_patient, "What was my haemoglobin on 25/03/2024?")
    assert corrected.status is CopilotStatus.ANSWERED
    text = corrected.claims[0].text
    assert "13.4" in text and 'the report printed "12.9 g/dL"' in text


def test_a_refusal_touches_no_data(test_patient, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("no tool may run for a refused question")

    monkeypatch.setattr(tools, "run_tool", forbidden)
    answer = ask(test_patient, "What is my diagnosis based on these results?")
    assert answer.status is CopilotStatus.REFUSED
    assert answer.plan.route is Route.REFUSAL


def test_document_search_on_the_real_index_is_cited_and_validated(test_patient) -> None:
    from app.retrieval.evidence import RetrievalConfig, RetrievalMode

    document, _ = _patient_with_results(test_patient, [("Haemoglobin", "12.9")])
    retrieval = copilot.RetrievalDeps(RetrievalConfig(mode=RetrievalMode.LEXICAL), None, None)
    answer = copilot.ask_copilot(
        test_patient, "What did the report say about haemoglobin?", retrieval=retrieval, today=TODAY
    )
    assert answer.status is CopilotStatus.ANSWERED, [i.code for i in answer.issues]
    assert answer.plan.route is Route.COMBINED
    kinds = {s.kind.value for s in answer.cited_sources}
    assert kinds == {"fact", "passage"}
    assert all(s.document_id == str(document) for s in answer.sources)


def test_a_document_id_of_another_patient_is_not_a_uuid_leak(test_patient) -> None:
    assert copilot.source_page(test_patient, str(uuid.uuid4()), 1) is None
    assert copilot.source_page(test_patient, "not-a-uuid", 1) is None
