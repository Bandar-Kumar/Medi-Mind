"""Records questions over clinical facts ``clinical-qa-1`` (docs/PHASE_6_PLAN.md §8). Pure.

**Router** (deterministic; no model): a question is *claimed* only when it names a supported
clinical-records topic; anything else is left to the unchanged Phase 5 lab copilot. A claimed
question about an unsupported records topic (vaccinations, blood group ...) abstains.

**Plain-lookup allow-list** (acceptance fix; replaces the earlier documentary-word rule, which the
acceptance audit showed let interaction, risk, condition, curability and causation questions
through). A claimed clinical question is answered only when the WHOLE normalized question is one
of the ``_PLAIN_LOOKUPS`` forms and the router agrees on the intent (``plain_lookup``). This holds
whether or not the frozen Phase 5 input gate (``app.answering.guardrails.input_gate``) refused it:
a gate refusal of ``advice`` or ``diagnosis`` is lifted only for these exact forms; injection,
invalid and general-knowledge outcomes are never lifted (the dispatcher hands them to Phase 5). A
claimed question that is not a plain lookup gets the fixed ``CLINICAL_REFUSAL`` (if uncertain,
refuse). Questions the router does not claim are left to Phase 5 unchanged (a test runs all 926
frozen ``copilot_v0``/``copilot_v1`` questions through the dispatcher).

**Identity and citations** (acceptance fixes): every citation carries its document's identity
outcome; an item whose source identity is not confirmed ends with "[Identity not confirmed]".
``citation_issues`` checks each citation against the stored page text at answer time; any issue
withholds the whole answer (never repaired).

**Answers** are extractive: each item is one stored fact (or document) with its citation
(document, page, exact excerpt). Sections are labelled ``documented`` (stated facts),
``derived`` (MediMind's ordering, filtering or comparison of documented facts), ``uncertain``
(needs review: shown apart, never mixed in), ``conflicting`` and ``unavailable`` ("nothing is
documented" is never stated as "none exists"). No sentence diagnoses, advises or interprets.
"""

import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

from app.clinical.compare import (
    ClinicalFact,
    compare_documents,
    detect_conflicts,
    medication_key,
)
from app.clinical.lexicon import Lexicon, load_lexicon, name_key
from app.clinical.versions import CLINICAL_QA_VERSION

MEDICATIONS, PRESCRIPTIONS, ADMISSIONS, ALLERGIES, DIAGNOSES = (
    "medications",
    "prescriptions",
    "admissions",
    "allergies",
    "diagnoses",
)
MENTIONS, CHANGES, CONFLICTS, HISTORY, ABSTAIN = (
    "medication_mentions",
    "changes",
    "medication_conflicts",
    "history_summary",
    "abstain",
)
DOCUMENTED, DERIVED, UNCERTAIN, CONFLICTING, UNAVAILABLE = (
    "documented",
    "derived",
    "uncertain",
    "conflicting",
    "unavailable",
)
ANSWERED, NO_RECORDS, ABSTAINED = "answered", "no_records", "abstained"


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_UNSUPPORTED = _p(
    r"\b(?:vaccin\w*|immuni[sz]\w*|blood\s+group|blood\s+type|weight|height|bmi|"
    r"blood\s+pressure\s+readings?|insurance)\b"
)
_CONFLICT = _p(r"\bconflict\w*\b|\bcontradict\w*\b|\binconsisten\w*\b|\bdisagree\w*\b")
_MEDICATION = _p(r"\b(?:medic(?:ine|ines|ation|ations)|drugs?|tablets?|capsules?)\b")
_CHANGES = _p(r"\bwhat\s+(?:has\s+|have\s+)?changed\b|\bdifferences?\s+between\b|\bcompare\b")
_PRESCRIPTION = _p(r"\bprescriptions?\b")
_MENTIONS = _p(
    r"\b(?:which|what)\s+(?:records?|documents?|reports?|prescriptions?)\s+"
    r"(?:mention|mentions|mentioned|list|lists|include|includes)\s+(?P<name>[\w\- ]{2,40}?)\s*\??$"
)
_ADMISSIONS = _p(r"\b(?:hospital\s+)?admissions?\b|\bhospitali[sz]ations?\b|\badmitted\b")
_ALLERGIES = _p(r"\ballerg(?:y|ies|ic)\b")
_DIAGNOSES = _p(r"\bdiagnos[ie]s\b|\bdocumented\s+conditions?\b")
_HISTORY = _p(
    r"\b(?:medical|health|clinical)\s+history\b|\bchronolog\w*\b|\btimeline\b|"
    r"\bsummar(?:y|ise|ize)\s+(?:of\s+)?my\s+(?:documented\s+)?(?:records|history)\b"
)
_DOCUMENTARY = _p(
    r"\b(?:documented|recorded|listed|mentioned|noted|written|on\s+record|"
    r"in\s+(?:my|the|these|his|her)\s+(?:records?|documents?|reports?|files?|prescriptions?))\b"
)
_YEAR = _p(r"\b(?P<y>(?:19|20)\d{2})\b")
_LAST_MONTHS = _p(r"\b(?:last|past|previous)\s+(?P<n>\d{1,2}|six|three|twelve)\s+months?\b")
_LAST_YEAR = _p(r"\b(?:last|past)\s+year\b")
_FIRST_LATEST = _p(r"\bfirst\s+and\s+(?:latest|last|most\s+recent)\b")
_WORD_NUMBERS: Final = {"three": 3, "six": 6, "twelve": 12}


