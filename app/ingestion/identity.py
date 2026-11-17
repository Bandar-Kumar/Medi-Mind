"""Patient identity verification over parsed pages (Task 2.8, ADR-036 D4, D5).

Pure and deterministic: no database, no files, no models. It compares the patient name, and the
date of birth when one is printed, with the selected profile, and returns an outcome with fixed
reason codes. The result never contains a name, a date or any page text.

Outcomes (``documents.identity_check``): ``match``, ``mismatch`` or ``unknown``.

Name: patient-specific labels only (``Patient Name:``, ``Patient's Name:`` with a straight or
curly apostrophe, ``Name of Patient:``, ``Pt. Name:``, ``Patient:``). A bare ``Name:`` at the
start of a line is a generic label: it may name a doctor, a guardian or the patient, so it is
never identity evidence. It is ignored when a patient label exists; alone it gives
``name_generic_label`` (``unknown``).
- The printed value runs to the next field (a token with ``:``, a digit or ``/``, a known label
  word such as ``Age`` or ``DOB``), a tab, two spaces, ``|`` or ``;``, or the end of the line.
  Docling often merges printed lines, so a field can be followed by others on the same line.
- Normalized for comparison only (the page text is never changed): Unicode NFKC, case-folded;
  ``.``, ``,``, ``-``, ``_`` and other punctuation separate tokens; apostrophes are dropped
  (``D'Souza`` = ``DSouza``); honorifics (Mr, Mrs, Ms, Miss, Dr, Smt, Shri, Master, Mstr, Prof)
  are removed; token order is ignored (a set). ``Baby`` is **not** removed: ``Baby Priya Sharma``
  is a newborn, not Priya Sharma.
- A full name has at least two tokens and no single-letter token (initial).
- ``match`` only when both the document's and the profile's names are full names and their token
  sets are equal.
- ``mismatch`` only when both are full names and neither token set contains the other.
- Everything else is ``unknown``: no patient label (``name_not_found``), only a generic ``Name:``
  label (``name_generic_label``), an initial in the document name (``name_initials``), a profile
  name that is not a full name or holds an unreadable character (``profile_name_incomplete``), a
  single token, subset or superset (``name_partial``), different names in one document
  (``name_multiple``), an unreadable name field (``name_unreadable``: U+FFFD, or any symbol,
  format (Cf: soft hyphen, ZWJ, ZWNJ), private-use or unassigned character, e.g. the ``■``
  Docling prints for a glyph it cannot map), or an implausibly long value (``name_ambiguous``).

Date of birth (``DOB``, ``D.O.B``, ``Date of Birth``, ``Birth Date``, as whole words), compared
only when both the document and the profile have one; age is never used (D4):
- A label owned by someone else is skipped: preceded by ``Baby``, ``Father's``, ``Mother``,
  ``Guardian`` and the like (``_OTHER_PERSON``), or followed by ``of <person>`` / ``(<person>)``.
- A combined ``Age/DOB`` or ``DOB/Age`` field may hold an age: a date at its start, or right
  after an age (``39 Y / 25-03-1985``), is used; otherwise the field is ignored, never read as a
  date (``Age/DOB: 33 Y``), and a later date on a merged line is never taken from it.
- Formats: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY (day first, ADR-025), DD-Mon-YYYY, YYYY-MM-DD.
- A numeric date whose day and month are both 12 or less and differ is ambiguous
  (``dob_ambiguous``); several different dates conflict (``dob_conflict``); an unparseable value
  after a plain DOB label is ``dob_unreadable``. Each makes the outcome ``unknown`` unless
  something is a clear mismatch.
- A clearly different date of birth is ``dob_mismatch``: a namesake is a different person.

Combination: any clear mismatch (name or date of birth) → ``mismatch``; otherwise any unknown →
``unknown``; otherwise a name match (with the date of birth matching or not comparable) →
``match``. A match always needs the name: a matching date of birth alone stays ``unknown``.

Any change to a pattern, list or rule must bump ``IDENTITY_RULES_VERSION`` (ADR-036).
Version history: ``1`` (first dev measurement); ``2`` (full names on both sides, ``Baby`` kept,
profile initials, DOB word boundaries and other-person labels, Cf unreadable, curly apostrophe,
generic ``Name:`` not evidence).
"""

import datetime as dt
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

