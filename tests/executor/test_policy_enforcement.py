"""Policy at runtime (Kraft-q55aw): every field validation accepts reaches the
code that enforces it, resolved at the scope the task sits in.

- `allowed_tools`/`deny_tools` reach the launch (`--allowedTools`,
  `--disallowed-tools`); the permission gate is tests/test_permissions.py.
- `sandbox` wraps the task's process.
- `allowed_harnesses` stops a launch on a profile its policy disallows.
- `token_budget` stops the next agent launch once the item has spent it.
- `max_attempts`/`timeout_minutes` bound the node's fix loop.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from support.harness import entry_of

from kraft import escalate, events, gate_review, store
from kraft import policy as _policy
from kraft.adapters import agent as agent_mod
from kraft.executor import dispatch, gates, stops, walk
from kraft.executor.context import BUDGET, CONFIG_ERROR, LaunchContext
from kraft.templates.models import MaterializedChain, ResolvedChain

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}))
TESTED = dataclasses.replace(
    NO_SETUP, repo_entry=entry_of({"setup_command": "", "test_command": "true"})
)


def _agent(task_id="implement", **fields):
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


def _node(*tasks, **fields):
    return [{"id": "implementation", "kind": "exec", "tasks": list(tasks), **fields}]


async def _dispatch(it, launch=NO_SETUP):
    node = it.chain.chain.nodes[0]
    return await dispatch.dispatch_node(
        it.database, it.run_dirs, node.steps[0].tasks[0], node, it.row(), it.repo, launch=launch
    )


def _flag(argv: list[str], flag: str) -> list[str]:
    return argv[argv.index(flag) + 1].split(",") if flag in argv else []


async def test_a_tasks_resolved_tool_policy_reaches_its_launch(item_on, fake_agent):
    """The allowlist is the task's own, narrowed from its node's; the deny
    list is its node's, accumulated. Both on the wire, not only in the
    snapshot."""
    it = await item_on(
        _node(
            _agent(policy={"allowed_tools": ["Read"]}),
            policy={"allowed_tools": ["Read", "Edit"], "deny_tools": ["WebFetch"]},
        )
    )

    assert await _dispatch(it) == "done"

    argv = fake_agent.argv()[0]
    assert _flag(argv, "--allowedTools") == ["Read"]
    assert "WebFetch" in _flag(argv, "--disallowed-tools")


async def test_a_live_repository_deny_list_still_applies_on_top_of_the_frozen_one(
    item_on, fake_agent
):
    """The frozen policy is a floor, not a replacement: a tool the repository
    denies after the item was filed is denied too -- only tightening can
    come from the live entry."""
    it = await item_on(_node(_agent(), policy={"deny_tools": ["WebFetch"]}))
    launch = dataclasses.replace(
        NO_SETUP, repo_entry=entry_of({"setup_command": "", "deny_tools": ["Bash"]})
    )

    assert await _dispatch(it, launch) == "done"

    assert {"WebFetch", "Bash"} <= set(_flag(fake_agent.argv()[0], "--disallowed-tools"))


_SANDBOX = {"kind": "docker", "image": "kraft/policy:1"}
_OTHER = {"kind": "docker", "image": "kraft/other:2"}
_BUILTIN = {"id": "run", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}


@pytest.mark.parametrize(
    ("task", "repo_sandbox"),
    [
        ({"id": "run", "kind": "subprocess", "command": "true"}, None),
        ({"id": "run", "kind": "subprocess", "command": "true"}, False),
        (_agent("run"), None),
        (_agent("run"), False),
        (_BUILTIN, False),
    ],
    ids=["subprocess", "subprocess-repo-says-off", "agent", "agent-repo-says-off", "builtin"],
)
async def test_a_policy_sandbox_wraps_the_task_and_the_repository_cannot_turn_it_off(
    item_on, monkeypatch, fake_agent, task, repo_sandbox
):
    """Ruling 105: `sandbox` is permission-shaped, so once a layer sets it the
    live entry's `sandbox: false` (legacy "turn it off") cannot undo it."""
    seen = {}

    async def run_task(*_a, sandbox=None, **_kw):
        seen["sandbox"] = sandbox
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", run_task)
    it = await item_on(_node(task, policy={"sandbox": _SANDBOX}))
    entry = entry_of(
        {"setup_command": "", "test_command": "true"}
        | ({"sandbox": repo_sandbox} if repo_sandbox is not None else {})
    )

    await _dispatch(it, dataclasses.replace(NO_SETUP, repo_entry=entry))

    assert seen["sandbox"] == _SANDBOX


