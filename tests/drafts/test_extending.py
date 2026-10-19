"""The Templates editor's ops on a chain that extends another: every one
writes `remove`, `add` or `overrides`, never a copy of what it inherits."""

from __future__ import annotations

import pytest
from support.drafts import apply_ops, draft_state, refused

IMPLEMENT = "implementation.main.implement"
TEAM = """id: team
extends: default
remove: [external_approval]
add:
  - after: verification
    node:
      id: security_scan
      kind: exec
      tasks:
        - {id: scan, kind: subprocess, command: "true"}
overrides:
  implementation.main.implement: {prompt: Implement it carefully.}
"""


@pytest.fixture
def st(templates_dir, database):
    (templates_dir / "chains" / "team.yaml").write_text(TEAM)
    return draft_state(templates_dir, database)


def team(st, *batch):
    return apply_ops(st, *batch, key="team")


def no(st, *batch) -> str:
    return refused(st, *batch, key="team")


@pytest.mark.parametrize(
    "batch",
    [
        [("add_node", {"at": 3, "id": "lint", "kind": "gate"})],
        [("remove", {"path": "post_merge_ci"})],
        [("move", {"path": "security_scan", "to": 10})],
        [("set_field", {"path": IMPLEMENT, "field": "effort", "value": "high"})],
        [
            (
                "add_task",
                {"container": "security_scan", "step": "main", "kind": "subprocess", "id": "lint"},
            )
        ],
        [("restore", {"node": "external_approval"})],
    ],
    ids=["add_node", "remove", "move", "set_field", "add_task-in-added", "restore"],
)
async def test_no_op_writes_a_nodes_list(st, batch):
    r = team(st, *batch)
    assert "nodes" not in r.model and "\nnodes:" not in r.text


async def test_removing_an_inherited_node_writes_remove(st):
    r = team(st, ("remove", {"path": "post_merge_ci"}))
    assert r.model["remove"] == ["external_approval", "post_merge_ci"]
    assert r.answers == [{"broken": []}]
    assert "post_merge_ci" not in r.resolved_order


async def test_the_editor_keeps_the_final_gate(st):
    assert no(st, ("remove", {"path": "final_review"})).startswith("final_review is the final gate")


async def test_restoring_a_removed_node_deletes_its_entry(st):
    r = team(st, ("restore", {"node": "external_approval"}))
    assert "remove" not in r.model
    order = r.resolved_order
    assert order.index("external_approval") == order.index("mark_ready") + 1


#: A gate added right behind `security_scan` (index 8) first, on the same
#: anchor: an add dropped between the two goes between their entries.
BEHIND = [("add_node", {"at": 9, "id": "notify", "kind": "gate"})]


@pytest.mark.parametrize(
    ("first", "prev", "after"),
    [
        ([], "implementation", "implementation"),
        ([], "verification", "verification"),
        (BEHIND, "security_scan", "verification"),
        ([], "post_merge_ci", "post_merge_ci"),
    ],
    ids=[
        "after-an-inherited-node",
        "before-an-earlier-add-on-that-node",
        "after-an-added-node",
        "at-the-end",
    ],
)
async def test_an_add_resolves_where_it_was_dropped(st, first, prev, after):
    at = team(st, *first).resolved_order.index(prev) + 1
    r = team(st, *first, ("add_node", {"at": at, "id": "lint", "kind": "gate"}))
    assert r.resolved_order[at] == "lint"
    assert next(e for e in r.model["add"] if e["node"]["id"] == "lint")["after"] == after


@pytest.mark.parametrize(
    "batch",
    [
        ("add_node", {"at": 0, "id": "lint", "kind": "gate"}),
        ("move", {"path": "security_scan", "to": 0}),
    ],
    ids=["add_node", "move"],
)
async def test_nothing_goes_before_the_first_node(st, batch):
    assert "no place before spec" in no(st, batch)


@pytest.mark.parametrize(
    "to_after", ["work_item_summary", "implementation"], ids=["later", "earlier"]
)
async def test_moving_an_added_node_rewrites_its_entry(st, to_after):
    rest = [i for i in team(st).resolved_order if i != "security_scan"]
    to = rest.index(to_after) + 1
    r = team(st, ("move", {"path": "security_scan", "to": to}))
    assert r.resolved_order.index("security_scan") == to
    assert r.model["add"][0]["after"] == to_after


