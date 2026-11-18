"""Allergy statements: items, statuses and tables (``clinical-rules-1`` / ``clinical-norm-1``).

Status, from the printed words only:
- ``No known (drug) allergies``, ``NKDA``, ``NKA``, ``None known``, ``Nil``, ``None`` →
  ``none_known`` (stored with ``negated = true``);
- ``suspected``, ``possible``, ``probable`` or a leading ``?`` → ``suspected``;
- anything else listed → ``confirmed`` (the document lists it as an allergy).

``Not recorded``, ``Not known``, ``Unknown``, ``Not assessed``, ``N/A``, ``-`` and ``?`` say the
status is **unknown**: no fact is written (abstention ``allergy_status_unknown``). An empty or
missing allergy field is never read as "no allergies".

Item grammar (list form): ``<substance> [(<reaction>)] [- <severity>] [- suspected]`` or
``<substance> - Reaction: <r> - Severity: <s> - Status: <st>``. Items of one value are separated
by ``;`` (and by ``,`` outside brackets). Tables are tab-separated rows under a header naming an
allergen column (``Allergen``, ``Substance``, ``Drug``) and optionally ``Reaction``, ``Severity``,
``Status``.
"""

import re
from dataclasses import dataclass
from typing import Final

from app.clinical.medications import Part
from app.extraction.spans import trim

NONE_KNOWN: Final = "none_known"
SUSPECTED: Final = "suspected"
CONFIRMED: Final = "confirmed"
SEVERITIES: Final = ("mild", "moderate", "severe")

_NONE = re.compile(
    r"(?:no\s+known\s+(?:drug\s+)?allerg(?:y|ies)|nkda|nka|none\s+known|nil\s+known|nil|none)\.?",
    re.IGNORECASE,
)
_UNKNOWN = re.compile(
    r"(?:not\s+recorded|not\s+known|unknown|not\s+assessed|n/?a|-|\?)\.?", re.IGNORECASE
)
_SUSPECTED = re.compile(r"(?<!\w)(?:suspected|possible|probable)(?!\w)", re.IGNORECASE)
_SEVERITY = re.compile(r"(?<!\w)(?:mild|moderate|severe)(?!\w)", re.IGNORECASE)
_LABELLED = re.compile(r"(?<!\w)(?P<label>reaction|severity|status)\s*:\s*", re.IGNORECASE)
_SEPARATOR = re.compile(r"\s+-\s+")
_HEADER_ALLERGEN = re.compile(r"^(?:allergen|substance|drug|allergy)s?$", re.IGNORECASE)


@dataclass(frozen=True)
class AllergyItem:
    excerpt: Part
    substance: Part
    status: str
    reaction: Part | None = None
    severity: Part | None = None
    status_word: Part | None = None


