import asyncio
import json
from types import SimpleNamespace

import pytest
from support.harness import make_repo, v1_chain, v1_item, v1_walk

from kraft import db, events, executor, store
from kraft.adapters import agent as agent_mod
from kraft.api.routes import gates as gates_route
from kraft.executor import gates as gates_module
from kraft.executor import resuming
from kraft.executor.context import LaunchContext
from kraft.paths import RunDirs
from kraft.templates.models import MaterializedChain


def test_apply_rejection_returns_the_reentry_index_then_stops_at_the_cap(tmp_path):
    """Converted to the typed chain (Task 4b): `apply_rejection` takes the
    ordered `ResolvedNode`s now, not a chain dict."""
    from kraft import policy as _policy

    chain = v1_chain(
        [
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            },
            {"id": "human_review_approval", "kind": "gate", "reject_to": "implementation"},
        ],
        repo="/r",
    )
    nodes = chain.chain.nodes
    policy = _policy.Policy(loops={}, default=_policy.Cap(attempts=2, wall_clock_s=3600))

    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            await v1_item(database, chain, repo="/r")
            results = []
            for note in ("not yet", "still not", "no"):
                results.append(
                    await executor.apply_rejection(
                        database,
                        policy,
                        work_item_id="w1",
                        nodes=nodes,
                        gate="human_review_approval",
                        note=note,
                    )
                )
            first, second, third = results
            assert (first, second) == (0, 0)
            # Third breaches attempts=2: no re-entry, and the item is parked.
            assert third is None
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = 'w1'").fetchone()
            )
            assert row["status"] == "needs_human"
        finally:
            await database.close()

    asyncio.run(scenario())


# `test_review_gates_reads_the_items_own_node_override_not_the_templates` and
# `test_review_gates_skips_before_its_delay_has_elapsed` lived here, both built
# on a legacy `gate_after` chain. They are replaced one-for-one, against the
# typed chain, by
# `test_a_node_override_permits_or_suppresses_the_declared_auto_review` and
# `test_auto_review_waits_out_its_effective_delay` at the end of this file.


async def _seed_stuck(database, wid, *, reason, chain=None, node_id="implementation"):
    # Both columns. `auto_escalate_stuck` is Task 7's and still reads node
    # fields off the legacy `chain_definition`; `resume_after_escalation` walks
    # the V1 chain to find the node to re-enter at, so the row needs one.
    v1 = v1_chain(
        [
            {
                "id": n["id"],
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            }
            for n in (chain or {"nodes": [{"id": node_id}]})["nodes"]
        ],
        repo="/r",
    )
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo="/r",
            chain_template="default",
            chain_definition=json.dumps(chain or {"nodes": [{"id": node_id, "tasks": []}]}),
            materialized_chain=v1.to_json(),
        )
    )
    await database.write(lambda c: store.enter_node(c, wid, node_id))
    await database.write(lambda c: store.mark_needs_human(c, wid, node_id, reason))


def test_auto_escalate_stuck_skips_a_non_needs_human_status(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            status = await gates_module.auto_escalate_stuck(
                "completed", database, None, work_item_id="w1", registry=None, launch=launch
            )
            return status
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "completed"


def test_auto_escalate_stuck_returns_status_unchanged_when_launch_is_none(tmp_path):
    """`startup.py`'s reattach path called this with `launch=None` when a
    resumed item's `repo_row` was falsy -- before Kraft-atdbw removed that
    call site, it crashed into `deps.guard`, overwriting the item's real
    stop reason with "executor crashed: ...". This guard is defense in
    depth against any future `launch=None` caller (Kraft-9046)."""
    from kraft.paths import RunDirs

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted")
            status = await gates_module.auto_escalate_stuck(
                "needs_human", database, rd, work_item_id=wid, registry=None, launch=None
            )
            assert status == "needs_human"
            evs = database.read(lambda c: events.read_after(c, 0, wid))
            assert not any(
                e["type"] == "work_item_needs_human"
                and "executor crashed" in (e["payload"].get("reason") or "")
                for e in evs
            )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_auto_escalate_stuck_skips_a_pending_gate(tmp_path):
    from kraft.paths import RunDirs

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            chain = {"nodes": [{"id": "spec", "tasks": [], "gate_after": "spec_approval"}]}
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id=None,
                    title="t",
                    repo="/r",
                    chain_template="default",
                    chain_definition=json.dumps(chain),
                )
            )
            await database.write(lambda c: store.enter_node(c, wid, "spec"))
            await database.write(lambda c: store.request_gate(c, wid, "spec", "spec_approval"))
            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1))
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            status = await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
            return status
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "needs_human"


def test_pending_gate_closes_on_a_node_skipped_event(tmp_path):
    """`kraft item skip` writes `node_skipped`, never `gate_approved`/
    `gate_rejected` -- without treating it as a boundary too, a gate
    bypassed by skip reads as pending forever (code review finding)."""

    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            wid = "w1"
            chain = {"nodes": [{"id": "spec", "tasks": [], "gate_after": "spec_approval"}]}
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id=None,
                    title="t",
                    repo="/r",
                    chain_template="default",
                    chain_definition=json.dumps(chain),
                )
            )
            await database.write(lambda c: store.enter_node(c, wid, "spec"))
            await database.write(lambda c: store.request_gate(c, wid, "spec", "spec_approval"))
            await database.write(lambda c: store.skip_node(c, wid, "spec", "spec_approval", None))
            return executor.pending_gate(database, wid)
        finally:
            await database.close()

    assert asyncio.run(scenario()) is None


def test_auto_escalate_stuck_skips_a_needs_context_reason(tmp_path):
    from kraft.paths import RunDirs

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="needs_context: which flag?")
            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1))
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)

            return await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
        finally:
            await database.close()

    status = asyncio.run(scenario())
    assert status == "needs_human"


def test_auto_escalate_stuck_skips_when_last_escalation_asked_a_question(tmp_path):
    """The *escalation session's own* exit status, not just a chain node's
    needs_human reason, can be `needs_context` -- the agent asked the human a
    direct question mid-turn. Dispatching another auto-escalation turn onto
    it just asks again (Kraft-b52cm)."""
    from kraft.paths import RunDirs

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted")
            # `escalate.dispatch` always appends `escalation_message` before
            # the session it names exists; the scoped scan
            # (`gates._current_run_escalation_session_id`) keys off that
            # event, not the session row alone.
            await database.write(
                lambda c: events.append(
                    c,
                    wid,
                    "escalation_message",
                    {"session_id": "e1", "message": "hi", "auto": True},
                )
            )
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="e1",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="escalation",
                    log_path=str(rd.logs / "e1.log"),
                    result_path=str(rd.logs / "e1.json"),
                )
            )
            await database.write(lambda c: store.session_exited(c, "e1", "needs_context"))
            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1))
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            status = await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
            evs = database.read(lambda c: events.read_after(c, 0, wid))
            return status, evs
        finally:
            await database.close()

    status, evs = asyncio.run(scenario())
    assert status == "needs_human"
    skips = [e for e in evs if e["type"] == "work_item_auto_escalate_skipped"]
    assert skips and skips[-1]["payload"]["reason"] == "needs_context"


