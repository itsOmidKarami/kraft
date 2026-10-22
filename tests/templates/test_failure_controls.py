"""The typed failure controls an execution node declares: recovery plans at
task, step and node scope, `on_base_changed` with its explicit conflict
handler, and `on_merge_rejected` with its repair. What they *do* is
`tests/executor/test_recovery.py` and `tests/executor/test_base_change.py`;
this is their shape and validation."""

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


def merge(id: str = "merge") -> dict:
    return {"id": id, "kind": "forge", "target": "mr.merge"}


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
        exec_node(
            tasks=[merge()],
            on_merge_rejected={
                "restart_from": "early",
                "repair": plan(sub("r", on_failure=NESTED)),
            },
        ),
    ],
    ids=[
        "recovery-task",
        "recovery-step",
        "fix-loop-task",
        "judge",
        "escalation",
        "on-conflict",
        "merge-rejected-repair",
    ],
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
                exec_node("early", tasks=[sub()]),
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
                ),
                exec_node(
                    "merge",
                    tasks=[merge()],
                    on_merge_rejected={"restart_from": "early", "repair": plan(sub("repair"))},
                ),
            ]
        }
    )
    node = tm.ResolvedChain.from_chain(chain).nodes[1]
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
    merging = tm.ResolvedChain.from_chain(chain).nodes[2]
    assert [t.path for s in merging.on_merge_rejected for t in s.tasks] == [
        "merge.on_merge_rejected.repair.main.repair"
    ]
    assert [s.path for s in merging.steps_in()] == [
        "merge.main",
        "merge.on_merge_rejected.repair.main",
    ]
    assert set(tm.ResolvedChain.from_chain(chain).task_paths) >= {
        "build.check.lint.on_failure.main.fmt",
        "build.check.on_failure.main.reset",
        "build.on_failure.main.clean",
        "build.on_base_changed.on_conflict.main.resolve",
        "merge.on_merge_rejected.repair.main.repair",
    }


def _chain(restart_from: str, policy: str = "on_base_changed", tasks=None) -> dict:
    """`rebase` declares `policy` restarting at `restart_from`. A merge
    rejection's repair is required, so it gets one."""
    declared = {"restart_from": restart_from}
    if policy == "on_merge_rejected":
        declared["repair"] = plan(sub("repair"))
    return {
        "id": "c",
        "nodes": [
            exec_node("early", tasks=[sub()]),
            {"id": "gate", "kind": "gate"},
            exec_node("rebase", tasks=tasks or [sub(), merge()], **{policy: declared}),
            exec_node("late", tasks=[sub()]),
        ],
    }


@pytest.mark.parametrize(
    ("policy", "target", "why"),
    [
        ("on_base_changed", "nowhere", "must name this or an earlier execution node in c"),
        ("on_base_changed", "gate", "must name this or an earlier execution node in c"),
        ("on_base_changed", "late", "must name this or an earlier execution node in c"),
        ("on_merge_rejected", "nowhere", "must name an earlier execution node in c"),
        ("on_merge_rejected", "gate", "must name an earlier execution node in c"),
        ("on_merge_rejected", "late", "must name an earlier execution node in c"),
        ("on_merge_rejected", "rebase", "must name an earlier execution node in c"),
    ],
    ids=[
        "missing",
        "gate",
        "later",
        "merge-rejected-missing",
        "merge-rejected-gate",
        "merge-rejected-later",
        "merge-rejected-itself",
    ],
)
def test_a_restart_target_must_be_this_or_an_earlier_execution_node(policy, target, why):
    """`base-change-restart-target-is-backward`. A merge rejection restarts
    strictly before its own node: the repaired head goes back through the
    merge request's own checks before it is offered to the forge again."""
    with pytest.raises(ValidationError) as refused:
        tm.Chain.model_validate(_chain(target, policy))
    assert f"node 'rebase': {policy}.restart_from {target!r} {why}" in str(refused.value)


@pytest.mark.parametrize(
    ("policy", "target"),
    [
        ("on_base_changed", "early"),
        ("on_base_changed", "rebase"),
        ("on_merge_rejected", "early"),
    ],
    ids=["earlier", "itself", "merge-rejected-earlier"],
)
def test_a_backward_restart_target_is_accepted(policy, target):
    chain = tm.Chain.model_validate(_chain(target, policy))
    assert getattr(chain.nodes[2], policy).restart_from == target


def _finalized(restart_from: str, shape: dict | None = None, **merge_fields) -> dict:
    """early -> final (the final gate) -> ready -> merge, which restarts at
    `restart_from`, runs `shape` (`tasks` or `steps`; just a merge by default)
    and declares `merge_fields` besides."""
    return {
        "id": "c",
        "nodes": [
            exec_node("early", tasks=[sub()]),
            {"id": "final", "kind": "gate", "chain_finalized": True},
            exec_node("ready", tasks=[sub()]),
            exec_node(
                "merge",
                **(shape or {"tasks": [merge()]}),
                on_merge_rejected={"restart_from": restart_from, "repair": plan(sub("repair"))},
                **merge_fields,
            ),
        ],
    }


