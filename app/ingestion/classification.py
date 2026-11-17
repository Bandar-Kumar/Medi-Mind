"""Rule-based document classification over parsed pages (Task 2.7, ADR-036, ADR-019).

Pure and deterministic: no database, no files, no models. The input is the parsed pages exactly as
stored (``page_number``, ``text``, ``has_tables``); any object with those three attributes works,
so the stored ``pages`` rows (``PageRecord``) and the IR's ``ParsedPage`` give the same result.

Phase 2 outputs (ADR-019, ADR-036 D1): ``lab_report``, ``other`` or ``unknown``.

How it decides (rules version ``CLASSIFIER_RULES_VERSION``):
1. Each rule is a fixed set of case-insensitive patterns, matched line by line. A rule *fires* when
   any of its patterns occurs on any page. ``lab.units`` needs ``MIN_UNIT_MATCHES`` unit
   occurrences in total (counted, not lines: Docling often merges several printed lines into one
   text line).
2. Lab evidence = the fired lab-rule weights / the sum of all lab-rule weights (a lab report
   shows many cues at once). Other-document evidence = the fired other-rule weights /
   ``OTHER_SCALE``, capped at 1: the other families (prescription, discharge, invoice, letter,
   booking) exclude each other, so one strong family is enough. Both are in [0, 1].
3. Lab evidence is *strong* when a structural lab cue fired (results-table header or reference
   range) and it reaches ``LAB_THRESHOLD``; other evidence is strong at ``OTHER_THRESHOLD``.
   Precedence, first match wins:
   - no text on any page              → ``unknown`` (``doc.no_text``)
   - strong lab, not strong other     → ``lab_report``
   - strong other, not strong lab     → ``other``
   - both strong                      → ``unknown`` (``doc.conflict``): never silently resolved
   - neither strong                   → ``unknown`` (``doc.weak``)
4. The score (``doc_type_confidence``, ADR-036 D3) is a deterministic **rule score**, not a
   probability: the winning side's evidence for ``lab_report`` / ``other``, and 0 for ``unknown``.
   It is rounded to 3 decimals, the precision of the database column.

The result holds rule identifiers only, never matched text. Page text is only read: never
modified, normalized or repaired. U+FFFD (an unreadable character) is reported as the informational
rule ``quality.replacement_character`` and otherwise ignored; it never matches a pattern.

Any change to a pattern, weight or threshold must bump ``CLASSIFIER_RULES_VERSION`` (ADR-036).

History: version 1 was the first, untuned rule set; its dev report is kept in
``evals/reports/doctype_v0/``. Version 2 (after reviewing the version-1 dev errors only) counts unit
occurrences instead of lines and recognises an inline reference range such as
``14.7 gm/dL (13.0 - 17.0)``.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

CLASSIFIER_RULES_VERSION: Final = "2"

LAB_REPORT: Final = "lab_report"
OTHER: Final = "other"
UNKNOWN: Final = "unknown"

LAB_THRESHOLD: Final = 0.40
OTHER_THRESHOLD: Final = 0.50
OTHER_SCALE: Final = 6
MIN_UNIT_MATCHES: Final = 3

NO_TEXT: Final = "doc.no_text"
CONFLICT: Final = "doc.conflict"
WEAK: Final = "doc.weak"
REPLACEMENT_CHARACTER: Final = "quality.replacement_character"

_FFFD = chr(0xFFFD)


class ClassifiablePage(Protocol):
    """What classification reads from a parsed page."""

    @property
    def page_number(self) -> int: ...

    @property
    def text(self) -> str: ...

    @property
    def has_tables(self) -> bool: ...


@dataclass(frozen=True)
class Rule:
    rule_id: str
    side: str  # LAB_REPORT or OTHER
    weight: int
    patterns: tuple[re.Pattern[str], ...]
    structural: bool = False  # a lab-report structure cue (results-table header, reference range)


@dataclass(frozen=True)
class Classification:
    """The outcome. Rule ids and numbers only: never page text."""

    doc_type: str
    score: float  # rule score in [0, 1], 3 decimals; NOT a calibrated probability
    rule_ids: tuple[str, ...]  # fired rules, sorted, plus the deciding doc.* rule if any
    rules_version: str
    lab_evidence: float
    other_evidence: float


def _patterns(*sources: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(source, re.IGNORECASE) for source in sources)


_TEST_HEADER = r"\b(test(\s+name)?|investigations?|parameters?)\b"
_RESULT_HEADER = r"\b(results?|observed\s+values?|values?)\b"

RULES: Final[tuple[Rule, ...]] = (
    # --- lab report -----------------------------------------------------------------------------
    Rule(
        "lab.results_header",
        LAB_REPORT,
        3,
        _patterns(rf"^\s*{_TEST_HEADER}.*{_RESULT_HEADER}"),
        structural=True,
    ),
    Rule(
        "lab.reference_range",
        LAB_REPORT,
        3,
        _patterns(
            r"\breference\s+(range|interval|value)s?\b",
            r"\bbiological\s+reference\b",
            r"\bnormal\s+range\b",
            r"\bref\.?\s+range\b",
            r"\bexpected\s+values?\b",
            # a value, a unit, then the printed range in brackets: "14.7 gm/dL (13.0 - 17.0)"
            r"\d\s*\S*/\S+\s*\(\s*\d+(\.\d+)?\s*-\s*\d+(\.\d+)?\s*\)",
        ),
        structural=True,
    ),
    Rule(
        "lab.units",
        LAB_REPORT,
        2,
        _patterns(
            r"\b(mg|gm|g|ng|pg|µg|ug)/(dl|ml|l)\b",
            r"/\s?cumm\b",
            r"\b(m|µ|u)?iu/m?l\b",
            r"\bmmol/l\b",
            r"\bmeq/l\b",
            r"\bfl\b",
            r"\bmm/hr\b",
        ),
    ),
    Rule(
        "lab.sample",
        LAB_REPORT,
        1,
        _patterns(r"\bsample\s+(id|collected|type)\b", r"\bspecimen\b", r"\bcollected\s+on\b"),
    ),
    Rule(
        "lab.section_heading",
        LAB_REPORT,
        1,
        _patterns(
            r"\bcomplete\s+blood\s+count\b",
            r"\b(lipid|thyroid|diabetes|liver|kidney)\s+(profile|function)",
            r"\bhaematology\b|\bhematology\b",
            r"\bmicrobiology\b|\bbiochemistry\b",
            r"\bculture\b",
        ),
    ),
    Rule(
        "lab.verified_by",
        LAB_REPORT,
        1,
        _patterns(r"\bpathologist\b", r"\bmethod\s*:", r"\breported\s+on\b"),
    ),
    Rule("lab.letterhead", LAB_REPORT, 1, _patterns(r"\b(diagnostics?|patholog\w*|labs?)\b")),
    Rule("lab.has_tables", LAB_REPORT, 1, ()),  # fires when any page has a structured table
    # --- other documents ------------------------------------------------------------------------
    Rule(
        "other.prescription",
        OTHER,
        3,
        _patterns(
            r"^\s*(rx|℞)\s*$",
            r"\bprescription\b",
            r"^\s*medicines?\s*:",
            r"\b(tab|cap|syp|inj)\.\s+\w+",
            r"\b[0-2]-[0-2]-[0-2]\b",
        ),
    ),
    Rule(
        "other.discharge",
        OTHER,
        3,
        _patterns(
            r"\bdischarge\s+summary\b",
            r"\bdate\s+of\s+(admission|discharge)\b",
            r"\bcourse\s+in\s+(the\s+)?hospital\b",
            r"\bcondition\s+at\s+discharge\b",
        ),
    ),
    Rule(
        "other.diagnosis",
        OTHER,
        1,
        _patterns(r"\b(final|provisional)?\s*diagnosis\s*:", r"\bchief\s+complaints?\b"),
    ),
    Rule(
        "other.invoice",
        OTHER,
        3,
        _patterns(
            r"\b(tax\s+)?invoice\b",
            r"\bbill\s+(no|number)\b",
            r"\btotal\s+amount\b",
            r"\bamount\s*\(rs\.?\)",
            r"\bgstin\b",
            r"\bpayment\s+mode\b",
        ),
    ),
    Rule(
        "other.letter",
        OTHER,
        3,
        _patterns(
            r"^\s*dear\b",
            r"\bto\s+whom\s+it\s+may\s+concern\b",
            r"\byours\s+(sincerely|faithfully|truly)\b",
            r"\bthis\s+is\s+to\s+certify\b",
        ),
    ),
    Rule(
        "other.booking",
        OTHER,
        3,
        _patterns(
            r"\bappointment\b",
            r"\backnowledg(e)?ment\b",
            r"\btests?\s+(booked|scheduled|advised)\b",
            r"\breports?\s+will\s+be\s+available\b",
        ),
    ),
)

_LAB_TOTAL = sum(rule.weight for rule in RULES if rule.side == LAB_REPORT)


def _check(pages: Sequence[ClassifiablePage]) -> None:
    """Reject malformed input. Messages name page numbers or positions, never text."""
    if isinstance(pages, str | bytes) or not isinstance(pages, Sequence):
        raise TypeError("pages must be a sequence of parsed pages")
    if not pages:
        raise ValueError("a document has at least one page")
    seen: set[int] = set()
    for position, page in enumerate(pages):
        number = getattr(page, "page_number", None)
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError(f"page at position {position} has no valid page number")
        if number in seen:
            raise ValueError(f"page {number} appears more than once")
        seen.add(number)
        if not isinstance(getattr(page, "text", None), str):
            raise TypeError(f"page {number} has no text string")
        if not isinstance(getattr(page, "has_tables", None), bool):
            raise TypeError(f"page {number} has no has_tables flag")


def _fired(rule: Rule, lines: Sequence[str], has_tables: bool) -> bool:
    if rule.rule_id == "lab.has_tables":
        return has_tables
    if rule.rule_id == "lab.units":
        hits = sum(len(p.findall(line)) for line in lines for p in rule.patterns)
        return hits >= MIN_UNIT_MATCHES
    return any(pattern.search(line) for line in lines for pattern in rule.patterns)


def classify_pages(pages: Sequence[ClassifiablePage]) -> Classification:
    """Classify one document from its parsed pages. Deterministic; reads, never changes, the text.

    Raises:
        TypeError, ValueError: malformed pages (no pages, a duplicate or invalid page number, a
            missing text string or ``has_tables`` flag). Messages never contain page text.
    """
    _check(pages)
    ordered = sorted(pages, key=lambda page: page.page_number)
    lines = [line for page in ordered for line in page.text.splitlines() if line.strip()]
    flags = (REPLACEMENT_CHARACTER,) if any(_FFFD in page.text for page in ordered) else ()
    if not lines:
        return Classification(
            UNKNOWN, 0.0, tuple(sorted((NO_TEXT, *flags))), CLASSIFIER_RULES_VERSION, 0.0, 0.0
        )
    has_tables = any(page.has_tables for page in ordered)
    fired = [rule for rule in RULES if _fired(rule, lines, has_tables)]
    lab = round(sum(r.weight for r in fired if r.side == LAB_REPORT) / _LAB_TOTAL, 3)
    other = round(min(1.0, sum(r.weight for r in fired if r.side == OTHER) / OTHER_SCALE), 3)
    lab_strong = any(rule.structural for rule in fired) and lab >= LAB_THRESHOLD
    other_strong = other >= OTHER_THRESHOLD
    if lab_strong and not other_strong:
        doc_type, score, decided = LAB_REPORT, lab, ()
    elif other_strong and not lab_strong:
        doc_type, score, decided = OTHER, other, ()
    elif lab_strong:
        doc_type, score, decided = UNKNOWN, 0.0, (CONFLICT,)
    else:
        doc_type, score, decided = UNKNOWN, 0.0, (WEAK,)
    rule_ids = tuple(sorted({rule.rule_id for rule in fired} | set(decided) | set(flags)))
    return Classification(doc_type, score, rule_ids, CLASSIFIER_RULES_VERSION, lab, other)
