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
from collections.abc import Iterable
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


def _model_of(envelope: dict) -> str | None:
    """The model an agent reported, in either shape it reports it.

    `--output-format stream-json`'s result envelope has no top-level `model`:
    it carries `modelUsage`, a mapping keyed by model name. Reading only
    `model` is why `worker_sessions.model` was NULL on every row ever written
    (Kraft-2r8s). A stated `model` still wins -- a result file written by a
    non-agent adapter names its model directly.
    """
    model = envelope.get("model")
    if isinstance(model, str) and model:
        return model
    by_model = envelope.get("modelUsage")
    if isinstance(by_model, dict):
        for name in by_model:
            if isinstance(name, str) and name:
                return name
    return None


def from_envelope(envelope: object) -> Usage | None:
    """Usage from a decoded agent result envelope, or None if it carries none."""
    if not isinstance(envelope, dict):
        return None
    u = _from_usage_block(envelope.get("usage"), _model_of(envelope))
    if u is None:
        return None
    cost = envelope.get("total_cost_usd", envelope.get("cost_usd"))
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        u = Usage(u.tokens_in, u.tokens_out, float(cost), u.model)
    return u


#: Reserved key in the caller's `seen` map for the model the `system/init` line
#: reported. A `request_id` can never look like this, and an entry with no
#: tokens adds nothing to the sums, so one map carries both facts across calls
#: -- which it has to, because the init line arrives in a different call from
#: the `assistant` lines that follow it.
_INIT_KEY = "\x00init"


def from_stream(lines: Iterable[str], seen: dict[str, Usage]) -> Usage | None:
    """Fold new stream-json lines into `seen` and return the running total.

    `seen` maps `request_id` -> that request's usage and is owned by the
    caller, which is what makes repeated calls over a growing log correct: the
    CLI emits several `assistant` lines per API request, with identical usage
    on each (measured: two lines, one request), so summing lines rather than
    requests double-counts. The envelope's own totals are themselves the sum
    over requests, so this converges on the number `session_exited` writes.

    Returns None only when nothing has been seen at all, so a caller can tell
    "no usage yet" from "zero tokens so far, model known".

    No cost: no per-request cost is reported, and cost is only ever the agent's
    own number (module docstring). A live row keeps `cost_usd` NULL until the
    envelope lands.
    """
    for raw in lines:
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        model = obj.get("model")
        if obj.get("type") == "system" and isinstance(model, str) and model:
            seen.setdefault(_INIT_KEY, Usage(model=model))
            continue
        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        u = _from_usage_block(message.get("usage"), message.get("model"))
        if u is None:
            continue
        request_id = obj.get("request_id")
        # A line with no request_id cannot be deduplicated against anything;
        # key it on its own position so it is counted exactly once.
        key = (
            request_id if isinstance(request_id, str) and request_id else f"{_INIT_KEY}{len(seen)}"
        )
        seen[key] = u
    if not seen:
        return None
    return Usage(
        sum(u.tokens_in for u in seen.values()),
        sum(u.tokens_out for u in seen.values()),
        None,
        next((u.model for u in seen.values() if u.model), None),
    )


def read_envelope(log_path: Path) -> dict | None:
    """The agent's final result envelope: the last JSON object that carries a
    `usage` block, searching from the end of the log.

    Claude Code's `stream-json` output appends a trailing `system/task_summary`
    line *after* the `result` line that carries `usage` and `total_cost_usd`.
    Taking the literal last line picked up that summary instead and silently
    dropped a reported cost (Kraft-xob8). Scanning backward for `usage` finds
    the `result` line regardless of what follows it; a log that never has one
    still gets its last parseable JSON object, unchanged from before.
    """
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    fallback = None
    for line in reversed(lines):
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(envelope, dict):
            continue
        if fallback is None:
            fallback = envelope
        if isinstance(envelope.get("usage"), dict):
            return envelope
    return fallback


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
