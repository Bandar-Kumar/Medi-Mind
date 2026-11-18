"""The extractor's output contract (docs/PHASE_3_PLAN.md §4.2, §6.1).

Frozen Pydantic models: unknown fields are refused, and validation errors never contain input
values (``hide_input_in_errors``), because every text field is document content. Text fields are
excluded from ``repr``. Raw, normalized and derived values are separate fields; no field ever
holds generated text.
"""

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.extraction.reasons import ReviewReason

VALIDATED = "validated"
NEEDS_REVIEW = "needs_review"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class DateFinding(_Model):
    """A labelled date on a page, with its exact span."""

    role: str  # "collection" or "report"
    page_number: int = Field(ge=1)
    raw: str = Field(repr=False)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    value: dt.date | None
    precision: str | None
    reasons: tuple[str, ...] = ()
    time_raw: str | None = Field(default=None, repr=False)
    time_value: dt.time | None = None


class ObservationDraft(_Model):
    """One lab observation ready to persist. Offsets index the stored ``pages.text``."""

    page_number: int = Field(ge=1)
    source_locator: str
    source_excerpt: str = Field(repr=False, max_length=1000)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)

    analyte_raw: str = Field(repr=False)
    analyte_name: str | None = Field(default=None, repr=False)  # canonical; resolved to an id later
    analyte_match: str | None = None

    value_raw: str = Field(repr=False)
    value_num: Decimal | None = None
    comparator: str | None = None
    value_qualitative: str | None = None

    unit_raw: str | None = Field(default=None, repr=False)
    unit_norm: str | None = None
    value_num_norm: Decimal | None = None

    ref_range_raw: str | None = Field(default=None, repr=False)
    ref_low: Decimal | None = None
    ref_high: Decimal | None = None
    flag_as_printed: str | None = Field(default=None, repr=False)
    ref_range_comparison: str | None = None  # derived

    specimen: str | None = Field(default=None, repr=False)

    effective_date: dt.date | None = None
    date_precision: str | None = None
    date_raw: str | None = Field(default=None, repr=False)
    effective_date_source: str | None = None
    date_page_number: int | None = None
    date_char_start: int | None = None
    date_char_end: int | None = None
    effective_time: dt.time | None = None
    time_raw: str | None = Field(default=None, repr=False)

    review_status: str
    review_reasons: tuple[str, ...] = ()
    quality_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> "ObservationDraft":
        if self.char_start > self.char_end:
            raise ValueError("char_start after char_end")
        if self.review_status not in (VALIDATED, NEEDS_REVIEW):
            raise ValueError("extraction stores only validated or needs_review")
        if (self.review_status == NEEDS_REVIEW) != bool(self.review_reasons):
            raise ValueError("needs_review exactly when there are reasons")
        if any(reason not in ReviewReason._value2member_map_ for reason in self.review_reasons):
            raise ValueError("unknown review reason")
        if (self.effective_date is None) != (self.date_precision is None):
            raise ValueError("effective_date and date_precision go together")
        if (self.effective_date is None) != (self.effective_date_source is None):
            raise ValueError("effective_date and its source go together")
        date_span = (self.date_page_number, self.date_char_start, self.date_char_end)
        if any(v is None for v in date_span) and any(v is not None for v in date_span):
            raise ValueError("date span fields go together")
        if self.comparator is not None and self.value_num is None:
            raise ValueError("a comparator needs a number")
        if self.value_num_norm is not None and self.unit_norm is None:
            raise ValueError("a normalized value needs a normalized unit")
        if self.ref_range_comparison is not None and self.value_num is None:
            raise ValueError("a range comparison needs a number")
        return self


class ExtractionOutput(_Model):
    observations: tuple[ObservationDraft, ...]
    abstentions: dict[str, int]  # reason code -> count (no text)
    report_date: DateFinding | None = None  # unambiguous report date for documents.document_date

    @property
    def needs_review_count(self) -> int:
        return sum(1 for o in self.observations if o.review_status == NEEDS_REVIEW)

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for observation in self.observations:
            for reason in observation.review_reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items()))
