"""Unit tests for the ``index`` and ``ask`` commands (Milestone R1). No database, no model."""

import argparse
import datetime as dt
import uuid
from dataclasses import replace

import pytest

from app.core.config import Settings
from app.core.errors import RetrievalError
from app.main import build_parser, format_evidence, run_ask, run_index
from app.retrieval.evidence import AnswerStatus, Evidence, EvidenceAnswer, RetrievalMode

PATIENT = uuid.uuid4()


def test_parser_accepts_index_and_ask() -> None:
    args = build_parser().parse_args(["ask", "--patient", str(PATIENT), "What was my Hb?"])
    assert (args.command, args.patient, args.mode, args.question) == (
        "ask",
        PATIENT,
        "hybrid_rerank",
        "What was my Hb?",
    )
    args = build_parser().parse_args(["index", "--patient", str(PATIENT)])
    assert args.document is None
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ask", "--patient", "not-a-uuid", "q"])


def test_commands_refuse_to_run_without_approved_revisions() -> None:
    settings = Settings(embedding_model_revision=None, reranker_model_revision=None)
    with pytest.raises(RetrievalError) as caught:
        run_index(settings, argparse.Namespace(patient=PATIENT, document=None))
    assert caught.value.kind == "revision_not_configured"
    with pytest.raises(RetrievalError):
        run_ask(settings, argparse.Namespace(patient=PATIENT, mode="dense", question="q"))


def test_evidence_is_printed_with_its_citation_and_ocr_warning() -> None:
    passage = Evidence(
        document_id=uuid.uuid4(),
        page_number=2,
        chunk_id=uuid.uuid4(),
        chunk_type="table_rows",
        section="table 0, row 1",
        excerpt="Test: HbA1c | Result: 6.1 | Unit: %",
        score=7.25,
        original_filename="lab-report.pdf",
        document_date=dt.date(2024, 6, 5),
        parse_method="ocr",
        identity_check="unknown",
    )
    text = format_evidence(
        EvidenceAnswer(AnswerStatus.ANSWERED, (passage,), 7.25, RetrievalMode.HYBRID_RERANK, False)
    )
    assert "[lab-report.pdf, p. 2]" in text and "table 0, row 1, 05/06/2024" in text
    assert "OCR text - unverified" in text and "Result: 6.1" in text
    assert "not calibrated" in text
    assert "! identity not confirmed" in text  # ADR-036 R1: the label is printed
    matched = format_evidence(
        EvidenceAnswer(
            AnswerStatus.ANSWERED,
            (replace(passage, identity_check="match"),),
            7.25,
            RetrievalMode.HYBRID_RERANK,
            False,
        )
    )
    assert "identity not confirmed" not in matched


def test_refusals_and_abstentions_print_their_fixed_messages() -> None:
    refused = format_evidence(
        EvidenceAnswer(AnswerStatus.OUT_OF_SCOPE, (), None, RetrievalMode.LEXICAL, True)
    )
    assert "cannot give medical advice" in refused
    empty = format_evidence(
        EvidenceAnswer(AnswerStatus.INSUFFICIENT_EVIDENCE, (), None, RetrievalMode.LEXICAL, True)
    )
    assert "Not enough evidence" in empty
