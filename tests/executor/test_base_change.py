"""`on_base_changed`: a node whose work moves the worktree base restarts the
chain at its declared `restart_from`, spending no recovery and no fix-loop
attempt; and a rebase conflict is resolved only by the node's explicit
`on_conflict` handler, whose successful rebase restarts the same span."""

import shlex
import subprocess

import pytest
from support.harness import _git

from kraft import executor, store
from kraft import policy as _policy
from kraft.executor.context import BASE_MOVED

NO_SETUP = {"setup_command": ""}


def _sub(task_id):
    return {"id": task_id, "kind": "subprocess", "command": shlex.join(["true"])}


def _sync():
    return {"id": "sync", "kind": "forge", "target": "mr.sync"}


def _chain(**rebase_fields):
    """verify (with a fix loop) -> rebase (sync, then open) -> publish."""
    rebase = {
        "id": "rebase",
        "kind": "exec",
        "steps": [{"id": "sync", "tasks": [_sync()]}, {"id": "open", "tasks": [_sub("open")]}],
        **rebase_fields,
    }
    return [
        {
            "id": "verify",
            "kind": "exec",
            "tasks": [_sub("check")],
            "fix_loop": {"tasks": [_sub("fix")]},
        },
        rebase,
        {"id": "publish", "kind": "exec", "tasks": [_sub("publish")]},
    ]


RESTART = {"on_base_changed": {"restart_from": "verify"}}


def _walk(it, policy=None):
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        policy=policy or _policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
    )


def _moves_base(it, times=1):
    """A `sync` effect that moves `base_ref` on its first `times` runs."""
    moved = [0]

    async def effect(_row):
        if moved[0] < times:
            moved[0] += 1
            base = f"{moved[0]:040d}"
            await it.database.write(lambda c: store.set_base_ref(c, it.id, base))

    return effect


def _starts(it):
    return [e["payload"]["node_id"] for e in it.events("node_started")]


async def test_a_moved_base_restarts_the_declared_span_and_spends_no_attempt(item_on, script):
    """`base-change-restarts-a-declared-chain-span` and
    `base-change-is-not-an-execution-failure`: the span reruns with a fresh
    fix-loop budget, and the base change itself opens no recovery and no fix
    cycle."""
    it = await item_on(_chain(**RESTART, fix_loop={"tasks": [_sub("rebase_fix")]}))
    script.effects = {"sync": _moves_base(it)}
    stale = _policy.Cap(attempts=3, wall_clock_s=3600)
    for key in ("verify.fix_loop", "rebase.fix_loop"):
        await it.database.write(lambda c, key=key: store.bump_counter(c, it.id, key, stale))

    assert await _walk(it) == "completed"
    assert _starts(it) == ["verify", "rebase", "verify", "rebase", "publish"]
    assert script.calls == ["check", "sync", "open", "check", "sync", "open", "publish"]
    [restart] = it.events("base_change_restart")
    assert restart["payload"] == {
        "node_id": "rebase",
        "restart_from": "verify",
        "nodes": ["verify", "rebase"],
        "restart": 1,
    }
    # The whole span gets a fresh budget, not only its first node.
    for key in ("verify.fix_loop", "rebase.fix_loop"):
        assert it.database.read(lambda c, key=key: store.read_counter(c, it.id, key)) is None
    assert not it.events("fix_cycle_started")
    assert not it.events("node_recovery_started")
    # The restarted span is told what moved. Nothing here is an agent to take
    # it, so it is reported undelivered -- which is where its text is readable.
    assert script.steers["check"][1].source == "seeded"
    [undelivered] = it.events("steer_undelivered")
    assert "rebased onto a newer" in undelivered["payload"]["steer"]


@pytest.mark.parametrize("fix_loop", [False, True], ids=["loopless", "fix-loop"])
async def test_a_base_moved_stop_skips_the_nodes_later_steps_until_the_restart(
    item_on, script, fix_loop
):
    loop = {"fix_loop": {"tasks": [_sub("rebase_fix")]}} if fix_loop else {}
    it = await item_on(_chain(**RESTART, **loop))
    script.plan = {"sync": [BASE_MOVED, "done"]}
    script.effects = {"sync": _moves_base(it)}

    assert await _walk(it) == "completed"
    assert script.calls == ["check", "sync", "check", "sync", "open", "publish"]
    assert not it.events("fix_cycle_started")
    # Restarted, not completed: `rebase` only completes on the pass that ran
    # its later step.
    order = [
        (e["type"], e["payload"]["node_id"])
        for e in it.events()
        if e["type"] in ("node_started", "node_completed")
    ]
    assert order.index(("node_completed", "rebase")) > order.index(("node_started", "verify"), 1)


