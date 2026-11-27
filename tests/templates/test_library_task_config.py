"""Each layer a chain is resolved from is read in the `config:` form before
layers merge (`flat-forge-keys-read-as-config`)."""

from __future__ import annotations

import logging

import pytest
from support.addons import example_tree, home, installed

from kraft.templates.library import TemplateLibrary, TemplateLibraryError

FLAT = {
    "kind": "forge",
    "target": "mr.automated_review",
    "watch_ci": True,
    "reviewers": [{"bot": "a"}],
    "wait": {"grace_period": "30s"},
}
NESTED = {
    "kind": "forge",
    "target": "mr.automated_review",
    "config": {"watch_ci": True, "reviewers": [{"bot": "a"}], "grace_period": "30s"},
}
CHILD_FLAT = {"reviewers": [{"bot": "b"}]}
CHILD_NESTED = {"config": {"reviewers": [{"bot": "b"}]}}
B = (["b"], True, 30)


def _chain(task: dict) -> dict:
    """The review task `r` in step `s0`, and the CI wait `watch_ci` needs in `s1`."""
    return {
        "nodes": [
            {
                "id": "n",
                "kind": "exec",
                "steps": [
                    {"id": "s0", "tasks": [{"id": "r", **task}]},
                    {"id": "s1", "tasks": [{"id": "ci", "kind": "forge", "target": "mr.ci"}]},
                ],
            }
        ]
    }


def _instance(tmp_path, parent, child):
    """Parent in the local library, child in a local chain (`_section`, `_add_chain`)."""
    root = home(
        tmp_path,
        library={"tasks": {"base": parent}},
        chains={"c": _chain({"extends": "base", **child})},
    )
    return TemplateLibrary.from_yaml_dir(root).resolve_chain("c")


def _addon(tmp_path, parent, child):
    """Parent in an add-on's library (`_add_addon` -> `_section`)."""
    addon = installed(tmp_path, "acme", library={"tasks": {"base": parent}})
    root = home(tmp_path, chains={"c": _chain({"extends": "acme:base", **child})})
    return TemplateLibrary.from_yaml_dir(root, addons=[addon]).resolve_chain("c")


def _child_addon(tmp_path, parent, child):
    """Parent in the root of a child add-on's tree, read through the child's
    namespace (`_add_inherited` -> `_section`), as
    `test_library_inheritance._override_replaces` reads one."""
    tree = example_tree(tmp_path, company={"library": {"tasks": {"base": parent}}})
    root = home(tmp_path, chains={"c": _chain({"extends": "team-payments:base", **child})})
    return TemplateLibrary.from_yaml_dir(root, addons=list(tree)).resolve_chain("c")


def _inherited_chain(tmp_path, parent, child):
    """Child in a chain the tree's root ships, resolved through the child add-on
    (`_register_layer`): only a child over a parent shows a missing lift there."""
    tree = example_tree(
        tmp_path,
        company={
            "library": {"tasks": {"base": parent}},
            "chains": {"review": _chain({"extends": "base", **child})},
        },
    )
    library = TemplateLibrary.from_yaml_dir(home(tmp_path), addons=list(tree))
    return library.resolve_chain("team-payments:review")


def _overrides(tmp_path, parent, child):
    """Parent as a chain's own task, child as an extending chain's `overrides:`."""
    root = home(
        tmp_path,
        chains={
            "parent": _chain(parent),
            "c": {"extends": "parent", "overrides": {"n.s0.r": child}},
        },
    )
    return TemplateLibrary.from_yaml_dir(root).resolve_chain("c")


def _review(chain):
    task = next(t.task for s in chain.nodes[0].steps for t in s.tasks if t.task.id == "r")
    return [r.bot for r in task.reviewers], task.watch_ci, task.grace.total_seconds()


@pytest.mark.parametrize(
    ("source", "parent", "child", "want"),
    [
        (_instance, FLAT, CHILD_NESTED, B),
        (_instance, NESTED, CHILD_FLAT, B),
        (_addon, FLAT, CHILD_NESTED, B),
        (_addon, NESTED, CHILD_FLAT, B),
        (_child_addon, FLAT, CHILD_NESTED, B),
        (_inherited_chain, NESTED, CHILD_FLAT, B),
        (_overrides, FLAT, CHILD_NESTED, B),
        (_overrides, NESTED, CHILD_FLAT, B),
        (_instance, NESTED, {"watch_ci": False}, (["a"], False, 30)),
    ],
    ids=[
        "instance-flat-parent",
        "instance-flat-child",
        "addon-flat-parent",
        "addon-flat-child",
        "child-addon-flat-parent",
        "inherited-chain-flat-child",
        "overrides-flat-parent",
        "overrides-flat-child",
        "a-flat-false-clears-an-inherited-true",
    ],
)
def test_every_layer_is_lifted_before_layers_merge(tmp_path, source, parent, child, want):
    """`flat-forge-keys-read-as-config`: without its seam's lift, a merged task
    holds a flat key beside the same key under `config` and is refused."""
    assert _review(source(tmp_path, parent, child)) == want


@pytest.mark.parametrize("cleared", [True, False], ids=["cleared", "not-cleared"])
def test_a_retargeting_child_clears_its_parents_config(tmp_path, cleared):
    parent = {"kind": "forge", "target": "ci.pipeline", "pipeline": "1"}
    child = {
        "extends": "base",
        "target": "mr.automated_review",
        **({"pipeline": None} if cleared else {}),
    }
    root = home(tmp_path, library={"tasks": {"base": parent}}, chains={"c": _chain(child)})
    library = TemplateLibrary.from_yaml_dir(root)
    if not cleared:
        with pytest.raises(TemplateLibraryError, match="not an option of mr.automated_review"):
            library.resolve_chain("c")
        return
    task = next(t.task for s in library.resolve_chain("c").nodes[0].steps for t in s.tasks)
    assert (task.target.value, task.pipeline) == ("mr.automated_review", None)


def test_a_flat_file_warns_naming_the_file_and_the_path(tmp_path, caplog, warnings_afresh):
    flat = home(tmp_path / "flat", library={"tasks": {"base": FLAT}})
    with caplog.at_level(logging.WARNING):
        TemplateLibrary.from_yaml_dir(flat)
    assert (
        f"{flat / 'library.yaml'}: tasks.base.reviewers is deprecated; write it as config.reviewers"
        in caplog.text
    )
    caplog.clear()
    nested = home(tmp_path / "nested", library={"tasks": {"base": NESTED}})
    with caplog.at_level(logging.WARNING):
        TemplateLibrary.from_yaml_dir(nested)
    assert "deprecated" not in caplog.text
