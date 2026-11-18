"""Extractive answer composer: a deterministic generator with no model (Phase 5; ADR-040). Pure.

It turns the sources chosen by the tools into short templated claims, each citing the sources it
states. Values, units and dates are copied from the stored evidence; nothing is inferred. It is:

- the **default generator** while no LLM provider is approved and evaluated (ADR-024 is still
  open), so the copilot works fully offline;
- the **baseline** any LLM generator must beat in the provider evaluation;
- held to the **same citation validator** as an LLM: it gets no special treatment.

Wording rules (ADR-022, ADR-023): no interpretation, no advice; ``validated`` facts are labelled
"auto-checked, not verified"; corrected facts show the corrected and the printed value; conflicts
show every side; identity-unconfirmed documents are labelled; a difference is a "calculation" and
only between identical units.
"""

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from app.answering.model import DraftAnswer, DraftClaim, EvidenceItem, Intent, RoutePlan, SourceKind

COMPOSER_VERSION = "extractive-1"
MAX_PASSAGE_CLAIMS = 3
MAX_LINE_CHARS = 200


def _value(item: EvidenceItem) -> str:
    shown = item.value if item.value is not None else (item.qualitative or "")
    return f"{item.comparator or ''}{shown}{' ' + item.unit if item.unit else ''}".strip()


def _when(item: EvidenceItem) -> str:
    if item.effective_date is None:
        return "with no date in the report"
    if item.date_precision in (None, "day"):
        return f"on {item.effective_date.isoformat()}"
    if item.date_precision == "month":
        return f"in {item.effective_date.strftime('%B %Y')} (month only)"
    return f"in {item.effective_date.year} (year only)"


def _labels(item: EvidenceItem) -> str:
    labels = []
    if item.corrected:
        printed = f"{item.value_raw or ''} {item.unit_raw or ''}".strip()
        labels.append(f'corrected by a reviewer; the report printed "{printed}"')
    elif item.review_status == "verified":
        labels.append("verified by a reviewer")
    else:
        labels.append("auto-checked, not verified")
    if item.identity_check != "match":
        labels.append("identity not confirmed")
    if item.in_conflict:
        labels.append("another record disagrees")
    return "; ".join(labels)


def fact_sentence(item: EvidenceItem) -> str:
    return f"{item.analyte}: {_value(item)} {_when(item)} ({_labels(item)})."


def _by_analyte(items: Sequence[EvidenceItem]) -> dict[str, list[EvidenceItem]]:
    groups: dict[str, list[EvidenceItem]] = {}
    for item in items:
        groups.setdefault(item.analyte or "", []).append(item)
    return groups


def _decimal(text: str | None) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _same_date_note(group: list[EvidenceItem]) -> list[DraftClaim]:
    """Several different stored values on the latest date: shown side by side, never resolved."""
    if len(group) < 2 or len({_value(i) for i in group}) < 2:
        return []
    return [
        DraftClaim(
            text=(
                f"{group[0].analyte} has different stored results {_when(group[0])}; MediMind "
                "shows all of them and does not choose one."
            ),
            citations=tuple(i.source_id for i in group),
            kind="summary",
        )
    ]


def _comparison(analyte: str, group: list[EvidenceItem]) -> list[DraftClaim]:
    """Previous then latest (already ordered by the tool), plus an exact difference if allowed."""
    claims = [
        DraftClaim(
            text=f"Previous result — {fact_sentence(group[0])}", citations=(group[0].source_id,)
        ),
        DraftClaim(
            text=f"Latest result — {fact_sentence(group[1])}", citations=(group[1].source_id,)
        ),
    ]
    first, second = group[0], group[1]
    a, b = _decimal(first.value), _decimal(second.value)
    if first.unit != second.unit:
        claims.append(
            DraftClaim(
                text=(
                    f"The two {analyte} results use different units, so MediMind does not "
                    "calculate a difference."
                ),
                citations=(first.source_id, second.source_id),
                kind="summary",
            )
        )
    elif a is not None and b is not None and first.comparator is None and second.comparator is None:
        delta = b - a
        sign = "+" if delta > 0 else ""
        claims.append(
            DraftClaim(
                text=(
                    f"Difference (latest minus previous): {sign}{delta}"
                    f"{' ' + second.unit if second.unit else ''}. This is arithmetic on the two "
                    "stored values, not a clinical interpretation."
                ),
                citations=(first.source_id, second.source_id),
                kind="calculation",
            )
        )
    return claims


