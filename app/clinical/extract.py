"""Deterministic clinical-fact extraction ``clinical-rules-1`` (docs/PHASE_6_PLAN.md §6).

``extract_clinical_facts(document, stored_texts, clinical_type, reference_date=...)`` is pure: it
reads the verified IR and the stored page texts and returns medication, allergy and event drafts
with exact spans, raw and normalized values, reasons and abstention counts. It never reads the
clock, never writes, never logs content, and never fills in a value that is not printed.

Per page: page-text check → subject guard → segmentation → per-field builders → grounding.

Dates (Phase 3 policy ``dates-1``, reused unchanged): the **document date** is the discharge date
of a discharge summary, otherwise the value of a ``Date:``-style label; two different printed
document dates give ``conflicting_dates`` on every fact dated by the document. A fact dated by the
document has ``date_source = document``; a date printed for the item itself (admission,
discharge, a procedure date, a history year) has ``date_source = stated``. Every date keeps its own
span. An undated document leaves those facts undated (quality note ``no_document_date``).
"""

import datetime as dt
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.clinical import allergies as al
from app.clinical import medications as md
from app.clinical import segments as sg
from app.clinical.classify import (
    DISCHARGE_SUMMARY,
    HISTORY_RECORD,
    IMAGING_REPORT,
    PRESCRIPTION,
)
from app.clinical.lexicon import Lexicon, load_lexicon
from app.clinical.reasons import ClinicalAbstention, ClinicalQualityNote, ClinicalReviewReason
from app.clinical.schemas import (
    NEEDS_REVIEW,
    VALIDATED,
    AllergyDraft,
    ClinicalExtractionOutput,
    DateSpan,
    EventDraft,
    MedicationDraft,
)
from app.extraction.date_policy import is_plausible
from app.extraction.grounding import grounded, span_matches
from app.extraction.lab.context import PAGE_FLAG_REASONS, patient_names
from app.extraction.normalize.dates import DATE_TOKEN, parse_date
from app.extraction.reasons import ReviewReason
from app.extraction.spans import line_spans, trim
from app.ingestion.ir import ParsedDocument

_FACILITY = re.compile(
    r"[A-Z][\w.'&-]*(?:[ \t]+[A-Z][\w.'&-]*)*[ \t]+(?:Hospital|Clinic|Centre|Center|Nursing\s+Home|"
    r"Medical\s+College|Institute)(?:[ \t]+\([^()\n]{1,30}\))?"
)
_CLINICIAN = re.compile(
    r"Dr\.\s+[A-Z][A-Za-z'.-]*(?:[ \t]+[A-Z][A-Za-z'.-]*)*(?:[ \t]+\([^()\n]{1,30}\))?"
    r"(?:,[ \t]*[A-Z]{2,}[A-Za-z]*)*"
)
_YEAR_SUFFIX = re.compile(
    r"(?:\s*\(\s*(?P<y1>(?:19|20)\d{2})\s*\)|\s+(?:since|in)\s+(?P<y2>(?:19|20)\d{2}))\s*$",
    re.IGNORECASE,
)
_DATE_SUFFIX = re.compile(r"\s*\(\s*(?P<d>[^()]{6,20})\s*\)\s*$")
_PROCEDURE_WORD = re.compile(
    r"(?:ectomy|otomy|ostomy|plasty|scopy|surgery|repair|replacement|operation|bypass|"
    r"transplant|graft)\b",
    re.IGNORECASE,
)
_NUMBERED = re.compile(r"(?<!\S)\d{1,2}[.)]\s+(?=[A-Za-z?])")
_STOP_ANYWHERE = re.compile(r"(?<![\w-])(?:recorded|reported)\s+by\s*:", re.IGNORECASE)
_BULLET = re.compile(r"\s*(?:\d{1,2}[.)]|[-•*])\s+")  # match(text, pos): no "^"
ENCOUNTER = "encounter"
HISTORY = "history"


