"""The CI-wait poller re-enters a node parked on a pipeline."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from support.harness import fake_registry, isolated_bd

from kraft import ci_wait, events, policy, store

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"

_CHAIN = (
    '{"template_id": "quick-task", "nodes": ['
    '{"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": null},'
    '{"id": "mr_checks", "tasks": ["on.ci.poll"], "gate_after": null}]}'
)


def _state(tmp_path, *, ci_wait_cap=None) -> dict:
    """What `ci_wait.tick` reads off `app.state` besides the database (`stub_app`)."""
    return {
        "registry": fake_registry(sys.executable, _FAKE_AGENT),
        "templates_dir": tmp_path / "templates",
        "skills_dir": tmp_path / "skills",
        "policy": policy.Policy(
            loops={"ci_wait": ci_wait_cap} if ci_wait_cap else {},
            default=policy.Cap(attempts=3, wall_clock_s=3600),
        ),
    }


async def _seed_waiting(app, *, retry_at: str, repo: str, wid: str = "w1") -> None:
    await app.state.db.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo=repo,
            chain_template="quick-task",
            chain_definition=_CHAIN,
        )
    )
    await app.state.db.write(lambda c: store.enter_node(c, wid, "mr_checks"))
    await app.state.db.write(lambda c: store.mark_waiting(c, wid, "mr_checks", retry_at))


def _status(app, wid="w1"):
    return app.state.db.read(
        lambda c: c.execute("SELECT status FROM work_items WHERE id=?", (wid,)).fetchone()
    )["status"]


async def test_tick_ignores_a_not_yet_due_item(tmp_path, monkeypatch, repo, stub_app):
    """retry_at in the future: left alone, exactly as rate_limit_retry does."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")

    app = stub_app(**_state(tmp_path))
    await _seed_waiting(app, retry_at="2999-01-01T00:00:00+00:00", repo=str(repo))
    assert await ci_wait.tick(app) == []
    assert _status(app) == "waiting"


async def test_tick_re_enters_a_due_item_at_its_waiting_node(tmp_path, monkeypatch, repo, stub_app):
    """The wait is over: the item goes back to work at the node it parked on."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    isolated_bd(tmp_path)

    app = stub_app(**_state(tmp_path))
    await _seed_waiting(app, retry_at="2000-01-01T00:00:00+00:00", repo=str(repo))
    assert await ci_wait.tick(app) == ["w1"]
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
    assert _status(app) != "waiting"
    types = {e["type"] for e in app.state.db.read(lambda c: events.read_after(c, 0, "w1"))}
    assert "work_item_waiting" in types


async def test_tick_stops_at_needs_human_once_the_cap_breaches(
    tmp_path, monkeypatch, repo, stub_app
):
    """The replacement for today's poll_timeout: same end state, reached through
    the counter machinery that already bounds fix loops. A cap of one attempt
    means the second re-entry is the breach."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    isolated_bd(tmp_path)

    app = stub_app(**_state(tmp_path, ci_wait_cap=policy.Cap(attempts=1, wall_clock_s=3600)))
    await _seed_waiting(app, retry_at="2000-01-01T00:00:00+00:00", repo=str(repo))
    await ci_wait.tick(app)
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
    # park it again, as a still-pending pipeline would
    await app.state.db.write(
        lambda c: store.mark_waiting(c, "w1", "mr_checks", "2000-01-01T00:00:00+00:00")
    )
    assert await ci_wait.tick(app) == []
    assert _status(app) == "needs_human"


async def test_tick_ignores_an_item_that_was_paused_while_waiting(
    tmp_path, monkeypatch, repo, stub_app
):
    """Kraft-tnak's other half. Pause clears retry_at and moves the row off
    'waiting' (Task 4) -- waking it anyway would be the exact bug this bead is
    about, so assert it from the poller's side too."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")

    app = stub_app(**_state(tmp_path))
    await _seed_waiting(app, retry_at="2000-01-01T00:00:00+00:00", repo=str(repo))
    await app.state.db.write(lambda c: store.pause_work_item(c, "w1", []))
    assert await ci_wait.tick(app) == []
    assert _status(app) == "paused"


async def test_tick_does_not_re_enter_a_row_its_own_previous_tick_already_claimed(
    tmp_path, monkeypatch, repo, stub_app
):
    """Kraft-ppk9: a repair that takes longer than one 30s poller interval
    must not spawn a second one for the same wait. Deliberately does NOT
    await the first tick's spawned task before calling tick() again -- that
    gap is exactly the race that used to fire twice."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    isolated_bd(tmp_path)

    app = stub_app(**_state(tmp_path))
    await _seed_waiting(app, retry_at="2000-01-01T00:00:00+00:00", repo=str(repo))
    first = await ci_wait.tick(app)
    assert first == ["w1"]
    # The spawned run hasn't been awaited yet -- if status were still
    # 'waiting', this second tick would re-select the same row.
    second = await ci_wait.tick(app)
    assert second == []
    assert _status(app) != "waiting"


