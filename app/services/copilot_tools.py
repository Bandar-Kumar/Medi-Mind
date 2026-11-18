"""Allowlisted structured-fact tools for the copilot (Phase 5; ARCHITECTURE §9.2; ADR-007, ADR-040).

Fixed, typed operations over the patient's stored lab results. No free-form SQL exists anywhere:
each tool calls the Phase 4 longitudinal service (``app.services.longitudinal``), which reads
through the patient-scoped repositories (``app.records.longitudinal``, ``app.records.facts``).

Safety boundaries:
- ``patient_id`` is a separate argument supplied by the caller's session (the selected patient).
  ``ToolArgs`` forbids extra fields, so a model-supplied ``patient_id`` (or anything else) is
  rejected, never used.
- ``analyte`` must be a canonical name from the versioned reference data; dates are real dates;
  limits are bounded.
- Only usable facts are returned (ADR-023: validated, verified, corrected; never rejected,
  ``needs_review``, superseded or from an identity-mismatch document). ``needs_review`` results are
  only *counted* (``awaiting_review``).
- Values are exact: ``Decimal`` as stored, the shown value (latest correction, else the machine
  value) plus the printed value and unit, with the document, page and exact source excerpt.
- Exact duplicates (the same printed result in two documents) are shown once; differing results on
  the same date are all returned, never reconciled; current conflicts are flagged.
"""

import datetime as dt
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.answering.model import EvidenceItem, SourceKind
from app.extraction.reference import load_reference
from app.longitudinal.duplicates import EXACT
from app.longitudinal.model import LongitudinalObservation
from app.records.longitudinal import count_facts_by_status, list_facts_by_ids
from app.services import longitudinal as history_service

MAX_RESULTS = 20


class ToolName(StrEnum):
    LIST_ANALYTES = "list_analytes"
    LATEST_RESULT = "latest_result"
    RESULTS_ON_DATE = "results_on_date"
    ANALYTE_HISTORY = "analyte_history"
    COMPARE_PREVIOUS = "compare_previous"
    CONFLICTS = "conflicts"


