"""Map Docling's output to the MediMind IR. Pure functions: no converter, no models, no torch.

``parsing.py`` runs Docling and hands over two things:
- the ``DoclingDocument`` (items with page provenance, tables with cell structure);
- per-page *signals*: Docling's text-line cells (with ``from_ocr``, OCR ``confidence`` and whether
  the cell is visible) and the PDF's own text layer read with PDFium (pypdfium2), reduced to
  counts (``TextLayerStats``): visible characters, watched visible characters, invisible
  characters.

Invisible PDF text (rendering mode 3 "invisible" or 7 "clip only": no ink on the page, e.g. the
OCR text layer of a "searchable scan") is not native text a reader can see. It is excluded from
every other signal: ``native_char_count``, ``mixed_native_and_ocr``, both
``docling_normalized_text`` checks. Docling makes the same distinction (it OCRs an image under
invisible text). Its presence is recorded by its own page flag, ``invisible_text_layer``.

A page is an OCR page only when Docling's line cells include OCR text (``from_ocr``); see
``_build_page``.

Evidence rules implemented here:
- **Pages are matched by page number**, never by list position. Docling and the IR both number
  pages from 1. Missing, duplicate or unexpected page numbers are refused (``unexpected_output``):
  page provenance is never guessed.
- **Text is copied verbatim** from Docling: MediMind never strips, normalizes, corrects or
  reconstructs it. Docling changes some characters before we receive them: its PDF text extractor
  (docling-parse) maps e.g. the en dash and the minus sign to "-", and its text normalization
  expands ligatures and joins hyphenated line breaks. This is recorded in ``ParserInfo`` and
  *detected* per page (``docling_normalized_text`` flag) by two checks: a watched character is in
  the PDF's visible text layer (PDFium) more often than in Docling's text for that page; or
  Docling's visible line cells contain characters it rewrites, or a line-end hyphen it may drop.
  It is never repaired here. The stored original PDF remains the authoritative source.
- **Multi-page items** are split across pages only when Docling's per-page character spans verify
  exactly; otherwise the item is placed on no page, every page it touches is flagged, and it stays
  available in Docling's JSON. Page text never contains text Docling did not place on that page.
- **OCR confidence** is taken from Docling's cells only; missing or invalid values stay ``None``.
- Anything unusual becomes a ``ReviewFlag``: flags never change the text.

Docling (docling-core) types are only imported inside functions: importing this module stays cheap.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.core.errors import DocumentParseError
from app.ingestion.ir import (
    OcrProvenance,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    ParseMethod,
    ParserInfo,
    ReviewReason,
    TableCell,
    canonical_flags,
    sanitized_problems,
)

if TYPE_CHECKING:
    from docling_core.types.doc import DoclingDocument

# Initial value, to be calibrated in Phase 3 evaluation. RapidOCR already drops boxes below 0.5.
LOW_OCR_CONFIDENCE_THRESHOLD = 0.80

# Characters Docling's extraction is known to replace (measured in Task 2.4: docling-parse maps "–"
# and "−" to "-"; Docling's text normalization maps quotes, bullets, "⁄" and ligatures). A page is
# flagged when Docling's text for it has fewer occurrences of one of them than its PDF text layer.
WATCHED_CHARACTERS = frozenset("–−’“”•ﬁ⁄")

# Characters Docling 2.150.1 replaces in extracted text (docling/utils/text_normalization.py):
# typographic map, ligatures, a private-use glyph, and STX restored to "-".
_NORMALIZED_CHARACTERS = frozenset("⁄’‘“”•ﬀﬁﬂﬃﬄﬅﬆĲĳ\x02")
_ARABIC_PRESENTATION_FORMS = ((0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
# A line ending in a letter or digit followed by a hyphen: Docling drops that hyphen when it joins
# the next line ("Anti-"/"HBs" -> "AntiHBs", "4.0-"/"6.0" -> "4.06.0").
_HYPHEN_AT_LINE_END = re.compile(r"[^\W_]-$")

# PDF 32000 text rendering modes that paint no ink: 3 (invisible) and 7 (add to clipping path
# only). Docling 2.150.1 uses the same set (``_INVISIBLE_RENDERING_MODES`` in its docling-parse
# backend), and PDFium's ``FPDF_TEXTRENDERMODE_INVISIBLE``/``_CLIP`` have the same numbers. Every
# other mode, including "unknown" (-1, which docling-parse reports for ordinary filled text), is
# treated as visible: an unclear case errs towards counting text and flagging, never hiding it.
INVISIBLE_RENDER_MODES = frozenset({3, 7})


@dataclass(frozen=True, order=True)
class ReviewFlag:
    """Something a reviewer should look at. ``page_number`` is None for document-level flags."""

    page_number: int | None
    reason: ReviewReason


@dataclass(frozen=True)
class LineCell:
    """One text-line cell from Docling's parsed page.

    This is docling-parse's output: after its own character normalization (e.g. "–" is already
    "-"), before Docling's text normalization. It is *not* the raw PDF text.
    """

    text: str = field(repr=False)
    from_ocr: bool
    confidence: float | None  # as reported by Docling; validated when used
    # False when docling-parse reports an invisible rendering mode for the cell. A cell is a whole
    # line: visible and invisible text on one line can be merged into one cell, which then counts
    # as visible (detection errs towards flagging).
    visible: bool = True


@dataclass(frozen=True)
class TextLayerStats:
    """What is kept of one page's PDF text layer: counts only, never the text."""

    visible_char_count: int  # visible non-whitespace characters
    # How often each WATCHED_CHARACTER occurs in the *visible* text, e.g. (("–", 3),); sorted,
    # zero counts omitted.
    watched_counts: tuple[tuple[str, int], ...] = ()
    # Non-whitespace characters drawn with an invisible rendering mode (3 or 7).
    invisible_char_count: int = 0


