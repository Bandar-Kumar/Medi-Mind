"""Fixed reason codes (docs/PHASE_3_PLAN.md §4.4, §4.5). Codes are stored and logged; text never is.

Three kinds, never mixed:
- ``Abstention``: no fact is written; the run counts the reason.
- ``ReviewReason``: the fact is stored with ``review_status = 'needs_review'``.
- ``QualityNote``: the fact is stored with its status unchanged; the note only informs.
"""

from collections.abc import Iterable
from enum import StrEnum


class Abstention(StrEnum):
    PAGE_TEXT_MISMATCH = "page_text_mismatch"
    TABLE_UNRECOGNIZED = "table_unrecognized"
    TABLE_BLOCK_NOT_FOUND = "table_block_not_found"
    TABLE_BLOCK_NOT_UNIQUE = "table_block_not_unique"
    TABLE_BLOCK_NOT_RENDERED = "table_block_not_rendered"
    HISTORICAL_COLUMN = "historical_column"
    MULTIPLE_SUBJECTS = "multiple_subjects"
    UNPLACED_ITEM = "unplaced_item"
    ROW_SPANS_ROWS = "row_spans_rows"
    CELL_CONTAINS_SEPARATOR = "cell_contains_separator"
    MERGED_VALUE_CELL = "merged_value_cell"
    EMPTY_VALUE = "empty_value"
    PLACEHOLDER_VALUE = "placeholder_value"
    EMPTY_ANALYTE = "empty_analyte"
    MISSING_CELL = "missing_cell"
    LINE_ANALYTE_UNMAPPED = "line_analyte_unmapped"
    SPAN_VERIFICATION_FAILED = "span_verification_failed"
    DUPLICATE_SOURCE = "duplicate_source"


class ReviewReason(StrEnum):
    UNMAPPED_ANALYTE = "unmapped_analyte"
    UNKNOWN_UNIT = "unknown_unit"
    MISSING_UNIT = "missing_unit"
    UNPARSED_VALUE = "unparsed_value"
    IMPLAUSIBLE_VALUE = "implausible_value"
    AMBIGUOUS_DATE = "ambiguous_date"
    TWO_DIGIT_YEAR = "two_digit_year"
    UNPARSED_DATE = "unparsed_date"
    IMPLAUSIBLE_DATE = "implausible_date"
    CONFLICTING_DATES = "conflicting_dates"
    CONFLICTING_VALUE_IN_DOCUMENT = "conflicting_value_in_document"
    FLAG_RANGE_DISAGREEMENT = "flag_range_disagreement"
    OCR_UNVERIFIED_SOURCE = "ocr_unverified_source"
    REPLACEMENT_CHARACTER_IN_SOURCE = "replacement_character_in_source"
    SOURCE_TEXT_NORMALIZED = "source_text_normalized"
    TABLE_STRUCTURE_INVALID_SOURCE = "table_structure_invalid_source"
    INVISIBLE_TEXT_LAYER_SOURCE = "invisible_text_layer_source"


class QualityNote(StrEnum):
    REF_RANGE_UNPARSED = "ref_range_unparsed"
    UNIT_NOT_CONVERTIBLE = "unit_not_convertible"
    EFFECTIVE_DATE_FROM_REPORT = "effective_date_from_report"
    NO_EFFECTIVE_DATE = "no_effective_date"
    TIME_UNPARSED = "time_unparsed"


def canonical_codes(codes: Iterable[str]) -> tuple[str, ...]:
    """Unique, sorted string codes (the stored form of reasons and notes)."""
    return tuple(sorted({str(code) for code in codes}))
