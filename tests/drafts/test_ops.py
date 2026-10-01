"""The structural draft ops on the shipped `default` chain: what each writes,
what it answers, and what it refuses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kraft.drafts import ops, resolve
from kraft.templates.library import TemplateLibrary


@pytest.fixture
def st(templates_dir, database):
    return SimpleNamespace(
        templates_dir=templates_dir,
        library=TemplateLibrary.from_yaml_dir(templates_dir),
        instance_policy=None,
        skills_dir=None,
        db=database,
    )


def apply(st, *batch, key="default"):
    """The batch applied to the published chain, and the resolve result after it."""
    name = f"chains/{key}.yaml"
    published = resolve.published(st.templates_dir, [name])
    draft = ops.Draft(
        st, key, {n: t for n, t in published.items() if t is not None}, exists=bool(published[name])
    )
    answers = ops.apply(draft, [{"op": op, **fields} for op, fields in batch])
    files = draft.finish()
    result = resolve.resolve(st, "chains", key, files, published)
    model = result["model"][name]
    return SimpleNamespace(
        answers=[a.get("result") for a in answers],
        text=files[name],
        nodes={n["id"]: n for n in model["nodes"]} if model else None,
        order=[n["id"] for n in model["nodes"]] if model else None,
        paths=(result["resolved"] or {}).get("task_paths"),
        problems=[(p["path"], p["field"], p["message"]) for p in result["problems"]],
    )


def refused(st, *batch) -> str:
    with pytest.raises(ops.OpError) as exc:
        apply(st, *batch)
    return str(exc.value)


async def test_add_node_inserts_an_exec_node_with_no_step_yet_or_a_gate(st):
    r = apply(
        st,
        ("add_node", {"at": 1, "id": "lint", "kind": "exec"}),
        ("add_node", {"at": 2, "id": "lint_ok", "kind": "gate"}),
    )
    assert r.order[:4] == ["spec", "lint", "lint_ok", "spec_approval"]
    assert r.nodes["lint"] == {"id": "lint", "kind": "exec", "steps": []}
    assert r.nodes["lint_ok"] == {"id": "lint_ok", "kind": "gate"}
    assert [p for p, _, _ in r.problems] == ["lint"]


@pytest.mark.parametrize(
    ("id", "why"),
    [("spec", "already taken"), ("main", "reserved"), ("Lint", "not an id")],
    ids=["taken", "reserved", "not-an-identifier"],
)
async def test_add_node_refuses_an_id_it_cannot_take(st, id, why):
    assert why in refused(st, ("add_node", {"at": 0, "id": id, "kind": "exec"}))


async def test_a_second_step_in_an_inherited_shorthand_writes_tasks_null_beside_its_steps(st):
    # The library's `implementation` writes `tasks:`; merged onto it, the
    # chain's `steps:` would leave the node with both.
    r = apply(
        st,
        ("add_step", {"container": "implementation", "at": 1}),
        ("add_task", {"container": "implementation", "step": "step_2", "extends": "implementer"}),
    )
    assert r.answers == [
        {"path": "implementation.step_2"},
        {"path": "implementation.step_2.implementer"},
    ]
    assert r.nodes["implementation"]["tasks"] is None
    assert r.problems == []
    assert {"implementation.step_1.implement", "implementation.step_2.implementer"} <= set(r.paths)


async def test_add_task_makes_its_id_unique_and_fills_a_named_slot(st):
    r = apply(
        st,
        ("add_task", {"container": "draft_merge_request", "step": "open", "extends": "mr_rebase"}),
        ("add_task", {"container": "draft_merge_request", "step": "open", "extends": "mr_rebase"}),
        ("add_task", {"slot": "escalation", "node": "spec", "extends": "code_review"}),
    )
    assert r.answers == [
        {"path": "draft_merge_request.open.mr_rebase"},
        {"path": "draft_merge_request.open.mr_rebase_2"},
        {"path": "spec.escalation.escalate"},
    ]
    assert r.nodes["spec"]["escalation"] == {"id": "escalate", "extends": "code_review"}
    assert "spec.escalation.escalate" in r.paths


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"container": "spec", "step": "main", "kind": "agent", "extends": "x"}, "exactly one"),
        ({"slot": "judge", "node": "spec", "kind": "agent"}, "no fix loop"),
        ({"slot": "auto_review", "node": "spec_approval", "extends": "code_review"}, "reviewer"),
    ],
    ids=["kind-and-extends", "judge-without-a-fix-loop", "reviewer-extends"],
)
async def test_add_task_refuses(st, fields, why):
    assert why in refused(st, ("add_task", fields))


async def test_add_handler_starts_one_empty_main_step_and_remove_handler_deletes_it(st):
    added = apply(st, ("add_handler", {"path": "spec", "kind": "on_failure"}))
    assert added.nodes["spec"]["on_failure"] == {"steps": [{"id": "main", "tasks": []}]}
    removed = apply(
        st,
        ("add_handler", {"path": "spec", "kind": "on_failure"}),
        ("remove_handler", {"path": "spec", "kind": "on_failure"}),
    )
    assert "on_failure" not in removed.nodes["spec"]


async def test_remove_handler_writes_null_over_one_the_base_supplies(st):
    r = apply(st, ("remove_handler", {"path": "merge_request_feedback", "kind": "on_failure"}))
    assert r.nodes["merge_request_feedback"]["on_failure"] is None
    assert not [p for p in r.paths if p.startswith("merge_request_feedback.on_failure")]
    assert r.problems == []


async def test_move_reorders_a_node_a_step_and_a_task_across_steps(st):
    r = apply(
        st,
        ("move", {"path": "post_merge_ci", "to": 0}),
        ("move", {"path": "draft_merge_request.open", "to": 0}),
        ("move", {"path": "draft_merge_request.rebase.rebase", "to": 0, "to_step": "open"}),
    )
    assert r.order[0] == "post_merge_ci"
    steps = r.nodes["draft_merge_request"]["steps"]
    assert [(s["id"], [t["id"] for t in s["tasks"]]) for s in steps] == [
        ("open", ["rebase", "open"]),
        ("rebase", []),
    ]


async def test_move_refuses_an_order_that_breaks_a_reject_to(st):
    why = refused(st, ("move", {"path": "spec", "to": 1}))
    assert why == "can't move spec: spec_approval rejects to spec, which would come after it"


async def test_move_refuses_a_task_into_a_step_with_one_of_its_name(st):
    why = refused(
        st,
        (
            "add_task",
            {"container": "draft_merge_request", "step": "open", "id": "rebase", "kind": "builtin"},
        ),
        ("move", {"path": "draft_merge_request.rebase.rebase", "to": 0, "to_step": "open"}),
    )
    assert why == "step open already has a task called rebase"


async def test_remove_leaves_a_reference_to_a_node_dangling_as_a_problem(st):
    r = apply(st, ("remove", {"path": "spec"}))
    assert r.answers == [{"broken": [{"path": "spec_approval", "field": "reject_to"}]}]
    assert r.nodes["spec_approval"]["reject_to"] == "spec"
    [(_, _, message)] = r.problems
    assert "node 'spec_approval': reject_to 'spec' must name an earlier" in message


@pytest.mark.parametrize(
    "path", ["verification.fix_loop.judge", "verification.fix_loop", "verification.review"]
)
async def test_remove_drops_an_inherited_slot_or_step(st, path):
    r = apply(st, ("remove", {"path": path}))
    assert r.answers == [{"broken": []}]
    assert r.problems == []
    assert not [p for p in r.paths if p.startswith(path)]
    assert "verification.tests.test_changed_scopes" in r.paths


async def test_extend_sets_the_base_and_drops_the_nodes_own_shape(st):
    r = apply(st, ("extend", {"node": "spec", "base": "implementation"}))
    assert r.answers == [{"dropped": ["steps"]}]
    assert r.nodes["spec"] == {"id": "spec", "extends": "implementation"}
    assert "spec.main.implement" in r.paths
    assert "no node" in refused(st, ("extend", {"node": "spec", "base": "nope"}))


@pytest.mark.parametrize(
    ("node", "base", "check"),
    [
        (
            "draft_merge_request",
            "verification",
            {
                "kept": [],
                "dropped": [{"key": "steps", "why": "verification has no step rebase, open"}],
            },
        ),
        (
            "merge_request_feedback",
            "implementation",
            {
                "kept": ["on_base_changed"],
                "dropped": [{"key": "on_failure", "why": "implementation has none to remove"}],
            },
        ),
    ],
    ids=["steps-it-lacks", "a-removal-it-cannot-take"],
)
async def test_change_base_keeps_what_the_new_base_can_take(st, node, base, check):
    # `merge_request_feedback` removes the on_failure its base supplies.
    remove = ("remove_handler", {"path": "merge_request_feedback", "kind": "on_failure"})
    r = apply(st, remove, ("change_base", {"node": node, "base": base}))
    assert r.answers == [None, check]
    assert r.nodes[node]["extends"] == base
    assert not {"kind", *(d["key"] for d in check["dropped"])} & set(r.nodes[node])
    assert set(check["kept"]) <= set(r.nodes[node])


async def test_new_chain_starts_empty_or_copies_another_under_its_own_id(st):
    fresh = apply(st, ("new_chain", {}), key="fresh")
    assert fresh.text == "id: fresh\n\ndescription: ''\n\nnodes: []\n"
    copy = apply(st, ("new_chain", {"from": "default"}), key="copy")
    assert copy.text.startswith("id: copy\n")
    assert copy.paths == apply(st, ("move", {"path": "spec", "to": 0})).paths
    assert "already exists" in refused(st, ("new_chain", {}))


async def test_delete_chain_is_a_problem_only_while_a_repo_defaults_to_it(st):
    (st.templates_dir / "repos.yaml").write_text("repos:\n  - {path: /b}\n")
    r = apply(st, ("delete_chain", {}))
    assert r.text is None
    assert r.problems == [(None, None, "repo /b defaults to it")]
    assert apply(st, ("delete_chain", {}), key="quick-task").problems == []