async def test_a_harness_its_policy_disallows_never_launches(item_on, fake_agent):
    """Materialization refuses this chain; a snapshot that reached the row
    some other way (an older build, a hand-edited row) is refused again at the
    launch, rather than trusted."""
    materialized = (await item_on(_node(_agent()), wid="source")).chain
    narrowed = MaterializedChain(
        chain=materialized.chain,
        target=materialized.target,
        policy=dataclasses.replace(materialized.policy, allowed_harnesses=("claude",)),
    )
    it = await item_on(narrowed)

    assert await _dispatch(it) == CONFIG_ERROR

    assert fake_agent.argv() == []
    (session,) = it.sessions("implementation")
    assert "'fake'" in Path(session["log_path"]).read_text()


@pytest.mark.parametrize("field", ["allowed_tools", "deny_tools"])
async def test_a_rule_frozen_into_a_snapshot_stops_its_launch_naming_the_field(
    item_on, fake_agent, field
):
    """Kraft-9ct4q: a snapshot frozen before rule syntax was refused still
    reads, but a rule never reaches the agent. The launch is refused and
    its log names the field, rather than the rule running unbounded or being
    silently dropped."""
    from test_policy_tool_names import with_a_frozen_rule

    source = (await item_on(_node(_agent()), wid="source")).chain
    it = await item_on(MaterializedChain.from_json(with_a_frozen_rule(source.to_json(), field)))

    assert await _dispatch(it) == CONFIG_ERROR

    assert fake_agent.argv() == []
    (session,) = it.sessions("implementation")
    assert f"{field}: 'Bash(git *)' is a permission rule" in Path(session["log_path"]).read_text()


async def _spend(
    it, tokens_in: int, tokens_out: int, path: str = "implementation.main.earlier"
) -> None:
    """A finished session of this item, at `path`, that spent these tokens."""

    def write(c):
        store.create_session(
            c,
            id="spent",
            work_item_id=it.id,
            node_id=path.split(".")[0],
            hook_point=path,
            log_path="/dev/null",
            result_path="/dev/null",
        )
        c.execute(
            "UPDATE worker_sessions SET tokens_in = ?, tokens_out = ? WHERE id = 'spent'",
            (tokens_in, tokens_out),
        )

    await it.database.write(write)


@pytest.mark.parametrize(("spent", "launched"), [(99, True), (100, False)], ids=["under", "at"])
async def test_token_budget_refuses_the_next_agent_launch(item_on, fake_agent, spent, launched):
    """`token_budget` is enforced where the dollar budget is: before an agent
    launch, against everything the launches inside its scope -- the node here
    -- have spent so far, input and output (Ruling 195). The stop names the
    token cap and its scope, not a dollar one."""
    it = await item_on(_node(_agent(), policy={"token_budget": 100}))
    await _spend(it, spent - 40, 40)

    status = await _dispatch(it)

    assert bool(fake_agent.argv()) is launched
    assert status == ("done" if launched else BUDGET)
    if not launched:
        await stops.stop_for_budget(it.database, it.id, it.chain.chain.nodes[0], _policy.NO_BUDGET)
        (stopped,) = it.events("work_item_needs_human")
        assert "100 tokens spent in `implementation`" in stopped["payload"]["reason"], stopped
        assert "cap 100 tokens" in stopped["payload"]["reason"], stopped


