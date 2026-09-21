"""A budget cap refuses the next agent launch (sub-project E §1-§3).

It cannot interrupt a running agent: cost only exists once the session exits.
Everything here tests the refusal of a *subsequent* launch.
"""

import asyncio
import sys
import uuid
from pathlib import Path

from support.harness import (
    isolated_bd,
    v1_fix_loop_node,
    v1_resolved,
    v1_seeded_chain,
)

from kraft import events, executor, policy, store
from kraft.executor import dispatch as dispatch_module

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE = f"{sys.executable} {_FAKE_AGENT}"
_SUITE = {"id": "suite", "kind": "subprocess", "command": f"{sys.executable} -m pytest -q"}
_AGENT = {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "Implement it."}
#: Where each task's sessions land (its canonical path).
_AGENT_PATH = "work.main.implement"
_SUITE_PATH = "work.main.suite"


def _work_node() -> dict:
    """One agent task and one subprocess task in the same node — which is what
    makes "the agent is blocked and the subprocess is not" observable."""
    return {"id": "work", "kind": "exec", "tasks": [_AGENT, _SUITE]}


def _template(tmp_path):
    """The `work` node. No `env_setup` node: V1 prepares the worktree first."""
    return v1_seeded_chain(tmp_path / "templates", [_work_node()], agent_command=_FAKE)


def _fixloop_template(tmp_path):
    """A fix_loop node measured by a subprocess only, so the node always reaches
    the fix-cycle agent dispatch — the one budget-gated launch a fix_loop has."""
    return v1_seeded_chain(
        tmp_path / "templates", [v1_fix_loop_node("verify", _SUITE)], agent_command=_FAKE
    )


def _policy(*, work_item_usd=None, daily_usd=None) -> policy.Policy:
    return policy.Policy(
        loops={},
        default=policy.Cap(attempts=3, wall_clock_s=3600),
        budget=policy.Budget(work_item_usd=work_item_usd, daily_usd=daily_usd),
        # This suite is about budget refusal, not the unrelated auto-escalate
        # trigger -- an item stopped by a budget breach is a needs_human stop
        # this feature would otherwise also try to auto-dispatch onto.
        auto_escalate_stuck=False,
    )


async def _spend(database, wid: str, usd: float) -> None:
    """Record a finished session that cost `usd`, as usage capture would have."""
    sid = uuid.uuid4().hex
    await database.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id=wid,
            node_id="prior",
            hook_point="on.implementation.start",
            log_path="/tmp/l",
            result_path="/tmp/r",
        )
    )
    await database.write(
        lambda c: c.execute(
            "UPDATE worker_sessions SET cost_usd = ?, status = 'done' WHERE id = ?", (usd, sid)
        )
    )


def _needs_human_payload(database, wid):
    evts = database.read(lambda c: events.read_after(c, 0, wid))
    return next(e["payload"] for e in reversed(evts) if e["type"] == "work_item_needs_human")


def _budget_stopped(database, wid) -> bool:
    """True iff any escalation for this item blamed a spend cap."""
    evts = database.read(lambda c: events.read_after(c, 0, wid))
    return any("budget" in e["payload"] for e in evts if e["type"] == "work_item_needs_human")


def _hook_points(database, wid, node_id) -> list[str]:
    rows = database.read(
        lambda c: c.execute(
            "SELECT hook_point FROM worker_sessions WHERE work_item_id = ? AND node_id = ?",
            (wid, node_id),
        ).fetchall()
    )
    return [r["hook_point"] for r in rows]


def _status(database, wid) -> str:
    return database.read(
        lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
    )["status"]


async def _intake(database, rd, tracker, repo, template=None):
    return await executor.intake(
        database,
        rd,
        title="budgeted work",
        repo=str(repo),
        chain=template or _template(Path(repo).parent),
        bd_cwd=str(tracker),
    )


async def _run(database, rd, tracker, wid, pol):
    return await executor.run(
        database,
        rd,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        policy=pol,
    )


async def test_under_the_cap_the_agent_launches(tmp_path, monkeypatch, database, run_dirs, repo):
    """$5 spent against a $20 cap runs normally."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)

    wid = await _intake(database, run_dirs, tracker, repo)
    await _spend(database, wid, 3.0)
    await _spend(database, wid, 2.0)
    await _run(database, run_dirs, tracker, wid, _policy(work_item_usd=20.0))
    assert not _budget_stopped(database, wid)
    assert _AGENT_PATH in _hook_points(database, wid, "work")


async def test_over_the_cap_refuses_the_next_launch(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """$25 spent against a $20 cap stops the item with a budget reason."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)

    wid = await _intake(database, run_dirs, tracker, repo)
    await _spend(database, wid, 25.0)
    result = await _run(database, run_dirs, tracker, wid, _policy(work_item_usd=20.0))
    assert result == "needs_human"
    assert _status(database, wid) == "needs_human"
    payload = _needs_human_payload(database, wid)
    assert payload["budget"] == {
        "scope": "work_item",
        "spent_usd": 25.0,
        "cap_usd": 20.0,
    }
    assert "budget" in payload["reason"] and "20" in payload["reason"]


