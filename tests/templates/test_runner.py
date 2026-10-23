"""`runner:` is a library setting on a chain, a node or a step; the most specific wins,
and whatever a node owns (handlers, fix loop, judge, escalation, a gate's reviewer) follows it."""

import pytest
from pydantic import ValidationError
from support.chain_builders import v1_node, v1_task

from kraft.templates.models import Chain, ResolvedChain


def _runners(chain: dict) -> dict[str, str | None]:
    resolved = ResolvedChain.from_chain(Chain.model_validate({"id": "t", **chain}))
    return {t.task.id: t.runner for n in resolved.nodes for t in n.tasks()}


def _agent(id, **kw):
    return v1_task(id, kind="agent", **kw)


def test_the_most_specific_runner_wins_and_a_flat_task_list_is_one_step():
    got = _runners(
        {
            "runner": "any",
            "nodes": [
                v1_node("plain", tasks=[v1_task("a")]),
                v1_node("placed", tasks=[v1_task("b")], runner="gpu"),
                v1_node(
                    "stepped",
                    runner="gpu",
                    steps=[
                        {"id": "s1", "runner": "local", "tasks": [v1_task("c")]},
                        {"id": "s2", "tasks": [v1_task("d")]},
                    ],
                ),
            ],
        }
    )
    assert got == {"a": "any", "b": "gpu", "c": "local", "d": "gpu"}


def test_whatever_a_node_owns_takes_its_placement():
    handler = {"tasks": [_agent("heal")]}
    got = _runners(
        {
            "nodes": [
                v1_node(
                    "build",
                    runner="gpu",
                    tasks=[v1_task("a", on_failure=handler)],
                    on_failure={"tasks": [_agent("node-heal")]},
                    fix_loop={"tasks": [_agent("fix")], "judge": _agent("judge")},
                    escalation=_agent("escalate"),
                ),
                v1_node("review", "gate", runner="box", auto_review=_agent("reviewer")),
            ]
        }
    )
    assert got == {
        "a": "gpu",
        "heal": "gpu",
        "node-heal": "gpu",
        "fix": "gpu",
        "judge": "gpu",
        "escalate": "gpu",
        "reviewer": "box",
    }


@pytest.mark.parametrize(
    ("chain", "message"),
    [
        ({"nodes": [v1_node("n", runner="Bad Name")]}, "runner"),
        (
            {
                "nodes": [
                    v1_node(
                        "n",
                        fix_loop={"steps": [{"id": "s", "runner": "gpu", "tasks": [_agent("f")]}]},
                    )
                ]
            },
            "set runner on the owner",
        ),
        (
            {
                "nodes": [
                    v1_node(
                        "n",
                        on_failure={
                            "steps": [{"id": "s", "runner": "gpu", "tasks": [_agent("f")]}]
                        },
                    )
                ]
            },
            "set runner on the owner",
        ),
        (
            {
                "nodes": [
                    v1_node(
                        "n",
                        on_base_changed={
                            "restart_from": "n",
                            "on_conflict": {
                                "steps": [{"id": "s", "runner": "gpu", "tasks": [_agent("f")]}]
                            },
                        },
                    )
                ]
            },
            "set runner on the owner",
        ),
    ],
    ids=[
        "not-a-machine-name",
        "inside-a-fix-loop",
        "inside-a-recovery-plan",
        "inside-a-conflict-handler",
    ],
)
def test_a_runner_that_cannot_be_honoured_is_refused(chain, message):
    with pytest.raises(ValidationError, match=message):
        Chain.model_validate({"id": "t", **chain})