async def test_no_movement_and_no_declaration_mean_no_restart(item_on, script):
    """Nothing moved: nothing restarts. Moved on a node that declares no
    `on_base_changed`: the walk goes on exactly as before."""
    it = await item_on(_chain(**RESTART))
    assert await _walk(it) == "completed"
    assert _starts(it) == ["verify", "rebase", "publish"]

    undeclared = await item_on(_chain(), wid="w2")
    script.effects = {"sync": _moves_base(undeclared)}
    assert await _walk(undeclared) == "completed"
    assert _starts(undeclared) == ["verify", "rebase", "publish"]
    assert not undeclared.events("base_change_restart")


@pytest.mark.parametrize("fix_loop", [False, True], ids=["loopless", "fix-loop"])
async def test_a_base_moved_stop_on_an_undeclared_node_completes_it_and_spends_nothing(
    item_on, script, fix_loop
):
    """Only a node declaring `on_base_changed` restarts; any other completes
    where it stopped, as a clean pass -- never a failure for a fix cycle."""
    loop = {"fix_loop": {"tasks": [_sub("rebase_fix")]}} if fix_loop else {}
    it = await item_on(_chain(**loop))
    script.plan = {"sync": [BASE_MOVED]}

    assert await _walk(it) == "completed"
    assert script.calls == ["check", "sync", "publish"]
    assert not it.events("fix_cycle_started")
    assert it.database.read(lambda c: store.read_counter(c, it.id, "rebase.fix_loop")) is None


async def test_restarts_are_bounded_by_the_nodes_own_counter(item_on, script):
    it = await item_on(_chain(**RESTART))
    script.effects = {"sync": _moves_base(it, times=5)}
    policy = _policy.Policy(
        loops={"rebase.on_base_changed": _policy.Cap(1, 3600)}, default=_policy.Cap(3, 3600)
    )

    assert await _walk(it, policy) == "needs_human"
    assert len(it.events("base_change_restart")) == 1
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert reason == "rebase.on_base_changed exhausted after 1 restart(s) from 'verify'"


async def test_a_conflict_without_an_explicit_handler_is_an_ordinary_failure(item_on, script):
    """`rebase-conflict-requires-explicit-handler`."""
    it = await item_on(_chain(**RESTART))
    script.plan = {"sync": ["conflict"]}

    assert await _walk(it) == "needs_human"
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert reason.startswith("task failed in node rebase: sync [forge]")
    assert not it.events("node_recovery_started")


def _with_handler():
    return {
        "on_base_changed": {
            "restart_from": "verify",
            "on_conflict": {"tasks": [_sub("resolve")]},
        }
    }


async def _upstream_moves(it) -> str:
    """A commit lands on the repo the worktree was cut from; its sha."""
    (it.repo / "upstream.txt").write_text("landed upstream\n")
    _git(it.repo, "add", "-A")
    _git(it.repo, "commit", "-m", "landed upstream")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=it.repo, capture_output=True, text=True)
    return head.stdout.strip()


async def test_a_conflict_handler_that_rebases_restarts_the_declared_span(item_on, script):
    """`resolved-conflict-restarts-from-base-change-target`."""
    it = await item_on(_chain(**_with_handler()))
    script.plan = {"sync": ["conflict", "done"]}
    upstream = {}

    async def land_upstream(_row):
        if not upstream:
            upstream["sha"] = await _upstream_moves(it)

    async def rebase(_row):
        _git(it.worktree, "rebase", upstream["sha"])

    script.effects = {"check": land_upstream, "resolve": rebase}

    assert await _walk(it) == "completed"
    assert _starts(it) == ["verify", "rebase", "verify", "rebase", "publish"]
    assert it.row()["base_ref"] == upstream["sha"]
    [conflict] = it.events("node_recovery_started")
    assert conflict["payload"]["scope"] == "conflict"
    assert conflict["payload"]["tasks"] == ["rebase.on_base_changed.on_conflict.main.resolve"]
    assert "A rebase conflict stopped this work item" in script.steers["resolve"][0].take()


@pytest.mark.parametrize(
    ("ending", "reason"),
    [
        ("done", "the conflict handler in node rebase finished without rebasing onto"),
        ("failed", "the conflict handler in node rebase could not resolve it: "),
        ("needs_context", "needs_context: "),
    ],
    ids=["did-not-rebase", "failed", "asked"],
)
async def test_a_conflict_handler_that_did_not_resolve_it_stops_for_a_human(
    item_on, script, ending, reason
):
    """A handler reporting success is believed only once the worktree sits on
    the upstream tip; one that failed stops naming the conflict, and one that
    asked stops with its question."""
    it = await item_on(_chain(**_with_handler()))
    script.plan = {"sync": ["conflict"], "resolve": [ending]}
    landed = []

    async def land_upstream(_row):
        if not landed:
            landed.append(await _upstream_moves(it))

    async def record(_row):
        await it.session("s-resolve", "rebase.on_base_changed.on_conflict.main.resolve", ending)

    script.effects = {"check": land_upstream, "resolve": record}

    assert await _walk(it) == "needs_human"
    assert it.events("work_item_needs_human")[-1]["payload"]["reason"].startswith(reason)
    assert not it.events("base_change_restart")


