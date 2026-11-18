"""Build verified IR documents in code for extraction tests (fictional content only).

Page text is assembled the way ``app.ingestion.docling_mapping`` assembles it: text segments and
each table's rendered block (rows joined by newlines, cells by tabs), joined with newlines.
"""

from collections.abc import Sequence

from app.extraction.spans import render_table_block
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

SHA = "a" * 64
GRID_HEADER = ("Test", "Result", "Unit", "Reference Range", "Flag")


def table(rows: Sequence[Sequence[str]], *, index: int = 0, header: bool = True) -> ParsedTable:
    cells = [
        TableCell(row=r, col=c, text=text, column_header=header and r == 0)
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    return ParsedTable(
        table_index=index, num_rows=len(rows), num_cols=max(len(r) for r in rows), cells=cells
    )


def page(
    number: int,
    segments: Sequence[str | ParsedTable],
    *,
    flags: Sequence[ReviewReason] = (),
    ocr: bool = False,
) -> ParsedPage:
    parts = [s if isinstance(s, str) else render_table_block(s) for s in segments]
    tables = tuple(s for s in segments if isinstance(s, ParsedTable))
    all_flags = set(flags)
    if ocr:
        all_flags.add(ReviewReason.OCR_TEXT_UNVERIFIED)
    return ParsedPage(
        page_number=number,
        text="\n".join(parts),
        parse_method=ParseMethod.OCR if ocr else ParseMethod.NATIVE,
        native_char_count=0 if ocr else 100,
        ocr=OcrProvenance(engine="test", engine_version="1", backend="test") if ocr else None,
        tables=tables,
        review_flags=tuple(sorted(all_flags, key=lambda f: f.value)),
    )


def document(pages: Sequence[ParsedPage], flags: Sequence[ReviewReason] = ()) -> ParsedDocument:
    return ParsedDocument(
        sha256=SHA,
        page_count=len(pages),
        parser=ParserInfo(name="test", version="1"),
        pages=tuple(pages),
        review_flags=tuple(flags),
    )


def texts(doc: ParsedDocument) -> dict[int, str]:
    return {p.page_number: p.text for p in doc.pages}


def lab_page(number: int = 1, rows: Sequence[Sequence[str]] = (), **kw) -> ParsedPage:
    """A grid-layout lab report page with the usual header lines."""
    return page(
        number,
        [
            "Sunrise Diagnostics (Fictional)",
            "Patient Name: Asha Fictional",
            "Report Date: 25/03/2024",
            "COMPLETE BLOOD COUNT (CBC)",
            table([GRID_HEADER, *rows]),
            "SYNTHETIC TEST DOCUMENT - fictional patient and values, not a medical record.",
        ],
        **kw,
    )
