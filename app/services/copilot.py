"""Evidence-grounded copilot: question -> cited answer (Phase 5; ARCHITECTURE §4.2, §9; ADR-040).

Flow (every step patient-scoped; the patient comes from the caller's session, never from text):

    route_question     safety gate first (refusal / unsupported: fixed text, nothing else runs),
                       then deterministic routing: route, intent, analytes, dates
    tools              allowlisted structured-fact tools (``copilot_tools``) for facts
    search_evidence    hybrid retrieval (R1) for document passages
    sources            numbered EvidenceItems: F<n> facts, D<n> passages (exact stored text)
    generator          extractive composer (default) or an LLM (structured JSON output)
    catalog            what exists for THIS patient: stored pages, chunks, usable facts
    validate_answer    deterministic citation validator; failure -> validation_failed, never shown

No generated claim is shown unless it passed validation. Logs carry ids, route, status, counts,
issue codes and timings only: never the question, evidence text, values or names.
"""

import datetime as dt
import logging
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.answering.citations import (
    CatalogFact,
    SourceCatalog,
    validate_answer,
    validate_sources,
)
from app.answering.generate import AnswerGenerator, ExtractiveGenerator, LlmGenerator
from app.answering.guardrails import REFUSAL_MESSAGES
from app.answering.model import (
    CitationIssue,
    CopilotStatus,
    DraftClaim,
    EvidenceItem,
    Intent,
    RefusalKind,
    Route,
    RoutePlan,
    SourceKind,
)
from app.answering.router import route_question
from app.answering.vocabulary import question_vocabulary
from app.core.errors import DocumentProcessingError
from app.extraction.reference import load_reference
from app.records.chunks import stored_chunks
from app.records.longitudinal import list_facts_by_ids
from app.records.repositories import get_patient, list_document_pages
from app.retrieval.evidence import AnswerStatus, RetrievalConfig
from app.retrieval.models import Embedder, Reranker
from app.retrieval.query import query_terms
from app.services import copilot_tools as tools
from app.services.search_evidence import search_evidence

logger = logging.getLogger(__name__)

MAX_ANALYTES = 3
INSUFFICIENT_MESSAGE = (
    "Not enough evidence in this patient's records to answer. Nothing is guessed."
)
VALIDATION_FAILED_MESSAGE = (
    "An answer was generated but failed citation checks, so it is not shown. The retrieved "
    "sources are listed below for you to inspect."
)
SOURCES_REJECTED_MESSAGE = (
    "The evidence gathered for this question failed MediMind's source checks, so nothing is shown."
)
GENERATION_FAILED_MESSAGE = (
    "The answer could not be generated. The retrieved sources are listed below for you to inspect."
)
NOTES = {
    "no_time_given": "No date was given, so the most recent result is shown.",
    "date_unreadable": "A date in the question could not be read exactly and was ignored.",
    "truncated": "Only the most recent results are shown; narrow the dates to see others.",
    "retrieval_unavailable": "Document search is not available (no approved models configured).",
    "threshold_not_calibrated": (
        "The document-search abstention threshold is not calibrated: weak matches may be shown."
    ),
    "awaiting_review": "Some results are awaiting review and are not used until reviewed.",
    "compare_needs_analyte": "Name a test to compare, for example 'compare my latest HbA1c'.",
    "date_filter": "Only document pages from reports dated as asked are shown.",
    "unrecognised_test": (
        "Part of the question was not recognised as a test name, so nothing was looked up for it; "
        "MediMind does not guess which test was meant."
    ),
    "ambiguous_test_name": (
        "That name can mean more than one test; each result is shown under its own test name."
    ),
    "passages_filtered": "Document pages that do not mention what was asked are not shown.",
}
UNRECOGNISED_MESSAGE = (
    "MediMind did not recognise the test name in this question, so it looked nothing up and "
    "guessed nothing. Try the name printed on the report (for example 'HbA1c' or 'TSH')."
)


@dataclass(frozen=True)
class RetrievalDeps:
    config: RetrievalConfig
    embedder: Embedder | None
    reranker: Reranker | None