def text_layer_stats(characters: Iterable[tuple[str, bool]]) -> TextLayerStats:
    """Reduce a page's (character, visible) pairs to counts as they stream past; nothing is kept.

    Visible characters feed ``visible_char_count`` and ``watched_counts``; invisible ones only
    ``invisible_char_count``. Whitespace is never counted (watched characters are not whitespace).
    """
    visible_count = 0
    invisible_count = 0
    watched: Counter[str] = Counter()
    for character, visible in characters:
        if not visible:
            if not character.isspace():
                invisible_count += 1
            continue
        if not character.isspace():
            visible_count += 1
        if character in WATCHED_CHARACTERS:
            watched[character] += 1
    return TextLayerStats(
        visible_char_count=visible_count,
        watched_counts=tuple(sorted(watched.items())),
        invisible_char_count=invisible_count,
    )


@dataclass(frozen=True)
class PageSignals:
    page_number: int
    native_char_count: int  # visible non-whitespace characters in the PDF's text layer (PDFium)
    cells: tuple[LineCell, ...]
    # How often each WATCHED_CHARACTER occurs in the PDF's visible text layer, e.g. (("–", 3),).
    # Only these counts are kept, never the text. Empty when the page has no visible text layer.
    watched_counts: tuple[tuple[str, int], ...] = ()
    # Invisible (mode 3 or 7) non-whitespace characters in the PDF's text layer (PDFium). Any
    # value above 0 flags the page ``invisible_text_layer``.
    invisible_char_count: int = 0


@dataclass(frozen=True)
class OcrEngine:
    engine: str
    engine_version: str
    backend: str


@dataclass(frozen=True)
class MappingResult:
    document: ParsedDocument

    @property
    def review_flags(self) -> tuple[ReviewFlag, ...]:
        return review_flags_of(self.document)


def review_flags_of(document: ParsedDocument) -> tuple[ReviewFlag, ...]:
    """All flags stored in the IR, as (page_number, reason); page_number None = whole document."""
    flags = [ReviewFlag(None, reason) for reason in document.review_flags]
    for page in document.pages:
        flags.extend(ReviewFlag(page.page_number, reason) for reason in page.review_flags)
    return tuple(flags)


def _unexpected(detail: str) -> DocumentParseError:
    return DocumentParseError(
        f"Docling output does not match the PDF ({detail})", kind="unexpected_output"
    )


