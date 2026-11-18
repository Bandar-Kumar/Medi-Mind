"""Page context for lab extraction (``lab-rules-1``; docs/PHASE_3_PLAN.md §4.3).

- **Subject guard.** A page that labels more than one distinct patient name yields no facts
  (``multiple_subjects``). This is MediMind's own extraction rule, deliberately separate from the
  frozen identity rules (ADR-036), which are never edited or imported for this purpose.
- **Labelled dates.** Collected / Collection Date / Sample Collected On give the collection date;
  Report Date / Reported On give the report date; Registered and Received are ignored. Each date
  keeps its exact span; a time printed right after a date is kept with its own text.
- **Specimen.** ``Specimen:`` / ``Sample Type:`` labels.
- **Page flags.** IR review flags that make every fact from the page ``needs_review`` (D-5).
"""

import re
from dataclasses import dataclass, field

from app.extraction.normalize.dates import DATE_TOKEN, TIME_TOKEN, parse_date, parse_time
from app.extraction.normalize.numbers import text_key
from app.extraction.reasons import ReviewReason
from app.extraction.schemas import DateFinding
from app.extraction.spans import trim
from app.ingestion.ir import ParsedPage
from app.ingestion.ir import ReviewReason as PageFlag

COLLECTION = "collection"
REPORT = "report"

_PATIENT_LABEL = re.compile(
    r"(?im)(?:^|[\t ])(?:patient(?:'|’)?s?\s+name|name\s+of\s+(?:the\s+)?patient|pt\.?\s*name)"
    r"\s*[:\-]\s*"
)
_NAME_STOP = re.compile(
    r"\t|\s{2,}|\s(?=(?:age|sex|gender|dob|d\.o\.b|uhid|ref|report|sample|reg|lab)\b)",
    re.IGNORECASE,
)
_DATE_LABEL = re.compile(
    r"(?i)\b(?P<label>sample\s+collected\s+on|sample\s+collection\s+date|collected\s+on"
    r"|collection\s+date(?:\s*(?:/|&|and)\s*time)?|collected|report\s+date|reported\s+on"
    r"|reporting\s+date|date\s+of\s+report)\s*[:\-]?\s*"
)
_SPECIMEN_LABEL = re.compile(r"(?i)\b(?:specimen|sample\s+type)\s*[:\-]\s*")

PAGE_FLAG_REASONS: dict[str, ReviewReason] = {
    PageFlag.OCR_TEXT_UNVERIFIED.value: ReviewReason.OCR_UNVERIFIED_SOURCE,
    PageFlag.REPLACEMENT_CHARACTER.value: ReviewReason.REPLACEMENT_CHARACTER_IN_SOURCE,
    PageFlag.DOCLING_NORMALIZED_TEXT.value: ReviewReason.SOURCE_TEXT_NORMALIZED,
    PageFlag.TABLE_STRUCTURE_INVALID.value: ReviewReason.TABLE_STRUCTURE_INVALID_SOURCE,
    PageFlag.INVISIBLE_TEXT_LAYER.value: ReviewReason.INVISIBLE_TEXT_LAYER_SOURCE,
}


@dataclass(frozen=True)
class Specimen:
    text: str = field(repr=False)
    start: int
    end: int


@dataclass(frozen=True)
class PageContext:
    page_number: int
    multiple_subjects: bool
    dates: tuple[DateFinding, ...]
    specimen: Specimen | None
    flag_reasons: tuple[ReviewReason, ...]


def _role(label: str) -> str:
    return COLLECTION if "collect" in label.lower() else REPORT


def patient_names(text: str) -> set[str]:
    """Distinct labelled patient names on a page (lookup keys only; never stored or logged)."""
    names: set[str] = set()
    for match in _PATIENT_LABEL.finditer(text):
        rest = text[match.end() :].split("\n", 1)[0]
        stop = _NAME_STOP.search(rest)
        name = text_key(rest[: stop.start()] if stop else rest).strip(" .,")
        if name:
            names.add(name)
    return names


def find_dates(text: str, page_number: int, reference_date) -> tuple[DateFinding, ...]:
    findings: list[DateFinding] = []
    for label in _DATE_LABEL.finditer(text):
        date_match = DATE_TOKEN.match(text, label.end())
        if not date_match:
            continue
        parsed = parse_date(date_match.group(0), reference_date)
        time_raw = time_value = None
        after = re.compile(r"[ ,]*(?:at\s+)?").match(text, date_match.end())
        time_match = TIME_TOKEN.match(text, after.end() if after else date_match.end())
        if time_match:
            time_raw = time_match.group(0)
            time_value = parse_time(time_raw)
        findings.append(
            DateFinding(
                role=_role(label["label"]),
                page_number=page_number,
                raw=date_match.group(0),
                char_start=date_match.start(),
                char_end=date_match.end(),
                value=parsed.value,
                precision=parsed.precision,
                reasons=tuple(str(r) for r in parsed.reasons),
                time_raw=time_raw,
                time_value=time_value,
            )
        )
    return tuple(findings)


def find_specimen(text: str) -> Specimen | None:
    found: list[Specimen] = []
    for match in _SPECIMEN_LABEL.finditer(text):
        line_end = text.find("\n", match.end())
        end = len(text) if line_end < 0 else line_end
        stop = re.compile(r"\t|\s{2,}").search(text, match.end(), end)
        span = trim(text, match.end(), stop.start() if stop else end)
        if span:
            found.append(Specimen(text[span[0] : span[1]], span[0], span[1]))
    distinct = {text_key(s.text) for s in found}
    return found[0] if len(distinct) == 1 else None


def page_context(page: ParsedPage, text: str, reference_date) -> PageContext:
    flags = tuple(
        sorted(
            {PAGE_FLAG_REASONS[f.value] for f in page.review_flags if f.value in PAGE_FLAG_REASONS}
        )
    )
    return PageContext(
        page_number=page.page_number,
        multiple_subjects=len(patient_names(text)) > 1,
        dates=find_dates(text, page.page_number, reference_date),
        specimen=find_specimen(text),
        flag_reasons=flags,
    )
