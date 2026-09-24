"""Identifies which of the four known statement formats a PDF is, from its page-1 header text.

A PDF matching none of these markers is unrecognized and should be routed to
data/needs_review/ by the caller, rather than skipped silently or crashing
the whole batch.
"""

import re
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


def identify_account(statement_type: StatementType, first_page_text: str) -> dict:
    """Extracts the identifying fields for this statement's account, in the
    same shape as a rules/accounts.local.yaml entry's `match` block, so the
    caller can look up which canonical ledger account it belongs to.
    Raises ValueError if the expected identifier isn't found — a statement
    this parser doesn't understand yet, not to be guessed at."""
    if statement_type in (StatementType.ANZ_ACCESS_ADVANTAGE, StatementType.ANZ_ONLINE_SAVER):
        bsb = re.search(r"(\d{3}-\d{3})\s*\$", first_page_text)
        acct = re.search(r"(\d{4}-\d{5})\b", first_page_text)
        if not bsb or not acct:
            raise ValueError("could not find BSB/account number on page 1")
        return {"bsb": bsb.group(1), "account_number": acct.group(1)}

    if statement_type is StatementType.ANZ_CREDIT_CARD:
        acct = re.search(r"ACCOUNT NUMBER:\s*(\d{4}-\d{4}-\d{4}-\d{4})", first_page_text)
        if not acct:
            raise ValueError("could not find account number on page 1")
        return {"account_number": acct.group(1)}

    if statement_type is StatementType.AMEX:
        member = re.search(r"Membership Number\s+([\dX\-]+)", first_page_text)
        if not member:
            raise ValueError("could not find membership number on page 1")
        return {"membership_number": member.group(1)}

    raise ValueError(f"no account-identification rule for {statement_type!r}")