def _cap(policy_fields=None, loop=None, override=None, loops=None):
    instance = _policy.Policy(loops=loops or {}, default=_policy.Cap(3, 3600))
    node_policy = dataclasses.replace(
        _policy.InstancePolicy.from_input(_policy.InstancePolicyInput()), **(policy_fields or {})
    )
    return walk.fix_loop_cap(instance, "n.fix_loop", node_policy, loop, override)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, (3, 3600)),
        ({"policy_fields": {"max_attempts": 5, "timeout_minutes": 10}}, (5, 600)),
        ({"policy_fields": {"max_attempts": 5}, "loop": 2}, (2, 3600)),
        ({"policy_fields": {"max_attempts": 5}, "loop": 2, "override": {"attempts": 7}}, (7, 3600)),
        (
            {"policy_fields": {"timeout_minutes": 10}, "override": {"wall_clock_s": 60}},
            (3, 60),
        ),
        ({"loops": {"n.fix_loop": _policy.Cap(4, 100)}}, (4, 100)),
        (
            {"loops": {"n.fix_loop": _policy.Cap(4, 100)}, "policy_fields": {"max_attempts": 6}},
            (6, 100),
        ),
    ],
    ids=[
        "policy-yaml-default",
        "v1-policy-replaces-it",
        "the-loops-own-max-attempts-wins-over-policy",
        "an-operator-node-override-wins-over-both",
        "an-operator-wall-clock-override-wins",
        "a-keyed-loops-cap",
        "v1-policy-over-a-keyed-cap",
    ],
)
def test_a_fix_loops_bounds_come_from_its_nodes_policy(kwargs, expected):
    """`max_attempts`/`timeout_minutes` resolved at the node scope bound its
    fix loop, between `policy.yaml`'s `loops:`/`default:` below and the
    loop's own `max_attempts` and an operator's node override above."""
    cap = _cap(**kwargs)
    assert (cap.attempts, cap.wall_clock_s) == expected


async def test_the_fix_loop_counts_under_the_bounds_its_node_policy_resolves(item_on, script):
    """Wiring, not precedence: the cap `walk_node` snapshots onto the counter
    is the node-scope one."""
    script.real = {"check"}
    check = {"id": "check", "kind": "subprocess", "command": "false"}
    fix = {"id": "fix", "kind": "subprocess", "command": "true"}
    it = await item_on(
        _node(
            check,
            fix_loop={"tasks": [fix]},
            policy={"max_attempts": 1, "timeout_minutes": 7},
        )
    )

    await walk.walk_node(
        it.database,
        it.run_dirs,
        it.id,
        it.chain.chain.nodes[0],
        it.row(),
        it.repo,
        policy=_policy.Policy(loops={}, default=_policy.Cap(9, 3600)),
        launch=NO_SETUP,
    )

    counter = it.database.read(lambda c: store.read_counter(c, it.id, "implementation.fix_loop"))
    assert (counter["cap_attempts"], counter["cap_wall_s"]) == (1, 420)


async def _set_item_policy(it, **override):
    item = _policy.WorkItemPolicy.model_validate(override)
    await it.database.write(lambda c: store.set_policy_override(c, it.id, item))


async def test_an_items_own_node_cap_binds_that_nodes_fix_loop(item_on, script):
    """Kraft-ab1bh: a work item's cap for one node wins over the loop's own
    `max_attempts`, the way an operator's per-item value always has. Set
    after the caller read the row -- a PATCH while the item ran -- it still
    binds the node entered next."""
    script.real = {"check"}
    check = {"id": "check", "kind": "subprocess", "command": "false"}
    fix = {"id": "fix", "kind": "subprocess", "command": "true"}
    it = await item_on(_node(check, fix_loop={"tasks": [fix], "max_attempts": 2}))
    stale = it.row()
    await _set_item_policy(it, paths={"implementation": {"max_attempts": 4, "timeout_minutes": 9}})

    await walk.walk_node(
        it.database,
        it.run_dirs,
        it.id,
        it.chain.chain.nodes[0],
        stale,
        it.repo,
        policy=_policy.Policy(loops={}, default=_policy.Cap(9, 3600)),
        launch=NO_SETUP,
    )

    counter = it.database.read(lambda c: store.read_counter(c, it.id, "implementation.fix_loop"))
    assert (counter["cap_attempts"], counter["cap_wall_s"]) == (4, 540)


async def test_a_node_entered_after_a_policy_change_launches_under_it(item_on, fake_agent):
    """A PATCH to a running item applies from the next node it enters: every
    task that node dispatches resolves its policy from the row as it is at
    the node's entry, not as the run first read it."""
    it = await item_on(_node(_agent()))
    stale = it.row()
    await _set_item_policy(it, allowed_tools=["Read"])

    await walk.walk_node(
        it.database, it.run_dirs, it.id, it.chain.chain.nodes[0], stale, it.repo, launch=NO_SETUP
    )

    assert _flag(fake_agent.argv()[0], "--allowedTools") == ["Read"]


# -- a gate's reviewer sits in its gate's scope ------------------------------------


def _reviewed_gate(policy: dict) -> list[dict]:
    return [
        {
            "id": "spec",
            "kind": "exec",
            "tasks": [{"id": "w", "kind": "subprocess", "command": "true"}],
        },
        {
            "id": "spec_approval",
            "kind": "gate",
            "artifact": "spec",
            "policy": policy,
            "auto_review": _agent("reviewer"),
        },
    ]


