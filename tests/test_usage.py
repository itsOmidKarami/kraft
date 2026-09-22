"""Usage capture: parsing, and the per-node / per-item rollups."""

from __future__ import annotations

import asyncio
import json

import pytest

from kraft import db, store, usage
from kraft.usage import Usage

# ── parsing ──────────────────────────────────────────────────────────────────


def test_agent_envelope_keeps_cache_tokens_apart_from_uncached_input():
    """Cache reads and writes were billed as input, and dropping them
    under-reports; summed into `tokens_in` they hid how much of a run was
    cache (Ruling 211). Each kind is kept on its own."""
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
    assert u == Usage(
        tokens_in=100,
        tokens_out=20,
        cost_usd=1.25,
        model="claude-opus-5",
        tokens_cache_write=300,
        tokens_cache_read=4000,
    )
    assert u.total == 4420


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


def test_model_is_the_one_that_did_the_work_not_the_warm_up():
    """`_model_of` took the FIRST `modelUsage` key, which is the haiku warm-up
    Claude Code makes before the session's real model runs -- so every agent row
    in orchestrator.db named haiku and every cost-by-model reading of that table
    was invalid (Kraft-s7c04.15)."""
    u = usage.from_envelope(
        {
            "usage": {"input_tokens": 100, "output_tokens": 10},
            "modelUsage": {
                "claude-haiku-4-5-20251001": {"inputTokens": 12, "outputTokens": 3},
                "claude-opus-5": {
                    "inputTokens": 40_000,
                    "outputTokens": 9_000,
                    "cacheReadInputTokens": 1_200_000,
                    "cacheCreationInputTokens": 30_000,
                },
            },
        }
    )
    assert u.model == "claude-opus-5"


def test_a_tie_keeps_the_first_key_so_the_read_is_deterministic():
    u = usage.from_envelope(
        {
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "modelUsage": {"a-model": {"inputTokens": 5}, "b-model": {"inputTokens": 5}},
        }
    )
    assert u.model == "a-model"


def test_model_usage_with_unexpected_value_shapes_does_not_raise():
    """Every reader in this module is best-effort: a shape it has never seen
    costs the model, never the session."""
    u = usage.from_envelope(
        {
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "modelUsage": {"a-model": "not a dict", "b-model": None},
        }
    )
    assert u.model == "a-model"


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
    assert usage.read(log, result, "claude-stream-json").tokens_in == 99


def test_read_falls_back_to_the_last_line_of_the_log(tmp_path):
    log = tmp_path / "s.log"
    result = tmp_path / "s.json"
    log.write_text(
        "building...\nsome noise\n" + json.dumps({"usage": {"input_tokens": 7, "output_tokens": 3}})
    )
    result.write_text(json.dumps({"status": "done"}))
    assert usage.read(log, result, "claude-stream-json") == Usage(7, 3)


def test_read_finds_usage_behind_a_trailing_line_that_has_none(tmp_path):
    """Claude Code's stream-json output appends a `system/task_summary` line
    after the `result` line that carries `usage`/`total_cost_usd` (Kraft-xob8).
    The literal last line must not shadow the cost one line above it."""
    log = tmp_path / "s.log"
    result = tmp_path / "s.json"
    log.write_text(
        json.dumps({"usage": {"input_tokens": 7, "output_tokens": 3}, "total_cost_usd": 0.42})
        + "\n"
        + json.dumps({"type": "system", "subtype": "task_summary"})
    )
    result.write_text(json.dumps({"status": "done"}))
    assert usage.read(log, result, "claude-stream-json") == Usage(7, 3, 0.42)


def test_read_survives_missing_and_unparseable_files(tmp_path):
    assert usage.read(tmp_path / "nope.log", tmp_path / "nope.json", "claude-stream-json") is None
    (tmp_path / "bad.log").write_text("not json at all")
    (tmp_path / "bad.json").write_text("{{{")
    assert usage.read(tmp_path / "bad.log", tmp_path / "bad.json", "claude-stream-json") is None


