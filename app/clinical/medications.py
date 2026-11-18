"""Medication items: split, parse and normalize (``clinical-rules-1`` / ``clinical-norm-1``).

An item starts at a dosage-form prefix (``Tab.``, ``Cap.``, ``Syp.``, ``Inj.`` and their long
forms), optionally preceded by an item number. Docling may merge several items into one line, so a
value is split at every item start. Within an item, the parts are found by fixed patterns, each
with its exact span:

- strength ``500 mg``, ``125 mg/5 ml``; a dose ``5 ml`` when it is not a strength;
- route ``IV``, ``IM``, ``SC``, ``PO``, ``orally``, ``topical``;
- frequency ``1-0-1``, ``OD``, ``BD``, ``BID``, ``TDS``, ``TID``, ``QID``, ``HS``, ``SOS``, ``PRN``,
  ``once/twice/thrice daily``;
- duration ``x 5 days``, ``for 2 weeks``, ``x 1 month``;
- instructions ``after food``, ``before food``, ``with milk``, ``empty stomach``.

The name is the text between the form and the first part. Normalization is separate from the raw
values and never invents anything: a missing part stays ``None`` (with a quality note); a duration
is never turned into an end date; a month is never turned into days; a route is never inferred
from the dosage form.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from app.extraction.spans import trim

FORMS: Final = {
    "tab": "tablet",
    "tablet": "tablet",
    "cap": "capsule",
    "capsule": "capsule",
    "syp": "syrup",
    "syrup": "syrup",
    "inj": "injection",
    "injection": "injection",
}
ITEM_START = re.compile(
    r"(?:(?<!\S)\d{1,2}[.)]\s+)?(?P<form>\b(?:tablet|capsule|syrup|injection|tab|cap|syp|inj))"
    r"(?:\.(?=\s*[A-Za-z])|(?=\s))",  # "Tab. X", "Tab X", or "Tab.X" (no space after the dot)
    re.IGNORECASE,
)
_STRENGTH = re.compile(
    r"(?<![\w.])(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mg|mcg|µg|g|gm|iu|units?)"
    r"(?:\s*/\s*\d+(?:\.\d+)?\s*ml)?(?![\w/])",
    re.IGNORECASE,
)
_DOSE = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?\s*(?:ml|tabs?|tablets?|puffs?|drops?)(?![\w/])", re.IGNORECASE
)
_ROUTE = re.compile(
    r"(?<!\w)(?:IV|IM|SC|PO)(?!\w)|(?<!\w)(?:orally|per\s+oral|topical(?:ly)?|inhaled)(?!\w)",
)
_ROUTE_CI = re.compile(r"(?<!\w)(?:orally|per\s+oral|topical(?:ly)?|inhaled)(?!\w)", re.IGNORECASE)
_FREQUENCY_PATTERN = re.compile(r"(?<![\w-])[0-2]\s*-\s*[0-2]\s*-\s*[0-2](?![\w-])")
_FREQUENCY_CODE = re.compile(r"(?<!\w)(?:OD|BD|BID|TDS|TID|QID|QDS|HS|SOS|PRN)(?!\w)")
_FREQUENCY_WORDS = re.compile(
    r"(?<!\w)(?:once|twice|thrice|three\s+times|four\s+times)\s+(?:a\s+)?(?:daily|a\s+day|per\s+day)"
    r"(?!\w)|(?<!\w)as\s+needed(?!\w)",
    re.IGNORECASE,
)
_DURATION = re.compile(
    r"(?<!\w)(?:x|for)\s+(?P<n>\d{1,3})\s*(?P<unit>days?|weeks?|months?)(?!\w)", re.IGNORECASE
)
_INSTRUCTIONS = re.compile(
    r"(?<!\w)(?:after|before|with)\s+(?:food|meals?|milk)(?!\w)|(?<!\w)(?:on\s+an?\s+)?empty\s+"
    r"stomach(?!\w)",
    re.IGNORECASE,
)
_CODE_PER_DAY: Final = {
    "od": 1,
    "bd": 2,
    "bid": 2,
    "tds": 3,
    "tid": 3,
    "qid": 4,
    "qds": 4,
    "hs": 1,
}
_WORD_PER_DAY: Final = {"once": 1, "twice": 2, "thrice": 3, "three": 3, "four": 4}
_ROUTES: Final = {
    "iv": "intravenous",
    "im": "intramuscular",
    "sc": "subcutaneous",
    "po": "oral",
    "orally": "oral",
    "per oral": "oral",
    "topical": "topical",
    "topically": "topical",
    "inhaled": "inhaled",
}
_UNITS: Final = {"gm": "g", "µg": "mcg", "unit": "units"}


@dataclass(frozen=True)
class Part:
    text: str
    start: int  # offsets into the PAGE text
    end: int


@dataclass(frozen=True)
class ParsedItem:
    excerpt: Part  # the item without its number
    form: Part
    name: Part
    strength: Part | None = None
    dose: Part | None = None
    route: Part | None = None
    frequency: Part | None = None
    duration: Part | None = None
    instructions: Part | None = None
    leftover: bool = False  # unparsed words remain between the parts

    @property
    def parts(self) -> tuple[Part, ...]:
        found = (
            self.form,
            self.name,
            self.strength,
            self.dose,
            self.route,
            self.frequency,
            self.duration,
            self.instructions,
        )
        return tuple(p for p in found if p is not None)


def item_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Each medication item's (start, end) inside ``text[start:end]``; whitespace trimmed."""
    starts = [m for m in ITEM_START.finditer(text, start, end)]
    spans: list[tuple[int, int]] = []
    for index, match in enumerate(starts):
        stop = starts[index + 1].start() if index + 1 < len(starts) else end
        span = trim(text, match.start("form"), stop)
        if span:
            item_start, item_end = span
            while item_end > item_start and text[item_end - 1] in ".,;":
                item_end -= 1
            spans.append((item_start, item_end))
    return spans


