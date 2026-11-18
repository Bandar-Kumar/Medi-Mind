"""MediMind's single entry point: load settings, set up logging, run a command, return an exit code.

Exit codes: 0 = success, 1 = health check failed or unexpected error, 2 = invalid configuration.
Run with: ``uv run python -m app health``
"""

import argparse
import logging
import sys
import uuid
from collections.abc import Callable

from app import __version__
from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError, MediMindError
from app.core.health import format_report, overall_status, run_health_checks
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG_ERROR = 2


def run_health(settings: Settings) -> int:
    results = run_health_checks(settings)
    print(format_report(results, settings), flush=True)
    status = overall_status(results)
    logger.info("Health check finished: %s", status)
    return EXIT_FAILURE if status == "fail" else EXIT_OK


def run_migrate(settings: Settings) -> int:
    """Upgrade the configured database to the latest schema revision (upgrade only).

    Database errors propagate to ``main``, which logs them sanitized (no driver text).
    """
    from app.records.migrations import run_upgrade

    head = run_upgrade(settings)
    print(f"Database schema is at revision {head} (latest).", flush=True)
    return EXIT_OK


COMMANDS: dict[str, Callable[[Settings], int]] = {"health": run_health, "migrate": run_migrate}


def run_index(settings: Settings, args: argparse.Namespace) -> int:
    """Index one document, or every parsed and not yet indexed document of the patient (R1).

    Needs the owner-approved embedding model revision and the model in the local cache; nothing
    is downloaded (ADR-035).
    """
    from app.retrieval.models import BgeEmbedder
    from app.services.index_document import IndexResult, index_document, index_patient

    def show(result: IndexResult) -> None:
        print(
            f"Indexed document {result.document_id}: {result.chunks} chunks "
            f"({result.text_chunks} text, {result.table_chunks} table rows) "
            f"in {result.duration_ms} ms",
            flush=True,
        )

    embedder = BgeEmbedder(settings.embedding_model_revision)
    if args.document is not None:  # a gate refusal propagates: exit 1 with its kind
        show(index_document(settings, embedder, args.patient, args.document))
        print("1 document(s) indexed.", flush=True)
        return EXIT_OK
    outcomes = index_patient(settings, embedder, args.patient)
    for outcome in outcomes:
        if outcome.result is not None:
            show(outcome.result)
        else:
            print(f"Not indexed: document {outcome.document_id}: {outcome.reason}", flush=True)
    indexed = sum(1 for outcome in outcomes if outcome.result is not None)
    print(
        f"{indexed} document(s) indexed, {len(outcomes) - indexed} not indexed "
        "(blocked by the indexing gate or already done).",
        flush=True,
    )
    return EXIT_OK


def run_identity(settings: Settings, args: argparse.Namespace) -> int:
    """Check that one parsed document belongs to the patient (Task 2.8, ADR-036).

    Prints the outcome and fixed reason codes only: never the name, the date of birth or text.
    A document already checked is reported and not checked again (ADR-036 A2).
    """
    from app.core.errors import DocumentProcessingError
    from app.services.check_identity import ALREADY_CHECKED, check_document_identity

    try:
        result = check_document_identity(args.patient, args.document)
    except DocumentProcessingError as exc:
        if exc.kind != ALREADY_CHECKED:
            raise
        print(f"Document {args.document} is already identity-checked; not repeated.", flush=True)
        return EXIT_OK
    print(
        f"Identity check of document {result.document_id}: {result.outcome} "
        f"(rules v{result.rules_version}); status {result.processing_status}",
        flush=True,
    )
    print(f"Reasons: {', '.join(result.reason_codes)}", flush=True)
    if result.outcome == "mismatch":
        print("This document is flagged for review and will not be indexed.", flush=True)
    elif result.outcome == "unknown":
        print("Identity not confirmed: evidence from this document is labelled.", flush=True)
    return EXIT_OK


def run_classify(settings: Settings, args: argparse.Namespace) -> int:
    """Classify one parsed document with the versioned rules (Task 2.7, ADR-036).

    A document that is already classified is reported and not classified again (ADR-036 A2).
    Other errors propagate to ``main``. Prints identifiers, the type, the rule score and rule ids;
    never page text.
    """
    from app.core.errors import DocumentProcessingError
    from app.services.classify_document import ALREADY_CLASSIFIED, classify_document

    try:
        result = classify_document(args.patient, args.document)
    except DocumentProcessingError as exc:
        if exc.kind != ALREADY_CLASSIFIED:
            raise
        print(f"Document {args.document} is already classified; not repeated.", flush=True)
        return EXIT_OK
    print(
        f"Classified document {result.document_id}: {result.doc_type} "
        f"(rule score {result.score:.3f}, not a probability; rules v{result.rules_version})",
        flush=True,
    )
    print(f"Rules: {', '.join(result.rule_ids) or 'none'}", flush=True)
    return EXIT_OK


