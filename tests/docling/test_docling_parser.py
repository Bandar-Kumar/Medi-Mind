"""Opt-in end-to-end tests of the Docling parser on synthetic PDFs (real models, CPU).

Run with:  HF_HUB_OFFLINE=1 uv run pytest -m docling tests/docling -q
Needs Docling's models in the local caches (downloaded once; see README). Slow: one converter is
shared by the whole module (this also exercises converter reuse), and each PDF is parsed once.

Assertions are about structure and provenance. OCR accuracy on the scan is measured and reported
separately, not asserted: only values the spike read correctly are required.
"""

import hashlib
import json
import logging
import stat
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path

import pypdfium2
import pypdfium2.raw as pdfium_c
import pytest

from app.core.config import Settings
from app.ingestion.docling_mapping import ReviewReason, text_layer_stats
from app.ingestion.ir import OCR_ONLY_REASONS, ParseMethod, dump_ir, load_ir
from app.ingestion.parsing import DoclingParser, ParseResult, ParserOptions
from app.ingestion.storage import store_original
from app.ingestion.validation import ValidatedPdf, validate_pdf, validate_pdf_bytes
from tests.fixtures import pdfs

pytestmark = pytest.mark.docling

OPTIONS = ParserOptions(timeout_seconds=600, max_pages=20, max_bytes=20 * 1024 * 1024)
FIXTURES: dict[str, Callable[[], bytes]] = {
    "native": pdfs.lab_table_pdf,
    "three_pages": lambda: pdfs.lab_table_pdf(3),
    "wrapped": pdfs.wrapped_text_pdf,
    "scanned": pdfs.scanned_pdf,
    "mixed": pdfs.mixed_pdf,
    "blank": pdfs.blank_pdf,
    "searchable_scan": pdfs.searchable_scan_pdf,
    "mixed_visibility": pdfs.mixed_visibility_pdf,
    "invisible_text": pdfs.invisible_text_pdf,
    "clip_only_text": pdfs.clip_only_text_pdf,
    "unmapped_glyph": pdfs.unmapped_glyph_pdf,
    "source_replacement_character": pdfs.source_replacement_character_pdf,
}


@pytest.fixture(scope="module")
def parser() -> DoclingParser:
    instance = DoclingParser(OPTIONS)
    instance.warm_up()
    return instance


@pytest.fixture(scope="module")
def parsed(parser: DoclingParser) -> Callable[[str], ParseResult]:
    cache: dict[str, ParseResult] = {}

    def get(name: str) -> ParseResult:
        if name not in cache:
            cache[name] = parser.parse(_validated(FIXTURES[name]()))
        return cache[name]

    return get


def _validated(data: bytes) -> ValidatedPdf:
    return validate_pdf_bytes(data, max_bytes=OPTIONS.max_bytes, max_pages=OPTIONS.max_pages)


def _reasons(result: ParseResult, page: int) -> set[ReviewReason]:
    return {f.reason for f in result.review_flags if f.page_number == page}


def _lab_table(result: ParseResult, page: int = 1):
    tables = result.document.pages[page - 1].tables
    assert tables, "no table structure found on the page"
    return max(tables, key=lambda t: t.num_rows * t.num_cols)


def _grid(table) -> list[list[str]]:
    grid = [[""] * table.num_cols for _ in range(table.num_rows)]
    for cell in table.cells:
        grid[cell.row][cell.col] = cell.text
    return grid


# Measured in Task 2.4: docling-parse (Docling's PDF text extractor) maps these to "-" and Docling
# does not expose the setting. MediMind never repairs this; it flags the page instead.
DOCLING_PARSE_DASH_MAPPING = str.maketrans({"–": "-", "−": "-"})


