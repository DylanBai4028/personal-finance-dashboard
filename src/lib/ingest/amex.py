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
from datetime import date, timedelta

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


def _parse_date_prefix(row):
    """Returns (month, day, description_words) if `row` starts with a
    transaction date, else None. Handles both 'October 16' (day as its own
    word) and 'December20' (day glued onto the month name with no space —
    confirmed on a real statement, a different font-encoding quirk from the
    rest of the corpus) shapes."""
    if not row:
        return None
    first = row[0]["text"]
    if first in MONTHS:
        if len(row) >= 2 and row[1]["text"].isdigit():
            return MONTHS.index(first) + 1, int(row[1]["text"]), row[2:]
        return None
    for month_name in MONTHS:
        if first.startswith(month_name) and first[len(month_name):].isdigit():
            return MONTHS.index(month_name) + 1, int(first[len(month_name):]), row[1:]
    return None


def _section_for(row_text):
    for prefix, kind in _SECTION_STARTS.items():
        if row_text.startswith(prefix):
            return kind
    return None


_PERIOD_BUFFER_DAYS = 5


def _resolve_year(month, day, start_date, end_date):
    """Picks whichever of start_date's or end_date's year puts (month, day)
    inside [start_date, end_date], with a small buffer on each side — a
    real transaction can be dated a few days before/after the statement's
    nominal period (settlement lag between the purchase date and the date
    Amex includes it), confirmed on a real statement where a December 16
    transaction preceded a "December 17 to January 16" period by one day.
    5 days is nowhere near the ~365-day gap between the two real candidate
    years, so it can't create ambiguity between them.

    Not sequential/stateful (no 'roll forward when the month decreases')
    because Amex doesn't print transactions in one single chronological
    run — confirmed on a real Dec/Jan-crossing statement where recurring
    subscription charges (Vodafone, Spotify) were listed out of order at
    the end of their section, after already-later January dates, which a
    sequential approach mis-years. Checking each transaction's date
    against the statement's actual bounds is correct regardless of print
    order."""
    buffer = timedelta(days=_PERIOD_BUFFER_DAYS)
    for year in {start_date.year, end_date.year}:
        candidate = date(year, month, day)
        if start_date - buffer <= candidate <= end_date + buffer:
            return year
    # Fall back to whichever year is closer, rather than raising — a
    # transaction dated right at the boundary should never actually reach
    # here, but this keeps a plausibly-wrong date from crashing the whole
    # statement over one row.
    return end_date.year


def parse_transactions(transaction_pages_words, start_date, end_date):
    """Parses every transaction row across the statement's transaction pages
    (all pages after page 1, which is the summary only). Amex prints month +
    day but never a year per row; see _resolve_year for how the year is
    determined."""
    transactions = []
    section = None
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

            if row_text.strip() == "CR":
                # A standalone 'CR' marker — its own row, the amount column's
                # baseline drifting from the rest of its transaction's row,
                # same sub-pixel issue _cluster_rows already works around.
                # It always means "this line is a credit", overriding
                # whatever sign its section implied — found on a real
                # statement: a promotional credit ('David Jones Offer') sat
                # under the Account Charges section (normally a debit) but
                # was marked CR, and not forcing it negative broke
                # reconciliation by exactly double its amount. Force rather
                # than flip: idempotent if the transaction (e.g. a New
                # Payments credit) was already negative.
                if transactions:
                    transactions[-1]["amount"] = -abs(transactions[-1]["amount"])
                continue

            date_prefix = _parse_date_prefix(row)
            if date_prefix is None:
                continue
            if section is None:
                raise ValueError(f"transaction row found outside any known section: {row_text!r}")

            month, day, rest = date_prefix
            desc_words = [w["text"] for w in rest if w["x0"] < _DESCRIPTION_MAX_X]

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

            year = _resolve_year(month, day, start_date, end_date)

            transactions.append({
                "date": date(year, month, day),
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
    """Extracts the statement period's start/end dates, e.g.
    'September 17 to October 16, 2022' -> (date(2022,9,17), date(2022,10,16)),
    or a period crossing a calendar year boundary (e.g. 'December 17 to
    January 16, 2023') -> (date(2022,12,17), date(2023,1,16)) — the start
    year is inferred from start_month > end_month, since Amex never prints
    the start date's year explicitly."""
    # 'to' and the following month are sometimes glued together with no
    # space in the extracted text (e.g. 'December 17 toJanuary 16, 2024',
    # confirmed on a real statement) — \s* rather than a literal space here.
    match = re.search(r"(\w+) (\d{1,2}) to\s*(\w+) (\d{1,2}), (\d{4})", page1_text)
    if not match:
        raise ValueError("could not find the statement period on page 1")
    start_month_name, start_day, end_month_name, end_day, end_year = match.groups()
    start_month = MONTHS.index(start_month_name) + 1
    end_month = MONTHS.index(end_month_name) + 1
    end_year = int(end_year)
    start_year = end_year - 1 if start_month > end_month else end_year
    return date(start_year, start_month, int(start_day)), date(end_year, end_month, int(end_day))


def parse(pdf):
    """Parses a full Amex statement from an already-open pdfplumber.PDF.
    Returns (transactions, ok, reconciliation_details)."""
    page1_text = pdf.pages[0].extract_text() or ""
    summary = parse_summary(page1_text)
    start_date, end_date = parse_period(page1_text)

    transaction_pages_words = [p.extract_words() for p in pdf.pages[1:]]
    transactions = parse_transactions(transaction_pages_words, start_date, end_date)
    ok, details = reconcile(summary, transactions)
    return transactions, ok, details