@pytest.mark.parametrize(
    ("batch", "error"),
    [
        ([("remove", {"path": "verification"})], "security_scan goes after it"),
        ([("remove", {"path": "spec"})], "spec_approval's reject_to names it"),
        (
            [
                ("remove", {"path": "spec_approval"}),
                ("remove", {"path": "spec"}),
                ("restore", {"node": "spec_approval"}),
            ],
            "restore spec first",
        ),
        (
            [
                (
                    "set_field",
                    {"path": "local_review", "field": "reject_to", "value": "external_approval"},
                )
            ],
            "reject_to would name external_approval",
        ),
    ],
    ids=[
        "remove-an-anchor",
        "remove-a-pointer-target",
        "restore-a-pointer-to-a-removed-node",
        "point-at-a-removed-node",
    ],
)
async def test_the_editor_leaves_nothing_naming_a_removed_node(st, batch, error):
    assert error in no(st, *batch)


FINAL_MESSAGE = "Review the complete work item and merge-request summary."


@pytest.mark.parametrize(
    ("batch", "path", "expected"),
    [
        (
            [
                (
                    "set_field",
                    {"path": "final_review", "field": "message", "value": "The lead signs off."},
                )
            ],
            "final_review",
            {"message": "The lead signs off."},
        ),
        (
            [("set_field", {"path": IMPLEMENT, "field": "model", "value": "opus"})],
            IMPLEMENT,
            {"prompt": "Implement it carefully.", "model": "opus"},
        ),
        (
            [
                ("set_field", {"path": IMPLEMENT, "field": "model", "value": "opus"}),
                ("set_field", {"path": IMPLEMENT, "field": "profile", "value": "strong"}),
            ],
            IMPLEMENT,
            {"prompt": "Implement it carefully."},
        ),
        (
            [
                (
                    "set_field",
                    {"path": "verification", "field": "policy.time_cap_minutes", "value": 30},
                )
            ],
            "verification",
            {"policy": {"time_cap_minutes": 30}},
        ),
        (
            [
                ("set_field", {"path": "final_review", "field": "message", "value": "x"}),
                (
                    "set_field",
                    {"path": "final_review", "field": "message", "value": FINAL_MESSAGE},
                ),
            ],
            "final_review",
            None,
        ),
    ],
    ids=[
        "node-field",
        "task-model",
        "task-route-back-to-inherited",
        "dotted-policy-field",
        "back-to-the-inherited-value",
    ],
)
async def test_an_inherited_field_edit_is_an_override(st, batch, path, expected):
    assert (team(st, *batch).model.get("overrides") or {}).get(path) == expected


@pytest.mark.parametrize(
    ("resets", "expected"),
    [
        ([{"field": "prompt"}], {IMPLEMENT: {"effort": "high"}}),
        ([{}], None),
        ([{"field": "prompt"}, {"field": "effort"}], None),
    ],
    ids=["one-field", "every-field", "the-last-field"],
)
async def test_resetting_an_inherited_field_deletes_its_override(st, resets, expected):
    r = team(
        st,
        ("set_field", {"path": IMPLEMENT, "field": "effort", "value": "high"}),
        *(("reset_field", {"path": IMPLEMENT, **reset}) for reset in resets),
    )
    assert r.model.get("overrides") == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [("id", "x"), ("kind", "exec"), ("extends", "x"), ("chain_finalized", False)],
    ids=["id", "kind", "extends", "chain_finalized"],
)
async def test_an_editor_override_keeps_identity(st, field, value):
    why = no(st, ("set_field", {"path": "final_review", "field": field, "value": value}))
    assert f"an override can't change {field}" in why


