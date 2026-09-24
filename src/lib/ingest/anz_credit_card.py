"""Parses ANZ Frequent Flyer Black (and future ANZ cards) credit card statements.

Unlike the deposit-account statements, this table is mostly one line per
transaction in extract_text() output, so this parser works from plain text
lines rather than word positions. Three line shapes appear:
  - a normal transaction: two dates, last-4 card digits, description,
    AUD amount (optionally suffixed "CR" for a payment/credit/refund), balance
  - a foreign-currency purchase's metadata line, e.g. "93.99 EUR" — the
    AUD amount on the transaction line above is what was actually charged;
    this line is captured as foreign_amount/foreign_currency metadata, not
    netted into the posted amount
  - an "INCL OVERSEAS TXN FEE" line — disclosure only, not a separate debit.
    Confirmed against real statements: its printed balance never moves from
    the preceding transaction's balance, and excluding it from the debit
    total is what makes the statement's declared total reconcile — the fee
    is already folded into the AUD amount on the purchase line above, unlike
    Amex's equivalent (which architecture.md documents as its own line item;
    don't assume the two banks behave the same way without checking each
    against real data).
"""

import re
from datetime import datetime

_TXN_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+\d{2}/\d{2}/\d{4}\s+\d{4}\s+(.+?)\s+"
    r"\$([\d,]+\.\d{2})(\s*C\s?R)?\s+\$([\d,]+\.\d{2})$"
)
_FX_FEE_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+(INCL OVERSEAS TXN FEE)\s+([\d,]+\.\d{2})\s+AUD\s+\$([\d,]+\.\d{2})$"
)
_FOREIGN_AMOUNT_RE = re.compile(r"^([\d,]+\.\d{2})\s+([A-Z]{3})$")

_SUMMARY_FIELDS = {
    "credit_limit": r"Credit Limit\s+\$([\d,]+\.\d{2})",
    "opening_balance": r"Opening Balance\s+\$([\d,]+\.\d{2})",
    "debits": r"Purchases, Cash Advances & Other Debits\s+\$([\d,]+\.\d{2})",
    "interest_charges": r"Interest Charges\s+\$([\d,]+\.\d{2})",
    "credits": r"Payments & Other Credits\s+\$([\d,]+\.\d{2})",
    "closing_balance": r"Closing Balance\s+\$([\d,]+\.\d{2})",
}


def _parse_amount(text):
    return float(text.replace(",", ""))


def parse_summary(page1_text):
    """Reads page 1's declared Credit Limit, Opening/Closing Balance, and
    the debit/credit totals this statement's transactions must reconcile
    against."""
    values = {}
    for field, pattern in _SUMMARY_FIELDS.items():
        match = re.search(pattern, page1_text)
        if not match:
            raise ValueError(f"could not find {field!r} on page 1")
        values[field] = _parse_amount(match.group(1))
    return values


def parse_transactions(transaction_pages_text):
    """Parses every transaction line across the statement's transaction pages
    (all pages after page 1, which is the summary only)."""
    transactions = []
    for page_text in transaction_pages_text:
        for line in page_text.splitlines():
            line = line.strip()

            txn_match = _TXN_RE.match(line)
            if txn_match:
                date_str, description, amount_str, cr_flag, balance_str = txn_match.groups()
                amount = _parse_amount(amount_str)
                if cr_flag:
                    amount = -amount
                transactions.append({
                    "date": datetime.strptime(date_str, "%d/%m/%Y").date(),
                    "description": description.strip(),
                    "amount": amount,
                    "balance": _parse_amount(balance_str),
                    "foreign_amount": None,
                    "foreign_currency": None,
                    "included_fx_fee": None,
                })
                continue

            fee_match = _FX_FEE_RE.match(line)
            if fee_match and transactions:
                # Not a new transaction — disclosure of how much of the
                # preceding purchase's AUD amount was the conversion fee.
                _, _, amount_str, _ = fee_match.groups()
                transactions[-1]["included_fx_fee"] = _parse_amount(amount_str)
                continue

            foreign_match = _FOREIGN_AMOUNT_RE.match(line)
            if foreign_match and transactions:
                # Metadata for the immediately preceding transaction (the
                # purchase itself, not the FX fee line that follows it).
                amount_str, currency = foreign_match.groups()
                transactions[-1]["foreign_amount"] = _parse_amount(amount_str)
                transactions[-1]["foreign_currency"] = currency

    return transactions


def reconcile(summary, transactions):
    """Checks extracted transactions against the statement's own declared
    totals. Returns (ok, details) — a mismatch means a parsing bug, not a
    real discrepancy, since these are the bank's own printed figures."""
    total_debits = round(sum(t["amount"] for t in transactions if t["amount"] > 0), 2)
    total_credits = round(-sum(t["amount"] for t in transactions if t["amount"] < 0), 2)
    declared_debits = round(summary["debits"] + summary["interest_charges"], 2)
    computed_closing = round(
        summary["opening_balance"] + total_debits - total_credits, 2
    )
    last_balance = round(transactions[-1]["balance"], 2) if transactions else None

    details = {
        "total_debits": (total_debits, declared_debits),
        "total_credits": (total_credits, summary["credits"]),
        "closing_balance": (computed_closing, summary["closing_balance"]),
        "last_transaction_balance": (last_balance, summary["closing_balance"]),
    }
    ok = all(abs(computed - declared) < 0.01 for computed, declared in details.values())
    return ok, details


def parse(pdf):
    """Parses a full ANZ credit card statement from an already-open
    pdfplumber.PDF. Returns (transactions, credit_limit, ok, reconciliation_details)."""
    page1_text = pdf.pages[0].extract_text() or ""
    summary = parse_summary(page1_text)
    transaction_pages_text = [p.extract_text() or "" for p in pdf.pages[1:]]
    transactions = parse_transactions(transaction_pages_text)
    ok, details = reconcile(summary, transactions)
    return transactions, summary["credit_limit"], ok, details
