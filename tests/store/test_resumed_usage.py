"""A session that resumes an earlier one's CLI session records only its own
spend (Kraft-s7c04.62). The CLI reports cost, and `modelUsage` tokens,
cumulatively over every invocation of one session id, so the resumed row's
envelope repeats what the rows before it already recorded."""

from __future__ import annotations

import json

import pytest
from support.store_fixtures import mk_item

from kraft import store
from kraft import usage as _usage


@pytest.fixture
async def database(database):
    await mk_item(database)
    return database


def _log(path, cli: str | None, *envelopes: dict) -> str:
    lines = [{"type": "system", "subtype": "init", "session_id": cli}] if cli else []
    lines += [{"type": "result", "session_id": cli, **e} for e in envelopes]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return str(path)


def _envelope(own_in, own_out, total_in, total_out, cost):
    """`usage` is this invocation's; `modelUsage` and the cost are the CLI
    session's running totals."""
    env = {
        "usage": {"input_tokens": own_in, "output_tokens": own_out},
        "modelUsage": {"claude-opus-5": {"inputTokens": total_in, "outputTokens": total_out}},
    }
    return env | ({"total_cost_usd": cost} if cost is not None else {})


async def _turn(
    database, tmp_path, sid, cli, *envelopes, hook="implementation.main.build", paused=False
):
    """One session on `hook`, exited through the real usage reader -- after an
    operator paused it, when `paused`."""
    log = _log(tmp_path / f"{sid}.log", cli, *envelopes)
    await database.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id="w1",
            node_id="implementation",
            hook_point=hook,
            log_path=log,
            result_path=str(tmp_path / f"{sid}.json"),
        )
    )
    if paused:
        await database.write(lambda c: store.pause_work_item(c, "w1", [sid]))
    seen = _usage.read(tmp_path / f"{sid}.log", tmp_path / f"{sid}.json", "claude-stream-json")
    await database.write(lambda c: store.session_exited(c, sid, "done", None, seen))


def _spent(database, sid):
    row = database.read(
        lambda c: c.execute(
            "SELECT tokens_in, tokens_out, cost_usd FROM worker_sessions WHERE id = ?", (sid,)
        ).fetchone()
    )
    return row["tokens_in"], row["tokens_out"], row["cost_usd"]


async def test_a_resumed_session_records_only_what_it_spent_itself(database, tmp_path):
    """`a-resumed-cli-session-is-counted-once`: the task spent $1.00 and 120
    input tokens before a pause, was resumed and paused again at $1.30 and 150
    for the whole CLI session, and finished at $1.50 and 170. Each row records
    its own share, and the item's rollup is the CLI session's total, not
    $3.80."""
    await _turn(database, tmp_path, "a", "cli-x", _envelope(0, 0, 120, 12, 1.00), paused=True)
    await _turn(database, tmp_path, "b", "cli-x", _envelope(0, 0, 150, 15, 1.30), paused=True)
    await _turn(database, tmp_path, "c", "cli-x", _envelope(20, 2, 170, 17, 1.50))

    assert _spent(database, "b") == (30, 3, pytest.approx(0.30))
    assert _spent(database, "c") == (20, 2, pytest.approx(0.20))
    total = database.read(lambda c: store.usage_rollup(c, "w1"))["total"]
    assert (total["tokens_in"], total["tokens_out"]) == (170, 17)
    assert total["cost_usd"] == pytest.approx(1.50)
    assert total["cost_complete"] is True


async def test_a_resumed_escalation_turn_skips_a_refused_turn_between(database, tmp_path):
    """Escalation turns resume one CLI session turn after turn; a turn refused
    before launch (no CLI session in its log) sits between them and must not
    hide the turn the next one resumed."""
    esc = {"hook": "escalation"}
    await _turn(database, tmp_path, "t1", "cli-x", _envelope(40, 4, 40, 4, 0.40), **esc)
    await _turn(database, tmp_path, "t2", None, **esc)
    await _turn(database, tmp_path, "t3", "cli-x", _envelope(10, 1, 50, 5, 0.55), **esc)

    assert _spent(database, "t3") == (10, 1, pytest.approx(0.15))


async def test_an_unknown_earlier_cost_leaves_the_resumed_cost_unknown(database, tmp_path):
    """Unknown cost is never free: the paused turn flushed no envelope, so
    what the resumed one added cannot be told apart from what came before,
    and the rollup says it is a floor."""
    await _turn(database, tmp_path, "a", "cli-x")
    await _turn(database, tmp_path, "b", "cli-x", _envelope(30, 3, 150, 15, 1.30))

    assert _spent(database, "b")[2] is None
    assert database.read(lambda c: store.usage_rollup(c, "w1"))["total"]["cost_complete"] is False


async def test_a_new_cli_session_is_not_netted_against_an_earlier_one(database, tmp_path):
    """A retry restarts the task in a fresh CLI session: nothing in its
    envelope was recorded before, so nothing comes off."""
    await _turn(database, tmp_path, "a", "cli-x", _envelope(120, 12, 120, 12, 1.00))
    await _turn(database, tmp_path, "b", "cli-y", _envelope(30, 3, 30, 3, 0.30))

    assert _spent(database, "b") == (30, 3, pytest.approx(0.30))


async def test_a_resumed_session_nets_each_kind_of_token(database, tmp_path):
    """The cache kinds are running totals too (Ruling 211): each one nets
    against what the earlier turn recorded, not just the uncached input."""

    def cached(write, read, cost):
        return {
            "modelUsage": {
                "claude-opus-5": {
                    "inputTokens": 10,
                    "outputTokens": 1,
                    "cacheCreationInputTokens": write,
                    "cacheReadInputTokens": read,
                }
            },
            "total_cost_usd": cost,
        }

    await _turn(database, tmp_path, "a", "cli-x", cached(100, 1000, 1.0), paused=True)
    await _turn(database, tmp_path, "b", "cli-x", cached(130, 1600, 1.2))

    row = database.read(
        lambda c: c.execute(
            "SELECT tokens_cache_write, tokens_cache_read FROM worker_sessions WHERE id = 'b'"
        ).fetchone()
    )
    assert tuple(row) == (30, 600)