@dataclass(frozen=True)
class RoutePlan:
    intent: str | None  # None: not a clinical-records question (left to the lab copilot)
    year: int | None = None
    months: int | None = None
    medication: str | None = None
    first_and_latest: bool = False
    version: str = CLINICAL_QA_VERSION

    @property
    def claimed(self) -> bool:
        return self.intent is not None


def route(question: str) -> RoutePlan:
    """The clinical intent of a question, or an unclaimed plan."""
    text = " ".join(question.split())
    if _UNSUPPORTED.search(text) and (_DOCUMENTARY.search(text) or _MEDICATION.search(text)):
        return RoutePlan(ABSTAIN)
    if _CONFLICT.search(text) and (_MEDICATION.search(text) or _PRESCRIPTION.search(text)):
        return RoutePlan(CONFLICTS)
    if _CHANGES.search(text) and _PRESCRIPTION.search(text):
        return RoutePlan(CHANGES, first_and_latest=bool(_FIRST_LATEST.search(text)))
    mention = _MENTIONS.search(text)
    if mention:
        return RoutePlan(MENTIONS, medication=mention["name"].strip())
    if _PRESCRIPTION.search(text):
        year = _YEAR.search(text)
        months = _LAST_MONTHS.search(text)
        if months:
            n = months["n"]
            return RoutePlan(PRESCRIPTIONS, months=int(n) if n.isdigit() else _WORD_NUMBERS[n])
        if _LAST_YEAR.search(text):
            return RoutePlan(PRESCRIPTIONS, months=12)
        if year:
            return RoutePlan(PRESCRIPTIONS, year=int(year["y"]))
        return RoutePlan(PRESCRIPTIONS)
    if _ADMISSIONS.search(text):
        return RoutePlan(ADMISSIONS)
    if _ALLERGIES.search(text):
        return RoutePlan(ALLERGIES)
    if _DIAGNOSES.search(text):
        return RoutePlan(DIAGNOSES)
    if _HISTORY.search(text):
        return RoutePlan(HISTORY)
    if _MEDICATION.search(text):
        return RoutePlan(MEDICATIONS)
    return RoutePlan(None)


