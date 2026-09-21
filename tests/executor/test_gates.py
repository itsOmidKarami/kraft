"""V1 gate nodes: a gate halts the walk, owns its artifact and reject target,
and is auto-reviewed only when it declares a reviewer and the item opts in.

Auto-escalation of a non-gate `needs_human` stop is tests/executor/test_escalation.py."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from support.harness import fake_harness_home, v1_chain, v1_resolved, v1_walk

from kraft import events, executor, store
from kraft import policy as _policy
from kraft.adapters import agent as agent_mod
from kraft.api.routes import gates as gates_route
from kraft.executor import gates as gates_module
from kraft.executor import resuming
from kraft.executor.context import LaunchContext
from kraft.templates.models import MaterializedChain

NO_SETUP = LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None)


def _exec(node_id, task_id="run"):
    return {
        "id": node_id,
        "kind": "exec",
        "tasks": [{"id": task_id, "kind": "subprocess", "command": "true"}],
    }


def _agent_task(id="reviewer", **kw):
    return {"id": id, "kind": "agent", "harness": "fake", "prompt": "review it", **kw}


def _spec_gate(**gate_extra):
    """spec (exec) -> spec_approval (gate) -> implementation (exec)."""
    return [
        _exec("spec", "write"),
        {"id": "spec_approval", "kind": "gate", "artifact": "spec", **gate_extra},
        _exec("implementation", "build"),
    ]


def _reviewed(*, declare=True):
    """spec -> spec_approval, whose gate declares an agent reviewer (or not)."""
    return [
        _exec("spec", "write"),
        {
            "id": "spec_approval",
            "kind": "gate",
            "artifact": "spec",
            "reject_to": "spec",
            **({"auto_review": _agent_task()} if declare else {}),
        },
    ]


def _walk(it, **kwargs):
    return executor.run_once(
        it.database, it.run_dirs, work_item_id=it.id, registry=None, launch=NO_SETUP, **kwargs
    )


def _cap(attempts):
    return _policy.Policy(loops={}, default=_policy.Cap(attempts=attempts, wall_clock_s=3600))


def _fake_state(it):
    """The slice of `app.state` the gate routes' `apply_approval` reads."""

    class _Indexer:
        async def ingest_gate_artifact(self, **kw):
            self.seen = kw

    return SimpleNamespace(db=it.database, run_dirs=it.run_dirs, indexer=_Indexer())


def _evt(t, **payload):
    return {"type": t, "payload": payload, "created_at": "2026-09-12T00:00:00+00:00"}


async def _requested(it, gate="spec_approval"):
    await it.database.write(
        lambda c: events.append(c, it.id, "gate_requested", {"gate": gate, "node_id": gate})
    )


def _review(it, **kwargs):
    return gates_module.review_gates(
        "awaiting_gate", it.database, it.run_dirs, work_item_id=it.id, registry=None, **kwargs
    )


def _no_review(monkeypatch, why):
    def _boom(*a, **kw):
        raise AssertionError(why)

    monkeypatch.setattr(gates_module.gate_review, "review", _boom)


def _launch_review(it, node=None):
    return gates_module.gate_review.review(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate="spec_approval",
        node=node or it.chain.chain.nodes[1],
        launch=NO_SETUP,
    )


async def _resolved(value):
    return value


# -- rejection ----------------------------------------------------------------


async def test_apply_rejection_returns_the_reentry_index_then_stops_at_the_cap(item_on):
    it = await item_on(
        [_exec("implementation"), {"id": "gate", "kind": "gate", "reject_to": "implementation"}],
        repo="/r",
    )
    nodes = it.chain.chain.nodes
    results = [
        await executor.apply_rejection(
            it.database, _cap(2), work_item_id=it.id, nodes=nodes, gate="gate", note=note
        )
        for note in ("not yet", "still not", "no")
    ]

    # The third breaches attempts=2: no re-entry, and the item is parked.
    assert results == [0, 0, None]
    assert it.status() == "needs_human"


async def test_gate_rejection_follows_its_own_reject_to(item_on):
    """`reject_to` wins over the "nearest preceding execution node" fallback, so
    this chain puts a second execution node between `spec` and the gate --
    otherwise the two answers coincide and the test pins nothing."""
    it = await item_on(
        [
            _exec("spec", "write"),
            _exec("plan", "write"),
            {"id": "spec_approval", "kind": "gate", "artifact": "spec", "reject_to": "spec"},
        ]
    )
    nodes = it.chain.chain.nodes

    target = await executor.apply_rejection(
        it.database, _cap(5), work_item_id=it.id, nodes=nodes, gate="spec_approval", note="redo"
    )

    assert nodes[target].id == "spec"
    assert it.events("gate_rejected")[0]["payload"]["node"] == "spec"