class ToolArgs(BaseModel):
    """Validated tool arguments. Unknown fields (including ``patient_id``) are rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    analyte: str | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    limit: int = Field(default=MAX_RESULTS, ge=1, le=MAX_RESULTS)

    @field_validator("analyte")
    @classmethod
    def _known_analyte(cls, value: str | None) -> str | None:
        if value is not None and value not in load_reference().analytes:
            raise ValueError("analyte must be a canonical analyte name from the reference data")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> "ToolArgs":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from is after date_to")
        return self


REQUIRES_ANALYTE = frozenset({ToolName.LATEST_RESULT, ToolName.COMPARE_PREVIOUS})


@dataclass(frozen=True)
class ToolFact:
    """One usable observation with its exact source excerpt and conflict flag."""

    observation: LongitudinalObservation
    excerpt: str
    filename: str
    document_date: dt.date | None
    in_conflict: bool


@dataclass(frozen=True)
class ToolResult:
    tool: ToolName
    args: ToolArgs
    facts: tuple[ToolFact, ...]  # in the order the answer should present them
    missing: tuple[str, ...]  # what was asked for but not found (fixed wording)
    awaiting_review: int  # needs_review results of the analyte(s), not used
    truncated: bool = False


def _sort_key(o: LongitudinalObservation) -> tuple:
    return (o.effective_date or dt.date.min, o.fact_id)


class _Loaded:
    """One patient-scoped load: observations (exact duplicates collapsed), excerpts, conflicts."""

    def __init__(
        self,
        patient_id: uuid.UUID,
        analytes: frozenset[str] | None,
        date_from: dt.date | None,
        date_to: dt.date | None,
    ) -> None:
        history = history_service.load_history(
            patient_id, analytes=analytes, date_from=date_from, date_to=date_to
        )
        hidden = {
            m
            for g in history.duplicates
            if g.kind == EXACT
            for m in g.members
            if m != g.representative
        }
        self.observations = sorted(
            (o for o in history.observations if o.fact_id not in hidden), key=_sort_key
        )
        self.filenames = history.filenames
        self.document_dates = {d.document_id: d.document_date for d in history.documents}
        self.patient_id = patient_id
        self.conflicted = {
            str(member.view.fact.id)
            for c in history_service.conflicts(patient_id)
            for member in c.members
            if not member.rejected_or_superseded
        }

    def facts(self, observations: Sequence[LongitudinalObservation]) -> tuple[ToolFact, ...]:
        ids = [uuid.UUID(o.fact_id) for o in observations]
        excerpts = {str(r.id): r.source_excerpt for r in list_facts_by_ids(self.patient_id, ids)}
        return tuple(
            ToolFact(
                observation=o,
                excerpt=excerpts[o.fact_id],
                filename=self.filenames.get(o.document_id, ""),
                document_date=self.document_dates.get(o.document_id),
                in_conflict=o.fact_id in self.conflicted,
            )
            for o in observations
            if o.fact_id in excerpts
        )


def _awaiting(patient_id: uuid.UUID, analyte: str | None) -> int:
    return sum(
        c.count
        for c in count_facts_by_status(patient_id)
        if c.review_status == "needs_review" and (analyte is None or c.analyte_name == analyte)
    )


def _missing(analyte: str | None, what: str) -> tuple[str, ...]:
    return (f"No usable {analyte} result {what}.",) if analyte else (f"No usable result {what}.",)


def _within(o: LongitudinalObservation, args: ToolArgs) -> bool:
    if o.effective_date is None:
        return args.date_from is None and args.date_to is None
    if args.date_from and o.effective_date < args.date_from:
        return False
    return not (args.date_to and o.effective_date > args.date_to)


def _of(analyte: str | None) -> frozenset[str] | None:
    return frozenset({analyte}) if analyte else None


def _latest(patient_id: uuid.UUID, args: ToolArgs) -> ToolResult:
    loaded = _Loaded(patient_id, _of(args.analyte), None, None)
    dated = [o for o in loaded.observations if o.effective_date is not None]
    if not dated:
        return ToolResult(
            ToolName.LATEST_RESULT,
            args,
            (),
            _missing(args.analyte, "with a date"),
            _awaiting(patient_id, args.analyte),
        )
    last = max(o.effective_date for o in dated)  # type: ignore[type-var]
    chosen = [o for o in dated if o.effective_date == last]
    return ToolResult(
        ToolName.LATEST_RESULT, args, loaded.facts(chosen), (), _awaiting(patient_id, args.analyte)
    )


def _on_date(patient_id: uuid.UUID, args: ToolArgs) -> ToolResult:
    if args.date_from is None or args.date_from != args.date_to:
        raise ValueError("results_on_date needs one exact date (date_from == date_to)")
    loaded = _Loaded(patient_id, _of(args.analyte), args.date_from, args.date_to)
    chosen = [o for o in loaded.observations if o.effective_date == args.date_from]
    missing = () if chosen else _missing(args.analyte, f"dated {args.date_from.isoformat()}")
    return ToolResult(
        ToolName.RESULTS_ON_DATE,
        args,
        loaded.facts(chosen),
        missing,
        _awaiting(patient_id, args.analyte),
    )


def _history(patient_id: uuid.UUID, args: ToolArgs) -> ToolResult:
    loaded = _Loaded(patient_id, _of(args.analyte), args.date_from, args.date_to)
    chosen = [o for o in loaded.observations if _within(o, args)]
    chosen.sort(key=lambda o: (o.analyte_name or "", *_sort_key(o)))
    truncated = len(chosen) > args.limit
    if truncated:  # keep the most recent results of each analyte when the window is too large
        chosen = sorted(chosen, key=_sort_key)[-args.limit :]
        chosen.sort(key=lambda o: (o.analyte_name or "", *_sort_key(o)))
    missing = () if chosen else _missing(args.analyte, "in the requested period")
    return ToolResult(
        ToolName.ANALYTE_HISTORY,
        args,
        loaded.facts(chosen),
        missing,
        _awaiting(patient_id, args.analyte),
        truncated,
    )


def _compare_previous(patient_id: uuid.UUID, args: ToolArgs) -> ToolResult:
    """The latest result and the one before it (day-precision dates only, so the order is sure)."""
    loaded = _Loaded(patient_id, _of(args.analyte), None, None)
    dated = [o for o in loaded.observations if o.effective_date and o.date_precision == "day"]
    dates = sorted({o.effective_date for o in dated}, reverse=True)  # type: ignore[type-var]
    awaiting = _awaiting(patient_id, args.analyte)
    if len(dates) < 2:
        chosen = [o for o in dated if dates and o.effective_date == dates[0]]
        return ToolResult(
            ToolName.COMPARE_PREVIOUS,
            args,
            loaded.facts(chosen),
            _missing(args.analyte, "before the latest one, so there is nothing to compare"),
            awaiting,
        )
    previous = [o for o in dated if o.effective_date == dates[1]]
    latest = [o for o in dated if o.effective_date == dates[0]]
    missing: tuple[str, ...] = ()
    if len(previous) > 1 or len(latest) > 1:
        missing = (
            f"Several different {args.analyte} results share a date, so MediMind does not pick "
            "one pair to compare.",
        )
    return ToolResult(
        ToolName.COMPARE_PREVIOUS, args, loaded.facts([*previous, *latest]), missing, awaiting
    )


def _conflicts(patient_id: uuid.UUID, args: ToolArgs) -> ToolResult:
    presentations = [
        c
        for c in history_service.conflicts(patient_id)
        if args.analyte is None or c.analyte_name == args.analyte
    ]
    member_ids = {
        str(m.view.fact.id)
        for c in presentations
        for m in c.members
        if not m.rejected_or_superseded
    }
    analytes = frozenset({c.analyte_name for c in presentations if c.analyte_name}) or _of(
        args.analyte
    )
    loaded = _Loaded(patient_id, analytes, None, None)
    chosen = [o for o in loaded.observations if o.fact_id in member_ids]
    chosen.sort(key=lambda o: (o.analyte_name or "", *_sort_key(o)))
    missing = () if chosen else _missing(args.analyte, "is in an open conflict")
    return ToolResult(
        ToolName.CONFLICTS,
        args,
        loaded.facts(chosen[: args.limit]),
        missing,
        _awaiting(patient_id, args.analyte),
        len(chosen) > args.limit,
    )


def _list_analytes(patient_id: uuid.UUID, args: ToolArgs) -> ToolResult:
    """The most recent usable result of each analyte (each one cites its source)."""
    loaded = _Loaded(patient_id, None, None, None)
    latest: dict[str, LongitudinalObservation] = {}
    for o in loaded.observations:  # sorted oldest first, so the last one wins
        if o.analyte_name:
            latest[o.analyte_name] = o
    chosen = [latest[name] for name in sorted(latest)][: args.limit]
    missing = () if chosen else ("No usable lab results are stored for this patient.",)
    return ToolResult(
        ToolName.LIST_ANALYTES,
        args,
        loaded.facts(chosen),
        missing,
        _awaiting(patient_id, None),
        len(latest) > args.limit,
    )


TOOLS: dict[ToolName, Callable[[uuid.UUID, ToolArgs], ToolResult]] = {
    ToolName.LIST_ANALYTES: _list_analytes,
    ToolName.LATEST_RESULT: _latest,
    ToolName.RESULTS_ON_DATE: _on_date,
    ToolName.ANALYTE_HISTORY: _history,
    ToolName.COMPARE_PREVIOUS: _compare_previous,
    ToolName.CONFLICTS: _conflicts,
}


def run_tool(patient_id: uuid.UUID, name: str, arguments: dict) -> ToolResult:
    """Run one allowlisted tool for the selected patient.

    ``arguments`` may come from untrusted input (a model): they are validated by ``ToolArgs``
    (unknown fields such as ``patient_id`` are rejected) and the patient always comes from the
    caller's ``patient_id``.

    Raises:
        ValueError: an unknown tool or invalid arguments (pydantic's ``ValidationError`` is a
            ``ValueError``).
    """
    if not isinstance(patient_id, uuid.UUID):
        raise TypeError("patient_id must be a UUID")
    try:
        tool = ToolName(name)
    except ValueError:
        raise ValueError("unknown tool") from None
    args = ToolArgs.model_validate(arguments)
    if tool in REQUIRES_ANALYTE and args.analyte is None:
        raise ValueError(f"{tool} needs an analyte")
    return TOOLS[tool](patient_id, args)


def to_evidence(facts: Sequence[ToolFact], start: int = 1) -> list[EvidenceItem]:
    """Numbered fact sources ``F<n>`` for the generator, with exact values and provenance."""
    items = []
    for number, f in enumerate(facts, start=start):
        o = f.observation
        items.append(
            EvidenceItem(
                source_id=f"F{number}",
                kind=SourceKind.FACT,
                document_id=o.document_id,
                page_number=o.page_number,
                excerpt=f.excerpt,
                filename=f.filename,
                document_date=f.document_date,
                identity_check=o.identity_check,
                fact_id=o.fact_id,
                analyte=o.analyte_name,
                value=None if o.value is None else str(o.value),
                unit=o.unit,
                comparator=o.comparator,
                qualitative=o.qualitative,
                value_raw=o.value_raw,
                unit_raw=o.unit_raw,
                ref_range_raw=o.ref_range_raw,
                effective_date=o.effective_date,
                date_precision=o.date_precision,
                review_status=o.review_status,
                value_source=o.value_source,
                in_conflict=f.in_conflict,
            )
        )
    return items