async def test_a_null_cap_never_blocks(tmp_path, monkeypatch, database, run_dirs, repo):
    """$1000 spent with both caps null runs anyway."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)

    wid = await _intake(database, run_dirs, tracker, repo)
    await _spend(database, wid, 1000.0)
    await _run(database, run_dirs, tracker, wid, _policy())
    assert not _budget_stopped(database, wid)
    assert _AGENT_PATH in _hook_points(database, wid, "work")


async def test_the_subprocess_task_in_the_same_node_still_runs(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """The agent is refused; the subprocess in the same node has a session row.

    Budget-blocking a subprocess would strand the item mid-node for no saving.
    """
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)

    wid = await _intake(database, run_dirs, tracker, repo)
    await _spend(database, wid, 25.0)
    await _run(database, run_dirs, tracker, wid, _policy(work_item_usd=20.0))
    hooks = _hook_points(database, wid, "work")
    assert _SUITE_PATH in hooks
    assert _AGENT_PATH not in hooks


async def test_the_daily_cap_stops_an_item_that_has_spent_nothing(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """Another item's spend today breaches the daily cap for this one."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)

    wid = await _intake(database, run_dirs, tracker, repo)
    other = uuid.uuid4().hex
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=other,
            bead_id="TEST-other",
            title="somebody else's expensive item",
            repo=str(repo),
            chain_template="budget",
            chain_definition="{}",
        )
    )
    await _spend(database, other, 150.0)
    result = await _run(database, run_dirs, tracker, wid, _policy(daily_usd=100.0))
    assert result == "needs_human"
    payload = _needs_human_payload(database, wid)
    assert payload["budget"] == {"scope": "daily", "spent_usd": 150.0, "cap_usd": 100.0}


async def test_yesterdays_spend_does_not_count_against_todays_daily_cap(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """Rollover at local midnight."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)

    wid = await _intake(database, run_dirs, tracker, repo)
    await _spend(database, wid, 150.0)
    await database.write(
        lambda c: c.execute("UPDATE worker_sessions SET created_at = '2020-01-01T00:00:00+00:00'")
    )
    await _run(database, run_dirs, tracker, wid, _policy(daily_usd=100.0))
    assert not _budget_stopped(database, wid)
    assert _AGENT_PATH in _hook_points(database, wid, "work")


async def test_a_fix_cycle_refused_for_budget_costs_no_attempt(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """The refusal happens before the counter bump and before `fix_cycle_started`.

    The fix agent is a fix_loop's only budget-gated launch, so if the check sat
    only inside `_dispatch` the item would spend an attempt — and log a cycle —
    for an agent that never ran, and a later retry would read that phantom cycle
    back as "no progress".
    """
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)

    wid = await _intake(database, run_dirs, tracker, repo, _fixloop_template(tmp_path))
    await _spend(database, wid, 25.0)
    result = await _run(database, run_dirs, tracker, wid, _policy(work_item_usd=20.0))
    assert result == "needs_human"
    payload = _needs_human_payload(database, wid)
    assert payload["budget"]["scope"] == "work_item"
    # the measuring subprocess ran (it costs nothing); the fix agent did not
    assert _hook_points(database, wid, "verify") == ["verify.main.suite"]
    assert database.read(lambda c: store.read_counter(c, wid, "verify.fix_loop")) is None
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]
    assert "fix_cycle_started" not in types


def test_a_co_task_exception_is_logged_even_when_budget_wins(monkeypatch, caplog):
    """The BUDGET rung returns early, so the traceback of a co-task that raised in
    the same node is the only record that it ever happened. Precedence is still
    budget over that failure (amendment A3) — but the log line is not optional.
    """

    class _NoopDb:
        async def write(self, fn):
            return None

    async def fake_dispatch_node(db, run_dirs, task, node, row, worktree, **kw):
        if task.task.id == "suite":
            raise RuntimeError("co-task blew up")
        return executor.BUDGET

    monkeypatch.setattr(dispatch_module, "dispatch_node", fake_dispatch_node)
    node = v1_resolved([_work_node()]).nodes[0]
    with caplog.at_level("ERROR", logger="kraft.executor"):
        verdict, failed, excs = asyncio.run(
            executor.measure_node(_NoopDb(), None, "w1", node, None, None)
        )
    assert verdict == executor.BUDGET
    assert failed == [] and excs == []
    assert "co-task blew up" in caplog.text


def test_shipped_policy_has_real_spend_caps():
    """The packaged default must never ship uncapped (Kraft-9oq).

    Read from the repo's own templates/ rather than a seeded home: this pins
    what a fresh install *gets*, and a seeded home is whatever the developer
    running the suite happens to have.
    """
    shipped = Path(__file__).parents[1] / "templates" / "policy.yaml"
    loaded = policy.load_policy(shipped)
    assert loaded.budget.work_item_usd == 10
    assert loaded.budget.daily_usd == 50