def _first(pattern: re.Pattern[str], text: str, start: int, end: int, taken: list) -> Part | None:
    for match in pattern.finditer(text, start, end):
        if any(match.start() < t_end and t_start < match.end() for t_start, t_end in taken):
            continue
        taken.append((match.start(), match.end()))
        return Part(match.group(0), match.start(), match.end())
    return None


def parse_item(text: str, start: int, end: int) -> ParsedItem | None:
    """Parse one item ``text[start:end]`` (it begins with its dosage form). None: no name found."""
    form = ITEM_START.match(text, start, end)
    if form is None:
        return None
    form_end = form.end()
    if form_end < end and text[form_end] == ".":
        form_end += 1
    taken: list[tuple[int, int]] = [(start, form_end)]
    strength = _first(_STRENGTH, text, form_end, end, taken)
    dose = _first(_DOSE, text, form_end, end, taken)
    route = _first(_ROUTE, text, form_end, end, taken)
    frequency = (
        _first(_FREQUENCY_PATTERN, text, form_end, end, taken)
        or _first(_FREQUENCY_CODE, text, form_end, end, taken)
        or _first(_FREQUENCY_WORDS, text, form_end, end, taken)
    )
    duration = _first(_DURATION, text, form_end, end, taken)
    instructions = _first(_INSTRUCTIONS, text, form_end, end, taken)
    first_part = min((t_start for t_start, _ in taken[1:]), default=end)
    name_span = trim(text, form_end, first_part)
    if name_span is None:
        return None
    leftover = _has_leftover(text, form_end, end, taken + [name_span])
    return ParsedItem(
        excerpt=Part(text[start:end], start, end),
        form=Part(text[form.start("form") : form_end], form.start("form"), form_end),
        name=Part(text[name_span[0] : name_span[1]], *name_span),
        strength=strength,
        dose=dose,
        route=route,
        frequency=frequency,
        duration=duration,
        instructions=instructions,
        leftover=leftover,
    )


def _has_leftover(text: str, start: int, end: int, taken: list) -> bool:
    covered = sorted(taken)
    position = start
    for t_start, t_end in covered:
        if t_start > position and text[position:t_start].strip(" ,;:-"):
            return True
        position = max(position, t_end)
    return bool(text[position:end].strip(" ,;:.-"))


# --- normalization ----------------------------------------------------------------------------


def dosage_form(raw: str) -> str | None:
    return FORMS.get(raw.rstrip(".").casefold())


def strength(raw: str) -> tuple[Decimal, str] | None:
    match = _STRENGTH.fullmatch(raw.strip())
    if not match:
        return None
    unit = match["unit"].casefold()
    unit = _UNITS.get(unit, unit)
    return Decimal(match["value"]), unit + (raw[match.end("unit") :].replace(" ", "").casefold())


def route(raw: str) -> str | None:
    return _ROUTES.get(" ".join(raw.casefold().split()))


def frequency(raw: str) -> tuple[int | None, bool]:
    """(doses per day, as needed). ``SOS``/``PRN``/``as needed``: (None, True)."""
    key = " ".join(raw.casefold().split())
    if _FREQUENCY_PATTERN.fullmatch(raw.strip()):
        return sum(int(c) for c in key if c.isdigit()), False
    if key in ("sos", "prn", "as needed"):
        return None, True
    if key in _CODE_PER_DAY:
        return _CODE_PER_DAY[key], False
    first = key.split(" ", 1)[0]
    if first in _WORD_PER_DAY:
        return _WORD_PER_DAY[first], False
    return None, False


def duration(raw: str) -> tuple[int, str] | None:
    match = _DURATION.fullmatch(raw.strip())
    if not match:
        return None
    return int(match["n"]), match["unit"].casefold().rstrip("s")


def is_route_text(raw: str) -> bool:
    return bool(_ROUTE.fullmatch(raw) or _ROUTE_CI.fullmatch(raw))
