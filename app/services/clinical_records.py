"""Clinical records for the UI and the records Q&A (Phase 6; docs/PHASE_6_PLAN.md §7–§9).

Thin orchestration over the patient-scoped repository and the pure ``app.clinical`` modules:

- ``clinical_facts``      stored facts as ``ClinicalFact`` (a correction's values applied; the
  source document's identity outcome carried on every fact; identity ``mismatch`` facts, which
  the extraction gate never stores, are dropped defensively);
- ``document_comparison`` two documents compared (``clinical-compare-1``);
- ``clinical_conflicts``  derived conflicts, never stored, never resolved;
- ``dispatch`` / ``ask_records`` the question dispatcher (acceptance fix, ADR-042 amendment):

  1. the frozen Phase 5 input gate runs first and is never changed;
  2. injection, invalid and general-knowledge outcomes, and every question the clinical router
     does not claim, go to the unchanged Phase 5 copilot (``PHASE5``);
  3. a claimed question that is a plain record lookup on the allow-list (``plain_lookup``) is
     answered from clinical facts (``CLINICAL``), also when the gate refused it as advice or
     diagnosis — only these exact forms lift such a refusal;
  4. any other claimed question keeps its Phase 5 refusal if it had one (``PHASE5``), and is
     otherwise refused with the fixed clinical refusal (``CLINICAL_REFUSED``): if uncertain,
     refuse.

  Before a clinical answer is shown, every citation is checked against the stored page text of
  this patient (``citation_issues``); any issue withholds the whole answer.

Logs carry identifiers, intents, statuses, codes and counts only.
"""

import datetime as dt
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.answering.guardrails import input_gate
from app.answering.model import RefusalKind
from app.clinical.answering import (
    ClinicalAnswer,
    answer,
    citation_issues,
    plain_lookup,
    refusal_answer,
    route,
    withheld_answer,
)
from app.clinical.compare import (
    ClinicalConflict,
    ClinicalFact,
    DocumentComparison,
    apply_correction,
    compare_documents,
    detect_conflicts,
)
from app.records.clinical import (
    ClinicalDocumentView,
    ClinicalFactView,
    list_clinical_documents,
    list_clinical_facts,
    stored_page_texts,
)

logger = logging.getLogger(__name__)

EXEMPTABLE = frozenset({RefusalKind.ADVICE, RefusalKind.DIAGNOSIS})
PHASE5: Final = "phase5"
CLINICAL: Final = "clinical"
CLINICAL_REFUSED: Final = "clinical_refused"
MISMATCH: Final = "mismatch"


def to_fact(view: ClinicalFactView) -> ClinicalFact:
    fact = ClinicalFact(
        id=str(view.id),
        fact_type=view.fact_type,
        document_id=str(view.document_id),
        document_name=view.document_name,
        clinical_doc_type=view.clinical_doc_type,
        document_date=view.document_date,
        page_number=view.page_number,
        source_excerpt=view.source_excerpt,
        subject=view.subject,
        negated=view.negated,
        date=view.date,
        date_precision=view.date_precision,
        date_source=view.date_source,
        review_status=view.review_status,
        review_reasons=view.review_reasons,
        values=dict(view.values),
        identity_check=view.identity_check,
        char_start=view.char_start,
        char_end=view.char_end,
    )
    if view.review_status == "corrected":
        return apply_correction(fact, view.corrected_values)
    return fact


def clinical_facts(patient_id: uuid.UUID) -> tuple[ClinicalFact, ...]:
    """The patient's clinical facts; identity-``mismatch`` facts never pass (defence in depth)."""
    views = list_clinical_facts(patient_id)
    dropped = sum(v.identity_check == MISMATCH for v in views)
    if dropped:
        logger.warning(
            "Clinical facts of identity-mismatch documents dropped: patient_id=%s count=%d",
            patient_id,
            dropped,
        )
    return tuple(to_fact(v) for v in views if v.identity_check != MISMATCH)


def clinical_documents(patient_id: uuid.UUID) -> tuple[ClinicalDocumentView, ...]:
    return list_clinical_documents(patient_id)


def document_comparison(
    patient_id: uuid.UUID, document_a: uuid.UUID, document_b: uuid.UUID
) -> DocumentComparison:
    """Compare two of this patient's documents (another patient's id finds no facts)."""
    return compare_documents(clinical_facts(patient_id), str(document_a), str(document_b))


def clinical_conflicts(patient_id: uuid.UUID) -> tuple[ClinicalConflict, ...]:
    return detect_conflicts(clinical_facts(patient_id))


@dataclass(frozen=True)
class RecordsReply:
    """Exactly one of ``clinical`` (a Phase 6 answer) or ``copilot`` (the Phase 5 answer)."""

    clinical: ClinicalAnswer | None = None
    copilot: object | None = None  # app.services.copilot.CopilotAnswer


def dispatch(question: object) -> str:
    """``PHASE5``, ``CLINICAL`` or ``CLINICAL_REFUSED`` for a question (see the module doc)."""
    if not isinstance(question, str):
        return PHASE5
    refusal, general = input_gate(question)
    if refusal in (RefusalKind.INJECTION, RefusalKind.INVALID) or general:
        return PHASE5
    plan = route(question)
    if not plan.claimed:
        return PHASE5
    if plain_lookup(question) is not None and (refusal is None or refusal in EXEMPTABLE):
        return CLINICAL
    if refusal is not None:
        return PHASE5  # its Phase 5 refusal stands
    return CLINICAL_REFUSED


def clinical_route(question: str) -> bool:
    """True when the dispatcher would answer this question from clinical facts."""
    return dispatch(question) == CLINICAL


def checked_answer(patient_id: uuid.UUID, result: ClinicalAnswer) -> ClinicalAnswer:
    """The answer when every citation is an exact slice of this patient's stored page, else the
    fixed withheld answer (never repaired)."""
    keys = {
        (uuid.UUID(c.document_id), c.page_number)
        for section in result.sections
        for item in section.items
        for c in item.citations
    }
    pages = stored_page_texts(patient_id, sorted(keys)) if keys else {}
    issues = citation_issues(result, lambda document, page: pages.get((document, page)))
    if not issues:
        return result
    logger.warning(
        "Clinical answer withheld: patient_id=%s intent=%s issues=%s",
        patient_id,
        result.intent,
        ",".join(issues),
    )
    return withheld_answer(result)


def ask_records(
    patient_id: uuid.UUID,
    question: str,
    *,
    copilot: Callable[[uuid.UUID, str], object],
    as_of: dt.date | None = None,
) -> RecordsReply:
    """Answer from clinical facts, refuse, or hand the question to the Phase 5 copilot.

    ``copilot`` is the Phase 5 path (``ask_copilot`` with its dependencies bound by the caller);
    it receives every ``PHASE5`` question, its refusals included, exactly as before Phase 6.
    """
    if not isinstance(patient_id, uuid.UUID):
        raise TypeError("patient_id must be a UUID")
    decision = dispatch(question)
    if decision == PHASE5:
        return RecordsReply(copilot=copilot(patient_id, question))
    plan = route(question)
    if decision == CLINICAL_REFUSED:
        result = refusal_answer(plan.intent)
    else:
        result = answer(plan, clinical_facts(patient_id), as_of=as_of or dt.date.today())
        result = checked_answer(patient_id, result)
    logger.info(
        "Records question answered: patient_id=%s intent=%s status=%s sections=%d",
        patient_id,
        result.intent,
        result.status,
        len(result.sections),
    )
    return RecordsReply(clinical=result)