IDENTITY_RULES_VERSION: Final = "2"

MATCH: Final = "match"
MISMATCH: Final = "mismatch"
UNKNOWN: Final = "unknown"

NAME_MATCH: Final = "name_match"
NAME_MISMATCH: Final = "name_mismatch"
NAME_NOT_FOUND: Final = "name_not_found"
NAME_GENERIC_LABEL: Final = "name_generic_label"
NAME_INITIALS: Final = "name_initials"
NAME_PARTIAL: Final = "name_partial"
NAME_MULTIPLE: Final = "name_multiple"
NAME_UNREADABLE: Final = "name_unreadable"
NAME_AMBIGUOUS: Final = "name_ambiguous"
PROFILE_NAME_INCOMPLETE: Final = "profile_name_incomplete"
DOB_MATCH: Final = "dob_match"
DOB_MISMATCH: Final = "dob_mismatch"
DOB_NOT_PRINTED: Final = "dob_not_printed"
DOB_NOT_IN_PROFILE: Final = "dob_not_in_profile"
DOB_AMBIGUOUS: Final = "dob_ambiguous"
DOB_CONFLICT: Final = "dob_conflict"
DOB_UNREADABLE: Final = "dob_unreadable"
REASON_CODES: Final = frozenset(
    {
        NAME_MATCH,
        NAME_MISMATCH,
        NAME_NOT_FOUND,
        NAME_GENERIC_LABEL,
        NAME_INITIALS,
        NAME_PARTIAL,
        NAME_MULTIPLE,
        NAME_UNREADABLE,
        NAME_AMBIGUOUS,
        PROFILE_NAME_INCOMPLETE,
        DOB_MATCH,
        DOB_MISMATCH,
        DOB_NOT_PRINTED,
        DOB_NOT_IN_PROFILE,
        DOB_AMBIGUOUS,
        DOB_CONFLICT,
        DOB_UNREADABLE,
    }
)

MAX_NAME_TOKENS: Final = 6
MIN_NAME_TOKENS: Final = 2
_FFFD = chr(0xFFFD)
_BAD = frozenset({"Cf", "Co", "Cn"})  # format (soft hyphen, ZWJ, ZWNJ), private use, unassigned
# Titles only. "Kumari", "Kum" and "Sri" are left out on purpose: they are also common given or
# middle names in India, and dropping them could turn a different person's name into a match.
# "Baby" is left out too (v2): "Baby Priya Sharma" is the newborn, not the mother Priya Sharma.
HONORIFICS: Final = frozenset(
    {"mr", "mrs", "ms", "miss", "dr", "smt", "shri", "master", "mstr", "prof"}
)
# Words that start the next field when Docling merges printed lines into one.
_STOP_WORDS: Final = frozenset(
    {"age", "sex", "gender", "dob", "d.o.b", "date", "uhid", "mrn", "id", "ref", "referred"}
    | {"sample", "report", "bill", "mobile", "phone", "address", "ward", "bed", "male"}
    | {"female", "years", "yrs", "consultant", "doctor", "patient", "reg", "registration"}
)
_WORD_START = r"(?<![^\W\d_])"  # not preceded by a letter
_WORD_END = r"(?![^\W\d_])"  # not followed by a letter
_NAME_LABEL = re.compile(
    rf"(?:{_WORD_START}(?P<patient>patient['’]?s\s+name|name\s+of\s+(?:the\s+)?patient|"
    r"pt\.?\s*name|patient\s+name|patient)|(?P<generic>^\s*name))\s*[:\-]\s*",
    re.IGNORECASE | re.MULTILINE,
)
_DOB_LABEL = re.compile(
    rf"{_WORD_START}(?P<age_before>age\s*/\s*)?"
    r"(?:d\.?\s?o\.?\s?b\.?|date\s+of\s+birth|birth\s+date)"
    rf"{_WORD_END}(?P<age_after>\s*/\s*age{_WORD_END})?\s*[:\-]?\s*",
    re.IGNORECASE,
)
# Whose date of birth a label names, when it is not the patient's (v2).
_OTHER_PERSON = (
    r"baby|infant|newborn|neonate|child|son|daughter|father|mother|parent|guardian|husband|wife"
    r"|spouse|attendant|nominee|relative|donor|sibling|brother|sister"
)
_OWNER_BEFORE = re.compile(rf"{_WORD_START}(?:{_OTHER_PERSON})(?:['’]s)?[\s/.\-]*$", re.IGNORECASE)
_OWNER_AFTER = re.compile(
    rf"^(?:of\s+(?:the\s+)?|\(\s*)(?:{_OTHER_PERSON}){_WORD_END}", re.IGNORECASE
)
_FIELD_END = re.compile(r"\t| {2,}|\||;|$")
_MONTHS: Final = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_NUMERIC_DATE = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_NAMED_DATE = re.compile(r"(\d{1,2})[\s\-/]+([A-Za-z]{3,9})[\s\-/,]+(\d{4})")
# An age at the start of a combined Age/DOB value, e.g. "39 Y / " or "41 Yrs, " (never used, D4).
_AGE_PREFIX = re.compile(
    rf"\s*\d{{1,3}}\s*(?:years?|yrs?|y|months?|mths?|m|days?|d)\.?{_WORD_END}\s*[/,(\-]?\s*",
    re.IGNORECASE,
)


