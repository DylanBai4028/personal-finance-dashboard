"""Parses Amex (Qantas American Express Ultimate Card) statements — the
messiest of the three PDF layouts, so it gets table-aware, position-based
extraction rather than plain-text regex.

Row grouping needs a tolerance, not exact y-position matching: a
transaction's date/amount words and its description words can land ~0.6px
apart in `top`, which independent rounding splits into two separate rows.

Sign isn't marked per row — it's determined by which section a transaction
falls under: "New Payments" reduces the balance (credit), while "New
Standard Transactions" and "Account Charges" increase it (debit). Confirmed
against real statements; no other section type appears in the data.

A transaction can show two dollar figures: a "Foreign Spend Amount" (the
original-currency amount, no currency code printed alongside it) and the
"Amount ($)" actually charged in AUD — the second is what's posted. Amex's
own footnote for these ("AUD X includes conversion commission of AUD Y")
confirms the commission is already folded into the AUD amount, same as
ANZ's equivalent — not a separate transaction.

The Foreign Spend Amount isn't always in AUD's comma-thousands/dot-decimal
format — e.g. '1.239,00' (period-thousands, comma-decimal, as several
European currencies write it) — and no currency code is printed per row to
say which convention applies. It's kept as the raw printed string rather
than parsed to a float, since converting it would mean guessing a locale.
"""

import re
from datetime import datetime

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

_AMOUNT_RE = re.compile(r"^[\d,]+\.\d{2}$")  # AUD, always this format
_FOREIGN_LOOKING_RE = re.compile(r"^[\d.,]*\d[.,]\d{2}$")  # digits with either separator convention
_DESCRIPTION_MAX_X = 374
# Boundary between the Foreign Spend and Amount ($) columns. Values are
# right-aligned, so a wider number (more digits) shifts left — a $5,146.07
# payment landed at x0=502, inside what a naive 505 threshold (the Amount
# header's own x0) would call "foreign spend". Calibrated against the
# widest observed foreign-spend value (x0=401) and narrowest observed
# amount value for a same-width number (x0=502): 470 sits clear of both.
_FOREIGN_MAX_X = 470

_SECTION_STARTS = {
    "New Payments": "payment",
    "New Standard Transactions for": "debit",
    "New Other Transactions for": "debit",
    "Account Charges": "debit",
}
_SECTION_ENDS = ("Total New Payments", "Total of New Standard Transactions",
                  "Total of New Other Transactions", "Total Account Charges")


def _cluster_rows(words, tolerance=3.0):
    """Groups words into visual rows, tolerant of the sub-pixel top-position
    drift between a row's date/amount words and its description words. Chain-
    linked (each word compared against the previous word's top, not the
    row's first word) since a real row's total drift can exceed a single
    fixed tolerance step by step — confirmed against a real statement, where
    the Foreign Spend column's own baseline sits ~2.7px below the date
    column's, which itself sits ~0.6px below the description column's.
    Distinct transaction rows are ~15px+ apart, well clear of this."""
    words = sorted(words, key=lambda w: w["top"])
    rows, current, prev_top = [], [], None
    for w in words:
        if prev_top is None or w["top"] - prev_top <= tolerance:
            current.append(w)
        else:
            rows.append(current)
            current = [w]
        prev_top = w["top"]
    if current:
        rows.append(current)
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def _parse_amount(text):
    return float(text.replace(",", ""))


def parse_summary(page1_text):
    """Reads page 1's declared Opening/Closing Balance and New Credits/Debits."""
    match = re.search(
        r"OPENING BALANCE.*?CLOSING BALANCE\s*\n"
        r"([\d,]+\.\d{2})\s*-\s*([\d,]+\.\d{2})\s*\+\s*([\d,]+\.\d{2})\s*=\s*([\d,]+\.\d{2})",
        page1_text,
    )
    if not match:
        raise ValueError("could not find the account summary on page 1")
    opening, credits, debits, closing = (_parse_amount(g) for g in match.groups())
    return {
        "opening_balance": opening,
        "new_credits": credits,
        "new_debits": debits,
        "closing_balance": closing,
    }


def _is_transaction_start(row):
    """A transaction row starts with a month name then a day number, e.g. 'October 16'."""
    return len(row) >= 2 and row[0]["text"] in MONTHS and row[1]["text"].isdigit()


def _section_for(row_text):
    for prefix, kind in _SECTION_STARTS.items():
        if row_text.startswith(prefix):
            return kind
    return None


