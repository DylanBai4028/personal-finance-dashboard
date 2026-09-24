"""Ingests every PDF statement sitting in data/landing/: detects its format,
matches it to a canonical account via rules/accounts.local.yaml, parses it,
checks reconciliation, dedupes against transactions already processed for
that account, then archives the source PDF — to data/processed/ on success,
or to data/needs_review/ (with a companion .error.txt explaining why) on a
detection, account-matching, or reconciliation failure. A bad statement is
flagged once, not re-parsed and re-flagged on every subsequent run.

Parsed, deduped transactions are written alongside the archived PDF as
<name>.json for categorize_2.py to read in the next pipeline stage.
"""

import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pdfplumber
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lib.ingest import amex, anz_credit_card, anz_deposit, dedupe, detect
from lib.ingest.detect import StatementType

LANDING = ROOT / "data" / "landing"
PROCESSED = ROOT / "data" / "processed"
NEEDS_REVIEW = ROOT / "data" / "needs_review"
ACCOUNTS_CONFIG = ROOT / "rules" / "accounts.local.yaml"

_PARSERS = {
    StatementType.ANZ_ACCESS_ADVANTAGE: "anz_deposit",
    StatementType.ANZ_ONLINE_SAVER: "anz_deposit",
    StatementType.ANZ_CREDIT_CARD: "anz_credit_card",
    StatementType.AMEX: "amex",
}


def _load_account_matchers():
    data = yaml.safe_load(ACCOUNTS_CONFIG.read_text())
    return data["accounts"]


def _account_name_for(identified_match, matchers):
    for entry in matchers:
        if entry["match"] == identified_match:
            return entry["name"]
    return None


def _serialize(txn):
    """Converts a parsed transaction dict to a JSON-safe form (dates -> ISO strings)."""
    out = dict(txn)
    out["date"] = txn["date"].isoformat()
    return out


def _reject(pdf_path, reason):
    NEEDS_REVIEW.mkdir(parents=True, exist_ok=True)
    dest = NEEDS_REVIEW / pdf_path.name
    shutil.move(str(pdf_path), dest)
    (NEEDS_REVIEW / f"{pdf_path.stem}.error.txt").write_text(reason + "\n")
    print(f"NEEDS REVIEW: {pdf_path.name} — {reason}")


def _accept(pdf_path, account_name, transactions, extra_fields=None):
    PROCESSED.mkdir(parents=True, exist_ok=True)
    output = {
        "source_file": pdf_path.name,
        "account_name": account_name,
        "transactions": [_serialize(t) for t in transactions],
    }
    if extra_fields:
        output.update(extra_fields)
    (PROCESSED / f"{pdf_path.stem}.json").write_text(json.dumps(output, indent=2))
    shutil.move(str(pdf_path), PROCESSED / pdf_path.name)
    print(f"processed: {pdf_path.name} -> {account_name} ({len(transactions)} new transactions)")


def _load_seen_keys(account_name):
    """Rebuilds the set of already-processed transaction keys for an account
    from every JSON output already sitting in data/processed/ — no separate
    index file to keep in sync, self-heals if a processed file is removed."""
    seen = set()
    if not PROCESSED.exists():
        return seen
    for json_path in PROCESSED.glob("*.json"):
        data = json.loads(json_path.read_text())
        if data["account_name"] != account_name:
            continue
        for t in data["transactions"]:
            txn = dict(t)
            txn["date"] = date.fromisoformat(txn["date"])
            seen.add(dedupe.transaction_key(account_name, txn))
    return seen


def _parse(statement_type, pdf):
    kind = _PARSERS[statement_type]
    if kind == "anz_deposit":
        transactions, ok, details = anz_deposit.parse(pdf)
        return transactions, ok, details, {}
    if kind == "anz_credit_card":
        transactions, credit_limit, ok, details = anz_credit_card.parse(pdf)
        return transactions, ok, details, {"credit_limit": credit_limit}
    if kind == "amex":
        transactions, ok, details = amex.parse(pdf)
        return transactions, ok, details, {}
    raise ValueError(f"no parser registered for {statement_type!r}")


def ingest_all():
    matchers = _load_account_matchers()
    seen_keys_by_account = {}

    pdf_paths = sorted(LANDING.glob("*.pdf"))
    if not pdf_paths:
        print("data/landing/ is empty — nothing to ingest")
        return

    for pdf_path in pdf_paths:
        with pdfplumber.open(pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""
            statement_type = detect.detect(first_page_text)
            if statement_type is None:
                _reject(pdf_path, "unrecognized statement format (no known header marker matched)")
                continue

            try:
                identified = detect.identify_account(statement_type, first_page_text)
            except ValueError as e:
                _reject(pdf_path, f"could not identify account: {e}")
                continue

            account_name = _account_name_for(identified, matchers)
            if account_name is None:
                _reject(pdf_path, f"no accounts.local.yaml entry matches {identified}")
                continue

            try:
                transactions, ok, details, extra_fields = _parse(statement_type, pdf)
            except ValueError as e:
                _reject(pdf_path, f"parsing failed: {e}")
                continue

            if not ok:
                mismatches = {k: v for k, v in details.items() if abs(v[0] - v[1]) >= 0.01}
                _reject(pdf_path, f"reconciliation failed: {mismatches}")
                continue

        if account_name not in seen_keys_by_account:
            seen_keys_by_account[account_name] = _load_seen_keys(account_name)

        kept, updated_keys = dedupe.dedupe(
            account_name, transactions, seen_keys_by_account[account_name]
        )
        seen_keys_by_account[account_name] = updated_keys
        _accept(pdf_path, account_name, kept, extra_fields)


if __name__ == "__main__":
    ingest_all()