def test_reader_none_reads_only_the_result_file(tmp_path):
    log = tmp_path / "s.log"
    log.write_text('{"type":"result","is_error":false,"usage":{"input_tokens":9}}\n')
    result = tmp_path / "s.json"
    result.write_text('{"status":"done","usage":{"input_tokens":4,"output_tokens":2}}')
    u = usage.read(log, result, reader=None)
    # The claude-shaped envelope in the log is ignored: this harness never
    # promised that schema, and parsing it anyway is how a wrong number
    # becomes a confident one.
    assert (u.tokens_in, u.tokens_out) == (4, 2)


def test_unknown_reader_is_a_config_error_not_a_silent_skip(tmp_path):
    with pytest.raises(KeyError):
        usage.read(tmp_path / "s.log", tmp_path / "s.json", reader="codex-jsonl")


def test_claude_reader_extracts_the_cli_session_id_off_the_init_line(tmp_path):
    """Kraft-cvnx1: the fourth Claude-shaped parser (session id off `--resume`,
    alongside the stream/envelope/rate_limit ones) now lives behind
    `READERS`, not standalone in `escalate.py`."""
    log = tmp_path / "s.log"
    log.write_text(
        "some noise\n"
        + json.dumps({"type": "system", "subtype": "init", "session_id": "cli-abc"})
        + "\n"
        + json.dumps({"type": "result", "usage": {"input_tokens": 1}})
    )
    assert usage.READERS["claude-stream-json"].session_id(log) == "cli-abc"


def test_claude_reader_session_id_survives_missing_and_unparseable_logs(tmp_path):
    reader = usage.READERS["claude-stream-json"]
    assert reader.session_id(tmp_path / "nope.log") is None
    bad = tmp_path / "bad.log"
    bad.write_text("not json at all\n")
    assert reader.session_id(bad) is None


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


async def test_rollup_of_an_item_with_no_sessions_is_empty_not_an_error(database):
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "('w','t','/r','quick-task','{}','active','now','now')"
        )
    )
    rollup = database.read(lambda c: store.usage_rollup(c, "w"))
    assert rollup["by_node"] == []
    assert rollup["total"] == {
        "tokens_in": 0,
        "tokens_cache_write": 0,
        "tokens_cache_read": 0,
        "tokens_out": 0,
        "cost_usd": 0,
        "wall_ms": 0,
        "sessions": 0,
        "capped_out": 0,
        "wait_timed_out": 0,
        "time_capped": 0,
        "rounds": 0,
        # nothing ran, so nothing is missing
        "cost_complete": True,
        "split_complete": True,
    }


async def test_a_session_with_tokens_and_no_cost_marks_the_rollup_incomplete(database):
    """Summing an unreported cost as zero would quietly under-report the bill —
    the one thing a cost figure must not do. The rollup says the sum is a floor."""

    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "('w','t','/r','quick-task','{}','active','now','now')"
        )
    )
    await database.write(lambda c: _session(c, "priced", "verify", u=Usage(100, 10, 0.5, "m")))
    # tokens, but the agent reported no cost
    await database.write(lambda c: _session(c, "unpriced", "verify", u=Usage(900, 90, None, "m")))
    # no tokens at all — a subprocess task, not a gap in the billing
    await database.write(lambda c: _session(c, "free", "env_setup"))
    rollup = database.read(lambda c: store.usage_rollup(c, "w"))
    verify = next(n for n in rollup["by_node"] if n["node"] == "verify")
    env = next(n for n in rollup["by_node"] if n["node"] == "env_setup")

    assert verify["cost_usd"] == pytest.approx(0.5)  # the floor, not a guess
    assert verify["cost_complete"] is False
    assert verify["tokens_in"] == 1000  # tokens are still fully counted
    assert env["cost_complete"] is True  # a task with no tokens owes nothing
    assert rollup["total"]["cost_complete"] is False


