"""Typed contracts of the copilot (Phase 5; ADR-040). Pydantic, frozen, validated.

- ``RoutePlan``     what the deterministic router decided (route, intent, analytes, dates).
- ``EvidenceItem``  one numbered source given to the generator: a stored fact (``F<n>``) or a
                    retrieved page passage (``D<n>``). Text fields are exact stored evidence and are
                    hidden from ``repr``; they are never logged.
- ``DraftAnswer``   the generator's structured output: claims, each citing source ids. This is
                    the schema an LLM must return; the extractive composer returns it too.
- ``CitationIssue`` one deterministic validation failure (a fixed code, never text).
"""

import datetime as dt
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SOURCE_ID_PATTERN = r"^[FD][1-9][0-9]{0,2}$"
MAX_CLAIMS = 20
MAX_CLAIM_CHARS = 500


class Route(StrEnum):
    DOCUMENT_SEARCH = "document_search"  # page passages from hybrid retrieval
    STRUCTURED_FACTS = "structured_facts"  # one value: latest, or on a date
    LONGITUDINAL = "longitudinal"  # history, date range, comparison with the previous result
    COMBINED = "combined"  # structured facts plus the document passages behind them
    UNSUPPORTED = "unsupported"  # outside the records-only scope (ADR-022), e.g. "What is HbA1c?"
    REFUSAL = "refusal"  # advice, diagnosis, treatment, injection or other patients' data


class Intent(StrEnum):
    LATEST = "latest"
    ON_DATE = "on_date"
    HISTORY = "history"
    COMPARE_PREVIOUS = "compare_previous"
    LIST_ANALYTES = "list_analytes"
    CONFLICTS = "conflicts"
    DOCUMENT_LOOKUP = "document_lookup"
    NONE = "none"


class RefusalKind(StrEnum):
    ADVICE = "advice"  # treatment, medicine, dose, tests to take
    DIAGNOSIS = "diagnosis"  # what disease / condition / diagnosis
    INJECTION = "injection"  # instruction override, system prompt, other patients
    INVALID = "invalid"  # empty or too long
    GENERAL_KNOWLEDGE = "general_knowledge"  # used with Route.UNSUPPORTED


class RoutePlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route: Route
    intent: Intent = Intent.NONE
    analytes: tuple[str, ...] = ()  # canonical names from the reference data
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    refusal: RefusalKind | None = None
    ambiguous: bool = False  # e.g. a date the router could not read exactly
    # Words that look like a test name MediMind does not know (ADR-041); never widened.
    unrecognised: tuple[str, ...] = Field(default=(), repr=False)
    reasons: tuple[str, ...] = ()  # fixed rule codes that fired, for the activity summary

    @field_validator("date_to")
    @classmethod
    def _ordered(cls, value: dt.date | None, info) -> dt.date | None:  # noqa: ANN001
        start = info.data.get("date_from")
        if value is not None and start is not None and start > value:
            raise ValueError("date_from is after date_to")
        return value


class SourceKind(StrEnum):
    FACT = "fact"
    PASSAGE = "passage"


class EvidenceItem(BaseModel):
    """One numbered source. ``excerpt`` is the exact stored text (fact span or chunk)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    kind: SourceKind
    document_id: str
    page_number: int = Field(ge=1)
    excerpt: str = Field(repr=False, min_length=1)
    filename: str = Field(default="", repr=False)
    document_date: dt.date | None = None
    identity_check: str = "unknown"  # match / unknown (mismatch documents are never used)
    parse_method: str = "native"
    chunk_id: str | None = None  # passages: the stored chunk the excerpt is
    # fact fields (None for passages)
    fact_id: str | None = None
    analyte: str | None = None
    value: str | None = Field(default=None, repr=False)  # shown value (correction, else machine)
    unit: str | None = None
    comparator: str | None = None
    qualitative: str | None = Field(default=None, repr=False)
    value_raw: str | None = Field(default=None, repr=False)  # exactly as printed
    unit_raw: str | None = Field(default=None, repr=False)
    ref_range_raw: str | None = Field(default=None, repr=False)
    effective_date: dt.date | None = None
    date_precision: str | None = None
    review_status: str | None = None  # validated / verified / corrected (never others)
    value_source: str | None = None  # machine / human_correction
    in_conflict: bool = False
    score: float | None = None  # passages: retrieval score of the mode used

    @property
    def unverified(self) -> bool:
        return self.review_status == "validated"

    @property
    def corrected(self) -> bool:
        return self.value_source == "human_correction"


ClaimKind = Literal["fact", "calculation", "summary"]


class DraftClaim(BaseModel):
    """One statement. ``fact`` and ``calculation`` claims must cite at least one source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_CLAIM_CHARS, repr=False)
    citations: tuple[str, ...] = Field(default=(), max_length=40)
    kind: ClaimKind = "fact"


DraftStatus = Literal["answered", "partial", "insufficient_evidence"]


class DraftAnswer(BaseModel):
    """The generator's structured output (the JSON schema an LLM must follow)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DraftStatus
    claims: tuple[DraftClaim, ...] = Field(default=(), max_length=MAX_CLAIMS)
    missing: tuple[str, ...] = Field(default=(), max_length=10, repr=False)


class CitationIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    claim_index: int | None = None
    source_id: str | None = None


class CopilotStatus(StrEnum):
    ANSWERED = "answered"
    PARTIAL = "partial"  # some requested information was found, some was not
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REFUSED = "refused"  # advice / diagnosis / injection (fixed response)
    UNSUPPORTED = "unsupported"  # outside the records-only scope (fixed response)
    VALIDATION_FAILED = "validation_failed"  # the generated answer failed citation validation
    GENERATION_FAILED = "generation_failed"  # the generator returned nothing usable