# --- plain-lookup allow-list (Phase 6 acceptance fix; ADR-042 amendment) ------------------------
#
# A clinical question is answered only when the WHOLE normalized question is one of these plain
# record-lookup forms. There is no "contains a documentary word" rule and no blocking-word list to
# keep complete: a trailing clause ("... that interact", "... for my diabetes", "... and which to
# stop"), a negation ("are not documented"), a condition, a symptom or any other extra word makes
# the full match fail, and the question is refused (or left to its Phase 5 refusal).
_REC = r"(?: (?:in|from) (?:my|the) (?:medical )?(?:records?|documents?|reports?|files?))?"
_VERB = r"(?:documented|recorded|listed|mentioned|noted)"
_BE = r"(?:are|were|have been)"
_MEDS = r"(?:medications?|medicines?|drugs|tablets)"
_LIST = r"(?:list|show)(?: me)?(?: (?:the|all|all the|my|all my))?"
_PERIOD = (
    r"(?: (?:in (?:19|20)\d{2}|(?:during|in) the (?:last|past) (?:six|three|twelve|\d{1,2}) months"
    r"|(?:during|in) the (?:last|past) year))?"
)
_PLAIN_LOOKUPS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (intent, re.compile(pattern))
    for intent, pattern in (
        (MEDICATIONS, rf"(?:what|which) {_MEDS} {_BE} {_VERB}{_REC}"),
        (MEDICATIONS, rf"{_LIST} {_MEDS}(?: (?:that (?:are|were) )?{_VERB})?{_REC}"),
        (
            ALLERGIES,
            rf"(?:what|which) allerg(?:y|ies) (?:is|are|was|were|has been|have been) {_VERB}"
            rf"(?: for me)?{_REC}",
        ),
        (ALLERGIES, rf"{_LIST} allergies(?: (?:that (?:are|were) )?{_VERB})?{_REC}"),
        (ADMISSIONS, rf"(?:what|which) (?:hospital )?admissions {_BE} {_VERB}{_REC}"),
        (ADMISSIONS, rf"{_LIST} (?:hospital )?admissions{_REC}"),
        (DIAGNOSES, rf"(?:what|which) diagnoses {_BE} {_VERB}{_REC}"),
        (DIAGNOSES, rf"{_LIST} diagnoses(?: (?:that (?:are|were) )?{_VERB})?{_REC}"),
        # one literal name only: "which records mention <name>"
        (
            MENTIONS,
            r"(?:which|what) (?:records|documents|reports|prescriptions) (?:mention|list) "
            r"[a-z][a-z0-9-]{1,30}",
        ),
        (
            PRESCRIPTIONS,
            rf"(?:which|what) prescriptions {_BE} (?:recorded|documented|issued|dated)"
            rf"{_PERIOD}{_REC}",
        ),
        (PRESCRIPTIONS, rf"{_LIST} prescriptions{_PERIOD}{_REC}"),
        (
            CHANGES,
            r"what (?:has |have )?changed between my (?:first and (?:latest|last|most recent)|"
            r"last two|latest two|two most recent) prescriptions",
        ),
        (CONFLICTS, rf"(?:are|is) there (?:any )?conflicting medication records{_REC}"),
        (
            HISTORY,
            r"summari[sz]e my (?:documented )?(?:medical|health|clinical) history"
            r"(?: chronologically| in date order)?",
        ),
        (ABSTAIN, rf"(?:what|which) (?:vaccinations?|immuni[sz]ations?) {_BE} {_VERB}{_REC}"),
        (ABSTAIN, rf"what is my blood (?:group|type){_REC}"),
    )
)
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def normalize_question(question: str) -> str:
    """Case-folded, single-spaced, one trailing ``?``/``.``/``!`` run removed. Nothing else."""
    text = " ".join(question.translate(_QUOTES).casefold().split())
    return re.sub(r"\s*[?.!]+$", "", text)


def plain_lookup(question: str) -> str | None:
    """The intent when the whole question is a plain record lookup on the allow-list, else None.

    The router must agree (``route(question).intent``); otherwise None. Anything uncertain is None.
    """
    if not isinstance(question, str):
        return None
    text = normalize_question(question)
    found = {intent for intent, pattern in _PLAIN_LOOKUPS if pattern.fullmatch(text)}
    if len(found) != 1:
        return None
    (intent,) = found
    return intent if route(question).intent == intent else None


