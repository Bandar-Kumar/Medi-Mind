"""Deterministic chunking of a verified parse IR into retrieval chunks (Milestone R1, ADR-035).

Rules:
- A chunk never crosses a page: each has exactly one ``page_number`` (exact page provenance).
- ``text`` chunks are exact spans of the page text: ``content == page.text[start:end]``. Lines are
  packed up to ``max_chars``; consecutive chunks overlap by one line. Leading and trailing
  whitespace is excluded by moving the span, never by editing text. Nothing is normalized.
- ``table_rows`` chunks: one per data row of a structured table, ``"<header>: <cell> | ..."``.
  Header and cell texts are verbatim (units and numbers exactly as printed). Columns without a
  header are named by position (``column 3``). The page text already contains the same row without
  headers (Docling renders tables into the page text); the overlap is intended, because headers
  make a value findable. Results are grouped by page later.
- Order: pages in order; on each page, text chunks, then table rows by table and row. The caller
  assigns ``chunk_index`` 0..n-1 in this order.

Changing any rule changes the output, so it needs a new ``CHUNKER_VERSION``.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from app.ingestion.ir import ParsedDocument, ParsedPage, ParsedTable

CHUNKER_VERSION = "1"
MAX_TEXT_CHUNK_CHARS = 1000
TEXT = "text"
TABLE_ROWS = "table_rows"
FIELD_SEPARATOR = " | "


@dataclass(frozen=True)
class ChunkDraft:
    """One chunk before indexing. ``content`` is evidence text: never logged or shown in repr."""

    page_number: int
    chunk_type: str  # TEXT or TABLE_ROWS
    section: str | None  # "table <i>, row <r>" for table rows; None for text
    content: str = field(repr=False)
    start: int | None = None  # text chunks: offsets into the page text
    end: int | None = None


def chunk_document(
    document: ParsedDocument, *, max_chars: int = MAX_TEXT_CHUNK_CHARS
) -> tuple[ChunkDraft, ...]:
    """All chunks of a verified document, in index order."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    drafts: list[ChunkDraft] = []
    for page in sorted(document.pages, key=lambda p: p.page_number):
        drafts.extend(text_chunks(page, max_chars=max_chars))
        for table in page.tables:
            drafts.extend(table_row_chunks(page.page_number, table))
    return tuple(drafts)


# --- text chunks ---------------------------------------------------------------------------------


def _line_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of every line, line ending included, covering ``text`` exactly."""
    spans = []
    position = 0
    for line in text.splitlines(keepends=True):
        spans.append((position, position + len(line)))
        position += len(line)
    return spans


def _trimmed(text: str, start: int, end: int) -> tuple[int, int] | None:
    """The span without surrounding whitespace, or None if it is only whitespace."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def _windows(start: int, end: int, size: int) -> Iterable[tuple[int, int]]:
    """Fixed windows over one line longer than ``size`` (no overlap)."""
    for window_start in range(start, end, size):
        yield window_start, min(window_start + size, end)


def text_chunks(page: ParsedPage, *, max_chars: int = MAX_TEXT_CHUNK_CHARS) -> list[ChunkDraft]:
    """Exact spans of the page text, packed by line, overlapping by one line."""
    text = page.text
    lines = _line_spans(text)
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        line_start, line_end = lines[i]
        if line_end - line_start > max_chars:  # one very long line: fixed windows
            spans.extend(_windows(line_start, line_end, max_chars))
            i += 1
            continue
        j = i
        while j + 1 < len(lines) and lines[j + 1][1] - line_start <= max_chars:
            j += 1
        spans.append((line_start, lines[j][1]))
        if j + 1 >= len(lines):
            break
        # Overlap by one line, but always advance (a single-line chunk moves to the next line).
        i = j if j > i and lines[j + 1][1] - lines[j][0] <= max_chars else j + 1
    drafts = []
    for start, end in spans:
        trimmed = _trimmed(text, start, end)
        if trimmed is None:
            continue
        s, e = trimmed
        drafts.append(ChunkDraft(page.page_number, TEXT, None, text[s:e], s, e))
    return drafts


# --- table-row chunks ----------------------------------------------------------------------------


def _header_rows(table: ParsedTable) -> set[int]:
    """Rows whose non-empty cells are all column headers."""
    by_row: dict[int, list[bool]] = {}
    for cell in table.cells:
        if cell.text.strip():
            by_row.setdefault(cell.row, []).append(cell.column_header)
    return {row for row, flags in by_row.items() if flags and all(flags)}


def _column_headers(table: ParsedTable, header_rows: set[int]) -> dict[int, str]:
    """Header text per column; stacked header rows are joined with " / " (verbatim parts)."""
    parts: dict[int, list[str]] = {}
    for cell in sorted(table.cells, key=lambda c: (c.row, c.col)):
        if cell.row not in header_rows or not cell.text.strip():
            continue
        for col in range(cell.col, cell.col + cell.col_span):
            parts.setdefault(col, []).append(cell.text)
    return {col: " / ".join(texts) for col, texts in parts.items()}


def table_row_chunks(page_number: int, table: ParsedTable) -> list[ChunkDraft]:
    """One chunk per data row: ``header: cell`` pairs in column order, texts verbatim."""
    header_rows = _header_rows(table)
    headers = _column_headers(table, header_rows)
    rows: dict[int, list[tuple[int, str]]] = {}
    for cell in table.cells:
        if cell.row in header_rows or not cell.text.strip():
            continue
        rows.setdefault(cell.row, []).append((cell.col, cell.text))
    drafts = []
    for row in sorted(rows):
        fields = [
            f"{headers.get(col) or f'column {col + 1}'}: {text}" for col, text in sorted(rows[row])
        ]
        drafts.append(
            ChunkDraft(
                page_number,
                TABLE_ROWS,
                f"table {table.table_index}, row {row}",
                FIELD_SEPARATOR.join(fields),
            )
        )
    return drafts


# --- measurements --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkStats:
    chunks: int
    text_chunks: int
    table_chunks: int
    max_chars: int
    over_token_limit: int | None  # None: no tokenizer was given


def chunk_stats(
    drafts: Sequence[ChunkDraft],
    *,
    count_tokens: Callable[[str], int] | None = None,
    token_limit: int = 512,
) -> ChunkStats:
    """Counts and size measurements; ``over_token_limit`` uses the model's own tokenizer."""
    over = None
    if count_tokens is not None:
        over = sum(1 for d in drafts if count_tokens(d.content) > token_limit)
    return ChunkStats(
        chunks=len(drafts),
        text_chunks=sum(1 for d in drafts if d.chunk_type == TEXT),
        table_chunks=sum(1 for d in drafts if d.chunk_type == TABLE_ROWS),
        max_chars=max((len(d.content) for d in drafts), default=0),
        over_token_limit=over,
    )