class IdentityPage(Protocol):
    """What identity verification reads from a parsed page."""

    @property
    def page_number(self) -> int: ...

    @property
    def text(self) -> str: ...


@dataclass(frozen=True)
class IdentityResult:
    """The outcome. Fixed codes and a page number only: never a name, a date or page text."""

    outcome: str
    reason_codes: tuple[str, ...]  # sorted
    page_number: int | None  # first page with a patient name label, if any
    rules_version: str


def normalize_name(value: str) -> frozenset[str]:
    """Comparison tokens of a name: NFKC, case-folded, punctuation split, honorifics removed."""
    folded = unicodedata.normalize("NFKC", value).casefold().replace("'", "").replace("’", "")
    spaced = "".join(
        char if char.isalnum() or unicodedata.category(char).startswith("M") else " "
        for char in folded
    )
    return frozenset(token for token in spaced.split() if token not in HONORIFICS)


def _is_full_name(tokens: frozenset[str]) -> bool:
    """At least two tokens and no initial: the only names that can match or mismatch."""
    return len(tokens) >= MIN_NAME_TOKENS and all(len(token) > 1 for token in tokens)


def _check(pages: Sequence[IdentityPage], display_name: object, date_of_birth: object) -> None:
    """Reject malformed input. Messages never contain the name, the date or page text."""
    if isinstance(pages, str | bytes) or not isinstance(pages, Sequence) or not pages:
        raise ValueError("pages must be a non-empty sequence of parsed pages")
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
    if not isinstance(display_name, str) or not normalize_name(display_name):
        raise ValueError("the profile has no usable display name")
    if date_of_birth is not None and (
        isinstance(date_of_birth, dt.datetime) or not isinstance(date_of_birth, dt.date)
    ):
        raise TypeError("date_of_birth must be a date or None")


def _name_values(line: str) -> list[tuple[str, bool]]:
    """``(raw value, generic)`` after each name label on one line; ``generic`` for ``Name:``."""
    values = []
    for label in _NAME_LABEL.finditer(line):
        rest = line[label.end() :]
        rest = rest[: _FIELD_END.search(rest).start()]  # type: ignore[union-attr]
        tokens = []
        for token in rest.split():
            bare = token.strip(".,").casefold()
            if ":" in token or "/" in token or any(c.isdigit() for c in token):
                break
            if bare in _STOP_WORDS:
                break
            tokens.append(token)
        values.append((" ".join(tokens), label["generic"] is not None))
    return values


def _unreadable(value: str) -> bool:
    """U+FFFD, or a symbol / format / private-use / unassigned character."""
    return any(
        char == _FFFD or unicodedata.category(char)[0] == "S" or unicodedata.category(char) in _BAD
        for char in value
    )


def _assess_name(value: str, profile_name: str) -> str:
    if _unreadable(value):
        return NAME_UNREADABLE
    if len(value.split()) > MAX_NAME_TOKENS:
        return NAME_AMBIGUOUS
    tokens = normalize_name(value)
    if any(len(token) == 1 for token in tokens):
        return NAME_INITIALS
    profile = normalize_name(profile_name)
    if _unreadable(profile_name) or not _is_full_name(profile):
        return PROFILE_NAME_INCOMPLETE
    if tokens == profile:  # the profile is a full name, so the document's is too
        return NAME_MATCH
    if not _is_full_name(tokens) or tokens <= profile or profile <= tokens:
        return NAME_PARTIAL
    return NAME_MISMATCH


