"""Lab result rows from verified IR tables (``lab-rules-1``; docs/PHASE_3_PLAN.md §4.3, §6.2).

A table is read only when its header row gives exactly one ``name`` and one ``value`` column
(``historical`` columns are never read) and the table's rendered block occurs exactly once in
the page text. Each data row becomes a ``RawRow`` whose parts carry exact page-text offsets.
Nothing is guessed: any row whose provenance cannot be proven is counted as an abstention.
"""

from collections import Counter
from dataclasses import dataclass, field

from app.extraction.lab.headers import (
    FLAG,
    HISTORICAL,
    NAME,
    RANGE,
    SPECIMEN,
    UNIT,
    VALUE,
    header_role,
)
from app.extraction.normalize.numbers import is_placeholder
from app.extraction.reasons import Abstention
from app.extraction.spans import (
    CellSpan,
    RowSpan,
    locate_table,
    multi_row_rows,
    row_abstention,
    trim,
)
from app.ingestion.ir import ParsedTable


@dataclass(frozen=True)
class Part:
    """An exact slice of the page text."""

    text: str = field(repr=False)
    start: int
    end: int


@dataclass(frozen=True)
class RawRow:
    page_number: int
    locator: str
    excerpt: Part
    analyte: Part
    value: Part
    unit: Part | None = None
    ref_range: Part | None = None
    flag: Part | None = None
    specimen: Part | None = None


@dataclass(frozen=True)
class TableResult:
    rows: tuple[RawRow, ...]
    abstentions: Counter
    span: tuple[int, int] | None  # the located block (to keep line extraction out of it)


def _roles(table: ParsedTable) -> tuple[int, dict[int, str]] | None:
    """(header row index, column -> role), or None when no valid header row exists."""
    marked = sorted({cell.row for cell in table.cells if cell.column_header})
    candidates = [marked[0]] if marked else [0]
    for row in candidates:
        cells = [cell for cell in table.cells if cell.row == row and cell.text.strip()]
        roles: dict[int, str] = {}
        for cell in cells:
            role = header_role(cell.text)
            if role is None:
                if not marked:
                    return None  # an unmarked first row must be all header words
                continue
            if cell.col_span > 1 and role != HISTORICAL:
                return None  # a role spanning columns is ambiguous
            roles[cell.col] = role
        return row, roles
    return None


def _part(page_text: str, cell: CellSpan | None) -> Part | None:
    if cell is None:
        return None
    span = trim(page_text, cell.start, cell.end)
    if span is None:
        return None
    return Part(page_text[span[0] : span[1]], span[0], span[1])


def table_rows(page_number: int, page_text: str, table: ParsedTable) -> TableResult:
    abstentions: Counter = Counter()
    found = _roles(table)
    if found is None:
        abstentions[Abstention.TABLE_UNRECOGNIZED] += 1
        return TableResult((), abstentions, None)
    header_row, roles = found
    by_role: dict[str, list[int]] = {}
    for col, role in roles.items():
        by_role.setdefault(role, []).append(col)
    if HISTORICAL in by_role:
        abstentions[Abstention.HISTORICAL_COLUMN] += 1  # those columns are never read
    single = {role: cols[0] for role, cols in by_role.items() if len(cols) == 1}
    duplicated = [r for r, cols in by_role.items() if len(cols) > 1 and r != HISTORICAL]
    if NAME not in single or VALUE not in single or duplicated:
        abstentions[Abstention.TABLE_UNRECOGNIZED] += 1
        return TableResult((), abstentions, None)
    located = locate_table(page_text, table)
    if isinstance(located, Abstention):
        abstentions[located] += 1
        return TableResult((), abstentions, None)
    multi_row = multi_row_rows(table)
    rows: list[RawRow] = []
    for row in located.rows:
        if row.row <= header_row:
            continue
        outcome = _row(page_number, page_text, table, row, single, multi_row)
        if isinstance(outcome, RawRow):
            rows.append(outcome)
        elif outcome is not None:
            abstentions[outcome] += 1
    return TableResult(tuple(rows), abstentions, (located.start, located.end))


def _row(
    page_number: int,
    page_text: str,
    table: ParsedTable,
    row: RowSpan,
    cols: dict[str, int],
    multi_row: frozenset[int],
) -> RawRow | Abstention | None:
    non_empty = [c for c in row.cells if c.cell.text.strip()]
    if not non_empty:
        return None  # a blank row: nothing to extract, nothing to report
    if all(header_role(c.cell.text) for c in non_empty):
        return None  # a repeated header row
    if len(non_empty) == 1 and non_empty[0].col == cols[NAME]:
        return None  # a section title such as "COMPLETE BLOOD COUNT"
    refused = row_abstention(row, value_col=cols[VALUE], multi_row=multi_row)
    if refused is not None:
        return refused
    name_cell, value_cell = row.cell_at(cols[NAME]), row.cell_at(cols[VALUE])
    if name_cell is None or value_cell is None:
        return Abstention.MISSING_CELL
    analyte = _part(page_text, name_cell)
    if analyte is None:
        return Abstention.EMPTY_ANALYTE
    value = _part(page_text, value_cell)
    if value is None:
        return Abstention.EMPTY_VALUE
    if is_placeholder(value.text):
        return Abstention.PLACEHOLDER_VALUE
    excerpt_span = trim(page_text, row.start, row.end)
    if excerpt_span is None:
        return None
    return RawRow(
        page_number=page_number,
        locator=f"p{page_number}:t{table.table_index}:r{row.row}",
        excerpt=Part(page_text[excerpt_span[0] : excerpt_span[1]], *excerpt_span),
        analyte=analyte,
        value=value,
        unit=_part(page_text, row.cell_at(cols[UNIT])) if UNIT in cols else None,
        ref_range=_part(page_text, row.cell_at(cols[RANGE])) if RANGE in cols else None,
        flag=_part(page_text, row.cell_at(cols[FLAG])) if FLAG in cols else None,
        specimen=_part(page_text, row.cell_at(cols[SPECIMEN])) if SPECIMEN in cols else None,
    )
