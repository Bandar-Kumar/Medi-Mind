"""Patient timeline (docs/PHASE_4_PLAN.md §4; USER_FLOWS F4; ADR-039).

Entries are dated by clinical dates only:
- a lab result by its display date and the source the extractor recorded (``collection`` or
  ``report``);
- a document by its printed report date (``documents.document_date``), when extraction found one.
Upload and processing dates are carried as metadata and never order or date an entry. Undated
results and documents are returned separately, never placed at a made-up position.

An exact duplicate group is one entry with every source; possible duplicates stay separate entries
that name each other. Documents with an identity ``mismatch`` are excluded and counted (they may
belong to someone else; no fact is ever extracted from them).
"""

import datetime as dt
import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Final

from app.longitudinal.dates import date_label, overlaps, sort_key
from app.longitudinal.duplicates import EXACT, POSSIBLE, DuplicateGroup
from app.longitudinal.model import LongitudinalDocument, LongitudinalObservation

LAB_RESULT: Final = "lab_result"
DOCUMENT: Final = "document"
DOCUMENT_REPORT_DATE: Final = "document_report_date"  # date source of a document entry
MISMATCH: Final = "mismatch"
UNKNOWN_IDENTITY: Final = "unknown"

MAX_PAGE_SIZE: Final = 200
_KIND_RANK = {DOCUMENT: 0, LAB_RESULT: 1}


@dataclass(frozen=True)
class TimelineFilter:
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    analytes: frozenset[str] | None = None  # display analyte names; documents are then hidden
    doc_types: frozenset[str] | None = None
    kinds: frozenset[str] | None = None  # lab_result / document


@dataclass(frozen=True)
class TimelineEntry:
    kind: str
    entry_id: str  # the representative fact id, or the document id
    date: dt.date
    precision: str
    date_source: str  # collection / report / document_report_date
    label: str
    observation: LongitudinalObservation | None  # the representative (lab results)
    sources: tuple[LongitudinalObservation, ...]  # every source of this result (exact group)
    duplicate_kind: str | None  # exact (collapsed) / possible (linked) / None
    related: tuple[str, ...]  # fact ids of possible duplicates shown as their own entries
    document: LongitudinalDocument | None  # document entries
    identity_uncertain: bool  # a source document's identity could not be confirmed


@dataclass(frozen=True)
class TimelinePage:
    entries: tuple[TimelineEntry, ...]
    total: int  # entries after filtering, before paging
    page: int
    page_size: int
    pages: int
    undated_observations: tuple[LongitudinalObservation, ...]
    undated_documents: tuple[LongitudinalDocument, ...]
    excluded_mismatch_documents: int


def _lab_entries(
    observations: Sequence[LongitudinalObservation],
    groups: Sequence[DuplicateGroup],
    documents: dict[str, LongitudinalDocument],
) -> list[TimelineEntry]:
    by_id = {o.fact_id: o for o in observations}
    collapsed: dict[str, DuplicateGroup] = {}
    related: dict[str, tuple[str, ...]] = {}
    for group in groups:
        if not set(group.members) <= by_id.keys():
            continue  # a member was filtered out upstream; never collapse a partial group
        if group.kind == EXACT:
            for member in group.members:
                collapsed[member] = group
        else:
            for member in group.members:
                related[member] = tuple(m for m in group.members if m != member)
    entries = []
    for o in observations:
        if not o.dated:
            continue
        group = collapsed.get(o.fact_id)
        if group is not None and o.fact_id != group.representative:
            continue
        sources = (
            tuple(sorted((by_id[m] for m in group.members), key=lambda s: s.document_id))
            if group is not None
            else (o,)
        )
        assert o.effective_date is not None and o.date_precision is not None
        entries.append(
            TimelineEntry(
                kind=LAB_RESULT,
                entry_id=o.fact_id,
                date=o.effective_date,
                precision=o.date_precision,
                date_source=o.date_source or "",
                label=date_label(o.effective_date, o.date_precision),
                observation=o,
                sources=sources,
                duplicate_kind=(
                    EXACT if group is not None else (POSSIBLE if o.fact_id in related else None)
                ),
                related=related.get(o.fact_id, ()),
                document=None,
                identity_uncertain=any(
                    documents[s.document_id].identity_check == UNKNOWN_IDENTITY
                    for s in sources
                    if s.document_id in documents
                ),
            )
        )
    return entries