def test_auto_escalate_stuck_ignores_a_needs_context_escalation_from_a_prior_run(
    tmp_path, monkeypatch
):
    """A `needs_context` escalation session belongs to a *previous* run of
    needs_human stuckness (a human retried instead of answering it, and the
    item stopped again for something else) must not suppress this run's
    auto-escalation forever -- the exact regression the unscoped
    `last_escalation_status` read caused (Kraft-b52cm code-review finding)."""
    from kraft.paths import RunDirs

    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(1)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted")
            await database.write(
                lambda c: events.append(
                    c,
                    wid,
                    "escalation_message",
                    {"session_id": "e1", "message": "hi", "auto": True},
                )
            )
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="e1",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="escalation",
                    log_path=str(rd.logs / "e1.log"),
                    result_path=str(rd.logs / "e1.json"),
                )
            )
            await database.write(lambda c: store.session_exited(c, "e1", "needs_context"))
            # A human retried instead of answering -- a `_RUN_BOUNDARY` event --
            # and the item stopped again for something unrelated. This is a
            # new run; "e1" belongs to the one before it.
            await database.write(lambda c: events.append(c, wid, "work_item_retried", {}))
            await database.write(
                lambda c: store.mark_needs_human(c, wid, "implementation", "again")
            )

            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1))
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            status = await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
            evs = database.read(lambda c: events.read_after(c, 0, wid))
            return status, evs
        finally:
            await database.close()

    status, evs = asyncio.run(scenario())
    assert status == "needs_human"
    assert calls == [1]
    skips = [e for e in evs if e["type"] == "work_item_auto_escalate_skipped"]
    assert not skips


def test_auto_escalate_stuck_does_not_skip_when_last_escalation_finished_clean(
    tmp_path, monkeypatch
):
    """The mirror case: the last escalation session finished `done`, not
    `needs_context` -- the new guard must not fire, and (with `dispatch`
    faked out) the function proceeds to actually dispatch."""
    from kraft.paths import RunDirs

    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(1)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted")
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="e1",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="escalation",
                    log_path=str(rd.logs / "e1.log"),
                    result_path=str(rd.logs / "e1.json"),
                )
            )
            await database.write(lambda c: store.session_exited(c, "e1", "done"))
            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1))
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            status = await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
            evs = database.read(lambda c: events.read_after(c, 0, wid))
            return status, evs
        finally:
            await database.close()

    status, evs = asyncio.run(scenario())
    assert status == "needs_human"
    assert len(calls) == 1  # dispatch actually fired -- the guard did not skip
    skips = [
        e
        for e in evs
        if e["type"] == "work_item_auto_escalate_skipped"
        and e["payload"]["reason"] == "needs_context"
    ]
    assert not skips


def test_auto_escalate_stuck_skips_when_disabled_by_policy(tmp_path):
    from kraft.paths import RunDirs

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted")
            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_escalate_stuck=False)
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            return await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "needs_human"


def test_auto_escalate_stuck_skips_before_its_delay_has_elapsed(tmp_path):
    from kraft.paths import RunDirs

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted")
            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_escalate_delay_s=600)
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            return await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "needs_human"


def test_auto_escalate_stuck_fires_once_its_delay_has_elapsed(tmp_path, monkeypatch):
    from kraft.paths import RunDirs

    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(1)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted")
            from kraft import policy as _policy

            # A delay of 0 must behave exactly like no delay at all -- the
            # zero-regression guarantee.
            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_escalate_delay_s=0)
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            return await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
        finally:
            await database.close()

    status = asyncio.run(scenario())
    assert status == "needs_human"
    assert len(calls) == 1


def test_auto_escalate_stuck_dispatches_when_eligible(tmp_path, monkeypatch):
    from kraft.paths import RunDirs

    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append({"message": message, "auto": auto, "evts": evts})
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted")
            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1))
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            return await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
        finally:
            await database.close()

    status = asyncio.run(scenario())
    assert status == "needs_human"
    assert len(calls) == 1
    assert calls[0]["auto"] is True
    assert "Auto-escalated" in calls[0]["message"]
    # The single timeline `auto_escalate_stuck` read for its own checks is the
    # same list handed to `dispatch` -- not None, not a second fresh read.
    assert calls[0]["evts"] is not None


def test_auto_escalate_stuck_caps_out_and_emits_an_event(tmp_path, monkeypatch):
    from kraft.paths import RunDirs

    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, **kw):
        # Mimics dispatch()'s *real* shape, since dispatch() itself is mocked
        # out here: an `escalation_message` event, then a session born and
        # exited around it -- `store.create_session` (called unconditionally
        # by the real `_agent.run_agent_task` -> `adapters/subprocess.py` for
        # every dispatched session, escalation included) and
        # `store.session_exited`, exactly the two extra event types that
        # broke the old "break on anything but needs_human/escalation_message"
        # scan after a single auto-dispatch. A fake that only appended
        # `escalation_message` (the original version of this test) passed
        # against that bug by construction -- its timeline never grew the
        # shape the scan actually has to survive.
        sid = f"s{len(calls)}"
        calls.append(1)
        await database.write(
            lambda c: events.append(
                c,
                work_item_id,
                "escalation_message",
                {"session_id": sid, "message": "m", "auto": True},
            )
        )
        await database.write(
            lambda c: store.create_session(
                c,
                id=sid,
                work_item_id=work_item_id,
                node_id="implementation",
                hook_point="escalation",
                log_path=str(run_dirs.logs / f"{sid}.log"),
                result_path=str(run_dirs.logs / f"{sid}.json"),
            )
        )
        await database.write(lambda c: store.session_exited(c, sid, "done"))
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted")
            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_escalate_stuck_cap=2)
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            for _ in range(2):
                await gates_module.auto_escalate_stuck(
                    "needs_human",
                    database,
                    rd,
                    work_item_id=wid,
                    registry=None,
                    policy=pol,
                    launch=launch,
                )
            # A third call is over the cap: no further dispatch, a capped event instead.
            await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
            evs = database.read(lambda c: events.read_after(c, 0, wid))
            return [e["type"] for e in evs]
        finally:
            await database.close()

    types = asyncio.run(scenario())
    # The session-lifecycle events are on the timeline (proving the fake
    # reproduces the real shape) and did not stop the scan from counting
    # past them.
    assert types.count("worker_session_created") == 2
    assert types.count("worker_session_exited") == 2
    assert len([t for t in types if t == "escalation_message"]) == 2
    assert types.count("work_item_auto_escalate_capped") == 1