def test_native_lab_table_structure_and_symbols(parsed: Callable[[str], ParseResult]) -> None:
    result = parsed("native")
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.NATIVE and page.ocr is None
    table = _lab_table(result)
    assert (table.num_rows, table.num_cols) == (len(pdfs.LAB_TABLE), 4)
    expected = [
        [cell.translate(DOCLING_PARSE_DASH_MAPPING) for cell in row] for row in pdfs.LAB_TABLE
    ]
    assert _grid(table) == expected  # µ ± ° ≤ ≥ ³ × exact; – and − arrive as "-"
    assert all(c.column_header for c in table.cells if c.row == 0)
    # The lost en dash / minus sign is detected against the PDF's own text layer and flagged.
    assert ReviewReason.DOCLING_NORMALIZED_TEXT in _reasons(result, 1)
    assert "–" not in page.text and "−" not in page.text  # never "repaired" by MediMind


def test_warm_up_loads_every_model_so_conversion_downloads_nothing(
    parser: DoclingParser, parsed: Callable[[str], ParseResult]
) -> None:
    pipeline = next(iter(parser._converter._converter.initialized_pipelines.values()))
    reader = pipeline.ocr_model.reader
    assert all(m is not None for m in (reader.text_det, reader.text_cls, reader.text_rec))
    models_dir = Path(reader.cfg.Global.model_root_dir)
    before = sorted((p.name, p.stat().st_size) for p in models_dir.iterdir())
    parsed("scanned")
    parsed("mixed")
    assert sorted((p.name, p.stat().st_size) for p in models_dir.iterdir()) == before


def test_pages_of_a_multi_page_pdf_keep_their_own_content(
    parsed: Callable[[str], ParseResult],
) -> None:
    result = parsed("three_pages")
    assert [p.page_number for p in result.document.pages] == [1, 2, 3]
    for page in result.document.pages:
        for other in (1, 2, 3):
            assert (pdfs.page_marker(other) in page.text) == (other == page.page_number)
        assert page.parse_method == ParseMethod.NATIVE and page.has_tables


def test_wrapped_hyphens_are_passed_through_and_flagged(
    parsed: Callable[[str], ParseResult],
) -> None:
    result = parsed("wrapped")
    assert ReviewReason.DOCLING_NORMALIZED_TEXT in _reasons(result, 1)
    _assert_pass_through(result)


def test_scanned_pdf_is_read_by_ocr_with_provenance(
    parsed: Callable[[str], ParseResult],
) -> None:
    result = parsed("scanned")
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.OCR and page.native_char_count == 0
    assert page.ocr is not None
    assert (page.ocr.engine, page.ocr.engine_version, page.ocr.backend) == (
        "rapidocr",
        "3.10.0",
        "torch",
    )
    assert page.ocr.mean_confidence is not None and 0 <= page.ocr.mean_confidence <= 1
    assert page.ocr.min_confidence is not None and 0 <= page.ocr.min_confidence <= 1
    assert "13.5" in page.text and "92" in page.text
    # The risk is communicated: OCR page (provenance above) and, on this fixture, a low-confidence
    # line ("℃" at 0.727 < 0.80). OCR misreads are kept as read (see the pass-through test).
    assert ReviewReason.LOW_OCR_CONFIDENCE in _reasons(result, 1)
    # Every OCR page is unverified, whatever its confidence; the flag is stored in the IR.
    assert ReviewReason.OCR_TEXT_UNVERIFIED in page.review_flags


def test_mixed_pdf_has_per_page_provenance(parsed: Callable[[str], ParseResult]) -> None:
    first, second = parsed("mixed").document.pages
    assert first.parse_method == ParseMethod.NATIVE and first.ocr is None
    assert second.parse_method == ParseMethod.OCR and second.ocr is not None
    assert ReviewReason.OCR_TEXT_UNVERIFIED not in first.review_flags
    assert ReviewReason.OCR_TEXT_UNVERIFIED in second.review_flags


@pytest.mark.parametrize("name", ["native", "three_pages", "wrapped", "mixed_visibility"])
def test_native_pdfs_have_no_ocr_flags(parsed: Callable[[str], ParseResult], name: str) -> None:
    for page in parsed(name).document.pages:
        assert page.parse_method == ParseMethod.NATIVE
        assert not set(page.review_flags) & OCR_ONLY_REASONS


