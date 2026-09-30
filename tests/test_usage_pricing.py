"""`usage.estimate_cost`: pricing a running or settled session's tokens
against the packaged rate table (Kraft-wz83s). Split out of test_usage.py to
stay under its line budget."""

from __future__ import annotations

import pytest

from kraft import store, usage
from kraft.usage import Usage


def test_estimate_cost_prices_live_tokens_for_a_known_model():
    """`estimate_cost` prices input, cache read and cache write against
    `prices.json` -- never output while running (`include_output` defaults
    False), since output is unknown live (Kraft-wz83s)."""
    live = Usage(
        tokens_in=1_000_000,
        tokens_out=999_999_999,  # must not be priced: it's unknown while running
        tokens_cache_read=1_000_000,
        tokens_cache_write=1_000_000,
        model="claude-sonnet-5",
    )
    # claude-sonnet-5 (prices.json): $2/M input, $0.2/M cache read, $4/M cache
    # write (the 1-hour tier, 2x input -- not models.dev's 5-minute $2.5/M) --
    # 1M tokens of each is $2 + $0.20 + $4.00 = $6.20.
    assert usage.estimate_cost(live, "claude-sonnet-5") == pytest.approx(6.20)


@pytest.mark.parametrize(("peak", "usd"), [(272_000, 4.0), (272_001, 8.0)])
def test_a_request_past_272k_prices_the_session_at_the_long_context_tier(peak, usd):
    """Kraft-tugdf.14: gpt-5.6-sol is $4/M input, and $8/M for a session one
    of whose requests sent more than 272k tokens of context (prices.json,
    from models.dev's `tiers`)."""
    spent = Usage(tokens_in=1_000_000, peak_context=peak)
    assert usage.estimate_cost(spent, "gpt-5.6-sol") == pytest.approx(usd)


def test_estimate_cost_is_none_for_an_unpriced_model():
    live = Usage(tokens_in=1_000_000, model="a-model-no-snapshot-has-ever-priced")
    assert usage.estimate_cost(live, "a-model-no-snapshot-has-ever-priced") is None


def test_estimate_cost_reproduces_a_real_sessions_bill():
    """Pins the cache-write tier fix against a real finished claude-sonnet-5
    session's own `total_cost_usd` (Kraft-wz83s review round 1): tokens_in=22,
    cache_write=96191, cache_read=1019087, output=7701, total_cost_usd=
    0.6656354. Only reproducible at the 1-hour cache-write rate (2x input,
    $4/M) -- at models.dev's own 5-minute figure ($2.5/M) this comes to 0.52,
    visibly short."""
    real = Usage(
        tokens_in=22,
        tokens_out=7701,
        tokens_cache_read=1_019_087,
        tokens_cache_write=96_191,
        model="claude-sonnet-5",
    )
    assert usage.estimate_cost(real, "claude-sonnet-5", include_output=True) == pytest.approx(
        0.6656354, abs=1e-6
    )


async def test_a_finished_priced_session_with_no_reported_cost_is_complete_but_estimated(database):
    """Kraft-wz83s review round 1: `session_exited` no longer drops to NULL
    when the envelope reports no cost on a priced model -- `_settle_cost`
    re-estimates from the final tokens instead. The rollup must read that as
    a *complete* sum (there is a `cost_usd`) that is still an *estimate*, not
    the agent's own figure -- the opposite combination from an unpriced
    model's floor (`cost_complete=False`, `cost_estimated=False`)."""
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "('w','t','/r','quick-task','{}','active','now','now')"
        )
    )

    def _exited(c):
        store.create_session(
            c,
            id="s1",
            work_item_id="w",
            node_id="verify",
            hook_point="on.verify",
            log_path="l",
            result_path="r",
        )
        store.session_running(c, "s1", 1, 1.0)
        store.session_exited(c, "s1", "done", None, Usage(1_000_000, 500, None, "claude-sonnet-5"))

    await database.write(_exited)
    rollup = database.read(lambda c: store.usage_rollup(c, "w"))
    verify = next(n for n in rollup["by_node"] if n["node"] == "verify")

    assert verify["cost_usd"] == pytest.approx(2.005)  # $2/M in + $10/M out
    assert verify["cost_complete"] is True
    assert verify["cost_estimated"] is True
