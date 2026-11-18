"""Input records of the longitudinal layer (docs/PHASE_4_PLAN.md §3).

A ``LongitudinalObservation`` is one *usable* stored fact (ADR-023: validated, verified or
corrected) with its display values already resolved by the service: the latest human correction
if there is one, otherwise the machine-normalized values. The printed values travel with it,
unchanged, so every view can show both. Evidence text fields are hidden from ``repr``.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

LONGITUDINAL_VERSION: Final = "longitudinal-1"  # timeline order, duplicates, trend series rules

DAY: Final = "day"
MONTH: Final = "month"
YEAR: Final = "year"

COLLECTION: Final = "collection"  # observations.effective_date_source
REPORT: Final = "report"

MACHINE: Final = "machine"  # value_source
HUMAN_CORRECTION: Final = "human_correction"

USABLE_STATUSES: Final = frozenset({"validated", "verified", "corrected"})


@dataclass(frozen=True)
class LongitudinalObservation:
    fact_id: str
    document_id: str
    extraction_run_id: str
    page_number: int
    source_locator: str
    char_start: int
    char_end: int
    # display values (latest correction, else machine-normalized)
    analyte_id: int | None
    analyte_name: str | None
    value: Decimal | None  # in ``unit``
    comparator: str | None
    unit: str | None
    qualitative: str | None
    ref_low: Decimal | None  # this document's printed range (or the corrected one)
    ref_high: Decimal | None
    range_comparison: str | None  # derived: below / within / above against that range
    effective_date: dt.date | None
    date_precision: str | None
    date_source: str | None
    effective_time: dt.time | None
    review_status: str
    value_source: str  # machine / human_correction
    # document context
    doc_type: str
    identity_check: str  # match / unknown (mismatch documents are never loaded)
    # printed values, exactly as extracted (never replaced)
    analyte_raw: str = field(repr=False, default="")
    value_raw: str = field(repr=False, default="")
    unit_raw: str | None = field(repr=False, default=None)
    ref_range_raw: str | None = field(repr=False, default=None)
    flag_as_printed: str | None = field(repr=False, default=None)
    date_raw: str | None = field(repr=False, default=None)

    @property
    def dated(self) -> bool:
        return self.effective_date is not None and self.date_precision is not None


@dataclass(frozen=True)
class LongitudinalDocument:
    """A document as the timeline sees it. Upload and processing dates are metadata only."""

    document_id: str
    doc_type: str
    identity_check: str  # match / unknown / mismatch
    processing_status: str
    document_date: dt.date | None  # the printed report date, when extraction found one
    document_date_precision: str | None
    uploaded_at: dt.datetime  # never a clinical date
    processed_at: dt.datetime | None  # last finished pipeline run; never a clinical date
    filename: str = field(repr=False, default="")
