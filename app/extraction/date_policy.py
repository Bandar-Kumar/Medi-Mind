"""Date plausibility policy (DATE_POLICY_VERSION; docs/PHASE_3_PLAN.md §7.1).

The extractor never reads the machine clock. The upper bound is an explicit ``reference_date``:
the service passes the document's upload date (``documents.created_at`` as a UTC date, an
immutable column) and the evaluation passes the dataset manifest's fixed ``reference_date``. Both
are recorded in the extraction run, so a re-run gives the same result.
"""

import datetime as dt
from typing import Final

EARLIEST_PLAUSIBLE: Final = dt.date(1900, 1, 1)


def is_plausible(value: dt.date, reference_date: dt.date) -> bool:
    """Within [1900-01-01, reference_date]; a date after the reference date is implausible."""
    return EARLIEST_PLAUSIBLE <= value <= reference_date
