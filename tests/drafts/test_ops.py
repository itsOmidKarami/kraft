"""The structural draft ops on the shipped `default` chain: what each writes,
what it answers, and what it refuses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from support.drafts import apply_ops as apply
from support.drafts import draft_state, refused

from kraft.drafts import authored, ops, resolve


@pytest.fixture
def st(templates_dir, database):
    return draft_state(templates_dir, database)


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


async def test_add_task_gives_a_gate_a_reviewer_that_extends_a_library_task(st):
    """Kraft-67yp8. Mutate: restore the refusal in `add_task`, or drop the
    `auto_review` expansion in `_Resolution._node`, and this fails."""
    r = apply(
        st, ("add_task", {"slot": "auto_review", "node": "spec_approval", "extends": "code_review"})
    )
    assert r.answers == [{"path": "spec_approval.auto_review"}]
    assert r.nodes["spec_approval"]["auto_review"] == {"id": "reviewer", "extends": "code_review"}
    assert r.problems == []
    assert "spec_approval.auto_review" in r.paths


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"container": "spec", "step": "main", "kind": "agent", "extends": "x"}, "exactly one"),
        ({"slot": "judge", "node": "spec", "kind": "agent"}, "no fix loop"),
    ],
    ids=["kind-and-extends", "judge-without-a-fix-loop"],
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


#: Taking the shipped `merge` node's merge-rejection policy away.
UNREJECTED = ("remove_handler", {"path": "merge", "kind": "on_merge_rejected"})
#: A restart with no repair yet, as the Config row's restart field writes it.
RESTARTS = (
    "set_field",
    {"path": "merge", "field": "on_merge_rejected.restart_from", "value": "implementation"},
)

#: Two merging nodes with no `on_merge_rejected`: `land` first, `merge` after
#: an execution node and a gate.
UNPOLICED = """id: unpoliced
nodes:
  - {id: land, kind: exec, tasks: [{id: merge, extends: merge_mr}]}
  - {id: build, kind: exec, tasks: [{id: t, kind: subprocess, command: "true"}]}
  - {id: review, kind: gate}
  - {id: merge, kind: exec, tasks: [{id: merge, extends: merge_mr}]}