async def test_rollup_counts_a_paused_session_s_real_span(database):
    """Kraft-s7c04.18: `wall_ms or 0` erased the time of every session that
    never reached `session_exited`. The stamps to answer with are on the row.

    The still-running case is Task 1's unit test, not this one: derived against
    `now`, it would make this assertion a moving target."""

    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "('w','t','/r','quick-task','{}','active','now','now')"
        )
    )
    # exited: 1000 ms, recorded on the row by session_exited
    await database.write(
        lambda c: c.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, "
            "log_path, result_path, status, attempt, created_at, started_at, "
            "exited_at, round, wall_ms) VALUES ('s1','w','verify','on.test.run',"
            "'l','r','done',1,'2026-09-15T10:00:00+00:00','2026-09-15T10:00:00+00:00',"
            "'2026-09-15T10:00:01+00:00',0,1000)"
        )
    )
    # paused: NULL wall_ms, 90 s between its own stamps
    await database.write(
        lambda c: c.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, "
            "log_path, result_path, status, attempt, created_at, started_at, "
            "exited_at, round, wall_ms) VALUES ('s2','w','verify','on.review.local.run',"
            "'l','r','paused',1,'2026-09-15T10:00:00+00:00','2026-09-15T10:00:00+00:00',"
            "'2026-09-15T10:01:30+00:00',0,NULL)"
        )
    )
    rollup = database.read(lambda c: store.usage_rollup(c, "w"))
    verify = next(n for n in rollup["by_node"] if n["node"] == "verify")
    assert verify["wall_ms"] == 91_000
    assert rollup["total"]["wall_ms"] == 91_000


def test_an_interrupted_envelope_reports_through_model_usage():
    """Kraft-s7c04.18: a SIGINT'd agent flushes a result envelope whose `usage`
    block is all zeroes, with the session's real counts only under
    `modelUsage`. Measured against claude 2.1.273. Without this the envelope
    parses to None and its total_cost_usd -- the only cost figure Kraft will
    ever have for that session -- goes with it."""
    u = usage.from_envelope(
        {
            "stop_reason": None,
            "total_cost_usd": 0.000983,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0},
            "modelUsage": {
                "claude-haiku-4-5-20251001": {
                    "inputTokens": 918,
                    "outputTokens": 13,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                }
            },
        }
    )
    assert u is not None
    assert (u.tokens_in, u.tokens_out) == (918, 13)
    assert u.cost_usd == pytest.approx(0.000983)
    assert u.model == "claude-haiku-4-5-20251001"


def test_model_usage_keeps_cache_tokens_apart_too():
    """Same rule as `_from_usage_block`: cache reads and writes were billed as
    input, and are kept, each kind on its own."""
    u = usage.from_envelope(
        {
            "usage": {},
            "modelUsage": {
                "claude-opus-5": {
                    "inputTokens": 100,
                    "outputTokens": 10,
                    "cacheReadInputTokens": 900,
                    "cacheCreationInputTokens": 50,
                }
            },
        }
    )
    assert (u.tokens_in, u.tokens_cache_write, u.tokens_cache_read, u.tokens_out) == (
        100,
        50,
        900,
        10,
    )


def test_the_fallback_does_not_invent_usage_from_an_empty_model_usage():
    """A task that reports nothing is still None, not a zero-token run."""
    assert usage.from_envelope({"usage": {}, "modelUsage": {}}) is None
    assert usage.from_envelope({"usage": {}, "modelUsage": {"m": "not a dict"}}) is None


def _result(sid, cost, usage_out, total_out, *, usage_in=10, total_in=None):
    """A claude `result` line: `usage` is this invocation's, `modelUsage` and
    `total_cost_usd` are cumulative over the CLI session (measured on real
    multi-invocation logs, Kraft-s7c04.60)."""
    line = {
        "type": "result",
        "session_id": sid,
        "usage": {"input_tokens": usage_in, "output_tokens": usage_out},
        "modelUsage": {
            "claude-sonnet-5": {
                "inputTokens": usage_in if total_in is None else total_in,
                "outputTokens": total_out,
            }
        },
    }
    if cost is not None:
        line["total_cost_usd"] = cost
    return json.dumps(line)