async def test_the_reject_loop_counter_a_retry_clears_is_the_one_a_rejection_bumps(item_on):
    """One spelling, two writers. `apply_rejection` bumps the key and the retry
    route clears it, and each had its own f-string (`f"{gate}_reject_loop"` here,
    `f"{node.id}_reject_loop"` there, and a third spelling in
    `store.retry_after_cap`'s docstring). They agreed only because a V1 gate's node
    id *is* its gate name -- and a retry that cleared a key nothing bumped leaves
    the gate re-opening onto a spent counter, every rejection after it refused
    forever (Kraft-ko7j §A4). Asserted against the row `bump_counter` actually
    wrote, not against a second copy of the format string.
    """
    it = await item_on(_spec_gate(reject_to="spec"))
    await executor.apply_rejection(
        it.database,
        _cap(5),
        work_item_id=it.id,
        nodes=it.chain.chain.nodes,
        gate="spec_approval",
        note="again",
    )

    keys = it.database.read(
        lambda c: [r["key"] for r in c.execute("SELECT key FROM retry_counters").fetchall()]
    )
    assert keys == [gates_module.reject_loop_key("spec_approval")]


def test_a_rejection_with_no_reject_to_re_enters_the_execution_node_before_the_gate():
    """Ruling 54. A V1 gate has no execution shape, so the old fallback --
    "re-enter at the gate node itself" -- dispatches nothing and immediately
    re-requests the same gate, a ping-pong bounded only by the reject cap. The
    node that produced what the gate is about is what gets re-measured."""
    nodes = v1_resolved(_spec_gate()).nodes  # no reject_to

    assert nodes[executor.reject_target(nodes, 1, None)].id == "spec"


def test_a_gate_with_nothing_before_it_falls_back_to_itself():
    """The one case where re-entering at the gate is still the answer: there is
    no earlier execution node to re-measure, so the gate re-opens with the note."""
    nodes = v1_resolved([{"id": "sign_off", "kind": "gate"}]).nodes

    assert executor.reject_target(nodes, 0, None) == 0


def test_a_rejection_cannot_be_aimed_forward_past_the_gate():
    """`reject_to` is validated by `Chain` itself, but a request body's `node`
    is validated by nothing -- aimed forward it would skip every node between."""
    with pytest.raises(ValueError, match="not a node of this chain before 'spec_approval'"):
        executor.reject_target(v1_resolved(_spec_gate()).nodes, 1, "implementation")


async def test_pending_gate_closes_on_a_node_skipped_event(item_on):
    """`kraft item skip` writes `node_skipped`, never `gate_approved`/
    `gate_rejected` -- without treating it as a boundary too, a gate
    bypassed by skip reads as pending forever (code review finding)."""
    it = await item_on(_spec_gate(), "spec", repo="/r")
    await it.database.write(lambda c: store.request_gate(c, it.id, "spec", "spec_approval"))
    assert executor.pending_gate(it.database, it.id) == "spec_approval"

    await it.database.write(lambda c: store.skip_node(c, it.id, "spec", "spec_approval", None))

    assert executor.pending_gate(it.database, it.id) is None


# -- gate-node-opens-and-halts-execution / gate-approval-advances-to-next-node --


async def test_gate_node_halts_until_approved(tmp_path, repo):
    """The walk stops *at* the gate: the node after it does not start, and the
    gate opens under its own node id with no name table anywhere. Reaching the
    gate launches nothing of its own: the only session is the one the preceding
    execution node ran (`gate-control-does-not-generate-review-work`)."""
    status, evts, sessions, row = await v1_walk(
        tmp_path, v1_chain(_spec_gate(), repo=repo), repo=repo
    )

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


async def test_gate_approval_advances_to_the_node_after_the_gate(item_on):
    """`apply_approval` hands back the ordered nodes, the caller advances one
    past the gate's own index, and the gate node is *completed* rather than left
    rendering as a node still running (4a's Concern 4)."""
    it = await item_on(_spec_gate())
    await _walk(it)

    nodes, reason = await gates_route.apply_approval(_fake_state(it), it.row(), "spec_approval")
    assert reason is None
    start = executor.gate_node_index(nodes, "spec_approval") + 1
    assert nodes[start].id == "implementation"
    await it.database.write(lambda c: store.approve_gate(c, it.id, "spec_approval"))

    assert await _walk(it, start_index=start) == "completed"
    assert [s["hook_point"] for s in it.sessions()] == [
        "spec.main.write",
        "implementation.main.build",
    ]
    # The gate's own node pair closes on approval, and the gate is never
    # re-requested on the way past.
    assert "spec_approval" in [e["payload"]["node_id"] for e in it.events("node_completed")]
    assert len(it.events("gate_requested")) == 1


