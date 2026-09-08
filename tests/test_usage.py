"""Usage capture: parsing, and the per-node / per-item rollups."""

from __future__ import annotations

import asyncio
import json

import pytest

from kraft import db, store, usage
from kraft.usage import Usage

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


def test_model_falls_back_to_model_usage_key():
    """The stream-json envelope has no top-level `model`; it has `modelUsage`,
    keyed by model name. Reading only `model` is why every worker_sessions row
    ever written had model NULL (Kraft-2r8s)."""
    u = usage.from_envelope(
        {
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "modelUsage": {"claude-opus-5": {"inputTokens": 100, "outputTokens": 20}},
            "total_cost_usd": 1.25,
        }
    )
    assert u == Usage(tokens_in=100, tokens_out=20, cost_usd=1.25, model="claude-opus-5")


def test_a_top_level_model_still_wins_over_model_usage():
    """A result file written by a non-agent adapter names its model directly."""
    u = usage.from_envelope(
        {
            "usage": {"tokens_in": 1, "tokens_out": 1},
            "model": "stated",
            "modelUsage": {"inferred": {}},
        }
    )
    assert u.model == "stated"


def _assistant(request_id: str, tokens_in: int, tokens_out: int) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "request_id": request_id,
            "message": {
                "model": "claude-opus-5",
                "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
            },
        }
    )


def test_stream_usage_sums_distinct_requests():
    """The CLI emits several `assistant` lines per API request, with the same
    usage on each; summing lines rather than requests double-counts."""
    seen: dict = {}
    first = usage.from_stream([_assistant("req_1", 100, 10), _assistant("req_1", 100, 10)], seen)
    assert (first.tokens_in, first.tokens_out) == (100, 10)
    # a later call folds new lines into the same map and returns the total
    second = usage.from_stream([_assistant("req_2", 50, 5)], seen)
    assert (second.tokens_in, second.tokens_out) == (150, 15)
    # cost is only ever the agent's own number, and no per-request cost exists
    assert second.cost_usd is None


def test_stream_usage_takes_model_from_the_init_line():
    """The init line carries the model before the first token is spent, and it
    has to survive the calls that follow it (Kraft-2r8s)."""
    seen: dict = {}
    init = usage.from_stream(
        [json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-5"})], seen
    )
    assert init == Usage(tokens_in=0, tokens_out=0, cost_usd=None, model="claude-opus-5")
    later = usage.from_stream([_assistant("req_1", 7, 3)], seen)
    assert later == Usage(tokens_in=7, tokens_out=3, cost_usd=None, model="claude-opus-5")


def test_stream_usage_ignores_noise_and_reports_nothing_from_nothing():
    seen: dict = {}
    assert usage.from_stream(["", "not json", "{oops", "[]"], seen) is None


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


# ── cost is the agent's, never Kraft's ───────────────────────────────────────


def test_a_reported_cost_is_kept_as_reported():
    u = usage.from_envelope(
        {"usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.42}
    )
    assert u.cost_usd == 0.42


def test_tokens_without_a_reported_cost_stay_unpriced():
    """Kraft has no rate table any more, on purpose: the agent knows what it was
    billed and Kraft does not, so an unreported cost is None — never a guess and
    never a zero."""
    u = usage.from_envelope({"usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}})
    assert u.tokens_in == 1_000_000
    assert u.cost_usd is None
    assert not hasattr(usage, "load_pricing")


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
        # nothing ran, so nothing is missing
        "cost_complete": True,
    }


def test_a_session_with_tokens_and_no_cost_marks_the_rollup_incomplete(tmp_path):
    """Summing an unreported cost as zero would quietly under-report the bill —
    the one thing a cost figure must not do. The rollup says the sum is a floor."""

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
            await database.write(
                lambda c: _session(c, "priced", "verify", u=Usage(100, 10, 0.5, "m"))
            )
            # tokens, but the agent reported no cost
            await database.write(
                lambda c: _session(c, "unpriced", "verify", u=Usage(900, 90, None, "m"))
            )
            # no tokens at all — a subprocess task, not a gap in the billing
            await database.write(lambda c: _session(c, "free", "env_setup"))
            return database.read(lambda c: store.usage_rollup(c, "w"))
        finally:
            await database.close()

    rollup = asyncio.run(scenario())
    verify = next(n for n in rollup["by_node"] if n["node"] == "verify")
    env = next(n for n in rollup["by_node"] if n["node"] == "env_setup")

    assert verify["cost_usd"] == pytest.approx(0.5)  # the floor, not a guess
    assert verify["cost_complete"] is False
    assert verify["tokens_in"] == 1000  # tokens are still fully counted
    assert env["cost_complete"] is True  # a task with no tokens owes nothing
    assert rollup["total"]["cost_complete"] is False
