"""Version stamps of everything that decides an extracted fact (docs/PHASE_3_PLAN.md §5.1).

Any change to a rule, vocabulary, threshold or output format of the matching component must bump
its version. Reference data is versioned by content (``app.extraction.reference``).
"""

from typing import Final

EXTRACTOR_VERSION: Final = "lab-rules-2"  # header roles, row/line rules, subject guard, checks
NORMALIZER_VERSION: Final = "norm-1"  # numbers, units, ranges, analyte keys, range comparison
DATE_POLICY_VERSION: Final = "dates-1"  # date formats and plausibility bounds
COMPARE_VERSION: Final = "compare-1"  # observation comparability and conflict grouping
REGION_PROFILE: Final = "IN"  # ADR-025

# No model takes part in rule-based extraction; the columns are NOT NULL, so they say so.
PROMPT_VERSION_NONE: Final = "none"
MODEL_ID_NONE: Final = "none"