def test_auto_escalate_stuck_skips_while_an_escalation_is_already_running(tmp_path):
    from kraft.paths import RunDirs

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted")
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="escalation",
                    log_path=str(rd.logs / "s1.log"),
                    result_path=str(rd.logs / "s1.json"),
                )
            )
            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1))
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            return await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "needs_human"


def test_auto_escalate_stuck_consumes_a_self_retry_after_the_session_exits(tmp_path, monkeypatch):
    """The escalation agent calling `kraft item retry` on itself (lifecycle.py's
    `work_item_self_retry_requested` deferral) must not run the rebase/spawn
    until `escalate.dispatch` -- and so the session it was mid-call from --
    has actually returned. `fake_dispatch` mimics the agent's self-retry by
    appending the same event `retry_work_item` would, from inside the
    dispatch call it is standing in for; `auto_escalate_stuck` must only act
    on it once `dispatch` (this call) returns."""
    from kraft.paths import RunDirs

    chain = {"nodes": [{"id": "implementation", "tasks": [], "fix_loop": "implementation"}]}

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        await database.write(
            lambda c: events.append(
                c,
                work_item_id,
                "work_item_self_retry_requested",
                {
                    "session_id": "s1",
                    "node_id": "implementation",
                    "key": "implementation",
                    "gate_key": None,
                    "steer": "fixed it",
                },
            )
        )
        return "done"

    async def fake_refresh(worktree, repo, branch):
        return None

    walk_calls = []

    async def fake_walk_run(database, run_dirs, **kw):
        walk_calls.append(kw)
        return "completed"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    monkeypatch.setattr(gates_module._builtins, "refresh_worktree_base", fake_refresh)
    monkeypatch.setattr("kraft.executor.walk.run", fake_walk_run)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget exhausted", chain=chain)
            from kraft import policy as _policy

            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1))
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            status = await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
            evs = database.read(lambda c: events.read_after(c, 0, wid))
            return status, [e["type"] for e in evs], evs
        finally:
            await database.close()

    status, types, evs = asyncio.run(scenario())
    assert status == "completed"
    assert len(walk_calls) == 1
    assert walk_calls[0]["start_index"] == 0
    assert walk_calls[0]["steer"] == "fixed it"
    retried = next(e for e in evs if e["type"] == "work_item_retried")
    assert retried["payload"]["escalated"] is True
    assert types.index("work_item_self_retry_requested") < types.index("work_item_retried")


def test_auto_dispatch_count_does_not_reset_on_the_escalations_own_retry():
    """A `work_item_retried` tagged `{"escalated": true}` -- the auto-escalated
    agent's own self-retry -- must not reset the cap counter, or a stop a
    retry cannot clear (a budget breach) escalates forever (finding: gates.py
    `_RUN_BOUNDARY`)."""
    evts = [
        {"type": "work_item_needs_human", "payload": {}},
        {"type": "escalation_message", "payload": {"auto": True}},
        {
            "type": "work_item_retried",
            "payload": {"node_id": "implementation", "loop": None, "escalated": True},
        },
        {"type": "work_item_needs_human", "payload": {}},
        {"type": "escalation_message", "payload": {"auto": True}},
    ]
    assert gates_module._auto_dispatch_count(evts) == 2

    # A human's own retry (no `escalated` tag) still resets it, unchanged.
    evts[2]["payload"].pop("escalated")
    assert gates_module._auto_dispatch_count(evts) == 1


def test_auto_dispatch_count_does_not_reset_on_the_self_retrys_node_started():
    """`resume_after_escalation` -> `retry_after_cap` -> `walk.run` writes an
    unconditional `node_started` for the stuck node before any verdict runs.
    That must not reset the cap counter either, or the `escalated: true` tag
    on the retry itself is defeated by the very `node_started` the same retry
    emits, and a stop a retry cannot clear escalates forever (finding:
    gates.py `_RUN_BOUNDARY`)."""
    evts = [
        {"type": "node_started", "payload": {"node_id": "implementation"}},
        {"type": "work_item_needs_human", "payload": {"node_id": "implementation"}},
        {"type": "escalation_message", "payload": {"auto": True}},
        {
            "type": "work_item_retried",
            "payload": {"node_id": "implementation", "loop": None, "escalated": True},
        },
        # The self-retry's own `walk.run` re-entering the stuck node.
        {"type": "node_started", "payload": {"node_id": "implementation"}},
        {"type": "work_item_needs_human", "payload": {"node_id": "implementation"}},
        {"type": "escalation_message", "payload": {"auto": True}},
    ]
    assert gates_module._auto_dispatch_count(evts) == 2

    # Nor does a `node_started` for a *different* node: chain movement is not
    # a boundary at all. A self-retry that moves the chain and re-stops
    # elsewhere is the same run of unattended stuckness -- counting it as a
    # new run reads 0 every cycle and the cap never engages.
    evts[4]["payload"]["node_id"] = "some_other_node"
    assert gates_module._auto_dispatch_count(evts) == 2

    # Same for a gate the chain re-reached, and for a gate an *agent* decided.
    evts[4] = {"type": "gate_requested", "payload": {"gate": "plan_approval"}}
    assert gates_module._auto_dispatch_count(evts) == 2
    evts[4] = {"type": "gate_approved", "payload": {"gate": "plan_approval", "by": "agent"}}
    assert gates_module._auto_dispatch_count(evts) == 2

    # A human deciding that gate does reset it.
    evts[4] = {"type": "gate_approved", "payload": {"gate": "plan_approval", "by": "human"}}
    assert gates_module._auto_dispatch_count(evts) == 1


def test_auto_escalate_stuck_skips_a_budget_breached_stop(tmp_path, monkeypatch):
    """An item that stopped *because* its spend cap was breached must not buy
    another `auto_escalate_stuck_cap` agent turns on top of it (finding:
    gates.py missing the `stops.budget_breach` check `review_gates` makes)."""
    from kraft.paths import RunDirs

    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append(message)
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="budget cap reached: $12.00 spent")
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path=str(rd.logs / "s1.log"),
                    result_path=str(rd.logs / "s1.json"),
                )
            )
            await database.write(
                lambda c: c.execute("UPDATE worker_sessions SET cost_usd = 12.0 WHERE id = 's1'")
            )
            from kraft import policy as _policy

            pol = _policy.Policy(
                loops={},
                default=_policy.Cap(1, 1),
                budget=_policy.Budget(work_item_usd=10.0, daily_usd=None),
            )
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            status = await gates_module.auto_escalate_stuck(
                "needs_human",
                database,
                rd,
                work_item_id=wid,
                registry=None,
                policy=pol,
                launch=launch,
            )
            evs = database.read(lambda c: events.read_after(c, 0, wid))
            return status, [e["type"] for e in evs]
        finally:
            await database.close()

    status, types = asyncio.run(scenario())
    assert status == "needs_human"
    assert calls == []
    assert types.count("work_item_auto_escalate_skipped") == 1


