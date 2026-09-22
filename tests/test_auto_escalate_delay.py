"""The auto-escalate delay poller: a backstop for awaiting_gate/needs_human
items whose configured delay elapsed after the inline call from
run()/resume() already returned."""

from __future__ import annotations

import asyncio
from pathlib import Path

from support.harness import v1_chain, v1_item

from kraft import auto_escalate_delay, policy, store

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _gate_chain(repo):
    """`a` (exec) -> `g` (a gate that declares its own reviewer)."""
    return v1_chain(
        [
            {
                "id": "a",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            },
            {
                "id": "g",
                "kind": "gate",
                "auto_review": {
                    "id": "reviewer",
                    "kind": "agent",
                    "harness": "claude",
                    "prompt": "Review it.",
                },
            },
        ],
        repo=repo,
    )


def _state(tmp_path, *, policy_obj=None) -> dict:
    """What `auto_escalate.tick` reads off `app.state` besides the database (`stub_app`)."""
    return {
        "registry": None,
        "templates_dir": tmp_path / "templates",
        "skills_dir": tmp_path / "skills",
        "policy": policy_obj
        or policy.Policy(loops={}, default=policy.Cap(attempts=3, wall_clock_s=3600)),
    }


async def _seed_awaiting_gate(app, repo, *, wid="w1", auto_gate=True) -> None:
    await v1_item(app.state.db, _gate_chain(repo), repo=repo, wid=wid, auto_gate=auto_gate)
    # A V1 gate is its own node, and the walk stands on it.
    await app.state.db.write(lambda c: store.enter_node(c, wid, "g"))
    await app.state.db.write(lambda c: store.request_gate(c, wid, "g", "g"))


async def test_tick_leaves_an_awaiting_gate_item_alone_before_its_delay_elapses(
    tmp_path, monkeypatch, repo, stub_app
):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600)

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    await _seed_awaiting_gate(app, str(repo))
    # Not even spawned: `tick` applies the delay/armed pre-filter itself
    # rather than spending a slot on a call that could only return the
    # status unchanged (code review finding).
    attempted = await auto_escalate_delay.tick(app)
    assert attempted == []
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
    # A pending gate's own DB status is 'needs_human' -- the work_items
    # CHECK constraint has no separate 'awaiting_gate' value; that string
    # only ever exists as the in-memory status review_gates is seeded
    # with. Unchanged here is what "delay not elapsed, no-op" looks like.
    row = app.state.db.read(
        lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
    )
    assert row["status"] == "needs_human"


async def test_tick_dispatches_an_awaiting_gate_item_once_its_delay_has_elapsed(
    tmp_path, monkeypatch, repo, stub_app
):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    calls = []

    async def fake_review(*a, **kw):
        calls.append(1)
        return "undecided", None

    monkeypatch.setattr("kraft.executor.gates.gate_review.review", fake_review)
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600)

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    await _seed_awaiting_gate(app, str(repo))
    await auto_escalate_delay.tick(app)
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
    assert len(calls) == 1


async def test_tick_dispatches_a_needs_human_item_once_its_delay_has_elapsed(
    tmp_path, monkeypatch, repo, stub_app
):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(1)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600)

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    chain = '{"template_id": "t", "nodes": [{"id": "a", "tasks": []}]}'
    await app.state.db.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id=None,
            title="t",
            repo=str(repo),
            chain_template="t",
            chain_definition=chain,
        )
    )
    await app.state.db.write(lambda c: store.enter_node(c, "w1", "a"))
    await app.state.db.write(lambda c: store.mark_needs_human(c, "w1", "a", "stuck", stuck=True))
    await auto_escalate_delay.tick(app)
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
    assert len(calls) == 1