@pytest.mark.parametrize(
    ("batch", "error"),
    [
        (("move", {"path": "spec", "to": 3}), "spec comes from default, so it keeps its place"),
        (("rename", {"path": "spec", "id": "specify"}), "would replace"),
        (("add_step", {"container": "verification", "at": 0}), "inherited from default"),
        (
            ("add_task", {"container": "verification", "step": "tests", "kind": "subprocess"}),
            "inherited from default",
        ),
        (("remove", {"path": "verification.review.code_review"}), "inherited from default"),
        (
            ("set_fragment", {"path": "spec", "yaml": "id: spec\nkind: exec\n"}),
            "inherited from default",
        ),
        (("extend", {"node": "spec", "base": "implementation"}), "inherited from default"),
        (("move_to_library", {"path": "spec", "name": "speccy"}), "inherited from default"),
        (("set_field", {"path": "", "field": "extends", "value": None}), "YAML view"),
        (("set_field", {"path": "", "field": "add", "value": []}), "YAML view"),
        (("reset_field", {"path": ""}), "YAML view"),
        (("set_fragment", {"path": "", "yaml": "id: team\n"}), "change the chain itself"),
        (("remove", {"path": "implementation"}), "implementation has overrides; reset them first"),
    ],
    ids=[
        "move-node",
        "rename-node",
        "add_step",
        "add_task",
        "remove-task",
        "set_fragment",
        "extend",
        "move_to_library",
        "unset-extends",
        "write-add",
        "reset-the-chain",
        "set_fragment-the-chain",
        "remove-an-overridden-node",
    ],
)
async def test_an_inherited_component_refuses_what_has_no_edit(st, batch, error):
    assert error in no(st, batch)


def _tasks(node):
    return [t["id"] for t in node["steps"][0]["tasks"]]


LINT = (
    "add_task",
    {"container": "security_scan", "step": "main", "kind": "subprocess", "id": "lint"},
)


@pytest.mark.parametrize(
    ("batch", "check"),
    [
        ([LINT], lambda node: _tasks(node) == ["scan", "lint"]),
        (
            [
                (
                    "set_field",
                    {"path": "security_scan.main.scan", "field": "command", "value": "make scan"},
                )
            ],
            lambda node: node["steps"][0]["tasks"][0]["command"] == "make scan",
        ),
        (
            [LINT, ("remove", {"path": "security_scan.main.lint"})],
            lambda node: _tasks(node) == ["scan"],
        ),
        (
            [("move_to_library", {"path": "security_scan", "name": "security_scan"})],
            lambda node: node == {"id": "security_scan", "extends": "security_scan"},
        ),
    ],
    ids=["add_task", "set_field", "remove-task", "move_to_library"],
)
async def test_an_op_inside_an_added_node_edits_its_entry(st, batch, check):
    assert check(team(st, *batch).model["add"][0]["node"])


@pytest.mark.parametrize(
    ("pointer", "read"),
    [
        (
            [
                ("add_node", {"at": 10, "id": "scan_ok", "kind": "gate"}),
                ("set_field", {"path": "scan_ok", "field": "reject_to", "value": "security_scan"}),
            ],
            lambda m: next(e for e in m["add"] if e["node"]["id"] == "scan_ok")["node"][
                "reject_to"
            ],
        ),
        (
            [
                (
                    "set_field",
                    {"path": "local_review", "field": "reject_to", "value": "security_scan"},
                )
            ],
            lambda m: m["overrides"]["local_review"]["reject_to"],
        ),
    ],
    ids=["added-gate", "inherited-gate-override"],
)
async def test_renaming_an_added_node_renames_its_pointers(st, pointer, read):
    r = team(st, *pointer, ("rename", {"path": "security_scan", "id": "sast"}))
    assert read(r.model) == "sast"
    assert r.answers[-1]["updated"]


@pytest.mark.parametrize(
    "batch",
    [
        [
            ("add_node", {"at": 19, "id": "sign_off", "kind": "gate"}),
            ("set_field", {"path": "sign_off", "field": "chain_finalized", "value": True}),
        ],
        [("set_field", {"path": IMPLEMENT, "field": "policy.time_cap_minutes", "value": 500})],
    ],
    ids=["a-second-final-gate", "a-looser-policy"],
)
async def test_no_op_leaves_the_edits_not_applying(st, batch):
    assert "would leave team not resolving" in no(st, *batch)


