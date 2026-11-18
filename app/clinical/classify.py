"""Clinical document-type classification ``clinical-doctype-1`` (docs/PHASE_6_PLAN.md §3).

Pure and deterministic, like the Phase 2 classifier (which stays frozen and unchanged): it runs
only on documents that Phase 2 did **not** call ``lab_report``, and refines them into the six
Phase 6 types. It reads page text, never changes it, and returns rule ids and numbers only.

Each family has **title** cues (weight ``TITLE_WEIGHT``: a heading that names the document),
optional **marker** cues (``MARKER_WEIGHT``, e.g. a lone ``Rx``) and **body** cues (1 each). A cue
fires when any of its patterns occurs on any page; each cue counts once.

Decision, first match wins:
- no text                                      → ``no_text``
- no family has a title or marker cue          → ``unclassified``
- the top family leads the next by < ``MARGIN`` → ``ambiguous_type`` (never silently resolved)
- the top family is an unsupported one         → ``unsupported_type`` (invoice, letter, booking)
- otherwise                                    → the top family

Any change to a pattern, weight or threshold must bump ``CLINICAL_CLASSIFIER_VERSION``.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.clinical.reasons import TypeAbstention
from app.clinical.versions import CLINICAL_CLASSIFIER_VERSION
from app.ingestion.classification import ClassifiablePage

PRESCRIPTION: Final = "prescription"
DISCHARGE_SUMMARY: Final = "discharge_summary"
CONSULTATION_NOTE: Final = "consultation_note"
HISTORY_RECORD: Final = "history_record"
ALLERGY_RECORD: Final = "allergy_record"
IMAGING_REPORT: Final = "imaging_report"
CLINICAL_TYPES: Final = (
    PRESCRIPTION,
    DISCHARGE_SUMMARY,
    CONSULTATION_NOTE,
    HISTORY_RECORD,
    ALLERGY_RECORD,
    IMAGING_REPORT,
)
UNSUPPORTED: Final = "unsupported"

TITLE_WEIGHT: Final = 6
MARKER_WEIGHT: Final = 3
MARGIN: Final = 2


def _p(*sources: str, flags: int = re.IGNORECASE) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(source, flags | re.MULTILINE) for source in sources)


@dataclass(frozen=True)
class Cue:
    rule_id: str
    family: str
    weight: int
    patterns: tuple[re.Pattern[str], ...]


CUES: Final[tuple[Cue, ...]] = (
    # --- prescription ---------------------------------------------------------------------------
    Cue("rx.title", PRESCRIPTION, TITLE_WEIGHT, _p(r"\bprescription\b(?!\s+(?:no|number)\b)")),
    Cue(
        "rx.marker",
        PRESCRIPTION,
        MARKER_WEIGHT,
        _p(
            r"^\s*(?:rx|℞)\s*:?\s*$",
            r"^\s*(?:rx|℞)\s*:?\s+(?:\d+[.)]\s+)?(?:tab|cap|syp|inj)\b",
            r"^\s*(?:medicines|prescribed\s+medications?|medications?\s+prescribed)\s*:",
        ),
    ),
    Cue("rx.item", PRESCRIPTION, 1, _p(r"\b(?:tab|cap|syp|inj)\.\s+\w+")),
    Cue("rx.frequency", PRESCRIPTION, 1, _p(r"\b[0-2]-[0-2]-[0-2]\b")),
    # --- discharge summary ----------------------------------------------------------------------
    Cue("ds.title", DISCHARGE_SUMMARY, TITLE_WEIGHT, _p(r"\bdischarge\s+(?:summary|card)\b")),
    Cue("ds.admission", DISCHARGE_SUMMARY, 1, _p(r"\bdate\s+of\s+admission\b")),
    Cue("ds.discharge", DISCHARGE_SUMMARY, 1, _p(r"\bdate\s+of\s+discharge\b")),
    Cue("ds.course", DISCHARGE_SUMMARY, 1, _p(r"\bcourse\s+in\s+(?:the\s+)?hospital\b")),
    Cue("ds.medications", DISCHARGE_SUMMARY, 1, _p(r"\bdischarge\s+medications?\b")),
    # --- consultation note ----------------------------------------------------------------------
    Cue(
        "cn.title",
        CONSULTATION_NOTE,
        TITLE_WEIGHT,
        _p(
            r"\b(?:outpatient|opd|clinic)\s+(?:consultation|visit)(?:\s+note)?\b",
            r"\bconsultation\s+note\b",
            r"\b(?:visit|progress)\s+note\b",
        ),
    ),
    Cue("cn.complaints", CONSULTATION_NOTE, 1, _p(r"\b(?:chief|presenting)\s+complaints?\b")),
    Cue("cn.consultant", CONSULTATION_NOTE, 1, _p(r"\bconsultant\s*:")),
    # --- medical history record -----------------------------------------------------------------
    Cue(
        "hx.title",
        HISTORY_RECORD,
        TITLE_WEIGHT,
        _p(
            r"\bmedical\s+history\s+(?:record|form|sheet)\b",
            r"\bhealth\s+history\s+(?:record|form)\b",
        ),
    ),
    Cue("hx.past", HISTORY_RECORD, 1, _p(r"\bpast\s+medical\s+history\b")),
    Cue("hx.surgical", HISTORY_RECORD, 1, _p(r"\bsurgical\s+history\b")),
    Cue("hx.family", HISTORY_RECORD, 1, _p(r"\bfamily\s+history\b")),
    # --- allergy record -------------------------------------------------------------------------
    Cue(
        "al.title",
        ALLERGY_RECORD,
        TITLE_WEIGHT,
        _p(r"\b(?:drug\s+)?allergy\s+(?:record|card|form|register)\b"),
    ),
    Cue("al.allergen", ALLERGY_RECORD, 1, _p(r"\ballergen\b")),
    Cue("al.reaction", ALLERGY_RECORD, 1, _p(r"\breaction\b")),
    Cue("al.severity", ALLERGY_RECORD, 1, _p(r"\bseverity\b")),
    # --- imaging report -------------------------------------------------------------------------
    Cue(
        "im.title",
        IMAGING_REPORT,
        TITLE_WEIGHT,
        _p(
            r"\bdepartment\s+of\s+(?:radiology|radio-?diagnosis|imaging)\b",
            r"\b(?:radiology|imaging|ultrasound|sonography|x-?ray|ct|mri)\s+report\b",
        ),
    ),
    Cue("im.impression", IMAGING_REPORT, 1, _p(r"^\s*impression\s*:", r"\bimpression\s*:")),
    Cue("im.findings", IMAGING_REPORT, 1, _p(r"\bfindings\s*:")),
    Cue("im.radiologist", IMAGING_REPORT, 1, _p(r"\bradiologist\b")),
    # --- unsupported (never extracted) ----------------------------------------------------------
    Cue(
        "un.invoice",
        UNSUPPORTED,
        TITLE_WEIGHT,
        _p(r"\b(?:tax\s+)?invoice\b", r"\bbill\s+(?:no|number)\b", r"\bpayment\s+mode\b"),
    ),
    Cue(
        "un.letter",
        UNSUPPORTED,
        TITLE_WEIGHT,
        _p(r"\bto\s+whom\s+it\s+may\s+concern\b", r"\bthis\s+is\s+to\s+certify\b", r"^\s*dear\b"),
    ),
    Cue(
        "un.booking",
        UNSUPPORTED,
        TITLE_WEIGHT,
        _p(r"\bappointment\s+(?:slip|confirmation)\b", r"\bsample\s+collection\s+receipt\b"),
    ),
    Cue("un.amount", UNSUPPORTED, 1, _p(r"\btotal\s+amount\b", r"\bamount\s*\(rs\.?\)")),
)
_STRONG = frozenset(c.rule_id for c in CUES if c.weight > 1)


@dataclass(frozen=True)
class ClinicalClassification:
    """The outcome. Rule ids and numbers only: never page text."""

    clinical_type: str | None  # one of CLINICAL_TYPES, or None with ``abstention``
    abstention: str | None
    scores: dict[str, int]
    rule_ids: tuple[str, ...]
    rules_version: str = CLINICAL_CLASSIFIER_VERSION


def classify_clinical(pages: Sequence[ClassifiablePage]) -> ClinicalClassification:
    """Classify one non-lab document from its parsed pages. Deterministic; reads, never changes."""
    if isinstance(pages, str | bytes) or not isinstance(pages, Sequence) or not pages:
        raise ValueError("pages must be a non-empty sequence of parsed pages")
    text = "\n".join(page.text for page in sorted(pages, key=lambda p: p.page_number))
    if not text.strip():
        return ClinicalClassification(None, TypeAbstention.NO_TEXT.value, {}, ())
    fired = [cue for cue in CUES if any(p.search(text) for p in cue.patterns)]
    scores: dict[str, int] = {}
    for cue in fired:
        scores[cue.family] = scores.get(cue.family, 0) + cue.weight
    rule_ids = tuple(sorted(cue.rule_id for cue in fired))
    strong = {cue.family for cue in fired if cue.rule_id in _STRONG}
    if not strong:
        return ClinicalClassification(None, TypeAbstention.UNCLASSIFIED.value, scores, rule_ids)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    top, top_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if top not in strong:
        return ClinicalClassification(None, TypeAbstention.UNCLASSIFIED.value, scores, rule_ids)
    if top_score - runner_up < MARGIN:
        return ClinicalClassification(None, TypeAbstention.AMBIGUOUS_TYPE.value, scores, rule_ids)
    if top == UNSUPPORTED:
        return ClinicalClassification(None, TypeAbstention.UNSUPPORTED_TYPE.value, scores, rule_ids)
    return ClinicalClassification(top, None, scores, rule_ids)