def test_resume_after_escalation_drops_a_self_retry_on_a_moved_item(tmp_path):
    """A human who abandons (or pauses) the item while the escalation turn is
    running must not have it resurrected: `retry_after_cap`/`mark_needs_human`
    both UPDATE unconditionally, and the worktree may already be gone
    (finding: `resume_after_escalation` never re-checked status)."""
    from kraft.paths import RunDirs

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_stuck(database, wid, reason="git rebase failed")
            cursor = database.read(lambda c: events.read_after(c, 0, wid))[-1]["seq"]
            await database.write(
                lambda c: events.append(
                    c,
                    wid,
                    "work_item_self_retry_requested",
                    {"node_id": "implementation", "key": None, "gate_key": None, "steer": None},
                )
            )
            # The human moved it while the escalation turn was running.
            await database.write(
                lambda c: c.execute(
                    "UPDATE work_items SET status = 'abandoned' WHERE id = ?", (wid,)
                )
            )
            status = await gates_module.resume_after_escalation(
                database, rd, work_item_id=wid, cursor=cursor, registry=None
            )
            evs = database.read(lambda c: events.read_after(c, 0, wid))
            return status, [e["type"] for e in evs]
        finally:
            await database.close()

    status, types = asyncio.run(scenario())
    assert status == "abandoned"
    assert types.count("work_item_self_retry_dropped") == 1
    # Nothing re-entered the chain.
    assert "work_item_retried" not in types


def _evt(t, **payload):
    return {"type": t, "payload": payload, "created_at": "2026-09-12T00:00:00+00:00"}


def test_gate_review_attempts_counts_the_started_event():
    """`gate_review.review` writes `gate_auto_review_started` before it
    launches anything, so a review that crashed mid-flight -- no verdict, no
    skip event -- still spends an attempt and suppresses the next tick at the
    default bound of one."""
    evts = [_evt("gate_requested", gate="g"), _evt("gate_auto_review_started", gate="g")]
    assert gates_module._gate_review_attempts(evts, "g") == 1


def test_gate_review_attempts_resets_at_a_run_boundary():
    """Otherwise a crashed review demotes the gate to human-only for good:
    a human's resume re-enters review_gates, hits the spent bound, and returns
    unchanged with nothing logged (code review finding)."""
    evts = [
        _evt("gate_requested", gate="g"),
        _evt("gate_auto_review_started", gate="g"),
        _evt("work_item_resumed"),
    ]
    assert gates_module._gate_review_attempts(evts, "g") == 0


def test_gate_review_attempts_ignores_a_previous_requests_attempt():
    evts = [
        _evt("gate_requested", gate="g"),
        _evt("gate_auto_review_skipped", gate="g", reason="undecided"),
        _evt("gate_rejected", gate="g"),
        _evt("gate_requested", gate="g"),
    ]
    assert gates_module._gate_review_attempts(evts, "g") == 0


def test_resume_after_escalation_threads_the_seeded_flag_onto_retry_after_cap(
    tmp_path, monkeypatch
):
    """The deferred self-retry (lifecycle.py's `work_item_self_retry_requested`)
    marks whether its steer was Kraft's own seeded recap of the last
    measurement or a human's; consuming it must not lose that mark."""
    from kraft.paths import RunDirs

    async def fake_refresh(worktree, repo, branch):
        return None

    async def fake_walk_run(database, run_dirs, **kw):
        return "completed"

    monkeypatch.setattr(gates_module._builtins, "refresh_worktree_base", fake_refresh)
    monkeypatch.setattr("kraft.executor.walk.run", fake_walk_run)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            chain = {"nodes": [{"id": "implementation", "tasks": [], "fix_loop": "implementation"}]}
            await _seed_stuck(database, wid, reason="stuck", chain=chain)
            cursor = database.read(lambda c: events.read_after(c, 0, wid))[-1]["seq"]
            await database.write(
                lambda c: events.append(
                    c,
                    wid,
                    "work_item_self_retry_requested",
                    {
                        "node_id": "implementation",
                        "key": "implementation",
                        "gate_key": None,
                        "steer": "- [important] a.py:1 — missing null check (reviewer)",
                        "seeded": True,
                    },
                )
            )
            status = await gates_module.resume_after_escalation(
                database, rd, work_item_id=wid, cursor=cursor, registry=None
            )
            evs = database.read(lambda c: events.read_after(c, 0, wid))
            retried = next(e for e in evs if e["type"] == "work_item_retried")
            return status, retried["payload"]["seeded"]
        finally:
            await database.close()

    status, seeded = asyncio.run(scenario())
    assert status == "completed"
    assert seeded is True


def test_resume_after_escalation_defaults_seeded_to_false_for_an_older_event_shape(
    tmp_path, monkeypatch
):
    """A `work_item_self_retry_requested` written before this field existed has
    no `seeded` key at all; reading it back must default False, not KeyError."""
    from kraft.paths import RunDirs

    async def fake_refresh(worktree, repo, branch):
        return None

    async def fake_walk_run(database, run_dirs, **kw):
        return "completed"

    monkeypatch.setattr(gates_module._builtins, "refresh_worktree_base", fake_refresh)
    monkeypatch.setattr("kraft.executor.walk.run", fake_walk_run)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            chain = {"nodes": [{"id": "implementation", "tasks": [], "fix_loop": "implementation"}]}
            await _seed_stuck(database, wid, reason="stuck", chain=chain)
            cursor = database.read(lambda c: events.read_after(c, 0, wid))[-1]["seq"]
            await database.write(
                lambda c: events.append(
                    c,
                    wid,
                    "work_item_self_retry_requested",
                    {
                        "node_id": "implementation",
                        "key": "implementation",
                        "gate_key": None,
                        "steer": "go",
                    },
                )
            )
            status = await gates_module.resume_after_escalation(
                database, rd, work_item_id=wid, cursor=cursor, registry=None
            )
            evs = database.read(lambda c: events.read_after(c, 0, wid))
            retried = next(e for e in evs if e["type"] == "work_item_retried")
            return status, retried["payload"]["seeded"]
        finally:
            await database.close()

    status, seeded = asyncio.run(scenario())
    assert status == "completed"
    assert seeded is False


