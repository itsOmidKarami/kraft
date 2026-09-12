"""The auto-escalate delay poller: a backstop for awaiting_gate/needs_human
items whose configured delay elapsed after the inline call from
run()/resume() already returned."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from support.harness import fake_registry, make_repo

from kraft import auto_escalate_delay, db, policy, store
from kraft.paths import RunDirs

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"

_GATE_CHAIN = (
    '{"template_id": "t", "nodes": ['
    '{"id": "a", "tasks": [], "gate_after": "g", "auto_escalate": true}]}'
)


@dataclass
class _Stub:
    state: SimpleNamespace


async def _stub(tmp_path, *, policy_obj=None) -> _Stub:
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    registry = fake_registry(sys.executable, _FAKE_AGENT)
    return _Stub(
        state=SimpleNamespace(
            db=database,
            run_dirs=rd,
            registry=registry,
            templates_dir=tmp_path / "templates",
            skills_dir=tmp_path / "skills",
            policy=policy_obj
            or policy.Policy(loops={}, default=policy.Cap(attempts=3, wall_clock_s=3600)),
            tasks={},
        )
    )


def _run(build, body):
    async def main():
        app = await build()
        try:
            return await body(app)
        finally:
            await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
            await app.state.db.close()

    return asyncio.run(main())


async def _seed_awaiting_gate(app, repo, *, wid="w1", auto_gate=True) -> None:
    await app.state.db.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo=repo,
            chain_template="t",
            chain_definition=_GATE_CHAIN,
            auto_gate=auto_gate,
        )
    )
    await app.state.db.write(lambda c: store.enter_node(c, wid, "a"))
    await app.state.db.write(lambda c: store.request_gate(c, wid, "a", "g"))


def test_tick_leaves_an_awaiting_gate_item_alone_before_its_delay_elapses(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600)

    def build():
        return _stub(tmp_path, policy_obj=pol)

    async def body(app):
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

    _run(build, body)


def test_tick_dispatches_an_awaiting_gate_item_once_its_delay_has_elapsed(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)
    calls = []

    async def fake_review(*a, **kw):
        calls.append(1)
        return "undecided", None

    monkeypatch.setattr("kraft.executor.gates.gate_review.review", fake_review)
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600)

    def build():
        return _stub(tmp_path, policy_obj=pol)

    async def body(app):
        await _seed_awaiting_gate(app, str(repo))
        await auto_escalate_delay.tick(app)
        await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
        assert len(calls) == 1

    _run(build, body)


def test_tick_dispatches_a_needs_human_item_once_its_delay_has_elapsed(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(1)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600)

    def build():
        return _stub(tmp_path, policy_obj=pol)

    async def body(app):
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
        await app.state.db.write(lambda c: store.mark_needs_human(c, "w1", "a", "stuck"))
        await auto_escalate_delay.tick(app)
        await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
        assert len(calls) == 1

    _run(build, body)


def test_tick_bounds_dispatches_by_max_concurrent(tmp_path, monkeypatch):
    """A backlog of `needs_human` rows parked before this feature existed must
    not all fire at once (code review finding): `max_concurrent` bounds this
    poller's dispatches the same way it already bounds `intake.tick` and a
    manual resume."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(work_item_id)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(
        loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600, max_concurrent=1
    )

    def build():
        return _stub(tmp_path, policy_obj=pol)

    async def body(app):
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
                lambda c, wid=wid: store.mark_needs_human(c, wid, "a", "stuck")
            )
        attempted = await auto_escalate_delay.tick(app)
        await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
        assert len(attempted) == 1
        assert len(calls) == 1

    _run(build, body)


def test_tick_bounds_dispatches_across_ticks_via_live_tasks(tmp_path, monkeypatch):
    """`active_count` alone only holds inside one tick: an auto-escalate turn
    never makes its row `active`, so a session a previous tick spawned is
    invisible to a later tick's budget unless that budget also counts live
    entries in `app.state.tasks` (code review finding). Here w2's task from
    an earlier tick is still running when this tick fires; with
    `max_concurrent=1` that must leave zero slots for w1, even though w1's
    own row is `needs_human` and due, same as w2's was."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(work_item_id)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(
        loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600, max_concurrent=1
    )

    def build():
        return _stub(tmp_path, policy_obj=pol)

    async def body(app):
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
                lambda c, wid=wid: store.mark_needs_human(c, wid, "a", "stuck")
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

    _run(build, body)


def test_tick_skips_a_row_with_a_live_task_already_running(tmp_path, monkeypatch):
    """Two ticks 30s apart must not both dispatch: `review_gates`'s own
    docstring calls a second concurrent run for one item the failure this
    feature most has to avoid, and a slow `gate_review.review` (an agent
    call) leaves `status` at `awaiting_gate` the whole time it runs."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=600)

    def build():
        return _stub(tmp_path, policy_obj=pol)

    async def body(app):
        await _seed_awaiting_gate(app, str(repo))
        placeholder = asyncio.get_event_loop().create_future()
        app.state.tasks["w1"] = placeholder
        try:
            attempted = await auto_escalate_delay.tick(app)
            assert attempted == []
        finally:
            placeholder.cancel()
            del app.state.tasks["w1"]

    _run(build, body)


def test_tick_skips_a_row_whose_effective_delay_is_zero(tmp_path, monkeypatch):
    """The default (0) means the inline call from run()/resume() already
    fired immediately, so there is nothing for this backstop to back-stop.
    Checking those rows anyway would hand a stuck item a fresh
    `auto_escalate_stuck` turn every interval up to the cap, where before
    this poller existed it got one per run()/resume() (code review
    finding)."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(work_item_id)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    pol = policy.Policy(loops={}, default=policy.Cap(3, 3600), auto_escalate_delay_s=0)

    def build():
        return _stub(tmp_path, policy_obj=pol)

    async def body(app):
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
        await app.state.db.write(lambda c: store.mark_needs_human(c, "w1", "a", "stuck"))
        assert await auto_escalate_delay.tick(app) == []
        await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
        assert calls == []

    _run(build, body)


def test_tick_skips_a_row_that_is_not_armed_for_auto_escalation(tmp_path, monkeypatch):
    """A row parked forever with `auto_escalate_stuck: false` can only return
    its status unchanged, so it must not spend a `max_concurrent` slot (or
    briefly hold the item's task slot, which 409s a human's retry) every
    tick (code review finding)."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)
    monkeypatch.setattr("kraft.executor.gates._seconds_since", lambda evts, pred: 10_000.0)
    pol = policy.Policy(
        loops={},
        default=policy.Cap(3, 3600),
        auto_escalate_delay_s=600,
        auto_escalate_stuck=False,
    )

    def build():
        return _stub(tmp_path, policy_obj=pol)

    async def body(app):
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
        await app.state.db.write(lambda c: store.mark_needs_human(c, "w1", "a", "stuck"))
        assert await auto_escalate_delay.tick(app) == []

    _run(build, body)