async def test_tick_bounds_dispatches_by_max_concurrent(tmp_path, monkeypatch, repo, stub_app):
    """A backlog of `needs_human` rows parked before this feature existed must
    not all fire at once (code review finding): `max_concurrent` bounds this
    poller's dispatches the same way it already bounds `intake.tick` and a
    manual resume."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(work_item_id)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(
        loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600, max_concurrent=1
    )

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    chain = '{"template_id": "t", "nodes": [{"id": "a", "tasks": []}]}'
    for wid in ("w1", "w2"):
        await app.state.db.write(
            lambda c, wid=wid: store.create_work_item(
                c,
                id=wid,
                bead_id=None,
                title="t",
                repo=str(repo),
                chain_template="t",
                chain_definition=chain,
            )
        )
        await app.state.db.write(lambda c, wid=wid: store.enter_node(c, wid, "a"))
        await app.state.db.write(
            lambda c, wid=wid: store.mark_needs_human(c, wid, "a", "stuck", stuck=True)
        )
    attempted = await auto_escalate_delay.tick(app)
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
    assert len(attempted) == 1
    assert len(calls) == 1


async def test_tick_bounds_dispatches_across_ticks_via_live_tasks(
    tmp_path, monkeypatch, repo, stub_app
):
    """`active_count` alone only holds inside one tick: an auto-escalate turn
    never makes its row `active`, so a session a previous tick spawned is
    invisible to a later tick's budget unless that budget also counts live
    entries in `app.state.tasks` (code review finding). Here w2's task from
    an earlier tick is still running when this tick fires; with
    `max_concurrent=1` that must leave zero slots for w1, even though w1's
    own row is `needs_human` and due, same as w2's was."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(work_item_id)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(
        loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600, max_concurrent=1
    )

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    chain = '{"template_id": "t", "nodes": [{"id": "a", "tasks": []}]}'
    for wid in ("w1", "w2"):
        await app.state.db.write(
            lambda c, wid=wid: store.create_work_item(
                c,
                id=wid,
                bead_id=None,
                title="t",
                repo=str(repo),
                chain_template="t",
                chain_definition=chain,
            )
        )
        await app.state.db.write(lambda c, wid=wid: store.enter_node(c, wid, "a"))
        await app.state.db.write(
            lambda c, wid=wid: store.mark_needs_human(c, wid, "a", "stuck", stuck=True)
        )
    # w2's dispatch from a previous tick is still in flight: its row is
    # still `needs_human` (an auto-escalate turn never flips it to
    # `active`), so only `app.state.tasks` still knows it is running.
    placeholder = asyncio.get_event_loop().create_future()
    app.state.tasks["w2"] = placeholder
    try:
        attempted = await auto_escalate_delay.tick(app)
        assert attempted == []
        assert calls == []
    finally:
        placeholder.cancel()
        del app.state.tasks["w2"]


async def test_tick_skips_a_row_with_a_live_task_already_running(
    tmp_path, monkeypatch, repo, stub_app
):
    """Two ticks 30s apart must not both dispatch: `review_gates`'s own
    docstring calls a second concurrent run for one item the failure this
    feature most has to avoid, and a slow `gate_review.review` (an agent
    call) leaves `status` at `awaiting_gate` the whole time it runs."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600)

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    await _seed_awaiting_gate(app, str(repo))
    placeholder = asyncio.get_event_loop().create_future()
    app.state.tasks["w1"] = placeholder
    try:
        attempted = await auto_escalate_delay.tick(app)
        assert attempted == []
    finally:
        placeholder.cancel()
        del app.state.tasks["w1"]


async def test_tick_skips_a_row_whose_effective_delay_is_zero(
    tmp_path, monkeypatch, repo, stub_app
):
    """The default (0) means the inline call from run()/resume() already
    fired immediately, so there is nothing for this backstop to back-stop.
    Checking those rows anyway would hand a stuck item a fresh
    `auto_escalate_stuck` turn every interval up to the cap, where before
    this poller existed it got one per run()/resume() (code review
    finding)."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(work_item_id)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=0)

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    chain = '{"template_id": "t", "nodes": [{"id": "a", "tasks": []}]}'
    await app.state.db.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id=None,
            title="t",
            repo=str(repo),
            chain_template="t",
            chain_definition=chain,
        )
    )
    await app.state.db.write(lambda c: store.enter_node(c, "w1", "a"))
    await app.state.db.write(lambda c: store.mark_needs_human(c, "w1", "a", "stuck", stuck=True))
    assert await auto_escalate_delay.tick(app) == []
    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
    assert calls == []