def _is_page_number(value: object, page_count: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= page_count


# --- page signals ---------------------------------------------------------------------------------


def build_page_signals(
    pages: Iterable[Any], text_layers: Mapping[int, TextLayerStats], page_count: int
) -> dict[int, PageSignals]:
    """Match Docling's per-page results to page numbers 1..page_count.

    ``pages`` are Docling ``Page`` objects (``page_no``, ``parsed_page.textline_cells``).
    ``text_layers`` maps page numbers to the counts taken from the PDF's visible text (PDFium).

    Raises:
        DocumentParseError: ``unexpected_output`` for a missing, duplicate or out-of-range page
            number, or a page without parsed cells. Provenance is never guessed.
    """
    expected = set(range(1, page_count + 1))
    if set(text_layers) != expected:
        raise _unexpected("text layers do not cover every page")
    signals: dict[int, PageSignals] = {}
    for page in pages:
        number = getattr(page, "page_no", None)
        if not _is_page_number(number, page_count):
            raise _unexpected("unexpected page number")
        if number in signals:
            raise _unexpected("duplicate page number")
        parsed = getattr(page, "parsed_page", None)
        if parsed is None:
            raise _unexpected("a page has no parsed cells")
        cells = tuple(_line_cell(cell) for cell in parsed.textline_cells)
        layer = text_layers[number]
        signals[number] = PageSignals(
            page_number=number,
            native_char_count=layer.visible_char_count,
            cells=cells,
            watched_counts=layer.watched_counts,
            invisible_char_count=layer.invisible_char_count,
        )
    if set(signals) != expected:
        raise _unexpected("a page is missing from Docling's results")
    return signals


def _line_cell(cell: Any) -> LineCell:
    # docling-parse sets orig = text = its own (already character-normalized) output; Docling's
    # text normalization happens later, when it builds document items. Prefer orig when present.
    orig = getattr(cell, "orig", None)
    text = orig if isinstance(orig, str) and orig else cell.text
    confidence = getattr(cell, "confidence", None)
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        confidence = None
    # Only PDF cells have a rendering mode (an int enum); OCR cells have none and are visible.
    mode = getattr(cell, "rendering_mode", None)
    invisible = (
        isinstance(mode, int) and not isinstance(mode, bool) and int(mode) in INVISIBLE_RENDER_MODES
    )
    return LineCell(
        text=text, from_ocr=bool(cell.from_ocr), confidence=confidence, visible=not invisible
    )


# --- document mapping -----------------------------------------------------------------------------


@dataclass
class _PageBuilder:
    segments: list[str] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)


class _Flags:
    def __init__(self) -> None:
        self._flags: set[ReviewFlag] = set()

    def add(self, page_number: int | None, reason: ReviewReason) -> None:
        self._flags.add(ReviewFlag(page_number, reason))

    def reasons(self, page_number: int | None) -> tuple[ReviewReason, ...]:
        return canonical_flags(f.reason for f in self._flags if f.page_number == page_number)


def map_document(
    document: "DoclingDocument",
    signals: Mapping[int, PageSignals],
    *,
    page_count: int,
    sha256: str,
    parser: ParserInfo,
    ocr_engine: OcrEngine,
) -> MappingResult:
    """Build the IR for one document.

    Raises:
        DocumentParseError: ``unexpected_output`` when Docling's pages do not match the PDF's
            pages, or the result fails IR validation (message: field locations and codes only).
    """
    from docling_core.types.doc import ContentLayer, PictureItem, TableItem, TextItem

    expected = set(range(1, page_count + 1))
    if set(document.pages) != expected or set(signals) != expected:
        raise _unexpected("page numbers differ")

    builders = {number: _PageBuilder() for number in sorted(expected)}
    flags = _Flags()
    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}  # furniture = page headers/footers
    for item, _level in document.iterate_items(
        traverse_pictures=True, included_content_layers=layers
    ):
        if isinstance(item, TableItem):
            _place_table(item, builders, flags, page_count)
        elif isinstance(item, TextItem):
            _place_text(item, builders, flags, page_count)
        elif isinstance(item, PictureItem):
            continue  # no text of its own; its child text items are yielded separately
        else:
            flags.add(_first_page(item, page_count), ReviewReason.UNMAPPED_ITEM)

    problems: str | None = None
    try:
        # Pages (and their OCR provenance) are built inside this boundary too: any validation
        # error becomes a sanitized `unexpected_output`, never a raw Pydantic error.
        pages = [
            _build_page(number, builders[number], signals[number], ocr_engine, flags)
            for number in sorted(expected)
        ]
        parsed = ParsedDocument(
            sha256=sha256,
            page_count=page_count,
            parser=parser,
            pages=pages,
            review_flags=flags.reasons(None),
        )
    except ValidationError as exc:
        problems = sanitized_problems(exc)
    if problems is not None:
        raise _unexpected(f"IR validation failed: {problems}")
    return MappingResult(document=parsed)


def _first_page(item: Any, page_count: int) -> int | None:
    prov = getattr(item, "prov", None) or []
    number = getattr(prov[0], "page_no", None) if prov else None
    return number if _is_page_number(number, page_count) else None


def _prov_pages(item: Any, page_count: int, flags: _Flags) -> list[int] | None:
    """The item's page numbers, or None (flagged) when it cannot be placed at all."""
    numbers = [getattr(prov, "page_no", None) for prov in (item.prov or [])]
    if not numbers or not all(_is_page_number(n, page_count) for n in numbers):
        flags.add(None, ReviewReason.UNPLACED_ITEM)
        return None
    return numbers