@pytest.mark.parametrize(
    ("chain", "why"),
    [
        (
            _finalized("ready"),
            "node 'merge': on_merge_rejected.restart_from 'ready' must name an execution node "
            "before the final gate 'final' in c",
        ),
        (
            _finalized("early", fix_loop=plan(sub("fix"))),
            "node 'merge': on_merge_rejected cannot share a node with fix_loop in c",
        ),
        (
            _finalized("early", on_failure=plan(sub("fix"))),
            "node 'merge': on_merge_rejected cannot share a node with on_failure in c",
        ),
        (
            _finalized(
                "early",
                {"steps": [{"id": "land", "tasks": [merge()], "on_failure": plan(sub("fix"))}]},
            ),
            "node 'merge': on_merge_rejected cannot share a node with an on_failure on "
            "step 'land' in c",
        ),
        (
            _finalized("early", {"tasks": [sub("prep", on_failure=plan(sub("fix"))), merge()]}),
            "node 'merge': on_merge_rejected cannot share a node with an on_failure on "
            "task 'prep' in c",
        ),
    ],
    ids=[
        "restart-after-the-final-gate",
        "with-a-fix-loop",
        "with-node-on-failure",
        "with-step-on-failure",
        "with-task-on-failure",
    ],
)
def test_a_merge_rejection_rewind_must_pass_the_final_gate_and_only_the_repair_answers(chain, why):
    """A rewind that starts past the final gate would merge the repair's
    commit with no person seeing it, and a fix cycle or a repair on the merge
    node -- its own, a step's or a task's -- would commit code its next
    measure merges unseen."""
    with pytest.raises(ValidationError) as refused:
        tm.Chain.model_validate(chain)
    assert why in str(refused.value)


def test_a_merge_rejection_rewind_from_before_the_final_gate_is_accepted():
    chain = tm.Chain.model_validate(_finalized("early"))
    assert chain.nodes[3].on_merge_rejected.restart_from == "early"


@pytest.mark.parametrize(
    "tasks",
    [
        [sub()],
        [sub(), {**merge(), "target": "mr.mark_ready"}],
        [sub("t", on_failure=plan(merge()))],
    ],
    ids=["no-forge-task", "another-forge-target", "only-in-a-recovery-plan"],
)
def test_a_merge_rejection_policy_needs_a_merge_in_its_own_node(tasks):
    """Only a node that asks the forge to merge can have a merge rejected."""
    with pytest.raises(ValidationError) as refused:
        tm.Chain.model_validate(_chain("early", "on_merge_rejected", tasks))
    assert "node 'rebase': on_merge_rejected needs an mr.merge task in the same node" in str(
        refused.value
    )


def test_library_components_extend_inside_step_task_and_conflict_handlers(tmp_path):
    """Every handler position expands `extends` like any other task position.
    A merge task reached through `extends` satisfies `on_merge_rejected`:
    `Chain` checks the expanded node."""
    (tmp_path / "chains").mkdir()
    (tmp_path / "library.yaml").write_text(
        yaml.safe_dump(
            {
                "tasks": {
                    "fixer": {"kind": "subprocess", "command": "make fix"},
                    "merger": {"kind": "forge", "target": "mr.merge"},
                }
            }
        )
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
                    ),
                    exec_node(
                        "merge",
                        tasks=[{"id": "merge", "extends": "merger"}],
                        on_merge_rejected={"restart_from": "build", "repair": fix},
                    ),
                ],
            }
        )
    )
    build, merging = TemplateLibrary.from_yaml_dir(tmp_path).resolve_chain("default").nodes
    handlers = [
        build.steps[0].tasks[0].on_failure,
        build.steps[0].on_failure,
        build.on_conflict,
        merging.on_merge_rejected,
    ]
    assert [h[0].tasks[0].task.command for h in handlers] == ["make fix"] * 4
    assert merging.on_merge_rejected[0].tasks[0].path == "merge.on_merge_rejected.repair.main.fix"


def test_a_fix_loop_is_an_execution_node_control_naming_its_fixing_tasks():
    """`fix-loop-is-an-exec-node-control`: a gate cannot carry one, and a
    fix loop with only a judge names no fixing task."""
    with pytest.raises(ValidationError, match="fix_loop"):
        tm.GateNode.model_validate({"id": "g", "kind": "gate", "fix_loop": plan(sub("fix"))})
    with pytest.raises(ValidationError, match="exactly one of 'tasks' or 'steps'"):
        tm.FixLoop.model_validate({"judge": sub("judge")})
