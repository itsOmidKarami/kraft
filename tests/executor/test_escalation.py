"""`gates.auto_escalate_stuck` and `gates.resume_after_escalation`: an agent
dispatched onto a `needs_human` stop nobody has looked at, and the self-retry
that agent may leave behind."""

import pytest

from kraft import events, executor, store
from kraft import policy as _policy
from kraft.executor import gates as gates_module

_CHAIN = """
- id: implementation
  kind: exec
  tasks: [{id: run, kind: subprocess, command: "true"}]
"""

LAUNCH = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)


async def _stuck(item_on, reason="budget exhausted"):
    """An item standing at `implementation`, stopped `needs_human` for `reason`."""
    it = await item_on(_CHAIN, "implementation", repo="/r")
    await _stop(it, reason)
    return it


async def _stop(it, reason):
    await it.database.write(lambda c: store.mark_needs_human(c, it.id, "implementation", reason))


def _pol(**kwargs):
    return _policy.Policy(loops={}, default=_policy.Cap(1, 1), **kwargs)


def _escalate(it, status="needs_human", *, launch=LAUNCH, **policy_kwargs):
    return gates_module.auto_escalate_stuck(
        status,
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        registry=None,
        policy=_pol(**policy_kwargs),
        launch=launch,
    )


@pytest.fixture
def dispatched(monkeypatch):
    """Every `escalate.dispatch` call `auto_escalate_stuck` makes, faked."""
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append({"message": message, "auto": auto, "evts": evts})
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    return calls


# -- seeds: what each row adds on top of an item stopped for "budget exhausted" --


def _restopped(reason):
    async def seed(it):
        await _stop(it, reason)

    return seed


async def _pending_gate(it):
    # A gate requested *after* the stop, so nothing but the pending-gate guard
    # can tell this row from an eligible one (Kraft-cg6yt).
    await it.database.write(lambda c: store.request_gate(c, it.id, "implementation", "g"))


def _escalation(status, *, retried=False):
    """An escalation turn this run dispatched (`escalation_message` names its
    session, as `escalate.dispatch` writes it), exited `status`. `retried`: a
    human then retried instead of answering, and the item stopped again for
    something unrelated -- so the turn belongs to the run before this one."""

    async def seed(it):
        await it.database.write(
            lambda c: events.append(
                c, it.id, "escalation_message", {"session_id": "e1", "message": "hi", "auto": True}
            )
        )
        await it.session("e1", "escalation", status)
        if retried:
            await it.database.write(lambda c: events.append(c, it.id, "work_item_retried", {}))
            await _stop(it, "again")

    return seed


async def _running_escalation(it):
    await it.session("s1", "escalation")


async def _over_budget(it):
    await it.session("s1", "implementation.main.run")
    await it.database.write(
        lambda c: c.execute("UPDATE worker_sessions SET cost_usd = 12.0 WHERE id = 's1'")
    )


