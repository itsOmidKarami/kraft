"""The shipped default chain's shape, walked (Ruling 87): the implementer runs
once, and `verification` -- the tests, then the review, under their own fix
loop -- is what a failure re-runs; the pre-draft gate shows a work brief; and a
rebase in post-draft feedback re-tests and re-reviews the rebased head.

The chain is the packaged seed's own, resolved from `templates/`, so every task
keeps the kind it ships with -- the changed-test-scope task stays a `builtin`,
which a test library seeded for the fake agent turns into a subprocess.
`dispatch_node` is scripted (`script`), so nothing real launches."""

from pathlib import Path

import pytest
from support.harness import entry_of
from support.item_walk import approve, commits_a_fix, merge_drops, walk_once

from kraft import db, events, executor, item_paths, store
from kraft import policy as _policy
from kraft.executor import gates, stops
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import BuiltinTask
from kraft.vocab import ChainEvent

SEEDED = Path(__file__).resolve().parents[2] / "config"
NO_SETUP = entry_of({"setup_command": ""})
POLICY = _policy.Policy(loops={}, default=_policy.Cap(3, 3600))


@pytest.fixture
def default_chain():
    return TemplateLibrary.from_yaml_dir(SEEDED).resolve_chain("default")


def _walk(it, chain, node: str):
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        start_index=[n.id for n in chain.nodes].index(node),
        policy=POLICY,
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )


def test_the_seeded_test_task_is_the_builtin_this_file_walks(default_chain):
    """What makes the walks below about the shipped chain: the task a failure
    comes from is the `builtin` Kraft ships, not a stand-in."""
    [tests] = next(n for n in default_chain.nodes if n.id == "verification").steps[0].tasks
    assert isinstance(tests.task, BuiltinTask)


@pytest.mark.parametrize(
    ("red", "calls"),
    [
        (
            "test_changed_scopes",
            ["implement", "test_changed_scopes", "repair", "test_changed_scopes", "code_review"],
        ),
        (
            "code_review",
            [
                "implement",
                "test_changed_scopes",
                "code_review",
                "repair",
                "test_changed_scopes",
                "code_review",
            ],
        ),
    ],
    ids=["tests-red", "review-red"],
)
async def test_a_verification_failure_reruns_the_tests_and_review_not_the_implementer(
    item_on, script, default_chain, red, calls
):
    """A red test or a red review is repaired inside `verification`, whose
    fix loop re-measures from its first step: the tests again, then the review.
    The implementer is dispatched once. And the review never runs on red tests:
    in `tests-red` it runs once, after the repair turned them green."""
    it = await item_on(default_chain, "implementation")
    script.plan = {red: ["failed", "done"]}

    assert await _walk(it, default_chain, "implementation") == "awaiting_gate"
    assert script.calls == [*calls, "author"]
    assert gates.pending_gate(it.database, it.id) == "local_review"
    [cycle] = it.events("fix_cycle_started")
    assert cycle["payload"]["node_id"] == "verification"
    # The library's own cap for this loop, not the policy default of 3 above.
    assert cycle["payload"]["max_attempts"] == 2


async def test_the_pre_draft_gate_shows_the_work_brief_the_node_before_it_wrote(
    item_on, script, fake_agent, run_dirs
):
    """`local_review` declares `artifact: work_brief`, and the `work_brief`
    node's author is contracted to write exactly that document -- run for real
    here, on the fake agent, so the file is the one the contract names."""
    chain = TemplateLibrary.from_yaml_dir(fake_agent.templates).resolve_chain("default")
    it = await item_on(chain, "work_brief")
    script.real = {"author"}

    assert await _walk(it, chain, "work_brief") == "awaiting_gate"
    assert gates.pending_gate(it.database, it.id) == "local_review"
    brief = item_paths.document_name(it.id, "work_brief")
    assert gates.gate_artifact(run_dirs, it.row(), "local_review") == brief


def _moves_base_once(it):
    moved = []

    async def effect(_row):
        if not moved:
            moved.append(True)
            await it.database.write(lambda c: store.set_base_ref(c, it.id, "1" * 40))

    return effect