def _place_text(
    item: Any, builders: dict[int, _PageBuilder], flags: _Flags, page_count: int
) -> None:
    text: str = item.text
    numbers = _prov_pages(item, page_count, flags)
    if numbers is None:
        return
    if not text:
        if getattr(item, "orig", ""):
            # Docling kept content only in `orig` (e.g. a formula without enrichment): not mapped.
            flags.add(numbers[0], ReviewReason.UNMAPPED_ITEM)
        return
    if len(set(numbers)) == 1:
        builders[numbers[0]].segments.append(text)
        return
    for number in set(numbers):
        flags.add(number, ReviewReason.MULTI_PAGE_ITEM)
    segments = _verified_segments(text, item.prov)
    if segments is None:
        for number in set(numbers):
            flags.add(number, ReviewReason.MULTI_PAGE_ITEM_UNPLACED)
        return
    for number, segment in segments:
        if segment:
            builders[number].segments.append(segment)


def _verified_segments(text: str, provs: list[Any]) -> list[tuple[int, str]] | None:
    """Split ``text`` by Docling's per-page character spans, only if they verify exactly.

    Docling joins merged pieces with one space and records each piece's (start, end) span. The
    spans must start at 0, be contiguous with exactly one space between them, and end at
    ``len(text)``. When Docling removed a hyphen while joining, its spans are shifted and fail this
    check, so the text is not split (and not placed).
    """
    spans: list[tuple[int, int, int]] = []
    for prov in provs:
        charspan = getattr(prov, "charspan", None)
        if not isinstance(charspan, tuple | list) or len(charspan) != 2:
            return None
        start, end = charspan
        if not (isinstance(start, int) and isinstance(end, int)) or end < start:
            return None
        spans.append((start, end, prov.page_no))
    spans.sort()
    expected_start = 0
    for index, (start, end, _number) in enumerate(spans):
        if start != expected_start or end > len(text):
            return None
        is_last = index == len(spans) - 1
        if not is_last and (end >= len(text) or text[end] != " "):
            return None
        expected_start = end + 1
    if spans[-1][1] != len(text):
        return None
    return [(number, text[start:end]) for start, end, number in spans]


def _place_table(
    item: Any, builders: dict[int, _PageBuilder], flags: _Flags, page_count: int
) -> None:
    numbers = _prov_pages(item, page_count, flags)
    if numbers is None:
        return
    if len(set(numbers)) > 1:
        # Cells carry no page number, so neither structure nor text can be attributed per page.
        for number in set(numbers):
            flags.add(number, ReviewReason.MULTI_PAGE_ITEM)
            flags.add(number, ReviewReason.MULTI_PAGE_ITEM_UNPLACED)
        return
    page = builders[numbers[0]]
    data = item.data
    rendered = _render_table_text(data.table_cells)
    if rendered:
        page.segments.append(rendered)
    table = _structured_table(data, table_index=len(page.tables))
    if table is None:
        flags.add(numbers[0], ReviewReason.TABLE_STRUCTURE_INVALID)
    else:
        page.tables.append(table)


def _render_table_text(cells: list[Any]) -> str:
    """Rows in order (one line each), cells by column joined with a tab; texts verbatim."""
    rows: dict[int, list[tuple[int, str]]] = {}
    for cell in cells:
        rows.setdefault(cell.start_row_offset_idx, []).append(
            (cell.start_col_offset_idx, cell.text)
        )
    return "\n".join(
        "\t".join(text for _col, text in sorted(rows[row], key=lambda c: c[0]))
        for row in sorted(rows)
    )


def _structured_table(data: Any, *, table_index: int) -> ParsedTable | None:
    """The table's grid, or None when Docling's structure is inconsistent (flagged by caller)."""
    cells: list[TableCell] = []
    valid = True
    try:
        for cell in data.table_cells:
            row_span = cell.end_row_offset_idx - cell.start_row_offset_idx
            col_span = cell.end_col_offset_idx - cell.start_col_offset_idx
            if row_span != cell.row_span or col_span != cell.col_span:
                valid = False
                break
            cells.append(
                TableCell(
                    row=cell.start_row_offset_idx,
                    col=cell.start_col_offset_idx,
                    row_span=row_span,
                    col_span=col_span,
                    text=cell.text,
                    column_header=bool(cell.column_header),
                    row_header=bool(cell.row_header),
                )
            )
        if valid:
            return ParsedTable(
                table_index=table_index,
                num_rows=data.num_rows,
                num_cols=data.num_cols,
                cells=cells,
            )
    except ValidationError:
        valid = False
    return None