def _progress(sid, tokens):
    """A claude `system/task_progress` line: a Task-tool sub-agent's running
    tally, `usage` and all, which is not an invocation of this session."""
    return json.dumps(
        {
            "type": "system",
            "subtype": "task_progress",
            "session_id": sid,
            "usage": {"total_tokens": tokens, "tool_uses": 1, "duration_ms": 10},
        }
    )


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # Session 56c92568: two invocations, one CLI session. The cost is
        # already cumulative, so it is the last one's -- summing would say
        # $1.019 -- while the tokens are both invocations'.
        (
            [
                _result("a", 0.316, 2717, 2717),
                _result("a", 0.703, 8694, 11411, total_in=20),
            ],
            Usage(20, 11411, 0.703, "claude-sonnet-5"),
        ),
        # Two CLI sessions in one log: nothing is shared, so both add up.
        (
            [_result("a", 0.25, 5, 5), _result("b", 0.5, 7, 7)],
            Usage(20, 12, 0.75, "claude-sonnet-5"),
        ),
        # One of them reported no cost: the total is unknown, never the
        # other one's figure passed off as the whole.
        (
            [_result("a", 0.25, 5, 5), _result("b", None, 7, 7)],
            Usage(20, 12, None, "claude-sonnet-5"),
        ),
        # No cumulative `modelUsage` to diff: each invocation's own block.
        (
            [
                json.dumps({"usage": {"input_tokens": 1, "output_tokens": 2}}),
                json.dumps({"usage": {"input_tokens": 3, "output_tokens": 4}}),
            ],
            Usage(4, 6),
        ),
        # Shaped like 6c235b8c (Kraft-lp01z): Task-tool progress lines carry a
        # `usage` of their own ({total_tokens, tool_uses, duration_ms}) and
        # are no invocation. Its first turn was killed before writing a
        # result, so only the cumulative `modelUsage` holds what the $34.72
        # paid for; each result's own `usage` holds a sliver of it.
        (
            [_progress("a", 999)] * 3
            + [
                _result("a", 34.72, 21730, 516797, usage_in=1_226_909, total_in=123_006_150),
                _progress("a", 5),
                _result("a", 34.72, 209, 516797, usage_in=156_662, total_in=123_006_150),
                # Killed again mid-turn, a sub-agent still reporting.
                _progress("a", 7),
            ],
            Usage(123_006_150, 516797, 34.72, "claude-sonnet-5"),
        ),
        # A result line reporting nothing (042ce40c: usage all zero, empty
        # modelUsage, cost 0) is no invocation either: read as the last one,
        # its $0 and its empty `modelUsage` would have stood for the session.
        (
            [
                _result("a", 1.0, 10, 100),
                _result("a", 1.0, 5, 100),
                json.dumps(
                    {
                        "type": "result",
                        "session_id": "a",
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                        "modelUsage": {},
                        "total_cost_usd": 0,
                    }
                ),
            ],
            Usage(10, 100, 1.0, "claude-sonnet-5"),
        ),
    ],
    ids=[
        "one-cli-session",
        "two-cli-sessions",
        "one-cost-unknown",
        "no-model-usage",
        "sub-agent-progress-lines",
        "empty-result-line",
    ],
)
def test_a_log_with_several_result_envelopes_counts_every_invocation(tmp_path, lines, expected):
    log = tmp_path / "s.log"
    log.write_text("\n".join(lines) + "\n" + json.dumps({"type": "system"}) + "\n")
    assert usage.read(log, tmp_path / "none.json", "claude-stream-json") == expected


def test_a_single_result_reads_its_cumulative_model_usage():
    """Kraft-s7c04.65: a result's `usage` block is the main agent's last turn
    only; `modelUsage` is the whole session's, Task-tool sub-agents and any
    turn killed before its result included, and it is what `total_cost_usd`
    paid for (193475c9 read 1.80M input tokens where 2.11M were billed)."""
    u = usage.from_envelope(
        {
            "type": "result",
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 40},
            "modelUsage": {
                "claude-haiku-4-5-20251001": {"inputTokens": 12, "outputTokens": 3},
                "claude-opus-5": {
                    "inputTokens": 100,
                    "outputTokens": 30,
                    "cacheReadInputTokens": 1000,
                    "cacheCreationInputTokens": 50,
                },
            },
            "total_cost_usd": 2.5,
        }
    )
    assert u == Usage(112, 33, 2.5, "claude-opus-5", tokens_cache_write=50, tokens_cache_read=1000)