async def _requested(it):
    await it.database.write(
        lambda c: events.append(
            c, it.id, "gate_requested", {"gate": "spec_approval", "node_id": "spec_approval"}
        )
    )


async def test_a_gate_reviewer_launches_under_its_gates_policy(item_on, fake_agent, monkeypatch):
    it = await item_on(
        _reviewed_gate({"allowed_tools": ["Read"], "deny_tools": ["Bash"]}), auto_gate=True
    )
    seen = {}

    async def launch(_db, _rd, **kw):
        seen.update(kw)
        return "done"

    monkeypatch.setattr(agent_mod, "run_agent_task", launch)
    await _requested(it)

    await gate_review.review(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate="spec_approval",
        node=it.chain.chain.nodes[1],
        launch=NO_SETUP,
    )

    assert seen["allowed_tools"] == ("Read",)
    assert "Bash" in seen["deny_tools"]


async def test_a_gate_reviewer_is_not_launched_past_its_token_budget(item_on, monkeypatch):
    def never(*_a, **_kw):
        raise AssertionError("the reviewer launched past its token budget")

    monkeypatch.setattr(gates.gate_review, "review", never)
    it = await item_on(_reviewed_gate({"token_budget": 50}), auto_gate=True)
    # The gate's own spend: its reviewer's earlier launches (Ruling 195).
    await _spend(it, 30, 20, path="spec_approval.auto_review")
    await _requested(it)

    status = await gates.review_gates("awaiting_gate", it.database, it.run_dirs, work_item_id=it.id)

    assert status == "awaiting_gate"
    assert [e["payload"]["reason"] for e in it.events("gate_auto_review_skipped")] == ["budget"]


# -- every agent launch resolves its policy from the snapshot (Kraft-l8ype) ------------


def _capture(monkeypatch) -> dict:
    """`run_agent_task`'s kwargs for the one launch the test makes; empty if
    nothing launched."""
    seen: dict = {}

    async def launch(_db, _rd, **kw):
        seen.update(kw)
        return "done"

    monkeypatch.setattr(agent_mod, "run_agent_task", launch)
    return seen


async def _escalate(it, auto):
    return await escalate.dispatch(
        it.database, it.run_dirs, work_item_id=it.id, message="help", launch=NO_SETUP, auto=auto
    )


_BOUNDED = {"allowed_tools": ["Read"], "deny_tools": ["WebFetch"], "sandbox": _SANDBOX}


@pytest.mark.parametrize("auto", [False, True], ids=["manual", "auto"])
async def test_an_escalation_turn_launches_under_its_nodes_policy(
    item_on, fake_agent, monkeypatch, auto
):
    """Decision on Kraft-l8ype: an escalation turn is node-scoped
    (`stuck-escalation-is-an-exec-node-control`), so it runs under the policy
    of the node its item stopped at -- the node's tool lists and frozen
    sandbox, not only the repository entry's live ones."""
    it = await item_on(_node(_agent(), policy=_BOUNDED), "implementation")
    seen = _capture(monkeypatch)

    await _escalate(it, auto)

    assert seen["allowed_tools"] == ("Read",)
    assert "WebFetch" in seen["deny_tools"]
    assert seen["sandbox"] == _SANDBOX


@pytest.mark.parametrize(
    ("policy", "spent", "names"),
    [
        ({"token_budget": 50}, 50, "token budget"),
        ({"allowed_harnesses": ["fake"]}, 0, "allowed_harnesses"),
    ],
    ids=["token-budget-spent", "harness-outside-allowed-harnesses"],
)
async def test_an_escalation_turn_its_nodes_policy_refuses_never_launches(
    item_on, fake_agent, monkeypatch, policy, spent, names
):
    it = await item_on(_node(_agent(), policy=policy), "implementation")
    await _spend(it, spent, 0)
    seen = _capture(monkeypatch)

    assert await _escalate(it, auto=False) == CONFIG_ERROR

    assert seen == {}
    (session,) = [s for s in it.sessions("implementation") if s["hook_point"] == "escalation"]
    assert names in Path(session["log_path"]).read_text()


