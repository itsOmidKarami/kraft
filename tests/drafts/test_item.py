"""The item chain draft's ops: each applied to the item's own chain through the
chain revision's and the retry override's bounds, `passed` once the item has
moved past where it acts, and a problem named on the op that has one."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from support.harness import v1_node, v1_task

from kraft.drafts import item
from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates.environment import WorkItemTarget
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import Chain, ResolvedChain

LIBRARY = TemplateLibrary.from_mappings(
    {
        "tasks": {"check": {"kind": "subprocess", "command": "just check"}},
        "nodes": {"extra_check": {"kind": "exec", "tasks": [{"id": "run", "extends": "check"}]}},
    },
    (),
    library_path=Path("library.yaml"),
)


def _chain():
    """plan -> build (work: implement, check) -> lint -> review (gate) -> land."""
    implement = v1_task("implement", kind="agent", harness="codex", prompt="do")
    nodes = [
        v1_node("plan", tasks=[v1_task("run")]),
        v1_node(steps=[{"id": "work", "tasks": [implement, v1_task("check")]}]),
        v1_node("lint", tasks=[v1_task("run")]),
        v1_node("review", "gate", artifact="work_brief"),
        v1_node("land", tasks=[v1_task("m", kind="forge")]),
    ]
    maxima = {"allowed_harnesses": ["codex"], "max_attempts": 3}
    instance = InstancePolicy.from_input(InstancePolicyInput.model_validate({"maxima": maxima}))
    return ResolvedChain.from_chain(Chain.model_validate({"id": "c", "nodes": nodes})).materialize(
        target=WorkItemTarget.for_repository("target"), effective_policy=instance
    )


OVERRIDE = {"op": "override", "path": "build.work.implement", "task_config": {"effort": "high"}}
ADD = {"op": "add_node", "after": "build", "node": {"id": "checked", "extends": "extra_check"}}
REMOVE = {"op": "remove_node", "node": "build"}
SKIP = {"op": "skip", "path": "build.work.check"}


def test_each_op_kind_applies_to_the_chain():
    ops = [OVERRIDE, ADD, {"op": "remove_node", "node": "lint"}, SKIP]
    evaluated = item.evaluate(_chain(), None, ops, LIBRARY)

    assert evaluated.problems == []
    nodes = evaluated.chain.chain.nodes
    assert [n.id for n in nodes] == ["plan", "build", "checked", "review", "land"]
    assert nodes[1].steps[0].tasks[0].task.effort == "high"
    assert evaluated.skips == ["build.work.check"]
    assert [op["passed"] for op in evaluated.ops] == [False] * 4


@pytest.mark.parametrize(
    ("op", "passed"),
    [
        (OVERRIDE, [False, False, True, True]),
        (REMOVE, [False, False, True, True]),
        (SKIP, [False, False, True, True]),
        (ADD, [False, False, False, True]),
    ],
    ids=["override", "remove_node", "skip", "add_node"],
)
def test_an_op_is_passed_once_the_item_reaches_its_node(op, passed):
    """Standing on (not started, before, at, after) `build`; an `add_node` may
    still follow the node the item stands on. Run to the end, all are passed."""
    ids = [n.id for n in _chain().chain.nodes]
    parsed = item.OPS.validate_python([op])[0]
    currents = [None, "plan", "build", "lint"]
    assert [item.passed(parsed, ids, current) for current in currents] == passed
    assert item.passed(parsed, ids, "a node no longer in the chain")


def test_an_override_reaches_the_chain_kept_from_before_the_attachment_trim():
    """As `revise` keeps a skip or an add there, so a later attachment change
    that rebuilds from it keeps the override."""
    chain = _chain()
    trimmed = replace(chain, untrimmed=chain.chain.chain)
    evaluated = item.evaluate(trimmed, None, [OVERRIDE], LIBRARY)
    build = next(n for n in evaluated.chain.untrimmed.nodes if n.id == "build")
    assert build.steps[0].tasks[0].effort == "high"


def test_a_passed_op_is_marked_and_not_applied():
    evaluated = item.evaluate(_chain(), "lint", [OVERRIDE], LIBRARY)
    assert evaluated.passed == [0]
    assert evaluated.chain.chain.nodes[1].steps[0].tasks[0].task.effort is None


def test_a_problem_names_its_op_and_the_others_still_apply():
    ops = [
        {"op": "override", "path": "build", "policy": {"max_attempts": 9}},
        {"op": "override", "path": "build.work.implement", "task_config": {"harness": "claude"}},
        {"op": "remove_node", "node": "review"},
        {"op": "skip", "path": "build"},
        ADD,
    ]
    evaluated = item.evaluate(_chain(), "plan", ops, LIBRARY)

    problems = {p["op"]: p["message"] for p in evaluated.problems}
    assert sorted(problems) == [0, 1, 2, 3]
    assert "max_attempts" in problems[0]
    assert "harness" in problems[1]
    assert "is a gate" in problems[2]
    assert "is a node" in problems[3]
    assert "checked" in [n.id for n in evaluated.chain.chain.nodes]