async def test_an_ordinary_gate_has_ordinary_pause_and_approval_behaviour(item_on):
    """`chain-finalized-remains-a-dedicated-marker`'s second half: a gate
    without the marker approves with no document at all, where the final-review
    gate refuses (see the marker test below)."""
    it = await item_on(
        [_exec("build"), {"id": "sign_off", "kind": "gate", "artifact": "review_brief"}]
    )
    await _walk(it)

    nodes, reason = await gates_route.apply_approval(_fake_state(it), it.row(), "sign_off")

    assert reason is None
    assert nodes is not None


async def test_the_chain_finalized_marker_not_the_gate_name_selects_chain_review(item_on):
    """Two gates, and the names are deliberately the wrong way round: the one
    *called* `chain_finalized` carries no marker, and the marked one is called
    something else. Only the marked gate takes the final-review path, which
    refuses an approval whose review document was never written."""
    it = await item_on(
        [
            _exec("build"),
            {"id": "chain_finalized", "kind": "gate", "artifact": "review_brief"},
            _exec("summarize"),
            {"id": "all_done", "kind": "gate", "chain_finalized": True, "artifact": "review_brief"},
        ]
    )
    await _walk(it)
    st, row = _fake_state(it), it.row()

    named_nodes, named_reason = await gates_route.apply_approval(st, row, "chain_finalized")
    flag_nodes, flag_reason = await gates_route.apply_approval(st, row, "all_done")

    # Named `chain_finalized`, unmarked: an ordinary gate, approvable.
    assert named_reason is None and named_nodes is not None
    # Marked, differently named: the final review's own document is the subject,
    # and nothing wrote one.
    assert flag_nodes is None
    assert "final review document is missing" in flag_reason


# -- the two stranding paths: `maybe_gate`'s cleared-gate guard and
# `resume_once`'s gate branch. Both fail the same way if they regress -- an
# approved item never advances and stays stopped forever, which looks like
# waiting rather than breaking.


async def test_a_walk_re_entered_at_an_approved_gate_passes_over_it(item_on):
    """`maybe_gate` answers False for a gate this item has already cleared. If
    it did not, a walk re-entered *at* the gate (a poller, a resume, a
    re-dispatch) would re-request a gate a human already answered and the item
    would never move again."""
    it = await item_on(_spec_gate())
    await _walk(it)
    await it.database.write(lambda c: store.approve_gate(c, it.id, "spec_approval"))

    # start_index is the *gate's* own index, not the node after it.
    assert await _walk(it, start_index=1) == "completed"
    assert len(it.events("gate_requested")) == 1


def _resume(it):
    return resuming.resume_once(
        it.database, it.run_dirs, work_item_id=it.id, registry=None, adopted={}, launch=NO_SETUP
    )


async def test_a_resume_at_an_approved_gate_continues_past_it(item_on):
    """`resume_once`'s gate branch, which had no V1 test. A crash in the window
    between `approve_gate` and the next dispatch leaves `current_node_id` on the
    gate; reattach has to walk *past* it, not sit on it."""
    it = await item_on(_spec_gate())
    await _walk(it)
    await it.database.write(lambda c: store.approve_gate(c, it.id, "spec_approval"))

    assert await _resume(it) == "completed"
    assert [s["hook_point"] for s in it.sessions()] == [
        "spec.main.write",
        "implementation.main.build",
    ]
    # The walk passes *over* the cleared gate rather than entering it: entering
    # it again stamps a second `node_started` on a gate the item already
    # answered. Cosmetic in effect and a lie in the timeline: the board would
    # show the approved gate entered twice.
    started = [e["payload"]["node_id"] for e in it.events("node_started")]
    assert started.count("spec_approval") == 1


async def test_a_resume_at_an_unanswered_gate_re_requests_it(item_on):
    """The other half: a gate *not* cleared before the crash reopens rather than
    being walked past. Crash resume picks up only an `active` item."""
    it = await item_on(_spec_gate())
    await _walk(it)
    await it.database.write(lambda c: c.execute("UPDATE work_items SET status = 'active'"))

    assert await _resume(it) == "awaiting_gate"
    # The node after the gate still has not run.
    assert [s["hook_point"] for s in it.sessions()] == ["spec.main.write"]