# ── Template Schema V1: gates own their own behaviour (Task 4b) ──
#
# Everything below drives the typed `MaterializedChain`. The legacy-chain tests
# above are Task 5b's to convert; do not read them as the current shape.


def _agent_task(id="reviewer", **kw):
    return {"id": id, "kind": "agent", "harness": "fake", "prompt": "review it", **kw}


def _v1(nodes, repo, **kw):
    return v1_chain(nodes, repo=repo, **kw)


def _spec_gate_chain(repo, *, gate_extra=None, after=True):
    """spec (exec) -> spec_approval (gate) -> implementation (exec)."""
    nodes = [
        {
            "id": "spec",
            "kind": "exec",
            "tasks": [{"id": "write", "kind": "subprocess", "command": "true"}],
        },
        {"id": "spec_approval", "kind": "gate", "artifact": "spec", **(gate_extra or {})},
    ]
    if after:
        nodes.append(
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [{"id": "build", "kind": "subprocess", "command": "true"}],
            }
        )
    return _v1(nodes, repo)


async def _open_db(tmp_path, chain, repo, **kwargs):
    database = await db.Database.open(tmp_path / "k.db")
    await v1_item(database, chain, repo=repo, **kwargs)
    return database


def _row(database, wid="w1"):
    return database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )


def _fake_state(database, run_dirs):
    """The slice of `app.state` the gate routes' `apply_approval` reads."""

    class _Indexer:
        async def ingest_gate_artifact(self, **kw):
            self.seen = kw

    return SimpleNamespace(db=database, run_dirs=run_dirs, indexer=_Indexer())


# -- gate-node-opens-and-halts-execution / gate-approval-advances-to-next-node --


def test_gate_node_halts_until_approved(tmp_path):
    """The walk stops *at* the gate: the node after it does not start, and the
    gate opens under its own node id with no name table anywhere."""
    repo = make_repo(tmp_path)
    chain = _spec_gate_chain(repo)

    status, evts, sessions, row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "awaiting_gate"
    assert [s["hook_point"] for s in sessions] == ["spec.main.write"]
    assert row["current_node_id"] == "spec_approval"
    requested = next(e for e in evts if e["type"] == "gate_requested")
    assert requested["payload"] == {"gate": "spec_approval", "node_id": "spec_approval"}
    # And nothing has completed it yet -- that is what approval is for.
    assert not [
        e
        for e in evts
        if e["type"] == "node_completed" and e["payload"]["node_id"] == "spec_approval"
    ]


def test_gate_approval_advances_to_the_node_after_the_gate(tmp_path):
    """`apply_approval` hands back the ordered nodes, the caller advances one
    past the gate's own index, and the gate node is *completed* rather than left
    rendering as a node still running (4a's Concern 4)."""
    repo = make_repo(tmp_path)
    chain = _spec_gate_chain(repo)
    rd = RunDirs(tmp_path / "run").ensure()

    async def scenario():
        database = await _open_db(tmp_path, chain, repo)
        try:
            await executor.run_once(
                database,
                rd,
                work_item_id="w1",
                registry=None,
                launch=LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
            )
            row = _row(database)
            nodes, reason = await gates_route.apply_approval(
                _fake_state(database, rd), row, "spec_approval"
            )
            assert reason is None
            start = executor.gate_node_index(nodes, "spec_approval") + 1
            assert nodes[start].id == "implementation"
            await database.write(lambda c: store.approve_gate(c, "w1", "spec_approval"))
            status = await executor.run_once(
                database,
                rd,
                work_item_id="w1",
                registry=None,
                start_index=start,
                launch=LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
            )
            evts = database.read(lambda c: events.read_after(c, 0, "w1"))
            sessions = database.read(
                lambda c: c.execute(
                    "SELECT hook_point FROM worker_sessions WHERE work_item_id = 'w1'"
                ).fetchall()
            )
            return (
                status,
                [(e["type"], e["payload"].get("node_id")) for e in evts],
                [s["hook_point"] for s in sessions],
            )
        finally:
            await database.close()

    status, pairs, hooks = asyncio.run(scenario())
    assert status == "completed"
    assert hooks == ["spec.main.write", "implementation.main.build"]
    # The gate's own node pair closes on approval, and the gate is never
    # re-requested on the way past.
    assert ("node_completed", "spec_approval") in pairs
    assert [t for t, _ in pairs].count("gate_requested") == 1


def test_an_ordinary_gate_has_ordinary_pause_and_approval_behaviour(tmp_path):
    """`chain-finalized-remains-a-dedicated-marker`'s second half: a gate
    without the marker approves with no document at all, where the final-review
    gate refuses (see the marker test below)."""
    repo = make_repo(tmp_path)
    chain = _v1(
        [
            {
                "id": "build",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            },
            {"id": "sign_off", "kind": "gate", "artifact": "review_brief"},
        ],
        repo,
    )
    rd = RunDirs(tmp_path / "run").ensure()

    async def scenario():
        database = await _open_db(tmp_path, chain, repo)
        try:
            await executor.run_once(
                database,
                rd,
                work_item_id="w1",
                registry=None,
                launch=LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
            )
            return await gates_route.apply_approval(
                _fake_state(database, rd), _row(database), "sign_off"
            )
        finally:
            await database.close()

    nodes, reason = asyncio.run(scenario())
    assert reason is None
    assert nodes is not None


def test_the_chain_finalized_marker_not_the_gate_name_selects_chain_review(tmp_path):
    """Two gates, and the names are deliberately the wrong way round: the one
    *called* `chain_finalized` carries no marker, and the marked one is called
    something else. Only the marked gate takes the final-review path, which
    refuses an approval whose review document was never written."""
    repo = make_repo(tmp_path)
    chain = _v1(
        [
            {
                "id": "build",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            },
            {"id": "chain_finalized", "kind": "gate", "artifact": "review_brief"},
            {
                "id": "summarize",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            },
            {"id": "all_done", "kind": "gate", "chain_finalized": True, "artifact": "review_brief"},
        ],
        repo,
    )
    rd = RunDirs(tmp_path / "run").ensure()

    async def scenario():
        database = await _open_db(tmp_path, chain, repo)
        try:
            await executor.run_once(
                database,
                rd,
                work_item_id="w1",
                registry=None,
                launch=LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
            )
            st = _fake_state(database, rd)
            row = _row(database)
            by_name = await gates_route.apply_approval(st, row, "chain_finalized")
            by_flag = await gates_route.apply_approval(st, row, "all_done")
            return by_name, by_flag
        finally:
            await database.close()

    (named_nodes, named_reason), (flag_nodes, flag_reason) = asyncio.run(scenario())
    # Named `chain_finalized`, unmarked: an ordinary gate, approvable.
    assert named_reason is None and named_nodes is not None
    # Marked, differently named: the final review's own document is the subject,
    # and nothing wrote one.
    assert flag_nodes is None
    assert "final review document is missing" in flag_reason


