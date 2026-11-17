"""Question text to lexical search terms, and the advice guardrail (Milestone R1, ADR-022).

Both are deterministic and pure. The question text never leaves this process except as
parameterized search terms; it is never logged.
"""

import re
from enum import StrEnum

MAX_QUESTION_CHARS = 500
MAX_TERMS = 24
MAX_TERM_CHARS = 64

# Postgres' 'simple' configuration keeps every word (no stopwords), so question words such as
# "what" or "my" would otherwise dominate an OR query. Kept small and explicit on purpose.
STOPWORDS = frozenset(
    """a an and are as at be been by can could did do does for from had has have how i in is it
    its me my of on or our please show tell than that the their them there these this those to
    us was we were what when where which who whom why will with would you your
    report reports value values result results""".split()
)

# Characters stripped from the ends of a token. Inner characters such as "/", "." and "-" are
# kept, so dates (05/06/2024), decimals (13.5) and codes (25-OH) survive as one term.
_EDGE_PUNCTUATION = "?!,;:\"'()[]{}<>“”‘’."


def query_terms(question: str) -> tuple[str, ...]:
    """Lower-cased content words of the question, in order, without duplicates or stopwords."""
    terms: list[str] = []
    for raw in question.split():
        token = raw.strip(_EDGE_PUNCTUATION).lower()
        if not token or token in STOPWORDS or len(token) > MAX_TERM_CHARS:
            continue
        if not any(ch.isalnum() for ch in token):
            continue
        if token not in terms:
            terms.append(token)
        if len(terms) == MAX_TERMS:
            break
    return tuple(terms)


class QuestionGate(StrEnum):
    ALLOWED = "allowed"
    ADVICE = "advice"  # asks for a decision about treatment, medicines or diagnosis
    INJECTION = "injection"  # tries to change instructions or reach other patients' data
    INVALID = "invalid"  # empty or too long


# Advice-seeking: decisions about medicines, doses, treatment or diagnosis (ADR-022). Asking what a
# document says (a value, a date, a reference range) is allowed.
_ADVICE = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bshould (?:i|we|he|she|they|my \w+)\b",
        r"\b(?:can|may|must) (?:i|we) (?:stop|start|take|increase|decrease|reduce|skip|double|"
        r"change|continue)\b",
        r"\bwhat (?:dose|dosage)\b",
        r"\bhow (?:much|many)\b.*\b(?:take|dose)\b",
        r"\bis it (?:safe|ok|okay|dangerous|harmful)\b",
        r"\b(?:recommend|prescribe|diagnose|diagnosis|treatment|treat|cure)\b",
        r"\bdo i have\b",
        r"\bam i (?:sick|ill|diabetic|anaemic|anemic|healthy)\b",
        r"\bwhich (?:medicine|medication|drug|tablet)s?\b",
    )
)
_INJECTION = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bignore\b.*\b(?:instructions?|rules?|prompt)\b",
        r"\bsystem prompt\b",
        r"\b(?:all|other|every|another) patients?\b",
        r"\bsomeone else'?s?\b",
    )
)


def classify_question(question: str) -> QuestionGate:
    """Deterministic gate before any retrieval."""
    if not question.strip() or len(question) > MAX_QUESTION_CHARS:
        return QuestionGate.INVALID
    if any(pattern.search(question) for pattern in _INJECTION):
        return QuestionGate.INJECTION
    if any(pattern.search(question) for pattern in _ADVICE):
        return QuestionGate.ADVICE
    return QuestionGate.ALLOWED
