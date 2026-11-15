"""Unit tests for Docling → IR mapping (app/ingestion/docling_mapping.py).

Documents are built by hand with docling-core (no Docling pipeline, no models, no torch). Page
signals are plain objects. All text is fictional.
"""

import math
from types import SimpleNamespace
from typing import Any

import pytest
from docling_core.types.doc import (
    BoundingBox,
    ContentLayer,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)
from docling_core.types.doc.page import PdfCellRenderingMode

from app.core.errors import DocumentParseError
from app.ingestion.docling_mapping import (
    LineCell,
    MappingResult,
    OcrEngine,
    PageSignals,
    ReviewFlag,
    ReviewReason,
    TextLayerStats,
    build_page_signals,
    map_document,
    text_layer_stats,
)
from app.ingestion.ir import ParseMethod, ParserInfo

SHA = "b" * 64
PARSER = ParserInfo(
    name="docling", version="2.150.1", options={"text_source": "docling_extracted_normalized"}
)
ENGINE = OcrEngine(engine="rapidocr", engine_version="3.10.0", backend="torch")
BOX = BoundingBox(l=10, t=20, r=200, b=10)
# Medically relevant strings that must pass through the mapping unchanged.
EXACT_VALUES = (
    "13.5",
    "1,50,000",
    "2.5 x 10³/µL",
    "µIU/mL",
    "5.4 ±0.2",
    "37.0 °C",
    "≤ 5.0",
    "≥ 10",
    "−12",  # U+2212 minus sign
    "4.0–6.0",  # en dash
    "<0.01",
    "Anti-HBs",
    "Hb  13.5 g/dL",  # double space
    "92 7p/6",  # an OCR error stays an OCR error
)


def _prov(page: int, start: int = 0, end: int = 0) -> ProvenanceItem:
    return ProvenanceItem(page_no=page, bbox=BOX, charspan=(start, end))


def _doc(pages: int) -> DoclingDocument:
    document = DoclingDocument(name="document")
    for number in range(1, pages + 1):
        document.add_page(page_no=number, size=Size(width=595, height=842))
    return document


def _text(document: DoclingDocument, page: int, text: str, **kwargs: Any) -> Any:
    return document.add_text(
        label=kwargs.pop("label", DocItemLabel.TEXT),
        text=text,
        prov=_prov(page, 0, len(text)),
        **kwargs,
    )


def _cell(row: int, col: int, text: str, rows: int = 1, cols: int = 1, **kw: Any) -> TableCell:
    return TableCell(
        text=text,
        start_row_offset_idx=row,
        end_row_offset_idx=row + rows,
        start_col_offset_idx=col,
        end_col_offset_idx=col + cols,
        row_span=kw.pop("row_span", rows),
        col_span=kw.pop("col_span", cols),
        **kw,
    )


def _table(document: DoclingDocument, page: int, cells: list[TableCell], rows: int, cols: int):
    data = TableData(num_rows=rows, num_cols=cols, table_cells=cells)
    return document.add_table(data=data, prov=_prov(page))


def _signals(
    pages: int, native: int = 100, cells: dict[int, tuple[LineCell, ...]] | None = None
) -> dict[int, PageSignals]:
    cells = cells or {}
    return {n: PageSignals(n, native, cells.get(n, ())) for n in range(1, pages + 1)}


def _map(document: DoclingDocument, signals: dict[int, PageSignals]) -> MappingResult:
    return map_document(
        document,
        signals,
        page_count=len(signals),
        sha256=SHA,
        parser=PARSER,
        ocr_engine=ENGINE,
    )


def _reasons(result: MappingResult, page: int | None) -> set[ReviewReason]:
    return {f.reason for f in result.review_flags if f.page_number == page}


# --- text placement -------------------------------------------------------------------------------


def test_text_lands_on_its_page_in_reading_order() -> None:
    document = _doc(2)
    _text(document, 1, "First line on page one")
    _text(document, 2, "Only line on page two")
    _text(document, 1, "Second line on page one")
    result = _map(document, _signals(2))
    pages = result.document.pages
    assert pages[0].text == "First line on page one\nSecond line on page one"
    assert pages[1].text == "Only line on page two"
    assert result.review_flags == ()


def test_medical_values_pass_through_unchanged() -> None:
    document = _doc(1)
    for value in EXACT_VALUES:
        _text(document, 1, value)
    result = _map(document, _signals(1))
    assert result.document.pages[0].text.split("\n") == list(EXACT_VALUES)


def test_page_headers_and_footers_are_included() -> None:
    document = _doc(1)
    _text(
        document,
        1,
        "Synthetic Lab - header",
        label=DocItemLabel.PAGE_HEADER,
        content_layer=ContentLayer.FURNITURE,
    )
    _text(document, 1, "Body text")
    result = _map(document, _signals(1))
    assert result.document.pages[0].text == "Synthetic Lab - header\nBody text"


# --- tables ---------------------------------------------------------------------------------------


