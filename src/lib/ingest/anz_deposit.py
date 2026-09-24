"""Parses ANZ deposit-account statements — Access Advantage and Online Saver
share the exact same table format, so one parser handles both.

These statements have no PDF gridlines; the transaction table is recovered
from each word's x-position, calibrated against the header row's own column
starts (Date / Withdrawals ($) / Deposits ($) / Balance ($)). ANZ prints the
literal word "blank" in an empty Withdrawals or Deposits cell rather than
leaving it blank, which this parser relies on to distinguish an empty cell
from a missing one.
"""

import re
from collections import defaultdict
from datetime import date

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_DAY_RE = re.compile(r"^\d{2}$")
_AMOUNT_RE = re.compile(r"^-?[\d,]+\.\d{2}$")

# Column right-edges, calibrated against real statements: Withdrawals values
# (including the literal "blank") land ~x0 360-380; Deposits ~x0 415-460;
# Balance ~x0 515-550. Header label x0s (Date 42, Withdrawals 327,
# Deposits 417, Balance 516) don't work directly as boundaries since
# right-aligned values can sit past the next label's start.
_WITHDRAWAL_MAX_X = 400
_DEPOSIT_MAX_X = 500


def _group_lines(words):
    """Groups words into visual rows by rounded y-position, each row sorted left to right."""
    lines = defaultdict(list)
    for w in words:
        lines[round(w["top"])].append(w)
    return [sorted(lines[top], key=lambda w: w["x0"]) for top in sorted(lines)]


def _parse_amount(token):
    return float(token.replace(",", "").lstrip("$"))


def _find_summary_value(lines, label_tokens):
    """Finds a row containing every token in `label_tokens`, then returns the
    last dollar-amount token on the nearest following row that has one. The
    value isn't always on the very next row — the address block (a separate
    column at overlapping y-positions) can land a row in between."""
    for i, row in enumerate(lines):
        texts = [w["text"] for w in row]
        if all(tok in texts for tok in label_tokens):
            for next_row in lines[i + 1:i + 5]:
                amounts = [w["text"] for w in next_row if w["text"].startswith("$")]
                if amounts:
                    return _parse_amount(amounts[-1])
    raise ValueError(f"could not find summary value for {label_tokens!r}")


def parse_summary(page1_words):
    """Reads page 1's declared Opening/Closing Balance and Total Deposits/Withdrawals —
    the figures this statement's own transactions must reconcile against."""
    lines = _group_lines(page1_words)
    return {
        "opening_balance": _find_summary_value(lines, ["Opening", "Balance:"]),
        "total_deposits": _find_summary_value(lines, ["Total", "Deposits:"]),
        "total_withdrawals": _find_summary_value(lines, ["Total", "Withdrawals:"]),
        "closing_balance": _find_summary_value(lines, ["Closing", "Balance:"]),
    }


_PERIOD_RE = re.compile(r"\d{2} [A-Z]+ (\d{4}) TO \d{2} [A-Z]+ (\d{4})")


def parse_period(page1_text):
    """Extracts the statement period's start/end years from the header line,
    e.g. '05 MAY 2022 TO 05 JULY 2022' -> (2022, 2022). ANZ prints day+month
    only on each transaction row, never a year, so this anchors year
    resolution for statements that don't cross a calendar-year boundary, and
    seeds it for ones that do.

    Matched against the specific 'DD MONTH YYYY TO DD MONTH YYYY' sentence,
    not scanned for any '20xx'-shaped number on the page — a real bug found
    via a real statement: Dylan's own postcode (Darlinghurst NSW 2010) also
    matches a bare \\b20\\d{2}\\b pattern and, being the last such match on
    the page, silently became the 'end year', producing a February 29 date
    in the non-leap year 2010."""
    match = _PERIOD_RE.search(page1_text)
    if not match:
        raise ValueError("could not find statement period years on page 1")
    return int(match.group(1)), int(match.group(2))


def _is_transaction_start(row):
    """A transaction row starts with a 2-digit day then a 3-letter month, e.g. '21 JUN'."""
    return len(row) >= 2 and bool(_DAY_RE.match(row[0]["text"])) and row[1]["text"] in MONTHS


