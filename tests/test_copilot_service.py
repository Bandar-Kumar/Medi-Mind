"""Copilot orchestration with fake tools, retrieval and catalog (Phase 5; ADR-040). No database.

What is checked here is the wiring: the safety gate runs before any tool, every answer goes
through the citation validator, failed answers are withheld, the external-provider privacy guard,
partial answers, tool-argument validation and log hygiene.
"""

import datetime as dt
import json
import logging
import uuid
from types import SimpleNamespace

import pytest

from app.answering.citations import CatalogFact, SourceCatalog
from app.answering.generate import LlmGenerator
from app.answering.model import CopilotStatus, Route
from app.llm.mock import ScriptedProvider
from app.longitudinal.model import LongitudinalObservation
from app.retrieval.evidence import (
    AnswerStatus,
    Evidence,
    EvidenceAnswer,
    RetrievalConfig,
    RetrievalMode,
)
from app.services import copilot
from app.services import copilot_tools as tools

PATIENT = uuid.uuid4()
DOC = str(uuid.uuid4())
FACT = str(uuid.uuid4())
PAGE = "Fictional Lab\nHbA1c\t6.2\t%\t4.0 - 5.6\n"
SECRET_QUESTION_WORD = "Zanzibar"


def observation(**changes) -> LongitudinalObservation:
    base = dict(
        fact_id=FACT, document_id=DOC, extraction_run_id=str(uuid.uuid4()), page_number=1,
        source_locator="p1:t0:r1", char_start=14, char_end=35, analyte_id=9, analyte_name="HbA1c",
        value=__import__("decimal").Decimal("6.2"), comparator=None, unit="%", qualitative=None,
        ref_low=None, ref_high=None, range_comparison=None, effective_date=dt.date(2022, 9, 13),
        date_precision="day", date_source="report", effective_time=None,
        review_status="validated", value_source="machine", doc_type="lab_report",
        identity_check="match", value_raw="6.2", unit_raw="%", ref_range_raw="4.0 - 5.6",
    )  # fmt: skip
    base.update(changes)
    return LongitudinalObservation(**base)


def tool_result(*, missing=(), facts=None):
    return tools.ToolResult(
        tool=tools.ToolName.LATEST_RESULT,
        args=tools.ToolArgs(analyte="HbA1c"),
        facts=facts
        if facts is not None
        else (tools.ToolFact(observation(), "HbA1c\t6.2\t%\t4.0 - 5.6", "r.pdf", None, False),),
        missing=missing,
        awaiting_review=0,
    )


CATALOG = SourceCatalog(
    pages={(DOC, 1): PAGE}, facts={FACT: CatalogFact(DOC, 1, "validated", False)}
)


@pytest.fixture
def fakes(monkeypatch):
    state = SimpleNamespace(tool_calls=[], result=tool_result(), catalog=CATALOG, synthetic=True)

    def run_tool(patient_id, name, arguments):
        state.tool_calls.append((patient_id, name, arguments))
        return state.result

    monkeypatch.setattr(tools, "run_tool", run_tool)
    monkeypatch.setattr(copilot, "_catalog", lambda patient_id, items: state.catalog)
    monkeypatch.setattr(
        copilot, "get_patient", lambda pid: SimpleNamespace(is_synthetic=state.synthetic)
    )
    return state


def ask(question, **kwargs):
    return copilot.ask_copilot(PATIENT, question, today=dt.date(2026, 11, 17), **kwargs)


def llm(reply) -> LlmGenerator:
    return LlmGenerator(ScriptedProvider(lambda request: json.dumps(reply)))


def test_a_refusal_runs_no_tool_retrieval_or_model(fakes) -> None:
    provider = ScriptedProvider(lambda r: "{}")
    answer = ask("Which medication should I take for my HbA1c?", generator=LlmGenerator(provider))
    assert answer.status is CopilotStatus.REFUSED
    assert answer.message and "cannot recommend" in answer.message
    assert fakes.tool_calls == [] and provider.calls == 0
    assert answer.claims == () and answer.sources == ()


def test_general_knowledge_is_unsupported(fakes) -> None:
    answer = ask("What is HbA1c?")
    assert answer.status is CopilotStatus.UNSUPPORTED and fakes.tool_calls == []


def test_a_structured_answer_is_validated_and_cited(fakes) -> None:
    answer = ask("What was my latest HbA1c?")
    assert answer.status is CopilotStatus.ANSWERED
    assert answer.plan.route is Route.STRUCTURED_FACTS
    assert fakes.tool_calls == [(PATIENT, tools.ToolName.LATEST_RESULT, {"analyte": "HbA1c"})]
    assert answer.cited == ("F1",)
    assert answer.cited_sources[0].excerpt == "HbA1c\t6.2\t%\t4.0 - 5.6"
    assert answer.generator == "extractive"
    assert "tool:latest_result" in answer.activity