def test_table_structure_and_cells_are_kept_exactly() -> None:
    document = _doc(1)
    cells = [
        _cell(0, 0, "Test", column_header=True),
        _cell(0, 1, "Result", column_header=True),
        _cell(0, 2, "Unit", column_header=True),
        _cell(1, 0, "TSH", row_header=True),
        _cell(1, 1, "2.5"),
        _cell(1, 2, "µIU/mL"),
        _cell(2, 0, "Complete blood count", cols=3),
    ]
    _table(document, 1, cells, rows=3, cols=3)
    page = _map(document, _signals(1)).document.pages[0]
    assert page.has_tables
    table = page.tables[0]
    assert (table.table_index, table.num_rows, table.num_cols) == (0, 3, 3)
    by_position = {(c.row, c.col): c for c in table.cells}
    assert by_position[(1, 2)].text == "µIU/mL"
    assert by_position[(0, 0)].column_header and by_position[(1, 0)].row_header
    assert (by_position[(2, 0)].row_span, by_position[(2, 0)].col_span) == (1, 3)
    assert page.text == "Test\tResult\tUnit\nTSH\t2.5\tµIU/mL\nComplete blood count"


def test_several_tables_on_a_page_are_indexed_in_order() -> None:
    document = _doc(1)
    _table(document, 1, [_cell(0, 0, "A")], rows=1, cols=1)
    _text(document, 1, "between")
    _table(document, 1, [_cell(0, 0, "B")], rows=1, cols=1)
    page = _map(document, _signals(1)).document.pages[0]
    assert [t.table_index for t in page.tables] == [0, 1]
    assert [t.cells[0].text for t in page.tables] == ["A", "B"]


@pytest.mark.parametrize(
    "cells",
    [
        [_cell(0, 0, "a", cols=2), _cell(0, 1, "b")],  # overlapping cells
        [_cell(0, 0, "a", row_span=2)],  # declared span disagrees with the offsets
        [_cell(0, 0, "a", cols=3)],  # wider than the table
    ],
)
def test_invalid_table_structure_is_flagged_and_its_text_kept(cells: list[TableCell]) -> None:
    document = _doc(1)
    _table(document, 1, cells, rows=1, cols=2)
    result = _map(document, _signals(1))
    page = result.document.pages[0]
    assert not page.has_tables
    assert "a" in page.text
    assert ReviewReason.TABLE_STRUCTURE_INVALID in _reasons(result, 1)


# --- multi-page and unplaced items ----------------------------------------------------------------


def test_multi_page_item_with_verified_charspans_is_split_exactly() -> None:
    document = _doc(2)
    first, second = "Reference ranges continue", "on the next page."
    text = f"{first} {second}"
    item = document.add_text(label=DocItemLabel.TEXT, text=text, prov=_prov(1, 0, len(first)))
    item.prov.append(_prov(2, len(first) + 1, len(text)))
    result = _map(document, _signals(2))
    assert result.document.pages[0].text == first
    assert result.document.pages[1].text == second
    for page in (1, 2):
        assert _reasons(result, page) == {ReviewReason.MULTI_PAGE_ITEM}


def test_multi_page_item_with_shifted_charspans_is_not_placed() -> None:
    # Docling's de-hyphenation case: "Anti-" + "HBs" became "AntiHBs" but the second span was
    # computed before the hyphen was removed, so the spans no longer match the text.
    document = _doc(2)
    text = "Result for AntiHBs antibody"
    item = document.add_text(label=DocItemLabel.TEXT, text=text, prov=_prov(1, 0, 16))
    item.prov.append(_prov(2, 17, 30))
    _text(document, 2, "Other text on page two")
    result = _map(document, _signals(2))
    assert result.document.pages[0].text == ""
    assert result.document.pages[1].text == "Other text on page two"
    for page in (1, 2):
        assert {
            ReviewReason.MULTI_PAGE_ITEM,
            ReviewReason.MULTI_PAGE_ITEM_UNPLACED,
        } <= _reasons(result, page)
    assert text in document.export_to_dict()["texts"][0]["text"]  # still in Docling's JSON


def test_multi_page_table_is_not_placed() -> None:
    document = _doc(2)
    table = _table(document, 1, [_cell(0, 0, "13.5")], rows=1, cols=1)
    table.prov.append(_prov(2))
    result = _map(document, _signals(2))
    assert all(not p.has_tables and p.text == "" for p in result.document.pages)
    for page in (1, 2):
        assert ReviewReason.MULTI_PAGE_ITEM_UNPLACED in _reasons(result, page)


@pytest.mark.parametrize("page_no", [0, 3])
def test_item_on_an_unknown_page_is_not_placed(page_no: int) -> None:
    document = _doc(2)
    document.add_text(label=DocItemLabel.TEXT, text="stray", prov=_prov(page_no, 0, 5))
    result = _map(document, _signals(2))
    assert all("stray" not in p.text for p in result.document.pages)
    assert ReviewReason.UNPLACED_ITEM in _reasons(result, None)


def test_item_without_provenance_is_not_placed() -> None:
    document = _doc(1)
    document.add_text(label=DocItemLabel.TEXT, text="no provenance")
    result = _map(document, _signals(1))
    assert result.document.pages[0].text == ""
    assert ReviewReason.UNPLACED_ITEM in _reasons(result, None)


def test_page_without_items_is_empty_and_flagged() -> None:
    document = _doc(2)
    _text(document, 1, "content")
    result = _map(document, _signals(2))
    assert result.document.pages[1].text == "" and result.document.pages[1].is_empty
    assert ReviewReason.EMPTY_PAGE in _reasons(result, 2)


def test_document_pages_must_match_the_pdf() -> None:
    document = _doc(2)
    with pytest.raises(DocumentParseError) as caught:
        _map(document, _signals(3))
    assert caught.value.kind == "unexpected_output"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