@pytest.mark.parametrize("name", list(FIXTURES))
def test_review_flags_survive_ir_serialization(
    parsed: Callable[[str], ParseResult], name: str
) -> None:
    document = parsed(name).document
    assert load_ir(dump_ir(document)) == document


def test_blank_page_stays_empty_and_is_flagged(parsed: Callable[[str], ParseResult]) -> None:
    result = parsed("blank")
    page = result.document.pages[0]
    assert page.text == "" and not page.has_tables
    assert ReviewReason.EMPTY_PAGE in _reasons(result, 1)


@pytest.mark.parametrize("name", list(FIXTURES))
def test_ir_text_is_exactly_doclings_output(
    parsed: Callable[[str], ParseResult], name: str
) -> None:
    _assert_pass_through(parsed(name))


def _assert_pass_through(result: ParseResult) -> None:
    """Every single-page Docling text item and table cell appears verbatim in the IR."""
    docling = json.loads(result.docling_json)
    pages = {p.page_number: p for p in result.document.pages}
    for item in docling["texts"]:
        page_numbers = {prov["page_no"] for prov in item.get("prov", [])}
        if len(page_numbers) == 1 and item["text"]:
            assert item["text"] in pages[page_numbers.pop()].text
    for table in docling["tables"]:
        page_numbers = {prov["page_no"] for prov in table.get("prov", [])}
        if len(page_numbers) != 1:
            continue
        ir_cells = {
            (c.row, c.col): c.text for t in pages[page_numbers.pop()].tables for c in t.cells
        }
        for cell in table["data"]["table_cells"]:
            key = (cell["start_row_offset_idx"], cell["start_col_offset_idx"])
            if key in ir_cells:
                assert ir_cells[key] == cell["text"]