def _parse_date(value: str) -> tuple[dt.date | None, bool]:
    """``(date, ambiguous)`` from the start of a DOB value; ``(None, False)`` if unreadable."""
    value = value.strip()
    try:
        if found := _ISO_DATE.match(value):
            return dt.date(int(found[1]), int(found[2]), int(found[3])), False
        if found := _NUMERIC_DATE.match(value):
            day, month = int(found[1]), int(found[2])
            ambiguous = day <= 12 and month <= 12 and day != month
            return dt.date(int(found[3]), month, day), ambiguous
        if (found := _NAMED_DATE.match(value)) and found[2].casefold() in _MONTHS:
            return dt.date(int(found[3]), _MONTHS[found[2].casefold()], int(found[1])), False
    except ValueError:  # e.g. 31/02/1990
        return None, False
    return None, False


def _combined_date(value: str) -> tuple[dt.date | None, bool]:
    """The date of a combined ``Age/DOB`` value: at its start, or right after an age.

    Nothing further on is read: Docling merges printed lines, so a later date on the same line
    (``Age/DOB: 38 Y Report Date: 15/06/2024``) belongs to another field.
    """
    if age := _AGE_PREFIX.match(value):
        value = value[age.end() :]
    return _parse_date(value)


def _assess_dob(lines: Sequence[str], profile: dt.date | None) -> str:
    printed: set[dt.date] = set()
    ambiguous = unreadable = False
    for line in lines:
        for label in _DOB_LABEL.finditer(line):
            rest = line[label.end() :]
            if _OWNER_BEFORE.search(line[: label.start()]) or _OWNER_AFTER.match(rest):
                continue  # another person's date of birth: never the patient's
            if label["age_before"] or label["age_after"]:
                date, is_ambiguous = _combined_date(rest)
                if date is None:
                    continue  # an age in an Age/DOB field: never used (D4)
            else:
                date, is_ambiguous = _parse_date(rest)
                if date is None:
                    unreadable = True
                    continue
            printed.add(date)
            ambiguous = ambiguous or is_ambiguous
    if not printed and not unreadable:
        return DOB_NOT_PRINTED
    if profile is None:
        return DOB_NOT_IN_PROFILE
    if unreadable:
        return DOB_UNREADABLE
    if len(printed) > 1:
        return DOB_CONFLICT
    if ambiguous:
        return DOB_AMBIGUOUS
    return DOB_MATCH if printed == {profile} else DOB_MISMATCH


def check_identity(
    pages: Sequence[IdentityPage], display_name: str, date_of_birth: dt.date | None
) -> IdentityResult:
    """Compare the document's printed patient name (and DOB, if printed) with the profile.

    Deterministic; reads the pages and never changes them.

    Raises:
        TypeError, ValueError: malformed pages or profile. Messages never contain the name, the
            date of birth or page text.
    """
    _check(pages, display_name, date_of_birth)
    ordered = sorted(pages, key=lambda page: page.page_number)
    lines = [(page.page_number, line) for page in ordered for line in page.text.splitlines()]

    found = [
        (number, value, generic)
        for number, line in lines
        for value, generic in _name_values(line)
        if value.strip()
    ]
    values = [(number, value) for number, value, generic in found if not generic]
    first_page = values[0][0] if values else None
    if not values:
        name = NAME_GENERIC_LABEL if found else NAME_NOT_FOUND
    elif any(_unreadable(value) for _, value in values):
        name = NAME_UNREADABLE
    elif len({normalize_name(value) for _, value in values}) > 1:
        name = NAME_MULTIPLE
    else:
        name = _assess_name(values[0][1], display_name)
    dob = _assess_dob([line for _, line in lines], date_of_birth)

    if NAME_MISMATCH == name or DOB_MISMATCH == dob:
        outcome = MISMATCH
    elif name == NAME_MATCH and dob in (DOB_MATCH, DOB_NOT_PRINTED, DOB_NOT_IN_PROFILE):
        outcome = MATCH
    else:
        outcome = UNKNOWN
    return IdentityResult(outcome, tuple(sorted({name, dob})), first_page, IDENTITY_RULES_VERSION)
