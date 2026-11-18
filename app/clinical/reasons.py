"""Fixed Phase 6 reason codes. Codes are stored and logged; text never is.

The same three kinds as Phase 3 (``app.extraction.reasons``), never mixed: an abstention writes no
fact; a review reason stores the fact as ``needs_review``; a quality note only informs. The Phase 3
date and page-source review reasons are reused unchanged.
"""

from enum import StrEnum


class ClinicalAbstention(StrEnum):
    PAGE_TEXT_MISMATCH = "page_text_mismatch"
    MULTIPLE_SUBJECTS = "multiple_subjects"
    SPAN_VERIFICATION_FAILED = "span_verification_failed"
    MEDICATION_ITEM_UNPARSED = "medication_item_unparsed"
    ALLERGY_ITEM_UNPARSED = "allergy_item_unparsed"
    ALLERGY_STATUS_UNKNOWN = "allergy_status_unknown"  # "Not recorded": unknown, never "none"
    EMPTY_ITEM = "empty_item"
    DUPLICATE_SOURCE = "duplicate_source"


class ClinicalReviewReason(StrEnum):
    UNMAPPED_MEDICATION = "unmapped_medication"
    AMBIGUOUS_MEDICATION_NAME = "ambiguous_medication_name"


class ClinicalQualityNote(StrEnum):
    NO_DOCUMENT_DATE = "no_document_date"
    STRENGTH_NOT_DOCUMENTED = "strength_not_documented"
    FREQUENCY_NOT_DOCUMENTED = "frequency_not_documented"
    DURATION_NOT_DOCUMENTED = "duration_not_documented"
    UNPARSED_TEXT_IN_ITEM = "unparsed_text_in_item"


class TypeAbstention(StrEnum):
    """Why ``clinical-doctype-1`` names no type (the stage records a ``skipped`` run)."""

    UNCLASSIFIED = "unclassified"
    AMBIGUOUS_TYPE = "ambiguous_type"
    UNSUPPORTED_TYPE = "unsupported_type"
    NO_TEXT = "no_text"
