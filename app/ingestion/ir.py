"""Intermediate representation (IR) of a parsed document, schema version 1.

The Docling parser (Task 2.4) fills these models; the ingestion service stores them as versioned
JSON next to Docling's own output and copies page text into the ``pages`` table.

Source of truth: the stored original PDF (byte-identical, identified by ``sha256``) is the
authoritative record. IR text is the *parser's extracted text*. Docling applies its own documented
normalization before we receive it (see ``ParserInfo.options``, e.g. ligature expansion and joining
of hyphenated line breaks); pages where that may have changed characters are flagged for review.

Rules:
- Text is stored **exactly** as the parser produced it: MediMind never strips, normalizes, corrects
  or reconstructs it. An OCR error such as "7p/6" for "g/dL" stays visible so later validation can
  flag it.
- OCR provenance (engine, version, backend, confidences) is recorded per page.
- Review flags are part of the IR (``ParsedPage.review_flags`` and document-level
  ``ParsedDocument.review_flags``), so they survive serialization and storage with the text they
  describe. Every OCR page carries ``ocr_text_unverified``: OCR confidence is not accuracy, so OCR
  text always needs verification against the original. A page is an OCR page only when OCR
  contributed text to it (see ``docling_mapping``); a page without visible text is not assumed to
  be OCR.
- A page whose text or table cells contain U+FFFD (REPLACEMENT CHARACTER) carries
  ``replacement_character``, and only such a page does (validated both ways). U+FFFD can come
  from Docling (docling-parse reports a glyph it cannot map to Unicode, which Docling turns into
  U+FFFD) or from the PDF itself (its own Unicode mapping stores U+FFFD); the stored text cannot
  tell these apart, so the flag records presence, not origin. Either way the character does not
  show what is printed there. The U+FFFD stays in the text.
- Text fields are excluded from ``repr()``, so a model that ends up in a log line or traceback
  never shows medical content.
- Models are frozen, use tuples for collections (immutable after validation; JSON arrays on disk)
  and reject unknown fields: a file from another schema version fails loudly.
- Validation errors never include input values (``hide_input_in_errors``), so a failing model
  cannot carry document text into a message, log or traceback.
"""

import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.errors import DocumentParseError

IR_SCHEMA_VERSION = 1
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
REPLACEMENT_CHARACTER = "\ufffd"
_SURROGATES = range(0xD800, 0xE000)  # U+D800-U+DFFF: never a character on its own


def _reject_lone_surrogates(value: str) -> str:
    """Reject a string holding a lone UTF-16 surrogate code point; never repair it.

    A Python string holds a supplementary character (e.g. U+1D707) as one code point, and JSON
    loading joins a valid surrogate *pair* into one, so only an unpaired half reaches this check.
    Such a string cannot be encoded as UTF-8: it could not be written to the IR file or stored.
    """
    if any(ord(character) in _SURROGATES for character in value):
        raise ValueError("contains a lone surrogate code point")
    return value


class ParseMethod(StrEnum):
    """How a page's text was obtained.

    Deliberately a copy of ``app.records.models.ParseMethod``: ingestion may not import the
    records layer. A unit test asserts both enums have the same values.
    """

    NATIVE = "native"
    OCR = "ocr"


class _Model(BaseModel):
    # hide_input_in_errors: Pydantic would otherwise put an excerpt of the input (which can be
    # document text) into every ValidationError message as `input_value=...`.
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class TableCell(_Model):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    text: str = Field(repr=False)  # exact cell text, including units as printed
    column_header: bool = False
    row_header: bool = False

    @field_validator("text")
    @classmethod
    def _text_has_no_lone_surrogates(cls, value: str) -> str:
        return _reject_lone_surrogates(value)


class ParsedTable(_Model):
    table_index: int = Field(ge=0)  # order of the table on its page, starting at 0
    num_rows: int = Field(ge=1)
    num_cols: int = Field(ge=1)
    cells: tuple[TableCell, ...]

    @model_validator(mode="after")
    def _cells_fit_the_grid_without_overlap(self) -> Self:
        occupied: set[tuple[int, int]] = set()
        for cell in self.cells:
            if cell.row + cell.row_span > self.num_rows or cell.col + cell.col_span > self.num_cols:
                raise ValueError(
                    f"cell at ({cell.row}, {cell.col}) with span ({cell.row_span}, "
                    f"{cell.col_span}) exceeds the {self.num_rows}x{self.num_cols} grid"
                )
            for r in range(cell.row, cell.row + cell.row_span):
                for c in range(cell.col, cell.col + cell.col_span):
                    if (r, c) in occupied:
                        raise ValueError(f"grid position ({r}, {c}) is covered by two cells")
                    occupied.add((r, c))
        return self


class OcrProvenance(_Model):
    """Which OCR produced a page's text, so later validation can treat its values with care."""

    engine: str = Field(min_length=1)  # e.g. "rapidocr"
    engine_version: str = Field(min_length=1)
    backend: str = Field(min_length=1)  # e.g. "torch"
    mean_confidence: float | None = Field(default=None, ge=0, le=1)
    min_confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("engine", "engine_version", "backend")
    @classmethod
    def _names_have_no_lone_surrogates(cls, value: str) -> str:
        return _reject_lone_surrogates(value)