def records_lookup_exempt(question: str) -> bool:
    """A question that is a plain record lookup (kept for callers; see ``plain_lookup``)."""
    return plain_lookup(question) is not None


REFUSED: Final = "refused"
CLINICAL_REFUSAL: Final = (
    'MediMind can answer plain questions about what your records state, for example "What '
    'medications are documented in my records?". It cannot say whether medicines interact, are '
    "risky, cause side effects, suit a condition or should be started, stopped or changed, and it "
    "cannot connect symptoms or diagnoses or say what a diagnosis means. Please ask your doctor."
)


def refusal_answer(intent: str | None) -> "ClinicalAnswer":
    """The fixed answer for a clinical question that is not a plain record lookup."""
    return ClinicalAnswer(REFUSED, intent or ABSTAIN, CLINICAL_REFUSAL)


# --- answers ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    fact_id: str | None
    document_id: str
    document_name: str = field(repr=False)
    page_number: int
    excerpt: str = field(repr=False)
    # Acceptance fixes: the source document's identity outcome and the excerpt's exact offsets.
    identity_confirmed: bool = False
    char_start: int | None = None
    char_end: int | None = None


@dataclass(frozen=True)
class AnswerItem:
    text: str = field(repr=False)  # stored values and fixed words only
    citations: tuple[Citation, ...]
    fact_ids: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    date: dt.date | None = None


@dataclass(frozen=True)
class AnswerSection:
    label: str  # documented / derived / uncertain / conflicting / unavailable
    title: str
    items: tuple[AnswerItem, ...] = ()


@dataclass(frozen=True)
class ClinicalAnswer:
    status: str  # answered / no_records / abstained / refused / withheld
    intent: str
    summary: str
    sections: tuple[AnswerSection, ...] = ()
    version: str = CLINICAL_QA_VERSION

    def section(self, label: str) -> tuple[AnswerItem, ...]:
        return tuple(i for s in self.sections if s.label == label for i in s.items)

    def fact_ids(self, label: str) -> set[str]:
        return {f for i in self.section(label) for f in i.fact_ids}

    def document_ids(self, label: str) -> set[str]:
        return {d for i in self.section(label) for d in i.document_ids}


IDENTITY_NOT_CONFIRMED: Final = "Identity not confirmed"


def _cite(fact: ClinicalFact) -> Citation:
    return Citation(
        fact.id,
        fact.document_id,
        fact.document_name,
        fact.page_number,
        fact.source_excerpt,
        identity_confirmed=fact.identity_confirmed,
        char_start=fact.char_start,
        char_end=fact.char_end,
    )


def _flag(text: str, facts: Sequence[ClinicalFact]) -> str:
    """Append the identity warning when any source document's identity is not confirmed."""
    if any(not f.identity_confirmed for f in facts):
        return f"{text} [{IDENTITY_NOT_CONFIRMED}]"
    return text


def _when(fact: ClinicalFact) -> str:
    if fact.date is None:
        return "date not documented"
    if fact.date_precision == "year":
        return str(fact.date.year)
    if fact.date_precision == "month":
        return fact.date.strftime("%m/%Y")
    return fact.date.strftime("%d/%m/%Y")


def _fact_item(fact: ClinicalFact, label: str | None = None) -> AnswerItem:
    text = f"“{fact.source_excerpt}” — {label + ', ' if label else ''}{_when(fact)}"
    return AnswerItem(
        _flag(text, (fact,)), (_cite(fact),), (fact.id,), (fact.document_id,), fact.date
    )


def _sorted(facts: Sequence[ClinicalFact]) -> list[ClinicalFact]:
    return sorted(facts, key=lambda f: (f.date is None, f.date or dt.date.min, f.document_id, f.id))


