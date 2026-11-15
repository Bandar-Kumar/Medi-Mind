"""Unit tests for IR → page records (app/services/parsed_pages.py). No database: the repository
call is replaced by a recorder. All text is fictional."""

import uuid

import pytest

from app.core.errors import DocumentProcessingError
from app.ingestion.ir import (
    OcrProvenance,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    ParseMethod,
    ParserInfo,
    ReviewReason,
    TableCell,
)
from app.services import parsed_pages as sp

FFFD = chr(0xFFFD)
SUPPLEMENTARY = chr(0x1D707)
TEXTS = (
    "  Haemoglobin  13.5 g/dL\n\n",
    f"TSH 2.5 {FFFD}IU/mL {SUPPLEMENTARY}",
    "",
    "Glucose 92 7p/6 \t",
)
OCR = OcrProvenance(engine="rapidocr", engine_version="3.10.0", backend="torch")


def _ir() -> ParsedDocument:
    table = ParsedTable(
        table_index=0, num_rows=1, num_cols=1, cells=[TableCell(row=0, col=0, text="13.5")]
    )
    pages = [
        ParsedPage(
            page_number=1,
            text=TEXTS[0],
            parse_method=ParseMethod.NATIVE,
            native_char_count=20,
            tables=[table],
        ),
        ParsedPage(
            page_number=2,
            text=TEXTS[1],
            parse_method=ParseMethod.NATIVE,
            native_char_count=12,
            review_flags=[ReviewReason.REPLACEMENT_CHARACTER],
        ),
        ParsedPage(
            page_number=3,
            text=TEXTS[2],
            parse_method=ParseMethod.NATIVE,
            native_char_count=0,
            review_flags=[ReviewReason.EMPTY_PAGE],
        ),
        ParsedPage(
            page_number=4,
            text=TEXTS[3],
            parse_method=ParseMethod.OCR,
            native_char_count=0,
            ocr=OCR,
            review_flags=[ReviewReason.OCR_TEXT_UNVERIFIED],
        ),
    ]
    return ParsedDocument(
        sha256="e" * 64, page_count=4, parser=ParserInfo(name="docling", version="x"), pages=pages
    )


def _one_page(text: str) -> ParsedDocument:
    """A one-page IR whose text bypasses the IR validator (constructed without validation)."""
    page = ParsedPage.model_construct(
        page_number=1,
        text=text,
        parse_method=ParseMethod.NATIVE,
        native_char_count=1,
        ocr=None,
        tables=(),
        review_flags=(),
    )
    return ParsedDocument.model_construct(
        schema_version=1,
        sha256="e" * 64,
        page_count=1,
        parser=ParserInfo(name="docling", version="x"),
        pages=(page,),
        review_flags=(),
    )


def test_pages_map_in_order_with_exact_text() -> None:
    document = _ir()
    records = sp.page_records_from_ir(document, expected_page_count=4)
    assert [r.page_number for r in records] == [1, 2, 3, 4]
    assert tuple(r.text for r in records) == TEXTS
    assert all(r.text is p.text for r, p in zip(records, document.pages, strict=True))
    assert [r.parse_method for r in records] == ["native", "native", "native", "ocr"]
    assert [r.has_tables for r in records] == [True, False, False, False]
    assert "Haemoglobin" not in repr(records[0])


@pytest.mark.parametrize("expected", [3, 5])
def test_page_count_must_match_the_record(expected: int) -> None:
    with pytest.raises(DocumentProcessingError) as caught:
        sp.page_records_from_ir(_ir(), expected_page_count=expected)
    assert caught.value.kind == "integrity_mismatch"


@pytest.mark.parametrize("bad", [chr(0), chr(0xD800), chr(0xDFFF)])
def test_unstorable_text_is_rejected_before_the_repository(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(sp, "persist_parsed_pages", lambda *args: calls.append(args))
    document = _one_page(f"Test Person 13.5 {bad}")
    with pytest.raises(DocumentProcessingError) as caught:
        sp.persist_ir_pages(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), document, expected_page_count=1
        )
    assert caught.value.kind == "text_not_storable"
    assert str(caught.value).startswith("Page 1 ")
    assert "Test Person" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert calls == []


def test_persist_calls_the_repository_once_with_all_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    def record(*args: object) -> int:
        calls.append(args)
        return len(args[3])  # type: ignore[arg-type]

    monkeypatch.setattr(sp, "persist_parsed_pages", record)
    patient, document_id, run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert sp.persist_ir_pages(patient, document_id, run, _ir(), expected_page_count=4) == 4
    assert len(calls) == 1 and calls[0][:3] == (patient, document_id, run)
    assert tuple(r.text for r in calls[0][3]) == TEXTS