async def test_an_escalation_turn_on_an_item_with_no_snapshot_never_launches(
    item_on, fake_agent, monkeypatch
):
    """No snapshot, no policy anyone could know: refused, never unbounded."""
    it = await item_on(_node(_agent(policy=_BOUNDED)), "implementation")
    await it.database.write(
        lambda c: c.execute(
            "UPDATE work_items SET materialized_chain = NULL WHERE id = ?", (it.id,)
        )
    )
    seen = _capture(monkeypatch)

    assert await _escalate(it, auto=False) == CONFIG_ERROR
    assert seen == {}


async def test_a_gate_reviewer_on_a_harness_its_policy_disallows_never_launches(
    item_on, fake_agent, monkeypatch
):
    """Like `test_a_harness_its_policy_disallows_never_launches`: a snapshot
    materialization would refuse is held to its policy again at the launch,
    and the refusal reaches `deps.guard` as an unavailable harness does."""
    materialized = (await item_on(_reviewed_gate({}), wid="source")).chain
    narrowed = dataclasses.replace(
        materialized,
        policy=dataclasses.replace(materialized.policy, allowed_harnesses=("claude",)),
    )
    it = await item_on(narrowed, auto_gate=True)
    seen = _capture(monkeypatch)
    await _requested(it)

    with pytest.raises(agent_mod.HarnessUnavailable, match="allowed_harnesses"):
        await gate_review.review(
            it.database,
            it.run_dirs,
            work_item_id=it.id,
            gate="spec_approval",
            node=it.chain.chain.nodes[1],
            launch=NO_SETUP,
        )
    assert seen == {}


def _fixing_node(where: str) -> list[dict]:
    """A node allowing `Read`, whose recovery or judge narrows to nothing."""
    narrow = _agent(where, policy={"allowed_tools": []})
    failing = {"id": "check", "kind": "subprocess", "command": "false"}
    fields = (
        {"on_failure": {"tasks": [narrow]}}
        if where == "repair"
        else {"fix_loop": {"tasks": [_agent("fix")], "judge": narrow}}
    )
    return _node(failing, policy={"allowed_tools": ["Read"]}, **fields)


@pytest.mark.parametrize("where", ["repair", "judge"])
async def test_a_recovery_and_a_judge_launch_under_their_own_scopes_policy(
    item_on, fake_agent, where
):
    """Both launch through `dispatch_node`, so each resolves at its own scope:
    the handler's narrowing reaches its argv, not the node's allowlist."""
    it = await item_on(_fixing_node(where), "implementation")
    node = it.chain.chain.nodes[0]
    common = dict(launch=NO_SETUP, budget=_policy.NO_BUDGET)
    if where == "repair":
        await dispatch.run_recovery(
            it.database,
            it.run_dirs,
            it.id,
            node,
            it.row(),
            it.repo,
            handler=node.on_failure,
            scope="node",
            failed=[node.steps[0].tasks[0]],
            note="it failed",
            steer=None,
            measured_round=0,
            round=1,
            **common,
        )
    else:
        await dispatch.judge_verdict(
            it.database,
            it.run_dirs,
            it.id,
            node,
            it.row(),
            it.repo,
            round=1,
            key="implementation.fix_loop",
            cap=_policy.Cap(3, 3600),
            policy=_policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
            **common,
        )

    (argv,) = fake_agent.argv()
    assert argv[argv.index("--tools") + 1] == ""
    assert "--allowedTools" not in argv


# -- a sandbox wraps the whole item (Ruling 189, Kraft-h10e5) ---------------------------


def _sandboxed_elsewhere(sandbox: dict | None) -> list[dict]:
    """Four launches, one of which (`first`) may set `sandbox`; none of the
    others sets one of its own."""
    first = _agent("first", **({"policy": {"sandbox": sandbox}} if sandbox else {}))
    return [
        {"id": "a", "kind": "exec", "tasks": [first]},
        {
            "id": "b",
            "kind": "exec",
            "tasks": [{"id": "s", "kind": "subprocess", "command": "true"}],
        },
        {"id": "c", "kind": "exec", "tasks": [_BUILTIN]},
        {"id": "d", "kind": "exec", "tasks": [_agent("later")]},
    ]