def _build_page(
    number: int,
    builder: _PageBuilder,
    signal: PageSignals,
    ocr_engine: OcrEngine,
    flags: _Flags,
) -> ParsedPage:
    # OCR provenance rests on one signal: Docling's line cells marked ``from_ocr``. Only an OCR
    # engine creates them (one per line it recognized), and they survive Docling's merge with the
    # PDF cells only where OCR text is used; Docling's own per-page ``ocr_score`` is computed from
    # the same cells. "No visible native text" is *not* evidence of OCR: a blank page or a page of
    # only invisible text has no OCR cells and stays native. Docling does not report OCR that ran
    # but recognized nothing; such a page contributes no OCR text and is native (flagged
    # ``empty_page`` when it has no text at all).
    ocr_cells = [cell for cell in signal.cells if cell.from_ocr and cell.text.strip()]
    use_ocr = bool(ocr_cells)
    provenance: OcrProvenance | None = None
    if use_ocr:
        # OCR confidence is not accuracy (Task 2.4 scan: 0.979 mean, 25/40 cells exact), so every
        # OCR page needs verification against the original, whatever its confidence.
        flags.add(number, ReviewReason.OCR_TEXT_UNVERIFIED)
        valid = [c.confidence for c in ocr_cells if _is_valid_confidence(c.confidence)]
        if len(valid) < len(ocr_cells):
            flags.add(number, ReviewReason.OCR_CONFIDENCE_UNAVAILABLE)
        mean = math.fsum(valid) / len(valid) if valid else None
        minimum = min(valid) if valid else None
        if minimum is not None and minimum < LOW_OCR_CONFIDENCE_THRESHOLD:
            flags.add(number, ReviewReason.LOW_OCR_CONFIDENCE)
        if ocr_cells and signal.native_char_count > 0:
            flags.add(number, ReviewReason.MIXED_NATIVE_AND_OCR)
        provenance = OcrProvenance(
            engine=ocr_engine.engine,
            engine_version=ocr_engine.engine_version,
            backend=ocr_engine.backend,
            mean_confidence=mean,
            min_confidence=minimum,
        )
    text = "\n".join(builder.segments)
    if _may_have_been_normalized(signal.cells) or _watched_characters_lost(signal, text):
        flags.add(number, ReviewReason.DOCLING_NORMALIZED_TEXT)
    if signal.invisible_char_count > 0:
        # Set from PDFium alone, whatever Docling did with that text (kept it, or OCR'd an image
        # under it). The text is never removed or changed.
        flags.add(number, ReviewReason.INVISIBLE_TEXT_LAYER)
    if not text.strip() and not builder.tables:  # same rule as ParsedPage.is_empty
        flags.add(number, ReviewReason.EMPTY_PAGE)
    return ParsedPage(
        page_number=number,
        text=text,
        parse_method=ParseMethod.OCR if use_ocr else ParseMethod.NATIVE,
        native_char_count=signal.native_char_count,
        ocr=provenance,
        tables=builder.tables,
        review_flags=flags.reasons(number),
    )


def _is_valid_confidence(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and 0.0 <= value <= 1.0


def _watched_characters_lost(signal: PageSignals, text: str) -> bool:
    """Docling's text has fewer occurrences of a watched character than the PDF's visible text.

    Detection only, never repair. Counts per character per page catch a lost "–" even when another
    "–" on the same page survived. Limits: occurrences are compared, not positions (a character
    lost in one place and duplicated in another cancels out); text PDFium reports for a page but
    Docling does not place on it (hidden or off-page text, an unplaced multi-page item) counts as
    lost, which errs towards flagging; a page without a visible text layer (a scan, or a
    searchable scan whose text layer is invisible) has no counts, so this never fires for it.
    """
    return any(text.count(character) < count for character, count in signal.watched_counts)


def _may_have_been_normalized(cells: tuple[LineCell, ...]) -> bool:
    """True if Docling's normalization may have changed characters on this page (detection only)."""
    # Invisible cells are ignored: Docling does not use their text for the page.
    lines = [cell.text for cell in cells if cell.visible and cell.text.strip()]
    for index, line in enumerate(lines):
        for character in line:
            code = ord(character)
            if character in _NORMALIZED_CHARACTERS or any(
                low <= code <= high for low, high in _ARABIC_PRESENTATION_FORMS
            ):
                return True
        if index < len(lines) - 1 and _HYPHEN_AT_LINE_END.search(line.rstrip()):
            return True
    return False
