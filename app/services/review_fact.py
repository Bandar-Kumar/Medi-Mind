"""Human review of one extracted fact (ADR-023; docs/PHASE_3_PLAN.md §6.5).

``review_fact`` is the only path to ``verified``, ``corrected`` and ``rejected``:
- ``verify``  confirm the **original extracted values** (it withdraws an earlier correction);
- ``correct`` enter corrected values; the original and machine-normalized values are never
  changed. The entered values are normalized with the same rules as extraction and stored in
  ``review_decisions.corrected_values`` (schema ``observation-correction-1``);
- ``reject``  the fact is excluded everywhere except the audit view.
A rejected fact can be verified or corrected later. One fact per call; there is no bulk action.
Conflicts are recomputed in the same transaction as the decision.

Errors: ``invalid_correction`` (nothing entered, or an entered value cannot be parsed);
``fact_not_found`` (another patient's or a superseded fact). Values are never logged.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.errors import DocumentProcessingError
from app.extraction.normalize.analytes import map_analyte
from app.extraction.normalize.dates import parse_date
from app.extraction.normalize.numbers import parse_value, text_key
from app.extraction.normalize.ranges import compare_to_range, parse_range
from app.extraction.normalize.units import normalize_unit
from app.extraction.reference import load_reference
from app.extraction.versions import NORMALIZER_VERSION
from app.records.facts import (
    ReviewRecord,
    get_extraction_target,
    get_fact,
    record_review_decision,
    reference_analyte_ids,
)
from app.services.fact_conflicts import build_detector

logger = logging.getLogger(__name__)

CORRECTION_SCHEMA = "observation-correction-1"
INVALID_CORRECTION = "invalid_correction"
DECISIONS = ("verify", "correct", "reject")


@dataclass(frozen=True)
class Correction:
    """Values a person enters. Every field is optional; at least one is required."""

    value: str | None = None
    unit: str | None = None
    ref_range: str | None = None
    effective_date: str | None = None  # DD/MM/YYYY or ISO
    analyte: str | None = None  # a printed analyte name (must map exactly)

    def entered(self) -> dict[str, str]:
        return {k: v.strip() for k, v in self.__dict__.items() if v is not None and v.strip()}


def _invalid(message: str) -> DocumentProcessingError:
    return DocumentProcessingError(message, kind=INVALID_CORRECTION)


def corrected_values(patient_id: uuid.UUID, fact_id: uuid.UUID, correction: Correction) -> dict:
    """The full ``corrected_values`` JSON: what was entered and the complete normalized view."""
    entered = correction.entered()
    if not entered:
        raise _invalid("A correction needs at least one corrected value")
    fact = get_fact(patient_id, fact_id)
    reference = load_reference()
    analyte = reference.analytes.get(fact.analyte_name) if fact.analyte_name else None
    if "analyte" in entered:
        analyte = map_analyte(entered["analyte"], reference)
        if analyte is None:
            raise _invalid("The corrected test name is not a known analyte")
    value_text = entered.get("value", fact.value_raw)
    value = parse_value(value_text)
    if value.reason is not None:
        raise _invalid("The corrected value could not be read")
    unit_text = entered.get("unit", fact.unit_raw)
    unit = normalize_unit(unit_text, value.value_num, analyte, reference)
    if "unit" in entered and unit.unit_norm is None:
        raise _invalid("The corrected unit is not a known unit")
    range_text = entered.get("ref_range", fact.ref_range_raw)
    printed = parse_range(range_text, unit_key=text_key(unit_text) if unit_text else None)
    if "ref_range" in entered and not printed.parsed:
        raise _invalid("The corrected reference range could not be read")
    effective_date, precision = fact.effective_date, fact.date_precision
    if "effective_date" in entered:
        reference_date = get_extraction_target(patient_id, fact.document_id).reference_date
        parsed = parse_date(entered["effective_date"], reference_date)
        if parsed.value is None or parsed.reasons:
            raise _invalid("The corrected date must be a complete, unambiguous, plausible date")
        effective_date, precision = parsed.value, parsed.precision

    def text(value: Any) -> str | None:
        return None if value is None else str(value)

    analyte_id = fact.analyte_id
    if "analyte" in entered and analyte is not None:
        analyte_id = reference_analyte_ids([analyte.canonical_name])[analyte.canonical_name]
    normalized = {
        "analyte_name": analyte.canonical_name if analyte else None,
        "value_num": text(value.value_num),
        "comparator": value.comparator,
        "value_qualitative": value.qualitative,
        "unit_norm": unit.unit_norm,
        "value_num_norm": text(unit.value_num_norm),
        "ref_low": text(printed.low),
        "ref_high": text(printed.high),
        "ref_range_comparison": compare_to_range(value.value_num, value.comparator, printed),
        "effective_date": effective_date.isoformat() if effective_date else None,
        "date_precision": precision,
    }
    normalized["analyte_id"] = analyte_id
    return {
        "schema": CORRECTION_SCHEMA,
        "entered": entered,
        "normalized": normalized,
        "normalizer_version": NORMALIZER_VERSION,
        "reference_data_version": reference.version,
    }


def review_fact(
    patient_id: uuid.UUID,
    fact_id: uuid.UUID,
    decision: str,
    *,
    correction: Correction | None = None,
    note: str | None = None,
) -> ReviewRecord:
    """Record one human decision on one fact.

    Raises:
        ValueError: unknown decision, or a correction given without ``correct``.
        DocumentProcessingError: ``invalid_correction``, ``fact_not_found``.
    """
    if decision not in DECISIONS:
        raise ValueError("decision must be verify, correct or reject")
    if (decision == "correct") != (correction is not None):
        raise ValueError("a correction goes with the correct decision only")
    values = corrected_values(patient_id, fact_id, correction) if correction else None
    record = record_review_decision(
        patient_id,
        fact_id,
        decision=decision,
        corrected_values=values,
        note=note.strip() if note and note.strip() else None,
        detector=build_detector(),
    )
    logger.info("Fact reviewed: fact_id=%s decision=%s", fact_id, decision)
    return record
