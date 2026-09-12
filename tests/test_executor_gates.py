import asyncio
import json

import pytest

from kraft import db, events, executor, store
from kraft.executor import gates as gates_module


def test_apply_rejection_returns_the_reentry_index_then_stops_at_the_cap(tmp_path):
    from kraft import policy as _policy

    chain = {
        "nodes": [
            {"id": "implementation", "tasks": [], "gate_after": None},
            {
                "id": "human_review",
                "tasks": [],
                "gate_after": "human_review_approval",
                "reject_to": "implementation",
            },
        ]
    }
    policy = _policy.Policy(loops={}, default=_policy.Cap(attempts=2, wall_clock_s=3600))

    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id=None,
                    title="t",
                    repo="/r",
                    chain_template="default",
                    chain_definition=json.dumps(chain),
                )
            )
            first = await executor.apply_rejection(
                database,
                policy,
                work_item_id="w1",
                chain=chain,
                gate="human_review_approval",
                note="not yet",
            )
            second = await executor.apply_rejection(
                database,
                policy,
                work_item_id="w1",
                chain=chain,
                gate="human_review_approval",
                note="still not",
            )
            third = await executor.apply_rejection(
                database,
                policy,
                work_item_id="w1",
                chain=chain,
                gate="human_review_approval",
                note="no",
            )
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


def test_review_gates_reads_the_items_own_node_override_not_the_templates(tmp_path, monkeypatch):
    """UI v2 · 04 point 1: the template turns `auto_escalate` on for this gate,
    but the item's own `node_overrides` turns it back off -- agent gate review
    must not fire. If `review_gates` read `chain_definition` straight, this
    node would still show `auto_escalate: true` and `gate_review.review`
    would be called (and this test's monkeypatch would raise).
    """
    chain = {
        "nodes": [
            {"id": "a", "tasks": [], "gate_after": "g", "auto_escalate": True},
        ]
    }

    def _boom(*a, **kw):
        raise AssertionError("gate_review.review must not run: the item's override disarmed it")

    monkeypatch.setattr(gates_module.gate_review, "review", _boom)

    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id=None,
                    title="t",
                    repo="/r",
                    chain_template="default",
                    chain_definition=json.dumps(chain),
                    auto_gate=True,
                )
            )
            await database.write(
                lambda c: store.set_node_overrides(c, "w1", {"a": {"auto_escalate": False}})
            )
            await database.write(lambda c: events.append(c, "w1", "gate_requested", {"gate": "g"}))
            status = await gates_module.review_gates(
                "awaiting_gate", database, tmp_path, work_item_id="w1", registry=None
            )
            assert status == "awaiting_gate"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reject_target_rejects_a_forward_node_with_a_value_error():
    chain = {
        "nodes": [
            {"id": "a", "tasks": [], "gate_after": "g"},
            {"id": "b", "tasks": [], "gate_after": None},
        ]
    }
    with pytest.raises(ValueError):
        executor.reject_target(chain, 0, "b")


async def _seed_stuck(database, wid, *, reason, chain=None, node_id="implementation"):
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo="/r",
            chain_template="default",
            chain_definition=json.dumps(chain or {"nodes": [{"id": node_id, "tasks": []}]}),
        )
    )
    await database.write(lambda c: store.enter_node(c, wid, node_id))
    await database.write(lambda c: store.mark_needs_human(c, wid, node_id, reason))


def test_auto_escalate_stuck_skips_a_non_needs_human_status(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            status = await gates_module.auto_escalate_stuck(
                "completed", database, None, work_item_id="w1", registry=None
            )
            return status
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "completed"


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
            status = await gates_module.auto_escalate_stuck(
                "needs_human", database, rd, work_item_id=wid, registry=None, policy=pol
            )
            return status
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "needs_human"


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

            return await gates_module.auto_escalate_stuck(
                "needs_human", database, rd, work_item_id=wid, registry=None, policy=pol
            )
        finally:
            await database.close()

    status = asyncio.run(scenario())
    assert status == "needs_human"


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
            return await gates_module.auto_escalate_stuck(
                "needs_human", database, rd, work_item_id=wid, registry=None, policy=pol
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "needs_human"


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
            return await gates_module.auto_escalate_stuck(
                "needs_human", database, rd, work_item_id=wid, registry=None, policy=pol
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
            for _ in range(2):
                await gates_module.auto_escalate_stuck(
                    "needs_human", database, rd, work_item_id=wid, registry=None, policy=pol
                )
            # A third call is over the cap: no further dispatch, a capped event instead.
            await gates_module.auto_escalate_stuck(
                "needs_human", database, rd, work_item_id=wid, registry=None, policy=pol
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
            return await gates_module.auto_escalate_stuck(
                "needs_human", database, rd, work_item_id=wid, registry=None, policy=pol
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
            status = await gates_module.auto_escalate_stuck(
                "needs_human", database, rd, work_item_id=wid, registry=None, policy=pol
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
            status = await gates_module.auto_escalate_stuck(
                "needs_human", database, rd, work_item_id=wid, registry=None, policy=pol
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