def run_ask(settings: Settings, args: argparse.Namespace) -> int:
    """Print page-cited evidence for a question about one patient's documents (R1, no LLM)."""
    from app.retrieval.evidence import RetrievalConfig, RetrievalMode
    from app.retrieval.models import BgeEmbedder, MiniLmReranker
    from app.services.search_evidence import search_evidence

    config = RetrievalConfig(
        mode=RetrievalMode(args.mode),
        candidates=settings.retrieval_candidates,
        rrf_k=settings.retrieval_rrf_k,
        rerank_top=settings.retrieval_rerank_top,
        top_k=settings.retrieval_top_k,
        abstain_threshold=settings.retrieval_abstain_threshold,
    )
    embedder = BgeEmbedder(settings.embedding_model_revision) if config.mode.uses_dense else None
    reranker = (
        MiniLmReranker(settings.reranker_model_revision) if config.mode.uses_reranker else None
    )
    answer = search_evidence(
        args.patient, args.question, config=config, embedder=embedder, reranker=reranker
    )
    print(format_evidence(answer), flush=True)
    return EXIT_OK  # an abstention or refusal is a valid answer, not a failure


def format_evidence(answer: object) -> str:
    """Human-readable evidence: ``[filename, p. N]`` before each verbatim excerpt."""
    from app.retrieval.evidence import AnswerStatus, EvidenceAnswer

    assert isinstance(answer, EvidenceAnswer)
    lines = [f"Status: {answer.status.value} (mode: {answer.mode.value})"]
    if answer.message:
        lines.append(answer.message)
    if not answer.threshold_calibrated and answer.status is AnswerStatus.ANSWERED:
        lines.append("Note: the abstention threshold is not calibrated; low scores still show.")
    for number, passage in enumerate(answer.passages, start=1):
        date = (
            passage.document_date.strftime("%d/%m/%Y") if passage.document_date else "date unknown"
        )
        where = f"[{passage.original_filename}, p. {passage.page_number}]"
        detail = f"{passage.section}, " if passage.section else ""
        lines.append("")
        lines.append(f"{number}. {where} ({detail}{date}; score {passage.score:.4f})")
        if passage.ocr_warning:
            lines.append(f"   ! {passage.ocr_warning}")
        if passage.identity_warning:
            lines.append(f"   ! {passage.identity_warning}")
        lines.extend(f"   {line}" for line in passage.excerpt.splitlines())
    return "\n".join(lines)


def run_extract(settings: Settings, args: argparse.Namespace) -> int:
    """Extract lab observations from one document, or from every document of the patient.

    Prints identifiers, outcomes, counts and fixed kinds only; never values or page text. A
    blocked or already extracted document is reported and the next one still runs (``--all``).
    """
    from app.core.errors import DocumentProcessingError
    from app.services.extract_document import extract_document
    from app.services.library import list_documents

    documents = (
        [args.document]
        if args.document is not None
        else [d.id for d in reversed(list_documents(args.patient))]
    )
    done = 0
    for document_id in documents:
        try:
            result = extract_document(settings, args.patient, document_id, reextract=args.reextract)
        except DocumentProcessingError as exc:
            if args.document is not None:
                raise
            print(f"Not extracted: document {document_id}: {exc.kind}", flush=True)
            continue
        done += 1
        print(
            f"Document {result.document_id}: {result.outcome}, {result.facts} fact(s), "
            f"{result.needs_review} need review, {result.superseded} superseded, "
            f"conflicts +{result.conflicts_opened}/-{result.conflicts_closed}"
            f"{', now processed' if result.completed else ''}",
            flush=True,
        )
    print(f"{done} of {len(documents)} document(s) extracted.", flush=True)
    return EXIT_OK


def run_facts(settings: Settings, args: argparse.Namespace) -> int:
    """List a document's current facts: ids, page, status and reason codes.

    Values and source text are printed only with ``--show-values`` (local terminal; never logged).
    'validated' means auto-checked only: not verified by a person, not clinically validated.
    """
    from app.services.facts import document_facts

    views = document_facts(args.patient, args.document)
    for view in views:
        fact = view.fact
        line = (
            f"{fact.id} p{fact.page_number} {fact.review_status:<12} "
            f"{','.join(fact.review_reasons) or '-'}"
        )
        if args.show_values:
            line += (
                f" | {view.original['analyte']}: {view.original['value']} "
                f"{view.original['unit'] or ''} [{view.source}]"
            )
        print(line, flush=True)
    print(f"{len(views)} current fact(s). 'validated' = auto-checked, not verified.", flush=True)
    return EXIT_OK


