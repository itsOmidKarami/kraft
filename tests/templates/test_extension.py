"""Chains that extend a chain: the parent's nodes with `remove`, `overrides`
and `add` applied, refused when an edit no longer matches
(`an-extending-chain-is-its-parent-with-edits` and its neighbours)."""

from __future__ import annotations

import pytest
import yaml
from support.addons import AGENT, home, installed, instance
from support.templates import agent_task, write_chains

from kraft.addons import update
from kraft.config import write_yaml
from kraft.templates.library import TemplateLibrary, TemplateLibraryError

LIBRARY = {
    "tasks": {"base": agent_task(), "other": agent_task(prompt="other")},
    "nodes": {"stage": {"kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}},
}


def node(id: str, extends: str = "base", **kw) -> dict:
    return {"id": id, "kind": "exec", "tasks": [{"id": "t", "extends": extends}], **kw}


def gate(id: str, **kw) -> dict:
    return {"id": id, "kind": "gate", **kw}


#: Every edit below is made against this: two gates pointing back at `build`,
#: a final gate, and `after`, whose base-change restart names `ship`.
PARENT = {
    "id": "parent",
    "nodes": [
        node("build"),
        gate("review", reject_to="build"),
        node("ship"),
        gate("final", chain_finalized=True, reject_to="build"),
        node("after", on_base_changed={"restart_from": "ship"}),
    ],
}


def library(tmp_path, child: dict, *, parent: dict = PARENT, extra: dict | None = None):
    chains = {"parent": parent, "child": {"id": "child", **child}, **(extra or {})}
    return write_chains(tmp_path, LIBRARY, chains)


def resolve(tmp_path, child: dict, **kw):
    return library(tmp_path, child, **kw).resolve_chain("child")


def refused(tmp_path, child: dict, **kw) -> str:
    with pytest.raises(TemplateLibraryError) as exc:
        resolve(tmp_path, child, **kw)
    return str(exc.value)


def ids(resolved) -> list[str]:
    return [n.id for n in resolved.nodes]


def _task(resolved, path):
    return next(t.task for n in resolved.nodes for t in n.tasks() if t.path == path)


@pytest.mark.parametrize("case", ["local", "addon-chain", "two-levels"])
def test_an_extending_chain_is_its_parent_with_edits(tmp_path, case):
    edits = {
        "remove": ["after"],
        "add": [{"after": "build", "node": node("lint")}],
        "overrides": {"build.main.t": {"prompt": "built"}},
    }
    if case == "local":
        resolved = resolve(tmp_path, {"extends": "parent", **edits})
    elif case == "addon-chain":
        addon = installed(
            tmp_path,
            "acme",
            library={"tasks": {"base": AGENT}},
            chains={"default": {**PARENT, "id": "default"}},
        )
        root = home(tmp_path, chains={"child": {"extends": "acme:default", **edits}})
        resolved = TemplateLibrary.from_yaml_dir(root, addons=[addon]).resolve_chain("child")
    else:
        middle = {"id": "middle", "extends": "parent", "remove": ["after"], "add": edits["add"]}
        resolved = resolve(
            tmp_path,
            {"extends": "middle", "overrides": edits["overrides"]},
            extra={"middle": middle},
        )
    assert ids(resolved) == ["build", "lint", "review", "ship", "final"]
    assert _task(resolved, "build.main.t").prompt == "built"


def test_an_extending_chain_holds_no_nodes(tmp_path):
    assert "holds no 'nodes'" in refused(tmp_path, {"extends": "parent", "nodes": [node("x")]})


def test_edits_need_a_parent(tmp_path):
    message = refused(tmp_path, {"remove": ["x"], "nodes": [node("x")]})
    assert "'remove' edits a parent chain" in message


def test_a_node_the_parent_adds_reaches_the_extending_chain(tmp_path):
    library(tmp_path, {"extends": "parent", "remove": ["after"]})
    grown = {**PARENT, "nodes": [PARENT["nodes"][0], node("docs"), *PARENT["nodes"][1:]]}
    (tmp_path / "chains" / "parent.yaml").write_text(yaml.safe_dump(grown))
    resolved = TemplateLibrary.from_yaml_dir(tmp_path).resolve_chain("child")
    assert ids(resolved) == ["build", "docs", "review", "ship", "final"]


@pytest.mark.parametrize(
    ("edits", "why"),
    [
        ({"remove": ["ghost"]}, "remove 'ghost': parent has no node 'ghost'"),
        ({"add": [{"after": "ghost", "node": node("x")}]}, "add x: parent has no node 'ghost'"),
        (
            {"add": [{"after": "build", "node": node("x")}, {"after": "x", "node": node("y")}]},
            "add y: put it after 'build', below 'x'",
        ),
        ({"add": [{"after": "build", "node": node("ship")}]}, "already has a node 'ship'"),
        ({"overrides": {"build.main.ghost": {"prompt": "p"}}}, "has no 'build.main.ghost'"),
        (
            {"remove": ["after"], "overrides": {"after.main.t": {"prompt": "p"}}},
            "'after' is removed by this chain",
        ),
    ],
    ids=[
        "remove",
        "add-after",
        "add-after-an-added-node",
        "add-id-taken",
        "override-path",
        "override-in-a-removed-node",
    ],
)
def test_an_edit_that_does_not_match_is_refused(tmp_path, edits, why):
    assert why in refused(tmp_path, {"extends": "parent", **edits})


def test_added_nodes_follow_their_anchor_in_written_order(tmp_path):
    added = [
        {"after": "build", "node": node("a")},
        {"after": "review", "node": node("c")},
        {"after": "build", "node": node("b")},
    ]
    resolved = resolve(tmp_path, {"extends": "parent", "add": added})
    assert ids(resolved) == ["build", "a", "b", "review", "c", "ship", "final", "after"]


def test_an_extending_chain_keeps_the_final_gate(tmp_path):
    assert "final gate" in refused(tmp_path, {"extends": "parent", "remove": ["final"]})


@pytest.mark.parametrize("case", ["parent-has-one", "parent-has-none", "added-through-extends"])
def test_an_extending_chain_holds_one_final_gate(tmp_path, case):
    """`added-through-extends`: the added node takes `chain_finalized` from a
    library node, and a merge after the parent's review would run before the
    parent's final gate if Kraft let it in."""
    if case == "added-through-extends":
        fingate = {"kind": "gate", "chain_finalized": True}
        lib = {**LIBRARY, "nodes": {**LIBRARY["nodes"], "fingate": fingate}}
        merge = {"id": "m", "kind": "forge", "target": "mr.merge"}
        child = {
            "id": "child",
            "extends": "parent",
            "add": [
                {"after": "build", "node": {"id": "g2", "extends": "fingate"}},
                {"after": "review", "node": {"id": "merge", "kind": "exec", "tasks": [merge]}},
            ],
        }
        chains = {"parent": PARENT, "child": child}
        with pytest.raises(TemplateLibraryError) as exc:
            write_chains(tmp_path, lib, chains).resolve_chain("child")
        assert "add g2: the chain already has a final gate" in str(exc.value)
        assert exc.value.loc == ("add", 0, "node")
        return
    parent = (
        PARENT
        if case == "parent-has-one"
        else {**PARENT, "nodes": [n for n in PARENT["nodes"] if n["id"] != "final"]}
    )
    child = {
        "extends": "parent",
        "add": [{"after": "after", "node": gate("second", chain_finalized=True)}],
    }
    if case == "parent-has-one":
        assert "already has a final gate" in refused(tmp_path, child, parent=parent)
    else:
        assert ids(resolve(tmp_path, child, parent=parent))[-1] == "second"


@pytest.mark.parametrize(
    "case",
    [
        "task-profile-displaces-model",
        "fix-loop-limit",
        "list-replaces",
        "tasks-replaces-steps",
        "replacement-list-is-expanded",
    ],
)
def test_an_override_merges_like_extends(tmp_path, case):
    looped = node(
        "check",
        fix_loop={
            "tasks": [{"id": "repair", "extends": "base"}],
            "judge": {"id": "judge", "extends": "base"},
            "max_attempts": 2,
        },
    )
    staged = {
        "id": "staged",
        "kind": "exec",
        "steps": [{"id": "s", "tasks": [{"id": "t", "extends": "base"}]}],
    }
    modelled = {**PARENT["nodes"][0], "tasks": [{"id": "t", "extends": "base", "model": "m"}]}
    parent = {**PARENT, "nodes": [modelled, looped, staged, *PARENT["nodes"][1:]]}
    replacement = {"tasks": [{"id": "u", "extends": "other"}]}
    overrides = {
        "task-profile-displaces-model": {"build.main.t": {"profile": "deep"}},
        "fix-loop-limit": {"check": {"fix_loop": {"max_attempts": 4}}},
        "list-replaces": {"build": replacement},
        "tasks-replaces-steps": {"staged": replacement},
        "replacement-list-is-expanded": {"build": replacement},
    }[case]
    lib = library(tmp_path, {"extends": "parent", "overrides": overrides}, parent=parent)
    resolved = lib.resolve_chain("child")
    paths = {n.id: [t.path for t in n.tasks()] for n in resolved.nodes}
    if case == "task-profile-displaces-model":
        task = _task(resolved, "build.main.t")
        assert (task.profile, task.model) == ("deep", None)
    elif case == "fix-loop-limit":
        check = next(n for n in resolved.nodes if n.id == "check")
        assert check.node.fix_loop.max_attempts == 4
    elif case == "list-replaces":
        assert paths["build"] == ["build.main.u"]
    elif case == "tasks-replaces-steps":
        assert paths["staged"] == ["staged.main.u"]
    else:
        assert _task(resolved, "build.main.u").prompt == "other"
        assert "tasks.other" in lib.references("child")["build"]
        assert "tasks.base" not in lib.references("child")["build"]


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"build": {"id": "x"}}, "cannot set id"),
        ({"build": {"kind": "gate"}}, "cannot set kind"),
        ({"build.main.t": {"extends": "other"}}, "cannot set extends"),
        ({"review": {"chain_finalized": True}}, "cannot set chain_finalized"),
        (
            {"build": {"fix_loop": {"judge": {"extends": "other"}}}},
            "cannot set fix_loop.judge.extends",
        ),
        (
            {
                "build": {"tasks": [{"id": "u", "extends": "other"}]},
                "build.main.t": {"prompt": "p"},
            },
            "sits under overrides build",
        ),
    ],
    ids=["id", "kind", "extends", "chain_finalized", "nested-extends", "under-a-replaced-list"],
)
def test_an_override_cannot_change_identity(tmp_path, overrides, why):
    assert why in refused(tmp_path, {"extends": "parent", "overrides": overrides})