# -- gate-rejection-follows-gate-reject-target --


def test_gate_rejection_follows_its_own_reject_to(tmp_path):
    """`reject_to` wins over the "nearest preceding execution node" fallback, so
    this chain puts a second execution node between `spec` and the gate --
    otherwise the two answers coincide and the test pins nothing."""
    from kraft import policy as _policy

    repo = make_repo(tmp_path)
    chain = _v1(
        [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [{"id": "write", "kind": "subprocess", "command": "true"}],
            },
            {
                "id": "plan",
                "kind": "exec",
                "tasks": [{"id": "write", "kind": "subprocess", "command": "true"}],
            },
            {"id": "spec_approval", "kind": "gate", "artifact": "spec", "reject_to": "spec"},
        ],
        repo,
    )
    nodes = chain.chain.nodes
    policy = _policy.Policy(loops={}, default=_policy.Cap(attempts=5, wall_clock_s=3600))

    async def scenario():
        database = await _open_db(tmp_path, chain, repo)
        try:
            target = await executor.apply_rejection(
                database,
                policy,
                work_item_id="w1",
                nodes=nodes,
                gate="spec_approval",
                note="redo the spec",
            )
            rejected = next(
                e
                for e in database.read(lambda c: events.read_after(c, 0, "w1"))
                if e["type"] == "gate_rejected"
            )
            return target, rejected["payload"]["node"]
        finally:
            await database.close()

    target, recorded = asyncio.run(scenario())
    assert nodes[target].id == "spec" and recorded == "spec"


def test_a_rejection_with_no_reject_to_re_enters_the_execution_node_before_the_gate(tmp_path):
    """Ruling 54. A V1 gate has no execution shape, so the old fallback --
    "re-enter at the gate node itself" -- dispatches nothing and immediately
    re-requests the same gate, a ping-pong bounded only by the reject cap. The
    node that produced what the gate is about is what gets re-measured."""
    repo = make_repo(tmp_path)
    chain = _spec_gate_chain(repo)  # no reject_to
    nodes = chain.chain.nodes

    assert nodes[executor.reject_target(nodes, 1, None)].id == "spec"


def test_a_gate_with_nothing_before_it_falls_back_to_itself(tmp_path):
    """The one case where re-entering at the gate is still the answer: there is
    no earlier execution node to re-measure, so the gate re-opens with the note."""
    repo = make_repo(tmp_path)
    chain = _v1([{"id": "sign_off", "kind": "gate"}], repo)

    assert executor.reject_target(chain.chain.nodes, 0, None) == 0


def test_a_rejection_cannot_be_aimed_forward_past_the_gate(tmp_path):
    """`reject_to` is validated by `Chain` itself, but a request body's `node`
    is validated by nothing -- aimed forward it would skip every node between."""
    repo = make_repo(tmp_path)
    chain = _spec_gate_chain(repo)

    with pytest.raises(ValueError, match="not a node of this chain before 'spec_approval'"):
        executor.reject_target(chain.chain.nodes, 1, "implementation")


# -- gate-owns-gate-behaviour / gate-control-does-not-generate-review-work --


def test_a_gate_shows_the_artifact_its_own_field_names(tmp_path):
    """The gate's own `artifact:` names the kind. Nothing scans the preceding
    node's tasks, so the preceding node here declares a *different* kind and the
    gate still shows its own."""
    repo = make_repo(tmp_path)
    chain = _v1(
        [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "write",
                        "kind": "agent",
                        "harness": "fake",
                        "prompt": "p",
                        "produces": "plan",
                    }
                ],
            },
            {"id": "spec_approval", "kind": "gate", "artifact": "spec"},
        ],
        repo,
    )
    rd = RunDirs(tmp_path / "run").ensure()
    rel = agent_mod.artifact_path("spec", "w1")
    document = rd.worktrees / "w1" / rel
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text("the spec\n")

    async def scenario():
        database = await _open_db(tmp_path, chain, repo)
        try:
            return executor.gate_artifact(rd, _row(database), "spec_approval")
        finally:
            await database.close()

    assert asyncio.run(scenario()) == rel


def test_a_gate_generates_no_artifact_of_its_own(tmp_path):
    """A gate declaring no `artifact` has no document, and that is an answerable
    gate rather than an error -- the gate is a decision control point, it does
    not produce review work (`gate-control-does-not-generate-review-work`).
    Neither does a declared kind whose file the worker never wrote."""
    repo = make_repo(tmp_path)
    chain = _v1(
        [
            {"id": "no_document", "kind": "gate"},
            {"id": "unwritten", "kind": "gate", "artifact": "spec"},
        ],
        repo,
    )
    rd = RunDirs(tmp_path / "run").ensure()

    async def scenario():
        database = await _open_db(tmp_path, chain, repo)
        try:
            row = _row(database)
            return (
                executor.gate_artifact(rd, row, "no_document"),
                executor.gate_artifact(rd, row, "unwritten"),
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == (None, None)


def test_a_gate_with_arbitrary_id_works_without_a_name_table(tmp_path):
    """`GATE_NAMES`' closed vocabulary is gone: a gate id a custom chain invents
    opens, carries its artifact, and rejects to its own target."""
    repo = make_repo(tmp_path)
    chain = _v1(
        [
            {
                "id": "shape_it",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            },
            {
                "id": "does_marketing_like_it",
                "kind": "gate",
                "artifact": "spec",
                "reject_to": "shape_it",
            },
        ],
        repo,
    )
    rd = RunDirs(tmp_path / "run").ensure()
    rel = agent_mod.artifact_path("spec", "w1")
    (rd.worktrees / "w1" / rel).parent.mkdir(parents=True, exist_ok=True)
    (rd.worktrees / "w1" / rel).write_text("x\n")

    status, evts, _sessions, _row_ = asyncio.run(v1_walk(tmp_path, chain, repo=repo, run_dirs=rd))

    assert status == "awaiting_gate"
    assert next(e for e in evts if e["type"] == "gate_requested")["payload"]["gate"] == (
        "does_marketing_like_it"
    )
    nodes = chain.chain.nodes
    assert nodes[executor.reject_target(nodes, 1, None)].id == "shape_it"


# -- gate-auto-review-is-explicit-and-bounded --


def _reviewed_chain(repo, *, declare=True):
    return _v1(
        [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [{"id": "write", "kind": "subprocess", "command": "true"}],
            },
            {
                "id": "spec_approval",
                "kind": "gate",
                "artifact": "spec",
                "reject_to": "spec",
                **({"auto_review": _agent_task()} if declare else {}),
            },
        ],
        repo,
    )


