"""Usage capture: parsing, pricing, and the per-node / per-item rollups."""

from __future__ import annotations

import asyncio
import json

import pytest

from kraft import db, store, usage
from kraft.usage import Pricing, Rate, Usage

# ── parsing ──────────────────────────────────────────────────────────────────


def test_agent_envelope_counts_cache_tokens_as_input():
    """Cache reads and writes were billed as input; dropping them under-reports."""
    u = usage.from_envelope(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_creation_input_tokens": 300,
                "cache_read_input_tokens": 4000,
            },
            "model": "claude-opus-5",
            "total_cost_usd": 1.25,
        }
    )
    assert u == Usage(tokens_in=4400, tokens_out=20, cost_usd=1.25, model="claude-opus-5")


def test_result_file_field_names_are_accepted_too():
    u = usage.from_envelope({"usage": {"tokens_in": 5, "tokens_out": 6}, "cost_usd": 0.5})
    assert u == Usage(tokens_in=5, tokens_out=6, cost_usd=0.5, model=None)


@pytest.mark.parametrize(
    "envelope",
    [None, "text", {}, {"usage": None}, {"usage": {}}, {"usage": {"input_tokens": 0}}],
)
def test_no_usage_reads_as_none_rather_than_zeroes(envelope):
    """A task that reports nothing must not be recorded as a zero-token run."""
    assert usage.from_envelope(envelope) is None


def test_read_prefers_the_result_file_over_the_log_envelope(tmp_path):
    log = tmp_path / "s.log"
    result = tmp_path / "s.json"
    log.write_text(json.dumps({"usage": {"input_tokens": 1, "output_tokens": 1}}) + "\n")
    result.write_text(json.dumps({"usage": {"tokens_in": 99, "tokens_out": 98}}))
    assert usage.read(log, result).tokens_in == 99


def test_read_falls_back_to_the_last_line_of_the_log(tmp_path):
    log = tmp_path / "s.log"
    result = tmp_path / "s.json"
    log.write_text(
        "building...\nsome noise\n" + json.dumps({"usage": {"input_tokens": 7, "output_tokens": 3}})
    )
    result.write_text(json.dumps({"status": "done"}))
    assert usage.read(log, result) == Usage(7, 3)


def test_read_survives_missing_and_unparseable_files(tmp_path):
    assert usage.read(tmp_path / "nope.log", tmp_path / "nope.json") is None
    (tmp_path / "bad.log").write_text("not json at all")
    (tmp_path / "bad.json").write_text("{{{")
    assert usage.read(tmp_path / "bad.log", tmp_path / "bad.json") is None


# ── pricing ──────────────────────────────────────────────────────────────────


def test_cost_uses_the_model_rate_then_the_default(tmp_path):
    p = tmp_path / "pricing.yaml"
    p.write_text(
        "models:\n  m1: { input: 10.0, output: 30.0 }\ndefault: { input: 1.0, output: 2.0 }\n"
    )
    pricing = usage.load_pricing(p)
    assert pricing.cost(Usage(1_000_000, 1_000_000, None, "m1")) == 40.0
    assert pricing.cost(Usage(1_000_000, 1_000_000, None, "unknown")) == 3.0


def test_a_reported_cost_is_never_overwritten_by_the_rate_table():
    pricing = Pricing(models={}, default=Rate(1000.0, 1000.0))
    assert Usage(10, 10, 0.42, "m").with_cost(pricing).cost_usd == 0.42


