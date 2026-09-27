"""`usage.estimate_cost`: pricing a running session's known-live tokens
against the packaged rate table (Kraft-wz83s). Split out of test_usage.py to
stay under its line budget."""

from __future__ import annotations

import pytest

from kraft import usage
from kraft.usage import Usage


def test_estimate_cost_prices_live_tokens_for_a_known_model():
    """`estimate_cost` prices input, cache read and cache write against
    `prices.json` -- never output, which is unknown live (Kraft-wz83s)."""
    live = Usage(
        tokens_in=1_000_000,
        tokens_out=999_999_999,  # must not be priced: it's unknown while running
        tokens_cache_read=1_000_000,
        tokens_cache_write=1_000_000,
        model="claude-sonnet-5",
    )
    # claude-sonnet-5 (prices.json): $2/M input, $0.2/M cache read, $2.5/M
    # cache write -- 1M tokens of each is $2 + $0.20 + $2.50 = $4.70.
    assert usage.estimate_cost(live, "claude-sonnet-5") == pytest.approx(4.70)


def test_estimate_cost_is_none_for_an_unpriced_model():
    live = Usage(tokens_in=1_000_000, model="a-model-no-snapshot-has-ever-priced")
    assert usage.estimate_cost(live, "a-model-no-snapshot-has-ever-priced") is None