# -- gate-owns-gate-behaviour / gate-control-does-not-generate-review-work --


async def test_a_gate_shows_the_artifact_its_own_field_names(item_on):
    """The gate's own `artifact:` names the kind. Nothing scans the preceding
    node's tasks, so the preceding node here declares a *different* kind and the
    gate still shows its own.

    And a gate generates no artifact of its own: one declaring no `artifact` has
    no document, which is an answerable gate rather than an error
    (`gate-control-does-not-generate-review-work`). Neither does a declared kind
    whose file the worker never wrote."""
    it = await item_on(
        [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [_agent_task("write", prompt="p", produces="plan")],
            },
            {"id": "spec_approval", "kind": "gate", "artifact": "spec"},
            {"id": "no_document", "kind": "gate"},
            {"id": "unwritten", "kind": "gate", "artifact": "review_brief"},
        ]
    )
    rel = agent_mod.artifact_path("spec", it.id)
    (it.worktree / rel).parent.mkdir(parents=True, exist_ok=True)
    (it.worktree / rel).write_text("the spec\n")

    row = it.row()
    assert executor.gate_artifact(it.run_dirs, row, "spec_approval") == rel
    assert executor.gate_artifact(it.run_dirs, row, "no_document") is None
    assert executor.gate_artifact(it.run_dirs, row, "unwritten") is None


async def test_a_gate_with_arbitrary_id_works_without_a_name_table(tmp_path, repo, run_dirs):
    """`GATE_NAMES`' closed vocabulary is gone: a gate id a custom chain invents
    opens, carries its artifact, and rejects to its own target."""
    nodes = [
        _exec("shape_it"),
        {
            "id": "does_marketing_like_it",
            "kind": "gate",
            "artifact": "spec",
            "reject_to": "shape_it",
        },
    ]
    rel = agent_mod.artifact_path("spec", "w1")
    (run_dirs.worktrees / "w1" / rel).parent.mkdir(parents=True, exist_ok=True)
    (run_dirs.worktrees / "w1" / rel).write_text("x\n")

    status, evts, _sessions, _row = await v1_walk(
        tmp_path, v1_chain(nodes, repo=repo), repo=repo, run_dirs=run_dirs
    )

    assert status == "awaiting_gate"
    assert next(e for e in evts if e["type"] == "gate_requested")["payload"]["gate"] == (
        "does_marketing_like_it"
    )
    resolved = v1_resolved(nodes).nodes
    assert resolved[executor.reject_target(resolved, 1, None)].id == "shape_it"


async def test_a_chain_whose_plan_node_was_trimmed_still_reaches_chain_finalized(tmp_path, repo):
    """Ruling 66, and the question attachment trimming actually raises.

    Legacy `gates.md:110` says a chain with no `plan` node SHALL NOT open its
    `chain_finalized` gate -- written when the only way to have no plan node
    was to have no plan. Under Rulings 28/30/35 an item filed with `--plan`
    has that node *trimmed*, so read literally the rule forbids the final gate
    ever opening and the chain could never finish.

    The answer is yes: a chain may finalize when the node that produced its
    plan was trimmed, because the plan exists as an attachment. Pinned here so
    nobody re-derives the legacy rule from the trimmed chain's shape.
    """
    from kraft.policy import InstancePolicy, InstancePolicyInput
    from kraft.templates.environment import Repository, WorkItemTarget

    resolved = v1_resolved(
        [
            {
                "id": "plan",
                "kind": "exec",
                "tasks": [_agent_task("author", prompt="write the plan", produces="plan")],
            },
            {"id": "plan_approval", "kind": "gate", "artifact": "plan"},
            _exec("implementation", "build"),
            {
                "id": "chain_review",
                "kind": "gate",
                "chain_finalized": True,
                "artifact": "review_brief",
            },
        ]
    )
    chain = resolved.materialize(
        target=WorkItemTarget.for_repository(Repository(id="target", path=str(repo))),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput()),
        attachment_kinds=frozenset({"plan"}),
    )
    # The trim took the producing node and its gate, and left the final marker.
    assert [n.id for n in chain.chain.nodes] == ["implementation", "chain_review"]

    status, evts, _sessions, row = await v1_walk(tmp_path, chain, repo=repo)

    assert status == "awaiting_gate"
    assert row["current_node_id"] == "chain_review"
    requested = [e["payload"]["gate"] for e in evts if e["type"] == "gate_requested"]
    assert requested == ["chain_review"]


# -- gate-auto-review-is-explicit-and-bounded --