def _uncertain_section(facts: Sequence[ClinicalFact]) -> tuple[AnswerSection, ...]:
    if not facts:
        return ()
    items = tuple(_fact_item(f, "needs review") for f in _sorted(facts))
    return (AnswerSection(UNCERTAIN, "Not yet verified (shown separately; may be misread)", items),)


def _simple(
    intent: str,
    facts: Sequence[ClinicalFact],
    predicate,
    noun: str,
    *,
    context_label=None,
) -> ClinicalAnswer:
    usable = _sorted([f for f in facts if f.usable and predicate(f)])
    uncertain = [f for f in facts if f.uncertain and predicate(f)]
    sections: list[AnswerSection] = []
    if usable:
        items = tuple(_fact_item(f, context_label(f) if context_label else None) for f in usable)
        sections.append(AnswerSection(DOCUMENTED, f"Documented {noun}", items))
    else:
        sections.append(
            AnswerSection(
                UNAVAILABLE,
                f"No {noun} are documented in the verified or validated records. This does not "
                "mean there are none.",
            )
        )
    sections.extend(_uncertain_section(uncertain))
    status = ANSWERED if usable or uncertain else NO_RECORDS
    summary = f"{len(usable)} documented {noun} found in the records, oldest first."
    return ClinicalAnswer(status, intent, summary, tuple(sections))


def _medication_label(fact: ClinicalFact) -> str:
    context = str(fact.values.get("mention_context", "")).replace("_", " ")
    return {"discharge list": "discharge medication", "history": "listed in history"}.get(
        context, context
    )


def _document_dates(facts: Sequence[ClinicalFact]) -> dict[str, tuple[dt.date | None, bool]]:
    """Per document: (its document date, from a usable fact)."""
    out: dict[str, tuple[dt.date | None, bool]] = {}
    for fact in facts:
        if fact.date_source != "document" or fact.date is None:
            continue
        current = out.get(fact.document_id)
        if current is None or (fact.usable and not current[1]):
            out[fact.document_id] = (fact.date, fact.usable)
    return out


def _prescriptions(
    plan: RoutePlan, facts: Sequence[ClinicalFact], as_of: dt.date
) -> ClinicalAnswer:
    dates = _document_dates([f for f in facts if f.clinical_doc_type == "prescription"])
    if plan.months is not None:
        start = _months_before(as_of, plan.months)
        in_period = lambda d: start <= d <= as_of  # noqa: E731
        period = f"from {start:%d/%m/%Y} to {as_of:%d/%m/%Y}"
    elif plan.year is not None:
        in_period = lambda d: d.year == plan.year  # noqa: E731
        period = f"in {plan.year}"
    else:
        in_period = lambda d: True  # noqa: E731
        period = "on any date"
    documented: list[AnswerItem] = []
    uncertain: list[AnswerItem] = []
    for document_id, (date, usable) in sorted(dates.items(), key=lambda kv: (kv[1][0], kv[0])):
        if date is None or not in_period(date):
            continue
        cited = [f for f in facts if f.document_id == document_id and f.date_source == "document"]
        cited = sorted(cited, key=lambda f: (not f.usable, f.page_number, f.id))[:1]
        item = AnswerItem(
            _flag(
                f"Prescription dated {date:%d/%m/%Y} ({cited[0].document_name if cited else ''})",
                cited,
            ),
            tuple(_cite(f) for f in cited),
            tuple(f.id for f in cited),
            (document_id,),
            date,
        )
        (documented if usable else uncertain).append(item)
    sections: list[AnswerSection] = []
    if documented:
        sections.append(AnswerSection(DERIVED, f"Prescriptions dated {period}", tuple(documented)))
    else:
        sections.append(
            AnswerSection(UNAVAILABLE, f"No verified or validated prescription is dated {period}.")
        )
    if uncertain:
        sections.append(
            AnswerSection(
                UNCERTAIN, "Prescriptions whose date is not yet verified", tuple(uncertain)
            )
        )
    status = ANSWERED if documented or uncertain else NO_RECORDS
    summary = f"{len(documented)} prescription(s) dated {period} (a derived date filter)."
    return ClinicalAnswer(status, PRESCRIPTIONS, summary, tuple(sections))