@pytest.mark.parametrize(
    ("child", "why"),
    [
        ({"remove": ["build"]}, "review's reject_to names 'build', which this chain removes"),
        ({"remove": ["ship"]}, "after's restart_from names 'ship', which this chain removes"),
        (
            {
                "remove": ["ship"],
                "overrides": {"after": {"on_base_changed": {"restart_from": "after"}}},
            },
            None,
        ),
    ],
    ids=["reject_to", "restart_from", "re-pointed-by-override"],
)
def test_a_removed_pointer_target_is_named(tmp_path, child, why):
    if why is None:
        assert "ship" not in ids(resolve(tmp_path, {"extends": "parent", **child}))
    else:
        assert why in refused(tmp_path, {"extends": "parent", **child})


@pytest.mark.parametrize("case", ["inline-parent-node", "library-node"])
def test_an_inherited_error_names_the_file_that_defined_it(tmp_path, case):
    """An override that breaks a component the parent's file (or the library)
    defined names both: the child's override, and where the definition lives."""
    inline = {"id": "solo", "kind": "exec", "tasks": [{"id": "t", **agent_task()}]}
    parent = {**PARENT, "nodes": [*PARENT["nodes"], inline]}
    path = "solo.main.t" if case == "inline-parent-node" else "build.main.t"
    child = {"extends": "parent", "overrides": {path: {"prompt": 5}}}
    lib = library(tmp_path, child, parent=parent)
    with pytest.raises(TemplateLibraryError) as exc:
        lib.resolve_chain("child")
    defined = (
        "chains/parent.yaml: chain parent"
        if case == "inline-parent-node"
        else "library.yaml: tasks.base"
    )
    assert f"child.yaml: {path}.prompt <- " in str(exc.value)
    assert defined in str(exc.value)
    assert exc.value.loc == ("overrides", path)
    # The key path in the file that defined it: the parent chain's own nodes
    # list (index 5 is `solo`), or the library section.
    where = (
        ("nodes", 5, "tasks", 0, "prompt")
        if case == "inline-parent-node"
        else ("tasks", "base", "prompt")
    )
    assert exc.value.related[1] == where