@pytest.mark.parametrize(
    ("status", "seed", "kwargs", "dispatches", "skipped"),
    [
        ("completed", None, {}, 0, []),
        # `startup.py`'s reattach path used to pass `launch=None`, and crashed
        # into `deps.guard` over the item's real stop reason (Kraft-9046).
        ("needs_human", None, {"launch": None}, 0, []),
        # The gate flow owns this stop (Kraft-cg6yt: this row used to pass with
        # the guard deleted, because nothing faked the dispatch it reached).
        ("needs_human", _pending_gate, {}, 0, []),
        # A worker asked a direct question; only a human can answer it.
        ("needs_human", _restopped("needs_context: which flag?"), {}, 0, []),
        # The escalation turn itself asked one (Kraft-b52cm).
        ("needs_human", _escalation("needs_context"), {}, 0, ["needs_context"]),
        # ... but a question from a previous run of stuckness does not suppress
        # this one forever (the unscoped `last_escalation_status` regression).
        ("needs_human", _escalation("needs_context", retried=True), {}, 1, []),
        ("needs_human", _escalation("done"), {}, 1, []),
        ("needs_human", _running_escalation, {}, 0, []),
        ("needs_human", None, {"auto_escalate_stuck": False}, 0, []),
        ("needs_human", None, {"auto_escalate_delay_s": 600}, 0, []),
        # A delay of 0 behaves exactly like no delay at all.
        ("needs_human", None, {"auto_escalate_delay_s": 0}, 1, []),
        # A stop *because* the spend cap was breached buys no more agent turns.
        (
            "needs_human",
            _over_budget,
            {"budget": _policy.Budget(work_item_usd=10.0, daily_usd=None)},
            0,
            ["budget"],
        ),
        ("needs_human", None, {}, 1, []),
    ],
    ids=[
        "not-needs-human",
        "no-launch-context",
        "pending-gate",
        "needs-context-reason",
        "escalation-asked-a-question",
        "question-from-a-prior-run",
        "escalation-finished-clean",
        "escalation-already-running",
        "disabled-by-policy",
        "before-its-delay",
        "delay-elapsed",
        "budget-breached",
        "eligible",
    ],
)
async def test_auto_escalate_stuck_dispatches_only_onto_an_unattended_stop(
    item_on, dispatched, status, seed, kwargs, dispatches, skipped
):
    """Every guard between a `needs_human` stop and an auto-dispatched agent
    turn, one row each. Each row fakes `escalate.dispatch` and counts its
    calls, so a guard that stops guarding turns its row red on the count --
    not only through conftest's real-agent tripwire."""
    it = await _stuck(item_on)
    if seed is not None:
        await seed(it)

    assert await _escalate(it, status, **kwargs) == status
    assert len(dispatched) == dispatches
    for call in dispatched:
        assert call["auto"] is True
        assert "Auto-escalated" in call["message"]
        # The single timeline `auto_escalate_stuck` read for its own checks is
        # the one handed to `dispatch`, not a second fresh read.
        assert call["evts"] is not None
    assert [e["payload"]["reason"] for e in it.events("work_item_auto_escalate_skipped")] == skipped


async def test_auto_escalate_stuck_caps_out_and_emits_an_event(item_on, monkeypatch):
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
        await it.session(sid, "escalation", "done")
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    it = await _stuck(item_on)

    for _ in range(2):
        await _escalate(it, auto_escalate_stuck_cap=2)
    # A third call is over the cap: no further dispatch, a capped event instead.
    await _escalate(it, auto_escalate_stuck_cap=2)

    types = [e["type"] for e in it.events()]
    # The session-lifecycle events are on the timeline (proving the fake
    # reproduces the real shape) and did not stop the scan from counting
    # past them.
    assert types.count("worker_session_created") == 2
    assert types.count("worker_session_exited") == 2
    assert types.count("escalation_message") == 2
    assert types.count("work_item_auto_escalate_capped") == 1


@pytest.fixture
def no_rebase_no_walk(monkeypatch):
    """`resume_after_escalation`'s rebase and walk, stood in for; returns the
    kwargs each `walk.run` call got."""

    async def fake_refresh(worktree, repo, branch):
        return None

    walk_calls = []

    async def fake_walk_run(database, run_dirs, **kw):
        walk_calls.append(kw)
        return "completed"

    monkeypatch.setattr(gates_module._builtins, "refresh_worktree_base", fake_refresh)
    monkeypatch.setattr("kraft.executor.walk.run", fake_walk_run)
    return walk_calls


def _self_retry_event(**payload):
    return {"node_id": "implementation", "key": None, "gate_key": None, "steer": None, **payload}


async def test_auto_escalate_stuck_consumes_a_self_retry_after_the_session_exits(
    item_on, monkeypatch, no_rebase_no_walk
):
    """The escalation agent calling `kraft item retry` on itself (lifecycle.py's
    `work_item_self_retry_requested` deferral) must not run the rebase/spawn
    until `escalate.dispatch` -- and so the session it was mid-call from --
    has actually returned. `fake_dispatch` mimics the agent's self-retry by
    appending the same event `retry_work_item` would, from inside the
    dispatch call it is standing in for; `auto_escalate_stuck` must only act
    on it once `dispatch` (this call) returns."""

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        payload = _self_retry_event(session_id="s1", key="implementation", steer="fixed it")
        await database.write(
            lambda c: events.append(c, work_item_id, "work_item_self_retry_requested", payload)
        )
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    it = await _stuck(item_on)

    assert await _escalate(it) == "completed"
    assert len(no_rebase_no_walk) == 1
    assert no_rebase_no_walk[0]["start_index"] == 0
    assert no_rebase_no_walk[0]["steer"] == "fixed it"
    types = [e["type"] for e in it.events()]
    assert it.events("work_item_retried")[0]["payload"]["escalated"] is True
    assert types.index("work_item_self_retry_requested") < types.index("work_item_retried")