async def test_tick_skips_a_row_that_is_not_armed_for_auto_escalation(
    tmp_path, monkeypatch, repo, stub_app
):
    """A row parked forever with `auto_escalate_stuck: false` can only return
    its status unchanged, so it must not spend a `max_concurrent` slot (or
    briefly hold the item's task slot, which 409s a human's retry) every
    tick (code review finding)."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(
        loops={},
        default=policy.Cap(3, 3600),
        auto_escalate_delay_s=600,
        auto_escalate_stuck=False,
    )

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    chain = '{"template_id": "t", "nodes": [{"id": "a", "tasks": []}]}'
    await app.state.db.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id=None,
            title="t",
            repo=str(repo),
            chain_template="t",
            chain_definition=chain,
        )
    )
    await app.state.db.write(lambda c: store.enter_node(c, "w1", "a"))
    await app.state.db.write(lambda c: store.mark_needs_human(c, "w1", "a", "stuck", stuck=True))
    assert await auto_escalate_delay.tick(app) == []


async def test_tick_skips_a_row_stopped_outside_the_stuck_set(
    tmp_path, monkeypatch, repo, stub_app
):
    """Armed, and past its delay, but stopped for something no agent can fix
    (Ruling 176): `auto_escalate_stuck` would only hand the status back, so
    the tick must not spend a slot on it either."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600)

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    chain = '{"template_id": "t", "nodes": [{"id": "a", "tasks": []}]}'
    await app.state.db.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id=None,
            title="t",
            repo=str(repo),
            chain_template="t",
            chain_definition=chain,
        )
    )
    await app.state.db.write(lambda c: store.enter_node(c, "w1", "a"))
    await app.state.db.write(lambda c: store.mark_needs_human(c, "w1", "a", "budget cap reached"))
    assert await auto_escalate_delay.tick(app) == []


async def test_a_legacy_row_does_not_abort_the_tick_for_every_other_row(
    tmp_path, monkeypatch, repo, stub_app
):
    """A legacy row with a pending gate and a delay > 0 makes `auto_check_due`
    walk the V1 chain, and `walk.chain_of` raises `LookupError` for a row the
    legacy intake path wrote. Uncaught, that aborted the whole scan -- every
    `_INTERVAL_S`, forever -- and starved every other due row behind it.

    Two rows: the legacy one and a V1 one that is genuinely due. The V1 row must
    be dispatched whichever order `ORDER BY RANDOM()` hands them over, so the
    legacy row is deliberately the first one seeded.
    """
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=0)
    # Off the clock, the same seam the sibling delay tests use.
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(work_item_id)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    # A legacy row: `chain_definition` only, and a per-node delay so it gets
    # past `tick`'s own zero-delay filter and reaches `auto_check_due`.
    await _seed_awaiting_gate(app, str(repo), wid="legacy")
    await app.state.db.write(
        lambda c: store.set_node_overrides(c, "legacy", {"a": {"auto_escalate_delay_s": 1}})
    )
    # A V1 row that is due: stopped, armed, past a delay of 1s.
    await app.state.db.write(
        lambda c: store.create_work_item(
            c,
            id="v1",
            bead_id=None,
            title="t",
            repo=str(repo),
            chain_template="t",
            chain_definition="{}",
            materialized_chain=_v1_stuck_chain(repo).to_json(),
        )
    )
    await app.state.db.write(lambda c: store.enter_node(c, "v1", "implementation"))
    await app.state.db.write(
        lambda c: store.set_node_overrides(
            c, "v1", {"implementation": {"auto_escalate_delay_s": 1}}
        )
    )
    await app.state.db.write(
        lambda c: store.mark_needs_human(c, "v1", "implementation", "verify failed", stuck=True)
    )
    attempted = await auto_escalate_delay.tick(app)

    await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
    # The legacy row is skipped, not raised on, and the V1 row still gets its
    # turn. Without the per-row guard `tick` raises out of the loop and
    # `attempted` is never returned at all.
    assert attempted == ["v1"]
    assert calls == ["v1"]


def _v1_stuck_chain(repo):
    from support.harness import v1_chain

    return v1_chain(
        [
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            }
        ],
        repo=repo,
    )