async def test_auto_review_runs_only_with_work_item_opt_in(item_on, monkeypatch):
    _no_review(monkeypatch, "an item that did not opt in must not be auto-reviewed")
    opted_out = await item_on(_reviewed(), auto_gate=False)
    opted_in = await item_on(_reviewed(), wid="w2", auto_gate=True)
    for it in (opted_out, opted_in):
        await _requested(it)

    assert await _review(opted_out) == "awaiting_gate"
    # And with opt-in the arming check passes, so the patched reviewer is reached.
    with pytest.raises(AssertionError, match="must not be auto-reviewed"):
        await _review(opted_in)


async def test_auto_review_waits_out_its_effective_delay(item_on, monkeypatch):
    _no_review(monkeypatch, "review must not run before the effective delay elapses")
    it = await item_on(_reviewed(), auto_gate=True)
    await _requested(it)
    pol = _policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_escalate_delay_s=600)

    assert await _review(it, policy=pol) == "awaiting_gate"


async def test_auto_review_stops_at_its_effective_attempt_limit(item_on, monkeypatch):
    """`policy.auto_review_attempts` bounds attempts per `gate_requested`; the
    limit used to be a hardcoded one. Two attempts are already spent here, so a
    limit of 2 refuses and a limit of 3 lets the third through."""
    _no_review(monkeypatch, "third attempt")
    it = await item_on(_reviewed(), auto_gate=True)
    await _requested(it)
    for _ in range(2):
        await it.database.write(
            lambda c: events.append(c, it.id, "gate_auto_review_started", {"gate": "spec_approval"})
        )

    def limit(n):
        return _policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_review_attempts=n)

    assert await _review(it, policy=limit(2)) == "awaiting_gate"
    with pytest.raises(AssertionError, match="third attempt"):
        await _review(it, policy=limit(3))


_REQUEST = _evt("gate_requested", gate="g")
_STARTED = _evt("gate_auto_review_started", gate="g")
_UNDECIDED = _evt("gate_auto_review_skipped", gate="g", reason="undecided")


@pytest.mark.parametrize(
    ("since_request", "attempts"),
    [
        ([_STARTED], 1),
        ([_STARTED, _evt("work_item_resumed")], 0),
        ([_UNDECIDED, _evt("gate_rejected", gate="g"), _REQUEST], 0),
        ([_STARTED, _UNDECIDED], 1),
        ([_evt("gate_auto_review_skipped", gate="g", reason="budget")], 1),
        ([_STARTED, _UNDECIDED, _STARTED, _UNDECIDED], 2),
    ],
    ids=[
        "a-review-that-crashed-mid-flight",
        "reset-at-a-run-boundary",
        "a-previous-requests-attempt",
        "an-undecided-review-is-one-not-two",
        "a-skip-that-never-launched",
        "two-undecided-reviews",
    ],
)
def test_gate_review_attempts_counts_the_reviews_since_the_request(since_request, attempts):
    """`gate_review.review` writes `gate_auto_review_started` before it launches
    anything, so a review that crashed mid-flight -- no verdict, no skip event --
    still spends an attempt at the default bound of one. A run boundary resets
    it, or a crashed review demotes the gate to human-only for good: a human's
    resume re-enters `review_gates`, hits the spent bound, and returns unchanged
    with nothing logged. Only the current request's attempts count. A review
    that ran and came back `undecided` writes both a `_started` and a `_skipped`,
    and counting both would halve every bound above 1; a `{reason: budget}` skip
    never launched and counts for itself."""
    assert gates_module._gate_review_attempts([_REQUEST, *since_request], "g") == attempts


def test_an_unpaired_skip_counts_as_its_own_attempt():
    """**Defence in depth against a state the type now forbids.** Do not delete
    this as testing the impossible.

    `_gate_review_attempts` pairs a `gate_auto_review_skipped {reason:
    undecided}` with the `gate_auto_review_started` it closes, so a review that
    ran and came back undecided is one attempt and not two. An *unpaired* skip --
    one with no `_started` before it -- is something that returned before
    launching, and it has to count for itself: counted as zero, `auto_check_due`
    stays True and the delay poller re-arms the same dead gate every tick
    forever, burning a `max_concurrent` slot and 409-ing a human's retry.

    Today's only producer of an unpaired skip was `gate_review.review`'s
    non-agent-reviewer guard, and `GateNode.auto_review: AgentTask | None` now
    makes that unrepresentable in a validated chain -- so the state is
    constructed here deliberately rather than reached through the product. The
    earlier implementation approximated "not the tail of a `_started`" as "reason
    is not `undecided`", which made correctness depend on every early return
    choosing a distinct reason string.
    """
    # Paired: one attempt.
    assert gates_module._gate_review_attempts([_REQUEST, _STARTED, _UNDECIDED], "g") == 1
    # Unpaired, same reason string: still one attempt.
    assert gates_module._gate_review_attempts([_REQUEST, _UNDECIDED], "g") == 1
    # And a second unpaired one is a second attempt, so any bound is reachable.
    assert gates_module._gate_review_attempts([_REQUEST, _UNDECIDED, _UNDECIDED], "g") == 2


