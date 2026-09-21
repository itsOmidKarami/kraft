"""The typed failure controls an execution node declares: recovery plans at
task, step and node scope, and `on_base_changed` with its explicit conflict
handler. What they *do* is `tests/executor/test_recovery.py` and
`tests/executor/test_base_change.py`; this is their shape and validation."""

import pytest
import yaml
from pydantic import ValidationError

from kraft.templates import models as tm
from kraft.templates.library import TemplateLibrary


def sub(id: str = "t", **kw) -> dict:
    return {"id": id, "kind": "subprocess", "command": "true", **kw}


def plan(*tasks) -> dict:
    return {"tasks": list(tasks)}


def exec_node(id: str = "build", **kw) -> dict:
    return {"id": id, "kind": "exec", **kw}


NESTED = plan(sub("inner"))


@pytest.mark.parametrize(
    "node",
    [
        exec_node(tasks=[sub()], on_failure=plan(sub("r", on_failure=NESTED))),
        exec_node(
            tasks=[sub()],
            on_failure={"steps": [{"id": "s", "tasks": [sub()], "on_failure": NESTED}]},
        ),
        exec_node(tasks=[sub()], fix_loop=plan(sub("fix", on_failure=NESTED))),
        exec_node(
            tasks=[sub()], fix_loop={**plan(sub("fix")), "judge": sub("j", on_failure=NESTED)}
        ),
        exec_node(tasks=[sub()], escalation=sub("esc", on_failure=NESTED)),
        exec_node(
            tasks=[sub()],
            on_base_changed={
                "restart_from": "build",
                "on_conflict": plan(sub("c", on_failure=NESTED)),
            },
        ),
    ],
    ids=["recovery-task", "recovery-step", "fix-loop-task", "judge", "escalation", "on-conflict"],
)
def test_a_handler_cannot_nest_inside_another_control(node):
    """`recovery-plan-supports-task-groups-or-steps`: no nested recovery
    handlers. A handler belongs only on the node's own steps and tasks."""
    with pytest.raises(ValidationError, match="cannot declare its own on_failure"):
        tm.ExecNode.model_validate(node)


def test_a_gate_reviewer_cannot_declare_a_handler():
    with pytest.raises(ValidationError, match="cannot declare its own on_failure"):
        tm.GateNode.model_validate(
            {
                "id": "g",
                "kind": "gate",
                "auto_review": {
                    "id": "r",
                    "kind": "agent",
                    "harness": "h",
                    "prompt": "p",
                    "on_failure": NESTED,
                },
            }
        )


def test_a_recovery_plan_is_one_shape_with_no_gate_or_fix_loop():
    with pytest.raises(ValidationError, match="exactly one of 'tasks' or 'steps'"):
        tm.RecoveryPlan.model_validate({**plan(sub()), "steps": [{"id": "s", "tasks": [sub()]}]})
    with pytest.raises(ValidationError, match="Extra inputs"):
        tm.RecoveryPlan.model_validate({**plan(sub()), "fix_loop": plan(sub())})


def test_task_step_and_node_handlers_resolve_under_their_owners_paths():
    chain = tm.Chain.model_validate(
        {
            "nodes": [
                exec_node(
                    steps=[
                        {
                            "id": "check",
                            "tasks": [sub("lint", on_failure=plan(sub("fmt"))), sub("test")],
                            "on_failure": plan(sub("reset")),
                        }
                    ],
                    on_failure=plan(sub("clean")),
                    on_base_changed={"restart_from": "build", "on_conflict": plan(sub("resolve"))},
                )
            ]
        }
    )
    node = tm.ResolvedChain.from_chain(chain).nodes[0]
    step = node.steps[0]
    assert [t.path for s in step.tasks[0].on_failure for t in s.tasks] == [
        "build.check.lint.on_failure.main.fmt"
    ]
    assert step.tasks[1].on_failure == ()
    assert [t.path for s in step.on_failure for t in s.tasks] == [
        "build.check.on_failure.main.reset"
    ]
    assert [t.path for s in node.on_conflict for t in s.tasks] == [
        "build.on_base_changed.on_conflict.main.resolve"
    ]
    assert set(tm.ResolvedChain.from_chain(chain).task_paths) >= {
        "build.check.lint.on_failure.main.fmt",
        "build.check.on_failure.main.reset",
        "build.on_failure.main.clean",
        "build.on_base_changed.on_conflict.main.resolve",
    }


def _chain(restart_from: str) -> dict:
    return {
        "id": "c",
        "nodes": [
            exec_node("early", tasks=[sub()]),
            {"id": "gate", "kind": "gate"},
            exec_node("rebase", tasks=[sub()], on_base_changed={"restart_from": restart_from}),
            exec_node("late", tasks=[sub()]),
        ],
    }


@pytest.mark.parametrize("target", ["nowhere", "gate", "late"], ids=["missing", "gate", "later"])
def test_a_restart_target_must_be_this_or_an_earlier_execution_node(target):
    """`base-change-restart-target-is-backward`."""
    with pytest.raises(
        ValidationError, match=f"restart_from {target!r} must name this or an earlier"
    ):
        tm.Chain.model_validate(_chain(target))


@pytest.mark.parametrize("target", ["early", "rebase"], ids=["earlier", "itself"])
def test_a_backward_restart_target_is_accepted(target):
    chain = tm.Chain.model_validate(_chain(target))
    assert chain.nodes[2].on_base_changed.restart_from == target


def test_library_components_extend_inside_step_task_and_conflict_handlers(tmp_path):
    """Every handler position expands `extends` like any other task position."""
    (tmp_path / "chains").mkdir()
    (tmp_path / "library.yaml").write_text(
        yaml.safe_dump({"tasks": {"fixer": {"kind": "subprocess", "command": "make fix"}}})
    )
    fix = {"tasks": [{"id": "fix", "extends": "fixer"}]}
    (tmp_path / "chains" / "default.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "default",
                "nodes": [
                    exec_node(
                        steps=[{"id": "s", "tasks": [sub("t", on_failure=fix)], "on_failure": fix}],
                        on_base_changed={"restart_from": "build", "on_conflict": fix},
                    )
                ],
            }
        )
    )
    node = TemplateLibrary.from_yaml_dir(tmp_path).resolve_chain("default").nodes[0]
    handlers = [node.steps[0].tasks[0].on_failure, node.steps[0].on_failure, node.on_conflict]
    assert [h[0].tasks[0].task.command for h in handlers] == ["make fix"] * 3


def test_a_fix_loop_is_an_execution_node_control_naming_its_fixing_tasks():
    """`fix-loop-is-an-exec-node-control`: a gate cannot carry one, and a
    fix loop with only a judge names no fixing task."""
    with pytest.raises(ValidationError, match="fix_loop"):
        tm.GateNode.model_validate({"id": "g", "kind": "gate", "fix_loop": plan(sub("fix"))})
    with pytest.raises(ValidationError, match="exactly one of 'tasks' or 'steps'"):
        tm.FixLoop.model_validate({"judge": sub("judge")})