# --- normalization detection ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_lines",
    [
        ("Patient’s report",),  # typographic apostrophe
        ("ﬁnal result",),  # ligature
        ("• Haemoglobin",),  # bullet
        ("range 4.0-", "6.0 mmol/L"),  # hyphen at a line end, followed by another line
        ("Anti-", "HBs"),
    ],
)
def test_possible_docling_normalization_is_flagged(raw_lines: tuple[str, ...]) -> None:
    document = _doc(1)
    _text(document, 1, "as produced by Docling")
    cells = {1: tuple(LineCell(text=line, from_ocr=False, confidence=None) for line in raw_lines)}
    result = _map(document, _signals(1, cells=cells))
    assert ReviewReason.DOCLING_NORMALIZED_TEXT in _reasons(result, 1)
    assert result.document.pages[0].text == "as produced by Docling"  # never repaired


@pytest.mark.parametrize(
    "raw_lines", [("Haemoglobin 13.5 g/dL", "Range 13.0–17.0"), ("ends with hyphen -",), ("4.0-",)]
)
def test_clean_pages_are_not_flagged(raw_lines: tuple[str, ...]) -> None:
    document = _doc(1)
    _text(document, 1, "text")
    cells = {1: tuple(LineCell(text=line, from_ocr=False, confidence=None) for line in raw_lines)}
    result = _map(document, _signals(1, cells=cells))
    assert ReviewReason.DOCLING_NORMALIZED_TEXT not in _reasons(result, 1)


def test_parser_info_records_the_normalization() -> None:
    result = _map(_doc(1), _signals(1))
    assert result.document.parser.options["text_source"] == "docling_extracted_normalized"


# --- OCR provenance -------------------------------------------------------------------------------


def _ocr(text: str, confidence: float | None) -> LineCell:
    return LineCell(text=text, from_ocr=True, confidence=confidence)


def test_native_ocr_and_mixed_pages() -> None:
    document = _doc(3)
    for page in (1, 2, 3):
        _text(document, page, f"text {page}")
    signals = {
        1: PageSignals(1, 120, (LineCell("native", False, None),)),
        2: PageSignals(2, 0, (_ocr("13.5", 0.97), _ocr("g/dL", 0.91))),
        3: PageSignals(3, 80, (LineCell("native", False, None), _ocr("stamp", 0.95))),
    }
    result = _map(document, signals)
    page1, page2, page3 = result.document.pages
    assert (page1.parse_method, page1.ocr) == (ParseMethod.NATIVE, None)
    assert page2.parse_method == ParseMethod.OCR and page2.ocr is not None
    assert (page2.ocr.engine, page2.ocr.engine_version, page2.ocr.backend) == (
        "rapidocr",
        "3.10.0",
        "torch",
    )
    assert page2.ocr.mean_confidence == pytest.approx(0.94)
    assert page2.ocr.min_confidence == pytest.approx(0.91)
    assert page3.parse_method == ParseMethod.OCR
    assert ReviewReason.MIXED_NATIVE_AND_OCR in _reasons(result, 3)
    assert ReviewReason.MIXED_NATIVE_AND_OCR not in _reasons(result, 2)


def test_page_without_text_layer_and_without_ocr_text_claims_no_ocr() -> None:
    # No visible native text is not evidence that OCR ran: with no OCR cells there is no OCR
    # provenance, no confidence and no OCR flag. The empty page is still flagged for review.
    result = _map(_doc(1), _signals(1, native=0))
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.NATIVE and page.ocr is None
    assert _reasons(result, 1) == {ReviewReason.EMPTY_PAGE}


def test_ocr_provenance_requires_ocr_cells_with_text() -> None:
    document = _doc(4)
    for page in (1, 2, 3, 4):
        _text(document, page, f"text {page}")
    signals = {
        1: PageSignals(1, 0, ()),  # no cells at all
        2: PageSignals(2, 0, (LineCell("native", False, None),)),  # PDF cell only
        3: PageSignals(3, 0, (_ocr(" \n", 0.9),)),  # blank OCR output
        4: PageSignals(4, 0, (_ocr("13.5", 0.9),)),  # OCR contributed text
    }
    pages = _map(document, signals).document.pages
    assert [p.parse_method for p in pages] == [ParseMethod.NATIVE] * 3 + [ParseMethod.OCR]
    assert [ReviewReason.OCR_TEXT_UNVERIFIED in p.review_flags for p in pages] == [
        False,
        False,
        False,
        True,
    ]


@pytest.mark.parametrize("bad", [None, math.nan, math.inf, -0.1, 1.5])
def test_missing_or_invalid_confidence_is_never_invented(bad: float | None) -> None:
    document = _doc(1)
    _text(document, 1, "13.5")
    result = _map(document, _signals(1, native=0, cells={1: (_ocr("13.5", bad),)}))
    ocr = result.document.pages[0].ocr
    assert ocr is not None and (ocr.mean_confidence, ocr.min_confidence) == (None, None)
    assert ReviewReason.OCR_CONFIDENCE_UNAVAILABLE in _reasons(result, 1)


def test_partial_confidence_uses_valid_values_only_and_is_flagged() -> None:
    document = _doc(1)
    _text(document, 1, "x")
    cells = {1: (_ocr("13.5", 0.9), _ocr("g/dL", None), _ocr("92", 0.7))}
    result = _map(document, _signals(1, native=0, cells=cells))
    ocr = result.document.pages[0].ocr
    assert ocr is not None
    assert ocr.mean_confidence == pytest.approx(0.8) and ocr.min_confidence == pytest.approx(0.7)
    assert {
        ReviewReason.OCR_CONFIDENCE_UNAVAILABLE,
        ReviewReason.LOW_OCR_CONFIDENCE,
    } <= _reasons(result, 1)