async def _seed_pending_gate(database, gate="spec_approval"):
    await database.write(
        lambda c: events.append(c, "w1", "gate_requested", {"gate": gate, "node_id": gate})
    )


def _no_review(monkeypatch, why):
    def _boom(*a, **kw):
        raise AssertionError(why)

    monkeypatch.setattr(gates_module.gate_review, "review", _boom)


def test_auto_review_runs_only_with_work_item_opt_in(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    chain = _reviewed_chain(repo)
    _no_review(monkeypatch, "an item that did not opt in must not be auto-reviewed")

    async def scenario(auto_gate):
        database = await db.Database.open(tmp_path / f"k-{auto_gate}.db")
        try:
            await v1_item(database, chain, repo=repo, auto_gate=auto_gate)
            await _seed_pending_gate(database)
            return await gates_module.review_gates(
                "awaiting_gate", database, tmp_path, work_item_id="w1", registry=None
            )
        finally:
            await database.close()

    assert asyncio.run(scenario(False)) == "awaiting_gate"
    # And with opt-in the arming check passes, so the patched reviewer is reached.
    with pytest.raises(AssertionError, match="must not be auto-reviewed"):
        asyncio.run(scenario(True))


def test_auto_review_waits_out_its_effective_delay(tmp_path, monkeypatch):
    from kraft import policy as _policy

    repo = make_repo(tmp_path)
    chain = _reviewed_chain(repo)
    _no_review(monkeypatch, "review must not run before the effective delay elapses")
    pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_escalate_delay_s=600)

    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            await v1_item(database, chain, repo=repo, auto_gate=True)
            await _seed_pending_gate(database)
            return await gates_module.review_gates(
                "awaiting_gate", database, tmp_path, work_item_id="w1", registry=None, policy=pol
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "awaiting_gate"


def test_auto_review_stops_at_its_effective_attempt_limit(tmp_path, monkeypatch):
    """`policy.auto_review_attempts` bounds attempts per `gate_requested`; the
    limit used to be a hardcoded one. Two attempts are already spent here, so a
    limit of 2 refuses and a limit of 3 lets the third through."""
    from kraft import policy as _policy

    repo = make_repo(tmp_path)
    chain = _reviewed_chain(repo)
    _no_review(monkeypatch, "third attempt")

    async def scenario(limit):
        database = await db.Database.open(tmp_path / f"k-{limit}.db")
        try:
            await v1_item(database, chain, repo=repo, auto_gate=True)
            await _seed_pending_gate(database)
            for _ in range(2):
                await database.write(
                    lambda c: events.append(
                        c, "w1", "gate_auto_review_started", {"gate": "spec_approval"}
                    )
                )
            pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_review_attempts=limit)
            return await gates_module.review_gates(
                "awaiting_gate", database, tmp_path, work_item_id="w1", registry=None, policy=pol
            )
        finally:
            await database.close()

    assert asyncio.run(scenario(2)) == "awaiting_gate"
    with pytest.raises(AssertionError, match="third attempt"):
        asyncio.run(scenario(3))


def test_an_undecided_review_counts_as_one_attempt_not_two(tmp_path):
    """A review that ran and came back `undecided` writes both a `_started` and
    a `_skipped {reason: undecided}`. Counting both would halve every bound
    above 1; a `{reason: budget}` skip never launched and counts for itself."""
    started = _evt("gate_auto_review_started", gate="g")
    undecided = _evt("gate_auto_review_skipped", gate="g", reason="undecided")
    budget = _evt("gate_auto_review_skipped", gate="g", reason="budget")
    request = _evt("gate_requested", gate="g")

    assert gates_module._gate_review_attempts([request, started, undecided], "g") == 1
    assert gates_module._gate_review_attempts([request, budget], "g") == 1
    assert (
        gates_module._gate_review_attempts([request, started, undecided, started, undecided], "g")
        == 2
    )


def test_auto_review_reports_a_verdict_and_cannot_clear_its_own_gate(tmp_path, monkeypatch):
    """The reviewer is dispatched as a *worker* -- `KRAFT_WORK_ITEM_ID` is set,
    so `client.context._forbid_self_action` refuses to let it approve or reject
    its own item. It reports a verdict and `review_gates` applies it."""
    import kraft.adapters.agent as agent_adapter

    repo = make_repo(tmp_path)
    chain = _reviewed_chain(repo)
    rd = RunDirs(tmp_path / "run").ensure()
    seen = {}

    async def _capture(_db, _rd, **kw):
        seen.update(kw)
        (rd.results / f"{kw['session_id']}.json").write_text(
            json.dumps({"status": "done", "verdict": "approve", "concerns": "looks right"})
        )
        return "done"

    monkeypatch.setattr(agent_adapter, "run_agent_task", _capture)

    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            await v1_item(database, chain, repo=repo, auto_gate=True)
            await _seed_pending_gate(database)
            return await gates_module.gate_review.review(
                database,
                rd,
                work_item_id="w1",
                gate="spec_approval",
                node=chain.chain.nodes[1],
                launch=LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
            )
        finally:
            await database.close()

    verdict, note = asyncio.run(scenario())
    assert (verdict, note) == ("approve", "looks right")
    # Launched as the gate's own declared task, at its own canonical path --
    # not a hardcoded `hook_point="gate_review"` and not `command="claude"`.
    assert seen["hook_point"] == "spec_approval.auto_review"
    assert seen["harness"] == "fake"
    # `identify_as_worker` is `run_agent_task`'s default True, which is what
    # sets KRAFT_WORK_ITEM_ID and therefore what `_forbid_self_action` reads;
    # nothing here overrides it.
    assert "identify_as_worker" not in seen


def test_a_gate_declaring_no_agent_reviewer_is_left_to_a_human(tmp_path):
    repo = make_repo(tmp_path)
    chain = _reviewed_chain(repo, declare=False)
    rd = RunDirs(tmp_path / "run").ensure()

    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            await v1_item(database, chain, repo=repo, auto_gate=True)
            return await gates_module.gate_review.review(
                database,
                rd,
                work_item_id="w1",
                gate="spec_approval",
                node=chain.chain.nodes[1],
                launch=LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
            )
        finally:
            await database.close()

    verdict, note = asyncio.run(scenario())
    assert verdict == "undecided"
    assert "declares no agent task" in note