def test_an_unreadable_rate_table_prices_at_zero_rather_than_raising(tmp_path):
    """load_pricing runs in the app's lifespan, so anything it raises stops the
    server — a hand-edited rate table must never be able to do that."""
    bad = tmp_path / "pricing.yaml"
    bad.write_text("[not, a, mapping]")
    assert usage.load_pricing(bad).cost(Usage(1_000_000, 1_000_000)) == 0.0
    assert usage.load_pricing(tmp_path / "missing.yaml") is usage.DEFAULT_PRICING

    # `models:` as a list, a scalar, or with a junk entry among good ones
    bad.write_text("models: [a, b]\ndefault: { input: 1.0, output: 2.0 }\n")
    assert usage.load_pricing(bad).cost(Usage(1_000_000, 0)) == 1.0
    bad.write_text("models: nope\ndefault: { input: 1.0, output: 2.0 }\n")
    assert usage.load_pricing(bad).models == {}
    bad.write_text(
        "models:\n  ok: { input: 5.0, output: 5.0 }\n  junk: 3\n"
        "default: { input: 1.0, output: 2.0 }\n"
    )
    priced = usage.load_pricing(bad)
    assert set(priced.models) == {"ok"}


def test_the_shipped_rate_table_parses():
    pricing = usage.load_pricing("templates/pricing.yaml")
    assert pricing.models and pricing.default.input > 0


# ── persistence and rollups ──────────────────────────────────────────────────


def _session(conn, sid, node, *, round=0, status="done", u: Usage | None = None):
    store.create_session(
        conn,
        id=sid,
        work_item_id="w",
        node_id=node,
        hook_point=f"on.{node}",
        log_path="l",
        result_path="r",
        round=round,
    )
    store.session_running(conn, sid, 1, 1.0)
    store.session_exited(conn, sid, status, None, u)


async def _scenario(tmp_path):
    database = await db.Database.open(tmp_path / "orchestrator.db")
    try:
        await database.write(
            lambda c: c.execute(
                "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
                "status, created_at, updated_at) VALUES "
                "('w','t','/r','quick-task','{}','active','now','now')"
            )
        )
        await database.write(
            lambda c: _session(c, "s1", "verify", round=0, u=Usage(100, 10, 0.5, "m"))
        )
        await database.write(
            lambda c: _session(c, "s2", "verify", round=0, u=Usage(50, 5, 0.25, "m"))
        )
        await database.write(
            lambda c: _session(
                c, "s3", "verify", round=1, status="capped_out", u=Usage(1, 1, 0.01, "m")
            )
        )
        # a task that reported nothing must not poison the sums
        await database.write(lambda c: _session(c, "s4", "env_setup", round=0))
        return database.read(lambda c: store.usage_rollup(c, "w")), database.read(
            lambda c: c.execute(
                "SELECT started_at, wall_ms, round, model FROM worker_sessions WHERE id='s1'"
            ).fetchone()
        )
    finally:
        await database.close()


def test_rollup_sums_per_node_and_per_item(tmp_path):
    rollup, s1 = asyncio.run(_scenario(tmp_path))

    verify = next(n for n in rollup["by_node"] if n["node"] == "verify")
    assert (verify["tokens_in"], verify["tokens_out"]) == (151, 16)
    assert verify["cost_usd"] == pytest.approx(0.76)
    assert verify["sessions"] == 3
    assert verify["rounds"] == 2  # two distinct fix cycles, not three sessions
    assert verify["capped_out"] == 1

    env = next(n for n in rollup["by_node"] if n["node"] == "env_setup")
    assert (env["tokens_in"], env["cost_usd"], env["rounds"]) == (0, 0.0, 1)

    total = rollup["total"]
    assert total["tokens_in"] == 151
    assert total["cost_usd"] == pytest.approx(0.76)
    assert total["sessions"] == 4
    # the deepest node's loop count, not the sum across nodes
    assert total["rounds"] == 2

    # the session row itself carries what the rollup was built from
    assert s1["started_at"] and s1["wall_ms"] is not None and s1["model"] == "m"
    assert s1["round"] == 0


def test_rollup_of_an_item_with_no_sessions_is_empty_not_an_error(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w','t','/r','quick-task','{}','active','now','now')"
                )
            )
            return database.read(lambda c: store.usage_rollup(c, "w"))
        finally:
            await database.close()

    rollup = asyncio.run(scenario())
    assert rollup["by_node"] == []
    assert rollup["total"] == {
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0,
        "wall_ms": 0,
        "sessions": 0,
        "capped_out": 0,
        "rounds": 0,
    }
