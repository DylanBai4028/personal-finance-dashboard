# Documentation

Deeper reference material than the main [README](../README.md) covers — that one gets you running; these explain how the system actually works underneath.

- **[pipeline.md](pipeline.md)** — what happens to a statement between landing in `data/landing/` and showing up on the dashboard, and the actual mechanism behind each step (detection, parsing, reconciliation, dedup, categorization, transfer detection, the LLM fallback, sync).
- **[database-schema.md](database-schema.md)** — every table and view in the Supabase ledger: what each column holds, how the double-entry sign convention actually works, and which dashboard chart reads from which view.
- **[scripts.md](scripts.md)** — which file imports which, and what the key mechanism inside each one does, for finding your way around the codebase without grepping for it.

For setup, running it on your own statements, or the two-deployment privacy model, see the main [README](../README.md).
