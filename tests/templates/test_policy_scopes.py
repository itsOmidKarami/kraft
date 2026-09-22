"""Per-scope policy: chain → node → step → task, and what each handler inherits.

`policy-is-layered-by-execution-scope`: a task's effective policy is the
materialized chain's policy with each enclosing scope's own `policy:` applied
in turn, broadest first. Resolution records which scopes enclose each task;
materialization refuses a chain any of whose scopes a broader one refuses, so
an item never starts on a policy it could not run under.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kraft.policy import InstancePolicy, InstancePolicyInput, PolicyError
from kraft.templates.environment import WorkItemTarget
from kraft.templates.models import Chain, MaterializedChain, ResolvedChain


def _instance(**maxima) -> InstancePolicy:
    return InstancePolicy.from_input(InstancePolicyInput.model_validate({"maxima": maxima}))


def _agent(id: str, **kw) -> dict:
    return {"id": id, "kind": "agent", "harness": "codex", "prompt": "do it", **kw}


def _materialize(nodes: list[dict], *, instance: InstancePolicy | None = None, **chain):
    resolved = ResolvedChain.from_chain(Chain.model_validate({"id": "c", "nodes": nodes, **chain}))
    return resolved.materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=instance if instance is not None else _instance(),
    )


def _deny(*names: str) -> dict:
    return {"deny_tools": list(names)}


#: One execution node with every handler and control task V1 has, and a gate
#: with an auto-review task. Each scope adds its own letter to `deny_tools`,
#: which only ever accumulates, so a task's resolved deny list spells out
#: exactly which scopes it inherited.
_EVERY_SCOPE = [
    {
        "id": "build",
        "kind": "exec",
        "policy": _deny("NODE"),
        "steps": [
            {
                "id": "work",
                "policy": _deny("STEP"),
                "tasks": [
                    _agent(
                        "implement",
                        policy=_deny("TASK"),
                        on_failure={"tasks": [_agent("task_repair")]},
                    ),
                    _agent("sibling"),
                ],
                "on_failure": {"tasks": [_agent("step_repair")]},
            }
        ],
        "on_failure": {"tasks": [_agent("node_repair")]},
        "fix_loop": {"tasks": [_agent("fix")], "judge": _agent("judge")},
        "escalation": _agent("escalate"),
        "on_base_changed": {
            "restart_from": "build",
            "on_conflict": {"tasks": [_agent("resolve", policy=_deny("OWN"))]},
        },
    },
    {"id": "review", "kind": "gate", "policy": _deny("GATE"), "auto_review": _agent("reviewer")},
]


@pytest.mark.parametrize(
    ("path", "denied"),
    [
        ("build.work.implement", ("CHAIN", "NODE", "STEP", "TASK")),
        ("build.work.sibling", ("CHAIN", "NODE", "STEP")),
        ("build.work.implement.on_failure.main.task_repair", ("CHAIN", "NODE", "STEP", "TASK")),
        ("build.work.on_failure.main.step_repair", ("CHAIN", "NODE", "STEP")),
        ("build.on_failure.main.node_repair", ("CHAIN", "NODE")),
        ("build.fix_loop.main.fix", ("CHAIN", "NODE")),
        ("build.fix_loop.judge", ("CHAIN", "NODE")),
        ("build.escalation.escalate", ("CHAIN", "NODE")),
        ("build.on_base_changed.on_conflict.main.resolve", ("CHAIN", "NODE", "OWN")),
        ("review.auto_review", ("CHAIN", "GATE")),
    ],
    ids=[
        "task",
        "sibling-without-its-own",
        "task-recovery-sits-in-its-task",
        "step-recovery-sits-in-its-step",
        "node-recovery-sits-in-its-node",
        "fix-loop-sits-in-its-node",
        "judge-sits-in-its-node",
        "escalation-sits-in-its-node",
        "on-conflict-sits-in-its-node",
        "auto-review-sits-in-its-gate",
    ],
)
def test_each_task_resolves_policy_from_the_scopes_it_sits_in(path, denied):
    """Which scope each handler and control task inherits, pinned: a
    recovery plan sits in the task, step or node that declares it; a fix loop,
    its judge, the escalation task and the conflict handler sit in their
    execution node; a gate's reviewer sits in its gate. The stored snapshot
    resolves the same way, because it stores the authored scopes, not the
    answer."""
    materialized = _materialize(_EVERY_SCOPE, policy=_deny("CHAIN"))
    assert materialized.policy_at(path).deny_tools == denied
    restored = MaterializedChain.from_json(materialized.to_json())
    assert restored.policy_at(path).deny_tools == denied


def test_a_narrower_scope_narrows_what_it_inherits():
    materialized = _materialize(
        [
            {
                "id": "build",
                "kind": "exec",
                "policy": {"allowed_tools": ["Read", "Edit", "Bash"]},
                "steps": [
                    {
                        "id": "work",
                        "policy": {"allowed_tools": ["Read", "Edit"]},
                        "tasks": [_agent("implement", policy={"allowed_tools": ["Read"]})],
                    }
                ],
            }
        ]
    )
    assert materialized.policy_at("build").allowed_tools == ("Read", "Edit", "Bash")
    assert materialized.policy_at("build.work").allowed_tools == ("Read", "Edit")
    assert materialized.policy_at("build.work.implement").allowed_tools == ("Read",)


@pytest.mark.parametrize(
    ("node_policy", "step_policy", "task_policy", "refused_at"),
    [
        ({"allowed_tools": ["Bash"]}, None, None, "build"),
        (
            None,
            {"allowed_tools": ["Read"]},
            {"allowed_tools": ["Read", "Edit"]},
            "build.work.x",
        ),
        ({"token_budget": 10}, {"token_budget": 20}, None, "build.work"),
        (
            None,
            {"sandbox": {"kind": "docker", "image": "a"}},
            {"sandbox": {"kind": "docker", "image": "b"}},
            "build.work.x",
        ),
    ],
    ids=[
        "node-widens-chain",
        "task-widens-step",
        "step-raises-node-budget",
        "task-swaps-step-sandbox",
    ],
)
def test_materialization_refuses_a_scope_that_relaxes_what_it_inherits(
    node_policy, step_policy, task_policy, refused_at
):
    """`template-policy-cannot-relax-safety-ceilings`, at every scope: the
    refusal happens when the item is filed and names the scope."""
    step = {
        "id": "work",
        "tasks": [_agent("x", **({"policy": task_policy} if task_policy else {}))],
    }
    if step_policy:
        step["policy"] = step_policy
    node = {"id": "build", "kind": "exec", "steps": [step]}
    if node_policy:
        node["policy"] = node_policy
    with pytest.raises(PolicyError, match=rf"^{refused_at}: "):
        _materialize([node], policy={"allowed_tools": ["Read", "Edit"]})


@pytest.mark.parametrize("field", ["max_attempts", "timeout_minutes"])
@pytest.mark.parametrize(
    "where",
    [
        lambda policy: [
            {
                "id": "n",
                "kind": "exec",
                "steps": [{"id": "s", "policy": policy, "tasks": [_agent("t")]}],
            }
        ],
        lambda policy: [{"id": "n", "kind": "exec", "tasks": [_agent("t", policy=policy)]}],
        lambda policy: [
            {"id": "n", "kind": "exec", "tasks": [_agent("t")]},
            {"id": "g", "kind": "gate", "policy": policy},
        ],
    ],
    ids=["step", "task", "gate"],
)
def test_a_scope_without_a_fix_loop_refuses_the_loop_bounds_at_load(where, field):
    """A value nothing would read is refused rather than accepted
    (Kraft-q55aw): only an execution node has a fix loop to bound."""
    # The location ends at the field itself, not at `policy` -- a scope with no
    # `policy:` at all would refuse the whole block and match a looser pattern.
    with pytest.raises(ValidationError, match=rf"\.policy\.{field}\n"):
        Chain.model_validate({"id": "c", "nodes": where({field: 2})})


def test_an_execution_node_accepts_the_loop_bounds():
    materialized = _materialize(
        [{"id": "n", "kind": "exec", "policy": {"max_attempts": 4}, "tasks": [_agent("t")]}]
    )
    assert materialized.policy_at("n").max_attempts == 4


@pytest.mark.parametrize(
    ("chain_policy", "task_policy"),
    [
        ({"allowed_harnesses": ["claude"]}, None),
        (None, {"allowed_harnesses": ["claude"]}),
    ],
    ids=["chain-scope", "task-scope"],
)
def test_materialization_refuses_an_agent_task_on_a_harness_its_policy_disallows(
    chain_policy, task_policy
):
    """`allowed_harnesses` is checked where the task is bound to its policy,
    so a disallowed profile never launches."""
    task = _agent("t", **({"policy": task_policy} if task_policy else {}))
    with pytest.raises(PolicyError, match=r"^n\.main\.t: .*'codex'") as refused:
        _materialize(
            [{"id": "n", "kind": "exec", "tasks": [task]}],
            **({"policy": chain_policy} if chain_policy else {}),
        )
    assert refused.value.field == "allowed_harnesses"


def test_materialization_refuses_a_fix_loop_past_the_attempts_maximum():
    """A fix loop's own `max_attempts` is template policy in all but name, so
    the administrator maximum bounds it too."""
    node = {
        "id": "n",
        "kind": "exec",
        "tasks": [_agent("t")],
        "fix_loop": {"tasks": [_agent("f")], "max_attempts": 9},
    }
    with pytest.raises(PolicyError, match=r"^n: .*max_attempts"):
        _materialize([node], instance=_instance(max_attempts=5))
    at_the_maximum = _materialize([node], instance=_instance(max_attempts=9))
    assert at_the_maximum.chain.nodes[0].node.fix_loop.max_attempts == 9


def test_policy_at_an_unknown_path_is_an_error():
    materialized = _materialize([{"id": "n", "kind": "exec", "tasks": [_agent("t")]}])
    with pytest.raises(LookupError, match="n.main.nope"):
        materialized.policy_at("n.main.nope")
