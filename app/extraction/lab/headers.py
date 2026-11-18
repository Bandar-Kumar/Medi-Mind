"""Column-header roles of lab result tables (``lab-rules-1``; docs/PHASE_3_PLAN.md §4.3).

Exact vocabulary match on the header text (NFKC, lower case, collapsed whitespace, trailing ``:``
or ``.`` removed). A header that is not in the vocabulary has no role; its column is never read.
``historical`` columns (previous results) are recognised so they are never extracted.
"""

from app.extraction.normalize.numbers import text_key

NAME = "name"
VALUE = "value"
UNIT = "unit"
RANGE = "range"
FLAG = "flag"
METHOD = "method"
SPECIMEN = "specimen"
HISTORICAL = "historical"

ROLES: dict[str, str] = {
    "test": NAME,
    "test name": NAME,
    "test description": NAME,
    "investigation": NAME,
    "investigations": NAME,
    "parameter": NAME,
    "parameters": NAME,
    "result": VALUE,
    "results": VALUE,
    "observed value": VALUE,
    "value": VALUE,
    "unit": UNIT,
    "units": UNIT,
    "reference range": RANGE,
    "biological reference interval": RANGE,
    "biological ref. interval": RANGE,
    "normal range": RANGE,
    "ref. range": RANGE,
    "reference interval": RANGE,
    "flag": FLAG,
    "method": METHOD,
    "specimen": SPECIMEN,
    "sample": SPECIMEN,
    "previous": HISTORICAL,
    "previous result": HISTORICAL,
    "previous value": HISTORICAL,
    "last value": HISTORICAL,
    "last result": HISTORICAL,
    "prev.": HISTORICAL,
    "prev": HISTORICAL,
}


def header_role(text: str) -> str | None:
    key = text_key(text)
    return ROLES.get(key) or ROLES.get(key.rstrip(":. "))
