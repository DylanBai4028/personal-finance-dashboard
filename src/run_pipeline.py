"""Runs the full pipeline in order: ingest_1 -> categorize_2 -> sync_to_supabase_3.

Each stage is also runnable standalone (see each script's own __main__), but
this is the one-command path for a normal statement drop.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import categorize_2
import ingest_1
import sync_to_supabase_3

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="personal", choices=["personal", "demo"])
    parser.add_argument(
        "--rules",
        type=Path,
        default=None,
        help="defaults to rules/category_rules.local.yaml for --target personal, "
             "rules/category_rules.example.yaml for --target demo",
    )
    args = parser.parse_args()

    rules_path = args.rules or (
        ROOT / "rules" / ("category_rules.local.yaml" if args.target == "personal"
                           else "category_rules.example.yaml")
    )

    print("--- ingest_1 ---")
    ingest_1.ingest_all()
    print("--- categorize_2 ---")
    categorize_2.run(rules_path)
    print(f"--- sync_to_supabase_3 (--target {args.target}) ---")
    sync_to_supabase_3.sync_all(args.target)


if __name__ == "__main__":
    main()