def _holds_a_members_move_once(it):
    """What `forge.run_task` writes when a workspace member's CI wait rebased it and the root
    then stopped, parked or failed: the member's move, held for the node's next completion."""
    held = []

    async def effect(_row):
        if not held:
            held.append(True)
            await it.database.write(
                lambda c: events.append(
                    c,
                    it.id,
                    ChainEvent.BASE_MOVE_HELD,
                    {"node_id": "merge_request_feedback", "before": ""},
                )
            )

    return effect


def _reentered_at_once(monkeypatch, it):
    """Its poller re-enters the park before the walk that parked it has returned."""
    for name in ("stop_for_rate_limit", "stop_for_waiting"):
        real = getattr(stops, name)

        async def parks(*args, real=real):
            stopped = await real(*args)
            await it.database.write(lambda c: store.mark_reentered(c, it.id))
            return stopped

        monkeypatch.setattr(stops, name, parks)


_REPAIRED = ["await_review", "triage", "await_ci", "repair_feedback", "sync_mr", "await_review"]


@pytest.mark.parametrize(
    ("plan", "moves", "parks", "before"),
    [
        ({}, "base", (), ["await_review", "triage", "await_ci"]),
        # Pushing the rebased head failed: `on_failure` repairs and pushes it,
        # and the node's re-measure parks on the review of that head.
        (
            {"await_ci": ["failed", "done"], "await_review": ["done", "waiting", "done"]},
            "base",
            ("waiting",),
            _REPAIRED,
        ),
        # Pushing the rebased head was rate-limited.
        (
            {"await_ci": ["rate_limited", "done"]},
            "base",
            ("rate_limited",),
            ["await_review", "triage", "await_ci"],
        ),
        # Its poller took the park back before the walk that parked it checked the move.
        (
            {"await_ci": ["failed", "done"], "await_review": ["done", "waiting", "done"]},
            "reentered",
            (),
            _REPAIRED,
        ),
        (
            {"await_ci": ["rate_limited", "done"]},
            "reentered",
            (),
            ["await_review", "triage", "await_ci"],
        ),
        # A workspace member moved under a root whose failure the node repaired.
        ({"await_ci": ["failed", "done"]}, "member", (), [*_REPAIRED, "triage", "await_ci"]),
        # A workspace member moved under a rate-limited root: held across each park.
        (
            {"await_ci": ["rate_limited", "rate_limited", "done"]},
            "member",
            ("rate_limited", "rate_limited"),
            ["await_review", "triage", "await_ci", "await_ci", "await_ci"],
        ),
    ],
    ids=[
        "completes",
        "parks-on-the-review-wait",
        "rate-limited",
        "parks-and-its-poller-re-enters-first",
        "rate-limited-and-its-poller-re-enters-first",
        "a-member-moved-under-a-repaired-root",
        "a-member-moved-under-a-rate-limited-root",
    ],
)
async def test_a_rebase_in_post_draft_feedback_retests_and_rereviews_the_rebased_head(
    item_on, script, default_chain, monkeypatch, plan, moves, parks, before
):
    """Kraft-bjw6a. A CI conflict rebased away moves the base, and
    `merge_request_feedback` declares `on_base_changed: {restart_from:
    verification}`, so the rebased head is tested and reviewed again before
    anything reads it as green -- also when the node parks after the move and
    its poller walks it again from its cursor.

    The rebase was clean -- the forge's forced rebase succeeds only with no
    textual conflict -- so the approved `local_review` in the restart span keeps
    its approval and the walk passes over it (Ruling 162). A rebase that had to
    resolve a conflict reopens it:
    test_base_change.py::test_a_resolved_conflict_reopens_the_approved_gates_in_its_span."""
    it = await item_on(default_chain, "merge_request_feedback")
    await approve(it, "local_review")
    script.plan = plan
    script.effects = {
        "await_ci": _holds_a_members_move_once(it) if moves == "member" else _moves_base_once(it)
    }
    if moves == "reentered":
        _reentered_at_once(monkeypatch, it)
    await it.session("parked", "merge_request_feedback.main.await_review", "waiting")

    walked = await _walk(it, default_chain, "merge_request_feedback")
    # No later walk observes that wait, or reuses its row for the rebased head.
    closed = [s["status"] for s in it.sessions() if s["id"] == "parked"] == ["paused"]
    assert closed == (moves != "member" or not parks), "a held member move closes it later"
    for park in parks:
        assert walked == park
        # A member's move does not re-verify into the rate limit it parked on.
        assert bool(it.events("base_change_restart")) == (moves != "member")
        await it.database.write(lambda c: store.mark_reentered(c, it.id))  # what its poller does
        walked = await walk_once(it, POLICY)
    assert walked == "awaiting_gate"
    assert [s["status"] for s in it.sessions() if s["id"] == "parked"] == ["paused"]
    assert script.calls == [
        *before,
        "test_changed_scopes",
        "code_review",
        "author",
        "author",
        "rebase",
        "open",
        "await_review",
        "triage",
        "await_ci",
        "author",
    ]
    [restart] = it.events("base_change_restart")
    assert restart["payload"]["restart_from"] == "verification"
    assert gates.pending_gate(it.database, it.id) == "final_review"
    requested = [e["payload"]["gate"] for e in it.events("gate_requested")]
    assert requested == ["local_review", "final_review"]
    assert it.events("gate_reopened") == []


