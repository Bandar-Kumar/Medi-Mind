"""Read models of extracted facts, runs and conflicts for the UI (docs/PHASE_3_PLAN.md §6.5, §10).

Four categories are kept apart in every view and never merged:
- ``original``            the values exactly as printed (raw columns);
- ``machine_normalized``  deterministic normalization, plus the derived comparison against the
  document's own printed range (labelled as derived);
- ``correction``          the latest human correction, when the latest decision is ``correct``;
- ``display``             what the fact shows now, with its ``source`` label: ``machine`` or
  ``human_correction``.

``validated`` means only that automated checks passed; it is never shown as verified (§6.8).
The UI imports this module, never the records layer.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.extraction.compare import CONFLICT, ComparableObservation, compare_pair
from app.extraction.reference import load_reference
from app.records.facts import (
    ConflictRecord,
    ExtractionRunRecord,
    FactRecord,
    GroupFact,
    acknowledge_conflict,
    get_fact,
    list_conflicts,
    list_document_facts,
    list_extraction_runs,
)
from app.services.fact_conflicts import display_values

MACHINE = "machine"
HUMAN_CORRECTION = "human_correction"


@dataclass(frozen=True)
class FactView:
    fact: FactRecord
    original: dict[str, Any] = field(repr=False)
    machine_normalized: dict[str, Any] = field(repr=False)
    correction: dict[str, Any] | None = field(repr=False)
    display: dict[str, Any] = field(repr=False)
    source: str  # machine / human_correction


def _group_fact(fact: FactRecord) -> GroupFact:
    return GroupFact(
        fact_id=str(fact.id),
        review_status=fact.review_status,
        machine_status="needs_review" if fact.review_reasons else "validated",
        analyte_id=fact.analyte_id,
        analyte_name=fact.analyte_name,
        effective_date=fact.effective_date,
        date_precision=fact.date_precision,
        effective_date_source=fact.effective_date_source,
        effective_time=fact.effective_time,
        value_num_norm=fact.value_num_norm,
        comparator=fact.comparator,
        unit_norm=fact.unit_norm,
        value_qualitative=fact.value_qualitative,
        decisions=tuple((str(d.id), d.decision, d.corrected_values) for d in fact.decisions),
    )


def fact_view(fact: FactRecord) -> FactView:
    latest = fact.decisions[-1] if fact.decisions else None
    correction = (
        latest.corrected_values if latest is not None and latest.decision == "correct" else None
    )
    shown = display_values(_group_fact(fact))
    return FactView(
        fact=fact,
        original={
            "analyte": fact.analyte_raw,
            "value": fact.value_raw,
            "unit": fact.unit_raw,
            "reference_range": fact.ref_range_raw,
            "flag": fact.flag_as_printed,
            "date": fact.date_raw,
            "time": fact.time_raw,
            "specimen": fact.specimen,
        },
        machine_normalized={
            "analyte": fact.analyte_name,
            "value": fact.value_num,
            "comparator": fact.comparator,
            "qualitative": fact.value_qualitative,
            "unit": fact.unit_norm,
            "value_in_unit": fact.value_num_norm,
            "ref_low": fact.ref_low,
            "ref_high": fact.ref_high,
            "derived_range_comparison": fact.ref_range_comparison,
            "effective_date": fact.effective_date,
            "date_precision": fact.date_precision,
            "date_source": fact.effective_date_source,
            "time": fact.effective_time,
        },
        correction=correction,
        display=shown,
        source=HUMAN_CORRECTION if correction is not None else MACHINE,
    )


def document_facts(
    patient_id: uuid.UUID, document_id: uuid.UUID, *, include_superseded: bool = False
) -> tuple[FactView, ...]:
    return tuple(
        fact_view(f)
        for f in list_document_facts(patient_id, document_id, include_superseded=include_superseded)
    )


def fact(patient_id: uuid.UUID, fact_id: uuid.UUID) -> FactView:
    return fact_view(get_fact(patient_id, fact_id))


def extraction_runs(
    patient_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[ExtractionRunRecord, ...]:
    return list_extraction_runs(patient_id, document_id)


@dataclass(frozen=True)
class ConflictView:
    conflict: ConflictRecord
    members: tuple[FactView, ...]
    direct_pairs: tuple[tuple[uuid.UUID, uuid.UUID], ...]  # the pairs that directly disagree


def conflicts(patient_id: uuid.UUID, *, current: bool = True) -> tuple[ConflictView, ...]:
    """Current conflicts (or history), each with its members and the pairs that directly disagree.

    Members of one conflict are linked by a chain of direct disagreements; not every pair
    disagrees directly, and the views never say so (§6.6).
    """
    reference = load_reference()
    views = []
    for record in list_conflicts(patient_id, current=current):
        members = tuple(fact(patient_id, fact_id) for fact_id in record.member_fact_ids)
        comparable = [
            ComparableObservation(
                fact_id=str(m.fact.id),
                analyte_name=m.display.get("analyte_name") or "",
                effective_time=m.fact.effective_time,
                value=m.display.get("value"),
                comparator=m.display.get("comparator"),
                unit=m.display.get("unit"),
                qualitative=m.display.get("qualitative"),
            )
            for m in members
        ]
        pairs = tuple(
            (uuid.UUID(a.fact_id), uuid.UUID(b.fact_id))
            for i, a in enumerate(comparable)
            for b in comparable[i + 1 :]
            if compare_pair(a, b, reference) == CONFLICT
        )
        views.append(ConflictView(record, members, pairs))
    return tuple(views)


def acknowledge(patient_id: uuid.UUID, conflict_id: uuid.UUID) -> str:
    return acknowledge_conflict(patient_id, conflict_id)