def _months_before(day: dt.date, months: int) -> dt.date:
    month = day.month - months
    year = day.year
    while month <= 0:
        month += 12
        year -= 1
    last_day = [
        31,
        29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ][month - 1]
    return dt.date(year, month, min(day.day, last_day))


def _mentions(plan: RoutePlan, facts: Sequence[ClinicalFact], lexicon: Lexicon) -> ClinicalAnswer:
    name = plan.medication or ""
    lookup = lexicon.lookup(name)
    if lookup.generic:
        wanted = f"generic:{lookup.generic}"
    else:
        wanted = f"text:{name_key(name)}"
    matches = [f for f in facts if f.fact_type == "medication" and medication_key(f)[0] == wanted]
    documented_docs: dict[str, list[ClinicalFact]] = {}
    uncertain_docs: dict[str, list[ClinicalFact]] = {}
    for fact in matches:
        if fact.usable:
            documented_docs.setdefault(fact.document_id, []).append(fact)
        elif fact.uncertain:
            uncertain_docs.setdefault(fact.document_id, []).append(fact)
    for document_id in documented_docs:
        uncertain_docs.pop(document_id, None)

    def items(groups: dict[str, list[ClinicalFact]]) -> tuple[AnswerItem, ...]:
        out = []
        for document_id, group in sorted(
            groups.items(), key=lambda kv: _sorted(kv[1])[0].date or dt.date.min
        ):
            first = _sorted(group)[0]
            out.append(
                AnswerItem(
                    _flag(
                        f"{first.document_name}: “{first.source_excerpt}” — {_when(first)}",
                        group,
                    ),
                    tuple(_cite(f) for f in group),
                    tuple(f.id for f in group),
                    (document_id,),
                    first.date,
                )
            )
        return tuple(out)

    sections: list[AnswerSection] = []
    if documented_docs:
        sections.append(
            AnswerSection(DOCUMENTED, "Records that mention it", items(documented_docs))
        )
    else:
        sections.append(
            AnswerSection(UNAVAILABLE, "No verified or validated record mentions this medication.")
        )
    if uncertain_docs:
        sections.append(
            AnswerSection(UNCERTAIN, "Not yet verified mentions", items(uncertain_docs))
        )
    status = ANSWERED if documented_docs or uncertain_docs else NO_RECORDS
    match_note = "" if lookup.generic else " (matched by exact name only)"
    summary = f"{len(documented_docs)} record(s) mention it{match_note}."
    return ClinicalAnswer(status, MENTIONS, summary, tuple(sections))


def _changes(plan: RoutePlan, facts: Sequence[ClinicalFact]) -> ClinicalAnswer:
    dates = _document_dates(
        [f for f in facts if f.clinical_doc_type == "prescription" and f.usable]
    )
    ordered = sorted(((d, doc) for doc, (d, _) in dates.items() if d is not None))
    if len(ordered) < 2:
        return ClinicalAnswer(
            NO_RECORDS,
            CHANGES,
            "Fewer than two dated, verified or validated prescriptions are documented, so "
            "nothing can be compared.",
            (AnswerSection(UNAVAILABLE, "Not enough documented prescriptions to compare"),),
        )
    (date_a, doc_a), (date_b, doc_b) = (
        (ordered[0], ordered[-1]) if plan.first_and_latest else (ordered[-2], ordered[-1])
    )
    comparison = compare_documents(facts, doc_a, doc_b)
    items = []
    for item in comparison.items:
        if item.fact_type != "medication":
            continue
        evidence = item.a_facts + item.b_facts
        name = evidence[0].values.get("name_raw", "")
        detail = f" ({', '.join(item.changed_fields)})" if item.changed_fields else ""
        flag = " — matched by exact name only" if item.uncertain_match else ""
        items.append(
            AnswerItem(
                _flag(f"{name}: {item.status}{detail}{flag}", evidence),
                tuple(_cite(f) for f in evidence),
                tuple(f.id for f in evidence),
                (doc_a, doc_b),
            )
        )
    sections = [
        AnswerSection(
            DERIVED,
            f"Medications, prescription of {date_a:%d/%m/%Y} → prescription of "
            f"{date_b:%d/%m/%Y} (“missing” means not listed, not stopped)",
            tuple(items),
        )
    ]
    sections.extend(_uncertain_section(comparison.uncertain))
    return ClinicalAnswer(
        ANSWERED, CHANGES, f"Compared {len(items)} medication(s).", tuple(sections)
    )