@dataclass(frozen=True)
class CopilotAnswer:
    status: CopilotStatus
    plan: RoutePlan
    message: str | None  # fixed text for refusals, abstentions and failures
    claims: tuple[DraftClaim, ...] = ()  # validated claims only
    sources: tuple[EvidenceItem, ...] = field(default=(), repr=False)
    cited: tuple[str, ...] = ()
    missing: tuple[str, ...] = field(default=(), repr=False)
    issues: tuple[CitationIssue, ...] = ()
    activity: tuple[str, ...] = ()  # tools and retrieval steps that ran (fixed names)
    notes: tuple[str, ...] = ()  # fixed note codes (see NOTES)
    generator: str = ""
    generator_version: str = ""
    awaiting_review: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)

    @property
    def cited_sources(self) -> tuple[EvidenceItem, ...]:
        by_id = {s.source_id: s for s in self.sources}
        return tuple(by_id[i] for i in self.cited if i in by_id)


def _fact_calls(plan: RoutePlan) -> list[tuple[str, dict]]:
    """The allowlisted tool calls for a plan (deterministic; no model chooses them)."""
    analytes = list(plan.analytes[:MAX_ANALYTES])
    if plan.intent is Intent.LIST_ANALYTES:
        return [(tools.ToolName.LIST_ANALYTES, {})]
    if plan.intent is Intent.CONFLICTS:
        return [(tools.ToolName.CONFLICTS, {"analyte": a}) for a in analytes] or [
            (tools.ToolName.CONFLICTS, {})
        ]
    if plan.intent is Intent.COMPARE_PREVIOUS:
        return [(tools.ToolName.COMPARE_PREVIOUS, {"analyte": a}) for a in analytes]
    if plan.intent is Intent.ON_DATE:
        dates = {"date_from": plan.date_from, "date_to": plan.date_to}
        return [(tools.ToolName.RESULTS_ON_DATE, {"analyte": a, **dates}) for a in analytes] or [
            (tools.ToolName.RESULTS_ON_DATE, dates)
        ]
    if plan.intent is Intent.HISTORY:
        dates = {"date_from": plan.date_from, "date_to": plan.date_to}
        return [(tools.ToolName.ANALYTE_HISTORY, {"analyte": a, **dates}) for a in analytes] or [
            (tools.ToolName.ANALYTE_HISTORY, dates)
        ]
    if plan.intent is Intent.LATEST:
        return [(tools.ToolName.LATEST_RESULT, {"analyte": a}) for a in analytes]
    return []


def _catalog(patient_id: uuid.UUID, items: Sequence[EvidenceItem]) -> SourceCatalog:
    """What exists for this patient, read through patient-scoped repositories only."""
    pages: dict[tuple[str, int], str] = {}
    for document_id in dict.fromkeys(i.document_id for i in items):
        try:
            stored = list_document_pages(patient_id, uuid.UUID(document_id))
        except (DocumentProcessingError, ValueError):
            continue  # another patient's or unknown document: its pages stay absent
        for page in stored:
            pages[(document_id, page.page_number)] = page.text
    fact_ids = []
    for item in items:
        try:
            if item.fact_id:
                fact_ids.append(uuid.UUID(item.fact_id))
        except ValueError:
            continue
    facts = {
        str(r.id): CatalogFact(str(r.document_id), r.page_number, r.review_status, r.superseded)
        for r in list_facts_by_ids(patient_id, fact_ids)
    }
    chunk_ids = []
    for item in items:
        try:
            if item.chunk_id:
                chunk_ids.append(uuid.UUID(item.chunk_id))
        except ValueError:
            continue
    chunks = {
        str(c.chunk_id): (str(c.document_id), c.page_number, c.content)
        for c in stored_chunks(patient_id, chunk_ids).values()
    }
    return SourceCatalog(pages=pages, facts=facts, chunks=chunks)


def _passages(answer, start: int) -> list[EvidenceItem]:  # noqa: ANN001 (EvidenceAnswer)
    return [
        EvidenceItem(
            source_id=f"D{number}",
            kind=SourceKind.PASSAGE,
            document_id=str(p.document_id),
            page_number=p.page_number,
            excerpt=p.excerpt,
            filename=p.original_filename,
            document_date=p.document_date,
            identity_check=p.identity_check,
            parse_method=p.parse_method,
            chunk_id=str(p.chunk_id),
            score=p.score,
        )
        for number, p in enumerate(answer.passages, start=start)
    ]