def test_a_fabricated_citation_from_a_model_is_withheld(fakes) -> None:
    reply = {"status": "answered", "claims": [{"text": "HbA1c: 6.2 %.", "citations": ["F4"]}]}
    answer = ask("What was my latest HbA1c?", generator=llm(reply))
    assert answer.status is CopilotStatus.VALIDATION_FAILED
    assert answer.claims == ()  # never shown
    assert {i.code for i in answer.issues} == {"unknown_source"}
    assert answer.sources  # still available for inspection


def test_a_source_missing_from_the_patient_catalog_is_withheld(fakes) -> None:
    fakes.catalog = SourceCatalog(pages={}, facts={})  # e.g. a foreign document slipped through
    answer = ask("What was my latest HbA1c?")
    assert answer.status is CopilotStatus.VALIDATION_FAILED
    assert "source_not_found" in {i.code for i in answer.issues}


def test_an_invented_value_from_a_model_is_withheld(fakes) -> None:
    reply = {"status": "answered", "claims": [{"text": "HbA1c: 8.4 %.", "citations": ["F1"]}]}
    answer = ask("What was my latest HbA1c?", generator=llm(reply))
    assert answer.status is CopilotStatus.VALIDATION_FAILED
    assert "unsupported_number" in {i.code for i in answer.issues}


def test_a_valid_model_answer_is_shown(fakes) -> None:
    reply = {
        "status": "answered",
        "claims": [{"text": "HbA1c: 6.2 % on 2022-09-13 (auto-checked, not verified).",
                    "citations": ["F1"]}],
    }  # fmt: skip
    answer = ask("What was my latest HbA1c?", generator=llm(reply))
    assert answer.status is CopilotStatus.ANSWERED
    assert answer.generator == "llm:mock:scripted-mock"
    assert answer.input_tokens and answer.output_tokens


def test_invalid_model_output_is_generation_failed(fakes) -> None:
    generator = LlmGenerator(ScriptedProvider(lambda r: "not json"))
    answer = ask("What was my latest HbA1c?", generator=generator)
    assert answer.status is CopilotStatus.GENERATION_FAILED and answer.claims == ()


class ExternalProvider(ScriptedProvider):
    @property
    def is_local(self) -> bool:
        return False


def test_an_external_provider_is_never_called_for_a_real_patient(fakes) -> None:
    fakes.synthetic = False
    provider = ExternalProvider(lambda r: "{}")
    answer = ask("What was my latest HbA1c?", generator=LlmGenerator(provider))
    assert answer.status is CopilotStatus.GENERATION_FAILED
    assert provider.calls == 0


def test_an_external_provider_may_be_used_for_a_synthetic_patient(fakes) -> None:
    reply = {"status": "insufficient_evidence", "claims": []}
    provider = ExternalProvider(lambda r: json.dumps(reply))
    answer = ask("What was my latest HbA1c?", generator=LlmGenerator(provider))
    assert provider.calls == 1
    assert answer.status is CopilotStatus.INSUFFICIENT_EVIDENCE


def test_missing_information_makes_a_partial_answer(fakes) -> None:
    fakes.result = tool_result(missing=("No usable TSH result with a date.",))
    answer = ask("What was my latest HbA1c and TSH?")
    assert answer.status is CopilotStatus.PARTIAL
    assert answer.missing == ("No usable TSH result with a date.",)


def test_no_facts_means_insufficient_evidence_without_generation(fakes) -> None:
    fakes.result = tool_result(facts=(), missing=("No usable HbA1c result with a date.",))
    provider = ScriptedProvider(lambda r: "{}")
    answer = ask("What was my latest HbA1c?", generator=LlmGenerator(provider))
    assert answer.status is CopilotStatus.INSUFFICIENT_EVIDENCE
    assert provider.calls == 0


def test_document_search_without_models_says_so(fakes) -> None:
    answer = ask("Which laboratory issued my reports?")
    assert answer.status is CopilotStatus.INSUFFICIENT_EVIDENCE
    assert "retrieval_unavailable" in answer.notes


