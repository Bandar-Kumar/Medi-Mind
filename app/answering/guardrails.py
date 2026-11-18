"""Deterministic safety gates for the copilot (ADR-022, ADR-040). Pure.

**Input gate** (before routing, retrieval or any model): the R1 gate
(``app.retrieval.query.classify_question``: advice, injection, invalid) plus Phase 5 rules for
diagnosis and interpretation requests and for general medical knowledge questions. A refused
question never reaches a tool, the retriever or a model, so no router or model can bypass it.

**Output gate** (after generation): generated claim text must not diagnose, advise, prescribe or
interpret. Quoted evidence (text inside double quotes) is excluded from the check, because it is
exact stored text, not a statement by MediMind.
"""

import re

from app.answering.model import RefusalKind
from app.retrieval.query import MAX_QUESTION_CHARS, QuestionGate, classify_question

REFUSAL_MESSAGES: dict[RefusalKind, str] = {
    RefusalKind.ADVICE: (
        "MediMind only shows what your own records say. It cannot recommend, start, stop or change "
        "medicines, treatments or tests. Please ask your doctor."
    ),
    RefusalKind.DIAGNOSIS: (
        "MediMind cannot diagnose conditions or interpret what results mean for your health. It "
        "can show the values and reference ranges printed in your reports. Please ask your doctor."
    ),
    RefusalKind.INJECTION: (
        "MediMind answers only from the selected patient's records and does not change its rules. "
        "This request was refused."
    ),
    RefusalKind.INVALID: "Please type a question (up to 500 characters).",
    RefusalKind.GENERAL_KNOWLEDGE: (
        "MediMind answers only from the selected patient's records; it does not give general "
        "medical explanations. Try asking what your reports say, for example your latest result."
    ),
}

_DIAGNOSIS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwhat (?:disease|diseases|condition|conditions|illness|illnesses|disorder)s?\b",
        r"\bwhat(?:'s| is) wrong with (?:me|my|him|her)\b",
        r"\bdiagnos(?:e|is|es|ed|ing)\b",
        r"(?<!tests )(?<!results )(?<!reports )(?<!documents )(?<!records )\bdo (?:i|you think i) "
        r"have\b",
        r"\bam i (?:sick|ill|diabetic|anaemic|anemic|healthy|ok|okay|fine|at risk)\b",
        r"\bis (?:my|this|it|the) [\w\s]{0,40}\b(?:normal|abnormal|good|bad|serious|dangerous|"
        r"healthy|worrying|concerning|ok|okay)\b",
        r"\b(?:does|do) (?:this|these|my) [\w\s]{0,40}\b(?:mean|indicate|suggest|show) "
        r"(?:i|that i)\b",
        r"\bwhat do (?:these|my|the) results? (?:mean|indicate|suggest)\b",
        r"\bshould i (?:be )?(?:worried|concerned)\b",
    )
)
_ADVICE = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwhich (?:medicine|medication|drug|tablet|pill|supplement|treatment)s?\b",
        r"\bwhich (?:tests?|scans?|procedures?) (?:should|do i need|must)\b",
        r"\bwhat (?:medicine|medication|drug|tablet|pill|supplement|treatment)s?\b",
        r"\bwhat should (?:i|we) (?:do|take|eat|avoid)\b",
        r"\b(?:recommend|prescribe|suggest) (?:a|an|some|me|the)?\b",
    )
)
# "What is HbA1c?", "What does TSH mean?", "Explain cholesterol": general knowledge, not records.
_GENERAL = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*what (?:is|are) (?:a |an |the )?(?:normal )?[\w\s\-()]{1,40}\??\s*$",
        r"^\s*what does [\w\s\-()]{1,40} (?:mean|measure|do)\??\s*$",
        r"^\s*(?:explain|define|describe) [\w\s\-()]{1,60}\??\s*$",
        r"\bwhat (?:is|are) (?:a )?normal (?:level|range|value)s?\b",
    )
)
# Words that tie a question to the patient's own records: such a question is not general knowledge.
_RECORD_WORDS = re.compile(
    r"\b(?:my|mine|me|i|report|reports|result|results|document|documents|record|records|value|"
    r"latest|last|recent|previous|test(?:ed)?|level)\b",
    re.IGNORECASE,
)


# "Which tests do I have?" asks about the records, not a diagnosis. The R1 gate (frozen for the R1.8
# evaluation) refuses every "do I have"; Phase 5 re-checks the question without this phrase only.
_RECORDS_HAVE = re.compile(
    r"\b(?:tests?|results?|reports?|documents?|records?)\s+do\s+i\s+have\b", re.IGNORECASE
)


_NORMAL_VALUES = re.compile(
    r"\bnormal (?:\w+ )?(?:level|levels|range|ranges|value|values)\b", re.IGNORECASE
)
_PERSONAL = re.compile(r"\b(?:my|mine|me|i)\b", re.IGNORECASE)


def input_gate(question: str) -> tuple[RefusalKind | None, bool]:
    """``(refusal, general_knowledge)`` for a question. ``(None, False)`` means allowed."""
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
        return RefusalKind.INVALID, False
    gate = classify_question(question)
    if gate is QuestionGate.ADVICE and _RECORDS_HAVE.search(question):
        gate = classify_question(_RECORDS_HAVE.sub(" ", question))
    if gate is QuestionGate.INVALID:
        return RefusalKind.INVALID, False
    if gate is QuestionGate.INJECTION:
        return RefusalKind.INJECTION, False
    if any(p.search(question) for p in _DIAGNOSIS):
        return RefusalKind.DIAGNOSIS, False
    if gate is QuestionGate.ADVICE or any(p.search(question) for p in _ADVICE):
        # The R1 advice rules also catch "diagnosis" and "treatment"; diagnosis matched above.
        return RefusalKind.ADVICE, False
    if any(p.search(question) for p in _GENERAL) and not _RECORD_WORDS.search(question):
        return None, True
    if _NORMAL_VALUES.search(question) and not _PERSONAL.search(question):
        return None, True  # "What is a normal haemoglobin level?": general, not the records
    return None, False


# --- output gate ---------------------------------------------------------------------------------

_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")
_PROHIBITED_OUTPUT = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\byou should\b",
        r"\b(?:i|we) (?:recommend|suggest|advise)\b",
        r"\brecommend(?:ed|s|ation)?\b",
        r"\bdiagnos(?:e|is|es|ed)\b",
        r"\bprescri(?:be|bed|ption)\b",
        r"\byou (?:have|may have|likely have|probably have|might have)\b",
        r"\b(?:indicates?|suggests?|consistent with|likely|probably)\b",
        r"\b(?:abnormal|healthy|unhealthy|dangerous|worrying|improv(?:ed|ing|ement)|"
        r"deteriorat(?:ed|ing|ion)|worsen(?:ed|ing)?)\b",
        r"\b(?:start|stop|increase|decrease|reduce) (?:taking|your|the) \w+",
        r"\bas (?:a|your) (?:doctor|physician|clinician)\b",
    )
)


def prohibited_output(text: str) -> bool:
    """True when generated text (outside quotes) diagnoses, advises, prescribes or interprets."""
    unquoted = _QUOTED.sub(" ", text)
    return any(p.search(unquoted) for p in _PROHIBITED_OUTPUT)
