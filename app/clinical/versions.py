"""Version stamps of everything that decides a clinical fact (docs/PHASE_6_PLAN.md).

Any change to a rule, vocabulary, threshold or output format of the matching component must bump
its version. The medication lexicon is versioned by content (``app.clinical.lexicon``).
"""

from typing import Final

CLINICAL_CLASSIFIER_VERSION: Final = "clinical-doctype-1"
CLINICAL_EXTRACTOR_VERSION: Final = "clinical-rules-1"
CLINICAL_NORMALIZER_VERSION: Final = "clinical-norm-1"
CLINICAL_COMPARE_VERSION: Final = "clinical-compare-1"
CLINICAL_QA_VERSION: Final = "clinical-qa-1"