def test_blank_ocr_cells_do_not_count_as_ocr_text() -> None:
    document = _doc(1)
    _text(document, 1, "native")
    result = _map(document, _signals(1, native=50, cells={1: (_ocr("   ", 0.99),)}))
    assert result.document.pages[0].parse_method == ParseMethod.NATIVE


# --- page matching (build_page_signals) -----------------------------------------------------------


def _page(number: object, cells: list[Any] | None = None, parsed: bool = True) -> Any:
    textline_cells = cells if cells is not None else []
    parsed_page = SimpleNamespace(textline_cells=textline_cells) if parsed else None
    return SimpleNamespace(page_no=number, parsed_page=parsed_page)


def _raw(
    text: str,
    from_ocr: bool,
    confidence: Any = None,
    orig: str | None = None,
    rendering_mode: Any = None,
) -> Any:
    cell = SimpleNamespace(text=text, orig=orig, from_ocr=from_ocr, confidence=confidence)
    if rendering_mode is not None:  # only PDF cells have one
        cell.rendering_mode = rendering_mode
    return cell


def _layer(visible: str, invisible: str = "") -> list[tuple[str, bool]]:
    """A text layer as (character, visible) pairs, as PDFium's pass yields them."""
    return [(c, True) for c in visible] + [(c, False) for c in invisible]


def _build_signals(
    pages: list[Any],
    layers: dict[int, str],
    page_count: int,
    invisible: dict[int, str] | None = None,
) -> dict[int, PageSignals]:
    """build_page_signals with each page's visible (and invisible) text layer given as strings."""
    invisible = invisible or {}
    stats = {n: text_layer_stats(_layer(text, invisible.get(n, ""))) for n, text in layers.items()}
    return build_page_signals(pages, stats, page_count=page_count)


def test_signals_are_matched_by_page_number_not_position() -> None:
    pages = [
        _page(3, [_raw("third", False)]),
        _page(1, [_raw("first", False)]),
        _page(2, [_raw("13.5", True, 0.9)]),
    ]
    signals = _build_signals(pages, {1: "a" * 10, 2: " \n ", 3: "bc " * 15}, page_count=3)
    assert [signals[n].cells[0].text for n in (1, 2, 3)] == ["first", "13.5", "third"]
    assert [signals[n].native_char_count for n in (1, 2, 3)] == [10, 0, 30]
    assert signals[2].cells[0].from_ocr and signals[2].cells[0].confidence == 0.9


def test_raw_orig_text_is_preferred_for_detection() -> None:
    pages = [_page(1, [_raw("normalized", False, orig="ﬁnal")])]
    assert _build_signals(pages, {1: "final"}, page_count=1)[1].cells[0].text == "ﬁnal"


@pytest.mark.parametrize(
    ("pages", "counts"),
    [
        ([_page(1), _page(3)], {1: "x", 2: "x"}),  # page 2 missing, unexpected 3
        ([_page(1)], {1: "x", 2: "x"}),  # page 2 missing
        ([_page(1), _page(1)], {1: "x"}),  # duplicate
        ([_page(0)], {1: "x"}),  # out of range
        ([_page(True)], {1: "x"}),  # not an int page number
        ([_page(1, parsed=False)], {1: "x"}),  # no parsed cells
        ([_page(1)], {}),  # no text layer for page 1
    ],
)
def test_page_mismatches_are_refused(pages: list[Any], counts: dict[int, str]) -> None:
    with pytest.raises(DocumentParseError) as caught:
        _build_signals(pages, counts, page_count=max(counts, default=1))
    assert caught.value.kind == "unexpected_output"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_non_numeric_confidence_becomes_none() -> None:
    pages = [_page(1, [_raw("x", True, "0.9"), _raw("y", True, True)])]
    cells = _build_signals(pages, {1: ""}, page_count=1)[1].cells
    assert [c.confidence for c in cells] == [None, None]


# --- safety ---------------------------------------------------------------------------------------


def test_errors_and_reprs_contain_no_document_text() -> None:
    document = _doc(1)
    _text(document, 1, "Test Person 13.5 g/dL")
    with pytest.raises(DocumentParseError) as caught:
        _map(document, _signals(2))
    assert "Test Person" not in str(caught.value)
    result = _map(document, _signals(1, cells={1: (LineCell("Test Person", False, None),)}))
    assert "Test Person" not in repr(result)
    assert "Test Person" not in repr(ReviewFlag(1, ReviewReason.EMPTY_PAGE))


# --- watched characters: PDF text layer vs Docling's text (Decision 1) ----------------------------


def _watched(layer: str) -> tuple[tuple[str, int], ...]:
    return _build_signals([_page(1)], {1: layer}, page_count=1)[1].watched_counts


def test_text_layer_is_reduced_to_a_count_and_watched_characters() -> None:
    signal = _build_signals([_page(1)], {1: "Range 13.0–17.0, BE −1.2 “x”"}, page_count=1)[1]
    assert signal.native_char_count == len("Range13.0–17.0,BE−1.2“x”")
    assert dict(signal.watched_counts) == {"–": 1, "−": 1, "“": 1, "”": 1}
    assert "Range" not in repr(signal)  # the text layer itself is not kept
    assert _watched("plain ASCII 13.0-17.0") == ()


