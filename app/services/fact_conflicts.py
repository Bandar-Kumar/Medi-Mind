"""The conflict detector handed to the extraction repository (docs/PHASE_3_PLAN.md §6.5-§6.7).

The records layer stores and closes conflicts but never compares values; this service builds the
``ConflictDetector`` it calls, from the pure rules in ``app.extraction.compare``:

1. each usable fact's **display values**: the latest decision decides; a ``correct`` decision's
   ``corrected_values.normalized`` replaces the machine-normalized values (including a corrected
   analyte or date); otherwise the machine values;
2. its **state token** (``apply_decisions``): changes only when a decision changed the fact's
   usability or display values;
3. groups by (display analyte, display date with day precision, date source); facts without a
   mapped analyte or a day-precision date are not compared;
4. one conflict per connected component of the conflict graph, with its detection key.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from app.extraction.compare import (
    ComparableObservation,
    Decision,
    apply_decisions,
    conflict_components,
    detection_key,
)
from app.extraction.reference import Reference, load_reference
from app.extraction.versions import COMPARE_VERSION
from app.records.facts import ConflictDetector, DetectedConflict, GroupFact

DAY = "day"


def _decimal(value: Any) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def display_values(fact: GroupFact) -> dict[str, Any]:
    """The values a fact shows and is compared with: the latest correction, else the machine's."""
    latest = fact.decisions[-1] if fact.decisions else None
    if latest is not None and latest[1] == "correct" and latest[2]:
        normalized = latest[2].get("normalized", {})
        return {
            "analyte_id": normalized.get("analyte_id", fact.analyte_id),
            "analyte_name": normalized.get("analyte_name", fact.analyte_name),
            "effective_date": (
                dt.date.fromisoformat(normalized["effective_date"])
                if normalized.get("effective_date")
                else fact.effective_date
            ),
            "date_precision": normalized.get("date_precision", fact.date_precision),
            "value": _decimal(normalized.get("value_num_norm")),
            "comparator": normalized.get("comparator"),
            "unit": normalized.get("unit_norm"),
            "qualitative": normalized.get("value_qualitative"),
        }
    return {
        "analyte_id": fact.analyte_id,
        "analyte_name": fact.analyte_name,
        "effective_date": fact.effective_date,
        "date_precision": fact.date_precision,
        "value": fact.value_num_norm,
        "comparator": fact.comparator,
        "unit": fact.unit_norm,
        "qualitative": fact.value_qualitative,
    }


def detect(facts: Sequence[GroupFact], reference: Reference) -> list[DetectedConflict]:
    groups: dict[tuple, list[ComparableObservation]] = defaultdict(list)
    for fact in facts:
        _status, token = apply_decisions(
            fact.machine_status, [Decision(d[0], d[1]) for d in fact.decisions]
        )
        shown = display_values(fact)
        if shown["analyte_id"] is None or shown["analyte_name"] is None:
            continue
        if shown["effective_date"] is None or shown["date_precision"] != DAY:
            continue
        key = (shown["analyte_id"], shown["effective_date"], fact.effective_date_source)
        groups[key].append(
            ComparableObservation(
                fact_id=fact.fact_id,
                analyte_name=shown["analyte_name"],
                effective_time=fact.effective_time,
                value=shown["value"],
                comparator=shown["comparator"],
                unit=shown["unit"],
                qualitative=shown["qualitative"],
                state_token=token,
            )
        )
    detected: list[DetectedConflict] = []
    for (analyte_id, date, source), members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        by_id = {m.fact_id: m for m in members}
        for component in conflict_components(members, reference):
            detected.append(
                DetectedConflict(
                    detection_key=detection_key(
                        analyte_id=analyte_id,
                        effective_date=date,
                        effective_date_source=source or "",
                        members=[by_id[i] for i in component.members],
                        reference=reference,
                    ),
                    rule_version=COMPARE_VERSION,
                    analyte_id=analyte_id,
                    effective_date=date,
                    members=component.members,
                )
            )
    return detected


def build_detector(reference: Reference | None = None) -> ConflictDetector:
    ref = reference or load_reference()
    return lambda facts: detect(facts, ref)
