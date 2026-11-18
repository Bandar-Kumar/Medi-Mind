"""Deterministic query router (Phase 5; ARCHITECTURE §9.1 as amended by ADR-040). Pure.

Order (each step can only narrow what the next one sees):

1. **Safety first** (``guardrails.input_gate``): advice, diagnosis, injection and invalid input are
   refused here; general-knowledge questions are ``unsupported`` (ADR-022). Nothing below runs.
2. **Analytes**: printed names and aliases from the versioned reference data
   (``aliases_v1.csv``), matched on word boundaries, longest first.
3. **Dates**: exact days (``16/09/2021``, ``2021-09-16``, ``16 September 2021``), months
   (``September 2021``), years (``in 2022``) and relative windows (``last year``,
   ``last 6 months``, relative to ``today``). An unreadable date-like token sets ``ambiguous``.
4. **Intent** from fixed keyword rules (comparison, history, conflicts, list, latest, document).
5. **Route** from intent and what was found.

Every rule that fires is recorded in ``reasons`` (fixed codes), so the UI can show why a route was
chosen. No model is involved: routing is reproducible and testable (ADR-040).
"""

import calendar
import datetime as dt
import re
from collections.abc import Mapping
from functools import cache

from app.answering.guardrails import input_gate
from app.answering.model import Intent, RefusalKind, Route, RoutePlan
from app.answering.vocabulary import question_vocabulary
from app.retrieval.query import STOPWORDS

_MONTHS = {
    name.lower(): number
    for number in range(1, 13)
    for name in (calendar.month_name[number], calendar.month_abbr[number])
}
_MONTHS["sept"] = 9

_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_MONTH_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_YEAR = re.compile(r"\b(?:in|from|during|of|for)\s+((?:19|20)\d{2})\b")
_RELATIVE = re.compile(
    r"\b(?:last|past|previous)\s+(?:(\d{1,2})\s+)?(year|years|month|months)\b", re.IGNORECASE
)
_SINCE_YEAR = re.compile(r"\bsince\s+((?:19|20)\d{2})\b", re.IGNORECASE)
_DATE_LIKE = re.compile(r"\b\d{1,4}[/.-]\d{1,2}(?:[/.-]\d{1,4})?\b")

_KEYWORDS: dict[Intent, re.Pattern[str]] = {
    Intent.COMPARE_PREVIOUS: re.compile(
        r"\b(?:compare|comparison|previous|prior|earlier|change|changed|difference|versus|vs\.?|"
        r"before that|than before)\b",
        re.IGNORECASE,
    ),
    Intent.CONFLICTS: re.compile(
        r"\b(?:conflict|conflicts|conflicting|disagree|disagreeing|inconsistent|discrepanc(?:y|ies)|"
        r"contradict\w*)\b",
        re.IGNORECASE,
    ),
    Intent.HISTORY: re.compile(
        r"\b(?:trend|trends|over time|history|historical|all (?:my )?(?:results|values|readings)|"
        r"every|over the (?:last|past)|results over|since|progress|timeline|series)\b",
        re.IGNORECASE,
    ),
    Intent.LIST_ANALYTES: re.compile(
        r"\b(?:which|what) (?:tests|analytes|lab tests|investigations)\b|\blist (?:my |the |all )?"
        r"(?:tests|analytes)\b|\bwhat (?:was|were|have) (?:i )?(?:been )?tested\b",
        re.IGNORECASE,
    ),
    Intent.LATEST: re.compile(
        r"\b(?:latest|last|most recent|recent|newest|current|currently)\b", re.IGNORECASE
    ),
    Intent.DOCUMENT_LOOKUP: re.compile(
        r"\b(?:report|reports|document|documents|note|notes|page|pages|letter)\b.*\b(?:say|says|said|"
        r"mention|mentions|mentioned|state|states|stated|write|written|show|shows|contain|contains)"
        r"\b|\b(?:laboratory|lab|clinic|hospital) (?:issued|name)\b|\bwhich (?:laboratory|lab)\b|"
        r"\bconsultation\b|\bwhat does (?:my|the) (?:report|document|note)\b",
        re.IGNORECASE,
    ),
}


@cache
def _word_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", re.IGNORECASE)


def find_analytes(question: str, aliases: Mapping[str, str]) -> tuple[str, ...]:
    """Canonical analytes named in the question (longest alias first, non-overlapping)."""
    text = " ".join(question.casefold().split())
    taken = [False] * len(text)
    found: list[str] = []
    for alias in sorted(aliases, key=lambda a: (-len(a), a)):
        for match in _word_pattern(alias).finditer(text):
            if any(taken[match.start() : match.end()]):
                continue
            for i in range(match.start(), match.end()):
                taken[i] = True
            canonical = aliases[alias]
            if canonical not in found:
                found.append(canonical)
    return tuple(found)


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    return dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1])


