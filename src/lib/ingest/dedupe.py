"""Deduplicates transactions across overlapping statement periods.

No bank-issued transaction ID exists in any of the four statement formats,
and adjacent statements for the same account aren't guaranteed to cover
non-overlapping date ranges — so a transaction is identified by account +
date + amount + description, plus balance when the parser captured one
(ANZ prints a running balance per row; Amex doesn't). Balance matters
because a merchant can legitimately charge the same amount twice on the
same day (e.g. two coffees) — only the balance tells those apart.
"""


def transaction_key(account_name, txn):
    return (
        account_name,
        txn["date"],
        round(txn["amount"], 2),
        txn["description"],
        round(txn["balance"], 2) if txn.get("balance") is not None else None,
    )


def dedupe(account_name, transactions, seen_keys):
    """Returns (kept, updated_seen_keys). `kept` excludes any transaction
    whose key is already in `seen_keys`; `updated_seen_keys` is `seen_keys`
    plus every kept transaction's key, for the caller to carry into the next
    statement processed for this account."""
    kept = []
    updated = set(seen_keys)
    for txn in transactions:
        key = transaction_key(account_name, txn)
        if key in updated:
            continue
        updated.add(key)
        kept.append(txn)
    return kept, updated
