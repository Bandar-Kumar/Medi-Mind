"""Analyte mapping (docs/PHASE_3_PLAN.md §7.5): exact alias match only.

No fuzzy, substring or phonetic matching, so ``Direct Bilirubin`` can never map to ``Total
Bilirubin``. An unmapped name keeps ``analyte_raw`` and is flagged ``unmapped_analyte``. No
terminology codes are assigned (D-6).
"""

from app.extraction.reference import Analyte, Reference, analyte_key

ALIAS_EXACT = "alias_exact"


def map_analyte(analyte_raw: str, reference: Reference) -> Analyte | None:
    name = reference.aliases.get(analyte_key(analyte_raw))
    return reference.analytes[name] if name is not None else None