def _add_months(day: dt.date, months: int) -> dt.date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    return dt.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def find_dates(
    question: str, today: dt.date
) -> tuple[dt.date | None, dt.date | None, bool, bool, tuple[str, ...]]:
    """``(date_from, date_to, exact_day, ambiguous, reasons)`` from the question text."""
    reasons: list[str] = []
    days: list[dt.date] = []
    ambiguous = False
    consumed = question
    for pattern, order in ((_ISO, "ymd"), (_DMY, "dmy")):
        for match in pattern.finditer(consumed):
            a, b, c = (int(g) for g in match.groups())
            day = _safe_date(a, b, c) if order == "ymd" else _safe_date(c, b, a)
            if day is None:
                ambiguous = True
                reasons.append("date_unreadable")
            else:
                days.append(day)
        consumed = pattern.sub(" ", consumed)
    for match in _DAY_MONTH_YEAR.finditer(consumed):
        month = _MONTHS.get(match.group(2).lower())
        day = _safe_date(int(match.group(3)), month, int(match.group(1))) if month else None
        if day is not None:
            days.append(day)
            consumed = consumed.replace(match.group(0), " ")
    if days:
        reasons.append("exact_date")
        if len(set(days)) == 1:
            return days[0], days[0], True, ambiguous, tuple(reasons)
        return min(days), max(days), False, ambiguous, tuple(reasons)
    for match in _MONTH_YEAR.finditer(consumed):
        month = _MONTHS.get(match.group(1).lower())
        if month:
            start, end = _month_range(int(match.group(2)), month)
            return start, end, False, ambiguous, (*reasons, "month_range")
    relative = _RELATIVE.search(consumed)
    if relative:
        count = int(relative.group(1) or 1)
        months = count * (12 if relative.group(2).startswith("year") else 1)
        return _add_months(today, -months), today, False, ambiguous, (*reasons, "relative_window")
    since = _SINCE_YEAR.search(consumed)
    if since:
        return dt.date(int(since.group(1)), 1, 1), today, False, ambiguous, (*reasons, "since_year")
    year = _YEAR.search(consumed)
    if year:
        value = int(year.group(1))
        return dt.date(value, 1, 1), dt.date(value, 12, 31), False, ambiguous, (*reasons, "year")
    if _DATE_LIKE.search(consumed):
        ambiguous = True
        reasons.append("date_unreadable")
    return None, None, False, ambiguous, tuple(reasons)


# Words that frame a question about the records rather than name a test. Anything else left over
# after test names, dates and these words is treated as an unrecognised test name (ADR-041).
_FRAME_WORDS = frozenset(
    """a about after all also am an and any are as at be been before between by can could day days
    did do does done during each earlier every for from get give had has have how i in is it its
    kindly last latest list me mine more most my newest of on or our over past please previous
    prior recent current currently see show since tell than that the their them there these this
    those to was we were what when where which who with year years month months week weeks ago
    time times value values level levels reading readings result results test tests tested lab labs
    blood report reports dated date document documents page pages note say says said mention
    mentioned contain contains shown shows stored record records history historical trend trends
    progress timeline series compare compared comparison change changed difference versus vs
    conflict conflicts conflicting disagree disagreeing inconsistent discrepancy discrepancies
    measured taken check checked number numbers find found look know want would like
    everything whole both first second next new old""".split()
)
_MONTH_WORDS = frozenset(_MONTHS)
_TOKEN = re.compile(r"[a-z][a-z0-9'\-]*")