class ReviewReason(StrEnum):
    """Why a page or document needs a human look. Flags never change the text."""

    EMPTY_PAGE = "empty_page"
    OCR_TEXT_UNVERIFIED = "ocr_text_unverified"  # every OCR page, whatever its confidence
    LOW_OCR_CONFIDENCE = "low_ocr_confidence"
    OCR_CONFIDENCE_UNAVAILABLE = "ocr_confidence_unavailable"
    MIXED_NATIVE_AND_OCR = "mixed_native_and_ocr"
    MULTI_PAGE_ITEM = "multi_page_item"
    MULTI_PAGE_ITEM_UNPLACED = "multi_page_item_unplaced"
    UNPLACED_ITEM = "unplaced_item"
    TABLE_STRUCTURE_INVALID = "table_structure_invalid"
    UNMAPPED_ITEM = "unmapped_item"
    DOCLING_NORMALIZED_TEXT = "docling_normalized_text"
    # The page's PDF text layer has non-whitespace text that paints no ink (rendering mode 3
    # "invisible" or 7 "clip only"; detected with PDFium), whether or not Docling kept that text.
    # It says the PDF holds text a reader cannot see, not that the text is wrong. Page-level only.
    INVISIBLE_TEXT_LAYER = "invisible_text_layer"
    # The page text or a table cell contains U+FFFD: a character the parser could not map to
    # Unicode, or one the PDF itself stores as U+FFFD (the origin cannot be told apart). Compare
    # with the original; the text is kept unchanged. Page-level only, on native and OCR pages.
    REPLACEMENT_CHARACTER = "replacement_character"


# Flags that only make sense on a page whose text came (at least partly) from OCR.
OCR_ONLY_REASONS = frozenset(
    {
        ReviewReason.OCR_TEXT_UNVERIFIED,
        ReviewReason.LOW_OCR_CONFIDENCE,
        ReviewReason.OCR_CONFIDENCE_UNAVAILABLE,
        ReviewReason.MIXED_NATIVE_AND_OCR,
    }
)
# Flags allowed on the document (an item that could not be placed on any page). `unmapped_item`
# is also valid on a page (an item with a known page that could not be mapped); `unplaced_item` is
# document-only by definition.
DOCUMENT_REASONS = frozenset({ReviewReason.UNPLACED_ITEM, ReviewReason.UNMAPPED_ITEM})
DOCUMENT_ONLY_REASONS = frozenset({ReviewReason.UNPLACED_ITEM})


def canonical_flags(reasons: Iterable[ReviewReason]) -> tuple[ReviewReason, ...]:
    """The one accepted form of a flag set: each flag once, sorted by value (deterministic JSON).

    Producers (the mapping) build flags with this; validation rejects any other form instead of
    silently rewriting it, so a stored file always loads back byte-for-byte the same.
    """
    return tuple(sorted(set(reasons), key=lambda reason: reason.value))


def contains_replacement_character(text: str, tables: Iterable[ParsedTable]) -> bool:
    """True if U+FFFD occurs in the page text or in any table cell text (detection only)."""
    return REPLACEMENT_CHARACTER in text or any(
        REPLACEMENT_CHARACTER in cell.text for table in tables for cell in table.cells
    )


def _require_canonical(value: tuple[ReviewReason, ...]) -> tuple[ReviewReason, ...]:
    if value != canonical_flags(value):
        raise ValueError("review_flags must be unique and sorted by value")
    return value


class ParsedPage(_Model):
    page_number: int = Field(ge=1)
    text: str = Field(repr=False)  # exact page text; may be empty, never filled in
    parse_method: ParseMethod
    # Visible non-whitespace characters in the PDF's own text layer (PDFium); invisible text
    # (rendering mode 3 or 7, e.g. a searchable scan's OCR layer) is not counted.
    native_char_count: int = Field(ge=0)
    ocr: OcrProvenance | None = None
    tables: tuple[ParsedTable, ...] = ()
    review_flags: tuple[ReviewReason, ...] = ()

    @field_validator("review_flags")
    @classmethod
    def _flags_are_canonical(cls, value: tuple[ReviewReason, ...]) -> tuple[ReviewReason, ...]:
        return _require_canonical(value)

    @field_validator("text")
    @classmethod
    def _text_has_no_lone_surrogates(cls, value: str) -> str:
        return _reject_lone_surrogates(value)

    @model_validator(mode="after")
    def _ocr_provenance_matches_method(self) -> Self:
        is_ocr = self.parse_method is ParseMethod.OCR
        if is_ocr != (self.ocr is not None):
            raise ValueError("OCR provenance is required for OCR pages and only for OCR pages")
        indexes = [table.table_index for table in self.tables]
        if indexes != list(range(len(indexes))):
            raise ValueError("table_index values must be 0, 1, 2, ... in order")
        flags = set(self.review_flags)
        if is_ocr != (ReviewReason.OCR_TEXT_UNVERIFIED in flags):
            raise ValueError("ocr_text_unverified must be set on every OCR page and only there")
        if not is_ocr and flags & OCR_ONLY_REASONS:
            raise ValueError("OCR review flags on a page that was not read by OCR")
        if flags & DOCUMENT_ONLY_REASONS:
            raise ValueError("unplaced_item is a document-level flag")
        if ReviewReason.EMPTY_PAGE in flags and not self.is_empty:
            raise ValueError("empty_page on a page that has text or tables")
        if ReviewReason.MULTI_PAGE_ITEM_UNPLACED in flags and (
            ReviewReason.MULTI_PAGE_ITEM not in flags
        ):
            raise ValueError("multi_page_item_unplaced requires multi_page_item")
        if (ReviewReason.REPLACEMENT_CHARACTER in flags) != contains_replacement_character(
            self.text, self.tables
        ):
            raise ValueError(
                "replacement_character must be set exactly on pages whose text or table cells "
                "contain U+FFFD"
            )
        return self

    @property
    def has_tables(self) -> bool:
        return bool(self.tables)

    @property
    def is_empty(self) -> bool:
        """No text and no tables: the document needs review; nothing is invented to fill it."""
        return not self.text.strip() and not self.tables


