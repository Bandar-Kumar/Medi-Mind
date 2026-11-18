"""Human review of one clinical fact (Phase 6; ADR-023 as for lab facts; ADR-042).

``review_clinical_fact`` is the clinical path to ``verified``, ``corrected`` and ``rejected``; it
uses the shared, unchanged ``record_review_decision`` (append-only decisions; fact value columns
are never updated):
- ``verify``  confirm the original extracted values (withdraws an earlier correction);
- ``correct`` enter corrected values: they are normalized with the same rules as extraction and
  stored in ``review_decisions.corrected_values`` (schema ``clinical-correction-1``). An entered
  value that cannot be normalized is refused (``invalid_correction``), never guessed;
- ``reject``  the fact is excluded from summaries, comparisons and answers.

Allowed fields per fact type (``FIELDS``): a medication's name, strength, frequency, duration and
route; an allergy's substance, reaction, severity and status; an event's description and date.
Errors: ``invalid_correction``, ``fact_not_found`` (another patient's, superseded, or not a
clinical fact). Values are never logged.
"""

import logging
import uuid
from typing import Any, Final

from app.clinical import allergies as al
from app.clinical import medications as md
from app.clinical.lexicon import load_lexicon
from app.clinical.versions import CLINICAL_NORMALIZER_VERSION
from app.core.errors import DocumentProcessingError
from app.extraction.normalize.dates import parse_date
from app.records.clinical import get_clinical_fact, get_clinical_target
from app.records.facts import ReviewRecord, record_review_decision
from app.services.fact_conflicts import build_detector

logger = logging.getLogger(__name__)

CORRECTION_SCHEMA: Final = "clinical-correction-1"
INVALID_CORRECTION: Final = "invalid_correction"
DECISIONS: Final = ("verify", "correct", "reject")
FIELDS: Final = {
    "medication": ("name", "strength", "frequency", "duration", "route"),
    "allergy": ("substance", "reaction", "severity", "status"),
    "event": ("description", "date"),
}
MAX_FIELD_CHARS: Final = 200


def _invalid(message: str) -> DocumentProcessingError:
    return DocumentProcessingError(message, kind=INVALID_CORRECTION)


def _entered(fact_type: str, correction: dict[str, str | None]) -> dict[str, str]:
    unknown = set(correction) - set(FIELDS[fact_type])
    if unknown:
        raise _invalid("A correction names a field this fact type does not have")
    entered = {k: v.strip() for k, v in correction.items() if v is not None and v.strip()}
    if not entered:
        raise _invalid("A correction needs at least one corrected value")
    if any(len(v) > MAX_FIELD_CHARS or "\x00" in v for v in entered.values()):
        raise _invalid("A corrected value is too long or contains characters that cannot be stored")
    return entered


def _medication(entered: dict[str, str]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    if "name" in entered:
        lookup = load_lexicon().lookup(entered["name"])
        if lookup.generic is None:
            raise _invalid("The corrected medication name is not one known name")
        normalized["name_norm"] = lookup.generic
        normalized["name_match"] = lookup.match.value if lookup.match else None
    if "strength" in entered:
        strength = md.strength(entered["strength"])
        if strength is None:
            raise _invalid("The corrected strength could not be read (e.g. 500 mg)")
        normalized["strength_value"] = str(strength[0])
        normalized["strength_unit"] = strength[1]
    if "frequency" in entered:
        per_day, as_needed = md.frequency(entered["frequency"])
        if per_day is None and not as_needed:
            raise _invalid("The corrected frequency could not be read (e.g. 1-0-1, BD)")
        normalized["frequency_per_day"] = per_day
        normalized["as_needed"] = as_needed
    if "duration" in entered:
        duration = md.duration(entered["duration"])
        if duration is None:
            raise _invalid("The corrected duration could not be read (e.g. for 5 days)")
        normalized["duration_value"], normalized["duration_unit"] = duration
    if "route" in entered:
        route = md.route(entered["route"])
        if route is None:
            raise _invalid("The corrected route is not a known route")
        normalized["route_norm"] = route
    return normalized


def _allergy(entered: dict[str, str]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    if "status" in entered:
        status = entered["status"].casefold()
        if status not in (al.CONFIRMED, al.SUSPECTED, al.NONE_KNOWN):
            raise _invalid("The corrected status must be confirmed, suspected or none_known")
        normalized["allergy_status"] = status
    if "substance" in entered:
        normalized["substance_norm"] = al.substance_norm(entered["substance"])
    if "reaction" in entered:
        normalized["reaction"] = entered["reaction"]
    if "severity" in entered:
        severity = al.severity_norm(entered["severity"])
        if severity is None:
            raise _invalid("The corrected severity must be mild, moderate or severe")
        normalized["severity_norm"] = severity
    return normalized


def _event(entered: dict[str, str], patient_id: uuid.UUID, document_id: uuid.UUID) -> dict:
    normalized: dict[str, Any] = {}
    if "description" in entered:
        normalized["description"] = entered["description"]
    if "date" in entered:
        reference_date = get_clinical_target(patient_id, document_id).reference_date
        parsed = parse_date(entered["date"], reference_date)
        if parsed.value is None or parsed.reasons:
            raise _invalid("The corrected date must be a complete, unambiguous, plausible date")
        normalized["date"] = parsed.value.isoformat()
        normalized["date_precision"] = parsed.precision
    return normalized


def corrected_values(
    patient_id: uuid.UUID, fact_id: uuid.UUID, correction: dict[str, str | None]
) -> dict[str, Any]:
    """The full ``corrected_values`` JSON: what was entered and its normalized view."""
    fact = get_clinical_fact(patient_id, fact_id)
    entered = _entered(fact.fact_type, correction)
    if fact.fact_type == "medication":
        normalized = _medication(entered)
    elif fact.fact_type == "allergy":
        normalized = _allergy(entered)
    else:
        normalized = _event(entered, patient_id, fact.document_id)
    return {
        "schema": CORRECTION_SCHEMA,
        "fact_type": fact.fact_type,
        "entered": entered,
        "normalized": normalized,
        "normalizer_version": CLINICAL_NORMALIZER_VERSION,
        "reference_data_version": load_lexicon().version,
    }


def review_clinical_fact(
    patient_id: uuid.UUID,
    fact_id: uuid.UUID,
    decision: str,
    *,
    correction: dict[str, str | None] | None = None,
    note: str | None = None,
) -> ReviewRecord:
    """Record one human decision on one clinical fact.

    Raises:
        ValueError: unknown decision, or a correction given without ``correct``.
        DocumentProcessingError: ``invalid_correction``, ``fact_not_found``.
    """
    if decision not in DECISIONS:
        raise ValueError("decision must be verify, correct or reject")
    if (decision == "correct") != (correction is not None):
        raise ValueError("a correction goes with the correct decision only")
    get_clinical_fact(patient_id, fact_id)  # scoping: a clinical fact of this patient
    # "is not None": an empty correction must be refused, not silently skipped.
    values = corrected_values(patient_id, fact_id, correction) if correction is not None else None
    record = record_review_decision(
        patient_id,
        fact_id,
        decision=decision,
        corrected_values=values,
        note=note.strip() if note and note.strip() else None,
        detector=build_detector(),
    )
    logger.info("Clinical fact reviewed: fact_id=%s decision=%s", fact_id, decision)
    return record