@pytest.mark.parametrize(("sandbox", "expected"), [(_SANDBOX, _SANDBOX), (None, None)])
async def test_a_sandbox_on_one_task_wraps_every_launch_of_the_item(
    item_on, monkeypatch, fake_agent, sandbox, expected
):
    """Ruling 189: once one task ran sandboxed the worktree is untrusted, so
    the item's other tasks -- subprocess, builtin and agent alike -- launch in
    that sandbox too. A chain with no sandbox anywhere is unchanged."""
    launched = []

    async def run_task(*_a, hook_point, sandbox=None, **_kw):
        launched.append((hook_point, sandbox))
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", run_task)
    it = await item_on(_sandboxed_elsewhere(sandbox))
    launch = TESTED

    for node in it.chain.chain.nodes:
        await dispatch.dispatch_node(
            it.database, it.run_dirs, node.steps[0].tasks[0], node, it.row(), it.repo, launch=launch
        )

    assert launched == [
        ("a.main.first", expected),
        ("b.main.s", expected),
        ("c.main.run", expected),
        ("d.main.later", expected),
    ]


@pytest.mark.parametrize("auto", [False, True], ids=["manual", "auto"])
async def test_an_escalation_turn_runs_in_a_sandbox_another_node_set(
    item_on, fake_agent, monkeypatch, auto
):
    it = await item_on(
        [
            {"id": "first", "kind": "exec", "tasks": [_agent(policy={"sandbox": _SANDBOX})]},
            *_node(_agent()),
        ],
        "implementation",
    )
    seen = _capture(monkeypatch)

    await _escalate(it, auto)

    assert seen["sandbox"] == _SANDBOX


async def test_a_gate_reviewer_runs_in_a_sandbox_another_node_set(item_on, fake_agent, monkeypatch):
    chain = _reviewed_gate({})
    chain[0]["tasks"][0]["policy"] = {"sandbox": _SANDBOX}
    it = await item_on(chain, auto_gate=True)
    seen = _capture(monkeypatch)
    await _requested(it)

    await gate_review.review(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate="spec_approval",
        node=it.chain.chain.nodes[1],
        launch=NO_SETUP,
    )

    assert seen["sandbox"] == _SANDBOX


@pytest.mark.parametrize(
    ("where", "names"),
    [
        (lambda c: c[3]["tasks"][0].update(policy={"sandbox": _OTHER}), "d.main.later"),
        (lambda c: c[1].update(policy={"sandbox": _OTHER}), "b"),
    ],
    ids=["another-task", "another-node"],
)
async def test_two_scopes_asking_for_different_sandboxes_are_refused_at_build(
    item_on, where, names
):
    chain = _sandboxed_elsewhere(_SANDBOX)
    where(chain)

    with pytest.raises(_policy.PolicyError, match="Ruling 189") as refused:
        await item_on(chain)

    assert refused.value.field == "sandbox"
    assert "a.main.first" in str(refused.value) and repr(_OTHER) in str(refused.value)
    assert f"{names} in" in str(refused.value)


async def test_two_scopes_asking_for_the_same_sandbox_build(item_on):
    chain = _sandboxed_elsewhere(_SANDBOX)
    chain[3]["tasks"][0]["policy"] = {"sandbox": _SANDBOX}

    it = await item_on(chain)

    assert it.chain.item_sandbox() == _policy.SandboxPolicy(**_SANDBOX)


async def test_an_item_filed_with_two_sandboxes_stops_rather_than_pick_one(item_on, monkeypatch):
    """A snapshot from before Ruling 189 can carry two; it is built past the
    refusal here the way such a row reaches the walk."""
    monkeypatch.setattr(ResolvedChain, "check_scopes", lambda *_a: None)
    chain = _sandboxed_elsewhere(_SANDBOX)
    chain[3]["tasks"][0]["policy"] = {"sandbox": _OTHER}
    it = await item_on(chain)
    monkeypatch.undo()

    with pytest.raises(RuntimeError, match="different sandboxes"):
        dispatch.item_sandbox(it.row(), NO_SETUP)


async def test_a_work_items_own_sandbox_wraps_the_whole_item(item_on):
    """#111's item override is a scope like any other (Ruling 189)."""
    it = await item_on(_sandboxed_elsewhere(None))

    item = it.chain.with_item_policy({"paths": {"d.main.later": {"sandbox": _SANDBOX}}})

    assert item.item_sandbox() == _policy.SandboxPolicy(**_SANDBOX)


async def test_a_work_items_sandbox_that_conflicts_with_the_chains_is_refused(item_on):
    it = await item_on(_sandboxed_elsewhere(_SANDBOX))

    with pytest.raises(_policy.PolicyError, match="Ruling 189"):
        it.chain.with_item_policy({"paths": {"d.main.later": {"sandbox": _OTHER}}})