def split_items(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Item spans of one allergy value: split at ``;`` and at ``,`` outside brackets."""
    spans: list[tuple[int, int]] = []
    depth = 0
    item_start = start
    for position in range(start, end):
        char = text[position]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" or (char == "," and depth == 0):
            span = trim(text, item_start, position)
            if span:
                spans.append(span)
            item_start = position + 1
    span = trim(text, item_start, end)
    if span:
        spans.append(span)
    return [(s, e - 1 if text[e - 1] == "." else e) for s, e in spans if e - s > 0]


def is_none_known(text: str) -> bool:
    return bool(_NONE.fullmatch(text.strip()))


def is_unknown(text: str) -> bool:
    return bool(_UNKNOWN.fullmatch(text.strip()))


def _part(text: str, start: int, end: int) -> Part | None:
    span = trim(text, start, end)
    if span is None:
        return None
    s, e = span
    while e > s and text[e - 1] in ".,;":
        e -= 1
    return Part(text[s:e], s, e) if e > s else None


def parse_item(text: str, start: int, end: int) -> AllergyItem | None:
    """One listed allergy (not a "none known" statement). None: no substance found."""
    excerpt = Part(text[start:end], start, end)
    labelled = list(_LABELLED.finditer(text, start, end))
    reaction = severity = status_word = None
    if labelled:
        head_end = labelled[0].start()
        for index, match in enumerate(labelled):
            value_end = labelled[index + 1].start() if index + 1 < len(labelled) else end
            separator = _SEPARATOR.search(text, match.end(), value_end)
            stop = separator.start() if separator else value_end
            value = _part(text, match.end(), stop)
            label = match["label"].casefold()
            if label == "reaction":
                reaction = value
            elif label == "severity":
                severity = value
            else:
                status_word = value
        separator = _SEPARATOR.search(text, start, head_end)
        substance = _part(text, start, separator.start() if separator else head_end)
    else:
        bracket = text.find("(", start, end)
        dash = _SEPARATOR.search(text, start, end)
        head_end = min(p for p in (bracket if bracket >= 0 else end, dash.start() if dash else end))
        substance = _part(text, start, head_end)
        if bracket >= 0:
            close = text.find(")", bracket, end)
            if close > bracket:
                reaction = _part(text, bracket + 1, close)
        severity_match = _SEVERITY.search(text, head_end, end)
        if severity_match:
            severity = Part(severity_match.group(0), severity_match.start(), severity_match.end())
        suspected_match = _SUSPECTED.search(text, head_end, end)
        if suspected_match:
            status_word = Part(
                suspected_match.group(0), suspected_match.start(), suspected_match.end()
            )
    if substance is None:
        return None
    question = substance.text.startswith("?")
    if question:
        substance = _part(text, substance.start + 1, substance.end)
        if substance is None:
            return None
    suspected = question or bool(status_word and _SUSPECTED.search(status_word.text))
    return AllergyItem(
        excerpt=excerpt,
        substance=substance,
        status=SUSPECTED if suspected else CONFIRMED,
        reaction=reaction,
        severity=severity,
        status_word=status_word,
    )


def severity_norm(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip().casefold()
    return key if key in SEVERITIES else None


def substance_norm(raw: str) -> str:
    return " ".join(raw.casefold().split())


@dataclass(frozen=True)
class TableColumns:
    allergen: int
    reaction: int | None
    severity: int | None
    status: int | None


def table_header(cells: list[str]) -> TableColumns | None:
    """Column indexes when ``cells`` is an allergy-table header, else None."""
    keys = [cell.strip().casefold() for cell in cells]
    allergen = next((i for i, k in enumerate(keys) if _HEADER_ALLERGEN.fullmatch(k)), None)
    if allergen is None or len(cells) < 2:
        return None

    def find(name: str) -> int | None:
        return next((i for i, k in enumerate(keys) if k == name), None)

    columns = TableColumns(allergen, find("reaction"), find("severity"), find("status"))
    if columns.reaction is None and columns.severity is None and columns.status is None:
        return None
    return columns


def cell_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The tab-separated cells of one line, as untrimmed spans."""
    spans = []
    position = start
    while True:
        tab = text.find("\t", position, end)
        stop = end if tab < 0 else tab
        spans.append((position, stop))
        if tab < 0:
            return spans
        position = tab + 1


def table_item(
    text: str, row_start: int, row_end: int, columns: TableColumns
) -> AllergyItem | None:
    cells = cell_spans(text, row_start, row_end)

    def cell(index: int | None) -> Part | None:
        if index is None or index >= len(cells):
            return None
        return _part(text, *cells[index])

    substance = cell(columns.allergen)
    if substance is None:
        return None
    status_word = cell(columns.status)
    suspected = bool(status_word and _SUSPECTED.search(status_word.text))
    return AllergyItem(
        excerpt=Part(text[row_start:row_end], row_start, row_end),
        substance=substance,
        status=SUSPECTED if suspected else CONFIRMED,
        reaction=cell(columns.reaction),
        severity=cell(columns.severity),
        status_word=status_word,
    )