async def test_auto_review_reports_a_verdict_and_cannot_clear_its_own_gate(
    item_on, tmp_path, monkeypatch
):
    """The reviewer is dispatched as a *worker* -- `KRAFT_WORK_ITEM_ID` is set,
    so `client.context._forbid_self_action` refuses to let it approve or reject
    its own item. It reports a verdict and `review_gates` applies it."""
    it = await item_on(_reviewed(), auto_gate=True)
    seen = {}

    async def _capture(_db, _rd, **kw):
        seen.update(kw)
        (it.run_dirs.results / f"{kw['session_id']}.json").write_text(
            json.dumps({"status": "done", "verdict": "approve", "concerns": "looks right"})
        )
        return "done"

    monkeypatch.setattr(agent_mod, "run_agent_task", _capture)
    # The reviewer selects profile `fake`; this puts it, and its provider, in
    # the test's `KRAFT_HOME`. The launch itself is `_capture` above.
    fake_harness_home(tmp_path, ["true"])
    await _requested(it)

    assert await _launch_review(it) == ("approve", "looks right")
    # Launched as the gate's own declared task, at its own canonical path --
    # not a hardcoded `hook_point="gate_review"` and not `command="claude"`.
    assert seen["hook_point"] == "spec_approval.auto_review"
    assert seen["harness"] == "fake"
    # `identify_as_worker` is `run_agent_task`'s default True, which is what
    # sets KRAFT_WORK_ITEM_ID and therefore what `_forbid_self_action` reads;
    # nothing here overrides it.
    assert "identify_as_worker" not in seen


async def test_auto_review_launches_with_the_method_mode_and_tools_it_resolved(
    item_on, monkeypatch
):
    """Kraft-k2tb2: `resolve_agent_task` answers the reviewer's `skill:` method,
    its profile's `permission_mode` and its `allowed_tools`, and the launch
    dropped all three -- a reviewer ran without the method it selected and
    still returned a verdict, so the drop looked like a working reviewer."""
    it = await item_on(_reviewed(), auto_gate=True)
    seen = {}
    resolved = agent_mod.Invocation(
        command="",
        harness="fake",
        model=None,
        deny_tools=(),
        steering_texts=(),
        method_text="THE GATE-REVIEW METHOD",
        permission_mode="plan",
        allowed_tools=("Read", "Grep"),
    )
    monkeypatch.setattr(agent_mod, "resolve_agent_task", lambda *a, **kw: resolved)

    async def _capture(_db, _rd, **kw):
        seen.update(kw)
        (it.run_dirs.results / f"{kw['session_id']}.json").write_text(
            json.dumps({"status": "done", "verdict": "approve", "concerns": "ok"})
        )
        return "done"

    monkeypatch.setattr(agent_mod, "run_agent_task", _capture)
    await _requested(it)

    await _launch_review(it)

    assert seen.get("method_text") == "THE GATE-REVIEW METHOD", "skill method dropped"
    assert seen.get("permission_mode") == "plan", "permission_mode dropped"
    assert tuple(seen.get("allowed_tools") or ()) == ("Read", "Grep"), "allowed_tools dropped"


async def test_a_gate_auto_review_launch_carries_the_never_signal_rule(
    item_on, tmp_path, monkeypatch
):
    """`every-agent-launch-carries-kraft-safety-rules`: the gate's reviewer is
    launched by `gate_review`, not `dispatch_node`, and still gets the rule."""
    it = await item_on(_reviewed(), auto_gate=True)
    fake_harness_home(tmp_path, ["true"])
    launched = {}

    async def _spawn(_db, _rd, *, cmd, **_kw):
        launched["argv"] = "\n".join(cmd)
        return "done"

    monkeypatch.setattr(agent_mod._subprocess, "run_task", _spawn)
    await _requested(it)

    await _launch_review(it)

    assert agent_mod.SAFETY_RULES in launched["argv"]