@dataclass
class _Page:
    number: int
    text: str
    reasons: tuple[str, ...]
    segments: tuple[sg.Segment, ...]
    counters: Counter = field(default_factory=Counter)

    def locator(self, line: int) -> str:
        self.counters[line] += 1
        return f"p{self.number}:l{line}:s{self.counters[line]}"


@dataclass
class _Builder:
    clinical_type: str
    reference_date: dt.date
    lexicon: Lexicon
    document_date: DateSpan | None
    document_date_reasons: tuple[str, ...]
    medications: list[MedicationDraft] = field(default_factory=list)
    allergies: list[AllergyDraft] = field(default_factory=list)
    events: list[EventDraft] = field(default_factory=list)
    abstentions: Counter = field(default_factory=Counter)

    # --- shared fields --------------------------------------------------------------------------

    def _doc_date_fields(self) -> tuple[dict, set[str], set[str]]:
        found = self.document_date
        if found is None or found.value is None:
            notes = {ClinicalQualityNote.NO_DOCUMENT_DATE.value}
            return {}, set(self.document_date_reasons), notes
        return (
            {
                "date": found.value,
                "date_precision": found.precision,
                "date_raw": found.raw,
                "date_source": "document",
                "date_page_number": found.page_number,
                "date_char_start": found.char_start,
                "date_char_end": found.char_end,
            },
            set(found.reasons) | set(self.document_date_reasons),
            set(),
        )

    @staticmethod
    def _stated_fields(found: DateSpan) -> tuple[dict, set[str]]:
        if found.value is None:
            return {}, set(found.reasons)
        return (
            {
                "date": found.value,
                "date_precision": found.precision,
                "date_raw": found.raw,
                "date_source": "stated",
                "date_page_number": found.page_number,
                "date_char_start": found.char_start,
                "date_char_end": found.char_end,
            },
            set(found.reasons),
        )

    def _common(
        self,
        page: _Page,
        line: int,
        excerpt: md.Part,
        parts: list[md.Part],
        dated: str,  # "document", "none" or "stated"
        stated: DateSpan | None = None,
        reasons: set[str] | None = None,
        notes: set[str] | None = None,
    ) -> dict | None:
        """The base draft fields, or None (abstention recorded) when grounding fails."""
        checked = [(p.text, p.start, p.end) for p in parts]
        if not grounded(page.text, excerpt.text, excerpt.start, excerpt.end, checked):
            self.abstentions[ClinicalAbstention.SPAN_VERIFICATION_FAILED] += 1
            return None
        all_reasons = set(page.reasons) | (reasons or set())
        all_notes = set(notes or set())
        date_fields: dict = {}
        if dated == "document":
            date_fields, date_reasons, date_notes = self._doc_date_fields()
            all_reasons |= date_reasons
            all_notes |= date_notes
        elif dated == "stated" and stated is not None:
            if not span_matches(page.text, stated.char_start, stated.char_end, stated.raw):
                self.abstentions[ClinicalAbstention.SPAN_VERIFICATION_FAILED] += 1
                return None
            date_fields, date_reasons = self._stated_fields(stated)
            all_reasons |= date_reasons
        ordered = tuple(sorted(all_reasons))
        return {
            "page_number": page.number,
            "source_locator": page.locator(line),
            "source_excerpt": excerpt.text,
            "char_start": excerpt.start,
            "char_end": excerpt.end,
            **date_fields,
            "review_status": NEEDS_REVIEW if ordered else VALIDATED,
            "review_reasons": ordered,
            "quality_notes": tuple(sorted(all_notes)),
        }

    # --- medications ----------------------------------------------------------------------------

    def medication_value(self, page: _Page, segment: sg.Segment, context: str) -> None:
        for start, end in md.item_spans(page.text, segment.value_start, segment.value_end):
            item = md.parse_item(page.text, start, end)
            if item is None:
                self.abstentions[ClinicalAbstention.MEDICATION_ITEM_UNPARSED] += 1
                continue
            self._medication(page, segment.line_number, item, context)

    def _medication(self, page: _Page, line: int, item: md.ParsedItem, context: str) -> None:
        reasons: set[str] = set()
        notes: set[str] = set()
        lookup = self.lexicon.lookup(item.name.text)
        if lookup.ambiguous:
            reasons.add(ClinicalReviewReason.AMBIGUOUS_MEDICATION_NAME.value)
        elif lookup.generic is None:
            reasons.add(ClinicalReviewReason.UNMAPPED_MEDICATION.value)
        strength = md.strength(item.strength.text) if item.strength else None
        if item.strength is None and item.dose is None:
            notes.add(ClinicalQualityNote.STRENGTH_NOT_DOCUMENTED.value)
        per_day, as_needed = md.frequency(item.frequency.text) if item.frequency else (None, None)
        if item.frequency is None and context != "discontinued":
            notes.add(ClinicalQualityNote.FREQUENCY_NOT_DOCUMENTED.value)
        duration = md.duration(item.duration.text) if item.duration else None
        if item.leftover:
            notes.add(ClinicalQualityNote.UNPARSED_TEXT_IN_ITEM.value)
        common = self._common(
            page, line, item.excerpt, list(item.parts), "document", reasons=reasons, notes=notes
        )
        if common is None:
            return
        self.medications.append(
            MedicationDraft(
                **common,
                name_raw=item.name.text,
                name_norm=lookup.generic,
                name_match=lookup.match.value if lookup.match else None,
                dosage_form=md.dosage_form(item.form.text),
                strength_raw=item.strength.text if item.strength else None,
                strength_value=strength[0] if strength else None,
                strength_unit=strength[1] if strength else None,
                dose_raw=item.dose.text if item.dose else None,
                route_raw=item.route.text if item.route else None,
                route_norm=md.route(item.route.text) if item.route else None,
                frequency_raw=item.frequency.text if item.frequency else None,
                frequency_per_day=per_day,
                as_needed=as_needed,
                duration_raw=item.duration.text if item.duration else None,
                duration_value=duration[0] if duration else None,
                duration_unit=duration[1] if duration else None,
                instructions_raw=item.instructions.text if item.instructions else None,
                mention_context=context,
            )
        )

    # --- allergies ------------------------------------------------------------------------------

    def allergy_value(self, page: _Page, segment: sg.Segment) -> None:
        start, end = _strip_bullet(page.text, segment.value_start, segment.value_end)
        value = page.text[start:end]
        if not value.strip() or al.is_unknown(value):
            self.abstentions[ClinicalAbstention.ALLERGY_STATUS_UNKNOWN] += 1
            return
        if al.is_none_known(value):
            span = trim(page.text, start, end)
            assert span is not None
            s, e = span
            while e > s and page.text[e - 1] == ".":
                e -= 1
            part = md.Part(page.text[s:e], s, e)
            common = self._common(page, segment.line_number, part, [part], "document")
            if common is not None:
                common["negated"] = True
                self.allergies.append(
                    AllergyDraft(**common, substance_raw=part.text, allergy_status=al.NONE_KNOWN)
                )
            return
        for item_start, item_end in al.split_items(page.text, start, end):
            item = al.parse_item(page.text, item_start, item_end)
            if item is None:
                self.abstentions[ClinicalAbstention.ALLERGY_ITEM_UNPARSED] += 1
                continue
            self._allergy(page, segment.line_number, item)

    def _allergy(self, page: _Page, line: int, item: al.AllergyItem) -> None:
        parts = [p for p in (item.substance, item.reaction, item.severity, item.status_word) if p]
        common = self._common(page, line, item.excerpt, parts, "document")
        if common is None:
            return
        self.allergies.append(
            AllergyDraft(
                **common,
                substance_raw=item.substance.text,
                substance_norm=al.substance_norm(item.substance.text),
                reaction_raw=item.reaction.text if item.reaction else None,
                severity_raw=item.severity.text if item.severity else None,
                severity_norm=al.severity_norm(item.severity.text if item.severity else None),
                allergy_status=item.status,
            )
        )

    def allergy_record_page(self, page: _Page) -> None:
        """An allergy record's own list: a table, or numbered items outside an allergy field.

        Docling may merge numbered items into a header line (``... Age/Sex: 69 Y / M 1. Latex -
        Reaction: ...``), so a numbered item is found anywhere; it ends at the next numbered item,
        the next label, or the end of its line.
        """
        allergy_fields = [
            (s.value_start, s.value_end) for s in page.segments if s.key == sg.ALLERGIES
        ]
        labels = sorted(s.label_start for s in page.segments if s.label_start != s.value_start)
        labels += [m.start() for m in _STOP_ANYWHERE.finditer(page.text)]
        for line in line_spans(page.text):
            starts = list(_NUMBERED.finditer(page.text, line.start, line.end))
            for index, match in enumerate(starts):
                if any(a <= match.start() < b for a, b in allergy_fields):
                    continue
                stop = starts[index + 1].start() if index + 1 < len(starts) else line.end
                stop = min([stop, *[p for p in labels if match.end() <= p < stop]])
                item_span = _part(page.text, match.end(), stop)
                if item_span is None:
                    continue
                item = al.parse_item(page.text, item_span.start, item_span.end)
                if item is None:
                    self.abstentions[ClinicalAbstention.ALLERGY_ITEM_UNPARSED] += 1
                    continue
                self._allergy(page, line.number, item)
        self._allergy_tables(page)

    def _allergy_tables(self, page: _Page) -> None:
        columns: al.TableColumns | None = None
        for line in line_spans(page.text):
            text = page.text[line.start : line.end]
            if "\t" in text:
                cells = [page.text[s:e] for s, e in al.cell_spans(page.text, line.start, line.end)]
                header = al.table_header(cells)
                if header is not None:
                    columns = header
                    continue
                if columns is not None:
                    item = al.table_item(page.text, line.start, line.end, columns)
                    if item is None:
                        self.abstentions[ClinicalAbstention.ALLERGY_ITEM_UNPARSED] += 1
                    else:
                        self._allergy(page, line.number, item)
                    continue
            columns = None

    # --- events ---------------------------------------------------------------------------------

    def event(
        self,
        page: _Page,
        line: int,
        excerpt: md.Part,
        event_type: str,
        *,
        description: md.Part | None = None,
        dated: str = "document",
        stated: DateSpan | None = None,
        context: str = ENCOUNTER,
        subject: str = "patient",
    ) -> None:
        description = description or excerpt
        parts = [description] + (
            [md.Part(stated.raw, stated.char_start, stated.char_end)]
            if stated is not None and excerpt.start <= stated.char_start < excerpt.end
            else []
        )
        common = self._common(page, line, excerpt, parts, dated, stated)
        if common is None:
            return
        common["subject"] = subject
        self.events.append(
            EventDraft(
                **common,
                event_type=event_type,
                event_context=context,
                description_raw=description.text,
            )
        )

    def list_events(
        self, page: _Page, segment: sg.Segment, event_type: str, separators: str = ";"
    ) -> None:
        for start, end in _split(page.text, segment.value_start, segment.value_end, separators):
            excerpt = md.Part(page.text[start:end], start, end)
            if event_type == "procedure":
                stated = self._suffix_date(page, start, end)
                if stated is not None:
                    desc_end = _DATE_SUFFIX.search(page.text, start, end).start()  # type: ignore[union-attr]
                    description = _part(page.text, start, desc_end)
                    if description is not None:
                        self.event(
                            page,
                            segment.line_number,
                            excerpt,
                            event_type,
                            description=description,
                            dated="stated",
                            stated=stated,
                        )
                        continue
            self.event(page, segment.line_number, excerpt, event_type)

    def history_events(self, page: _Page, segment: sg.Segment, surgical: bool) -> None:
        start, end = _strip_bullet(page.text, segment.value_start, segment.value_end)
        items = _split(page.text, start, end, ";")
        if start != segment.value_start and page.text[segment.value_start] in "-•*":
            # A bulleted list whose bullets Docling merged into one line: "- A (2010) - B (2013)".
            items = [s for a, b in items for s in _split_bullets(page.text, a, b)]
        for item_start, item_end in items:
            excerpt = md.Part(page.text[item_start:item_end], item_start, item_end)
            year = _YEAR_SUFFIX.search(page.text, item_start, item_end)
            description = _part(page.text, item_start, year.start() if year else item_end)
            if description is None:
                self.abstentions[ClinicalAbstention.EMPTY_ITEM] += 1
                continue
            is_procedure = surgical or bool(_PROCEDURE_WORD.search(description.text))
            event_type = "procedure" if is_procedure else "diagnosis_documented"
            if year is None:
                self.event(
                    page,
                    segment.line_number,
                    excerpt,
                    event_type,
                    description=description,
                    dated="none",
                    context=HISTORY,
                )
                continue
            group = "y1" if year["y1"] else "y2"
            stated = _year_span(page, year.start(group), year.end(group), self.reference_date)
            self.event(
                page,
                segment.line_number,
                excerpt,
                event_type,
                description=description,
                dated="stated",
                stated=stated,
                context=HISTORY,
            )

    def family_events(self, page: _Page, segment: sg.Segment) -> None:
        for start, end in _split(page.text, segment.value_start, segment.value_end, ";"):
            excerpt = md.Part(page.text[start:end], start, end)
            self.event(
                page,
                segment.line_number,
                excerpt,
                "diagnosis_documented",
                dated="none",
                context=HISTORY,
                subject="family",
            )

    def _suffix_date(self, page: _Page, start: int, end: int) -> DateSpan | None:
        match = _DATE_SUFFIX.search(page.text, start, end)
        if match is None or not DATE_TOKEN.fullmatch(match["d"]):
            return None
        return _date_span(page, match.start("d"), match.end("d"), self.reference_date)


