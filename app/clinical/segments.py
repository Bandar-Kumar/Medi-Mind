"""Label-based segmentation of clinical page text (``clinical-rules-1``; docs/PHASE_6_PLAN.md §6).

Docling often merges several printed lines into one text line, so a field label is found
**anywhere** in a line, not only at its start. A field's value runs from the end of its label to
the next label on the same line (any registry label or stop label) or the end of the line.

List fields (medications, past and surgical history, allergies, discontinued medications) also
continue onto the following lines, but only while each following line starts with an item marker
(``1.``, ``-``, ``•``, ``Tab.`` ...) and carries no label of its own before that marker. A line
that is neither ends the list. Every span is an offset into the page text; the text itself is
never modified.

Stop labels (patient fields, advice, course, findings, signature, the synthetic disclaimer ...)
end a value but are never extracted.
"""

import re
from dataclasses import dataclass
from typing import Final

from app.extraction.spans import line_spans, trim

# Field keys (the extractor maps each key to fact types, per document type).
DATE: Final = "date"
ADMISSION: Final = "admission_date"
DISCHARGE: Final = "discharge_date"
CONSULTANT: Final = "consultant"
COMPLAINTS: Final = "complaints"
PAST_HISTORY: Final = "past_history"
SURGICAL_HISTORY: Final = "surgical_history"
FAMILY_HISTORY: Final = "family_history"
DIAGNOSIS: Final = "diagnosis"
PROCEDURES: Final = "procedures"
ALLERGIES: Final = "allergies"
MEDICATIONS: Final = "medications"
DISCHARGE_MEDICATIONS: Final = "discharge_medications"
CURRENT_MEDICATIONS: Final = "current_medications"
STOPPED: Final = "stopped"
FOLLOW_UP: Final = "follow_up"
STUDY: Final = "study"
STOP: Final = "stop"  # ends a value, never extracted

LIST_KEYS: Final = frozenset(
    {
        MEDICATIONS,
        DISCHARGE_MEDICATIONS,
        CURRENT_MEDICATIONS,
        STOPPED,
        PAST_HISTORY,
        SURGICAL_HISTORY,
        ALLERGIES,
    }
)

