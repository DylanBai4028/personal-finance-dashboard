"""Generates synthetic, raw (uncategorized) transactions for the public demo,
in the same shape ingest_1.py's real output uses — so the demo runs through
the real categorize_2.py (rules + LLM fallback) rather than being
pre-categorized, proving the categorization pipeline itself works, not just
the dashboard rendering pre-made numbers.

Writes one JSON file per synthetic "statement" (grouped by account and
month, mirroring how real statements arrive) directly to a demo-only
processed directory — never data/processed/, which holds real personal
data. Run standalone, then feed the output through:
    categorize_2.py --rules rules/category_rules.example.yaml --processed-dir data/demo_processed
    sync_to_supabase_3.py --target demo --processed-dir data/demo_processed
"""

import calendar
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_PROCESSED = ROOT / "data" / "demo_processed"

# Fake identifiers matching rules/accounts.example.yaml, embedded in
# transfer descriptions so internal-transfer detection has something real
# to exercise on the demo data too, not just on real statements.
ACCESS_ADVANTAGE = "Assets:ANZ:AccessAdvantage"
ONLINE_SAVER = "Assets:ANZ:OnlineSaver"
CREDIT_CARD = "Liabilities:ANZ:FrequentFlyerBlack"
AMEX = "Liabilities:Amex:Card"

ONLINE_SAVER_DIGITS = "012345" + "987654321"  # bsb + account_number, no dashes
ACCESS_ADVANTAGE_DIGITS = "012345" + "123456789"
CREDIT_CARD_DIGITS = "1111222233334444"

# Merchants deliberately split: some match rules/category_rules.example.yaml
# directly (COLES, WOOLWORTHS, UBER EATS, TRANSPORTFORNSW), the rest don't
# — so a real run exercises the LLM fallback too, not just rule matching.
GROCERY_MERCHANTS = ["COLES 3311 SYDNEY", "WOOLWORTHS METRO SYDNEY", "IGA CORNER STORE"]
DINING_MERCHANTS = [
    "UBER EATS SYDNEY", "THE CORNER CAFE", "BAKERY ON MAIN", "NOODLE HOUSE SURRY HILLS",
    "MARGARITA'S TACOS", "HARBOUR VIEW HOTEL",
]
TRANSPORT_MERCHANTS = ["TRANSPORTFORNSW TAP SYDNEY", "UBER TRIP SYDNEY", "EASY PARKING CBD"]
SHOPPING_MERCHANTS = [
    "JB HI-FI CITY", "AMAZON AU MARKETPLACE", "CHEMIST WAREHOUSE", "BUNNINGS WAREHOUSE",
    "KMART AUSTRALIA",
]
SUBSCRIPTION_MERCHANTS = ["NETFLIX.COM", "SPOTIFY AU", "APPLE.COM/BILL"]
UTILITY_MERCHANTS = ["ORIGIN ENERGY LTD", "SYDNEY WATER"]

RENT_DESCRIPTION = "RENT PAYMENT REALESTATE CO"
SALARY_DESCRIPTION = "SALARY ACME PTY LTD"


def _random_date(year, month, rng):
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, rng.randint(1, last_day))


def _spend_transaction(rng, merchant, low, high):
    return {
        "date": None,  # filled in by caller
        "description": merchant,
        "amount": -round(rng.uniform(low, high), 2),
        "foreign_amount": None,
        "foreign_currency": None,
    }