async def test_a_new_chain_can_extend_a_chain(st):
    r = apply_ops(st, ("new_chain", {"extends": "default"}), key="mine")
    assert r.model == {"id": "mine", "extends": "default"}
    assert r.resolved_order == apply_ops(st).resolved_order


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"extends": "nope"}, "there is no chain 'nope' to extend"),
        ({"extends": "default", "from": "default"}, "not both"),
    ],
    ids=["unknown", "with-from"],
)
async def test_a_new_chain_cannot_extend_what_it_may_not(st, fields, error):
    assert error in refused(st, ("new_chain", fields), key="mine")


async def test_detach_writes_the_resolved_chain(st):
    before = team(st).resolved_order
    describe = ("set_field", {"path": "", "field": "description", "value": "Ours."})
    r = team(st, describe, ("detach", {}))
    assert r.answers == [None, {"detached_from": "default"}]
    assert set(r.model) == {"id", "description", "nodes"} and r.order == before
    assert r.model["description"] == "Ours."
    implement = r.nodes["implementation"]
    assert "extends" not in implement
    assert implement["steps"][0]["tasks"][0]["prompt"] == "Implement it carefully."
    assert r.resolved_order == before


TEAM2 = """id: team2
extends: team
overrides:
  spec.main.author: {prompt: Write the spec.}
"""
SCAN = {
    "id": "security_scan",
    "kind": "exec",
    "steps": [{"id": "main", "tasks": [{"id": "scan", "kind": "subprocess", "command": "true"}]}],
}


@pytest.mark.parametrize(
    ("key", "node", "expected"),
    [
        ("team", "verification", {"id": "verification", "extends": "verification"}),
        ("team", "security_scan", SCAN),
        ("team2", "verification", {"id": "verification", "extends": "verification"}),
    ],
    ids=["inherited", "added", "two-levels"],
)
async def test_detach_keeps_untouched_nodes_as_authored(st, key, node, expected):
    (st.templates_dir / "chains" / "team2.yaml").write_text(TEAM2)
    r = apply_ops(draft_state(st.templates_dir, st.db), ("detach", {}), key=key)
    assert r.nodes[node] == expected
    if key == "team2":
        # Touched one level down: written resolved, the override's prompt in it.
        spec_task = r.nodes["spec"]["steps"][0]["tasks"][0]
        assert spec_task["prompt"] == "Write the spec." and "extends" not in spec_task
        # Touched by `team`, the chain between: resolved too.
        implement = r.nodes["implementation"]["steps"][0]["tasks"][0]
        assert implement["prompt"] == "Implement it carefully."


FIT = {
    "kept": ["overrides implementation.main.implement"],
    "dropped": [
        {"key": "remove external_approval", "why": "quick-task has no node external_approval"},
        {"key": "add security_scan", "why": "quick-task has no node verification to add it after"},
    ],
}


async def test_a_chain_base_check_says_which_edits_the_new_parent_takes(st):
    assert team(st, ("chain_base_check", {"base": "quick-task"})).answers == [FIT]


#: An override quick-task has no path for: dropped with the rest.
NO_PATH = ("set_field", {"path": "verification", "field": "policy.time_cap_minutes", "value": 30})


@pytest.mark.parametrize(
    "first", [[], [NO_PATH]], ids=["as-filed", "with-an-override-it-cant-take"]
)
async def test_changing_a_chains_base_drops_the_edits_it_cant_take(st, first):
    r = team(st, *first, ("change_chain_base", {"base": "quick-task"}))
    dropped = [{"key": "overrides verification", "why": "quick-task has no verification"}]
    assert r.answers[-1] == {**FIT, "dropped": FIT["dropped"] + (dropped if first else [])}
    assert r.model == {
        "id": "team",
        "extends": "quick-task",
        "overrides": {IMPLEMENT: {"prompt": "Implement it carefully."}},
    }
    assert r.resolved_order == apply_ops(st, key="quick-task").resolved_order


@pytest.mark.parametrize(
    ("base", "error"),
    [
        ("team", "can't extend itself"),
        ("team2", "would be a cycle"),
        ("nope", "there is no chain 'nope' to extend"),
    ],
    ids=["itself", "cycle", "unknown"],
)
async def test_a_chains_base_is_one_it_may_extend(st, base, error):
    (st.templates_dir / "chains" / "team2.yaml").write_text(TEAM2)
    st = draft_state(st.templates_dir, st.db)
    for op in ("chain_base_check", "change_chain_base"):
        assert error in no(st, (op, {"base": base}))