async def test_a_gate_declaring_no_agent_reviewer_is_left_to_a_human(item_on):
    it = await item_on(_reviewed(declare=False), auto_gate=True)

    verdict, note = await _launch_review(it)

    assert verdict == "undecided"
    assert "declares no agent task" in note


async def test_a_reviewer_that_slipped_past_the_type_spends_an_attempt(item_on):
    """The defensive half. `gate_review.review`'s kind guard is unreachable from a
    validated chain, but a `MaterializedChain` round-tripped out of a row written
    by an older build is not something the new type can retroactively police. When
    it does fire it must spend an attempt, which means writing a skip whose reason
    is not `"undecided"` -- `auto_check_due`'s own docstring is about exactly the
    re-arm-forever loop an uncounted return causes.

    Built with `model_construct`, which bypasses validation on purpose: that is the
    only way to reach the guard now.
    """
    from kraft.templates.models import GateNode, ResolvedNode, ResolvedTask, SubprocessTask

    it = await item_on(_reviewed(), auto_gate=True)
    task = SubprocessTask.model_validate({"id": "r", "kind": "subprocess", "command": "true"})
    node = ResolvedNode(
        id="spec_approval",
        node=GateNode.model_construct(id="spec_approval", kind="gate", auto_review=task),
        auto_review=ResolvedTask(path="spec_approval.auto_review", task=task),
    )
    await _requested(it)

    verdict, note = await _launch_review(it, node)

    assert verdict == "undecided"
    assert "declares no agent task" in note
    # The attempt is countable, so the next poller tick refuses instead of
    # re-arming: one unpaired skip is one attempt, and the default bound is 1.
    assert gates_module._gate_review_attempts(it.events(), "spec_approval") == 1