def _best_lines(excerpt: str, terms: Sequence[str], *, fallback: bool) -> list[str]:
    lines = [line.strip() for line in excerpt.splitlines() if line.strip()]
    wanted = [re.compile(r"(?<!\w)" + re.escape(t.casefold()) + r"(?!\w)") for t in terms if t]
    scored = [
        (sum(1 for t in wanted if t.search(line.casefold())), -index, line)
        for index, line in enumerate(lines)
    ]
    hits = [s for s in scored if s[0] > 0]
    # Retrieval already judged the passage relevant (above its threshold); when no line contains a
    # query word, its opening lines are quoted instead. Quotes are always exact lines.
    # A question naming a test (``fallback=False``) quotes only lines that mention it.
    chosen = sorted(hits, reverse=True)[:2] if hits else (scored[:2] if fallback else [])
    return [line[:MAX_LINE_CHARS] for _, _, line in sorted(chosen, key=lambda s: -s[1])]


def _passage_claims(
    passages: Sequence[EvidenceItem], terms: Sequence[str], *, fallback: bool
) -> list[DraftClaim]:
    claims = []
    for item in passages[:MAX_PASSAGE_CLAIMS]:
        lines = _best_lines(item.excerpt, terms, fallback=fallback)
        if not lines:
            continue
        first = next((line.strip() for line in item.excerpt.splitlines() if line.strip()), "")
        if first and first[:MAX_LINE_CHARS] not in lines:  # the page's heading, for context
            lines = [first[:MAX_LINE_CHARS], *lines]
        label = " (identity not confirmed)" if item.identity_check != "match" else ""
        if item.parse_method == "ocr":
            label += " (OCR text, unverified)"
        quoted = " / ".join(f'"{line}"' for line in lines)
        claims.append(
            DraftClaim(
                text=(
                    f"Page {item.page_number} of {item.filename or 'the document'}{label}: {quoted}"
                ),
                citations=(item.source_id,),
                kind="fact",
            )
        )
    return claims


_WORD = re.compile(r"[\w.\-/()]+")


def compose_answer(
    plan: RoutePlan,
    items: Sequence[EvidenceItem],
    *,
    missing: Sequence[str] = (),
    terms: Sequence[str] = (),
) -> DraftAnswer:
    """Claims for the supplied sources, following the plan's intent. Deterministic."""
    facts = [i for i in items if i.kind is SourceKind.FACT]
    passages = [i for i in items if i.kind is SourceKind.PASSAGE]
    claims: list[DraftClaim] = []
    for analyte, group in _by_analyte(facts).items():
        if plan.intent is Intent.COMPARE_PREVIOUS and len(group) == 2:
            claims += _comparison(analyte, group)
            continue
        if plan.intent is Intent.CONFLICTS:
            by_date: dict[object, list[EvidenceItem]] = {}
            for item in group:
                by_date.setdefault(item.effective_date, []).append(item)
            for members in by_date.values():
                claims.append(
                    DraftClaim(
                        text=(
                            f"Conflicting stored results for {analyte} "
                            f"{_when(members[0])}: {' and '.join(_value(i) for i in members)}. "
                            "MediMind does not decide which is correct; a reviewer can verify or "
                            "correct them."
                        ),
                        citations=tuple(i.source_id for i in members),
                        kind="fact",
                    )
                )
            continue
        if plan.intent is Intent.LIST_ANALYTES:
            item = group[-1]
            claims.append(
                DraftClaim(
                    text=f"{analyte}: results are stored; the most recent is {_when(item)}.",
                    citations=(item.source_id,),
                )
            )
            continue
        for item in group:
            claims.append(DraftClaim(text=fact_sentence(item), citations=(item.source_id,)))
        if plan.intent in (Intent.LATEST, Intent.ON_DATE):
            claims += _same_date_note(group)
    if passages:
        fact_pages = {(i.document_id, i.page_number) for i in facts}
        shown = [
            p for p in passages if not fact_pages or (p.document_id, p.page_number) in fact_pages
        ]
        words = list(terms) or [w for a in plan.analytes for w in _WORD.findall(a)]
        claims += _passage_claims(shown, words, fallback=not plan.analytes)
    if not claims:
        return DraftAnswer(status="insufficient_evidence", missing=tuple(missing))
    return DraftAnswer(
        status="partial" if missing else "answered",
        claims=tuple(claims[:20]),
        missing=tuple(missing),
    )