def _conflicts(facts: Sequence[ClinicalFact]) -> ClinicalAnswer:
    found = [c for c in detect_conflicts(facts) if c.conflict_type.startswith("medication")]
    if not found:
        return ClinicalAnswer(
            ANSWERED,
            CONFLICTS,
            "No conflicting medication records were detected among the verified or validated "
            "records. Records needing review are not checked.",
            (AnswerSection(UNAVAILABLE, "No medication conflict detected"),),
        )
    items = tuple(
        AnswerItem(
            _flag(
                f"{c.members[0].values.get('name_raw')}: different "
                f"{', '.join(c.differing_fields)} documented for {_when(c.members[0])} in two "
                "records (not resolved)",
                c.members,
            ),
            tuple(_cite(m) for m in c.members),
            c.member_ids,
            tuple(m.document_id for m in c.members),
        )
        for c in found
    )
    return ClinicalAnswer(
        ANSWERED,
        CONFLICTS,
        f"{len(found)} medication conflict(s) found. MediMind does not decide which record is "
        "correct.",
        (AnswerSection(CONFLICTING, "Conflicting medication records", items),),
    )


def _history(facts: Sequence[ClinicalFact]) -> ClinicalAnswer:
    events = [f for f in facts if f.fact_type == "event"]
    usable = [f for f in events if f.usable]
    dated = _sorted([f for f in usable if f.date is not None])
    undated = [f for f in usable if f.date is None]
    label = lambda f: str(f.values.get("event_type", "")).replace("_", " ")  # noqa: E731
    sections: list[AnswerSection] = []
    if dated:
        sections.append(
            AnswerSection(
                DERIVED,
                "Documented events in date order (the ordering is MediMind's)",
                tuple(_fact_item(f, label(f)) for f in dated),
            )
        )
    if undated:
        sections.append(
            AnswerSection(
                DOCUMENTED,
                "Documented without a date",
                tuple(_fact_item(f, label(f)) for f in undated),
            )
        )
    if not usable:
        sections.append(
            AnswerSection(UNAVAILABLE, "No verified or validated events are documented.")
        )
    sections.extend(_uncertain_section([f for f in events if f.uncertain]))
    conflicts = detect_conflicts(facts)
    if conflicts:
        sections.append(
            AnswerSection(
                CONFLICTING,
                "Conflicting records exist (see the conflicts view); none is resolved",
                tuple(
                    AnswerItem(
                        _flag(c.conflict_type.replace("_", " "), c.members),
                        tuple(_cite(m) for m in c.members),
                        c.member_ids,
                        tuple(m.document_id for m in c.members),
                    )
                    for c in conflicts
                ),
            )
        )
    status = ANSWERED if usable or any(f.uncertain for f in events) else NO_RECORDS
    return ClinicalAnswer(status, HISTORY, f"{len(usable)} documented event(s).", tuple(sections))