def _match_terms(
    question: str, single: Mapping[str, str], ambiguous: Mapping[str, tuple[str, ...]]
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """``(analytes, ambiguous_terms_found, remaining_text)``; longest term first, no overlaps."""
    text = " ".join(question.casefold().split())
    taken = [False] * len(text)
    found: list[str] = []
    unclear: list[str] = []
    terms = [(t, (single[t],)) for t in single] + [(t, ambiguous[t]) for t in ambiguous]
    for term, canonicals in sorted(terms, key=lambda pair: (-len(pair[0]), pair[0])):
        for match in _word_pattern(term).finditer(text):
            if any(taken[match.start() : match.end()]):
                continue
            for i in range(match.start(), match.end()):
                taken[i] = True
            if term in ambiguous:
                unclear.append(term)
            for canonical in canonicals:
                if canonical not in found:
                    found.append(canonical)
    remaining = "".join(" " if taken[i] else ch for i, ch in enumerate(text))
    return tuple(found), tuple(unclear), remaining


def unrecognised_terms(remaining: str) -> tuple[str, ...]:
    """Phrases left after test names, dates and framing words: candidate unknown test names.

    Contiguous leftover words form one phrase ("vitamin k"), so a short part of a name is never
    dropped and a page must contain the whole phrase to count as mentioning it.
    """
    remaining = _DMY.sub(" | ", _ISO.sub(" | ", remaining))
    phrases: list[str] = []
    current: list[str] = []

    def close() -> None:
        if any(len(w) >= 2 and not w.isdigit() for w in current):
            phrase = " ".join(current)
            if phrase not in phrases:
                phrases.append(phrase)
        current.clear()

    for raw in re.findall(r"[a-z0-9][a-z0-9'.\-]*|[^\sa-z0-9]", remaining):
        token = raw.strip("'-.")
        if not token or not token[0].isalnum():
            close()
            continue
        if token in _FRAME_WORDS or token in _MONTH_WORDS or token in STOPWORDS or token.isdigit():
            close()
            continue
        current.append(token)
    close()
    return tuple(phrases)


def route_question(question: str, *, aliases: Mapping[str, str], today: dt.date) -> RoutePlan:
    """The route for one question. Pure and deterministic."""
    refusal, general = input_gate(question)
    if refusal is not None:
        return RoutePlan(route=Route.REFUSAL, refusal=refusal, reasons=(f"gate_{refusal}",))
    if general:
        return RoutePlan(
            route=Route.UNSUPPORTED,
            refusal=RefusalKind.GENERAL_KNOWLEDGE,
            reasons=("gate_general_knowledge",),
        )
    single, ambiguous_terms = question_vocabulary(dict(aliases))
    analytes, unclear, remaining = _match_terms(question, single, ambiguous_terms)
    unknown = unrecognised_terms(remaining)
    date_from, date_to, exact_day, ambiguous, date_reasons = find_dates(question, today)
    reasons = [*date_reasons]
    if analytes:
        reasons.append("analyte_named")
    if unclear:
        reasons.append("ambiguous_test_name")
    hits = {intent for intent, pattern in _KEYWORDS.items() if pattern.search(question)}
    for intent in sorted(hits):
        reasons.append(f"keyword_{intent}")
    document = Intent.DOCUMENT_LOOKUP in hits
    if unknown:
        reasons.append("unmatched_words" if document else "unrecognised_test")

    def plan(route: Route, intent: Intent, *extra: str) -> RoutePlan:
        return RoutePlan(
            route=route,
            intent=intent,
            analytes=analytes,
            date_from=date_from,
            date_to=date_to,
            ambiguous=ambiguous or bool(unclear),
            unrecognised=unknown,
            reasons=(*reasons, *extra),
        )

    if not analytes and unknown and not document and Intent.LIST_ANALYTES not in hits:
        # A word that looks like a test name MediMind does not know: keep the question's shape,
        # but never widen it to "all tests" (ADR-041). The service abstains.
        if Intent.CONFLICTS in hits:
            intent = Intent.CONFLICTS
        elif Intent.COMPARE_PREVIOUS in hits:
            intent = Intent.COMPARE_PREVIOUS
        elif exact_day:
            intent = Intent.ON_DATE
        elif Intent.HISTORY in hits or date_from is not None:
            intent = Intent.HISTORY
        else:
            intent = Intent.LATEST
        route = (
            Route.LONGITUDINAL
            if intent in (Intent.HISTORY, Intent.COMPARE_PREVIOUS, Intent.CONFLICTS)
            else Route.STRUCTURED_FACTS
        )
        return plan(route, intent)
    if Intent.CONFLICTS in hits:
        return plan(Route.LONGITUDINAL, Intent.CONFLICTS)
    if Intent.LIST_ANALYTES in hits and not analytes:
        return plan(Route.STRUCTURED_FACTS, Intent.LIST_ANALYTES)
    if Intent.COMPARE_PREVIOUS in hits and (analytes or not document):
        route = Route.COMBINED if document else Route.LONGITUDINAL
        return plan(route, Intent.COMPARE_PREVIOUS)
    if analytes:
        if exact_day:
            intent = Intent.ON_DATE
        elif Intent.HISTORY in hits or date_from is not None:
            intent = Intent.HISTORY
        elif Intent.LATEST in hits:
            intent = Intent.LATEST
        else:
            # "What was my haemoglobin?": no time given. Shown as the latest result, labelled.
            ambiguous = True
            reasons.append("no_time_given")
            intent = Intent.LATEST
        if document:
            return plan(Route.COMBINED, intent)
        route = Route.LONGITUDINAL if intent is Intent.HISTORY else Route.STRUCTURED_FACTS
        return plan(route, intent)
    if exact_day and not document and Intent.HISTORY not in hits:
        return plan(Route.STRUCTURED_FACTS, Intent.ON_DATE, "all_analytes")
    if Intent.HISTORY in hits or (date_from is not None and not document):
        return plan(Route.LONGITUDINAL, Intent.HISTORY, "all_analytes")
    if document or exact_day:
        return plan(Route.DOCUMENT_SEARCH, Intent.DOCUMENT_LOOKUP)
    if Intent.LATEST in hits:
        return plan(Route.STRUCTURED_FACTS, Intent.LIST_ANALYTES, "latest_of_each_test")
    return plan(Route.DOCUMENT_SEARCH, Intent.NONE, "fallback_document_search")