def _classify_amount(word):
    """Returns ('withdrawal' | 'deposit' | 'balance', amount) for an amount-shaped
    word, or None if the word isn't a real amount (e.g. the literal 'blank')."""
    text = word["text"]
    if text == "blank" or not _AMOUNT_RE.match(text.lstrip("$")):
        return None
    amount = _parse_amount(text)
    if word["x0"] < _WITHDRAWAL_MAX_X:
        return "withdrawal", amount
    if word["x0"] < _DEPOSIT_MAX_X:
        return "deposit", amount
    return "balance", amount


def parse_transactions(transaction_pages_words, start_year, end_year):
    """Parses every transaction row across the statement's transaction pages
    (i.e. all pages after page 1, which is the summary only).

    Each transaction spans one dated row (date, start of description, one of
    withdrawal/deposit, running balance) plus zero or more continuation rows
    of extra description text (merchant address lines, "EFFECTIVE DATE ...").
    Rows with neither a withdrawal nor a deposit (e.g. "OPENING BALANCE") are
    informational, not real transactions, and are skipped.
    """
    rows = []
    for words in transaction_pages_words:
        rows.extend(_group_lines(words))

    raw_transactions = []
    current = None
    for row in rows:
        first_text = row[0]["text"] if row else ""
        if first_text in ("TOTALS", "Page", "Date"):
            continue  # per-page subtotal / page footer / repeated header row

        if _is_transaction_start(row):
            if current is not None:
                raw_transactions.append(current)

            day = int(row[0]["text"])
            month = MONTHS[row[1]["text"]]
            desc_words = [
                w["text"] for w in row[2:]
                if w["x0"] < _WITHDRAWAL_MAX_X and w["text"] != "blank"
            ]
            withdrawal = deposit = balance = None
            for w in row:
                classified = _classify_amount(w)
                if classified is None:
                    continue
                kind, amount = classified
                if kind == "withdrawal":
                    withdrawal = amount
                elif kind == "deposit":
                    deposit = amount
                else:
                    balance = amount

            if withdrawal is None and deposit is None:
                current = None  # informational row, not a transaction
                continue

            current = {
                "day": day,
                "month": month,
                "description": " ".join(desc_words),
                "amount": -withdrawal if withdrawal is not None else deposit,
                "balance": balance,
            }
        elif current is not None:
            extra = [w["text"] for w in row if w["x0"] < _WITHDRAWAL_MAX_X]
            if extra:
                current["description"] += " " + " ".join(extra)

    if current is not None:
        raw_transactions.append(current)

    # Resolve each row's year: transactions appear in chronological order, so
    # start at start_year and roll forward to end_year the first time the
    # month decreases (a Dec -> Jan wrap).
    year = start_year
    prev_month = None
    transactions = []
    for t in raw_transactions:
        if prev_month is not None and t["month"] < prev_month:
            year = end_year
        prev_month = t["month"]
        transactions.append({
            "date": date(year, t["month"], t["day"]),
            "description": t["description"].strip(),
            "amount": t["amount"],
            "balance": t["balance"],
        })
    return transactions


def reconcile(summary, transactions):
    """Checks extracted transactions against the statement's own declared
    totals. Returns (ok, details) — a mismatch means a parsing bug, not a
    real discrepancy, since these are the bank's own printed figures."""
    total_deposits = round(sum(t["amount"] for t in transactions if t["amount"] > 0), 2)
    total_withdrawals = round(-sum(t["amount"] for t in transactions if t["amount"] < 0), 2)
    computed_closing = round(summary["opening_balance"] + total_deposits - total_withdrawals, 2)
    last_balance = round(transactions[-1]["balance"], 2) if transactions else None

    details = {
        "total_deposits": (total_deposits, summary["total_deposits"]),
        "total_withdrawals": (total_withdrawals, summary["total_withdrawals"]),
        "closing_balance": (computed_closing, summary["closing_balance"]),
        "last_transaction_balance": (last_balance, summary["closing_balance"]),
    }
    ok = all(abs(computed - declared) < 0.01 for computed, declared in details.values())
    return ok, details


def parse(pdf):
    """Parses a full ANZ deposit-account statement (Access Advantage or Online
    Saver) from an already-open pdfplumber.PDF. Returns (transactions, ok,
    reconciliation_details)."""
    page1_words = pdf.pages[0].extract_words()
    page1_text = pdf.pages[0].extract_text() or ""
    summary = parse_summary(page1_words)
    start_year, end_year = parse_period(page1_text)
    transaction_pages_words = [p.extract_words() for p in pdf.pages[1:]]
    transactions = parse_transactions(transaction_pages_words, start_year, end_year)
    ok, details = reconcile(summary, transactions)
    return transactions, ok, details
