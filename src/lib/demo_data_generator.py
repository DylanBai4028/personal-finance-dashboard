"""Generates synthetic, raw (uncategorized) transactions for the public demo,
in the same shape ingest_1.py's real output uses — so the demo runs through
the real categorize_2.py (rules + LLM fallback) rather than being
pre-categorized, proving the categorization pipeline itself works, not just
the dashboard rendering pre-made numbers.

The bank/card identity here (Meridian/Voyager) is entirely fictional,
deliberately not modeled on any specific real institution — the account
names are what the dashboard displays most prominently, so this is the one
thing in the demo that gets a made-up brand rather than reusing a common
real one (unlike ordinary merchants below, where real everyday brand names
like Coles or Netflix are just plausible background detail).

Writes one JSON file per synthetic "statement" (grouped by account and
month, mirroring how real statements arrive) directly to a demo-only
processed directory — never data/processed/, which holds real personal
data. Run standalone, then feed the output through:
    categorize_2.py --rules rules/category_rules.example.yaml \
        --accounts rules/accounts.example.yaml --processed-dir data/demo_processed
    sync_to_supabase_3.py --target demo --processed-dir data/demo_processed
"""

import calendar
import json
import random
import secrets
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_PROCESSED = ROOT / "data" / "demo_processed"

# Fake identifiers matching rules/accounts.example.yaml, embedded in
# transfer descriptions so internal-transfer detection has something real
# to exercise on the demo data too, not just on real statements.
EVERYDAY = "Assets:Meridian:Everyday"
SAVER = "Assets:Meridian:Saver"
TRAVEL_REWARDS = "Liabilities:Meridian:TravelRewards"
VOYAGER = "Liabilities:Voyager:Card"

EVERYDAY_DIGITS = "062000" + "12345678"  # bsb + account_number, no dashes
SAVER_DIGITS = "062000" + "87654321"
TRAVEL_REWARDS_DIGITS = "5544991122339981"
TRAVEL_REWARDS_MASKED = TRAVEL_REWARDS_DIGITS[:4] + "X" * 8 + TRAVEL_REWARDS_DIGITS[-4:]
DEBIT_CARD_LAST4 = "4471"  # Everyday's own linked debit card, used consistently below

# A fictional "own name," standing in for the account holder's own external
# accounts (an overseas savings account, an investing platform) — matched
# by rules/category_rules.example.yaml on "FROM ALEX MORGAN"/"TO ALEX
# MORGAN", the same mechanism a real same-name external transfer uses.
OWN_NAME = "ALEX MORGAN"
FRIEND_NAMES = ["JORDAN NG", "PRIYA CHAND", "SAM WILSON"]

# Merchants deliberately split: some match rules/category_rules.example.yaml
# directly, the rest don't — so a real run exercises the LLM fallback too,
# not just rule matching. Real, common brand names (Coles, Netflix, Uber)
# are just plausible everyday detail here, not identifying information —
# unlike the account identity above, there's nothing personal about a
# synthetic transaction saying "COLES."
GROCERY_MERCHANTS = ["COLES 3311 SYDNEY", "WOOLWORTHS METRO SYDNEY", "IGA CORNER STORE"]
DINING_MERCHANTS = [
    "UBER EATS SYDNEY",
    "THE CORNER CAFE",
    "BAKERY ON MAIN",
    "NOODLE HOUSE SURRY HILLS",
    "MARGARITA'S TACOS",
    "HARBOUR VIEW HOTEL",
]
TRANSPORT_MERCHANTS = ["TRANSPORTFORNSW TAP SYDNEY", "EASY PARKING CBD"]
RIDESHARE_MERCHANTS = ["UBER TRIP SYDNEY", "UBER TRIP CBD"]
SHOPPING_MERCHANTS = [
    "JB HI-FI CITY",
    "AMAZON AU MARKETPLACE",
    "CHEMIST WAREHOUSE",
    "BUNNINGS WAREHOUSE",
    "KMART AUSTRALIA",
]
SUBSCRIPTION_MERCHANTS = ["NETFLIX.COM", "SPOTIFY AU", "APPLE.COM/BILL"]
UTILITY_MERCHANTS = ["ORIGIN ENERGY LTD", "SYDNEY WATER"]
ALCOHOL_MERCHANTS = ["BWS 2210 CITY", "DAN MURPHY'S/45 GEORGE ST", "LIQUORLAND CBD"]
GYM_MERCHANTS = ["FITNESS FIRST CBD", "ANYTIME FITNESS SYDNEY"]
HAIRCUT_MERCHANTS = ["CITY BARBERSHOP SURRY HILLS"]
ENTERTAINMENT_MERCHANTS = ["EVENT CINEMAS CITY", "ICC SYDNEY TICKETS"]

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


