"""Exact source spans in the stored page text (docs/PHASE_3_PLAN.md §6.2, §6.3).

Every excerpt MediMind stores is a slice of ``pages.text``; offsets are computed, never guessed.

**Tables.** ``app.ingestion.docling_mapping`` puts each structurally valid table into the page
text as one segment, rendered by ``_render_table_text``: the rows that have cells, in start-row
order, joined with ``"\n"``; within a row, the cells in start-column order, joined with
``"\t"``; cell text verbatim. ``render_table_block`` reproduces that rendering from the IR
cells (a unit test checks the two renderers agree). The block is then located by exact search:
it must occur exactly once in the page text. Row and cell offsets inside the block are computed
by adding lengths, so a repeated value or analyte always resolves to its own row.

Rows whose boundaries cannot be proven are refused (``row_abstention``): a cell containing a tab
or a newline, a row touched by a multi-row cell, or a value cell spanning several columns.

**Lines.** A line is a maximal run of the page text without ``"\n"`` (a trailing ``"\r"`` is
excluded by moving the span end). Matches inside a line are offset by the line start.
"""

from dataclasses import dataclass, field

from app.extraction.reasons import Abstention
from app.ingestion.ir import ParsedTable, TableCell

ROW_SEPARATOR = "\n"
CELL_SEPARATOR = "\t"


@dataclass(frozen=True)
class CellSpan:
    col: int
    start: int  # offsets into the PAGE text
    end: int
    cell: TableCell = field(repr=False)


@dataclass(frozen=True)
class RowSpan:
    row: int
    start: int  # offsets into the PAGE text
    end: int
    cells: tuple[CellSpan, ...]

    def cell_at(self, col: int) -> CellSpan | None:
        for cell in self.cells:
            if cell.col == col:
                return cell
        return None


@dataclass(frozen=True)
class TableSpan:
    start: int
    end: int
    rows: tuple[RowSpan, ...]


def _rows(table: ParsedTable) -> dict[int, list[TableCell]]:
    rows: dict[int, list[TableCell]] = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)
    for cells in rows.values():
        cells.sort(key=lambda c: c.col)
    return rows


def render_table_block(table: ParsedTable) -> str:
    """The table exactly as Docling's mapping writes it into the page text."""
    rows = _rows(table)
    return ROW_SEPARATOR.join(
        CELL_SEPARATOR.join(cell.text for cell in rows[row]) for row in sorted(rows)
    )


def locate_table(page_text: str, table: ParsedTable) -> TableSpan | Abstention:
    """The table's exact position in the page text, or why it cannot be proven."""
    block = render_table_block(table)
    if not block:
        return Abstention.TABLE_BLOCK_NOT_RENDERED
    first = page_text.find(block)
    if first < 0:
        return Abstention.TABLE_BLOCK_NOT_FOUND
    if page_text.find(block, first + 1) >= 0:
        return Abstention.TABLE_BLOCK_NOT_UNIQUE
    rows = _rows(table)
    position = first
    row_spans: list[RowSpan] = []
    for index, row in enumerate(sorted(rows)):
        if index:
            position += len(ROW_SEPARATOR)
        row_start = position
        cell_spans: list[CellSpan] = []
        for cell_index, cell in enumerate(rows[row]):
            if cell_index:
                position += len(CELL_SEPARATOR)
            cell_spans.append(CellSpan(cell.col, position, position + len(cell.text), cell))
            position += len(cell.text)
        row_spans.append(RowSpan(row, row_start, position, tuple(cell_spans)))
    if position != first + len(block):  # arithmetic must land exactly on the block end
        return Abstention.SPAN_VERIFICATION_FAILED
    return TableSpan(first, first + len(block), tuple(row_spans))


def multi_row_rows(table: ParsedTable) -> frozenset[int]:
    """Rows touched by any cell spanning several rows (their boundaries are not provable)."""
    touched: set[int] = set()
    for cell in table.cells:
        if cell.row_span > 1:
            touched.update(range(cell.row, cell.row + cell.row_span))
    return frozenset(touched)


def row_abstention(row: RowSpan, *, value_col: int, multi_row: frozenset[int]) -> Abstention | None:
    """Why this row's provenance cannot be proven exactly, or None."""
    if any(CELL_SEPARATOR in c.cell.text or ROW_SEPARATOR in c.cell.text for c in row.cells):
        return Abstention.CELL_CONTAINS_SEPARATOR
    if row.row in multi_row:
        return Abstention.ROW_SPANS_ROWS
    value = row.cell_at(value_col)
    if value is not None and value.cell.col_span > 1:
        return Abstention.MERGED_VALUE_CELL
    return None


@dataclass(frozen=True)
class LineSpan:
    number: int  # 0-based line index in the page text
    start: int
    end: int


def line_spans(page_text: str) -> tuple[LineSpan, ...]:
    """Every line of the page text with exact offsets (``"\r"`` before a newline excluded)."""
    spans: list[LineSpan] = []
    start = 0
    number = 0
    while start <= len(page_text):
        newline = page_text.find(ROW_SEPARATOR, start)
        end = len(page_text) if newline < 0 else newline
        trimmed = end - 1 if end > start and page_text[end - 1] == "\r" else end
        spans.append(LineSpan(number, start, trimmed))
        if newline < 0:
            break
        start = newline + 1
        number += 1
    return tuple(spans)


def trim(page_text: str, start: int, end: int) -> tuple[int, int] | None:
    """The span without surrounding whitespace (by moving offsets, never editing text)."""
    while start < end and page_text[start].isspace():
        start += 1
    while end > start and page_text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None
