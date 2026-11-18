"""Lab results printed as labelled lines (``lab-rules-2``; docs/PHASE_3_PLAN.md §4.3, §6.3).

Accepted shapes, each one text line of the page:
1. **One result**: ``<analyte>[:] <value> [<unit>] [(<range>)]``.
2. **Several results** (``lab-rules-2``): Docling joins printed lines, so one text line can hold
   ``<analyte>: <value> [<unit>] [(<range>)]`` several times. The segments are read only when
   they tile the whole line (nothing but whitespace between them) and every segment has a colon.

``<analyte>`` must map exactly to a catalogue alias (no table header gives context here). If any
segment's name does not map, the whole line is skipped (``line_analyte_unmapped``), never
partially read. Narrative sentences are never parsed. Lines are skipped when they contain a tab
(a rendered table row), lie inside a located table block, or are on a page with a table that could
not be located (its text may be in the page in another order, e.g. a previous-results column).

Locators: ``p<page>:l<line>`` for a one-result line; ``p<page>:l<line>:s<segment>`` for each
segment of a multi-result line.

History: ``lab-rules-1`` read one result per line only; on dev it missed results that Docling
had joined onto one line (``evals/reports/extract_v0/extraction_dev_lab-rules-1.md``).
"""

import re
from collections import Counter
from collections.abc import Sequence

from app.extraction.lab.tables import Part, RawRow
from app.extraction.normalize.analytes import map_analyte
from app.extraction.normalize.numbers import _NUMBER, QUALITATIVE
from app.extraction.reasons import Abstention
from app.extraction.reference import Reference
from app.extraction.spans import line_spans

_QUALITATIVE = "|".join(sorted((re.escape(k) for k in QUALITATIVE), key=len, reverse=True))
_VALUE = rf"(?P<value>(?:<=|>=|<|>)?\s*(?:{_NUMBER})|(?i:{_QUALITATIVE}))"
_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-z][A-Za-z0-9 ()'/.\-]*?)\s*[:\-]?\s+"
    + _VALUE
    + r"(?:\s+(?P<unit>[^\s()]+))?"
    r"(?:\s*\((?P<range>[^()]*)\))?\s*$"
)
# One segment of a multi-result line: the colon is required, the unit has no colon.
_SEGMENT = re.compile(
    r"\s*(?P<name>[A-Za-z][A-Za-z0-9 ()'/.\-]*?)\s*:\s*" + _VALUE + r"(?:\s+(?P<unit>[^\s():]+))?"
    r"(?:\s*\((?P<range>[^()]*)\))?(?=\s|$)"
)


def _part(match: re.Match[str], group: str, line_start: int) -> Part | None:
    """The group's text without surrounding whitespace, with exact page offsets."""
    raw = match[group]
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    start = line_start + match.start(group) + raw.index(value)
    return Part(value, start, start + len(value))


def _segments(text: str) -> list[re.Match[str]] | None:
    """Consecutive segments covering the whole line, or None."""
    found: list[re.Match[str]] = []
    position = 0
    while position < len(text) and text[position:].strip():
        match = _SEGMENT.match(text, position)
        if match is None or match.end() == position:
            return None
        found.append(match)
        position = match.end()
    return found if len(found) > 1 else None


def _row(page_number: int, page_text: str, line_start: int, match, locator: str) -> RawRow | None:
    name = _part(match, "name", line_start)
    value = _part(match, "value", line_start)
    if name is None or value is None:
        return None
    unit = _part(match, "unit", line_start)
    ref_range = _part(match, "range", line_start)
    last = max(p.end for p in (name, value, unit, ref_range) if p is not None)
    if ref_range is not None and page_text[last : last + 1] == ")":
        last += 1  # keep the closing parenthesis of the printed range in the excerpt
    return RawRow(
        page_number=page_number,
        locator=locator,
        excerpt=Part(page_text[name.start : last], name.start, last),
        analyte=name,
        value=value,
        unit=unit,
        ref_range=ref_range,
    )


def line_rows(
    page_number: int,
    page_text: str,
    reference: Reference,
    *,
    excluded: Sequence[tuple[int, int]],
) -> tuple[tuple[RawRow, ...], Counter]:
    abstentions: Counter = Counter()
    rows: list[RawRow] = []
    for line in line_spans(page_text):
        text = page_text[line.start : line.end]
        if "\t" in text or not text.strip():
            continue
        if any(start <= line.start < end or start < line.end <= end for start, end in excluded):
            continue
        segments = _segments(text)
        if segments is not None:
            if any(map_analyte(m["name"], reference) is None for m in segments):
                abstentions[Abstention.LINE_ANALYTE_UNMAPPED] += 1
                continue
            for index, match in enumerate(segments):
                locator = f"p{page_number}:l{line.number}:s{index}"
                row = _row(page_number, page_text, line.start, match, locator)
                if row is not None:
                    rows.append(row)
            continue
        match = _LINE.match(text)
        if not match:
            continue
        if map_analyte(match["name"], reference) is None:
            abstentions[Abstention.LINE_ANALYTE_UNMAPPED] += 1
            continue
        row = _row(page_number, page_text, line.start, match, f"p{page_number}:l{line.number}")
        if row is not None:
            rows.append(row)
    return tuple(rows), abstentions