def test_a_node_override_permits_or_suppresses_the_declared_auto_review(tmp_path, monkeypatch):
    """Ruling 31's split: the chain declares the reviewing task, the per-item
    override permits or suppresses it. The override key keeps its persisted,
    publicly exposed name `auto_escalate` even though the chain field is now
    `auto_review`."""
    repo = make_repo(tmp_path)
    chain = _reviewed_chain(repo)
    _no_review(monkeypatch, "reached the reviewer")

    async def scenario(override):
        database = await db.Database.open(tmp_path / f"k-{override}.db")
        try:
            await v1_item(database, chain, repo=repo, auto_gate=True)
            await database.write(
                lambda c: store.set_node_overrides(
                    c, "w1", {"spec_approval": {"auto_escalate": override}}
                )
            )
            await _seed_pending_gate(database)
            return await gates_module.review_gates(
                "awaiting_gate", database, tmp_path, work_item_id="w1", registry=None
            )
        finally:
            await database.close()

    assert asyncio.run(scenario(False)) == "awaiting_gate"
    with pytest.raises(AssertionError, match="reached the reviewer"):
        asyncio.run(scenario(True))


def test_an_override_cannot_switch_on_a_gate_that_declares_no_auto_review(tmp_path, monkeypatch):
    """The override permits a declared task; it cannot name one. A gate with no
    `auto_review` stays human-only however the override is set."""
    repo = make_repo(tmp_path)
    chain = _reviewed_chain(repo, declare=False)
    _no_review(monkeypatch, "a gate declaring no reviewer must not be auto-reviewed")

    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            await v1_item(database, chain, repo=repo, auto_gate=True)
            await database.write(
                lambda c: store.set_node_overrides(
                    c, "w1", {"spec_approval": {"auto_escalate": True}}
                )
            )
            await _seed_pending_gate(database)
            return await gates_module.review_gates(
                "awaiting_gate", database, tmp_path, work_item_id="w1", registry=None
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "awaiting_gate"


def test_the_typed_override_is_a_read_time_view_and_does_not_touch_the_snapshot(tmp_path):
    """`materialized-chain-is-immutable-work-item-input`: the overlay is the
    first thing in V1 that could have written through to the frozen snapshot.
    It must not -- the stored column is byte-identical afterwards, and the
    suppression is only visible in the view."""
    repo = make_repo(tmp_path)
    chain = _reviewed_chain(repo)
    stored_before = chain.to_json()

    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            await v1_item(database, chain, repo=repo, auto_gate=True)
            await database.write(
                lambda c: store.set_node_overrides(
                    c, "w1", {"spec_approval": {"auto_escalate": False}}
                )
            )
            row = _row(database)
            view = store.effective_nodes(executor.chain_of(row), store.node_overrides_of(row))
            return row["materialized_chain"], view
        finally:
            await database.close()

    stored_after, view = asyncio.run(scenario())
    assert stored_after == stored_before
    assert view[1].auto_review is None
    assert view[1].node.auto_review is None
    # The chain the row still holds is untouched: re-reading it arms the gate again.
    assert MaterializedChain.from_json(stored_after).chain.nodes[1].auto_review is not None


# -- the two stranding paths (asked for by the controller alongside this task):
# `maybe_gate`'s cleared-gate guard and `resume_once`'s gate branch. Both fail
# the same way if they regress -- an approved item never advances and stays
# stopped forever, which looks like waiting rather than breaking.


def test_a_walk_re_entered_at_an_approved_gate_passes_over_it(tmp_path):
    """`maybe_gate` answers False for a gate this item has already cleared. If
    it did not, a walk re-entered *at* the gate (a poller, a resume, a
    re-dispatch) would re-request a gate a human already answered and the item
    would never move again."""
    repo = make_repo(tmp_path)
    chain = _spec_gate_chain(repo)
    rd = RunDirs(tmp_path / "run").ensure()

    async def scenario():
        database = await _open_db(tmp_path, chain, repo)
        try:
            launch = LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None)
            await executor.run_once(database, rd, work_item_id="w1", registry=None, launch=launch)
            await database.write(lambda c: store.approve_gate(c, "w1", "spec_approval"))
            # start_index is the *gate's* own index, not the node after it.
            status = await executor.run_once(
                database,
                rd,
                work_item_id="w1",
                registry=None,
                start_index=1,
                launch=launch,
            )
            evts = database.read(lambda c: events.read_after(c, 0, "w1"))
            return status, [e["type"] for e in evts].count("gate_requested")
        finally:
            await database.close()

    status, requests = asyncio.run(scenario())
    assert status == "completed"
    assert requests == 1


def test_a_resume_at_an_approved_gate_continues_past_it(tmp_path):
    """`resume_once`'s gate branch, which had no V1 test. A crash in the window
    between `approve_gate` and the next dispatch leaves `current_node_id` on the
    gate; reattach has to walk *past* it, not sit on it."""
    repo = make_repo(tmp_path)
    chain = _spec_gate_chain(repo)
    rd = RunDirs(tmp_path / "run").ensure()

    async def scenario():
        database = await _open_db(tmp_path, chain, repo)
        try:
            launch = LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None)
            await executor.run_once(database, rd, work_item_id="w1", registry=None, launch=launch)
            await database.write(lambda c: store.approve_gate(c, "w1", "spec_approval"))
            status = await resuming.resume_once(
                database,
                rd,
                work_item_id="w1",
                registry=None,
                adopted={},
                launch=launch,
            )
            sessions = database.read(
                lambda c: c.execute(
                    "SELECT hook_point FROM worker_sessions WHERE work_item_id = 'w1' "
                    "ORDER BY created_at"
                ).fetchall()
            )
            return status, [s["hook_point"] for s in sessions]
        finally:
            await database.close()

    status, hooks = asyncio.run(scenario())
    assert status == "completed"
    assert hooks == ["spec.main.write", "implementation.main.build"]


def test_a_resume_at_an_unanswered_gate_re_requests_it(tmp_path):
    """The other half of the same branch: a gate that was *not* cleared before
    the crash reopens rather than being walked past."""
    repo = make_repo(tmp_path)
    chain = _spec_gate_chain(repo)
    rd = RunDirs(tmp_path / "run").ensure()

    async def scenario():
        database = await _open_db(tmp_path, chain, repo)
        try:
            launch = LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None)
            await executor.run_once(database, rd, work_item_id="w1", registry=None, launch=launch)
            status = await resuming.resume_once(
                database,
                rd,
                work_item_id="w1",
                registry=None,
                adopted={},
                launch=launch,
            )
            sessions = database.read(
                lambda c: c.execute(
                    "SELECT hook_point FROM worker_sessions WHERE work_item_id = 'w1'"
                ).fetchall()
            )
            return status, [s["hook_point"] for s in sessions]
        finally:
            await database.close()

    status, hooks = asyncio.run(scenario())
    assert status == "awaiting_gate"
    # The node after the gate still has not run.
    assert hooks == ["spec.main.write"]