@pytest.mark.parametrize(
    ("how", "answer"),
    [
        ("pause", "paused"),
        ("question", "needs_context"),
        ("server-restart", "paused"),
        ("skip", "paused"),
        ("by-another-door", "paused"),
    ],
    ids=["pause", "question", "server-restart", "skip", "by-another-door"],
)
async def test_a_stop_after_a_rebase_keeps_what_a_restart_must_not_overwrite(
    item_on, script, default_chain, run_dirs, how, answer
):
    """A person's stop after the node moved its base stays on that node, so a steer or an
    answer reaches its agent, and the restart runs when the node is next walked, from the
    record alone. A skip drops the owed restart; a door that took the item gets no record."""
    node = "merge_request_feedback"
    it = await item_on(default_chain, node)
    await approve(it, "local_review")
    feedback = next(n for n in default_chain.nodes if n.id == node)
    paths = {t.task.id: t.path for step in feedback.steps for t in step.tasks}
    triage, ci = paths["triage"], paths["await_ci"]
    await it.session("held", triage, "paused")
    moves = _moves_base_once(it)

    async def stops(row):
        if it.events("base_move_held") or len(script.steers["await_ci"]) > 1:
            if answer == "needs_context":  # the answered look's own session
                await it.session(f"answered-{len(script.steers['await_ci'])}", ci, "done")
            return  # only the first look stops
        await moves(row)
        if answer == "needs_context":
            await it.session("asked", ci, "needs_context")
        elif how != "by-another-door":
            await it.database.write(lambda c: store.pause_work_item(c, it.id, []))

    script.plan = {"await_ci": [answer, "done"]}
    script.effects = {"await_ci": stops}

    walked = await _walk(it, default_chain, node)

    assert walked == ("paused" if answer == "paused" else "needs_human")
    assert it.events("base_change_restart") == []
    assert it.events("gate_reopened") == []
    assert it.row()["current_node_id"] == node
    assert [s["status"] for s in it.sessions() if s["id"] == "held"] == ["paused"]
    assert bool(it.events("base_move_held")) == (how != "by-another-door")
    if how == "by-another-door":
        return
    if how == "skip":
        await it.database.write(lambda c: store.skip_node(c, it.id, node, None, "not needed"))
        await it.database.write(lambda c: store.stand_at(c, it.id, node))
        script.plan = {}
        await walk_once(it, POLICY)
        assert it.events("base_change_restart") == []
        return
    assert executor.resume_steer(it.database, it.row(), "note", {}) == {triage: "note"}

    await it.database.write(lambda c: store.mark_reentered(c, it.id))
    database = await db.Database.open(run_dirs.db) if how == "server-restart" else it.database
    try:
        walked = await executor.run_once(
            database,
            it.run_dirs,
            work_item_id=it.id,
            policy=POLICY,
            steer="answer",
            launch=executor.LaunchContext(repo_entry=NO_SETUP),
        )
    finally:
        if how == "server-restart":
            await database.close()

    assert walked == "awaiting_gate"
    assert script.calls[3:5] == ["await_ci", "test_changed_scopes"]
    [restart] = it.events("base_change_restart")
    assert restart["payload"]["restart_from"] == "verification"