@pytest.mark.parametrize(
    ("layer", "docling_text"),
    [
        ("13.0–17.0", "13.0-17.0"),  # docling-parse: en dash -> hyphen
        ("Base excess −1.2", "Base excess -1.2"),  # minus sign -> hyphen
        ("Patient’s report", "Patient's report"),
        ("“Fasting”", '"Fasting"'),
        ("• Haemoglobin", "· Haemoglobin"),
        ("ﬁnal", "final"),
        ("1⁄2 tablet", "1/2 tablet"),
    ],
)
def test_watched_character_lost_by_docling_is_flagged(layer: str, docling_text: str) -> None:
    document = _doc(1)
    _text(document, 1, docling_text)
    signals = {1: _build_signals([_page(1)], {1: layer}, page_count=1)[1]}
    result = _map(document, signals)
    assert ReviewReason.DOCLING_NORMALIZED_TEXT in _reasons(result, 1)
    assert result.document.pages[0].text == docling_text  # flagged, never repaired


def test_watched_character_kept_by_docling_is_not_flagged() -> None:
    document = _doc(1)
    _text(document, 1, "Range 13.0–17.0 and −1.2")
    layers = {1: "Range 13.0–17.0 and −1.2"}
    signals = {1: _build_signals([_page(1)], layers, page_count=1)[1]}
    assert ReviewReason.DOCLING_NORMALIZED_TEXT not in _reasons(_map(document, signals), 1)


def test_page_without_text_layer_is_never_flagged_by_the_text_layer_check() -> None:
    # A scanned page: no PDF text layer, OCR text with hyphens. Nothing to compare against.
    document = _doc(1)
    _text(document, 1, "13.0-17.0 -1.2")
    page = _page(1, [_raw("13.0-17.0 -1.2", True, 0.95)])
    signals = _build_signals([page], {1: ""}, page_count=1)
    result = _map(document, signals)
    assert result.document.pages[0].parse_method == ParseMethod.OCR
    assert ReviewReason.DOCLING_NORMALIZED_TEXT not in _reasons(result, 1)


def test_mixed_page_with_a_lost_character_in_its_text_layer_is_flagged() -> None:
    document = _doc(1)
    _text(document, 1, "Range 4.0-11.0 stamp")
    page = _page(1, [_raw("Range 4.0-11.0", False), _raw("stamp", True, 0.9)])
    signals = _build_signals([page], {1: "Range 4.0–11.0"}, page_count=1)
    result = _map(document, signals)
    assert {
        ReviewReason.DOCLING_NORMALIZED_TEXT,
        ReviewReason.MIXED_NATIVE_AND_OCR,
    } <= _reasons(result, 1)


def test_lost_detection_is_per_page() -> None:
    document = _doc(2)
    _text(document, 1, "13.0-17.0")
    _text(document, 2, "13.0–17.0")
    signals = _build_signals([_page(1), _page(2)], {1: "13.0–17.0", 2: "13.0–17.0"}, page_count=2)
    result = _map(document, signals)
    assert ReviewReason.DOCLING_NORMALIZED_TEXT in _reasons(result, 1)
    assert ReviewReason.DOCLING_NORMALIZED_TEXT not in _reasons(result, 2)


# --- ocr_text_unverified on every OCR page (change A) --------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "low"), [(0.99, False), (0.81, False), (0.79, True), (0.30, True)]
)
def test_every_ocr_page_is_unverified_whatever_its_confidence(confidence: float, low: bool) -> None:
    document = _doc(1)
    _text(document, 1, "13.5")
    result = _map(document, _signals(1, native=0, cells={1: (_ocr("13.5", confidence),)}))
    reasons = _reasons(result, 1)
    assert ReviewReason.OCR_TEXT_UNVERIFIED in reasons
    assert (ReviewReason.LOW_OCR_CONFIDENCE in reasons) is low  # separate diagnostic, 0.80
    assert result.document.pages[0].text == "13.5"  # never corrected
    assert ReviewReason.OCR_TEXT_UNVERIFIED in result.document.pages[0].review_flags  # in the IR


def test_native_only_pages_are_not_flagged_unverified() -> None:
    document = _doc(2)
    _text(document, 1, "native")
    _text(document, 2, "scanned")
    signals = {
        1: PageSignals(1, 50, (LineCell("native", False, None),)),
        2: PageSignals(2, 0, (_ocr("scanned", 0.97),)),
    }
    result = _map(document, signals)
    assert ReviewReason.OCR_TEXT_UNVERIFIED not in _reasons(result, 1)
    assert ReviewReason.OCR_TEXT_UNVERIFIED in _reasons(result, 2)


def test_review_flags_are_read_from_the_ir() -> None:
    document = _doc(2)
    document.add_text(label=DocItemLabel.TEXT, text="stray", prov=_prov(9, 0, 5))
    result = _map(document, _signals(2, native=0))
    assert result.document.review_flags == (ReviewReason.UNPLACED_ITEM,)
    assert set(result.review_flags) == {
        ReviewFlag(None, ReviewReason.UNPLACED_ITEM),
        *(ReviewFlag(n, r) for n in (1, 2) for r in result.document.pages[n - 1].review_flags),
    }


# --- occurrence counts (change E) ----------------------------------------------------------------


def test_text_layer_keeps_only_watched_occurrence_counts() -> None:
    assert dict(_watched("13.0–17.0, 70–100 and 4.0–6.0; −1.2")) == {"–": 3, "−": 1}


