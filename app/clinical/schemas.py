"""The clinical extractor's output contract (docs/PHASE_6_PLAN.md §6).

Frozen Pydantic models like Phase 3's ``ObservationDraft``: unknown fields are refused, validation
errors never contain input values, and text fields are excluded from ``repr``. Raw, normalized and
derived values are separate fields. No field ever holds generated text: every ``*_raw`` value and
the excerpt are exact slices of the stored page text.
"""

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.clinical.reasons import ClinicalReviewReason
from app.extraction.reasons import ReviewReason

VALIDATED = "validated"
NEEDS_REVIEW = "needs_review"
DATE_SOURCES = ("document", "stated")
_REVIEW_CODES = frozenset(ReviewReason._value2member_map_) | frozenset(
    ClinicalReviewReason._value2member_map_
)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class DateSpan(_Model):
    """A printed date with its exact span and parse result."""

    page_number: int = Field(ge=1)
    raw: str = Field(repr=False)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    value: dt.date | None
    precision: str | None
    reasons: tuple[str, ...] = ()


class ClinicalDraft(_Model):
    """Fields shared by every clinical fact. Offsets index the stored ``pages.text``."""

    page_number: int = Field(ge=1)
    source_locator: str
    source_excerpt: str = Field(repr=False, max_length=1000)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    subject: str = "patient"
    negated: bool = False

    date: dt.date | None = None
    date_precision: str | None = None
    date_raw: str | None = Field(default=None, repr=False)
    date_source: str | None = None
    date_page_number: int | None = None
    date_char_start: int | None = None
    date_char_end: int | None = None

    review_status: str
    review_reasons: tuple[str, ...] = ()
    quality_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> "ClinicalDraft":
        if self.char_start > self.char_end:
            raise ValueError("char_start after char_end")
        if self.review_status not in (VALIDATED, NEEDS_REVIEW):
            raise ValueError("extraction stores only validated or needs_review")
        if (self.review_status == NEEDS_REVIEW) != bool(self.review_reasons):
            raise ValueError("needs_review exactly when there are reasons")
        if any(reason not in _REVIEW_CODES for reason in self.review_reasons):
            raise ValueError("unknown review reason")
        if self.subject not in ("patient", "family", "other"):
            raise ValueError("unknown subject")
        if (self.date is None) != (self.date_precision is None):
            raise ValueError("date and precision go together")
        if (self.date is None) != (self.date_source is None):
            raise ValueError("date and date_source go together")
        if self.date_source is not None and self.date_source not in DATE_SOURCES:
            raise ValueError("unknown date source")
        span = (self.date_page_number, self.date_char_start, self.date_char_end)
        if any(v is None for v in span) and any(v is not None for v in span):
            raise ValueError("date span fields go together")
        if self.date is not None and self.date_page_number is None:
            raise ValueError("a date needs its own span")
        return self


class MedicationDraft(ClinicalDraft):
    name_raw: str = Field(repr=False)
    name_norm: str | None = None
    name_match: str | None = None
    dosage_form: str | None = None
    strength_raw: str | None = Field(default=None, repr=False)
    strength_value: Decimal | None = None
    strength_unit: str | None = None
    dose_raw: str | None = Field(default=None, repr=False)
    route_raw: str | None = Field(default=None, repr=False)
    route_norm: str | None = None
    frequency_raw: str | None = Field(default=None, repr=False)
    frequency_per_day: int | None = None
    as_needed: bool | None = None
    duration_raw: str | None = Field(default=None, repr=False)
    duration_value: int | None = None
    duration_unit: str | None = None
    instructions_raw: str | None = Field(default=None, repr=False)
    mention_context: str

    @model_validator(mode="after")
    def _medication(self) -> "MedicationDraft":
        if (self.strength_value is None) != (self.strength_unit is None):
            raise ValueError("strength value and unit go together")
        if (self.duration_value is None) != (self.duration_unit is None):
            raise ValueError("duration value and unit go together")
        if (self.name_norm is None) != (self.name_match is None):
            raise ValueError("a normalized name needs its match kind")
        return self


class AllergyDraft(ClinicalDraft):
    substance_raw: str = Field(repr=False)
    substance_norm: str | None = None
    reaction_raw: str | None = Field(default=None, repr=False)
    severity_raw: str | None = Field(default=None, repr=False)
    severity_norm: str | None = None
    allergy_status: str

    @model_validator(mode="after")
    def _allergy(self) -> "AllergyDraft":
        if self.allergy_status not in ("confirmed", "suspected", "none_known"):
            raise ValueError("unknown allergy status")
        if (self.allergy_status == "none_known") != self.negated:
            raise ValueError("none_known exactly when negated")
        return self


class EventDraft(ClinicalDraft):
    event_type: str
    event_context: str
    description_raw: str = Field(repr=False)


class ClinicalExtractionOutput(_Model):
    clinical_type: str
    medications: tuple[MedicationDraft, ...] = ()
    allergies: tuple[AllergyDraft, ...] = ()
    events: tuple[EventDraft, ...] = ()
    abstentions: dict[str, int] = {}  # reason code -> count (no text)
    document_date: DateSpan | None = None

    @property
    def drafts(self) -> tuple[ClinicalDraft, ...]:
        return (*self.medications, *self.allergies, *self.events)

    @property
    def needs_review_count(self) -> int:
        return sum(1 for d in self.drafts if d.review_status == NEEDS_REVIEW)

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for draft in self.drafts:
            for reason in draft.review_reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items()))
