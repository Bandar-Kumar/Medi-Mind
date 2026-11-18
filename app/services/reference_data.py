"""Copy the versioned reference CSVs into the database (docs/PHASE_3_PLAN.md §5.7).

``python -m app seed-reference`` runs this once after ``migrate``, and again after the reference
data changes. It is idempotent and never changes the meaning of an existing row. Reference data
holds no patient data.
"""

from app.extraction.reference import load_reference
from app.records.facts import AnalyteSeed, SeedResult, seed_reference_data


def seed_reference() -> tuple[SeedResult, str]:
    """Seed analytes, aliases and conversions; returns the result and the reference version."""
    reference = load_reference()
    analytes = [
        AnalyteSeed(a.canonical_name, a.default_unit, a.plausible_min, a.plausible_max)
        for a in reference.analytes.values()
    ]
    conversions = [
        (name, from_unit, to_unit, c.factor, c.addend)
        for (name, from_unit, to_unit), c in reference.conversions.items()
    ]
    return seed_reference_data(analytes, reference.alias_rows, conversions), reference.version