def test_one_lost_occurrence_is_flagged_even_when_another_survives() -> None:
    document = _doc(1)
    _text(document, 1, "Hb 13.0–17.0, glucose 70-100")  # second en dash became "-"
    layer = "Hb 13.0–17.0, glucose 70–100"
    signals = {1: _build_signals([_page(1)], {1: layer}, page_count=1)[1]}
    result = _map(document, signals)
    assert ReviewReason.DOCLING_NORMALIZED_TEXT in _reasons(result, 1)
    assert result.document.pages[0].text == "Hb 13.0–17.0, glucose 70-100"  # not repaired


def test_equal_or_higher_counts_are_not_flagged() -> None:
    for docling_text in ("A–B C–D", "A–B C–D E–F"):
        document = _doc(1)
        _text(document, 1, docling_text)
        signals = {1: _build_signals([_page(1)], {1: "A–B C–D"}, page_count=1)[1]}
        assert ReviewReason.DOCLING_NORMALIZED_TEXT not in _reasons(_map(document, signals), 1)


def test_counts_are_compared_per_watched_character() -> None:
    document = _doc(1)
    _text(document, 1, "13.0–17.0 and -1.2 and 4.0–6.0")  # dashes kept, minus sign lost
    layer = "13.0–17.0 and −1.2 and 4.0–6.0"
    signals = {1: _build_signals([_page(1)], {1: layer}, page_count=1)[1]}
    assert ReviewReason.DOCLING_NORMALIZED_TEXT in _reasons(_map(document, signals), 1)


# --- audit hardening: sanitization boundary (change 2) -------------------------------------------


def test_page_and_provenance_errors_become_sanitized_unexpected_output() -> None:
    document = _doc(1)
    _text(document, 1, "Test Person 13.5 g/dL")
    broken_engine = OcrEngine(engine="", engine_version="3.10.0", backend="torch")  # invalid
    signals = _signals(1, native=0, cells={1: (_ocr("Test Person 13.5", 0.9),)})
    with pytest.raises(DocumentParseError) as caught:
        map_document(
            document,
            signals,
            page_count=1,
            sha256=SHA,
            parser=PARSER,
            ocr_engine=broken_engine,
        )
    error = caught.value
    assert error.kind == "unexpected_output"
    assert "Test Person" not in str(error) and "input_value" not in str(error)
    assert error.__cause__ is None and error.__context__ is None


def test_mapping_produces_canonical_flags() -> None:
    document = _doc(1)
    _text(document, 1, "13.5 ’")
    # Visible native text, an OCR line below the threshold, invisible text and a normalized quote.
    cells = [_raw("Glucose ’", False, rendering_mode=0), _raw("13.5", True, 0.5)]
    signals = _build_signals([_page(1, cells)], {1: "Glucose"}, 1, invisible={1: "hidden"})
    flags = _map(document, signals).document.pages[0].review_flags
    assert [r.value for r in flags] == [
        "docling_normalized_text",
        "invisible_text_layer",
        "low_ocr_confidence",
        "mixed_native_and_ocr",
        "ocr_text_unverified",
    ]
    assert flags == tuple(sorted(set(flags), key=lambda reason: reason.value))


# --- visibility-aware text signals ----------------------------------------------------------------


def test_text_layer_stats_keep_counts_only() -> None:
    stats = text_layer_stats(_layer("Range 13.0–17.0, BE −1.2 and 4.0–6.0\n"))
    assert stats == TextLayerStats(
        visible_char_count=len("Range13.0–17.0,BE−1.2and4.0–6.0"),
        watched_counts=(("–", 2), ("−", 1)),
        invisible_char_count=0,
    )
    assert "Range" not in repr(stats)
    assert text_layer_stats(iter([])) == TextLayerStats(0, (), 0)  # consumes any iterable


def test_text_layer_stats_count_invisible_characters_separately() -> None:
    # Invisible characters (watched ones included) never reach the visible count or watched
    # counts; whitespace is never counted.
    stats = text_layer_stats(_layer("Hb 13.5", invisible=" 13.0–17.0 −1.2 \n"))
    assert stats == TextLayerStats(
        visible_char_count=len("Hb13.5"),
        watched_counts=(),
        invisible_char_count=len("13.0–17.0−1.2"),
    )
    assert text_layer_stats(_layer("", invisible=" \n\t ")).invisible_char_count == 0


@pytest.mark.parametrize(
    ("mode", "visible"),
    [
        (3, False),  # invisible
        (7, False),  # clip only
        (PdfCellRenderingMode.INVISIBLE, False),
        (PdfCellRenderingMode.ONLY_CLIPPING, False),
        (PdfCellRenderingMode.UNKNOWN, True),  # docling-parse reports ordinary filled text so
        (0, True),
        (4, True),  # fill + clip: ink is painted
        (5, True),
        (None, True),  # OCR cells have no rendering mode
        (True, True),  # not a mode
        ("3", True),  # not a mode
    ],
)
def test_cell_visibility_follows_the_rendering_mode(mode: Any, visible: bool) -> None:
    pages = [_page(1, [_raw("x", False, rendering_mode=mode)])]
    assert _build_signals(pages, {1: ""}, page_count=1)[1].cells[0].visible is visible