def answer(
    plan: RoutePlan,
    facts: Sequence[ClinicalFact],
    *,
    as_of: dt.date,
    lexicon: Lexicon | None = None,
) -> ClinicalAnswer:
    """The extractive answer for a claimed plan. ``as_of`` is the explicit "today"."""
    lexicon = lexicon or load_lexicon()
    if plan.intent == ABSTAIN or plan.intent is None:
        return ClinicalAnswer(
            ABSTAINED,
            ABSTAIN,
            "MediMind does not extract this kind of information from your records yet, so it "
            "cannot answer this question.",
        )
    if plan.intent == MEDICATIONS:
        return _simple(
            MEDICATIONS,
            facts,
            lambda f: f.fact_type == "medication",
            "medication mentions",
            context_label=_medication_label,
        )
    if plan.intent == ALLERGIES:
        answer_ = _simple(
            ALLERGIES,
            facts,
            lambda f: f.fact_type == "allergy",
            "allergy statements",
            context_label=lambda f: str(f.values.get("allergy_status", "")).replace("_", " "),
        )
        allergy_conflicts = [
            c for c in detect_conflicts(facts) if c.conflict_type.startswith("allergy")
        ]
        if allergy_conflicts:
            extra = AnswerSection(
                CONFLICTING,
                "Records disagree about allergies (not resolved)",
                tuple(
                    AnswerItem(
                        _flag(c.conflict_type.replace("_", " "), c.members),
                        tuple(_cite(m) for m in c.members),
                        c.member_ids,
                        tuple(m.document_id for m in c.members),
                    )
                    for c in allergy_conflicts
                ),
            )
            answer_ = ClinicalAnswer(
                answer_.status, ALLERGIES, answer_.summary, answer_.sections + (extra,)
            )
        return answer_
    if plan.intent == ADMISSIONS:
        return _simple(
            ADMISSIONS,
            facts,
            lambda f: f.fact_type == "event" and f.values.get("event_type") == "admission",
            "hospital admissions",
            context_label=lambda f: "admitted",
        )
    if plan.intent == DIAGNOSES:
        return _simple(
            DIAGNOSES,
            facts,
            lambda f: (
                f.fact_type == "event" and f.values.get("event_type") == "diagnosis_documented"
            ),
            "diagnoses as written in the records",
            context_label=lambda f: (
                "past history" if f.values.get("event_context") == "history" else "documented"
            ),
        )
    if plan.intent == PRESCRIPTIONS:
        return _prescriptions(plan, facts, as_of)
    if plan.intent == MENTIONS:
        return _mentions(plan, facts, lexicon)
    if plan.intent == CHANGES:
        return _changes(plan, facts)
    if plan.intent == CONFLICTS:
        return _conflicts(facts)
    return _history(facts)


# --- answer-time citation integrity (Phase 6 acceptance fix) ------------------------------------

WITHHELD: Final = "withheld"
CITATION_WITHHELD: Final = (
    "This answer was withheld: a cited source could not be matched exactly to the stored page "
    "text. Nothing was changed; please open the document on the Documents page."
)


def citation_issues(
    answer: "ClinicalAnswer", page_text: "Callable[[str, int], str | None]"
) -> tuple[str, ...]:
    """Fixed issue codes for every citation that is not an exact slice of its stored page.

    ``page_text(document_id, page_number)`` returns the stored page text of THIS patient's
    document, or None. Codes: ``source_missing`` (no such page for the patient),
    ``offsets_missing``, ``excerpt_missing``, ``excerpt_mismatch``. Nothing is repaired.
    """
    issues: set[str] = set()
    for section in answer.sections:
        for item in section.items:
            for citation in item.citations:
                if not citation.excerpt:
                    issues.add("excerpt_missing")
                    continue
                if citation.char_start is None or citation.char_end is None:
                    issues.add("offsets_missing")
                    continue
                text = page_text(citation.document_id, citation.page_number)
                if text is None:
                    issues.add("source_missing")
                elif text[citation.char_start : citation.char_end] != citation.excerpt:
                    issues.add("excerpt_mismatch")
    return tuple(sorted(issues))


def withheld_answer(answer: "ClinicalAnswer") -> "ClinicalAnswer":
    return ClinicalAnswer(WITHHELD, answer.intent, CITATION_WITHHELD)