async def test_a_rebase_in_draft_merge_request_does_not_restart_merge_request_feedback(
    item_on, script, default_chain
):
    """Kraft-3llig. `draft_merge_request`'s rebase step moves and persists
    `base_ref` on the node *before* `merge_request_feedback` -- and
    `draft_merge_request` itself declares no `on_base_changed`, so its own
    pass completes ordinarily. `merge_request_feedback` reads `base_ref` as
    its `pre_base` only once it starts, which is already the moved value by
    then: nothing restarts, even though `merge_request_feedback` does declare
    `on_base_changed: {restart_from: verification}`."""
    it = await item_on(default_chain, "draft_merge_request")
    await approve(it, "local_review")
    script.effects = {"rebase": _moves_base_once(it)}

    assert await _walk(it, default_chain, "draft_merge_request") == "awaiting_gate"
    assert script.calls == ["rebase", "open", "await_review", "triage", "await_ci", "author"]
    assert it.events("base_change_restart") == []
    assert gates.pending_gate(it.database, it.id) == "final_review"
    assert it.row()["base_ref"] == "1" * 40


_GATES = (
    "spec_approval",
    "plan_approval",
    "chain_revision_approval",
    "local_review",
    "final_review",
)


async def test_default_chain_rewinds_a_train_drop_through_final_review(
    item_on, script, default_chain
):
    """The shipped `merge` declares `on_merge_rejected: {restart_from:
    merge_request_feedback}`: a train that rejects the merge request for its
    code runs the repair, and the repaired head goes back through the merge
    request's own CI and a fresh `final_review` before it is queued again.
    Only the approved gates in that span reopen: `chain_revision_approval` and
    `local_review` lie before it and keep their approvals."""
    it = await item_on(default_chain, "merge")
    for gate in _GATES:
        await approve(it, gate)
    merge_drops(it, script)
    script.effects["repair"] = commits_a_fix(it)

    assert await walk_once(it) == "awaiting_gate"
    assert gates.pending_gate(it.database, it.id) == "final_review"
    assert [e["payload"] for e in it.events("gate_reopened")] == [
        {"gate": "final_review", "reason": "merge_rejected"}
    ]

    await approve(it, "final_review")
    assert await walk_once(it) == "completed"
    [restart] = it.events("merge_rejection_restart")
    assert restart["payload"]["restart_from"] == "merge_request_feedback"
    starts = [
        e["payload"]["node_id"] for e in it.events("node_started") if e["seq"] > restart["seq"]
    ]
    assert starts == [
        "merge_request_feedback",
        "work_item_summary",
        "final_review",
        "mark_ready",
        "external_approval",
        "merge",
        "post_merge_ci",
    ]
    assert script.calls[:2] == ["merge", "repair"]


async def test_a_final_review_rejection_asks_the_approved_local_review_again(
    item_on, script, default_chain
):
    """`final_review` rejects to `implementation`, before `local_review`: the
    re-run walks back through `local_review` with new work, so its earlier
    approval no longer stands and the walk asks it again rather than passing
    over it, though no `gate_reopened` names it (`store.reject_gate` writes
    none)."""
    it = await item_on(default_chain, "final_review")
    for gate in _GATES[:-1]:
        await approve(it, gate)
    await it.database.write(lambda c: store.request_gate(c, it.id, "final_review", "final_review"))
    nodes = default_chain.nodes
    target = await executor.apply_rejection(
        it.database, POLICY, work_item_id=it.id, nodes=nodes, gate="final_review", note="redo"
    )
    assert nodes[target].id == "implementation"

    assert await _walk(it, default_chain, "implementation") == "awaiting_gate"
    assert script.calls == ["implement", "test_changed_scopes", "code_review", "author"]
    assert gates.pending_gate(it.database, it.id) == "local_review"
    [rejected] = it.events("gate_rejected")
    asked = [
        e["payload"]["gate"] for e in it.events("gate_requested") if e["seq"] > rejected["seq"]
    ]
    assert asked == ["local_review"]

    # Approved again after the rejection, it stands: the walk goes on to the
    # gate that rejected, which asks again.
    await it.database.write(lambda c: store.approve_gate(c, it.id, "local_review"))
    assert await _walk(it, default_chain, "local_review") == "awaiting_gate"
    assert gates.pending_gate(it.database, it.id) == "final_review"