def test_stored_original_is_unchanged_and_logs_hold_no_content(
    parser: DoclingParser, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    stored = store_original(settings, uuid.UUID(int=1), pdfs.lab_table_pdf())
    before = hashlib.sha256(stored.path.read_bytes()).hexdigest()
    caplog.set_level(logging.DEBUG)
    validated = validate_pdf(stored.path, max_bytes=OPTIONS.max_bytes, max_pages=OPTIONS.max_pages)
    result = parser.parse(validated)
    assert hashlib.sha256(stored.path.read_bytes()).hexdigest() == before == stored.sha256
    assert result.document.sha256 == before
    assert not stored.path.stat().st_mode & stat.S_IWRITE  # still read-only
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in ("Test Person", "Haemoglobin", "13.5", "µIU/mL", str(stored.path)):
        assert secret not in logged


def test_one_converter_serves_every_document(
    parser: DoclingParser, parsed: Callable[[str], ParseResult]
) -> None:
    converter = parser._converter
    for name in ("native", "scanned"):
        parsed(name)
    parser.parse(_validated(pdfs.blank_pdf()))
    assert parser._converter is converter


def test_searchable_scan_is_ocr_without_false_native_flags(
    parsed: Callable[[str], ParseResult],
) -> None:
    """A page image under an invisible OCR text layer: Docling OCRs the image.

    The PDFium signals count visible text only, so the invisible layer adds no native characters,
    no mixed_native_and_ocr and no docling_normalized_text (before the visibility change it caused
    all three). The invisible layer itself is flagged invisible_text_layer.
    """
    result = parsed("searchable_scan")
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.OCR and page.native_char_count == 0
    assert page.ocr is not None and page.ocr.mean_confidence is not None
    assert ReviewReason.OCR_TEXT_UNVERIFIED in page.review_flags
    assert ReviewReason.INVISIBLE_TEXT_LAYER in page.review_flags
    assert ReviewReason.MIXED_NATIVE_AND_OCR not in page.review_flags
    assert ReviewReason.DOCLING_NORMALIZED_TEXT not in page.review_flags
    assert "–" not in page.text and "−" not in page.text  # the invisible layer is not the text
    assert "Haemoglobin" in page.text  # read by OCR from the image


def test_mixed_visibility_page_counts_visible_text_only(
    parsed: Callable[[str], ParseResult],
) -> None:
    """Visible, invisible and clip-only text on one native page (no image, so no OCR).

    native_char_count and the watched characters come from the visible text only. The page is
    flagged docling_normalized_text because its one *visible* en dash became "-" (correct), and
    invisible_text_layer because Docling keeps the invisible and clip-only text in its output.
    That text is passed through unchanged, never removed.
    """
    page = parsed("mixed_visibility").document.pages[0]
    visible = text_layer_stats(pdfs.classified_text())
    assert page.parse_method == ParseMethod.NATIVE and page.ocr is None
    assert page.native_char_count == visible.visible_char_count
    assert set(page.review_flags) == {
        ReviewReason.DOCLING_NORMALIZED_TEXT,
        ReviewReason.INVISIBLE_TEXT_LAYER,
    }
    assert all(marker in page.text for marker in ("Base excess", "Potassium", "hidden"))


@pytest.mark.parametrize("name", ["invisible_text", "clip_only_text"])
def test_invisible_text_without_an_image_is_native_and_flagged(
    parsed: Callable[[str], ParseResult], name: str
) -> None:
    """Only invisible (or clip-only) text, nothing drawn: nothing for OCR to read.

    Docling produces no OCR cells for the page, so no OCR is claimed: the page is native, with no
    OCR provenance and no OCR flags. Docling keeps the invisible text, which reaches the IR
    unchanged and is flagged invisible_text_layer.
    """
    page = parsed(name).document.pages[0]
    assert page.parse_method == ParseMethod.NATIVE and page.ocr is None
    assert page.native_char_count == 0
    assert page.review_flags == (ReviewReason.INVISIBLE_TEXT_LAYER,)
    assert "Haemoglobin" in page.text and "hidden" in page.text


REPLACEMENT_CHARACTER = "\ufffd"


def _pdfium_code_points(data: bytes) -> list[int]:
    """Every character code point PDFium reports for page 1 (test diagnostics only)."""
    document = pypdfium2.PdfDocument(data)
    try:
        page = document[0]
        text_page = page.get_textpage()
        try:
            return [
                pdfium_c.FPDFText_GetUnicode(text_page, i) for i in range(text_page.count_chars())
            ]
        finally:
            text_page.close()
            page.close()
    finally:
        document.close()


def test_unmapped_glyphs_reach_the_ir_as_replacement_characters_and_are_flagged(
    parsed: Callable[[str], ParseResult],
) -> None:
    """D7 spike, kept as a test: a Type3 font with unmappable glyph names and no ToUnicode.

    Docling's text holds one U+FFFD per glyph, never NUL; MediMind keeps them and flags the page.
    PDFium reports the same glyphs as their raw character codes, so PDFium and Docling disagree
    about unmapped glyphs: PDFium cannot be used to tell where a U+FFFD came from.
    """
    page = parsed("unmapped_glyph").document.pages[0]
    assert page.parse_method == ParseMethod.NATIVE and page.ocr is None
    assert page.text.count(REPLACEMENT_CHARACTER) == len(pdfs.UNMAPPED_GLYPH_CODES)
    assert "\x00" not in page.text
    assert ReviewReason.REPLACEMENT_CHARACTER in page.review_flags
    assert "Platelet count 2.5" in page.text  # the mapped text around them is unaffected
    codes = _pdfium_code_points(pdfs.unmapped_glyph_pdf())
    assert 0xFFFD not in codes
    assert [c for c in codes if c in (1, 2)] == list(pdfs.UNMAPPED_GLYPH_CODES)


def test_a_replacement_character_stored_in_the_source_is_kept_and_flagged(
    parsed: Callable[[str], ParseResult],
) -> None:
    """Fallback fixture: the PDF's own ToUnicode map stores U+FFFD for one glyph.

    The flag means "compare with the original", not "the parser failed": it is set the same way
    for a U+FFFD that is genuinely in the source (an accepted, documented false positive).
    """
    page = parsed("source_replacement_character").document.pages[0]
    assert page.text.count(REPLACEMENT_CHARACTER) == 1
    assert page.review_flags == (ReviewReason.REPLACEMENT_CHARACTER,)
    assert _pdfium_code_points(pdfs.source_replacement_character_pdf()).count(0xFFFD) == 1


def test_runtime_ocr_dictionary_cannot_emit_replacement_characters_or_nul(
    parser: DoclingParser,
) -> None:
    """The recognition dictionary RapidOCR actually loaded: OCR text is built only from it."""
    pipeline = next(iter(parser._converter._converter.initialized_pipelines.values()))
    decoder = pipeline.ocr_model.reader.text_rec.postprocess_op
    ignored = set(decoder.get_ignored_tokens())
    emittable = [c for i, c in enumerate(decoder.character) if i not in ignored]
    assert len(emittable) > 1000  # a real dictionary, not an empty stand-in
    joined = "".join(emittable)
    assert REPLACEMENT_CHARACTER not in joined
    assert "\x00" not in joined
    # The whole Unicode Control category: C0 (U+0000-U+001F, including tab and newlines), DEL
    # (U+007F) and C1 (U+0080-U+009F).
    assert not [c for c in joined if unicodedata.category(c) == "Cc"]


# Expected (parse_method, review flags) for every page of every fixture.
_N, _O = ParseMethod.NATIVE, ParseMethod.OCR
_R = ReviewReason
EXPECTED_PAGES: dict[str, list[tuple[ParseMethod, set[ReviewReason]]]] = {
    "native": [(_N, {_R.DOCLING_NORMALIZED_TEXT})],
    "three_pages": [(_N, {_R.DOCLING_NORMALIZED_TEXT})] * 3,
    "wrapped": [(_N, {_R.DOCLING_NORMALIZED_TEXT})],
    "scanned": [(_O, {_R.OCR_TEXT_UNVERIFIED, _R.LOW_OCR_CONFIDENCE})],
    "mixed": [
        (_N, {_R.DOCLING_NORMALIZED_TEXT}),
        (_O, {_R.OCR_TEXT_UNVERIFIED, _R.LOW_OCR_CONFIDENCE}),
    ],
    "blank": [(_N, {_R.EMPTY_PAGE})],
    "searchable_scan": [
        (_O, {_R.OCR_TEXT_UNVERIFIED, _R.INVISIBLE_TEXT_LAYER, _R.LOW_OCR_CONFIDENCE})
    ],
    "mixed_visibility": [(_N, {_R.DOCLING_NORMALIZED_TEXT, _R.INVISIBLE_TEXT_LAYER})],
    "invisible_text": [(_N, {_R.INVISIBLE_TEXT_LAYER})],
    "clip_only_text": [(_N, {_R.INVISIBLE_TEXT_LAYER})],
    "unmapped_glyph": [(_N, {_R.REPLACEMENT_CHARACTER})],
    "source_replacement_character": [(_N, {_R.REPLACEMENT_CHARACTER})],
}


def test_expected_pages_cover_every_fixture() -> None:
    assert set(EXPECTED_PAGES) == set(FIXTURES)


@pytest.mark.parametrize("name", list(FIXTURES))
def test_every_fixture_has_its_expected_method_and_flags(
    parsed: Callable[[str], ParseResult], name: str
) -> None:
    document = parsed(name).document
    actual = [(p.parse_method, set(p.review_flags)) for p in document.pages]
    assert actual == EXPECTED_PAGES[name]
    assert document.review_flags == ()
    for page in document.pages:
        assert (page.ocr is not None) == (page.parse_method is ParseMethod.OCR)
        assert page.review_flags == tuple(sorted(page.review_flags, key=lambda r: r.value))
