"""Token, cost and wall-time capture per worker session.

Nothing recorded any of this before: the detail control row, the log modal
header and the whole analytics view are all reading numbers that have to be
harvested when a session ends. Two shapes are accepted, because two kinds of
task produce them:

* the agent envelope an agent CLI writes as the last line of its log
  (Claude Code with ``--output-format json``: ``usage.input_tokens`` and
  friends, ``total_cost_usd``, ``model``);
* a ``usage`` block in the result file any adapter may write at
  ``$KRAFT_RESULT_PATH``.

**Cost is only ever the agent's own number.** Kraft used to carry a per-model
rate table and multiply tokens by it when an agent reported no cost. That is
gone: the agent knows what it was billed and Kraft does not, so a computed
figure would be a guess wearing the same font as a fact — and every consumer
of these numbers is someone deciding whether a run was worth it. A session with
tokens and no reported cost stores ``cost_usd = NULL``, and the rollups say so
rather than counting it as zero.

Tokens are different: they are measured, they are reported by everything that
has any, and they are what a rate table would have been applied to anyway. If
per-model pricing is ever wanted, it belongs in one place over the stored
token counts, not smeared across every session row at write time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    #: None means "this agent did not report a cost", never "it was free".
    cost_usd: float | None = None
    model: str | None = None


def _int(v: object) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _from_usage_block(block: object, model: object) -> Usage | None:
    """One ``usage`` mapping, in either the agent's or Kraft's own field names."""
    if not isinstance(block, dict):
        return None
    tokens_in = _int(block.get("input_tokens", block.get("tokens_in")))
    tokens_out = _int(block.get("output_tokens", block.get("tokens_out")))
    # Cache reads and writes are input tokens that were billed; leaving them out
    # would under-report a long agent run by most of its input.
    tokens_in += _int(block.get("cache_creation_input_tokens"))
    tokens_in += _int(block.get("cache_read_input_tokens"))
    if not tokens_in and not tokens_out:
        return None
    return Usage(tokens_in, tokens_out, None, model if isinstance(model, str) else None)


def from_envelope(envelope: object) -> Usage | None:
    """Usage from a decoded agent result envelope, or None if it carries none."""
    if not isinstance(envelope, dict):
        return None
    u = _from_usage_block(envelope.get("usage"), envelope.get("model"))
    if u is None:
        return None
    cost = envelope.get("total_cost_usd", envelope.get("cost_usd"))
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        u = Usage(u.tokens_in, u.tokens_out, float(cost), u.model)
    return u


def read_envelope(log_path: Path) -> dict | None:
    """The last JSON object on the log's final non-empty line, if there is one."""
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    try:
        envelope = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return envelope if isinstance(envelope, dict) else None


def read(log_path: Path, result_path: Path) -> Usage | None:
    """Usage for a finished session: the result file wins, the log envelope backs it up."""
    try:
        result = json.loads(result_path.read_text())
    except OSError, json.JSONDecodeError:
        result = None
    if isinstance(result, dict):
        u = from_envelope(result)
        if u is not None:
            return u
    return from_envelope(read_envelope(log_path))
