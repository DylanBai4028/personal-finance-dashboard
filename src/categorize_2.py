"""Categorizes ingested transactions into balanced double-entry postings.

For each transaction, tries in order:
  1. Internal-transfer detection — if the description contains another
     tracked account's own identifying number, it's a transfer between two
     of Dylan's own accounts, not a category.
  2. Category rules (from --rules) — most-specific match wins (the longest
     matching pattern), not file order.
  3. The Claude Code CLI fallback (lib/llm_categorize.py) for anything still
     unmatched, batched once per run. Each new decision is written back to
     the same --rules file as a `source: llm` rule, cached by normalized
     description within the run so a repeat merchant costs one call, not one
     per transaction.

Writes one balanced posting pair per transaction: the source account (sign
as ingest_1.py parsed it) and the resolved target (a category or the other
side of a transfer), which is always the exact negation — every
transaction's postings sum to zero. Income postings are credit-normal
(negative); see ledger/schema.sql's v_monthly_income_expense for the
display-side sign flip.

Runs incrementally: only processes data/processed/*.json files that don't
already have a sibling *.categorized.json, so re-running the pipeline never
re-categorizes (and never re-spends LLM calls on) the same transactions.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lib import llm_categorize

PROCESSED = ROOT / "data" / "processed"
ACCOUNTS_CONFIG = ROOT / "rules" / "accounts.local.yaml"

# Starting taxonomy — the LLM fallback expands this over time as it promotes
# new `source: llm` rules, but a live ledger needs a reasonable baseline to
# choose from on day one.
DEFAULT_CANDIDATE_ACCOUNTS = [
    "Expenses:Food:Groceries", "Expenses:Food:Dining", "Expenses:Transport",
    "Expenses:Housing:Rent", "Expenses:Utilities", "Expenses:Entertainment",
    "Expenses:Shopping", "Expenses:Health", "Expenses:Travel",
    "Expenses:Subscriptions", "Expenses:Fees", "Expenses:Uncategorized",
    "Income:Salary", "Income:Interest", "Income:Other",
]

_NORMALIZE_RE = re.compile(r"\d+")


def normalize_description(description):
    """Collapses variable per-transaction noise (reference numbers, dates
    embedded in the description) so repeat merchants share one cache key."""
    return re.sub(r"\s+", " ", _NORMALIZE_RE.sub("#", description)).strip().upper()


def load_rules(rules_path):
    if not rules_path.exists():
        return []
    data = yaml.safe_load(rules_path.read_text())
    return (data or {}).get("rules", [])


def save_rules(rules_path, rules):
    rules_path.write_text(yaml.dump({"rules": rules}, sort_keys=False, default_flow_style=False))


def match_rule(description, rules):
    """Most-specific match wins: the longest pattern that's a substring of
    the description, not file order."""
    matches = [r for r in rules if r["pattern"] in description]
    if not matches:
        return None
    return max(matches, key=lambda r: len(r["pattern"]))


def load_account_matchers():
    data = yaml.safe_load(ACCOUNTS_CONFIG.read_text())
    return data["accounts"]


def _digit_keys(match):
    """Every reasonable digits-only representation of an account's
    identifying number(s), for substring-matching against a transfer
    description — banks vary whether they print BSB+account concatenated,
    account alone, or with/without separators."""
    keys = set()
    if "bsb" in match and "account_number" in match:
        bsb_digits = re.sub(r"\D", "", match["bsb"])
        acct_digits = re.sub(r"\D", "", match["account_number"])
        keys.add(bsb_digits + acct_digits)
        keys.add(acct_digits)
    if "account_number" in match and "bsb" not in match:
        keys.add(re.sub(r"\D", "", match["account_number"]))
    # membership_number (Amex) is partially masked by Amex itself in the
    # source PDFs — not enough real digits survive to match reliably, so
    # it's deliberately not included here.
    return {k for k in keys if len(k) >= 6}  # skip anything too short to be a real identifier


def find_transfer_target(description, own_account_name, matchers):
    """Returns the other tracked account's name if `description` contains
    its identifying number, else None."""
    for entry in matchers:
        if entry["name"] == own_account_name:
            continue
        for key in _digit_keys(entry["match"]):
            if key in re.sub(r"\D", "", description):
                return entry["name"]
    return None


def categorize_statement(data, rules, matchers, cache):
    """Categorizes one ingested statement's transactions. Mutates `rules`
    (appending any newly LLM-promoted ones) and `cache` in place. Returns
    the list of categorized transactions, each with a `postings` field."""
    own_account = data["account_name"]
    uncached_llm = {}

    resolved = []
    for txn in data["transactions"]:
        target = find_transfer_target(txn["description"], own_account, matchers)
        if target is None:
            rule = match_rule(txn["description"], rules)
            if rule is not None:
                target = rule["account"]

        if target is None:
            key = normalize_description(txn["description"])
            if key in cache:
                target = cache[key]
            else:
                uncached_llm[key] = txn["description"]

        resolved.append({"txn": txn, "target": target})

    if uncached_llm:
        llm_results = llm_categorize.categorize_batch(
            list(uncached_llm.values()), DEFAULT_CANDIDATE_ACCOUNTS
        )
        for key, description in uncached_llm.items():
            account = llm_results[description]
            cache[key] = account
            rules.append({"pattern": description, "account": account, "source": "llm"})

    output = []
    for item in resolved:
        txn, target = item["txn"], item["target"]
        if target is None:
            target = cache[normalize_description(txn["description"])]
        postings = [
            {"account": own_account, "amount": txn["amount"]},
            {"account": target, "amount": -txn["amount"]},
        ]
        output.append({**txn, "postings": postings})

    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules", required=True, type=Path)
    args = parser.parse_args()

    rules = load_rules(args.rules)
    matchers = load_account_matchers()
    cache = {}

    statement_files = sorted(PROCESSED.glob("*.json"))
    statement_files = [f for f in statement_files if not f.name.endswith(".categorized.json")]

    for json_path in statement_files:
        out_path = PROCESSED / f"{json_path.stem}.categorized.json"
        if out_path.exists():
            continue

        data = json.loads(json_path.read_text())
        categorized = categorize_statement(data, rules, matchers, cache)
        out_path.write_text(json.dumps({**data, "transactions": categorized}, indent=2))
        print(f"categorized: {json_path.name} ({len(categorized)} transactions)")

    save_rules(args.rules, rules)


if __name__ == "__main__":
    main()
