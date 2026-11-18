"""Exact spans in the stored page text (docs/PHASE_3_PLAN.md §6.2-§6.4). Fictional content only."""

import random
from types import SimpleNamespace

import pytest

from app.extraction.grounding import grounded, span_matches
from app.extraction.reasons import Abstention
from app.extraction.spans import (
    line_spans,
    locate_table,
    multi_row_rows,
    render_table_block,
    row_abstention,
    trim,
)
from app.ingestion.docling_mapping import _render_table_text
from app.ingestion.ir import ParsedTable, TableCell
from tests.fixtures.extraction_ir import table


def _docling_cells(t: ParsedTable) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(start_row_offset_idx=c.row, start_col_offset_idx=c.col, text=c.text)
        for c in t.cells
    ]


def _merged_table() -> ParsedTable:
    cells = [
        TableCell(row=0, col=0, text="Test", column_header=True),
        TableCell(row=0, col=1, text="Result", column_header=True),
        TableCell(row=1, col=0, text="Haemoglobin"),
        TableCell(row=1, col=1, text="13.2", row_span=2),
        TableCell(row=2, col=0, text="Platelets"),
        TableCell(row=3, col=0, text="MCV", col_span=2),
    ]
    return ParsedTable(table_index=0, num_rows=4, num_cols=2, cells=cells)


@pytest.mark.parametrize(
    "t",
    [
        table([("Test", "Result"), ("Hb", "13.2"), ("", "")]),
        table([("A", "", "C"), ("", "x", "")]),
        _merged_table(),
        ParsedTable(  # a row with no cells at all (a gap) and cells listed out of order
            table_index=0,
            num_rows=3,
            num_cols=2,
            cells=[
                TableCell(row=2, col=1, text="b"),
                TableCell(row=0, col=1, text="y"),
                TableCell(row=0, col=0, text="x"),
            ],
        ),
    ],
)
def test_render_matches_doclings_own_table_rendering(t: ParsedTable) -> None:
    assert render_table_block(t) == _render_table_text(_docling_cells(t))


def test_rows_and_cells_get_computed_exact_offsets() -> None:
    t = table([("Test", "Result"), ("Haemoglobin", "13.2"), ("Platelets", "2.6")])
    text = "Header line\n" + render_table_block(t) + "\nfooter"
    located = locate_table(text, t)
    assert not isinstance(located, Abstention)
    for row in located.rows:
        for cell in row.cells:
            assert text[cell.start : cell.end] == cell.cell.text
    assert text[located.rows[1].start : located.rows[1].end] == "Haemoglobin\t13.2"


def test_repeated_values_resolve_to_their_own_rows() -> None:
    t = table([("Test", "Result"), ("Urea", "5.0"), ("Uric Acid", "5.0"), ("Urea", "5.0")])
    text = render_table_block(t)
    located = locate_table(text, t)
    assert not isinstance(located, Abstention)
    values = [row.cell_at(1) for row in located.rows[1:]]
    assert len({(v.start, v.end) for v in values}) == 3
    assert all(text[v.start : v.end] == "5.0" for v in values)
    analytes = [row.cell_at(0) for row in located.rows[1:]]
    assert analytes[0].start != analytes[2].start  # the repeated analyte keeps its own row


def test_table_not_in_page_text_is_not_found() -> None:
    t = table([("Test", "Result"), ("Haemoglobin", "13.2")])
    reordered = "Result\tTest\n13.2\tHaemoglobin"  # same cells, another order
    assert locate_table(reordered, t) is Abstention.TABLE_BLOCK_NOT_FOUND


def test_table_rendered_twice_is_not_unique() -> None:
    t = table([("Test", "Result"), ("Haemoglobin", "13.2")])
    block = render_table_block(t)
    assert locate_table(block + "\n" + block, t) is Abstention.TABLE_BLOCK_NOT_UNIQUE


def test_empty_table_is_not_rendered() -> None:
    t = ParsedTable(table_index=0, num_rows=1, num_cols=1, cells=[])
    assert locate_table("anything", t) is Abstention.TABLE_BLOCK_NOT_RENDERED


def test_multiline_cell_row_is_refused() -> None:
    t = table([("Test", "Result"), ("Haemo-\nglobin", "13.2")])
    located = locate_table(render_table_block(t), t)
    assert not isinstance(located, Abstention)
    row = located.rows[1]
    assert row_abstention(row, value_col=1, multi_row=frozenset()) is (
        Abstention.CELL_CONTAINS_SEPARATOR
    )


def test_merged_cells_are_refused() -> None:
    t = _merged_table()
    located = locate_table(render_table_block(t), t)
    assert not isinstance(located, Abstention)
    multi = multi_row_rows(t)
    assert multi == frozenset({1, 2})
    by_row = {row.row: row for row in located.rows}
    assert row_abstention(by_row[1], value_col=1, multi_row=multi) is Abstention.ROW_SPANS_ROWS
    assert row_abstention(by_row[2], value_col=1, multi_row=multi) is Abstention.ROW_SPANS_ROWS
    mcv = by_row[3]
    assert row_abstention(mcv, value_col=0, multi_row=multi) is Abstention.MERGED_VALUE_CELL


def test_property_every_offset_is_an_exact_slice() -> None:
    rng = random.Random(20261117)
    alphabet = "abcXYZ019 .-/%<>"
    for _ in range(300):
        rows, cols = rng.randint(1, 5), rng.randint(1, 4)
        grid = [
            ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6))) for _ in range(cols)]
            for _ in range(rows)
        ]
        t = table(grid)
        prefix = "".join(rng.choice(alphabet + "\n") for _ in range(rng.randint(0, 20)))
        text = prefix + "\n" + render_table_block(t) + "\nend"
        located = locate_table(text, t)
        if isinstance(located, Abstention):
            assert located is Abstention.TABLE_BLOCK_NOT_UNIQUE or not render_table_block(t)
            continue
        assert text[located.start : located.end] == render_table_block(t)
        for row in located.rows:
            for cell in row.cells:
                assert text[cell.start : cell.end] == cell.cell.text


def test_line_spans_exclude_carriage_returns() -> None:
    text = "first\r\nsecond\n\nlast"
    spans = line_spans(text)
    assert [text[s.start : s.end] for s in spans] == ["first", "second", "", "last"]
    assert [s.number for s in spans] == [0, 1, 2, 3]


def test_trim_moves_offsets_and_never_edits() -> None:
    text = "  value  "
    assert trim(text, 0, len(text)) == (2, 7)
    assert trim("   ", 0, 3) is None


def test_grounding_requires_exact_slices_inside_the_excerpt() -> None:
    text = "Hb\t13.2\tg/dL"
    assert span_matches(text, 3, 7, "13.2")
    assert grounded(text, text, 0, len(text), [("13.2", 3, 7), ("Hb", 0, 2)])
    assert not grounded(text, text, 0, len(text), [("13.3", 3, 7)])
    assert not grounded(text, "Hb\t13.2", 0, 7, [("g/dL", 8, 12)])  # part outside the excerpt
    assert not grounded(text, "Hb", 1, 3, [])  # excerpt not at its offsets
    assert not grounded(text, "  ", 0, 2, [])