def run_seed_reference(settings: Settings) -> int:
    """Copy the versioned reference data (analytes, aliases, conversions) into the database."""
    from app.services.reference_data import seed_reference

    result, version = seed_reference()
    print(
        f"Reference data {version}: {result.analytes_added} analyte(s), "
        f"{result.aliases_added} alias(es), {result.conversions_added} conversion(s) added.",
        flush=True,
    )
    return EXIT_OK


ARG_COMMANDS: dict[str, Callable[[Settings, argparse.Namespace], int]] = {
    "index": run_index,
    "ask": run_ask,
    "classify": run_classify,
    "identity": run_identity,
    "extract": run_extract,
    "facts": run_facts,
}
COMMANDS["seed-reference"] = run_seed_reference


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app", description="MediMind command line.")
    parser.add_argument("--version", action="version", version=f"MediMind {__version__}")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("health", help="check that the environment is set up correctly")
    subcommands.add_parser(
        "migrate",
        help="upgrade the database schema to the latest migration (downgrades: alembic CLI only)",
    )
    index = subcommands.add_parser(
        "index", help="chunk and embed parsed documents for evidence search (R1)"
    )
    index.add_argument("--patient", type=uuid.UUID, required=True, help="patient id")
    index.add_argument("--document", type=uuid.UUID, help="one document id (default: all)")
    classify = subcommands.add_parser(
        "classify", help="classify one parsed document by rules (lab_report / other / unknown)"
    )
    classify.add_argument("--patient", type=uuid.UUID, required=True, help="patient id")
    classify.add_argument("--document", type=uuid.UUID, required=True, help="document id")
    identity = subcommands.add_parser(
        "identity", help="check that one parsed document belongs to the patient (outcome only)"
    )
    identity.add_argument("--patient", type=uuid.UUID, required=True, help="patient id")
    identity.add_argument("--document", type=uuid.UUID, required=True, help="document id")
    subcommands.add_parser(
        "seed-reference", help="copy the versioned analyte reference data into the database"
    )
    extract = subcommands.add_parser(
        "extract", help="extract lab observations with exact source spans (Phase 3)"
    )
    extract.add_argument("--patient", type=uuid.UUID, required=True, help="patient id")
    extract.add_argument("--document", type=uuid.UUID, help="one document id (default: all)")
    extract.add_argument(
        "--reextract",
        action="store_true",
        help="replace facts from an older extractor version (refused if any fact was reviewed)",
    )
    facts = subcommands.add_parser("facts", help="list a document's extracted facts (ids, status)")
    facts.add_argument("--patient", type=uuid.UUID, required=True, help="patient id")
    facts.add_argument("--document", type=uuid.UUID, required=True, help="document id")
    facts.add_argument(
        "--show-values", action="store_true", help="also print values (local terminal only)"
    )
    ask = subcommands.add_parser(
        "ask", help="show page-cited evidence from one patient's documents (R1, no LLM)"
    )
    ask.add_argument("--patient", type=uuid.UUID, required=True, help="patient id")
    ask.add_argument(
        "--mode",
        choices=("dense", "lexical", "hybrid", "hybrid_rerank"),
        default="hybrid_rerank",
        help="retrieval mode (default: hybrid_rerank)",
    )
    ask.add_argument("question", help="the question (never logged)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK

    # Settings come first: logging cannot be configured until we know the log level.
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(f"Configuration error. {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    configure_logging(settings.log_level, settings.log_format)

    try:
        if args.command in ARG_COMMANDS:
            return ARG_COMMANDS[args.command](settings, args)
        return COMMANDS[args.command](settings)
    except MediMindError as exc:  # expected failure: clear message, no traceback
        logger.error("%s", exc)
        return EXIT_FAILURE
    except Exception as exc:
        # Database errors: log only the sanitized category, class name and SQLSTATE. Their raw
        # messages and tracebacks can contain driver text, so they are never logged.
        from app.records.db import describe_db_error, is_database_error

        if is_database_error(exc):
            logger.error(
                "Database error while running %r: %s", args.command, describe_db_error(exc)
            )
            return EXIT_FAILURE
        # Anything else is a bug: log the full traceback so it can be fixed.
        logger.exception("Unexpected error while running %r", args.command)
        return EXIT_FAILURE
