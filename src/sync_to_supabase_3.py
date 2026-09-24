"""Syncs categorized transactions to Supabase — accounts, transactions, and
postings — via the PostgREST API, using the service-role key (never the
anon key, which the schema grants read-only).

Runs incrementally: only pushes data/processed/*.categorized.json files
that don't already have a sibling *.synced marker, so re-running the
pipeline never re-inserts the same rows — there's no unique constraint on
transactions/postings to catch that at the database level, unlike
ingest_1.py's dedup or categorize_2.py's *.categorized.json guard.

credit_limit updates (ANZ credit card only) are applied by the statement's
own transaction dates, not file-processing order — data/processed/*.json
isn't guaranteed chronological (e.g. a statement named 'AUG2026' sorts
before one named 'JUN2025' alphabetically) — tracked in
data/processed/_credit_limit_state.json so a newer statement always wins
regardless of what order files get synced in.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
CREDIT_LIMIT_STATE = PROCESSED / "_credit_limit_state.json"

_ROOT_TYPE_BY_PREFIX = {
    "Assets": "asset",
    "Liabilities": "liability",
    "Income": "income",
    "Expenses": "expense",
}


def _load_env_file():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _env(target, name):
    key = f"SUPABASE_{target.upper()}_{name}"
    value = os.environ.get(key)
    if not value:
        raise SystemExit(f"missing required env var {key} — check .env is filled in")
    return value


class SupabaseClient:
    def __init__(self, target):
        _load_env_file()
        # Tolerate either the bare project URL or one that already includes
        # /rest/v1 — confirmed against a real .env that Supabase's current
        # dashboard can hand out either form depending on which field is
        # copied.
        url = _env(target, "URL").rstrip("/")
        if url.endswith("/rest/v1"):
            url = url[: -len("/rest/v1")]
        self.base_url = url + "/rest/v1"
        key = _env(target, "SERVICE_ROLE_KEY")
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def upsert(self, table, rows, on_conflict):
        if not rows:
            return []
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"}
        resp = requests.post(
            f"{self.base_url}/{table}", headers=headers, params={"on_conflict": on_conflict},
            json=rows, timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def insert(self, table, rows):
        if not rows:
            return []
        headers = {**self.headers, "Prefer": "return=representation"}
        resp = requests.post(f"{self.base_url}/{table}", headers=headers, json=rows, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def patch(self, table, filter_params, body):
        resp = requests.patch(
            f"{self.base_url}/{table}", headers=self.headers, params=filter_params,
            json=body, timeout=30,
        )
        resp.raise_for_status()


def root_type_for(account_name):
    prefix = account_name.split(":", 1)[0]
    if prefix not in _ROOT_TYPE_BY_PREFIX:
        raise ValueError(f"unrecognized account name prefix: {account_name!r}")
    return _ROOT_TYPE_BY_PREFIX[prefix]


def collect_account_names(transactions):
    names = set()
    for t in transactions:
        for p in t["postings"]:
            names.add(p["account"])
    return names


def ensure_accounts(client, account_names):
    """Upserts {name, root_type} only — never includes credit_limit, so this
    can never accidentally reset it on an account referenced as someone
    else's transfer target or category posting."""
    rows = [{"name": name, "root_type": root_type_for(name)} for name in sorted(account_names)]
    result = client.upsert("accounts", rows, on_conflict="name")
    return {row["name"]: row["id"] for row in result}


def load_credit_limit_state():
    if CREDIT_LIMIT_STATE.exists():
        return json.loads(CREDIT_LIMIT_STATE.read_text())
    return {}


def save_credit_limit_state(state):
    CREDIT_LIMIT_STATE.write_text(json.dumps(state, indent=2))


def maybe_update_credit_limit(client, data, credit_limit_state):
    """Updates the account's credit_limit only if this statement's own
    transactions are newer than the last statement that set it."""
    if "credit_limit" not in data:
        return
    account_name = data["account_name"]
    statement_max_date = max(t["date"] for t in data["transactions"]) if data["transactions"] else None
    if statement_max_date is None:
        return
    last_applied = credit_limit_state.get(account_name)
    if last_applied is not None and statement_max_date <= last_applied:
        return
    client.patch("accounts", {"name": f"eq.{account_name}"}, {"credit_limit": data["credit_limit"]})
    credit_limit_state[account_name] = statement_max_date


def sync_statement(client, data, account_ids):
    transactions = data["transactions"]
    if not transactions:
        return

    txn_rows = [
        {
            "date": t["date"],
            "description": t["description"],
            "source_file": data["source_file"],
            "foreign_amount": (
                str(t["foreign_amount"]) if t.get("foreign_amount") is not None else None
            ),
            "foreign_currency": t.get("foreign_currency"),
        }
        for t in transactions
    ]
    inserted = client.insert("transactions", txn_rows)

    posting_rows = []
    for txn, inserted_row in zip(transactions, inserted):
        for p in txn["postings"]:
            posting_rows.append({
                "transaction_id": inserted_row["id"],
                "account_id": account_ids[p["account"]],
                "amount": p["amount"],
            })
    client.insert("postings", posting_rows)


def sync_all(target):
    client = SupabaseClient(target)
    credit_limit_state = load_credit_limit_state()

    statement_files = sorted(PROCESSED.glob("*.categorized.json"))
    pending = [
        f for f in statement_files
        if not (PROCESSED / f"{f.stem.removesuffix('.categorized')}.synced").exists()
    ]

    if not pending:
        print("nothing to sync — every categorized statement already has a .synced marker")
        return

    all_account_names = set()
    parsed = []
    for json_path in pending:
        data = json.loads(json_path.read_text())
        all_account_names |= collect_account_names(data["transactions"])
        parsed.append((json_path, data))

    account_ids = ensure_accounts(client, all_account_names)

    for json_path, data in parsed:
        sync_statement(client, data, account_ids)
        maybe_update_credit_limit(client, data, credit_limit_state)
        marker = PROCESSED / f"{json_path.stem.removesuffix('.categorized')}.synced"
        marker.write_text("synced\n")
        print(f"synced: {json_path.name} ({len(data['transactions'])} transactions)")

    save_credit_limit_state(credit_limit_state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=["personal", "demo"])
    args = parser.parse_args()
    sync_all(args.target)


if __name__ == "__main__":
    main()
