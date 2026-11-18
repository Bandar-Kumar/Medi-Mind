"""Unit normalization (docs/PHASE_3_PLAN.md §7.3). Additive: ``unit_raw`` is never changed.

1. Spelling: the printed unit maps to a canonical spelling through the reference synonyms.
2. Generic scale: units on one base (``/µL``, ``10^5/µL`` = lakhs/cumm, ``10^6/µL``) convert by
   powers of ten, exactly.
3. Analyte-specific conversion: only reference rows with a cited source, only toward the
   analyte's default unit.
Otherwise the value keeps its own canonical unit, with the note ``unit_not_convertible``.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.extraction.normalize.numbers import text_key
from app.extraction.reasons import QualityNote, ReviewReason
from app.extraction.reference import Analyte, Reference


@dataclass(frozen=True)
class NormalizedUnit:
    unit_norm: str | None
    value_num_norm: Decimal | None
    reasons: tuple[ReviewReason, ...] = ()
    notes: tuple[QualityNote, ...] = ()


def canonical_unit(raw: str, reference: Reference) -> str | None:
    return reference.unit_synonyms.get(text_key(raw))


def normalize_unit(
    unit_raw: str | None,
    value_num: Decimal | None,
    analyte: Analyte | None,
    reference: Reference,
) -> NormalizedUnit:
    """Canonical unit and value in the analyte's default unit when an exact rule exists."""
    if unit_raw is None or not unit_raw.strip():
        if analyte is not None and not analyte.unitless and value_num is not None:
            return NormalizedUnit(None, None, (ReviewReason.MISSING_UNIT,))
        return NormalizedUnit(None, None)
    unit = canonical_unit(unit_raw, reference)
    if unit is None:
        return NormalizedUnit(None, None, (ReviewReason.UNKNOWN_UNIT,))
    if value_num is None:
        return NormalizedUnit(unit, None)
    if analyte is None or not analyte.default_unit:
        return NormalizedUnit(unit, value_num, notes=(QualityNote.UNIT_NOT_CONVERTIBLE,))
    target = analyte.default_unit
    if unit == target:
        return NormalizedUnit(unit, value_num)
    scale_from = reference.unit_scales.get(unit)
    scale_to = reference.unit_scales.get(target)
    if scale_from and scale_to and scale_from[0] == scale_to[0]:
        power = scale_from[1] - scale_to[1]
        factor = Decimal(10) ** power if power >= 0 else Decimal(1) / Decimal(10) ** -power
        return NormalizedUnit(target, value_num * factor)
    conversion = reference.conversions.get((analyte.canonical_name, unit, target))
    if conversion is not None:
        return NormalizedUnit(target, value_num * conversion.factor + conversion.addend)
    return NormalizedUnit(unit, value_num, notes=(QualityNote.UNIT_NOT_CONVERTIBLE,))
