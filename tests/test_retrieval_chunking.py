"""Unit tests for deterministic chunking (Milestone R1.3). No database, no model."""

import pytest

from app.ingestion.ir import (
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    ParseMethod,
    ParserInfo,
    ReviewReason,
    TableCell,
)
from app.retrieval import chunking
from app.retrieval.chunking import ChunkDraft, chunk_document, chunk_stats, text_chunks

SHA = "a" * 64


def _page(number: int, text: str, tables: tuple[ParsedTable, ...] = (), **kw) -> ParsedPage:
    flags = (ReviewReason.EMPTY_PAGE,) if not text.strip() and not tables else ()
    return ParsedPage(
        page_number=number,
        text=text,
        parse_method=ParseMethod.NATIVE,
        native_char_count=len(text),
        tables=tables,
        review_flags=flags,
        **kw,
    )


def _document(*pages: ParsedPage) -> ParsedDocument:
    return ParsedDocument(
        sha256=SHA,
        page_count=len(pages),
        parser=ParserInfo(name="fake", version="1"),
        pages=pages,
    )


def _lab_table(index: int = 0) -> ParsedTable:
    cells = [
        TableCell(row=0, col=0, text="Test", column_header=True),
        TableCell(row=0, col=1, text="Result", column_header=True),
        TableCell(row=0, col=2, text="Unit", column_header=True),
        TableCell(row=1, col=0, text="Haemoglobin"),
        TableCell(row=1, col=1, text="13.5"),
        TableCell(row=1, col=2, text="gm/dL"),
        TableCell(row=2, col=0, text="HbA1c"),
        TableCell(row=2, col=1, text="6.1  H"),  # verbatim, double space kept
        TableCell(row=2, col=2, text="%"),
    ]
    return ParsedTable(table_index=index, num_rows=3, num_cols=3, cells=tuple(cells))


def test_text_chunks_are_exact_substrings_of_the_page_text() -> None:
    text = "Lab Report\n  Patient: Fictional  \n\nHaemoglobin\t13.5\tgm/dL\n"
    drafts = text_chunks(_page(1, text), max_chars=1000)
    assert len(drafts) == 1
    [draft] = drafts
    assert draft.content == text[draft.start : draft.end]
    assert draft.content == text.strip()  # only surrounding whitespace is excluded


def test_long_pages_are_split_by_line_with_one_line_overlap() -> None:
    lines = [f"line {n:02d} " + "x" * 30 for n in range(10)]  # 39 chars + newline each
    text = "\n".join(lines) + "\n"
    drafts = text_chunks(_page(1, text), max_chars=100)
    assert len(drafts) > 1
    for draft in drafts:
        assert draft.content == text[draft.start : draft.end]
        assert len(draft.content) <= 100
    for previous, current in zip(drafts, drafts[1:], strict=False):
        last_line = previous.content.splitlines()[-1]
        assert current.content.startswith(last_line)  # one shared line
    covered = "".join(d.content + "\n" for d in drafts)
    assert all(line in covered for line in lines)  # nothing is lost


def test_a_single_line_longer_than_the_limit_is_windowed_not_dropped() -> None:
    text = "y" * 250
    drafts = text_chunks(_page(1, text), max_chars=100)
    assert [len(d.content) for d in drafts] == [100, 100, 50]
    assert "".join(d.content for d in drafts) == text


def test_empty_and_whitespace_pages_give_no_chunks() -> None:
    document = _document(_page(1, ""), _page(2, "   \n\t\n"))
    assert chunk_document(document) == ()


def test_text_is_never_normalized() -> None:
    text = "Ｈｂ 13.5 – ﬁ  value‍"
    [draft] = text_chunks(_page(1, text))
    assert draft.content == text  # full-width, NBSP, en dash, ligature, ZWJ all kept


def test_chunks_never_cross_pages_and_keep_page_order() -> None:
    document = _document(_page(1, "first page"), _page(2, "second page", (_lab_table(),)))
    drafts = chunk_document(document)
    assert [(d.page_number, d.chunk_type) for d in drafts] == [
        (1, "text"),
        (2, "text"),
        (2, "table_rows"),
        (2, "table_rows"),
    ]
    assert chunk_document(document) == drafts  # deterministic


def test_table_rows_pair_verbatim_headers_and_cells() -> None:
    drafts = chunking.table_row_chunks(3, _lab_table(index=0))
    assert [d.content for d in drafts] == [
        "Test: Haemoglobin | Result: 13.5 | Unit: gm/dL",
        "Test: HbA1c | Result: 6.1  H | Unit: %",
    ]
    assert [d.section for d in drafts] == ["table 0, row 1", "table 0, row 2"]
    assert all(d.page_number == 3 and d.chunk_type == "table_rows" for d in drafts)


def test_columns_without_a_header_are_named_by_position() -> None:
    table = ParsedTable(
        table_index=0,
        num_rows=1,
        num_cols=2,
        cells=(TableCell(row=0, col=0, text="Urea"), TableCell(row=0, col=1, text="27")),
    )
    [draft] = chunking.table_row_chunks(1, table)
    assert draft.content == "column 1: Urea | column 2: 27"


def test_spanning_and_stacked_headers_apply_to_every_covered_column() -> None:
    table = ParsedTable(
        table_index=0,
        num_rows=3,
        num_cols=2,
        cells=(
            TableCell(row=0, col=0, col_span=2, text="Lipid", column_header=True),
            TableCell(row=1, col=0, text="Test", column_header=True),
            TableCell(row=1, col=1, text="Value", column_header=True),
            TableCell(row=2, col=0, text="LDL"),
            TableCell(row=2, col=1, text="112"),
        ),
    )
    [draft] = chunking.table_row_chunks(1, table)
    assert draft.content == "Lipid / Test: LDL | Lipid / Value: 112"


def test_empty_cells_and_empty_rows_are_skipped() -> None:
    table = ParsedTable(
        table_index=0,
        num_rows=2,
        num_cols=2,
        cells=(
            TableCell(row=0, col=0, text="  "),
            TableCell(row=1, col=0, text="TSH"),
            TableCell(row=1, col=1, text=""),
        ),
    )
    assert [d.content for d in chunking.table_row_chunks(1, table)] == ["column 1: TSH"]


def test_chunk_content_never_appears_in_repr() -> None:
    draft = ChunkDraft(1, "text", None, "Secret Fictional Value 13.5")
    assert "Secret" not in repr(draft)


def test_stats_measure_counts_sizes_and_the_token_limit() -> None:
    document = _document(_page(1, "short text"), _page(2, "x" * 50, (_lab_table(),)))
    drafts = chunk_document(document)
    without = chunk_stats(drafts)
    assert (without.chunks, without.text_chunks, without.table_chunks) == (4, 2, 2)
    assert without.max_chars == 50 and without.over_token_limit is None
    counted = chunk_stats(drafts, count_tokens=len, token_limit=40)
    assert counted.over_token_limit == 2  # the 50-char text and the 44-char table row


def test_max_chars_must_be_positive() -> None:
    with pytest.raises(ValueError):
        chunk_document(_document(_page(1, "x")), max_chars=0)


def test_chunker_version_is_pinned() -> None:
    assert chunking.CHUNKER_VERSION == "1"
