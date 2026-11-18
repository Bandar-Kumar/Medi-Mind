"""Automated checks on extracted observations (docs/PHASE_3_PLAN.md §4.5).

Checks flag, never fix: plausibility (values outside the reference sanity bounds, in the default
unit only), printed flag vs the derived range comparison, and conflicting values for the same
analyte and date within one document. Passing every check gives ``validated``, which means only
that automated checks passed: not human verification and not clinical validation (§6.8).
"""

from collections import defaultdict
from collections.abc import Sequence

from app.extraction.normalize.numbers import HIGH_FLAGS, LOW_FLAGS
from app.extraction.normalize.ranges import ABOVE, BELOW
from app.extraction.reasons import ReviewReason
from app.extraction.reference import Analyte, Reference
from app.extraction.schemas import NEEDS_REVIEW, VALIDATED, ObservationDraft


def implausible(draft: ObservationDraft, analyte: Analyte | None) -> bool:
    if analyte is None or draft.value_num_norm is None:
        return False
    if draft.unit_norm != analyte.default_unit:
        return False
    value = draft.value_num_norm
    if analyte.plausible_min is not None and value < analyte.plausible_min:
        return True
    return analyte.plausible_max is not None and value > analyte.plausible_max


def flag_disagrees(flag: str | None, comparison: str | None) -> bool:
    """The printed flag contradicts the comparison against the printed range."""
    if flag is None or comparison is None:
        return False
    if flag in HIGH_FLAGS:
        return comparison != ABOVE
    if flag in LOW_FLAGS:
        return comparison != BELOW
    return False


def _value_key(draft: ObservationDraft) -> tuple:
    return (draft.comparator, draft.value_num_norm, draft.unit_norm, draft.value_qualitative)


def with_document_checks(
    drafts: Sequence[ObservationDraft], reference: Reference
) -> tuple[ObservationDraft, ...]:
    """Add ``conflicting_value_in_document`` and set the final status of each draft."""
    groups: dict[tuple, list[int]] = defaultdict(list)
    for index, draft in enumerate(drafts):
        if draft.analyte_name is not None:
            groups[(draft.analyte_name, draft.effective_date, draft.effective_time)].append(index)
    conflicting: set[int] = set()
    for members in groups.values():
        if len({_value_key(drafts[i]) for i in members}) > 1:
            conflicting.update(members)
    result = []
    for index, draft in enumerate(drafts):
        reasons = set(draft.review_reasons)
        if index in conflicting:
            reasons.add(ReviewReason.CONFLICTING_VALUE_IN_DOCUMENT.value)
        ordered = tuple(sorted(reasons))
        status = NEEDS_REVIEW if ordered else VALIDATED
        result.append(draft.model_copy(update={"review_reasons": ordered, "review_status": status}))
    return tuple(result)