@pytest.mark.parametrize("line", ["Patient’s report", "ﬁnal", "Anti-"])
@pytest.mark.parametrize(("mode", "flagged"), [(3, False), (7, False), (0, True), (-1, True)])
def test_invisible_line_cells_are_ignored_by_the_normalization_check(
    line: str, mode: int, flagged: bool
) -> None:
    document = _doc(1)
    _text(document, 1, "Glucose 92 mg/dL")
    cells = [_raw(line, False, rendering_mode=mode), _raw("HBs 92", False, rendering_mode=-1)]
    signals = _build_signals([_page(1, cells)], {1: "Glucose 92 mg/dL"}, page_count=1)
    reasons = _reasons(_map(document, signals), 1)
    assert (ReviewReason.DOCLING_NORMALIZED_TEXT in reasons) is flagged


def test_searchable_scan_signals_are_ocr_without_mixed_or_normalized_flags() -> None:
    # Invisible PDF text (with en dashes) over a scan; Docling OCRs the image. PDFium's visible
    # text layer is empty, so nothing native is counted and nothing can be "lost".
    document = _doc(1)
    _text(document, 1, "Haemoglobin 13.5 g/dL 13.0-17.0")
    cells = [
        _raw("Haemoglobin 13.5 g/dL 13.0-17.0", False, rendering_mode=3),
        _raw("Haemoglobin 13.5 g/dL 13.0-17.0", True, 0.97),
    ]
    layer = "Haemoglobin 13.5 g/dL 13.0–17.0"
    signals = _build_signals([_page(1, cells)], {1: ""}, page_count=1, invisible={1: layer})
    result = _map(document, signals)
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.OCR and page.native_char_count == 0
    assert _reasons(result, 1) == {
        ReviewReason.OCR_TEXT_UNVERIFIED,
        ReviewReason.INVISIBLE_TEXT_LAYER,
    }


def test_visible_native_text_alongside_ocr_is_still_mixed() -> None:
    document = _doc(1)
    _text(document, 1, "Haemoglobin 13.5 stamp")
    cells = [_raw("Haemoglobin 13.5", False, rendering_mode=-1), _raw("stamp", True, 0.9)]
    signals = _build_signals([_page(1, cells)], {1: "Haemoglobin 13.5"}, page_count=1)
    assert ReviewReason.MIXED_NATIVE_AND_OCR in _reasons(_map(document, signals), 1)


@pytest.mark.parametrize("mode", [3, 7])
def test_invisible_only_page_without_ocr_is_native_and_flagged(mode: int) -> None:
    # Docling kept the invisible text (no image, so no OCR): it is passed through unchanged, the
    # page is not an OCR page, and the invisible text layer is flagged.
    document = _doc(1)
    _text(document, 1, "hidden 13.0-17.0")
    cells = [_raw("hidden 13.0-17.0", False, rendering_mode=mode)]
    signals = _build_signals([_page(1, cells)], {1: ""}, 1, invisible={1: "hidden 13.0–17.0"})
    result = _map(document, signals)
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.NATIVE and page.ocr is None
    assert page.native_char_count == 0
    assert page.text == "hidden 13.0-17.0"  # not removed, not corrected
    assert _reasons(result, 1) == {ReviewReason.INVISIBLE_TEXT_LAYER}


def test_invisible_only_page_whose_text_docling_dropped_is_still_flagged() -> None:
    document = _doc(1)
    cells = [_raw("hidden text", False, rendering_mode=3)]
    signals = _build_signals([_page(1, cells)], {1: ""}, 1, invisible={1: "hidden text"})
    result = _map(document, signals)
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.NATIVE and page.text == ""
    assert _reasons(result, 1) == {ReviewReason.INVISIBLE_TEXT_LAYER, ReviewReason.EMPTY_PAGE}


def test_mixed_visible_and_invisible_native_page() -> None:
    document = _doc(1)
    _text(document, 1, "Sodium 140 hidden 135-145 mmol/L")
    cells = [_raw("Sodium 140 hidden 135-145 mmol/L", False, rendering_mode=-1)]
    signals = _build_signals(
        [_page(1, cells)], {1: "Sodium 140  mmol/L"}, 1, invisible={1: "hidden 135–145"}
    )
    result = _map(document, signals)
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.NATIVE
    assert page.native_char_count == len("Sodium140mmol/L")  # invisible text not counted
    # The invisible "–" is not compared with Docling's text, so no docling_normalized_text.
    assert _reasons(result, 1) == {ReviewReason.INVISIBLE_TEXT_LAYER}


def test_no_invisible_text_no_flag() -> None:
    document = _doc(1)
    _text(document, 1, "Glucose 92")
    signals = _build_signals(
        [_page(1, [_raw("Glucose 92", False, rendering_mode=0)])],
        {1: "Glucose 92"},
        1,
        invisible={1: " \n "},
    )
    assert ReviewReason.INVISIBLE_TEXT_LAYER not in _reasons(_map(document, signals), 1)


def test_invisible_text_layer_is_never_a_document_flag() -> None:
    document = _doc(2)
    signals = _build_signals([_page(1), _page(2)], {1: "", 2: ""}, 2, invisible={2: "x"})
    result = _map(document, signals)
    assert result.document.review_flags == ()
    assert ReviewFlag(2, ReviewReason.INVISIBLE_TEXT_LAYER) in result.review_flags
    assert ReviewFlag(1, ReviewReason.INVISIBLE_TEXT_LAYER) not in result.review_flags


# --- replacement_character (D7) -------------------------------------------------------------------

FFFD = "\ufffd"


def test_replacement_character_in_text_is_flagged_and_kept() -> None:
    document = _doc(1)
    _text(document, 1, f"Result: {FFFD}{FFFD} g/dL")
    result = _map(document, _signals(1))
    page = result.document.pages[0]
    assert page.text == f"Result: {FFFD}{FFFD} g/dL"  # never stripped or replaced
    assert _reasons(result, 1) == {ReviewReason.REPLACEMENT_CHARACTER}
    assert result.document.review_flags == ()