def generate(months=12, seed=42):
    """Returns {account_name: [monthly transaction lists]} — roughly
    300-500 transactions total across all four accounts and `months` of
    history, plausible merchants/dates/amounts, enough that every
    dashboard visualization has real data to show."""
    rng = random.Random(seed)
    today = date.today()
    year, month = today.year, today.month

    months_list = []
    y, m = year, month
    for _ in range(months):
        months_list.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months_list.reverse()

    by_account = {ACCESS_ADVANTAGE: [], ONLINE_SAVER: [], CREDIT_CARD: [], AMEX: []}

    for (y, m) in months_list:
        # --- Access Advantage: salary in, rent out, a card-payment transfer, interest ---
        txns = []
        salary_day = min(15, calendar.monthrange(y, m)[1])
        txns.append({
            "date": date(y, m, salary_day).isoformat(), "description": SALARY_DESCRIPTION,
            "amount": round(rng.uniform(4800, 5400), 2), "foreign_amount": None, "foreign_currency": None,
        })
        rent_day = min(3, calendar.monthrange(y, m)[1])
        txns.append({
            "date": date(y, m, rent_day).isoformat(), "description": RENT_DESCRIPTION,
            "amount": -round(rng.uniform(1900, 2100), 2), "foreign_amount": None, "foreign_currency": None,
        })
        # transfer to Online Saver (savings sweep)
        txns.append({
            "date": date(y, m, min(16, calendar.monthrange(y, m)[1])).isoformat(),
            "description": f"ANZ INTERNET BANKING FUNDS TFER TRANSFER {rng.randint(100000, 999999)} TO {ONLINE_SAVER_DIGITS}",
            "amount": -round(rng.uniform(300, 800), 2), "foreign_amount": None, "foreign_currency": None,
        })
        # a handful of everyday EFTPOS-style spends
        for _ in range(rng.randint(5, 9)):
            merchant = rng.choice(GROCERY_MERCHANTS + DINING_MERCHANTS + TRANSPORT_MERCHANTS)
            t = _spend_transaction(rng, merchant, 4, 90)
            t["date"] = _random_date(y, m, rng).isoformat()
            txns.append(t)
        if m in (3, 6, 9, 12):
            txns.append({
                "date": date(y, m, calendar.monthrange(y, m)[1]).isoformat(),
                "description": "CREDIT INTEREST PAID", "amount": round(rng.uniform(2, 15), 2),
                "foreign_amount": None, "foreign_currency": None,
            })
        by_account[ACCESS_ADVANTAGE].append({"source_file": f"demo-access-advantage-{y}-{m:02d}.json", "transactions": txns})

        # --- Online Saver: the incoming transfer, occasional withdrawal back, interest ---
        txns = []
        txns.append({
            "date": date(y, m, min(17, calendar.monthrange(y, m)[1])).isoformat(),
            "description": f"ANZ INTERNET BANKING FUNDS TFER TRANSFER {rng.randint(100000, 999999)} FROM {ACCESS_ADVANTAGE_DIGITS}",
            "amount": round(rng.uniform(300, 800), 2), "foreign_amount": None, "foreign_currency": None,
        })
        if m in (3, 6, 9, 12):
            txns.append({
                "date": date(y, m, calendar.monthrange(y, m)[1]).isoformat(),
                "description": "BONUS CREDIT INTEREST PAID", "amount": round(rng.uniform(10, 40), 2),
                "foreign_amount": None, "foreign_currency": None,
            })
        by_account[ONLINE_SAVER].append({"source_file": f"demo-online-saver-{y}-{m:02d}.json", "transactions": txns})

        # --- Credit card: everyday spend, a monthly payment transfer, occasional foreign spend ---
        txns = []
        for _ in range(rng.randint(10, 18)):
            merchant = rng.choice(
                GROCERY_MERCHANTS + DINING_MERCHANTS + TRANSPORT_MERCHANTS
                + SHOPPING_MERCHANTS + SUBSCRIPTION_MERCHANTS + UTILITY_MERCHANTS
            )
            t = _spend_transaction(rng, merchant, 5, 250)
            t["amount"] = -t["amount"]  # credit card: positive = spend
            t["date"] = _random_date(y, m, rng).isoformat()
            if merchant in UTILITY_MERCHANTS and rng.random() < 0.3:
                t["foreign_amount"] = str(round(-t["amount"] * 0.65, 2))
                t["foreign_currency"] = "USD"
            txns.append(t)
        txns.append({
            "date": date(y, m, min(20, calendar.monthrange(y, m)[1])).isoformat(),
            "description": f"PAYMENT THANKYOU {rng.randint(100000, 999999)} FROM {ACCESS_ADVANTAGE_DIGITS}",
            "amount": -round(rng.uniform(400, 1200), 2), "foreign_amount": None, "foreign_currency": None,
        })
        by_account[CREDIT_CARD].append({"source_file": f"demo-credit-card-{y}-{m:02d}.json", "transactions": txns})

        # --- Amex: everyday spend, a monthly payment ---
        txns = []
        for _ in range(rng.randint(6, 12)):
            merchant = rng.choice(DINING_MERCHANTS + SHOPPING_MERCHANTS + SUBSCRIPTION_MERCHANTS)
            t = _spend_transaction(rng, merchant, 5, 200)
            t["amount"] = -t["amount"]
            t["date"] = _random_date(y, m, rng).isoformat()
            txns.append(t)
        txns.append({
            "date": date(y, m, min(22, calendar.monthrange(y, m)[1])).isoformat(),
            "description": f"ONLINE PAYMENT RECEIVED - THANKYOU {rng.randint(1000, 9999)}",
            "amount": -round(rng.uniform(300, 900), 2), "foreign_amount": None, "foreign_currency": None,
        })
        by_account[AMEX].append({"source_file": f"demo-amex-{y}-{m:02d}.json", "transactions": txns})

    return by_account


def write_demo_processed(by_account, out_dir=DEMO_PROCESSED):
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for account_name, statements in by_account.items():
        for statement in statements:
            out_path = out_dir / statement["source_file"]
            out_path.write_text(json.dumps({
                "source_file": statement["source_file"],
                "account_name": account_name,
                "transactions": statement["transactions"],
            }, indent=2))
            total += len(statement["transactions"])
    print(f"wrote {total} synthetic transactions to {out_dir}")


if __name__ == "__main__":
    write_demo_processed(generate())