@pytest.mark.parametrize(
    ("between", "expected"),
    [
        ({"type": "work_item_retried", "payload": {"escalated": True}}, 2),
        ({"type": "work_item_retried", "payload": {}}, 1),
        ({"type": "node_started", "payload": {"node_id": "implementation"}}, 2),
        ({"type": "node_started", "payload": {"node_id": "some_other_node"}}, 2),
        ({"type": "gate_requested", "payload": {"gate": "plan_approval"}}, 2),
        ({"type": "gate_approved", "payload": {"gate": "plan_approval", "by": "agent"}}, 2),
        ({"type": "gate_approved", "payload": {"gate": "plan_approval", "by": "human"}}, 1),
    ],
    ids=[
        "escalations-own-retry",
        "humans-retry",
        "self-retrys-node-started",
        "chain-movement",
        "gate-re-reached",
        "gate-decided-by-an-agent",
        "gate-decided-by-a-human",
    ],
)
def test_auto_dispatch_count_resets_only_when_a_human_acts(between, expected):
    """The cap counter counts auto-dispatched turns since the current run of
    stuckness began, and only a *human* acting ends that run (gates.py
    `_RUN_BOUNDARY`). The machinery acting on itself -- the escalated agent's
    own `{"escalated": true}` retry, the `node_started` that retry's `walk.run`
    writes, chain movement, a gate re-reached or decided `by: agent` -- is the
    same run, or a stop a retry cannot clear (a budget breach) escalates
    forever."""
    stop = {"type": "work_item_needs_human", "payload": {"node_id": "implementation"}}
    auto = {"type": "escalation_message", "payload": {"auto": True}}

    assert gates_module._auto_dispatch_count([stop, auto, between, stop, auto]) == expected


@pytest.mark.parametrize("moved_to", ["abandoned", "active"])
async def test_resume_after_escalation_drops_a_self_retry_on_a_moved_item(
    item_on, run_dirs, moved_to
):
    """A human who moves the item while the escalation turn is running must not
    have it resurrected: `retry_after_cap`/`mark_needs_human` both UPDATE
    unconditionally, and the worktree may already be gone (finding:
    `resume_after_escalation` never re-checked status).

    `active` is the status that makes the bracket dangerous: a human resumed the
    item and somebody else's walk owns it. This site awaits its walk inline, so
    it has no `task_is_live` callback, and `stops.claimed_or_stopped` would see
    `active` on the way out of the drop and stamp `needs_human` over it -- the
    opposite of what `work_item_self_retry_dropped` means. The bracket covers
    only the claim this call made (`handed_off=lambda: not claimed`)."""
    it = await _stuck(item_on, "git rebase failed")
    cursor = it.events()[-1]["seq"]
    await it.database.write(
        lambda c: events.append(c, it.id, "work_item_self_retry_requested", _self_retry_event())
    )
    await it.database.write(
        lambda c: c.execute("UPDATE work_items SET status = ? WHERE id = ?", (moved_to, it.id))
    )

    status = await gates_module.resume_after_escalation(
        it.database, run_dirs, work_item_id=it.id, cursor=cursor, registry=None
    )

    assert status == moved_to
    assert it.status() == moved_to, "the bracket stopped an item this call never claimed"
    assert len(it.events("work_item_self_retry_dropped")) == 1
    assert not it.events("work_item_retried"), "something re-entered the chain"