"""


def _chain(st, key: str) -> str:
    (st.templates_dir / "chains" / "unpoliced.yaml").write_text(UNPOLICED)
    return key


@pytest.mark.parametrize(
    ("key", "batch", "restart"),
    [
        ("default", [UNREJECTED, RESTARTS], "implementation"),
        ("default", [UNREJECTED], "work_item_summary"),
        ("unpoliced", [], "build"),
    ],
    ids=["keeps-the-restart-it-has", "the-last-exec-before-the-final-gate", "the-nearest-exec"],
)
async def test_add_handler_gives_a_merge_rejection_an_empty_repair_and_a_restart(
    st, key, batch, restart
):
    """The repair is required, so the policy arrives whole: `restart_from`
    the one the node sets, else the last node `Chain` lets it name. The empty
    repair is the only thing left to fill."""
    add = ("add_handler", {"path": "merge", "kind": "on_merge_rejected"})
    r = apply(st, *batch, add, key=_chain(st, key))
    assert r.answers[-1] == {"path": "merge.on_merge_rejected.repair"}
    assert r.nodes["merge"]["on_merge_rejected"] == {
        "restart_from": restart,
        "repair": {"steps": [{"id": "main", "tasks": []}]},
    }
    assert [(p, f) for p, f, _ in r.problems] == [("merge", "on_merge_rejected.repair")]


_NO_MERGE = "does not merge: on_merge_rejected needs an mr.merge task in it"


@pytest.mark.parametrize(
    ("key", "path", "why"),
    [
        ("unpoliced", "land", "land has no execution node before it to restart from"),
        ("default", "nope", "on_merge_rejected is a node's, and there is no node 'nope'"),
        ("default", "merge", "merge already has an on_merge_rejected; edit its repair"),
        ("default", "final_review", f"final_review {_NO_MERGE}"),
        ("default", "implementation", f"implementation {_NO_MERGE}"),
    ],
    ids=["nothing-earlier", "no-such-node", "already-has-one", "a-gate", "no-merge-in-it"],
)
async def test_add_handler_refuses_a_merge_rejection_where_it_cannot_go(st, key, path, why):
    """Nowhere to restart, no node, one already there -- whose repair, the
    shipped one a task of its own, would become an empty step -- or nothing
    in the node a train could reject."""
    add = ("add_handler", {"path": path, "kind": "on_merge_rejected"})
    assert why in refused(st, add, key=_chain(st, key))


@pytest.mark.parametrize(
    ("path", "at"),
    [("merge", ("on_failure",)), ("merge.main.merge", ("tasks", 0, "on_failure"))],
    ids=["the-node-s", "its-merge-task-s"],
)
async def test_add_handler_starts_an_on_failure_beside_a_merge_rejection_read_only(st, path, at):
    """A handler on a node with `on_merge_rejected` re-runs the merge in place,
    so `Chain` takes only one that cannot commit: the plan arrives read_only,
    written on the plan since its `tasks` shorthand has no step of its own.
    Its Config row says so; the repair, which writes by design, has none."""
    fill = {"container": f"{path}.on_failure", "step": "main", "extends": "await_mr_ci"}
    r = apply(st, ("add_handler", {"path": path, "kind": "on_failure"}), ("add_task", fill))
    assert r.problems == []
    handler = next(n for n in authored.parse(r.text)["nodes"] if n["id"] == "merge")
    for key in at:
        handler = handler[key]
    assert handler == {
        "tasks": [{"id": "await_mr_ci", "extends": "await_mr_ci"}],
        "read_only": True,
    }
    sources = r.result["sources"]
    assert sources[f"{path}.on_failure"]["read_only"] == {"value": True, "source": "chain"}
    assert "read_only" not in sources.get("merge.on_merge_rejected.repair", {})


async def test_remove_handler_drops_a_merge_rejection_whole_and_only_a_merge_keeps_its_row(st):
    """Its repair is required, so removing it takes the restart too; the
    node still merges, so its Config row stays, unset; a node that does not
    merge has none."""
    r = apply(st, UNREJECTED)
    assert "on_merge_rejected" not in r.nodes["merge"]
    assert r.problems == []
    sources = r.result["sources"]
    assert sources["merge"]["on_merge_rejected"] == {"value": None, "source": "default"}
    assert "on_merge_rejected" not in sources["verification"]
    assert "on_base_changed" in sources["verification"]


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


async def test_new_chain_copies_only_a_chain_in_the_library(st):
    """`from` was joined onto `chains/` as written, so `../..` previewed any
    YAML file the server could read."""
    (st.templates_dir.parent / "outside.yaml").write_text("description: a secret\n")
    origin = "../../outside"
    assert (st.templates_dir / f"chains/{origin}.yaml").is_file()
    with pytest.raises(ops.OpError, match="there is no chain"):
        apply(st, ("new_chain", {"from": origin}), key="copy")


async def test_delete_chain_is_a_problem_only_while_a_repo_defaults_to_it(st):
    (st.templates_dir / "repos.yaml").write_text("repos:\n  - {path: /b}\n")
    r = apply(st, ("delete_chain", {}))
    assert r.text is None
    assert r.problems == [(None, None, "repo /b defaults to it")]
    assert apply(st, ("delete_chain", {}), key="quick-task").problems == []


def fields(path, *pairs):
    return [("set_field", {"path": path, "field": f, "value": v}) for f, v in pairs]


GATE = {
    "id": "spec_approval",
    "kind": "gate",
    "message": "Review and approve the specification.",
    "artifact": "spec",
    "reject_to": "spec",
}


@pytest.mark.parametrize(
    ("path", "pairs", "after"),
    [
        (
            "spec.main.author",
            [("model", "opus"), ("effort", "high"), ("profile", "strong")],
            {"id": "author", "extends": "spec_author", "profile": "strong"},
        ),
        (
            "spec.main.author",
            [("profile", "strong"), ("model", "opus")],
            {"id": "author", "extends": "spec_author", "model": "opus"},
        ),
        (
            "implementation.main.implement",
            [("policy.time_cap_minutes", 120)],
            {"id": "implement", "extends": "implementer"},
        ),
        (
            "implementation.main.implement",
            [("policy.time_cap_minutes", 90)],
            {"id": "implement", "extends": "implementer", "policy": {"time_cap_minutes": 90}},
        ),
        ("spec_approval", [("message", "")], {k: v for k, v in GATE.items() if k != "message"}),
        (
            "spec_approval",
            [("artifact_required", True), ("artifact", None)],
            {k: v for k, v in GATE.items() if k != "artifact"},
        ),
        ("spec_approval", [("artifact_required", True), ("artifact_required", False)], GATE),
    ],
    ids=[
        "profile-drops-model-and-effort",
        "model-drops-profile",
        "equal-to-inherited-leaves-no-key",
        "an-override-stays",
        "empty-removes",
        "no-artifact-drops-artifact-required",
        "artifact-required-false-removes",
    ],
)
async def test_set_field(st, path, pairs, after):
    r = apply(st, *fields(path, *pairs))
    assert authored.at(r.model, path) == after
    assert r.problems == []


async def test_an_empty_restart_from_drops_on_base_changed_whole(st):
    path = "merge_request_feedback"
    r = apply(
        st,
        ("add_handler", {"path": path, "kind": "on_conflict"}),
        *fields(path, ("on_base_changed.restart_from", "")),
    )
    assert authored.at(r.model, path) == {"id": path, "extends": "post_draft_feedback"}


async def test_set_field_refuses_the_id(st):
    assert "rename it with rename" in refused(st, *fields("spec", ("id", "x")))


@pytest.mark.parametrize(
    ("op", "fields", "why"),
    [
        ("restore", {"node": "spec"}, "extends nothing, so no node is removed"),
        ("detach", {}, "extends nothing, so there is nothing to detach"),
        ("chain_base_check", {"base": "quick-task"}, "extends nothing, so it has no base"),
        ("change_chain_base", {"base": "quick-task"}, "extends nothing, so it has no base"),
        ("nope", {}, "no op 'nope'"),
    ],
    ids=["restore", "detach", "chain_base_check", "change_chain_base", "unknown"],
)
async def test_a_chain_extending_nothing_has_no_extending_ops(st, op, fields, why):
    assert why in refused(st, (op, fields))


async def test_reset_field_removes_one_override_or_every_one(st):
    path = "implementation.main.implement"
    overrides = fields(path, ("icon", "hammer"), ("model", "opus"), ("policy.budget_usd", 2))
    one = apply(st, *overrides, ("reset_field", {"path": path, "field": "policy.budget_usd"}))
    assert authored.at(one.model, path) == {
        "id": "implement", "extends": "implementer", "icon": "hammer", "model": "opus"
    }  # fmt: skip
    every = apply(st, *overrides, ("reset_field", {"path": path}))
    assert authored.at(every.model, path) == {
        "id": "implement",
        "extends": "implementer",
        "icon": "hammer",
    }
    assert "extends nothing" in refused(st, ("reset_field", {"path": "spec_approval"}))


async def test_renaming_a_node_rewrites_and_lists_every_reference_to_it(st):
    r = apply(
        st,
        *fields("merge_request_feedback", ("on_base_changed.restart_from", "implementation")),
        ("rename", {"path": "implementation", "id": "build"}),
    )
    assert r.answers[1] == {
        "updated": [
            {"path": "local_review", "field": "reject_to"},
            {"path": "merge_request_feedback", "field": "on_base_changed.restart_from"},
            {"path": "final_review", "field": "reject_to"},
        ]
    }
    assert r.nodes["local_review"]["reject_to"] == r.nodes["final_review"]["reject_to"] == "build"
    assert r.nodes["merge_request_feedback"]["on_base_changed"] == {"restart_from": "build"}
    assert r.problems == []


#: The shipped `merge` node given a merge-rejection repair that restarts at
#: `merge_request_feedback`.
REJECTED = """\
id: merge
kind: exec
tasks:
  - {id: merge, extends: merge_mr}