def _on_asked_date(passages: list[EvidenceItem], plan: RoutePlan) -> list[EvidenceItem]:
    """ARCHITECTURE §9.3 filters: for one exact asked date, keep pages of reports dated that day.

    Pages whose report date is unknown are kept (they cannot be excluded on date); pages of
    reports with a different known date are dropped.
    """
    if plan.date_from is None or plan.date_from != plan.date_to:
        return passages
    return [p for p in passages if p.document_date in (None, plan.date_from)]


def _renumber(passages: list[EvidenceItem]) -> list[EvidenceItem]:
    return [p.model_copy(update={"source_id": f"D{i}"}) for i, p in enumerate(passages, start=1)]


def _test_names(plan: RoutePlan) -> list[str]:
    """Every name (printed alias or lay term) of the plan's tests, lower-case."""
    single, ambiguous = question_vocabulary(dict(load_reference().aliases))
    names = [term for term, canonical in single.items() if canonical in plan.analytes]
    names += [term for term, options in ambiguous.items() if set(options) & set(plan.analytes)]
    return names


# Words about the document itself (who issued it, what kind it is). A page can answer them without
# printing the word, so a written date may stand in for them; subject words never.
_DOCUMENT_WORDS = frozenset(
    """laboratory lab labs issued issuing issuer hospital clinic centre center diagnostics doctor
    physician consultant referred referring address name sample collected collection signed
    consultation note notes letter summary pathology""".split()
)


def _document_only(plan: RoutePlan) -> bool:
    """No test and no subject words: the question only asks about the document itself."""
    return not plan.analytes and not any(
        set(p.split()) - _DOCUMENT_WORDS for p in plan.unrecognised
    )


def _key_terms(question: str, plan: RoutePlan) -> tuple[str, ...]:
    """What a document page must mention to be shown (ADR-041 relevance gate). Empty: no gate.

    - The question names tests: one of their names (printed or everyday).
    - It has other subject words (e.g. an unknown test name): one of those words.
    - It only asks about the document (issuer, kind): a written date, if the question has one.
    """
    if plan.analytes:
        return tuple(_test_names(plan))
    subject = tuple(p for p in plan.unrecognised if set(p.split()) - _DOCUMENT_WORDS)
    if subject:
        return subject
    # Only document words: they need not be printed, so a written date (if any) is the gate.
    return tuple(t for t in query_terms(question) if any(ch.isdigit() for ch in t) and len(t) >= 6)


def _mentions(passage: EvidenceItem, terms: tuple[str, ...]) -> bool:
    text = passage.excerpt.casefold()
    return any(re.search(r"(?<!\w)" + re.escape(t.casefold()) + r"(?!\w)", text) for t in terms)


def _quote_terms(question: str, plan: RoutePlan) -> tuple[str, ...]:
    """Words that select which passage lines are quoted.

    A question naming a test uses that test's printed names from the reference data (and any date
    in the question); otherwise the question's content words of three or more characters.
    """
    terms = [t for t in query_terms(question) if len(t) >= 3]
    if not plan.analytes:
        return tuple(terms)
    dates = [t for t in terms if any(ch.isdigit() for ch in t)]
    return (*_test_names(plan), *dates)


def _external_allowed(patient_id: uuid.UUID, generator: AnswerGenerator) -> bool:
    """ADR-005 / ARCHITECTURE §12: an external provider is used only for synthetic patients."""
    if not isinstance(generator, LlmGenerator) or generator.provider.is_local:
        return True
    return get_patient(patient_id).is_synthetic is True