@pytest.mark.parametrize("case", ["bad-edit", "parent-broken"])
def test_an_extending_chain_fails_alone(tmp_path, case):
    broken = {**PARENT, "nodes": [node("build", extends="ghost")]}
    chains = {
        "parent": PARENT if case == "bad-edit" else broken,
        "child": {
            "id": "child",
            "extends": "parent",
            "remove": ["nope" if case == "bad-edit" else "build"],
        },
        "sibling": {"id": "sibling", "nodes": [node("n")]},
    }
    write_chains(tmp_path, LIBRARY, chains)
    report = TemplateLibrary.lint_dir(tmp_path)
    child = [i for i in report.issues if i.chain == "child"]
    assert len(child) == 1
    assert "sibling" in report.chains
    if case == "parent-broken":
        assert "extends 'parent', which does not resolve" in child[0].message
        assert "ghost" not in child[0].message
    else:
        assert "parent" in report.chains


@pytest.mark.parametrize(
    ("edits", "outcome"),
    [
        ({"policy": {"deny_tools": ["Write"]}}, (None, {"deny_tools": {"Bash", "Write"}})),
        ({"policy": {"token_budget": 5000}}, "token_budget: 5000 cannot exceed the parent's 1000"),
        ({"policy": {"allowed_tools": ["Read"]}}, (None, {"allowed_tools": {"Read"}})),
        (
            {"policy": {"allowed_tools": ["Read", "Bash"]}},
            "cannot widen the inherited safety ceiling",
        ),
        (
            {"policy": {"deny_tools": None, "token_budget": None}},
            "policy.deny_tools: cannot clear the parent's deny_tools",
        ),
        (
            {"overrides": {"build": {"policy": {"deny_tools": []}}}},
            ("build", {"deny_tools": {"Bash"}}),
        ),
    ],
    ids=[
        "drops-a-deny",
        "raises-a-cap",
        "narrows",
        "widens-allowed-tools",
        "null-drops-a-limit",
        "override-loosens-a-node",
    ],
)
def test_an_extending_chains_policy_only_tightens(tmp_path, edits, outcome):
    own = {"deny_tools": ["Bash"], "token_budget": 1000, "allowed_tools": ["Read", "Edit"]}
    guarded = {**PARENT["nodes"][0], "policy": {"deny_tools": ["Bash"]}}
    parent = {**PARENT, "policy": own, "nodes": [guarded, *PARENT["nodes"][1:]]}
    child = {"extends": "parent", **edits}
    if isinstance(outcome, str):
        assert outcome in refused(tmp_path, child, parent=parent)
        return
    where, expected = outcome
    resolved = resolve(tmp_path, child, parent=parent)
    policy = (
        resolved.chain.policy
        if where is None
        else next(n for n in resolved.nodes if n.id == where).node.policy
    )
    for field, values in expected.items():
        assert set(getattr(policy, field)) == values