def test_document_passages_are_cited_from_retrieval(fakes, monkeypatch) -> None:
    chunk = uuid.uuid4()
    passage = Evidence(
        document_id=uuid.UUID(DOC), page_number=1, chunk_id=chunk, chunk_type="text",
        section=None, excerpt="Fictional Lab", score=0.9, original_filename="r.pdf",
        document_date=None, parse_method="native", identity_check="unknown",
    )  # fmt: skip
    found = EvidenceAnswer(AnswerStatus.ANSWERED, (passage,), 0.9, RetrievalMode.LEXICAL, True)
    monkeypatch.setattr(copilot, "search_evidence", lambda *a, **k: found)
    fakes.catalog = SourceCatalog(
        pages={(DOC, 1): PAGE}, chunks={str(chunk): (DOC, 1, "Fictional Lab")}
    )
    retrieval = copilot.RetrievalDeps(RetrievalConfig(mode=RetrievalMode.LEXICAL), None, None)
    answer = ask("Which laboratory issued my reports?", retrieval=retrieval)
    assert answer.status is CopilotStatus.ANSWERED, [i.code for i in answer.issues]
    assert answer.cited == ("D1",)
    assert "identity not confirmed" in answer.claims[0].text
    assert "retrieval:lexical" in answer.activity


def test_logs_carry_no_question_or_evidence_text(fakes, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    ask(f"What was my latest HbA1c in {SECRET_QUESTION_WORD}?")
    ask(f"Should I stop my tablets in {SECRET_QUESTION_WORD}?")
    logged = caplog.text
    assert SECRET_QUESTION_WORD not in logged
    assert "6.2" not in logged and "Fictional Lab" not in logged
    assert "route=" in logged


def test_patient_id_must_be_a_uuid() -> None:
    with pytest.raises(TypeError):
        copilot.ask_copilot("not-a-uuid", "q")  # type: ignore[arg-type]


# --- tool argument validation (no database needed: validation happens first) ---------------------


@pytest.mark.parametrize(
    "arguments",
    [
        {"analyte": "HbA1c", "patient_id": str(uuid.uuid4())},  # a model cannot pick the patient
        {"analyte": "Unobtainium"},  # not in the reference data
        {"analyte": "HbA1c", "sql": "SELECT * FROM observations"},
        {"analyte": "HbA1c", "date_from": "2022-02-01", "date_to": "2021-01-01"},
        {"analyte": "HbA1c", "limit": 10_000},
    ],
)
def test_tool_arguments_are_validated(arguments) -> None:
    with pytest.raises(ValueError):
        tools.run_tool(PATIENT, "analyte_history", arguments)


def test_unknown_tools_and_missing_analytes_are_refused() -> None:
    with pytest.raises(ValueError):
        tools.run_tool(PATIENT, "execute_sql", {})
    with pytest.raises(ValueError):
        tools.run_tool(PATIENT, "latest_result", {})
    with pytest.raises(TypeError):
        tools.run_tool(str(PATIENT), "list_analytes", {})  # type: ignore[arg-type]


def test_an_uncited_foreign_source_fails_closed_and_shows_nothing(fakes) -> None:
    # A compromised tool returns another patient's fact. Even if no claim cites it, it must never
    # be shown (not even as "retrieved but not cited"), and no generator may see it.
    foreign_doc = str(uuid.uuid4())
    foreign = tools.ToolFact(
        observation(fact_id=str(uuid.uuid4()), document_id=foreign_doc, analyte_name="TSH"),
        "TSH\t2.0\tuIU/mL",
        "other.pdf",
        None,
        False,
    )
    fakes.result = tools.ToolResult(
        tools.ToolName.LIST_ANALYTES,
        tools.ToolArgs(),
        (foreign, *tool_result().facts),
        (),
        0,
    )
    provider = ScriptedProvider(lambda r: "{}")
    answer = ask("Which tests do I have results for?", generator=LlmGenerator(provider))
    assert answer.status is CopilotStatus.VALIDATION_FAILED
    assert answer.sources == () and answer.claims == ()
    assert provider.calls == 0
    assert "source_not_found" in {i.code for i in answer.issues}


def test_passages_of_reports_with_another_date_are_dropped(fakes, monkeypatch) -> None:
    def passage(day, chunk):
        return Evidence(
            document_id=uuid.UUID(DOC), page_number=1, chunk_id=chunk, chunk_type="text",
            section=None, excerpt="Fictional Lab", score=0.9, original_filename="r.pdf",
            document_date=day, parse_method="native", identity_check="match",
        )  # fmt: skip

    same, other, unknown = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    found = EvidenceAnswer(
        AnswerStatus.ANSWERED,
        (passage(dt.date(2021, 1, 1), other), passage(dt.date(2022, 9, 13), same),
         passage(None, unknown)),
        0.9, RetrievalMode.LEXICAL, True,
    )  # fmt: skip
    monkeypatch.setattr(copilot, "search_evidence", lambda *a, **k: found)
    fakes.catalog = SourceCatalog(
        pages={(DOC, 1): PAGE},
        chunks={str(c): (DOC, 1, "Fictional Lab") for c in (same, other, unknown)},
    )
    retrieval = copilot.RetrievalDeps(RetrievalConfig(mode=RetrievalMode.LEXICAL), None, None)
    answer = ask("Which laboratory issued my report dated 13/09/2022?", retrieval=retrieval)
    # The other-date page is dropped by the date filter; the unknown-date page shows neither the
    # asked date nor a matching report date, so the relevance gate drops it too (ADR-041).
    assert [s.chunk_id for s in answer.sources] == [str(same)]
    assert [s.source_id for s in answer.sources] == ["D1"]
    assert "date_filter" in answer.notes


def test_without_the_validator_a_fabricated_answer_would_be_shown(fakes, monkeypatch) -> None:
    # Sensitivity check: a validator that accepts everything lets the attack through, so the
    # evaluation's invalid-citation acceptance would rise above 0 for a broken implementation.
    from app.answering.citations import ValidationResult

    reply = {"status": "answered", "claims": [{"text": "HbA1c: 9.9 %.", "citations": ["F7"]}]}
    monkeypatch.setattr(copilot, "validate_answer", lambda *a, **k: ValidationResult((), ()))
    broken = ask("What was my latest HbA1c?", generator=llm(reply))
    assert broken.status is CopilotStatus.ANSWERED
    monkeypatch.undo()


# --- ADR-041: unrecognised test names and the passage relevance gate -----------------------------


def test_an_unrecognised_test_name_abstains_without_any_lookup(fakes, monkeypatch) -> None:
    def no_search(*args, **kwargs):
        raise AssertionError("no retrieval for an unrecognised test name")

    monkeypatch.setattr(copilot, "search_evidence", no_search)
    answer = ask("Show my ferritin results over time.")
    assert answer.status is CopilotStatus.INSUFFICIENT_EVIDENCE
    assert fakes.tool_calls == []  # never widened to "all tests"
    assert answer.sources == () and answer.claims == ()
    assert "unrecognised_test" in answer.notes
    assert answer.missing == ("Not recognised as a test name: ferritin.",)


def test_a_known_and_an_unknown_test_answer_partially(fakes) -> None:
    answer = ask("What were my latest HbA1c and ferritin results?")
    assert answer.status is CopilotStatus.PARTIAL
    assert [c[2] for c in fakes.tool_calls] == [{"analyte": "HbA1c"}]
    assert any("ferritin" in m for m in answer.missing)


def _passage_answer(excerpt: str):
    chunk = uuid.uuid4()
    passage = Evidence(
        document_id=uuid.UUID(DOC), page_number=1, chunk_id=chunk, chunk_type="text",
        section=None, excerpt=excerpt, score=0.9, original_filename="r.pdf",
        document_date=dt.date(2022, 9, 13), parse_method="native", identity_check="match",
    )  # fmt: skip
    found = EvidenceAnswer(AnswerStatus.ANSWERED, (passage,), 0.9, RetrievalMode.LEXICAL, True)
    return found, SourceCatalog(pages={(DOC, 1): PAGE}, chunks={str(chunk): (DOC, 1, excerpt)})


def test_a_page_that_does_not_mention_the_asked_subject_is_not_shown(fakes, monkeypatch) -> None:
    found, catalog = _passage_answer("Report Date: 13/09/2022\nVitamin D (25-OH)\t40.0")
    monkeypatch.setattr(copilot, "search_evidence", lambda *a, **k: found)
    fakes.catalog = catalog
    retrieval = copilot.RetrievalDeps(RetrievalConfig(mode=RetrievalMode.LEXICAL), None, None)
    answer = ask("What did my report dated 13/09/2022 say about vitamin K?", retrieval=retrieval)
    assert answer.status is CopilotStatus.INSUFFICIENT_EVIDENCE
    assert answer.sources == ()
    assert "passages_filtered" in answer.notes


def test_a_document_question_can_be_answered_by_its_date(fakes, monkeypatch) -> None:
    found, catalog = _passage_answer("Fictional Lab\nReport Date: 13/09/2022")
    monkeypatch.setattr(copilot, "search_evidence", lambda *a, **k: found)
    fakes.catalog = catalog
    retrieval = copilot.RetrievalDeps(RetrievalConfig(mode=RetrievalMode.LEXICAL), None, None)
    answer = ask("Which laboratory issued my report dated 13/09/2022?", retrieval=retrieval)
    assert answer.status is CopilotStatus.ANSWERED, [i.code for i in answer.issues]
    assert answer.cited == ("D1",)