# (key, pattern). Longer, more specific labels come first so the alternation prefers them.
_LABELS: Final[tuple[tuple[str, str], ...]] = (
    (ADMISSION, r"date\s+of\s+admission\s*:"),
    (DISCHARGE, r"date\s+of\s+discharge\s*:"),
    (DATE, r"date\s+(?:recorded|of\s+visit|of\s+consultation|of\s+study)\s*:"),
    (DATE, r"(?<![\w/])date\s*:"),
    (CONSULTANT, r"(?:consultant|consulting\s+doctor|treating\s+doctor)\s*:"),
    (COMPLAINTS, r"(?:chief|presenting)\s+complaints?\s*:"),
    (SURGICAL_HISTORY, r"(?:past\s+)?surgical\s+history\s*:"),
    (FAMILY_HISTORY, r"family\s+history\s*:"),
    (PAST_HISTORY, r"past\s+(?:medical\s+)?history\s*:"),
    (DISCHARGE_MEDICATIONS, r"(?:discharge\s+medications?|medications?\s+on\s+discharge)\s*:"),
    (CURRENT_MEDICATIONS, r"current\s+medications?\s*:"),
    (
        MEDICATIONS,
        r"(?:prescribed\s+medications?|medications?\s+prescribed|medicines|medications?)\s*:",
    ),
    (MEDICATIONS, r"(?:rx|℞)\s*:"),
    (STOPPED, r"(?:stop|stopped|discontinue|discontinued)\s*:"),
    (DIAGNOSIS, r"(?:final\s+|provisional\s+|discharge\s+|working\s+)?diagnos[ie]s\s*:"),
    (PROCEDURES, r"(?:procedures?(?:\s+(?:done|performed))?|operations?\s+done)\s*:"),
    (ALLERGIES, r"(?:known\s+|drug\s+)?allerg(?:y|ies)(?:\s+status)?\s*:"),
    (FOLLOW_UP, r"follow[\s-]?up\s*:"),
    (STUDY, r"(?:study|examination|scan)\s*:"),
    # stop labels
    (STOP, r"patient(?:'s)?\s+name\s*:"),
    (STOP, r"name\s+of\s+patient\s*:"),
    (STOP, r"age\s*/\s*sex\s*:"),
    (STOP, r"(?:age|sex|gender|uhid|ip\s+no|op\s+no|bill\s+no|mrn)\s*:"),
    (STOP, r"advice(?:\s+on\s+discharge)?\s*:"),
    (STOP, r"course\s+in\s+(?:the\s+)?hospital\s*:"),
    (STOP, r"condition\s+at\s+discharge\s*:"),
    (STOP, r"(?:findings|impression|investigations?(?:\s+advised)?)\s*:"),
    (STOP, r"(?:reported|recorded|referred)\s+by\s*:"),
    (STOP, r"signature\b"),
    (STOP, r"synthetic\s+test\s+document\b"),
    (STOP, r"page\s+\d+\s+of\s+\d+\b"),
)
_LABEL = re.compile(
    "|".join(f"(?P<k{i}>(?<![\\w-]){pattern})" for i, (_, pattern) in enumerate(_LABELS)),
    re.IGNORECASE,
)
_KEYS = {f"k{i}": key for i, (key, _) in enumerate(_LABELS)}
_MAX_UNMARKED: Final = 80
# A standalone "Rx" line opens the medications list (no colon).
_RX_LINE = re.compile(r"\s*(?:rx|℞)\s*", re.IGNORECASE)  # used with fullmatch(pos)
# No "^": Pattern.match(text, pos) anchors at pos, while "^" would not match there.
ITEM_MARKER = re.compile(
    r"\s*(?:\d{1,2}[.)]\s+|[-•*]\s+|(?=(?:tab|cap|syp|inj|tablet|capsule|syrup|injection)(?:\.?\s|\.[a-z])))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Segment:
    """One field value on one line. Offsets index the page text."""

    key: str
    page_number: int
    line_number: int  # 0-based line index in the page text
    label_start: int  # == value_start for a continuation line or an Rx-line list
    value_start: int
    value_end: int
    continuation: bool = False

    def value(self, text: str) -> str:
        return text[self.value_start : self.value_end]


def _labels(text: str, start: int, end: int) -> list[tuple[str, int, int]]:
    return [
        (_KEYS[m.lastgroup], m.start(), m.end())
        for m in _LABEL.finditer(text, start, end)
        if m.lastgroup is not None
    ]


def segment_page(page_number: int, text: str) -> tuple[Segment, ...]:
    """Every labelled field value on the page, in reading order, including list continuations."""
    segments: list[Segment] = []
    open_list: str | None = None
    header_block = False  # the list label stood alone on its line (a heading over item lines)
    for line in line_spans(text):
        if line.start == line.end:
            open_list = None
            continue
        found = _labels(text, line.start, line.end)
        if _RX_LINE.fullmatch(text, line.start, line.end):
            open_list, header_block = MEDICATIONS, True
            continue
        marker = ITEM_MARKER.match(text, line.start, line.end)
        first_label = found[0][1] if found else line.end
        # Docling may drop list bullets, so under a heading an unmarked line is an item too. A
        # history item must look like one: no colon and at most _MAX_UNMARKED characters.
        unmarked_item = (
            header_block
            and marker is None
            and (
                open_list not in (PAST_HISTORY, SURGICAL_HISTORY)
                or (
                    ":" not in text[line.start : first_label]
                    and first_label - line.start <= _MAX_UNMARKED
                )
            )
        )
        if (
            open_list is not None
            and (marker is not None or unmarked_item)
            and (first_label > line.start)
        ):
            stop = first_label
            span = trim(text, line.start, stop)
            if span:
                segments.append(
                    Segment(open_list, page_number, line.number, span[0], span[0], span[1], True)
                )
        elif not found:
            open_list = None
        if not found:
            continue
        for index, (key, label_start, label_end) in enumerate(found):
            next_start = found[index + 1][1] if index + 1 < len(found) else line.end
            if key == STOP:
                open_list = None
                continue
            span = trim(text, label_end, next_start)
            value_start, value_end = span if span else (label_end, label_end)
            segments.append(
                Segment(key, page_number, line.number, label_start, value_start, value_end)
            )
            open_list = key if key in LIST_KEYS else None
            header_block = span is None and index + 1 == len(found)
    return tuple(segments)
