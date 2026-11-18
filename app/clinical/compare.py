"""Cross-document comparison and conflict detection ``clinical-compare-1`` (PHASE_6_PLAN §7).

Pure and deterministic; derived at read time and never stored (as Phase 4's duplicates, ADR-039).
Only **usable** facts take part: ``validated``, ``verified`` or ``corrected``, and ``subject =
patient``. A fact needing review, a rejected fact or a family-history fact is never compared; the
callers show needs-review facts separately as *uncertain*. A correction's normalized values stand
in for the machine values (the original stays visible in the evidence).

**Document comparison** (A → B; A is the older or the first named):
``new`` (only in B), ``missing`` (only in A: not listed, which is NOT the same as stopped),
``discontinued`` (B explicitly documents stopping it), ``repeated`` (same item, same compared
fields), ``changed`` (same item, a different compared field). Medications are matched by their
lexicon generic; an unmapped name is matched only by its exact normalized text and the item is
marked ``uncertain_match``. Compared fields: medication strength, frequency, as-needed, dosage
form and route (a course duration is not compared); allergy status, severity and reaction.

**Conflicts** (patient-wide; never resolved; the latest document is never assumed correct):
- ``medication_same_date_mismatch``: the same generic documented on the same day-precision date
  in two documents with a different strength or frequency (a discontinuation is not a conflict);
- ``allergy_contradiction``: a "no known allergies" statement and a confirmed or suspected allergy
  in different documents;
- ``allergy_status_disagreement``: the same substance confirmed in one document and suspected in
  another.
Each conflict lists both facts and the differing fields; nothing is merged or deleted.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Final

from app.clinical.lexicon import name_key

USABLE: Final = frozenset({"validated", "verified", "corrected"})
MEDICATION_FIELDS: Final = (
    "strength_value",
    "strength_unit",
    "frequency_per_day",
    "as_needed",
    "dosage_form",
    "route_norm",
)
ALLERGY_FIELDS: Final = ("allergy_status", "severity_norm", "reaction_key")
CONFLICT_FIELDS: Final = ("strength_value", "strength_unit", "frequency_per_day", "as_needed")

NEW, MISSING, DISCONTINUED, REPEATED, CHANGED = (
    "new",
    "missing",
    "discontinued",
    "repeated",
    "changed",
)


@dataclass(frozen=True)
class ClinicalFact:
    """A clinical fact as comparison, conflicts and answers see it (no database types)."""

    id: str
    fact_type: str  # medication / allergy / event
    document_id: str
    document_name: str = field(repr=False)
    clinical_doc_type: str | None
    document_date: dt.date | None
    page_number: int
    source_excerpt: str = field(repr=False)
    subject: str
    negated: bool
    date: dt.date | None
    date_precision: str | None
    date_source: str | None
    review_status: str
    review_reasons: tuple[str, ...]
    values: Mapping[str, Any] = field(repr=False)
    corrected: bool = False
    # Phase 6 acceptance fixes: the source document's identity outcome, and the excerpt's exact
    # offsets (for answer-time citation checks). The default is the cautious "unknown", so a
    # caller that omits it can only add a warning, never hide one.
    identity_check: str = "unknown"
    char_start: int | None = None
    char_end: int | None = None

    @property
    def identity_confirmed(self) -> bool:
        return self.identity_check == "match"

    @property
    def usable(self) -> bool:
        return self.review_status in USABLE and self.subject == "patient"

    @property
    def uncertain(self) -> bool:
        return self.review_status == "needs_review" and self.subject == "patient"


def apply_correction(fact: ClinicalFact, corrected: Mapping[str, Any] | None) -> ClinicalFact:
    """The fact with a correction's normalized values in place of the machine values."""
    if not corrected:
        return fact
    normalized = dict(corrected.get("normalized") or {})
    values = dict(fact.values)
    date, precision = fact.date, fact.date_precision
    if "date" in normalized:
        date = dt.date.fromisoformat(normalized.pop("date"))
        precision = normalized.pop("date_precision", precision)
    if "strength_value" in normalized and normalized["strength_value"] is not None:
        normalized["strength_value"] = Decimal(str(normalized["strength_value"]))
    if "reaction" in normalized:
        values["reaction_raw"] = normalized.pop("reaction")
    if "description" in normalized:
        values["description_raw"] = normalized.pop("description")
    values.update(normalized)
    negated = (
        values.get("allergy_status") == "none_known"
        if fact.fact_type == "allergy"
        else (fact.negated)
    )
    return replace(
        fact,
        values=values,
        date=date,
        date_precision=precision,
        negated=negated,
        corrected=True,
    )


# --- keys ---------------------------------------------------------------------------------------


def medication_key(fact: ClinicalFact) -> tuple[str, bool]:
    """(match key, uncertain): the generic, or the exact normalized printed name."""
    generic = fact.values.get("name_norm")
    if generic:
        return f"generic:{generic}", False
    return f"text:{name_key(str(fact.values.get('name_raw', '')))}", True


def allergy_key(fact: ClinicalFact) -> str:
    if fact.values.get("allergy_status") == "none_known":
        return "none_known"
    norm = fact.values.get("substance_norm") or name_key(str(fact.values.get("substance_raw", "")))
    return f"substance:{norm}"


def _fields(fact: ClinicalFact, names: Iterable[str]) -> tuple:
    out = []
    for name in names:
        if name == "reaction_key":
            reaction = fact.values.get("reaction_raw")
            out.append(name_key(reaction) if reaction else None)
        else:
            value = fact.values.get(name)
            out.append(value.normalize() if isinstance(value, Decimal) else value)
    return tuple(out)


