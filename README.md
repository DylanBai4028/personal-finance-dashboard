# Personal Finance Dashboard

A personal transaction tracking and budgeting dashboard: ingests ANZ/Amex bank and card PDF
statements into a double-entry Postgres ledger (Supabase), categorizes transactions with rules
plus an LLM fallback, and serves a read-only dashboard.

Two deployments from one codebase: a public demo running on synthetic data (GitHub Pages), and a
private local-only instance running on real data. See `personal-finance-dashboard-Architecture.md`
in the source workspace for the full design, and `docs/` here for user-facing documentation
(added later in the build).

**Status: scaffolding only, pipeline not yet built.**

## Setup

```
uv venv
uv pip install -e .
cp .env.example .env   # fill in your own Supabase project URL + service role key
```

## Structure

See the architecture doc's "Folder structure" section — this repo mirrors it exactly:
`src/` (numbered pipeline stages + `lib/` helpers), `rules/` (categorization + account mapping,
`.local.yaml` gitignored / `.example.yaml` committed), `ledger/` (schema), `frontend/` (static
dashboard, no build step), `data/` (gitignored landing/processed/needs_review zones).
