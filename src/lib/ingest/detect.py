"""Identifies which of the four known statement formats a PDF is, from its page-1 header text.

A PDF matching none of these markers is unrecognized and should be routed to
data/needs_review/ by the caller, rather than skipped silently or crashing
the whole batch.
"""

from enum import Enum


class StatementType(Enum):
    ANZ_ACCESS_ADVANTAGE = "anz_access_advantage"
    ANZ_ONLINE_SAVER = "anz_online_saver"
    ANZ_CREDIT_CARD = "anz_credit_card"
    AMEX = "amex"


# Order matters only in that each marker must be unambiguous on its own —
# these four strings don't overlap across statement types.
_MARKERS = {
    StatementType.ANZ_ACCESS_ADVANTAGE: "ANZ ACCESS ADVANTAGE STATEMENT",
    StatementType.ANZ_ONLINE_SAVER: "ANZ ONLINE SAVER STATEMENT",
    StatementType.ANZ_CREDIT_CARD: "ANZ FREQUENT FLYER BLACK",
    StatementType.AMEX: "Statement of Account",
}


def detect(first_page_text: str) -> StatementType | None:
    """Returns the matching statement type, or None if no known marker is found."""
    for statement_type, marker in _MARKERS.items():
        if marker in first_page_text:
            return statement_type
    return None