def ask_copilot(
    patient_id: uuid.UUID,
    question: str,
    *,
    generator: AnswerGenerator | None = None,
    retrieval: RetrievalDeps | None = None,
    today: dt.date | None = None,
) -> CopilotAnswer:
    """A validated, cited answer about one patient's records, or a fixed refusal / abstention."""
    if not isinstance(patient_id, uuid.UUID):
        raise TypeError("patient_id must be a UUID")
    generator = generator or ExtractiveGenerator()
    started = time.perf_counter()
    timings: dict[str, int] = {}
    reference = load_reference()
    plan = route_question(
        question if isinstance(question, str) else "",
        aliases=reference.aliases,
        today=today or dt.date.today(),
    )
    notes = [
        code
        for code in ("no_time_given", "date_unreadable", "ambiguous_test_name")
        if code in plan.reasons
    ]

    def finish(status: CopilotStatus, message: str | None, **extra: object) -> CopilotAnswer:
        timings["total"] = round((time.perf_counter() - started) * 1000)
        answer = CopilotAnswer(
            status=status,
            plan=plan,
            message=message,
            notes=tuple(notes),
            timings_ms=timings,
            **extra,  # type: ignore[arg-type]
        )
        logger.info(
            "Copilot answer: patient_id=%s route=%s intent=%s status=%s sources=%d cited=%d "
            "issues=%s generator=%s total_ms=%d",
            patient_id,
            plan.route,
            plan.intent,
            status,
            len(answer.sources),
            len(answer.cited),
            ",".join(sorted({i.code for i in answer.issues})) or "-",
            answer.generator or "-",
            timings["total"],
        )
        return answer

    if plan.route is Route.REFUSAL:
        return finish(CopilotStatus.REFUSED, REFUSAL_MESSAGES[plan.refusal or RefusalKind.INVALID])
    if plan.route is Route.UNSUPPORTED:
        return finish(CopilotStatus.UNSUPPORTED, REFUSAL_MESSAGES[RefusalKind.GENERAL_KNOWLEDGE])

    if plan.unrecognised and not plan.analytes and plan.route is not Route.DOCUMENT_SEARCH:
        # An unknown test name: nothing is looked up and nothing is widened to "all tests".
        notes.append("unrecognised_test")
        return finish(
            CopilotStatus.INSUFFICIENT_EVIDENCE,
            UNRECOGNISED_MESSAGE,
            missing=(f"Not recognised as a test name: {', '.join(plan.unrecognised)}.",),
        )

    activity: list[str] = []
    missing: list[str] = []
    if plan.unrecognised and plan.analytes and plan.route is not Route.COMBINED:
        notes.append("unrecognised_test")
        missing.append(f"Not recognised as a test name: {', '.join(plan.unrecognised)}.")
    facts: list[tools.ToolFact] = []
    awaiting = 0
    step = time.perf_counter()
    if plan.route in (Route.STRUCTURED_FACTS, Route.LONGITUDINAL, Route.COMBINED):
        calls = _fact_calls(plan)
        if plan.intent is Intent.COMPARE_PREVIOUS and not calls:
            notes.append("compare_needs_analyte")
        for name, arguments in calls:
            result = tools.run_tool(patient_id, name, arguments)
            activity.append(f"tool:{result.tool}")
            facts.extend(f for f in result.facts if f not in facts)
            missing.extend(result.missing)
            awaiting = max(awaiting, result.awaiting_review)
            if result.truncated and "truncated" not in notes:
                notes.append("truncated")
    timings["tools"] = round((time.perf_counter() - step) * 1000)
    if awaiting:
        notes.append("awaiting_review")
    items = tools.to_evidence(facts)

    if plan.route in (Route.DOCUMENT_SEARCH, Route.COMBINED):
        if retrieval is None:
            notes.append("retrieval_unavailable")
        else:
            step = time.perf_counter()
            found = search_evidence(
                patient_id,
                question,
                config=retrieval.config,
                embedder=retrieval.embedder if retrieval.config.mode.uses_dense else None,
                reranker=retrieval.reranker if retrieval.config.mode.uses_reranker else None,
            )
            timings["retrieval"] = round((time.perf_counter() - step) * 1000)
            activity.append(f"retrieval:{retrieval.config.mode}")
            if not found.threshold_calibrated:
                notes.append("threshold_not_calibrated")
            if found.status is AnswerStatus.OUT_OF_SCOPE:  # the R1 gate (a subset of ours)
                return finish(CopilotStatus.REFUSED, REFUSAL_MESSAGES[RefusalKind.ADVICE])
            passages = _passages(found, 1) if found.status is AnswerStatus.ANSWERED else []
            dated = _on_asked_date(passages, plan)
            if len(dated) != len(passages):
                notes.append("date_filter")
            terms = _key_terms(question, plan)
            asked_day = plan.date_from if plan.date_from == plan.date_to else None
            relevant = [
                p for p in dated
                if not terms or _mentions(p, terms)
                or (_document_only(plan) and asked_day is not None and p.document_date == asked_day)
            ]  # fmt: skip
            if len(relevant) != len(dated):
                notes.append("passages_filtered")
            dated = relevant
            items += _renumber(dated)
            if not dated and plan.route is Route.DOCUMENT_SEARCH:
                missing.append("No document passage matched the question closely enough.")

    common = {
        "sources": tuple(items),
        "missing": tuple(dict.fromkeys(missing)),
        "activity": tuple(activity),
        "awaiting_review": awaiting,
    }
    if not items:
        return finish(CopilotStatus.INSUFFICIENT_EVIDENCE, INSUFFICIENT_MESSAGE, **common)
    # Fail closed: every gathered source (cited or not) must be this patient's stored, usable
    # evidence before any generator or screen sees it. A failure here is a tool/retrieval fault.
    step = time.perf_counter()
    catalog = _catalog(patient_id, items)
    rejected = validate_sources(items, catalog)
    timings["source_check"] = round((time.perf_counter() - step) * 1000)
    if rejected:
        logger.warning(
            "Copilot sources rejected: patient_id=%s codes=%s",
            patient_id,
            ",".join(sorted({i.code for i in rejected})),
        )
        return finish(
            CopilotStatus.VALIDATION_FAILED,
            SOURCES_REJECTED_MESSAGE,
            issues=rejected,
            activity=common["activity"],
        )
    if not _external_allowed(patient_id, generator):
        return finish(
            CopilotStatus.GENERATION_FAILED,
            GENERATION_FAILED_MESSAGE,
            generator=generator.label,
            **common,
        )

    step = time.perf_counter()
    generation = generator.generate(
        question, plan, items, missing=common["missing"], terms=_quote_terms(question, plan)
    )
    timings["generate"] = round((time.perf_counter() - step) * 1000)
    meta = {
        "generator": generation.generator,
        "generator_version": generation.version,
        "input_tokens": generation.input_tokens,
        "output_tokens": generation.output_tokens,
    }
    draft = generation.draft
    if draft is None:
        logger.warning(
            "Copilot generation failed: patient_id=%s kind=%s", patient_id, generation.error_kind
        )
        return finish(CopilotStatus.GENERATION_FAILED, GENERATION_FAILED_MESSAGE, **common, **meta)

    step = time.perf_counter()
    result = validate_answer(draft, items, catalog, aliases=reference.aliases)
    timings["validate"] = round((time.perf_counter() - step) * 1000)
    if not result.valid:
        return finish(
            CopilotStatus.VALIDATION_FAILED,
            VALIDATION_FAILED_MESSAGE,
            issues=result.issues,
            **common,
            **meta,
        )
    if draft.status == "insufficient_evidence":
        return finish(CopilotStatus.INSUFFICIENT_EVIDENCE, INSUFFICIENT_MESSAGE, **common, **meta)
    partial = draft.status == "partial" or bool(common["missing"])
    return finish(
        CopilotStatus.PARTIAL if partial else CopilotStatus.ANSWERED,
        None,
        claims=draft.claims,
        cited=result.cited,
        **common,
        **meta,
    )


def source_page(patient_id: uuid.UUID, document_id: str, page_number: int) -> str | None:
    """The full stored text of one cited page of this patient (for page navigation), or None.

    Another patient's document is ``document_not_found`` in the repository and returns None.
    """
    try:
        pages = list_document_pages(patient_id, uuid.UUID(document_id))
    except (DocumentProcessingError, ValueError):
        return None
    return next((p.text for p in pages if p.page_number == page_number), None)