def test_an_extending_chain_pins_its_ancestors(tmp_path):
    """Nothing in the add-on's chain names `acme`: only the chain it is does."""
    inline = {"nodes": [{"id": "n", "kind": "exec", "tasks": [{**AGENT, "id": "t"}]}]}
    addon = installed(tmp_path, "acme", library={"tasks": {}}, chains={"inline": inline})
    chains = {"middle": {"extends": "acme:inline"}, "child": {"extends": "middle"}}
    root = home(tmp_path, chains=chains)
    resolved = TemplateLibrary.from_yaml_dir(root, addons=[addon]).resolve_chain("child")
    assert list(resolved.addons) == ["acme"]


def test_a_resolved_node_says_where_it_came_from(tmp_path):
    lib = library(
        tmp_path,
        {
            "extends": "parent",
            "remove": ["after"],
            "add": [{"after": "build", "node": node("lint")}],
            "overrides": {"build.main.t": {"prompt": "built"}},
        },
        extra={"grandchild": {"id": "grandchild", "extends": "child"}},
    )
    assert lib.provenance("child") == {
        "parent": "parent",
        "nodes": {
            "build": "overridden",
            "lint": "added",
            "review": "inherited",
            "ship": "inherited",
            "final": "inherited",
        },
        "overrides": {"build": ["build.main.t"]},
        "removed": ["after"],
    }
    assert lib.provenance("parent") is None
    assert lib.extended_by("parent") == ("child", "grandchild")
    assert lib.extended_by("child") == ("grandchild",)


def test_a_local_chain_cannot_extend_a_repo_library_chain(tmp_path):
    config_dir, _ = instance(tmp_path, chains={"team": {"extends": "app:default"}})
    write_yaml(config_dir / "repos.yaml", {"repos": [{"path": "/r", "id": "app"}]})
    [(file, why)] = update.scope_problems(config_dir)
    assert file == "chains/team.yaml"
    assert "extends app:default is in app's repo library" in why