def test_replacement_character_in_a_table_cell_is_flagged_and_kept() -> None:
    document = _doc(1)
    cells = [_cell(0, 0, "Haemoglobin"), _cell(0, 1, f"13.5 g/{FFFD}L")]
    _table(document, 1, cells, rows=1, cols=2)
    result = _map(document, _signals(1))
    page = result.document.pages[0]
    assert page.tables[0].cells[1].text == f"13.5 g/{FFFD}L"
    assert ReviewReason.REPLACEMENT_CHARACTER in _reasons(result, 1)


def test_replacement_character_in_an_invalid_table_is_flagged_through_its_text() -> None:
    document = _doc(1)
    _table(document, 1, [_cell(0, 0, f"a{FFFD}", cols=3)], rows=1, cols=2)  # too wide
    result = _map(document, _signals(1))
    page = result.document.pages[0]
    assert not page.has_tables and f"a{FFFD}" in page.text
    assert _reasons(result, 1) == {
        ReviewReason.TABLE_STRUCTURE_INVALID,
        ReviewReason.REPLACEMENT_CHARACTER,
    }


def test_replacement_character_on_a_mixed_ocr_page_is_flagged_with_the_ocr_flags() -> None:
    # Native text with an unmapped glyph next to OCR text: the flag is not an OCR flag.
    document = _doc(1)
    _text(document, 1, f"Result: {FFFD} 13.5")
    signals = {
        1: PageSignals(1, 40, (LineCell(f"Result: {FFFD}", False, None), _ocr("13.5", 0.97)))
    }
    result = _map(document, signals)
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.OCR
    assert page.review_flags == (
        ReviewReason.MIXED_NATIVE_AND_OCR,
        ReviewReason.OCR_TEXT_UNVERIFIED,
        ReviewReason.REPLACEMENT_CHARACTER,
    )


def test_replacement_character_is_flagged_per_page() -> None:
    document = _doc(3)
    _text(document, 1, "Glucose 92 mg/dL")
    _text(document, 2, f"TSH 2.5 {FFFD}IU/mL")
    _text(document, 3, "Sodium 140 mmol/L")
    result = _map(document, _signals(3))
    flagged = [f.page_number for f in result.review_flags]
    assert flagged == [2]
    assert ReviewFlag(2, ReviewReason.REPLACEMENT_CHARACTER) in result.review_flags


@pytest.mark.parametrize(
    "text",
    [
        "Result ? g/dL",
        "Result □ g/dL",  # white square
        "Result \ue000 g/dL",  # private-use character
        "Result U+FFFD g/dL",  # the name written as text
        "• Haemoglobin 13.5",  # a mapped bullet
    ],
)
def test_look_alikes_of_the_replacement_character_are_not_flagged(text: str) -> None:
    document = _doc(1)
    _text(document, 1, text)
    result = _map(document, _signals(1))
    assert result.document.pages[0].text == text
    assert ReviewReason.REPLACEMENT_CHARACTER not in _reasons(result, 1)


def test_replacement_character_only_in_a_dropped_invisible_cell_is_not_flagged() -> None:
    # Detection reads the page's final text, not Docling's cells: text Docling did not use for the
    # page (here an invisible cell) does not set the flag; the invisible layer has its own flag.
    document = _doc(1)
    _text(document, 1, "Glucose 92")
    cells = [
        _raw("Glucose 92", False, rendering_mode=0),
        _raw(f"hidden {FFFD}", False, rendering_mode=3),
    ]
    signals = _build_signals([_page(1, cells)], {1: "Glucose 92"}, 1, invisible={1: "hidden x"})
    result = _map(document, signals)
    assert _reasons(result, 1) == {ReviewReason.INVISIBLE_TEXT_LAYER}


def test_replacement_character_in_an_unplaced_item_sets_no_page_flag() -> None:
    document = _doc(1)
    document.add_text(label=DocItemLabel.TEXT, text=f"stray {FFFD}")  # no provenance
    result = _map(document, _signals(1))
    assert ReviewReason.REPLACEMENT_CHARACTER not in _reasons(result, 1)
    assert result.document.review_flags == (ReviewReason.UNPLACED_ITEM,)


def test_replacement_character_in_an_unplaced_multi_page_table_sets_no_flag() -> None:
    document = _doc(2)
    table = _table(document, 1, [_cell(0, 0, f"13.5 {FFFD}")], rows=1, cols=1)
    table.prov.append(_prov(2))
    result = _map(document, _signals(2))
    for page in (1, 2):
        assert ReviewReason.REPLACEMENT_CHARACTER not in _reasons(result, page)
        assert ReviewReason.MULTI_PAGE_ITEM_UNPLACED in _reasons(result, page)


# --- lone surrogates (Task 2.5d) ------------------------------------------------------------------


@pytest.mark.parametrize("surrogate", [chr(0xD800), chr(0xDFFF)])
def test_docling_text_with_a_lone_surrogate_is_sanitized_unexpected_output(surrogate: str) -> None:
    document = _doc(1)
    _text(document, 1, f"Test Person 13.5 {surrogate}")
    with pytest.raises(DocumentParseError) as caught:
        _map(document, _signals(1))
    assert caught.value.kind == "unexpected_output"
    assert "Test Person" not in str(caught.value) and "input_value" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