on_merge_rejected:
  restart_from: merge_request_feedback
  repair: {tasks: [{id: repair, extends: implementer, model: opus}]}
"""


async def test_a_merge_rejection_is_addressed_and_its_restart_is_a_reference(st):
    """Its repair's tasks are edited at their own paths, its node lists the
    policy without the repair, and its `restart_from` is renamed and ordered
    like the other pointers."""
    fragment = ("set_fragment", {"path": "merge", "yaml": REJECTED})
    repair = "merge.on_merge_rejected.repair.main.repair"
    r = apply(
        st,
        fragment,
        *fields(repair, ("effort", "high")),
        ("rename", {"path": "merge_request_feedback", "id": "feedback"}),
    )
    assert r.problems == []
    assert authored.at(r.model, repair)["effort"] == "high"
    assert {"path": "merge", "field": "on_merge_rejected.restart_from"} in r.answers[-1]["updated"]
    assert r.nodes["merge"]["on_merge_rejected"]["restart_from"] == "feedback"
    sources = r.result["sources"]
    assert sources["merge"]["on_merge_rejected"]["value"] == {"restart_from": "feedback"}
    assert sources[repair]["model"] == {"value": "opus", "source": "chain"}
    assert sources[repair]["prompt"]["source"] == "library:tasks.implementer"
    assert refused(st, fragment, ("move", {"path": "merge", "to": 0})) == (
        "can't move merge: merge restarts from merge_request_feedback after a rejected merge, "
        "which would not come before it"
    )


async def test_renaming_a_task_or_a_slot_changes_its_id_only(st):
    r = apply(
        st,
        ("rename", {"path": "spec.main.author", "id": "writer"}),
        ("rename", {"path": "verification.fix_loop.judge", "id": "arbiter"}),
    )
    assert r.answers == [{"updated": []}, {"updated": []}]
    assert "spec.main.writer" in r.paths
    assert authored.at(r.model, "verification.fix_loop.judge")["id"] == "arbiter"
    assert "already taken" in refused(
        st, ("rename", {"path": "draft_merge_request.open", "id": "rebase"})
    )


async def test_renaming_the_chain_moves_its_file(st):
    (st.templates_dir / "repos.yaml").write_text("repos:\n  - {path: /b}\n")
    r = apply(st, ("rename", {"path": "", "id": "renamed"}))
    assert r.files["chains/default.yaml"] is None
    assert r.files["chains/renamed.yaml"].startswith("id: renamed\n")
    assert "spec.main.author" in r.paths
    assert r.problems == [(None, None, "repo /b defaults to it")]
    assert "already exists" in refused(st, ("rename", {"path": "", "id": "quick-task"}))
    back = apply(st, *[("rename", {"path": "", "id": id}) for id in ("renamed", "default")])
    assert back.files["chains/renamed.yaml"] is None
    assert back.text.startswith("id: default\n")


async def test_set_fragment_replaces_the_component(st):
    fragment = "id: spec\nkind: exec\ntasks:\n  - {id: author, extends: spec_author, model: opus}\n"
    r = apply(
        st,
        ("set_fragment", {"path": "spec", "yaml": fragment}),
        *fields("spec.main.author", ("effort", "high")),
    )
    assert authored.at(r.model, "spec.main.author") == {
        "id": "author", "extends": "spec_author", "model": "opus", "effort": "high"
    }  # fmt: skip
    assert "spec.main.author" in r.paths
    why = refused(st, ("set_fragment", {"path": "spec", "yaml": "id: other\nkind: exec\n"}))
    assert "rename it with rename" in why


async def test_a_fragment_yaml_error_is_at_the_fragments_own_line(st):
    with pytest.raises(ops.OpError) as exc:
        apply(st, ("set_fragment", {"path": "spec", "yaml": "id: spec\nkind: exec\ntasks: [\n"}))
    assert exc.value.extra == {"line": 4, "col": 1}
    assert str(exc.value).startswith("line 4, column 1: ")


# ── the library ──


def library(st, *batch):
    """The batch applied to the published library, and the resolve result after it."""
    published = resolve.published(st.templates_dir, ["library.yaml"])
    draft = ops.Draft(st, "library", published, exists=True, area="library")
    answers = ops.apply(draft, [{"op": op, **fields} for op, fields in batch])
    files = draft.finish()
    result = resolve.resolve(
        st, "library", "library", files, resolve.published(st.templates_dir, sorted(files))
    )
    return SimpleNamespace(
        answers=[a.get("result") for a in answers],
        files=files,
        model=result["model"],
        problems=[(p["chain"], p["path"], p["component"]) for p in result["problems"]],
    )


def refused_library(st, *batch) -> str:
    with pytest.raises(ops.OpError) as exc:
        library(st, *batch)
    return str(exc.value)


async def test_a_library_field_op_addresses_a_component_by_its_section(st):
    task = "nodes.verification.review.code_review"
    r = library(
        st,
        ("set_field", {"path": task, "field": "prompt", "value": "Look harder."}),
        # Equal to what `tasks.code_review` gives it: no key.
        ("set_field", {"path": task, "field": "skill", "value": "kraft:code-review"}),
    )
    assert authored.at(r.model["library.yaml"], task, file=authored.LIBRARY) == {
        "id": "code_review", "extends": "code_review", "prompt": "Look harder."
    }  # fmt: skip
    assert r.problems == []


async def test_add_component_adds_an_empty_one_to_its_section(st):
    r = library(
        st,
        ("add_component", {"section": "steering", "name": "terse"}),
        ("add_component", {"section": "tasks", "name": "lint", "kind": "subprocess"}),
    )
    assert r.answers == [{"path": "steering.terse"}, {"path": "tasks.lint"}]
    model = r.model["library.yaml"]
    assert (model["steering"]["terse"], model["tasks"]["lint"]) == (
        {"instructions": ""},
        {"kind": "subprocess"},
    )
    # A profile with no instructions is a problem until they are written.
    assert r.problems == [(None, "steering.terse", "steering.terse")]
    why = refused_library(st, ("add_component", {"section": "tasks", "name": "implementer"}))
    assert "already taken" in why


async def test_renaming_a_library_task_rewrites_every_use_and_pulls_its_chains_in(st):
    r = library(st, ("rename", {"path": "tasks.implementer", "id": "builder"}))
    assert r.answers == [
        {
            "updated": [
                {"file": "library.yaml", "path": "nodes.implementation.main.implement",
                 "field": "extends"},
                {"file": "chains/quick-task.yaml", "path": "implementation.main.implement",
                 "field": "extends"},
            ]
        }
    ]  # fmt: skip
    # `default` reaches it only through the library node: its file stays out.
    assert sorted(r.files) == ["chains/quick-task.yaml", "library.yaml"]
    quick = r.model["chains/quick-task.yaml"]
    assert authored.at(quick, "implementation.main.implement")["extends"] == "builder"
    assert list(r.model["library.yaml"]["tasks"]).index("builder") == 3
    assert r.problems == []


MERGING = """id: merging
nodes:
  - {id: build, kind: exec, tasks: [{id: t, kind: subprocess, command: "true"}]}
  - id: merge
    kind: exec
    tasks: [{id: merge, extends: merge_mr}]
    on_merge_rejected: {restart_from: build, repair: {tasks: [{id: fix, extends: implementer}]}}