def test_a_gate_cannot_declare_a_reviewer_that_cannot_report_a_verdict():
    """The root fix for the re-dispatch loop above (item 9). The contract is
    "write a `verdict` into your result file", which a subprocess or a forge wait
    has no way to do -- so a chain declaring one declares something the runtime
    cannot honour. Closed by the type, the same call `AgentTask.produces` makes,
    rather than discovered by running it."""
    from kraft.templates.models import GateNode

    for bad in (
        {"id": "r", "kind": "subprocess", "command": "true"},
        {"id": "r", "kind": "forge", "target": "mr.ci"},
        {"id": "r", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"},
    ):
        with pytest.raises(ValidationError, match="auto_review"):
            GateNode.model_validate({"id": "g", "kind": "gate", "auto_review": bad})


async def _override(it, value):
    await it.database.write(
        lambda c: store.set_node_overrides(c, it.id, {"spec_approval": {"auto_escalate": value}})
    )


async def test_a_node_override_permits_or_suppresses_the_declared_auto_review(item_on, monkeypatch):
    """Ruling 31's split: the chain declares the reviewing task, the per-item
    override permits or suppresses it. The override key keeps its persisted,
    publicly exposed name `auto_escalate` even though the chain field is now
    `auto_review`."""
    _no_review(monkeypatch, "reached the reviewer")
    suppressed = await item_on(_reviewed(), auto_gate=True)
    permitted = await item_on(_reviewed(), wid="w2", auto_gate=True)
    for it, value in ((suppressed, False), (permitted, True)):
        await _override(it, value)
        await _requested(it)

    assert await _review(suppressed) == "awaiting_gate"
    with pytest.raises(AssertionError, match="reached the reviewer"):
        await _review(permitted)


async def test_an_override_cannot_switch_on_a_gate_that_declares_no_auto_review(
    item_on, monkeypatch
):
    """The override permits a declared task; it cannot name one. A gate with no
    `auto_review` stays human-only however the override is set."""
    _no_review(monkeypatch, "a gate declaring no reviewer must not be auto-reviewed")
    it = await item_on(_reviewed(declare=False), auto_gate=True)
    await _override(it, True)
    await _requested(it)

    assert await _review(it) == "awaiting_gate"


async def test_the_typed_override_is_a_read_time_view_and_does_not_touch_the_snapshot(item_on):
    """`materialized-chain-is-immutable-work-item-input`: the overlay is the
    first thing in V1 that could have written through to the frozen snapshot.
    It must not -- the stored column is byte-identical afterwards, and the
    suppression is only visible in the view."""
    it = await item_on(_reviewed(), auto_gate=True)
    stored_before = it.chain.to_json()
    await _override(it, False)

    row = it.row()
    view = store.effective_nodes(executor.chain_of(row), store.node_overrides_of(row))

    assert row["materialized_chain"] == stored_before
    assert view[1].auto_review is None
    assert view[1].node.auto_review is None
    # The chain the row still holds is untouched: re-reading it arms the gate again.
    stored = MaterializedChain.from_json(row["materialized_chain"])
    assert stored.chain.nodes[1].auto_review is not None


async def test_an_override_that_cannot_do_anything_is_refused_at_the_door(item_on):
    """§4 says to say it "in the error or the docstring, whichever the caller will
    read". The caller here is `PATCH /work-items/{id}`, and a 200 that persists a
    switch which changes nothing is the shape a human reads as "I turned it on".
    `_validate_node_overrides` is the route's own validator, called with the same
    arguments the route calls it with."""
    from fastapi import HTTPException

    from kraft.api.routes import work_items as work_items_route

    declared = await item_on(_reviewed())
    undeclared = await item_on(_reviewed(declare=False), wid="w2")

    def validate(it, value):
        return work_items_route._validate_node_overrides(
            SimpleNamespace(db=it.database), it.row(), {"spec_approval": {"auto_escalate": value}}
        )

    # Declared: arming it is meaningful, so it is accepted.
    assert validate(declared, True) is None
    # Undeclared: refused, and the message says why rather than leaving the
    # caller to infer it from an unchanged board.
    with pytest.raises(HTTPException) as exc:
        validate(undeclared, True)
    assert exc.value.status_code == 422
    assert "declares no 'auto_review' task" in exc.value.detail
    # Suppressing a gate that declares nothing is a harmless no-op, not an error:
    # it says the same thing the chain already says.
    assert validate(undeclared, False) is None


async def test_a_fixed_verdict_re_enters_the_execution_node_before_the_gate(item_on, monkeypatch):
    """Ruling 54 applied to the `fixed` verdict, on a chain where the answer is
    *not* the same as `reject_to`'s: `reject_to` names `spec` and the node before
    the gate is `plan`. A `fixed` verdict repaired something in this worktree, so
    the smallest thing whose re-run measures the repair is the node that produced
    what the gate is about -- not the whole way back to `reject_to`, and not the
    gate itself, which runs nothing."""
    nodes = [
        _exec("spec", "w"),
        _exec("plan", "w"),
        {
            "id": "plan_approval",
            "kind": "gate",
            "artifact": "plan",
            "reject_to": "spec",
            "auto_review": _agent_task(),
        },
        _exec("build", "w"),
    ]
    verdicts = {"fixed": "fixed", "rejected": "reject"}
    starts = {}

    async def fake_run_once(database, run_dirs, *, work_item_id, **kw):
        starts[work_item_id] = kw.get("start_index")
        return "completed"

    monkeypatch.setattr("kraft.executor.walk.run_once", fake_run_once)
    monkeypatch.setattr(
        gates_module.gate_review,
        "review",
        lambda *a, work_item_id, **kw: _resolved((verdicts[work_item_id], "I fixed it")),
    )
    for wid in verdicts:
        it = await item_on(nodes, wid=wid, auto_gate=True)
        await _requested(it, "plan_approval")
        await _review(it, policy=_cap(5), launch=NO_SETUP)

    # index 1 == `plan`, the execution node immediately before the gate. index 0
    # == `spec`, which is what the gate's own `reject_to` names. The two verdicts
    # must not coincide, or this pins nothing.
    assert starts == {"fixed": 1, "rejected": 0}


async def test_gate_review_that_cannot_locate_its_gate_stops_rather_than_leaving_it_claimed(
    item_on, monkeypatch
):
    """`review_gates`' approve branch claims the item with `store.approve_gate`
    and then locates its start index with `gate_node_index(approved, gate) + 1`
    -- a defaultless `next(...)` whose own docstring says it "Raises
    `StopIteration` for a gate this chain does not have", *after* the claim.

    `dev/check_claim_handoff.py` cannot see that exit: it is a propagating
    exception, not a `return`/`raise` statement. So this is the test that has to,
    and it is the reason the fix is a bracket over the region rather than a stop
    at each exit the checker happens to list.
    """
    it = await item_on(_reviewed(), auto_gate=True)
    monkeypatch.setattr(
        gates_module.gate_review, "review", lambda *a, **kw: _resolved(("approve", None))
    )

    async def _on_approve(row, gate):
        # A node list the gate is absent from -- what a template switch or a
        # spliced chain leaves behind.
        return [n for n in it.chain.chain.nodes if n.id != gate], None

    await _requested(it)
    with pytest.raises((StopIteration, RuntimeError)):
        await _review(it, on_approve=_on_approve)

    assert it.status() == "needs_human", (
        "the gate was cleared and the item left claimed 'active' with no walk behind it"
    )