def _beem_transaction(rng, y, m):
    """A peer-to-peer payment with an opaque per-payment reference, mostly
    outgoing but sometimes money coming back — real Beem-style activity is
    genuinely bidirectional, which is exactly what makes it a good test of
    the frontend's merchant-grouping logic (it has to collapse hundreds of
    unique hashes down to one sensible "Beem" line)."""
    token = secrets.token_hex(16).upper()
    amount = round(rng.uniform(5, 150), 2)
    if rng.random() > 0.7:
        amount = -amount
    return {
        "date": _random_date(y, m, rng).isoformat(),
        "description": f"FUNDS TRANSFER CARD {DEBIT_CARD_LAST4} BEEM\\{token}",
        "amount": amount,
        "foreign_amount": None,
        "foreign_currency": None,
    }


def generate(months=60, seed=42):
    """Returns {account_name: [monthly transaction lists]} — roughly 6,800-
    7,000 transactions total across all four accounts and `months` of
    history (5 years, matching a real several-year backfill's scope),
    plausible merchants/dates/amounts, enough that every dashboard
    visualization (including multi-year trends and the Category Deep-Dive
    tab's merchant sub-groupings) has rich, realistic-looking data to show."""
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

    by_account = {EVERYDAY: [], SAVER: [], TRAVEL_REWARDS: [], VOYAGER: []}

    for y, m in months_list:
        last_day = calendar.monthrange(y, m)[1]

        # --- Everyday: salary in, rent out, savings sweep, card payments,
        # everyday spend, and the occasional Beem/friend/Wise/investing
        # activity that makes a real ledger messy ---
        txns = []
        txns.append(
            {
                "date": date(y, m, min(15, last_day)).isoformat(),
                "description": SALARY_DESCRIPTION,
                # A high-earning, high-spend persona -- sized against this
                # generator's own total expense volume (~$9,575/mo, verified
                # by direct calculation) for a realistic ~18% savings rate.
                # An earlier, unscaled $4,800-5,400 range was fine before
                # this session's spend-volume increases, but left the
                # dashboard showing an -83% savings rate once spend grew
                # without income growing to match -- a real bug, not
                # deliberately "how the demo persona lives."
                "amount": round(rng.uniform(11200, 12200), 2),
                "foreign_amount": None,
                "foreign_currency": None,
            }
        )
        txns.append(
            {
                "date": date(y, m, min(3, last_day)).isoformat(),
                "description": RENT_DESCRIPTION,
                "amount": -round(rng.uniform(1900, 2100), 2),
                "foreign_amount": None,
                "foreign_currency": None,
            }
        )
        sweep_desc = f"MERIDIAN FUNDS TRANSFER {rng.randint(100000, 999999)} TO {SAVER_DIGITS}"
        txns.append(
            {
                "date": date(y, m, min(16, last_day)).isoformat(),
                "description": sweep_desc,
                "amount": -round(rng.uniform(300, 800), 2),
                "foreign_amount": None,
                "foreign_currency": None,
            }
        )
        # Paying off both cards each month — via the masked-card-number
        # structural match for Travel Rewards, and a rule-matched phrase
        # for Voyager, exercising both of the pipeline's two real
        # card-payment-transfer detection mechanisms.
        card_payment_desc = (
            f"MERIDIAN TRANSFER {rng.randint(100000, 999999)} TO {TRAVEL_REWARDS_MASKED}"
        )
        txns.append(
            {
                "date": date(y, m, min(20, last_day)).isoformat(),
                "description": card_payment_desc,
                "amount": -round(rng.uniform(400, 1200), 2),
                "foreign_amount": None,
                "foreign_currency": None,
            }
        )
        voyager_payment_desc = (
            f"MERIDIAN PAYMENT {rng.randint(100000, 999999)} TO VOYAGER CARD {DEBIT_CARD_LAST4}"
        )
        txns.append(
            {
                "date": date(y, m, min(22, last_day)).isoformat(),
                "description": voyager_payment_desc,
                "amount": -round(rng.uniform(300, 900), 2),
                "foreign_amount": None,
                "foreign_currency": None,
            }
        )
        for _ in range(rng.randint(22, 32)):
            merchant = rng.choice(GROCERY_MERCHANTS + DINING_MERCHANTS + TRANSPORT_MERCHANTS)
            t = _spend_transaction(rng, merchant, 4, 90)
            t["date"] = _random_date(y, m, rng).isoformat()
            txns.append(t)
        for _ in range(rng.randint(3, 6)):
            t = _spend_transaction(rng, rng.choice(RIDESHARE_MERCHANTS), 8, 35)
            t["date"] = _random_date(y, m, rng).isoformat()
            txns.append(t)
        if rng.random() < 0.9:
            for _ in range(rng.randint(3, 6)):
                t = _spend_transaction(rng, rng.choice(ALCOHOL_MERCHANTS), 15, 70)
                t["date"] = _random_date(y, m, rng).isoformat()
                txns.append(t)
        if rng.random() < 0.85:
            t = _spend_transaction(rng, rng.choice(GYM_MERCHANTS), 60, 90)
            t["date"] = _random_date(y, m, rng).isoformat()
            txns.append(t)
        if rng.random() < 0.4:
            t = _spend_transaction(rng, rng.choice(HAIRCUT_MERCHANTS), 35, 65)
            t["date"] = _random_date(y, m, rng).isoformat()
            txns.append(t)
        if rng.random() < 0.35:
            t = _spend_transaction(rng, rng.choice(ENTERTAINMENT_MERCHANTS), 20, 90)
            t["date"] = _random_date(y, m, rng).isoformat()
            txns.append(t)
        if rng.random() < 0.06:
            # A rare genuine one-off income the rules file has no pattern
            # for -- Income:Other, same as real data always has a small
            # residual of income that isn't salary or bank interest.
            txns.append(
                {
                    "date": _random_date(y, m, rng).isoformat(),
                    "description": "TAX REFUND ATO",
                    "amount": round(rng.uniform(200, 1500), 2),
                    "foreign_amount": None,
                    "foreign_currency": None,
                }
            )
        if rng.random() < 0.06:
            # A genuinely ambiguous line item -- rules/category_rules.
            # example.yaml maps it straight to Expenses:Uncategorized
            # rather than leaving it for the LLM to guess at, so
            # regenerating the demo never needs a real API/CLI call.
            txns.append(
                {
                    "date": _random_date(y, m, rng).isoformat(),
                    "description": f"MISC DEBIT ADJUSTMENT REF {rng.randint(10000, 99999)}",
                    "amount": -round(rng.uniform(10, 60), 2),
                    "foreign_amount": None,
                    "foreign_currency": None,
                }
            )
        for _ in range(rng.randint(10, 20)):
            txns.append(_beem_transaction(rng, y, m))
        if rng.random() < 0.3:
            friend = rng.choice(FRIEND_NAMES)
            friend_amt = round(rng.uniform(20, 300), 2)
            friend_desc = f"MERIDIAN PAYMENT {rng.randint(100000, 999999)} {friend_amt} TO {friend}"
            txns.append(
                {
                    "date": _random_date(y, m, rng).isoformat(),
                    "description": friend_desc,
                    "amount": -friend_amt,
                    "foreign_amount": None,
                    "foreign_currency": None,
                }
            )
        if rng.random() < 0.2:
            wise_desc = f"MERIDIAN PAYMENT {rng.randint(100000, 999999)} TO WISE AUSTRALIA PTY LTD"
            txns.append(
                {
                    "date": _random_date(y, m, rng).isoformat(),
                    "description": wise_desc,
                    "amount": -round(rng.uniform(20, 600), 2),
                    "foreign_amount": None,
                    "foreign_currency": None,
                }
            )
        if m in (3, 6, 9, 12):
            txns.append(
                {
                    "date": date(y, m, last_day).isoformat(),
                    "description": "CREDIT INTEREST PAID",
                    "amount": round(rng.uniform(2, 15), 2),
                    "foreign_amount": None,
                    "foreign_currency": None,
                }
            )
        by_account[EVERYDAY].append(
            {"source_file": f"demo-everyday-{y}-{m:02d}.json", "transactions": txns}
        )

        # --- Saver: the incoming sweep, interest, occasional payment out
        # to the fictional investing platform ---
        txns = []
        sweep_in_desc = (
            f"MERIDIAN FUNDS TRANSFER {rng.randint(100000, 999999)} FROM {EVERYDAY_DIGITS}"
        )
        txns.append(
            {
                "date": date(y, m, min(17, last_day)).isoformat(),
                "description": sweep_in_desc,
                "amount": round(rng.uniform(300, 800), 2),
                "foreign_amount": None,
                "foreign_currency": None,
            }
        )
        if m in (3, 6, 9, 12):
            txns.append(
                {
                    "date": date(y, m, last_day).isoformat(),
                    "description": "BONUS CREDIT INTEREST PAID",
                    "amount": round(rng.uniform(10, 40), 2),
                    "foreign_amount": None,
                    "foreign_currency": None,
                }
            )
        if rng.random() < 0.08:
            stake_desc = f"MERIDIAN PAYMENT {rng.randint(100000, 999999)} TO {OWN_NAME}"
            txns.append(
                {
                    "date": _random_date(y, m, rng).isoformat(),
                    "description": stake_desc,
                    "amount": -round(rng.uniform(500, 2500), 2),
                    "foreign_amount": None,
                    "foreign_currency": None,
                }
            )
        by_account[SAVER].append(
            {"source_file": f"demo-saver-{y}-{m:02d}.json", "transactions": txns}
        )

        # --- Travel Rewards card: everyday spend, the monthly payment
        # transfer, occasional foreign spend, rare cash advance ---
        txns = []
        for _ in range(rng.randint(26, 38)):
            merchant = rng.choice(
                GROCERY_MERCHANTS
                + DINING_MERCHANTS
                + TRANSPORT_MERCHANTS
                + SHOPPING_MERCHANTS
                + SUBSCRIPTION_MERCHANTS
                + UTILITY_MERCHANTS
            )
            t = _spend_transaction(rng, merchant, 5, 250)
            t["amount"] = -t["amount"]  # credit card: positive = spend
            t["date"] = _random_date(y, m, rng).isoformat()
            if merchant in UTILITY_MERCHANTS and rng.random() < 0.3:
                t["foreign_amount"] = str(round(-t["amount"] * 0.65, 2))
                t["foreign_currency"] = "USD"
            txns.append(t)
        # Paid off close to in full each month -- based on this statement's
        # own spend total, not an independent fixed range, so the balance
        # stays realistically flat over years instead of growing without
        # bound (a real bug found by checking the demo's own Net Position:
        # an independent $400-1200 payment against $1,500+ of real spend
        # left an ever-growing gap that compounded into a six-figure
        # "unpaid" balance after a few years of simulated history).
        travel_rewards_spend = sum(t["amount"] for t in txns)
        txns.append(
            {
                "date": date(y, m, min(20, last_day)).isoformat(),
                "description": f"PAYMENT THANKYOU {rng.randint(100000, 999999)}",
                "amount": -round(travel_rewards_spend * rng.uniform(0.9, 1.02), 2),
                "foreign_amount": None,
                "foreign_currency": None,
            }
        )
        if rng.random() < 0.03:
            txns.append(
                {
                    "date": _random_date(y, m, rng).isoformat(),
                    "description": f"CASH ADVANCE {rng.randint(100000, 999999)}",
                    "amount": round(rng.uniform(200, 800), 2),
                    "foreign_amount": None,
                    "foreign_currency": None,
                }
            )
            txns.append(
                {
                    "date": _random_date(y, m, rng).isoformat(),
                    "description": "CASH ADVANCE FEE",
                    "amount": round(rng.uniform(3, 15), 2),
                    "foreign_amount": None,
                    "foreign_currency": None,
                }
            )
        by_account[TRAVEL_REWARDS].append(
            {"source_file": f"demo-travel-rewards-{y}-{m:02d}.json", "transactions": txns}
        )

        # --- Voyager: everyday spend, the monthly payment ---
        txns = []
        for _ in range(rng.randint(16, 26)):
            merchant = rng.choice(DINING_MERCHANTS + SHOPPING_MERCHANTS + SUBSCRIPTION_MERCHANTS)
            t = _spend_transaction(rng, merchant, 5, 200)
            t["amount"] = -t["amount"]
            t["date"] = _random_date(y, m, rng).isoformat()
            txns.append(t)
        voyager_spend = sum(t["amount"] for t in txns)
        txns.append(
            {
                "date": date(y, m, min(22, last_day)).isoformat(),
                "description": f"ONLINE PAYMENT RECEIVED - THANKYOU {rng.randint(1000, 9999)}",
                "amount": -round(voyager_spend * rng.uniform(0.9, 1.02), 2),
                "foreign_amount": None,
                "foreign_currency": None,
            }
        )
        by_account[VOYAGER].append(
            {"source_file": f"demo-voyager-{y}-{m:02d}.json", "transactions": txns}
        )

    # A couple of rare, guaranteed one-off transfers to/from the fictional
    # overseas account — real data had only 2 of these across 4 years, so
    # low-probability-per-month chance isn't reliable enough to guarantee
    # they show up at all; placed a third and two-thirds of the way through
    # the history, matching how rare, spread-out events actually look.
    if len(months_list) >= 3:
        early_idx = len(months_list) // 3
        early_y, early_m = months_list[early_idx]
        by_account[EVERYDAY][early_idx]["transactions"].append(
            {
                "date": _random_date(early_y, early_m, rng).isoformat(),
                "description": f"INTL PAYMENT FROM {OWN_NAME} REF:IM{rng.randint(100000, 999999)}",
                "amount": round(rng.uniform(3000, 8000), 2),
                "foreign_amount": None,
                "foreign_currency": None,
            }
        )
        late_idx = (2 * len(months_list)) // 3
        late_y, late_m = months_list[late_idx]
        by_account[EVERYDAY][late_idx]["transactions"].append(
            {
                "date": _random_date(late_y, late_m, rng).isoformat(),
                "description": f"TRANSFER FROM {OWN_NAME}",
                "amount": round(rng.uniform(3000, 8000), 2),
                "foreign_amount": None,
                "foreign_currency": None,
            }
        )

    return by_account


def write_demo_processed(by_account, out_dir=DEMO_PROCESSED):
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for account_name, statements in by_account.items():
        for statement in statements:
            out_path = out_dir / statement["source_file"]
            out_path.write_text(
                json.dumps(
                    {
                        "source_file": statement["source_file"],
                        "account_name": account_name,
                        "transactions": statement["transactions"],
                    },
                    indent=2,
                )
            )
            total += len(statement["transactions"])
    print(f"wrote {total} synthetic transactions to {out_dir}")


if __name__ == "__main__":
    write_demo_processed(generate())