"""


async def test_renaming_a_library_task_rewrites_its_use_in_a_merge_rejection_repair(st):
    (st.templates_dir / "chains" / "merging.yaml").write_text(MERGING)
    r = library(st, ("rename", {"path": "tasks.implementer", "id": "builder"}))
    repair = "merge.on_merge_rejected.repair.main.fix"
    assert {"file": "chains/merging.yaml", "path": repair, "field": "extends"} in r.answers[0][
        "updated"
    ]
    assert authored.at(r.model["chains/merging.yaml"], repair)["extends"] == "builder"


TEAM_USES = """id: team
extends: default
add:
  - after: verification
    node: {id: build, extends: implementation}
  - after: verification
    node:
      id: rebuild
      kind: exec
      tasks:
        - {id: impl, extends: implementer}
overrides:
  verification:
    steps:
      - id: tests
        tasks:
          - {id: again, extends: implementer}
  implementation.main.implement: {steering: [project-standards]}
  verification.fix_loop.judge: {steering: [project-standards]}
  post_draft_feedback.on_failure.repair.repair_feedback: {steering: [project-standards]}
"""


@pytest.mark.parametrize(
    ("old", "new", "path", "read"),
    [
        ("nodes.implementation", "build_node", "build", lambda m: m["add"][0]["node"]["extends"]),
        (
            "tasks.implementer",
            "builder",
            "rebuild.main.impl",
            lambda m: m["add"][1]["node"]["steps"][0]["tasks"][0]["extends"],
        ),
        (
            "tasks.implementer",
            "builder",
            "verification.tests.again",
            lambda m: m["overrides"]["verification"]["steps"][0]["tasks"][0]["extends"],
        ),
        (
            "steering.project-standards",
            "standards",
            "implementation.main.implement",
            lambda m: m["overrides"]["implementation.main.implement"]["steering"][0],
        ),
        (
            "steering.project-standards",
            "standards",
            "verification.fix_loop.judge",
            lambda m: m["overrides"]["verification.fix_loop.judge"]["steering"][0],
        ),
        (
            "steering.project-standards",
            "standards",
            "post_draft_feedback.on_failure.repair.repair_feedback",
            lambda m: m["overrides"]["post_draft_feedback.on_failure.repair.repair_feedback"][
                "steering"
            ][0],
        ),
    ],
    ids=[
        "added-node",
        "added-task",
        "override-list",
        "override-steering",
        "override-a-judge",
        "override-a-handler-task",
    ],
)
async def test_renaming_a_library_component_rewrites_an_extending_chains_uses(
    st, old, new, path, read
):
    (st.templates_dir / "chains" / "team.yaml").write_text(TEAM_USES)
    r = library(st, ("rename", {"path": old, "id": new}))
    assert {"file": "chains/team.yaml", "path": path} in [
        {k: u[k] for k in ("file", "path")} for u in r.answers[0]["updated"]
    ]
    assert read(r.model["chains/team.yaml"]) == new


async def test_renaming_a_steering_profile_rewrites_the_tasks_selecting_it(st):
    r = library(st, ("rename", {"path": "steering.project-standards", "id": "standards"}))
    assert r.model["library.yaml"]["tasks"]["spec_author"]["steering"] == ["standards"]
    assert r.problems == []


async def test_removing_a_library_component_leaves_its_uses_as_problems(st):
    r = library(st, ("remove", {"path": "tasks.implementer"}))
    assert [b["path"] for b in r.answers[0]["broken"]] == [
        "nodes.implementation.main.implement",
        "implementation.main.implement",
    ]
    assert "implementer" not in r.model["library.yaml"]["tasks"]
    assert sorted(r.files) == ["library.yaml"]
    # `default` through its library node, `quick-task` in its own file.
    assert r.problems == [
        ("default", "implementation", "nodes.implementation"),
        ("quick-task", "implementation", None),
    ]


async def test_move_to_library_moves_a_node_and_its_chain_extends_it(st):
    r = apply(
        st,
        ("move_to_library", {"path": "spec", "name": "spec_node"}),
        # Inherited from the moved node, which only the draft's library has: no key.
        ("set_field", {"path": "spec", "field": "kind", "value": "exec"}),
    )
    assert r.answers == [{"path": "nodes.spec_node"}, None]
    assert r.nodes["spec"] == {"id": "spec", "extends": "spec_node"}
    assert r.model is not None and r.problems == []
    moved = authored.load(r.files["library.yaml"])["nodes"]["spec_node"]
    assert moved == {"kind": "exec", "steps": [{"id": "main", "tasks": [
        {"id": "author", "extends": "spec_author"}
    ]}]}  # fmt: skip
    assert "spec.main.author" in r.paths


async def test_move_to_library_moves_a_task_and_refuses_a_taken_name(st):
    r = apply(st, ("move_to_library", {"path": "draft_merge_request.open.open", "name": "opener"}))
    assert authored.at(r.model, "draft_merge_request.open.open") == {
        "id": "open", "extends": "opener"
    }  # fmt: skip
    assert authored.load(r.files["library.yaml"])["tasks"]["opener"] == {"extends": "open_draft_mr"}
    assert r.problems == []
    why = refused(st, ("move_to_library", {"path": "spec", "name": "verification"}))
    assert "already taken" in why


EXTENDING = "id: team\nextends: default\nremove: [external_approval]\n"


def extending(st) -> ops.Draft:
    return ops.Draft(st, "team", {"chains/team.yaml": EXTENDING}, exists=True)


async def test_an_extending_chain_can_still_be_renamed_or_deleted(st):
    renamed = extending(st)
    ops.apply(renamed, [{"op": "rename", "path": "", "id": "crew"}])
    assert "extends: default" in renamed.finish()["chains/crew.yaml"]
    deleted = extending(st)
    ops.apply(deleted, [{"op": "delete_chain"}])
    assert deleted.finish()["chains/team.yaml"] is None