class ParserInfo(_Model):
    name: str = Field(min_length=1)  # e.g. "docling"
    version: str = Field(min_length=1)
    options: dict[str, bool | int | str] = Field(default_factory=dict)

    @field_validator("name", "version")
    @classmethod
    def _names_have_no_lone_surrogates(cls, value: str) -> str:
        return _reject_lone_surrogates(value)

    @field_validator("options")
    @classmethod
    def _options_have_no_lone_surrogates(
        cls, value: dict[str, bool | int | str]
    ) -> dict[str, bool | int | str]:
        for key, option in value.items():
            _reject_lone_surrogates(key)
            if isinstance(option, str):
                _reject_lone_surrogates(option)
        return value


class ParsedDocument(_Model):
    schema_version: Literal[1] = IR_SCHEMA_VERSION
    sha256: str  # of the original file, lowercase hex
    page_count: int = Field(ge=1)
    parser: ParserInfo
    pages: tuple[ParsedPage, ...]
    review_flags: tuple[ReviewReason, ...] = ()  # document-level only

    @field_validator("review_flags")
    @classmethod
    def _document_flags_are_canonical(
        cls, value: tuple[ReviewReason, ...]
    ) -> tuple[ReviewReason, ...]:
        if not set(value) <= DOCUMENT_REASONS:
            raise ValueError("only unplaced_item and unmapped_item are document-level flags")
        return _require_canonical(value)

    @field_validator("sha256")
    @classmethod
    def _sha256_is_lowercase_hex(cls, value: str) -> str:
        if not _SHA256_HEX.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def _pages_are_numbered_1_to_n(self) -> Self:
        numbers = [page.page_number for page in self.pages]
        if numbers != list(range(1, self.page_count + 1)):
            raise ValueError("pages must be numbered 1..page_count in order, without gaps")
        return self


def dump_ir(document: ParsedDocument) -> bytes:
    """Serialize to UTF-8 JSON. Field order is fixed by the models, so output is deterministic."""
    return document.model_dump_json(indent=2).encode("utf-8")


_KNOWN_FIELD_NAMES = frozenset(
    name
    for model in (TableCell, ParsedTable, OcrProvenance, ParsedPage, ParserInfo, ParsedDocument)
    for name in model.model_fields
)


def _safe_location(location: tuple[int | str, ...]) -> str:
    parts = [
        str(part)
        if (isinstance(part, int) and not isinstance(part, bool)) or part in _KNOWN_FIELD_NAMES
        else "<field>"
        for part in location
    ]
    return ".".join(parts) or "<root>"


def sanitized_problems(exc: ValidationError, limit: int = 5) -> str:
    """Describe a Pydantic error safely: known IR field locations and error type codes only.

    Pydantic's message can include input values (document text), and locations can contain
    arbitrary keys from untrusted input (an unknown field, a key inside ``options``), so only known
    IR field names and list indexes are kept; anything else becomes "<field>".
    """
    problems = sorted({f"{_safe_location(e['loc'])}: {e['type']}" for e in exc.errors()})
    return "; ".join(problems[:limit])


def load_ir(data: bytes) -> ParsedDocument:
    """Load and validate an IR file.

    Raises:
        DocumentParseError: kind ``"ir_invalid"`` if the file is not valid IR of this schema
            version. The message names only known IR fields, list indexes and Pydantic error
            type codes: never input values or keys taken from the file.
    """
    try:
        return ParsedDocument.model_validate_json(data)
    except ValidationError as exc:
        problems = sanitized_problems(exc)
    # Raised outside the except block: no chained exception can carry the original values.
    raise DocumentParseError(
        f"Invalid IR file (schema version {IR_SCHEMA_VERSION}): {problems}", kind="ir_invalid"
    )
