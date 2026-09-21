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

from kraft import events, executor, gate_review, store
from kraft import policy as _policy
from kraft.adapters import agent as agent_mod
from kraft.executor import dispatch, gates, stops, walk
from kraft.executor.context import BUDGET, CONFIG_ERROR, LaunchContext
from kraft.templates.models import MaterializedChain

NO_SETUP = LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None)


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
    launch = dataclasses.replace(NO_SETUP, repo_entry={"setup_command": "", "deny_tools": ["Bash"]})

    assert await _dispatch(it, launch) == "done"

    assert {"WebFetch", "Bash"} <= set(_flag(fake_agent.argv()[0], "--disallowed-tools"))


_SANDBOX = {"kind": "docker", "image": "kraft/policy:1"}
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
    entry = {"setup_command": "", "test_command": "true"} | (
        {"sandbox": repo_sandbox} if repo_sandbox is not None else {}
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
        policy=dataclasses.replace(materialized.policy, allowed_harnesses=("claude_review",)),
    )
    it = await item_on(narrowed)

    assert await _dispatch(it) == CONFIG_ERROR

    assert fake_agent.argv() == []
    (session,) = it.sessions("implementation")
    assert "'fake'" in Path(session["log_path"]).read_text()


async def _spend(it, tokens_in: int, tokens_out: int) -> None:
    """A finished session of this item that spent these tokens."""

    def write(c):
        store.create_session(
            c,
            id="spent",
            work_item_id=it.id,
            node_id="implementation",
            hook_point="implementation.main.earlier",
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
    launch, against everything the item has spent so far, input and output.
    The stop names the token cap, not a dollar one."""
    it = await item_on(_node(_agent(policy={"token_budget": 100})))
    await _spend(it, spent - 40, 40)

    status = await _dispatch(it)

    assert bool(fake_agent.argv()) is launched
    assert status == ("done" if launched else BUDGET)
    if not launched:
        await stops.stop_for_budget(it.database, it.id, it.chain.chain.nodes[0], _policy.NO_BUDGET)
        (stopped,) = it.events("work_item_needs_human")
        assert "100 tokens spent" in stopped["payload"]["reason"], stopped
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
        launch=executor.LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
    )

    counter = it.database.read(lambda c: store.read_counter(c, it.id, "implementation.fix_loop"))
    assert (counter["cap_attempts"], counter["cap_wall_s"]) == (1, 420)


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
    await _spend(it, 30, 20)
    await _requested(it)

    status = await gates.review_gates("awaiting_gate", it.database, it.run_dirs, work_item_id=it.id)

    assert status == "awaiting_gate"
    assert [e["payload"]["reason"] for e in it.events("gate_auto_review_skipped")] == ["budget"]
