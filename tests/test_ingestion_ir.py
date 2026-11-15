"""Unit tests for the parse IR (app/ingestion/ir.py). Fictional data only."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.errors import DocumentParseError
from app.ingestion.ir import (
    IR_SCHEMA_VERSION,
    OcrProvenance,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    ParseMethod,
    ParserInfo,
    ReviewReason,
    TableCell,
    canonical_flags,
    dump_ir,
    load_ir,
)
from app.ingestion.storage import write_new_file
from app.records.models import ParseMethod as RecordsParseMethod

SHA = "a" * 64
# Exact OCR-style text, including double spaces, a trailing newline and a misread unit.
EXACT_TEXT = "Haemoglobin  13.5 g/dL\nGlucose 92 7p/6 \n"
OCR = OcrProvenance(engine="rapidocr", engine_version="3.10.0", backend="torch")


def _table(**overrides: Any) -> ParsedTable:
    fields: dict[str, Any] = {
        "table_index": 0,
        "num_rows": 2,
        "num_cols": 2,
        "cells": [
            TableCell(row=0, col=0, text="Test", column_header=True),
            TableCell(row=0, col=1, text="Unit", column_header=True),
            TableCell(row=1, col=0, text="Haemoglobin"),
            TableCell(row=1, col=1, text=" g/dL "),
        ],
    }
    return ParsedTable(**(fields | overrides))


def _page(number: int = 1, **overrides: Any) -> ParsedPage:
    fields: dict[str, Any] = {
        "page_number": number,
        "text": EXACT_TEXT,
        "parse_method": ParseMethod.NATIVE,
        "native_char_count": 120,
    }
    return ParsedPage(**(fields | overrides))


def _document(pages: list[ParsedPage], page_count: int | None = None) -> ParsedDocument:
    return ParsedDocument(
        sha256=SHA,
        page_count=len(pages) if page_count is None else page_count,
        parser=ParserInfo(name="docling", version="2.150.1", options={"do_ocr": True}),
        pages=pages,
    )


def test_parse_method_matches_the_records_enum() -> None:
    assert {m.value for m in ParseMethod} == {m.value for m in RecordsParseMethod}


def test_round_trip_preserves_everything_exactly() -> None:
    document = _document(
        [
            _page(1, tables=[_table()]),
            _page(
                2,
                text="",
                parse_method=ParseMethod.OCR,
                native_char_count=0,
                ocr=OCR,
                review_flags=[ReviewReason.EMPTY_PAGE, ReviewReason.OCR_TEXT_UNVERIFIED],
            ),
        ]
    )
    data = dump_ir(document)
    assert data == dump_ir(document)  # deterministic
    loaded = load_ir(data)
    assert loaded == document
    assert loaded.pages[0].text == EXACT_TEXT  # not stripped, normalized or corrected
    assert loaded.pages[0].tables[0].cells[3].text == " g/dL "
    assert json.loads(data)["schema_version"] == IR_SCHEMA_VERSION


@pytest.mark.parametrize("numbers", [[1, 3], [1, 1], [2], [2, 1]])
def test_pages_must_be_numbered_1_to_n(numbers: list[int]) -> None:
    with pytest.raises(ValidationError):
        _document([_page(n) for n in numbers])


def test_page_count_must_match_the_pages() -> None:
    with pytest.raises(ValidationError):
        _document([_page(1)], page_count=2)


def test_ocr_provenance_iff_ocr_page() -> None:
    with pytest.raises(ValidationError):
        _page(parse_method=ParseMethod.OCR)  # OCR page without provenance
    with pytest.raises(ValidationError):
        _page(ocr=OCR)  # native page with OCR provenance
    with pytest.raises(ValidationError):
        _page(parse_method="handwriting")


@pytest.mark.parametrize(
    "cells",
    [
        [TableCell(row=2, col=0, text="x")],  # row outside the grid
        [TableCell(row=0, col=1, col_span=2, text="x")],  # column span overflows
        [TableCell(row=1, col=0, row_span=2, text="x")],  # row span overflows
        [TableCell(row=0, col=0, text="a"), TableCell(row=0, col=0, text="b")],  # same start
        [  # overlapping spans with different starts
            TableCell(row=0, col=0, col_span=2, text="a"),
            TableCell(row=0, col=1, text="b"),
        ],
        [
            TableCell(row=0, col=0, row_span=2, text="a"),
            TableCell(row=1, col=0, text="b"),
        ],
    ],
)
def test_table_cells_must_fit_the_grid_without_overlap(cells: list[TableCell]) -> None:
    with pytest.raises(ValidationError):
        _table(cells=cells)


def test_full_spans_that_fit_are_accepted() -> None:
    table = _table(
        cells=[
            TableCell(row=0, col=0, col_span=2, text="Complete blood count", column_header=True),
            TableCell(row=1, col=0, text="WBC"),
            TableCell(row=1, col=1, text="7,200"),
        ]
    )
    assert len(table.cells) == 3


def test_table_indexes_must_be_sequential() -> None:
    with pytest.raises(ValidationError):
        _page(tables=[_table(table_index=1)])


def test_invalid_sha256_is_rejected() -> None:
    for bad in ("A" * 64, "a" * 63, "g" * 64):
        with pytest.raises(ValidationError):
            ParsedDocument(
                sha256=bad,
                page_count=1,
                parser=ParserInfo(name="docling", version="2.150.1"),
                pages=[_page()],
            )


def test_has_tables_and_is_empty() -> None:
    assert _page(tables=[_table()]).has_tables
    assert not _page().has_tables
    assert _page(text=" \n").is_empty
    assert not _page(text=" \n", tables=[_table()]).is_empty
    assert not _page().is_empty


def test_repr_never_contains_medical_text() -> None:
    page = _page(tables=[_table()])
    assert "Haemoglobin" not in repr(page)
    assert "Haemoglobin" not in repr(_document([page]))


@pytest.mark.parametrize("change", ["unknown_field", "schema_v2", "not_json"])
def test_load_rejects_invalid_files_without_echoing_values(change: str) -> None:
    raw = json.loads(dump_ir(_document([_page()])))
    if change == "unknown_field":
        raw["pages"][0]["patient_name"] = "Test Person"
    elif change == "schema_v2":
        raw["schema_version"] = 2
    data = b"{not json Haemoglobin" if change == "not_json" else json.dumps(raw).encode()
    with pytest.raises(DocumentParseError) as caught:
        load_ir(data)
    assert caught.value.kind == "ir_invalid"
    assert "Test Person" not in str(caught.value)
    assert "Haemoglobin" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_load_error_never_contains_keys_from_the_file() -> None:
    # Unknown keys at several levels, and a bad value under an arbitrary `options` key: the keys
    # themselves are untrusted input (here, fictional personal data) and must not be echoed.
    raw = json.loads(dump_ir(_document([_page(tables=[_table()])])))
    raw["Test Person DOB 01-01-1990"] = "x"
    raw["pages"][0]["SECRET page key"] = "x"
    raw["pages"][0]["tables"][0]["cells"][0]["SECRET cell key"] = "x"
    raw["parser"]["options"]["SECRET option key"] = ["not", "allowed"]
    with pytest.raises(DocumentParseError) as caught:
        load_ir(json.dumps(raw).encode())
    message = str(caught.value)
    assert caught.value.kind == "ir_invalid"
    assert "Test Person" not in message and "SECRET" not in message
    assert "<field>: extra_forbidden" in message
    assert "pages.0.<field>: extra_forbidden" in message
    assert caught.value.__cause__ is None and caught.value.__context__ is None


# --- review flags in the IR (Task 2.4, change A) ----------------------------------------------


def _ocr_page(number: int = 1, **overrides: Any) -> ParsedPage:
    fields: dict[str, Any] = {
        "parse_method": ParseMethod.OCR,
        "native_char_count": 0,
        "ocr": OCR,
        "review_flags": [ReviewReason.OCR_TEXT_UNVERIFIED],
    }
    return _page(number, **(fields | overrides))


def test_every_ocr_page_must_carry_ocr_text_unverified() -> None:
    assert ReviewReason.OCR_TEXT_UNVERIFIED in _ocr_page().review_flags
    with pytest.raises(ValidationError):
        _ocr_page(review_flags=[])
    with pytest.raises(ValidationError):
        _ocr_page(review_flags=[ReviewReason.LOW_OCR_CONFIDENCE])


@pytest.mark.parametrize(
    "reason",
    [
        ReviewReason.OCR_TEXT_UNVERIFIED,
        ReviewReason.LOW_OCR_CONFIDENCE,
        ReviewReason.OCR_CONFIDENCE_UNAVAILABLE,
        ReviewReason.MIXED_NATIVE_AND_OCR,
    ],
)
def test_native_pages_cannot_carry_ocr_flags(reason: ReviewReason) -> None:
    with pytest.raises(ValidationError):
        _page(review_flags=[reason])


def test_canonical_flags_are_unique_and_sorted() -> None:
    flags = canonical_flags(
        [
            ReviewReason.OCR_TEXT_UNVERIFIED,
            ReviewReason.LOW_OCR_CONFIDENCE,
            ReviewReason.OCR_TEXT_UNVERIFIED,
        ]
    )
    assert flags == (ReviewReason.LOW_OCR_CONFIDENCE, ReviewReason.OCR_TEXT_UNVERIFIED)
    assert _ocr_page(review_flags=flags).review_flags == flags


@pytest.mark.parametrize(
    "flags",
    [
        [ReviewReason.OCR_TEXT_UNVERIFIED, ReviewReason.LOW_OCR_CONFIDENCE],  # unsorted
        [ReviewReason.OCR_TEXT_UNVERIFIED, ReviewReason.OCR_TEXT_UNVERIFIED],  # duplicate
    ],
)
def test_non_canonical_page_flags_are_rejected_not_rewritten(flags: list[ReviewReason]) -> None:
    with pytest.raises(ValidationError):
        _ocr_page(review_flags=flags)


def test_document_level_flags_are_limited() -> None:
    document = ParsedDocument(
        sha256=SHA,
        page_count=1,
        parser=ParserInfo(name="docling", version="2.150.1"),
        pages=[_page()],
        review_flags=[ReviewReason.UNPLACED_ITEM],
    )
    assert document.review_flags == (ReviewReason.UNPLACED_ITEM,)
    with pytest.raises(ValidationError):
        ParsedDocument(
            sha256=SHA,
            page_count=1,
            parser=ParserInfo(name="docling", version="2.150.1"),
            pages=[_page()],
            review_flags=[ReviewReason.EMPTY_PAGE],
        )


def test_flags_survive_serialization_and_storage(tmp_path: Path) -> None:
    document = _document(
        [
            _page(1, review_flags=[ReviewReason.DOCLING_NORMALIZED_TEXT]),
            _ocr_page(
                2,
                review_flags=[ReviewReason.LOW_OCR_CONFIDENCE, ReviewReason.OCR_TEXT_UNVERIFIED],
            ),
        ]
    )
    stored = tmp_path / "data" / "parsed" / "ir.v1.json"
    assert write_new_file(tmp_path / "data", stored, dump_ir(document))
    loaded = load_ir(stored.read_bytes())
    assert loaded == document
    assert dump_ir(loaded) == stored.read_bytes()  # loads back byte-for-byte
    assert loaded.pages[0].review_flags == (ReviewReason.DOCLING_NORMALIZED_TEXT,)
    assert loaded.pages[1].review_flags == (
        ReviewReason.LOW_OCR_CONFIDENCE,
        ReviewReason.OCR_TEXT_UNVERIFIED,
    )
    assert json.loads(stored.read_bytes())["pages"][1]["review_flags"] == [
        "low_ocr_confidence",
        "ocr_text_unverified",
    ]


def test_loading_an_ocr_page_without_the_flag_is_rejected() -> None:
    raw = json.loads(dump_ir(_document([_ocr_page()])))
    raw["pages"][0]["review_flags"] = []
    with pytest.raises(DocumentParseError) as caught:
        load_ir(json.dumps(raw).encode())
    assert caught.value.kind == "ir_invalid"


# --- audit hardening (Task 2.4 close-out) ---------------------------------------------------------


def test_validation_errors_never_contain_input_values() -> None:  # change 1
    secret = "Test Person HIV+ 13.5"
    attempts = [
        lambda: _page(text=secret, review_flags=[ReviewReason.OCR_TEXT_UNVERIFIED]),
        lambda: TableCell(row=-1, col=0, text=secret),
        lambda: _table(cells=[TableCell(row=5, col=0, text=secret)]),
        lambda: ParsedPage(page_number=1, text=secret, parse_method="ocr", native_char_count=0),
    ]
    for attempt in attempts:
        with pytest.raises(ValidationError) as caught:
            attempt()
        assert secret not in str(caught.value)
        assert "input_value" not in str(caught.value)


def test_unplaced_item_is_document_only() -> None:  # change 3
    with pytest.raises(ValidationError):
        _page(review_flags=[ReviewReason.UNPLACED_ITEM])
    assert _page(review_flags=[ReviewReason.UNMAPPED_ITEM]).review_flags == (
        ReviewReason.UNMAPPED_ITEM,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": "13.5 g/dL"},  # page has text
        {"text": "", "tables": [_table()]},  # page has a table
    ],
)
def test_empty_page_flag_requires_an_empty_page(overrides: dict[str, Any]) -> None:  # change 4
    with pytest.raises(ValidationError):
        _page(review_flags=[ReviewReason.EMPTY_PAGE], **overrides)
    assert _page(text=" \n", review_flags=[ReviewReason.EMPTY_PAGE]).is_empty


def test_multi_page_item_unplaced_requires_multi_page_item() -> None:  # change 4
    with pytest.raises(ValidationError):
        _page(review_flags=[ReviewReason.MULTI_PAGE_ITEM_UNPLACED])
    flags = canonical_flags([ReviewReason.MULTI_PAGE_ITEM, ReviewReason.MULTI_PAGE_ITEM_UNPLACED])
    assert _page(review_flags=flags).review_flags == flags


def test_collections_are_immutable_tuples_with_unchanged_json() -> None:  # change 5
    document = _document([_ocr_page(tables=[_table()])])
    page = document.pages[0]
    for collection in (document.pages, document.review_flags, page.tables, page.review_flags):
        assert isinstance(collection, tuple)
    assert isinstance(page.tables[0].cells, tuple)
    with pytest.raises(AttributeError):
        page.review_flags.append(ReviewReason.EMPTY_PAGE)  # type: ignore[attr-defined]
    raw = json.loads(dump_ir(document))
    assert isinstance(raw["pages"], list) and isinstance(raw["review_flags"], list)
    assert isinstance(raw["pages"][0]["tables"][0]["cells"], list)
    assert raw["pages"][0]["review_flags"] == ["ocr_text_unverified"]
    assert set(raw) == {"schema_version", "sha256", "page_count", "parser", "pages", "review_flags"}


@pytest.mark.parametrize(
    ("where", "flags"),
    [
        ("page", ["ocr_text_unverified", "ocr_text_unverified"]),  # duplicate
        ("page", ["ocr_text_unverified", "low_ocr_confidence"]),  # unsorted
        ("document", ["unplaced_item", "unplaced_item"]),
        ("document", ["unplaced_item", "unmapped_item"]),
    ],
)
def test_loading_non_canonical_flags_is_rejected(where: str, flags: list[str]) -> None:  # 6
    raw = json.loads(dump_ir(_document([_ocr_page()])))
    if where == "page":
        raw["pages"][0]["review_flags"] = flags
    else:
        raw["review_flags"] = flags
    with pytest.raises(DocumentParseError) as caught:
        load_ir(json.dumps(raw).encode())
    assert caught.value.kind == "ir_invalid"


# --- invisible_text_layer -------------------------------------------------------------------------


def test_invisible_text_layer_is_a_page_flag_on_native_and_ocr_pages() -> None:
    native = _page(native_char_count=0, review_flags=[ReviewReason.INVISIBLE_TEXT_LAYER])
    assert native.review_flags == (ReviewReason.INVISIBLE_TEXT_LAYER,)
    # Not an OCR-only flag: a native page may carry it; an OCR page (searchable scan) too.
    ocr = _ocr_page(
        review_flags=canonical_flags(
            [ReviewReason.OCR_TEXT_UNVERIFIED, ReviewReason.INVISIBLE_TEXT_LAYER]
        )
    )
    assert ocr.review_flags == (
        ReviewReason.INVISIBLE_TEXT_LAYER,
        ReviewReason.OCR_TEXT_UNVERIFIED,
    )


def test_invisible_text_layer_is_not_a_document_flag() -> None:
    with pytest.raises(ValidationError):
        ParsedDocument(
            sha256=SHA,
            page_count=1,
            parser=ParserInfo(name="docling", version="2.150.1"),
            pages=[_page()],
            review_flags=[ReviewReason.INVISIBLE_TEXT_LAYER],
        )
    raw = json.loads(dump_ir(_document([_page()])))
    raw["review_flags"] = ["invisible_text_layer"]
    with pytest.raises(DocumentParseError) as caught:
        load_ir(json.dumps(raw).encode())
    assert caught.value.kind == "ir_invalid"


def test_invisible_text_layer_has_a_canonical_position() -> None:
    every_page_flag = [
        ReviewReason.OCR_TEXT_UNVERIFIED,
        ReviewReason.INVISIBLE_TEXT_LAYER,
        ReviewReason.EMPTY_PAGE,
        ReviewReason.DOCLING_NORMALIZED_TEXT,
        ReviewReason.LOW_OCR_CONFIDENCE,
        ReviewReason.MIXED_NATIVE_AND_OCR,
    ]
    assert [r.value for r in canonical_flags(every_page_flag)] == [
        "docling_normalized_text",
        "empty_page",
        "invisible_text_layer",
        "low_ocr_confidence",
        "mixed_native_and_ocr",
        "ocr_text_unverified",
    ]


@pytest.mark.parametrize(
    "flags",
    [
        ["invisible_text_layer", "invisible_text_layer"],  # duplicate
        ["ocr_text_unverified", "invisible_text_layer"],  # unsorted
    ],
)
def test_non_canonical_invisible_text_layer_flags_are_rejected(flags: list[str]) -> None:
    raw = json.loads(dump_ir(_document([_ocr_page()])))
    raw["pages"][0]["review_flags"] = flags
    with pytest.raises(DocumentParseError):
        load_ir(json.dumps(raw).encode())


def test_invisible_text_layer_survives_serialization_with_unchanged_layout() -> None:
    page = _page(
        text="",
        native_char_count=0,
        review_flags=canonical_flags([ReviewReason.INVISIBLE_TEXT_LAYER, ReviewReason.EMPTY_PAGE]),
    )
    document = _document([page])
    data = dump_ir(document)
    assert load_ir(data) == document and dump_ir(load_ir(data)) == data
    raw = json.loads(data)
    assert raw["pages"][0]["review_flags"] == ["empty_page", "invisible_text_layer"]
    assert set(raw) == {"schema_version", "sha256", "page_count", "parser", "pages", "review_flags"}
    assert raw["schema_version"] == IR_SCHEMA_VERSION == 1


# --- replacement_character (D7) -------------------------------------------------------------------

FFFD = "\ufffd"
# Fictional text with two U+FFFD where a glyph could not be mapped; kept exactly as received.
FFFD_TEXT = f"Haemoglobin 13.5 g/dL {FFFD}{FFFD} flag\n"


def _fffd_table() -> ParsedTable:
    return _table(
        cells=[
            TableCell(row=0, col=0, text="Test", column_header=True),
            TableCell(row=0, col=1, text="Unit", column_header=True),
            TableCell(row=1, col=0, text="Haemoglobin"),
            TableCell(row=1, col=1, text=f"g/{FFFD}L"),
        ]
    )


def test_replacement_character_is_a_page_flag_on_native_and_ocr_pages() -> None:
    flag = ReviewReason.REPLACEMENT_CHARACTER
    native = _page(text=FFFD_TEXT, review_flags=[flag])
    assert native.text == FFFD_TEXT and native.review_flags == (flag,)  # text unchanged
    ocr = _ocr_page(
        text=FFFD_TEXT,
        review_flags=canonical_flags([ReviewReason.OCR_TEXT_UNVERIFIED, flag]),
    )
    assert ocr.review_flags == (ReviewReason.OCR_TEXT_UNVERIFIED, flag)


def test_replacement_character_is_neither_document_level_nor_ocr_only() -> None:
    from app.ingestion.ir import DOCUMENT_REASONS, OCR_ONLY_REASONS

    assert ReviewReason.REPLACEMENT_CHARACTER not in DOCUMENT_REASONS
    assert ReviewReason.REPLACEMENT_CHARACTER not in OCR_ONLY_REASONS


def test_replacement_character_in_text_requires_the_flag() -> None:
    with pytest.raises(ValidationError):
        _page(text=FFFD_TEXT)
    with pytest.raises(ValidationError):
        _ocr_page(text=FFFD_TEXT)  # OCR flags alone are not enough


def test_replacement_character_in_a_table_cell_requires_the_flag() -> None:
    with pytest.raises(ValidationError):
        _page(tables=[_fffd_table()])
    page = _page(tables=[_fffd_table()], review_flags=[ReviewReason.REPLACEMENT_CHARACTER])
    assert page.tables[0].cells[3].text == f"g/{FFFD}L"


def test_replacement_character_flag_without_the_character_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _page(review_flags=[ReviewReason.REPLACEMENT_CHARACTER])
    with pytest.raises(ValidationError):
        _page(
            text="",
            native_char_count=0,
            review_flags=canonical_flags(
                [ReviewReason.EMPTY_PAGE, ReviewReason.REPLACEMENT_CHARACTER]
            ),
        )


@pytest.mark.parametrize(
    "text",
    [
        "Result ? g/dL",  # question mark
        "Result □ g/dL",  # white square, a common look-alike
        "Result \ue000 g/dL",  # private-use character (icon fonts)
        "Result \\ufffd g/dL",  # the escape sequence written as plain text
        "Result U+FFFD g/dL",  # the code point name written as plain text
        "• Haemoglobin 13.5",  # a mapped bullet
    ],
)
def test_look_alikes_of_the_replacement_character_need_no_flag(text: str) -> None:
    assert _page(text=text).review_flags == ()
    with pytest.raises(ValidationError):
        _page(text=text, review_flags=[ReviewReason.REPLACEMENT_CHARACTER])


def test_replacement_character_is_not_a_document_flag() -> None:
    page = _page(text=FFFD_TEXT, review_flags=[ReviewReason.REPLACEMENT_CHARACTER])
    with pytest.raises(ValidationError):
        ParsedDocument(
            sha256=SHA,
            page_count=1,
            parser=ParserInfo(name="docling", version="2.150.1"),
            pages=[page],
            review_flags=[ReviewReason.REPLACEMENT_CHARACTER],
        )
    raw = json.loads(dump_ir(_document([page])))
    raw["review_flags"] = ["replacement_character"]
    with pytest.raises(DocumentParseError) as caught:
        load_ir(json.dumps(raw).encode())
    assert caught.value.kind == "ir_invalid"


def test_loading_an_unflagged_replacement_character_is_rejected_without_echoing_text() -> None:
    secret = f"Test Person 13.5 {FFFD}"
    raw = json.loads(dump_ir(_document([_page()])))
    raw["pages"][0]["text"] = secret
    with pytest.raises(DocumentParseError) as caught:
        load_ir(json.dumps(raw, ensure_ascii=False).encode())
    assert caught.value.kind == "ir_invalid"
    assert "Test Person" not in str(caught.value) and FFFD not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    with pytest.raises(ValidationError) as direct:
        _page(text=secret)
    assert "Test Person" not in str(direct.value) and "input_value" not in str(direct.value)


def test_replacement_character_has_a_canonical_position() -> None:
    flags = [
        ReviewReason.REPLACEMENT_CHARACTER,
        ReviewReason.OCR_TEXT_UNVERIFIED,
        ReviewReason.MIXED_NATIVE_AND_OCR,
        ReviewReason.DOCLING_NORMALIZED_TEXT,
    ]
    assert [r.value for r in canonical_flags(flags)] == [
        "docling_normalized_text",
        "mixed_native_and_ocr",
        "ocr_text_unverified",
        "replacement_character",
    ]
    raw = json.loads(dump_ir(_document([_ocr_page()])))
    raw["pages"][0]["text"] = FFFD_TEXT
    raw["pages"][0]["review_flags"] = ["replacement_character", "ocr_text_unverified"]  # unsorted
    with pytest.raises(DocumentParseError):
        load_ir(json.dumps(raw).encode())


def test_replacement_character_survives_serialization_with_unchanged_layout() -> None:
    page = _page(
        text=FFFD_TEXT,
        tables=[_fffd_table()],
        review_flags=[ReviewReason.REPLACEMENT_CHARACTER],
    )
    document = _document([page])
    data = dump_ir(document)
    assert load_ir(data) == document and dump_ir(load_ir(data)) == data
    assert load_ir(data).pages[0].text == FFFD_TEXT  # byte-for-byte, U+FFFD kept
    raw = json.loads(data)
    assert raw["pages"][0]["review_flags"] == ["replacement_character"]
    assert set(raw) == {"schema_version", "sha256", "page_count", "parser", "pages", "review_flags"}
    assert set(raw["pages"][0]) == {
        "page_number",
        "text",
        "parse_method",
        "native_char_count",
        "ocr",
        "tables",
        "review_flags",
    }
    assert raw["schema_version"] == IR_SCHEMA_VERSION == 1


# --- lone surrogates (Task 2.5d) ------------------------------------------------------------------

HIGH_SURROGATE = chr(0xD800)
LOW_SURROGATE = chr(0xDC00)
SUPPLEMENTARY = chr(0x1D707)  # MATHEMATICAL ITALIC SMALL MU, outside the BMP


@pytest.mark.parametrize("surrogate", [HIGH_SURROGATE, LOW_SURROGATE])
def test_lone_surrogates_are_rejected_in_page_and_cell_text(surrogate: str) -> None:
    with pytest.raises(ValidationError):
        _page(text=f"Haemoglobin 13.5 {surrogate}")
    with pytest.raises(ValidationError):
        TableCell(row=0, col=0, text=f"g/dL{surrogate}")


@pytest.mark.parametrize("surrogate", [HIGH_SURROGATE, LOW_SURROGATE])
def test_lone_surrogates_are_rejected_in_parser_and_ocr_fields(surrogate: str) -> None:
    attempts = [
        lambda: ParserInfo(name="docling", version="2.150.1", options={"key": surrogate}),
        lambda: ParserInfo(name="docling", version="2.150.1", options={surrogate: True}),
        lambda: ParserInfo(name=f"docling{surrogate}", version="2.150.1"),
        lambda: OcrProvenance(engine="rapidocr", engine_version=surrogate, backend="torch"),
    ]
    for attempt in attempts:
        with pytest.raises(ValidationError):
            attempt()


def test_lone_surrogate_errors_never_echo_the_text() -> None:
    secret = f"Test Person 13.5 {HIGH_SURROGATE}"
    with pytest.raises(ValidationError) as caught:
        _page(text=secret)
    assert "Test Person" not in str(caught.value) and "input_value" not in str(caught.value)


def test_loading_a_lone_surrogate_escape_is_rejected_as_invalid_ir() -> None:
    data = dump_ir(_document([_page(text="Test Person 13.5 PLACEHOLDER")])).decode("utf-8")
    escaped = json.dumps(HIGH_SURROGATE)[1:-1]  # the six characters of a JSON \uD800 escape
    assert escaped.lower() == "\\" + "ud800"
    with pytest.raises(DocumentParseError) as caught:
        load_ir(data.replace("PLACEHOLDER", escaped).encode("utf-8"))
    assert caught.value.kind == "ir_invalid"
    assert "Test Person" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_replacement_and_supplementary_characters_round_trip_exactly() -> None:
    text = f"TSH 2.5 {FFFD}IU/mL; {SUPPLEMENTARY} 4.1"
    cell = TableCell(row=0, col=0, text=SUPPLEMENTARY)
    table = ParsedTable(table_index=0, num_rows=1, num_cols=1, cells=[cell])
    page = _page(text=text, tables=[table], review_flags=[ReviewReason.REPLACEMENT_CHARACTER])
    document = _document([page])
    data = dump_ir(document)
    loaded = load_ir(data)
    assert loaded == document and dump_ir(loaded) == data
    assert loaded.pages[0].text == text and loaded.pages[0].tables[0].cells[0].text == SUPPLEMENTARY
    # A surrogate *pair* in JSON is one supplementary character, not two lone halves.
    paired = data.decode("utf-8").replace(SUPPLEMENTARY, json.dumps(SUPPLEMENTARY)[1:-1])
    assert load_ir(paired.encode("utf-8")) == document


def test_lone_surrogate_rule_leaves_the_json_layout_unchanged() -> None:
    raw = json.loads(dump_ir(_document([_page()])))
    assert set(raw) == {"schema_version", "sha256", "page_count", "parser", "pages", "review_flags"}
    assert raw["schema_version"] == IR_SCHEMA_VERSION == 1
