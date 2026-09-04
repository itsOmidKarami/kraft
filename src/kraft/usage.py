"""Token, cost and wall-time capture per worker session.

Nothing recorded any of this before: the detail control row, the log modal
header and the whole analytics view are all reading numbers that have to be
harvested when a session ends. Two shapes are accepted, because two kinds of
task produce them:

* the agent envelope an agent CLI writes as the last line of its log
  (Claude Code with ``--output-format json``: ``usage.input_tokens`` and
  friends, ``total_cost_usd``, ``duration_ms``, ``modelUsage``);
* a ``usage`` block in the result file any adapter may write at
  ``$KRAFT_RESULT_PATH``.

Cost is stored, not derived at read time, but the *rates* live in a YAML file
so a wrong rate can be corrected and the numbers recomputed later without
re-running any work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    model: str | None = None

    def with_cost(self, pricing: Pricing) -> Usage:
        """Fill in cost from the rate table when the agent did not report one."""
        if self.cost_usd is not None:
            return self
        return Usage(self.tokens_in, self.tokens_out, pricing.cost(self), self.model)


@dataclass(frozen=True)
class Rate:
    """USD per million tokens."""

    input: float
    output: float


@dataclass(frozen=True)
class Pricing:
    models: dict[str, Rate]
    default: Rate

    def cost(self, u: Usage) -> float:
        rate = self.models.get(u.model or "", self.default)
        return (u.tokens_in * rate.input + u.tokens_out * rate.output) / 1_000_000


DEFAULT_PRICING = Pricing(models={}, default=Rate(input=0.0, output=0.0))


def _rate(raw: object) -> Rate | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Rate(input=float(raw["input"]), output=float(raw["output"]))
    except KeyError, TypeError, ValueError:
        return None


def load_pricing(path: str | Path) -> Pricing:
    """Rates from YAML. A missing or unreadable file prices everything at zero.

    Deliberately lenient: a bad rate table must never stop work from running,
    it only makes the cost column wrong until the file is fixed.
    """
    try:
        data = yaml.safe_load(Path(path).read_text())
    except OSError, yaml.YAMLError:
        return DEFAULT_PRICING
    if not isinstance(data, dict):
        return DEFAULT_PRICING
    raw_models = data.get("models")
    models = (
        {str(k): r for k, v in raw_models.items() if (r := _rate(v)) is not None}
        if isinstance(raw_models, dict)
        else {}
    )
    return Pricing(models=models, default=_rate(data.get("default")) or Rate(0.0, 0.0))


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