@pytest.mark.parametrize(
    ("extra", "seeded"),
    [({"seeded": True}, True), ({}, False)],
    ids=["seeded-steer", "event-written-before-the-field"],
)
async def test_resume_after_escalation_threads_the_seeded_flag_onto_retry_after_cap(
    item_on, run_dirs, no_rebase_no_walk, extra, seeded
):
    """The deferred self-retry marks whether its steer was Kraft's own seeded
    recap of the last measurement or a human's; consuming it must not lose that
    mark. An event written before the field existed has no `seeded` key at all,
    and reads back False, not KeyError."""
    it = await _stuck(item_on, "stuck")
    cursor = it.events()[-1]["seq"]
    payload = _self_retry_event(key="implementation", steer="- [important] a.py:1 — x", **extra)
    await it.database.write(
        lambda c: events.append(c, it.id, "work_item_self_retry_requested", payload)
    )

    status = await gates_module.resume_after_escalation(
        it.database, run_dirs, work_item_id=it.id, cursor=cursor, registry=None
    )

    assert status == "completed"
    assert it.events("work_item_retried")[0]["payload"]["seeded"] is seeded


async def test_resume_after_escalation_stops_an_item_whose_node_left_the_chain(item_on, run_dirs):
    """The claim-then-return class in its fourth instance, and the one no review
    named. `resume_after_escalation` claims the item, writes `retry_after_cap`,
    and *then* located its start index with
    `next(i for i, n in enumerate(walk.chain_of(row).chain.nodes) if n.id == node_id)`
    -- which raises `StopIteration` for a node the chain does not have and
    `LookupError` for a legacy row, both after the write. The item was left
    claimed `active` with the cap already cleared and no walk behind it.

    A static sweep of `return`/`raise` statements cannot see either raise, which
    is the argument for `stops.claimed_or_stopped` bracketing the whole region
    rather than a stop per early return.
    """
    it = await _stuck(item_on, "git rebase failed")
    cursor = it.events()[-1]["seq"]
    # The self-retry names a node this item's chain does not contain -- what a
    # `set_chain_template` switch leaves behind.
    payload = _self_retry_event(node_id="gone_from_the_chain")
    await it.database.write(
        lambda c: events.append(c, it.id, "work_item_self_retry_requested", payload)
    )

    status = await gates_module.resume_after_escalation(
        it.database, run_dirs, work_item_id=it.id, cursor=cursor, registry=None
    )

    assert it.status() == "needs_human", "left claimed 'active' with no walk behind it"
    assert status == "needs_human"


@pytest.mark.parametrize(
    ("extra", "path"),
    [
        ({}, "implementation"),
        ({"path": "implementation.main.run"}, "implementation.main.run"),
        ({"restart": True}, None),
    ],
    ids=["an-older-request-names-its-node", "by-path", "restart"],
)
async def test_a_self_retry_forks_at_the_path_it_asked_for(
    item_on, run_dirs, no_rebase_no_walk, extra, path
):
    """The escalated agent's `kraft item retry --path` is deferred with its
    path, and consuming the request forks the run there, as `/retry` would."""
    it = await _stuck(item_on, "stuck")
    cursor = it.events()[-1]["seq"]
    payload = _self_retry_event(**extra)
    await it.database.write(
        lambda c: events.append(c, it.id, "work_item_self_retry_requested", payload)
    )

    await gates_module.resume_after_escalation(
        it.database, run_dirs, work_item_id=it.id, cursor=cursor, registry=None
    )

    assert it.events("run_forked")[-1]["payload"]["path"] == path
    assert len(no_rebase_no_walk) == 1


async def test_a_self_retry_applies_the_override_it_carried(item_on, run_dirs, no_rebase_no_walk):
    """Kraft-vvj32: consuming a deferred self-retry forks with the validated
    override the request carried, the same as a direct `/retry` would."""
    from kraft.templates.forks import override_record
    from kraft.templates.retry import validate_retry_override

    it = await item_on(_CHAIN, "implementation", repo="/r")
    await _stop(it, "stuck")
    cursor = it.events()[-1]["seq"]
    override = validate_retry_override(
        store.materialized_chain_of(it.row()),
        "implementation.main.run",
        task_config={"command": "make again"},
    )
    payload = _self_retry_event(path="implementation.main.run", override=override_record(override))
    await it.database.write(
        lambda c: events.append(c, it.id, "work_item_self_retry_requested", payload)
    )

    await gates_module.resume_after_escalation(
        it.database, run_dirs, work_item_id=it.id, cursor=cursor, registry=None
    )

    [fork] = it.database.read(lambda c: store.run_forks(c, it.id))
    assert fork.override["task_config"] == {"command": "make again"}
    task = fork.chain.chain.nodes[0].steps[0].tasks[0].task
    assert task.command == "make again"