def test_stream_usage_keeps_cache_tokens_apart():
    """The live count splits the way the envelope does, or a running row would
    read one shape and the finished one another."""
    line = json.dumps(
        {
            "type": "assistant",
            "request_id": "req_1",
            "message": {
                "model": "claude-opus-5",
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 700,
                },
            },
        }
    )
    u = usage.from_stream([line, line], {})
    assert (u.tokens_in, u.tokens_cache_write, u.tokens_cache_read, u.tokens_out) == (3, 20, 700, 2)


def test_several_result_envelopes_keep_cache_tokens_apart(tmp_path):
    """`_combine` stands one envelope in for a log's invocations; the kinds
    survive it, in both the cumulative and the per-invocation read."""

    def result(sid, cached, cost=1.0):
        return json.dumps(
            {
                "type": "result",
                "session_id": sid,
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "modelUsage": {
                    "m": {
                        "inputTokens": 5,
                        "outputTokens": 2,
                        "cacheReadInputTokens": cached,
                        "cacheCreationInputTokens": 7,
                    }
                },
                "total_cost_usd": cost,
            }
        )

    log = tmp_path / "s.log"
    log.write_text("\n".join([result("a", 100), result("a", 300, 2.0), result("b", 50)]) + "\n")
    u = usage.read(log, tmp_path / "none.json", "claude-stream-json")
    assert u == Usage(10, 4, 3.0, "m", tokens_cache_write=14, tokens_cache_read=350)
    plain = tmp_path / "p.log"
    block = {"input_tokens": 1, "output_tokens": 1}
    plain.write_text(
        "\n".join(json.dumps({"usage": block | {"cache_read_input_tokens": n}}) for n in (10, 20))
        + "\n"
    )
    assert usage.read(plain, tmp_path / "none.json", "claude-stream-json").tokens_cache_read == 30


async def test_rollup_sums_each_kind_and_says_when_the_split_is_unknown(database):
    """Ruling 211: the rollup sums uncached input, cache writes and cache reads
    apart. A row written before the split has its cache kinds NULL, its
    `tokens_in` the old total: still counted in full, but the rollup says the
    split is incomplete rather than calling all of it uncached."""
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
            "status, created_at, updated_at) VALUES "
            "('w','t','/r','quick-task','{}','active','now','now')"
        )
    )
    split = Usage(10, 5, 0.5, "m", tokens_cache_write=20, tokens_cache_read=300)
    await database.write(lambda c: _session(c, "new", "verify", u=split))
    await database.write(lambda c: _session(c, "free", "env_setup"))
    rollup = database.read(lambda c: store.usage_rollup(c, "w"))
    verify = next(n for n in rollup["by_node"] if n["node"] == "verify")
    assert (verify["tokens_in"], verify["tokens_cache_write"], verify["tokens_cache_read"]) == (
        10,
        20,
        300,
    )
    assert rollup["total"]["split_complete"] is True

    await database.write(lambda c: _session(c, "old", "verify", u=Usage(1000, 1)))
    await database.write(
        lambda c: c.execute(
            "UPDATE worker_sessions SET tokens_cache_write = NULL, tokens_cache_read = NULL "
            "WHERE id = 'old'"
        )
    )
    rollup = database.read(lambda c: store.usage_rollup(c, "w"))
    verify = next(n for n in rollup["by_node"] if n["node"] == "verify")
    env = next(n for n in rollup["by_node"] if n["node"] == "env_setup")
    assert verify["tokens_in"] == 1010
    assert verify["split_complete"] is False
    assert env["split_complete"] is True  # no tokens, nothing to split
    assert rollup["total"]["split_complete"] is False
