"""Categorizes transactions no rule matched, via the Claude Code CLI.

Shells out to `claude -p ... --output-format json` rather than the
Anthropic API, so this runs under Dylan's Pro/Max subscription's included
usage instead of metered per-token billing (see architecture.md's Key
architectural decisions). Batches every unmatched transaction in a run into
one call rather than one call per transaction, to keep subprocess overhead
and usage cost down.
"""

import json
import re
import subprocess

_TIMEOUT_SECONDS = 180
# A single statement can have 200+ unique unmatched merchants (confirmed
# against real data) — one call that size risks a slow response and a
# higher chance of a malformed reply (already seen a markdown-fence
# formatting slip on a batch of just 21). Chunking keeps each call's
# duration predictable and limits the blast radius of one bad response.
_CHUNK_SIZE = 40


def _build_prompt(descriptions, candidate_accounts):
    # Numbered rather than asking the model to echo each description back
    # as a dict key: confirmed against a real batch that it can silently
    # normalize a description (whitespace, casing) when echoing it, which
    # breaks an exact-string lookup. Indexing sidesteps that entirely.
    numbered = "\n".join(f"{i}. {d}" for i, d in enumerate(descriptions))
    accounts = "\n".join(f"- {a}" for a in candidate_accounts)
    return (
        "You are categorizing real personal bank transactions into a "
        "ledger's expense/income accounts. For each numbered transaction "
        "description below, pick the single best-matching account from the "
        "candidate list. If genuinely nothing fits, use "
        "'Expenses:Uncategorized'.\n\n"
        f"Transaction descriptions:\n{numbered}\n\n"
        f"Candidate accounts:\n{accounts}\n\n"
        "Reply with ONLY a JSON object mapping each number (as a string key) "
        "to its chosen account name, e.g. "
        '{"0": "Expenses:Transport", "1": "Expenses:Food:Groceries"}. '
        f"Include every number from 0 to {len(descriptions) - 1}. "
        "No other text, no markdown code fences."
    )


def categorize_batch(descriptions, candidate_accounts):
    """Asks Claude Code to categorize each description in `descriptions`
    (a list of unique transaction description strings) against
    `candidate_accounts`. Returns {description: account_name}. Splits into
    chunks of _CHUNK_SIZE, each its own `claude -p` call — a chunk that
    fails raises RuntimeError (the caller should treat that chunk as
    needing review, not guess), independent of any other chunk's result."""
    mapping = {}
    for i in range(0, len(descriptions), _CHUNK_SIZE):
        chunk = descriptions[i:i + _CHUNK_SIZE]
        mapping.update(_categorize_chunk(chunk, candidate_accounts))
    return mapping


def _categorize_chunk(descriptions, candidate_accounts):
    if not descriptions:
        return {}

    prompt = _build_prompt(descriptions, candidate_accounts)
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise RuntimeError(f"claude -p invocation failed: {e}") from e

    try:
        envelope = json.loads(result.stdout)
        reply = envelope["result"].strip()
        # Despite being told not to, the model sometimes wraps its reply in
        # a markdown code fence anyway — strip it if present rather than
        # relying on prompt wording alone to prevent it.
        reply = re.sub(r"^```(?:json)?\n|\n```$", "", reply)
        index_mapping = json.loads(reply)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise RuntimeError(f"could not parse claude -p's response: {e}\nraw: {result.stdout[:500]}") from e

    missing = [i for i in range(len(descriptions)) if str(i) not in index_mapping]
    if missing:
        raise RuntimeError(f"claude -p's response is missing {len(missing)} of {len(descriptions)} indices")

    return {descriptions[i]: index_mapping[str(i)] for i in range(len(descriptions))}