# --- helpers --------------------------------------------------------------------------------------


def _part(text: str, start: int, end: int) -> md.Part | None:
    span = trim(text, start, end)
    if span is None:
        return None
    s, e = span
    while e > s and text[e - 1] in ".,;":
        e -= 1
    return md.Part(text[s:e], s, e) if e > s else None


def _strip_bullet(text: str, start: int, end: int) -> tuple[int, int]:
    match = _BULLET.match(text, start, end)
    return (match.end(), end) if match else (start, end)


def _split(text: str, start: int, end: int, separators: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    item_start = start
    depth = 0
    for position in range(start, end + 1):
        char = text[position] if position < end else ";"
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif position == end or (char in separators and depth == 0):
            part = _part(text, item_start, position)
            if part is not None:
                spans.append((part.start, part.end))
            item_start = position + 1
    return spans


_MERGED_BULLET = re.compile(r"\s[-•*]\s+")


def _split_bullets(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    position = start
    for match in _MERGED_BULLET.finditer(text, start, end):
        part = _part(text, position, match.start())
        if part is not None:
            spans.append((part.start, part.end))
        position = match.end()
    part = _part(text, position, end)
    if part is not None:
        spans.append((part.start, part.end))
    return spans


def _date_span(page: _Page, start: int, end: int, reference_date: dt.date) -> DateSpan:
    raw = page.text[start:end]
    parsed = parse_date(raw, reference_date)
    return DateSpan(
        page_number=page.number,
        raw=raw,
        char_start=start,
        char_end=end,
        value=parsed.value,
        precision=parsed.precision,
        reasons=tuple(str(r) for r in parsed.reasons),
    )


def _year_span(page: _Page, start: int, end: int, reference_date: dt.date) -> DateSpan:
    raw = page.text[start:end]
    value = dt.date(int(raw), 1, 1)
    reasons = () if is_plausible(value, reference_date) else (ReviewReason.IMPLAUSIBLE_DATE.value,)
    return DateSpan(
        page_number=page.number,
        raw=raw,
        char_start=start,
        char_end=end,
        value=value,
        precision="year",
        reasons=reasons,
    )


def _first_date(page: _Page, segment: sg.Segment, reference_date: dt.date) -> DateSpan | None:
    match = DATE_TOKEN.search(page.text, segment.value_start, segment.value_end)
    if match is None:
        return None
    return _date_span(page, match.start(), match.end(), reference_date)


def _document_date(
    pages: list[_Page], clinical_type: str, reference_date: dt.date
) -> tuple[DateSpan | None, tuple[str, ...]]:
    key = sg.DISCHARGE if clinical_type == DISCHARGE_SUMMARY else sg.DATE
    found = [
        d
        for page in pages
        for segment in page.segments
        if segment.key == key
        for d in [_first_date(page, segment, reference_date)]
        if d is not None
    ]
    if not found:
        return None, ()
    if len({" ".join(d.raw.split()) for d in found}) > 1:
        return None, (ReviewReason.CONFLICTING_DATES.value,)
    return found[0], ()


def _medication_context(key: str, clinical_type: str) -> str:
    if key == sg.STOPPED:
        return "discontinued"
    if key == sg.DISCHARGE_MEDICATIONS:
        return "discharge_list"
    if key == sg.CURRENT_MEDICATIONS:
        return "history"
    if clinical_type == DISCHARGE_SUMMARY:
        return "discharge_list"
    if clinical_type == HISTORY_RECORD:
        return "history"
    return "prescribed"


def _first_line_match(
    page: _Page, pattern: re.Pattern[str], lines: int
) -> tuple[int, md.Part] | None:
    for line in line_spans(page.text)[:lines]:
        match = pattern.search(page.text, line.start, line.end)
        if match:
            return line.number, md.Part(match.group(0), match.start(), match.end())
    return None


# --- entry point --------------------------------------------------------------------------------


def extract_clinical_facts(
    document: ParsedDocument,
    stored_texts: Mapping[int, str],
    clinical_type: str,
    *,
    reference_date: dt.date,
    lexicon: Lexicon | None = None,
) -> ClinicalExtractionOutput:
    """Extract clinical facts from a verified IR whose page texts equal the stored ones."""
    lexicon = lexicon or load_lexicon()
    ir_pages = sorted(document.pages, key=lambda p: p.page_number)
    if len(stored_texts) != len(ir_pages) or any(
        stored_texts.get(p.page_number) != p.text for p in ir_pages
    ):
        return ClinicalExtractionOutput(
            clinical_type=clinical_type,
            abstentions={ClinicalAbstention.PAGE_TEXT_MISMATCH.value: 1},
        )
    abstentions: Counter = Counter()
    pages: list[_Page] = []
    for ir_page in ir_pages:
        if len(patient_names(ir_page.text)) > 1:
            abstentions[ClinicalAbstention.MULTIPLE_SUBJECTS] += 1
            continue
        flags = sorted(
            {
                PAGE_FLAG_REASONS[f.value].value
                for f in ir_page.review_flags
                if f.value in PAGE_FLAG_REASONS
            }
        )
        pages.append(
            _Page(
                ir_page.page_number,
                ir_page.text,
                tuple(flags),
                sg.segment_page(ir_page.page_number, ir_page.text),
            )
        )
    document_date, date_reasons = _document_date(pages, clinical_type, reference_date)
    builder = _Builder(clinical_type, reference_date, lexicon, document_date, date_reasons)
    builder.abstentions.update(abstentions)
    medication_keys = {sg.MEDICATIONS, sg.DISCHARGE_MEDICATIONS, sg.CURRENT_MEDICATIONS, sg.STOPPED}
    for page in pages:
        if page.number == pages[0].number:
            _header_events(builder, page, clinical_type)
        for segment in page.segments:
            key = segment.key
            if segment.value_start == segment.value_end:
                continue
            if key in medication_keys:
                builder.medication_value(page, segment, _medication_context(key, clinical_type))
            elif key == sg.ALLERGIES:
                builder.allergy_value(page, segment)
            elif key == sg.DIAGNOSIS:
                builder.list_events(page, segment, "diagnosis_documented")
            elif key == sg.PROCEDURES:
                builder.list_events(page, segment, "procedure")
            elif key == sg.COMPLAINTS:
                builder.list_events(page, segment, "symptom_documented", ";,")
            elif key in (sg.PAST_HISTORY, sg.SURGICAL_HISTORY):
                builder.history_events(page, segment, surgical=key == sg.SURGICAL_HISTORY)
            elif key == sg.FAMILY_HISTORY:
                builder.family_events(page, segment)
            elif key == sg.FOLLOW_UP:
                part = _part(page.text, segment.value_start, segment.value_end)
                if part is not None:
                    builder.event(page, segment.line_number, part, "follow_up_instruction")
            elif key == sg.STUDY and clinical_type == IMAGING_REPORT:
                part = _part(page.text, segment.value_start, segment.value_end)
                if part is not None:
                    builder.event(page, segment.line_number, part, "imaging")
            elif key == sg.CONSULTANT:
                found = _CLINICIAN.search(page.text, segment.value_start, segment.value_end)
                part = (
                    md.Part(found.group(0), found.start(), found.end())
                    if found
                    else _part(page.text, segment.value_start, segment.value_end)
                )
                if part is not None:
                    builder.event(page, segment.line_number, part, "consultation")
            elif key == sg.ADMISSION and clinical_type == DISCHARGE_SUMMARY:
                _admission(builder, page, segment, pages[0])
            elif key == sg.DISCHARGE and clinical_type == DISCHARGE_SUMMARY:
                _discharge(builder, page, segment)
        if clinical_type == "allergy_record":
            builder.allergy_record_page(page)
    return ClinicalExtractionOutput(
        clinical_type=clinical_type,
        medications=tuple(builder.medications),
        allergies=tuple(builder.allergies),
        events=tuple(builder.events),
        abstentions={str(k): v for k, v in sorted(builder.abstentions.items())},
        document_date=document_date,
    )


def _header_events(builder: _Builder, page: _Page, clinical_type: str) -> None:
    """A prescription's clinician line documents a consultation on the prescription date."""
    if clinical_type != PRESCRIPTION:
        return
    found = _first_line_match(page, _CLINICIAN, 4)
    if found is not None:
        line, part = found
        builder.event(page, line, part, "consultation")


def _admission(builder: _Builder, page: _Page, segment: sg.Segment, first: _Page) -> None:
    stated = _first_date(page, segment, builder.reference_date)
    if stated is None:
        return
    facility = _first_line_match(first, _FACILITY, 3)
    if facility is not None:
        line, part = facility
        builder.event(first, line, part, "admission", dated="stated", stated=stated)
        return
    excerpt = md.Part(
        page.text[segment.label_start : stated.char_end], segment.label_start, stated.char_end
    )
    builder.event(page, segment.line_number, excerpt, "admission", dated="stated", stated=stated)


def _discharge(builder: _Builder, page: _Page, segment: sg.Segment) -> None:
    stated = _first_date(page, segment, builder.reference_date)
    if stated is None:
        return
    excerpt = md.Part(
        page.text[segment.label_start : stated.char_end], segment.label_start, stated.char_end
    )
    builder.event(page, segment.line_number, excerpt, "discharge", dated="stated", stated=stated)