async def test_a_ci_wait_reentry_resumes_at_the_waiting_group(
    tmp_path, monkeypatch, repo, stub_app
):
    """Re-entry recomputed start from current_node_id alone, so a node whose
    waiting step was its fourth re-ran the first three -- a paid agent session
    per poll tick on a node that opens the MR. The poller now hands the walk no
    position at all: `walk.run_once` reads the item's cursor itself
    (tests/executor/test_entry_paths.py)."""
    from kraft import executor

    seen = {}

    async def fake_run(*args, **kw):
        seen.update(kw)
        return "waiting"

    monkeypatch.setattr(executor, "run", fake_run)

    app = stub_app(**_state(tmp_path))
    await _seed_waiting(app, retry_at="2000-01-01T00:00:00+00:00", repo=str(repo))
    await app.state.db.write(lambda c: store.set_current_step(c, "w1", 3))
    assert await ci_wait.tick(app) == ["w1"]
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
    assert "start_index" not in seen and "start_step" not in seen
    assert seen["work_item_id"] == "w1"


async def test_a_v1_item_is_re_entered_rather_than_stranded_waiting(
    tmp_path, monkeypatch, repo, stub_app
):
    """H1's poller half. `chain_definition` is `"{}"` on a V1 row, so the old
    `next(i for i, n in enumerate(chain["nodes"]) ...)` raised -- inside a
    poller tick, after `mark_reentered` had already bumped the counter. The item
    sat `waiting` forever with nothing coming for it, and the only trace was a
    log line. The index comes from `store.node_index` now, over either shape.
    """
    from support.harness import v1_chain, v1_item

    chain = v1_chain(
        [
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [{"id": "build", "kind": "subprocess", "command": "true"}],
            },
            {
                "id": "mr_checks",
                "kind": "exec",
                "tasks": [{"id": "poll", "kind": "subprocess", "command": "true"}],
            },
        ],
        repo=repo,
    )
    spawned: list[int] = []

    app = stub_app(**_state(tmp_path))
    await v1_item(app.state.db, chain, repo=str(repo), wid="w1")
    await app.state.db.write(lambda c: store.enter_node(c, "w1", "mr_checks"))
    await app.state.db.write(
        lambda c: store.mark_waiting(c, "w1", "mr_checks", "2000-01-01T00:00:00+00:00")
    )
    from kraft.api import deps as api_deps

    def spawn(app, wid, coro):
        spawned.append(1)
        api_deps.discard(coro)  # never run: close it, or it leaks unawaited

    monkeypatch.setattr(api_deps, "spawn", spawn)
    await ci_wait.tick(app)
    assert spawned, "the poller found no node index and left the item waiting"


async def test_a_node_the_chain_does_not_have_stops_the_item_rather_than_wedging_it(
    tmp_path, repo, stub_app
):
    """N1. `mark_reentered` has already flipped the row to `active` by the time
    the start index is read, and `tick`'s own SELECT filters
    `status = 'waiting'` -- so a return that leaves it `active` means no later
    tick will ever select this row again. Permanently wedged, looking live, with
    one log line to show for it.

    The stop is the bracket's (`stops.claimed_or_stopped`), not this branch's, so
    the same guarantee covers the exits no static sweep can enumerate.
    """
    from support.harness import v1_chain, v1_item

    chain = v1_chain(
        [
            {
                "id": "mr_checks",
                "kind": "exec",
                "tasks": [{"id": "poll", "kind": "subprocess", "command": "true"}],
            }
        ],
        repo=repo,
    )

    app = stub_app(**_state(tmp_path))
    await v1_item(app.state.db, chain, repo=str(repo), wid="w1")
    # A current node this chain does not have -- the state a template switch
    # or a legacy row leaves behind, and the case the `start is None` branch
    # was written for.
    await app.state.db.write(lambda c: store.enter_node(c, "w1", "gone_from_the_chain"))
    await app.state.db.write(
        lambda c: store.mark_waiting(c, "w1", "gone_from_the_chain", "2000-01-01T00:00:00+00:00")
    )
    assert await ci_wait.tick(app) == []
    result = _status(app)
    assert result == "needs_human"