async def test_a_conflict_handler_is_judged_against_the_items_base_branch(
    item_on, script, monkeypatch
):
    """Kraft-v9gbi: a handler's rebase is believed only once the worktree sits
    on the tip of the item's own base branch, not the default's."""
    from kraft import builtins as _builtins
    from kraft.templates.environment import WorkItemTarget

    target = WorkItemTarget.for_repository("target", base_branch="release")
    it = await item_on(_chain(**_with_handler()), target=target)
    script.plan = {"sync": ["conflict"], "resolve": ["done"]}
    asked = []

    async def upstream_head(_repo, branch):
        asked.append(branch)

    async def record(_row):
        await it.session("s-resolve", "rebase.on_base_changed.on_conflict.main.resolve", "done")

    monkeypatch.setattr(_builtins, "upstream_head", upstream_head)
    script.effects = {"resolve": record}

    assert await _walk(it) == "needs_human"
    assert asked == ["release", "release"], "the fork point, then the handler's check"


def _gated(**rebase_fields):
    """`_chain` with an approvable `review` gate between `verify` and `rebase`,
    inside the span a restart from `verify` reruns."""
    verify, rebase, publish = _chain(**rebase_fields)
    return [verify, {"id": "review", "kind": "gate"}, rebase, publish]


async def _approved_review(it):
    await it.database.write(lambda c: store.request_gate(c, it.id, "review", "review"))
    await it.database.write(lambda c: store.approve_gate(c, it.id, "review"))


async def test_a_resolved_conflict_reopens_the_approved_gates_in_its_span(item_on, script):
    """Ruling 162: the `on_conflict` handler changed code no gate saw, so the
    restart reopens the approved `review` and the walk stops there again. A
    clean moved base keeps the approval
    (test_default_chain.py::test_a_rebase_in_post_draft_feedback_retests_and_rereviews_the_rebased_head)."""
    it = await item_on(_gated(**_with_handler()), "rebase")
    await _approved_review(it)
    script.plan = {"sync": ["conflict", "done"]}
    upstream = {}

    async def land_upstream(_row):
        if not upstream:
            upstream["sha"] = await _upstream_moves(it)

    async def rebase(_row):
        _git(it.worktree, "rebase", upstream["sha"])

    script.effects = {"sync": land_upstream, "resolve": rebase}

    assert await _walk_from(it, "rebase") == "awaiting_gate"
    assert [e["payload"]["gate"] for e in it.events("gate_reopened")] == ["review"]
    assert it.row()["current_node_id"] == "review"


@pytest.mark.parametrize("door", ["retry", "resume"])
async def test_a_conflict_at_the_door_goes_to_the_nodes_handler(item_on, script, door):
    """Kraft-e7anb: a `/retry` or `/resume` refresh that conflicts is handed to
    the node's `on_conflict` handler the way a task's conflict is inside the
    walk -- and, resolved, reopens the span's approved gates the same way."""
    it = await item_on(_gated(**_with_handler()), "rebase")
    await _approved_review(it)
    await _prepared(it)
    sha = await _upstream_moves(it)

    async def rebase(_row):
        _git(it.worktree, "rebase", sha)

    script.effects = {"resolve": rebase}

    assert await _enter_with_conflict(it, door) == "awaiting_gate"
    assert script.calls[:2] == ["resolve", "check"]
    assert [e["payload"]["gate"] for e in it.events("gate_reopened")] == ["review"]


async def test_a_conflict_at_the_door_with_no_handler_stops_for_a_human(item_on, script):
    """`rebase-conflict-requires-explicit-handler`, at the door: nothing runs,
    the stop names the conflict, and the steer the resume carried waits on the
    row for the next attempt."""
    it = await item_on(_gated(), "rebase")

    assert await _enter_with_conflict(it, "resume", steer="mind the auth") == "needs_human"
    assert script.calls == []
    assert it.events("work_item_needs_human")[-1]["payload"]["reason"] == "rebase failed for x"
    assert it.row()["pending_steer_context"] == "mind the auth"


async def _prepared(it):
    """The worktree cut and its base recorded, as the walk that stopped at the
    door left them."""
    from kraft import builtins

    await builtins.ensure_worktree(
        it.database, it.run_dirs, repo=str(it.repo), work_item_id=it.id, repo_entry=None
    )


def _walk_from(it, node):
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        start_index=[n.id for n in it.chain.chain.nodes].index(node),
        policy=_policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
    )


async def _enter_with_conflict(it, door, steer=None):
    """What `/retry` and `/resume` hand the walk once their refresh conflicted."""
    kwargs = dict(
        policy=_policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
        conflict="rebase failed for x",
    )
    if door == "retry":
        from kraft.templates.forks import ChainPath

        target = ChainPath.parse(store.materialized_chain_of(it.row()), "rebase")
        return await executor.retry(
            it.database, it.run_dirs, work_item_id=it.id, target=target, steer=steer, **kwargs
        )
    return await executor.run_once(
        it.database, it.run_dirs, work_item_id=it.id, steer=steer, **kwargs
    )
