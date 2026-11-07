"""A `group` node is made only by chain revision: a chain file may not declare
one, a stored chain may hold one, and its body and reads are checked."""

import pytest
from pydantic import ValidationError
from support.chain_builders import exec_node, sub

from kraft.templates import models as tm
from kraft.templates.models import STORED_SNAPSHOT


def group_node(*body: dict, **kw) -> dict:
    return {
        "id": "slices",
        "kind": "group",
        "nodes": list(body) or [exec_node("implementation", tasks=[sub()])],
        **kw,
    }


def test_a_chain_file_cannot_declare_a_group():
    with pytest.raises(ValidationError, match="made only by chain revision"):
        tm.Chain.model_validate({"id": "c", "nodes": [group_node()]})


def test_a_stored_chain_may_hold_a_group():
    chain = tm.Chain.model_validate(
        {
            "id": "c",
            "nodes": [
                group_node(
                    exec_node("implementation", tasks=[sub("build")]),
                    exec_node("verification", tasks=[sub("check")]),
                ),
            ],
        },
        context=STORED_SNAPSHOT,
    )
    resolved = tm.ResolvedChain.from_chain(chain)
    (node,) = resolved.nodes
    assert [b.id for b in node.body] == ["slices.implementation", "slices.verification"]
    assert set(resolved.task_paths) == {
        "slices.implementation.main.build",
        "slices.verification.main.check",
    }


@pytest.mark.parametrize(
    ("body", "group", "says"),
    [
        pytest.param(
            exec_node("implementation", tasks=[{"id": "m", "kind": "forge", "target": "mr.merge"}]),
            {},
            "forge task",
            id="forge-task",
        ),
        pytest.param(
            exec_node(
                "implementation",
                tasks=[sub()],
                on_merge_rejected={"restart_from": "implementation", "repair": {"tasks": [sub()]}},
            ),
            {},
            "on_merge_rejected",
            id="on-merge-rejected",
        ),
        pytest.param(
            exec_node(
                "implementation",
                tasks=[sub()],
                on_base_changed={"restart_from": "implementation"},
            ),
            {},
            "on_base_changed",
            id="on-base-changed",
        ),
        pytest.param(
            exec_node("implementation", tasks=[sub()]),
            {"policy": {"max_attempts": 2}},
            "policy",
            id="group-caps",
        ),
        pytest.param(
            exec_node("implementation", tasks=[sub()]),
            {"on_conflict": {"tasks": [sub()]}},
            "on_conflict",
            id="join-conflict-handler",
        ),
    ],
)
def test_a_group_body_refuses(body, group, says):
    with pytest.raises(ValidationError, match=says):
        tm.Chain.model_validate(
            {"id": "c", "nodes": [group_node(body, **group)]}, context=STORED_SNAPSHOT
        )


_OUTPUTS = {"outputs": {"variables": ["X"]}}


@pytest.mark.parametrize(
    "nodes",
    [
        pytest.param(
            [
                exec_node("before", tasks=[sub("check", **_OUTPUTS)]),
                group_node(exec_node("a", tasks=[sub("use", reads=["before.main.check"])])),
            ],
            id="into-the-body",
        ),
        pytest.param(
            [
                group_node(exec_node("a", tasks=[sub("build", **_OUTPUTS)])),
                exec_node("after", tasks=[sub("use", reads=["slices.a.main.build"])]),
            ],
            id="out-of-the-body",
        ),
    ],
)
def test_reads_may_not_cross_a_group_boundary(nodes):
    with pytest.raises(ValidationError, match=r"reads"):
        tm.Chain.model_validate({"id": "c", "nodes": nodes}, context=STORED_SNAPSHOT)


def test_reads_between_body_nodes_are_accepted():
    outputs = {"outputs": {"variables": ["X"]}}
    chain = tm.Chain.model_validate(
        {
            "id": "c",
            "nodes": [
                group_node(
                    exec_node("a", tasks=[sub("build", **outputs)]),
                    exec_node("b", tasks=[sub("use", reads=["a.main.build"])]),
                )
            ],
        },
        context=STORED_SNAPSHOT,
    )
    assert [n.id for n in chain.nodes] == ["slices"]