# --- document comparison ------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparisonItem:
    fact_type: str
    key: str
    status: str
    a_facts: tuple[ClinicalFact, ...]
    b_facts: tuple[ClinicalFact, ...]
    changed_fields: tuple[str, ...] = ()
    uncertain_match: bool = False


@dataclass(frozen=True)
class DocumentComparison:
    document_a: str
    document_b: str
    items: tuple[ComparisonItem, ...]
    uncertain: tuple[ClinicalFact, ...]  # needs-review facts of A or B, never compared

    def statuses(self, fact_type: str = "medication") -> dict[str, str]:
        return {i.key: i.status for i in self.items if i.fact_type == fact_type}


def _group(facts: Iterable[ClinicalFact], fact_type: str) -> dict[str, list[ClinicalFact]]:
    groups: dict[str, list[ClinicalFact]] = defaultdict(list)
    for fact in facts:
        if fact.fact_type != fact_type:
            continue
        if fact_type == "medication":
            groups[medication_key(fact)[0]].append(fact)
        elif fact_type == "allergy":
            groups[allergy_key(fact)].append(fact)
        elif fact.values.get("event_type") == "diagnosis_documented":
            groups[f"diagnosis:{name_key(str(fact.values.get('description_raw', '')))}"].append(
                fact
            )
    return groups


def compare_documents(
    facts: Sequence[ClinicalFact], document_a: str, document_b: str
) -> DocumentComparison:
    """Compare two documents' usable medications, allergies and documented diagnoses."""
    a = [f for f in facts if f.document_id == document_a and f.usable]
    b = [f for f in facts if f.document_id == document_b and f.usable]
    uncertain = tuple(f for f in facts if f.document_id in (document_a, document_b) and f.uncertain)
    items: list[ComparisonItem] = []
    for fact_type, compared in (
        ("medication", MEDICATION_FIELDS),
        ("allergy", ALLERGY_FIELDS),
        ("event", ()),
    ):
        groups_a, groups_b = _group(a, fact_type), _group(b, fact_type)
        for key in sorted(set(groups_a) | set(groups_b)):
            in_a, in_b = tuple(groups_a.get(key, ())), tuple(groups_b.get(key, ()))
            uncertain_match = fact_type == "medication" and key.startswith("text:")
            stopped = [f for f in in_b if f.values.get("mention_context") == "discontinued"]
            listed_b = [f for f in in_b if f.values.get("mention_context") != "discontinued"]
            changed: tuple[str, ...] = ()
            if fact_type == "medication" and stopped:
                status = DISCONTINUED
            elif not in_a:
                status = NEW
            elif not (listed_b if fact_type == "medication" else in_b):
                status = MISSING
            else:
                fields_a = {_fields(f, compared) for f in in_a}
                fields_b = {_fields(f, compared) for f in (listed_b or in_b)}
                if fields_a == fields_b:
                    status = REPEATED
                else:
                    status = CHANGED
                    changed = tuple(
                        name
                        for index, name in enumerate(compared)
                        if {t[index] for t in fields_a} != {t[index] for t in fields_b}
                    )
            items.append(
                ComparisonItem(fact_type, key, status, in_a, in_b, changed, uncertain_match)
            )
    return DocumentComparison(document_a, document_b, tuple(items), uncertain)


# --- conflicts ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClinicalConflict:
    conflict_type: str
    members: tuple[ClinicalFact, ClinicalFact]
    differing_fields: tuple[str, ...]

    @property
    def member_ids(self) -> tuple[str, str]:
        return tuple(sorted(m.id for m in self.members))  # type: ignore[return-value]


def detect_conflicts(facts: Sequence[ClinicalFact]) -> tuple[ClinicalConflict, ...]:
    """Every pairwise conflict among the patient's usable facts (see the module docstring)."""
    usable = sorted((f for f in facts if f.usable), key=lambda f: f.id)
    found: list[ClinicalConflict] = []
    medications = [
        f
        for f in usable
        if f.fact_type == "medication"
        and f.values.get("name_norm")
        and f.values.get("mention_context") != "discontinued"
        and f.date is not None
        and f.date_precision == "day"
    ]
    for index, first in enumerate(medications):
        for second in medications[index + 1 :]:
            if first.document_id == second.document_id:
                continue
            if first.values["name_norm"] != second.values["name_norm"] or first.date != second.date:
                continue
            differing = tuple(
                name
                for name, x, y in zip(
                    CONFLICT_FIELDS,
                    _fields(first, CONFLICT_FIELDS),
                    _fields(second, CONFLICT_FIELDS),
                    strict=True,
                )
                if x != y
            )
            if differing:
                found.append(
                    ClinicalConflict("medication_same_date_mismatch", (first, second), differing)
                )
    allergies = [f for f in usable if f.fact_type == "allergy"]
    for index, first in enumerate(allergies):
        for second in allergies[index + 1 :]:
            if first.document_id == second.document_id:
                continue
            statuses = {first.values.get("allergy_status"), second.values.get("allergy_status")}
            if "none_known" in statuses and statuses != {"none_known"}:
                found.append(
                    ClinicalConflict("allergy_contradiction", (first, second), ("allergy_status",))
                )
            elif statuses == {"confirmed", "suspected"} and allergy_key(first) == allergy_key(
                second
            ):
                found.append(
                    ClinicalConflict(
                        "allergy_status_disagreement", (first, second), ("allergy_status",)
                    )
                )
    return tuple(sorted(found, key=lambda c: (c.conflict_type, c.member_ids)))