def parse_transactions(transaction_pages_words, start_year, end_year):
    """Parses every transaction row across the statement's transaction pages
    (all pages after page 1, which is the summary only).

    Amex prints month + day but never a year per row, and the statement
    period line itself only carries a year next to its end date (e.g.
    'September 17 to October 16, 2022') — never the start date. Transactions
    appear in chronological order, so this starts at start_year and rolls
    forward to end_year the first time the month decreases (a Dec -> Jan
    wrap), the same resolution the ANZ deposit-account parser uses.
    """
    transactions = []
    section = None
    year = start_year
    prev_month = None
    for words in transaction_pages_words:
        for row in _cluster_rows(words):
            texts = [w["text"] for w in row]
            row_text = " ".join(texts)

            new_section = _section_for(row_text)
            if new_section is not None:
                section = new_section
                continue
            if row_text.startswith(_SECTION_ENDS):
                section = None
                continue

            if not _is_transaction_start(row):
                continue
            if section is None:
                raise ValueError(f"transaction row found outside any known section: {row_text!r}")

            month = MONTHS.index(row[0]["text"]) + 1
            day = int(row[1]["text"])
            desc_words = [w["text"] for w in row[2:] if w["x0"] < _DESCRIPTION_MAX_X]

            amount = foreign_amount = None
            for w in row:
                if w["x0"] >= _FOREIGN_MAX_X and _AMOUNT_RE.match(w["text"]):
                    amount = _parse_amount(w["text"])
                elif (
                    _DESCRIPTION_MAX_X <= w["x0"] < _FOREIGN_MAX_X
                    and _FOREIGN_LOOKING_RE.match(w["text"])
                ):
                    foreign_amount = w["text"]  # raw string — see module docstring

            if amount is None:
                raise ValueError(f"transaction row has no Amount ($) value: {row_text!r}")

            if prev_month is not None and month < prev_month:
                year = end_year
            prev_month = month

            transactions.append({
                "date": datetime(year, month, day).date(),
                "description": " ".join(desc_words).strip(),
                "amount": -amount if section == "payment" else amount,
                "foreign_amount": foreign_amount,
                "foreign_currency": None,  # not printed per-row on this statement layout
            })

    return transactions


def reconcile(summary, transactions):
    """Checks extracted transactions against the statement's own declared
    totals. Returns (ok, details) — a mismatch means a parsing bug, not a
    real discrepancy, since these are Amex's own printed figures. No
    per-transaction running balance is printed on this layout, unlike ANZ's
    statements, so this relies on the arithmetic totals alone."""
    total_debits = round(sum(t["amount"] for t in transactions if t["amount"] > 0), 2)
    total_credits = round(-sum(t["amount"] for t in transactions if t["amount"] < 0), 2)
    computed_closing = round(
        summary["opening_balance"] + total_debits - total_credits, 2
    )
    details = {
        "total_debits": (total_debits, summary["new_debits"]),
        "total_credits": (total_credits, summary["new_credits"]),
        "closing_balance": (computed_closing, summary["closing_balance"]),
    }
    ok = all(abs(computed - declared) < 0.01 for computed, declared in details.values())
    return ok, details


def parse_period(page1_text):
    """Extracts the statement period's start/end years, e.g.
    'September 17 to October 16, 2022' -> (2022, 2022), or a period crossing
    a calendar year boundary (e.g. 'December 17 to January 16, 2023') ->
    (2022, 2023) — inferred from start_month > end_month, since Amex never
    prints the start date's year explicitly."""
    match = re.search(r"(\w+) \d{1,2} to (\w+) \d{1,2}, (\d{4})", page1_text)
    if not match:
        raise ValueError("could not find the statement period on page 1")
    start_month_name, end_month_name, end_year = match.groups()
    start_month = MONTHS.index(start_month_name) + 1
    end_month = MONTHS.index(end_month_name) + 1
    end_year = int(end_year)
    start_year = end_year - 1 if start_month > end_month else end_year
    return start_year, end_year


def parse(pdf):
    """Parses a full Amex statement from an already-open pdfplumber.PDF.
    Returns (transactions, ok, reconciliation_details)."""
    page1_text = pdf.pages[0].extract_text() or ""
    summary = parse_summary(page1_text)
    start_year, end_year = parse_period(page1_text)

    transaction_pages_words = [p.extract_words() for p in pdf.pages[1:]]
    transactions = parse_transactions(transaction_pages_words, start_year, end_year)
    ok, details = reconcile(summary, transactions)
    return transactions, ok, details