def _document_entry(d: LongitudinalDocument) -> TimelineEntry:
    assert d.document_date is not None and d.document_date_precision is not None
    return TimelineEntry(
        kind=DOCUMENT,
        entry_id=d.document_id,
        date=d.document_date,
        precision=d.document_date_precision,
        date_source=DOCUMENT_REPORT_DATE,
        label=date_label(d.document_date, d.document_date_precision),
        observation=None,
        sources=(),
        duplicate_kind=None,
        related=(),
        document=d,
        identity_uncertain=d.identity_check == UNKNOWN_IDENTITY,
    )


def _keep(entry: TimelineEntry, f: TimelineFilter) -> bool:
    if f.kinds is not None and entry.kind not in f.kinds:
        return False
    if not overlaps(entry.date, entry.precision, f.date_from, f.date_to):
        return False
    if f.analytes is not None:
        if entry.observation is None or entry.observation.analyte_name not in f.analytes:
            return False
    if f.doc_types is not None:
        types = (
            {entry.document.doc_type}
            if entry.document is not None
            else {s.doc_type for s in entry.sources}
        )
        if not types & f.doc_types:
            return False
    return True


def _order(entry: TimelineEntry) -> tuple:
    o = entry.observation
    return (
        sort_key(entry.date, entry.precision, o.effective_time if o else None),
        _KIND_RANK[entry.kind],
        (o.analyte_name or "") if o else "",
        o.document_id if o else entry.entry_id,
        o.page_number if o else 0,
        o.char_start if o else 0,
        entry.entry_id,
    )


def build_timeline(
    observations: Sequence[LongitudinalObservation],
    documents: Collection[LongitudinalDocument],
    groups: Sequence[DuplicateGroup],
    *,
    filters: TimelineFilter | None = None,
    page: int = 1,
    page_size: int = 50,
    newest_first: bool = False,
) -> TimelinePage:
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError("page must be >= 1 and page_size between 1 and 200")
    filters = filters or TimelineFilter()
    docs = {d.document_id: d for d in documents}
    excluded = sum(d.identity_check == MISMATCH for d in documents)
    kept_docs = {k: d for k, d in docs.items() if d.identity_check != MISMATCH}
    usable = [o for o in observations if o.document_id in kept_docs]

    entries = _lab_entries(usable, groups, kept_docs)
    entries += [_document_entry(d) for d in kept_docs.values() if d.document_date is not None]
    selected = sorted((e for e in entries if _keep(e, filters)), key=_order, reverse=newest_first)

    def undated_observation_kept(o: LongitudinalObservation) -> bool:
        if filters.kinds is not None and LAB_RESULT not in filters.kinds:
            return False
        if filters.analytes is not None and o.analyte_name not in filters.analytes:
            return False
        return filters.doc_types is None or o.doc_type in filters.doc_types

    undated_obs = tuple(
        sorted(
            (o for o in usable if not o.dated and undated_observation_kept(o)),
            key=lambda o: (o.analyte_name or "", o.document_id, o.page_number, o.char_start),
        )
    )
    undated_docs = tuple(
        sorted(
            (
                d
                for d in kept_docs.values()
                if d.document_date is None
                and filters.analytes is None
                and (filters.kinds is None or DOCUMENT in filters.kinds)
                and (filters.doc_types is None or d.doc_type in filters.doc_types)
            ),
            key=lambda d: d.document_id,
        )
    )
    total = len(selected)
    start = (page - 1) * page_size
    return TimelinePage(
        entries=tuple(selected[start : start + page_size]),
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, math.ceil(total / page_size)),
        undated_observations=undated_obs,
        undated_documents=undated_docs,
        excluded_mismatch_documents=excluded,
    )
