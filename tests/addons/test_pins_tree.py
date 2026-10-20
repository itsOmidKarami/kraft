"""A work item that reads a child add-on keeps its whole tree: its pins
carry every ancestor, root first, and a launch reads each pinned version,
whatever the instance has taken since."""

import json

import pytest
import yaml
from support.addons import (
    AGENT,
    TREE,
    addon_json,
    chain,
    drop_store,
    edit_addons_yaml,
    edit_store,
    instance,
    launch_task,
    publish,
    remove_from_store,
    resolve_chain,
    store_of,
    stored_snapshot,
    tree_collection,
    update_addons,
)

from kraft.adapters import agent
from kraft.addons import load
from kraft.addons.manifest import ADDON_JSON
from kraft.executor import dispatch
from kraft.templates.library import TemplateLibrary


@pytest.fixture
def tree_home(tmp_path, monkeypatch):
    """A Kraft home, this process's own, with the example tree (`TREE`)
    installed root first from one collection `acme`. Company also ships a
    chain `default` running its `security_scan`, which `team-payments`
    inherits, and its `slow` falls back to its own `fast` by name: a bare
    `profile:` in an add-on is the instance's, which this one lacks. The
    other two ship an empty `library.yaml`: an install refuses an add-on that
    would add nothing."""
    company = {
        "chains": {"default": chain("security_scan")},
        "profiles": {
            "slow": {
                "model": {"codex": "company-model"},
                "fallback": [{"profile": "company:fast"}],
            },
            "fast": {"model": {"codex": "fast-model"}},
        },
    }
    repo = tree_collection(tmp_path, company=company, dept={"library": {}}, team={"library": {}})
    config_dir, addons_dir = instance(tmp_path, {"acme": {"git": repo.as_uri()}})
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(addons_dir.parent))
    for addon_id in TREE:
        update_addons(config_dir, addons_dir, [addon_id], install=True)
    return repo, config_dir, addons_dir


def test_a_launch_reads_an_inherited_skill_under_the_childs_name(tree_home):
    """A local task names the skill team-payments inherits from company by
    the team's name: the launch injects company's file."""
    repo, config_dir, addons_dir = tree_home
    skill = "team-payments:deploy-review"
    body = {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", **AGENT, "skill": skill}]}]}
    (config_dir / "chains" / "outside.yaml").write_text(json.dumps(body))
    resolved = resolve_chain(config_dir, addons_dir, "outside")

    inv = launch_task(resolved.addons, skill=skill)

    assert "company method" in inv.method_text


def _write_chain(config_dir, name, **task):
    """A local one-node chain `name` whose single task is an agent task with
    `task`'s fields."""
    body = {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", **AGENT, **task}]}]}
    (config_dir / "chains" / f"{name}.yaml").write_text(json.dumps(body))


def _fast_falls_back_to(config_dir, profile):
    """`harnesses.yaml` gains a local profile `fast` that falls back to `profile`."""
    harnesses = yaml.safe_load((config_dir / "harnesses.yaml").read_text()) or {}
    fast = {"model": {"codex": "gpt-5"}, "fallback": [{"profile": profile}]}
    harnesses["profiles"] = {**(harnesses.get("profiles") or {}), "fast": fast}
    (config_dir / "harnesses.yaml").write_text(yaml.safe_dump(harnesses))


_WHOLE_TREE = ["company", "platform-dept", "team-payments"]

#: The skill team-payments inherits from company, named by the team's name.
_SKILL = "team-payments:deploy-review"

_TAMPERED = (
    ".kraft/addon.json does not match its digest; the store was changed after it was installed"
)


@pytest.mark.parametrize(
    ("case", "chain_id", "expected"),
    [
        pytest.param("child-chain", "team-payments:default", _WHOLE_TREE, id="child-chain"),
        pytest.param("outside-extends", "outside", _WHOLE_TREE, id="outside-extends"),
        pytest.param("fallback", "quick", _WHOLE_TREE, id="fallback-into-inherited-profile"),
        pytest.param("root-only", "company:default", ["company"], id="root-only"),
        pytest.param(
            "child-first", "team-payments:default", _WHOLE_TREE, id="lock-lists-the-child-first"
        ),
    ],
)
def test_an_item_pins_its_whole_tree(tree_home, case, chain_id, expected):
    """A chain that reads a child add-on pins every ancestor too, root first,
    and each child's pin names its parent. A root's pin is what it always was."""
    _repo, config_dir, addons_dir = tree_home
    locked = load.installed(config_dir, addons_dir)
    if case == "outside-extends":
        # Names only the child; the dept is read through it and named nowhere.
        (config_dir / "chains" / "outside.yaml").write_text(
            json.dumps(chain("team-payments:security_scan"))
        )
    elif case == "fallback":
        _fast_falls_back_to(config_dir, "team-payments:slow")
        _write_chain(config_dir, "quick", profile="fast")
    elif case == "child-first":
        locked = tuple(reversed(locked))
    library = TemplateLibrary.from_yaml_dir(config_dir, addons=locked)

    pins = library.resolve_chain(chain_id).addons

    assert list(pins) == expected
    by_namespace = {a.namespace: a for a in locked}
    # The ancestors' digests are what GC keeps (D11).
    assert pins == {n: by_namespace[n].pin for n in expected}
    assert pins["company"] == {
        "id": "company@acme",
        "commit": by_namespace["company"].commit,
        "tree": by_namespace["company"].tree,
        "source": "./addons/company",
        "digest": f"sha256:{by_namespace['company'].root.name}",
        "version": "1.0.0",
    }
    if "team-payments" in expected:
        assert pins["team-payments"]["parent"] == "platform-dept@acme"
        assert pins["platform-dept"]["parent"] == "company@acme"


@pytest.mark.parametrize(
    "case",
    [
        "launch-reads-pinned-ancestor",
        "for-item-tree-order",
        "restore-an-ancestor",
        "pin-without-parent",
        "child-disabled-since",
        "pin-parent-wins-over-its-store",
        "pin-without-parent-manifest-tampered",
    ],
)
async def test_an_item_keeps_its_tree_across_a_parent_update(tree_home, case):
    """An item that read `team-payments` keeps the company version it started
    with after company moves to 1.1.0, which the team's range `1` accepts: its
    pins carry the whole tree, a launch reads the pinned ancestor, and a pinned
    ancestor whose store is gone leaves its descendants out with its reason
    until it is restored."""
    repo, config_dir, addons_dir = tree_home
    resolved = resolve_chain(config_dir, addons_dir, "team-payments:default")
    pins = resolved.addons
    skill = next(iter(resolved.nodes[0].tasks())).task.skill
    old = store_of(config_dir, addons_dir, "company")
    publish(repo, "company", version="1.1.0", skills={"deploy-review": "company 1.1.0 method"})
    update_addons(config_dir, addons_dir, ["company@acme"])

    if case == "launch-reads-pinned-ancestor":
        started, filed_now = launch_task(pins, skill=skill), launch_task(None, skill=skill)
        assert "company method" in started.method_text
        assert "company 1.1.0 method" in filed_now.method_text
        return
    if case == "restore-an-ancestor":
        drop_store(old)
        reason = (
            "its parent platform-dept@acme is not loaded: its parent company@acme is not "
            "loaded: the version this work item started with is no longer in the store"
        )
        with pytest.raises(agent.HarnessUnavailable) as stop:
            # Reads only the team; its pinned grandparent is what is gone.
            launch_task(pins, fallback=[{"profile": "team-payments:slow"}])
        assert str(stop.value) == f"add-on team-payments@acme 1.0.0 could not be read: {reason}"

        await dispatch.restore_pins({"materialized_chain": stored_snapshot(resolved)})

        assert "company method" in launch_task(pins, skill=skill).method_text
        return
    expected = [
        ("company", None, None),
        ("platform-dept", "company@acme", None),
        ("team-payments", "platform-dept@acme", None),
    ]
    if case == "for-item-tree-order":
        pins = dict(reversed(pins.items()))
    elif case == "pin-without-parent":
        # A snapshot stored before pins recorded parents.
        pins = {n: {k: v for k, v in p.items() if k != "parent"} for n, p in pins.items()}
    elif case == "pin-without-parent-manifest-tampered":
        # An old snapshot whose child's manifest no longer reads: never a root.
        pins = {n: {k: v for k, v in p.items() if k != "parent"} for n, p in pins.items()}
        edit_store(store_of(config_dir, addons_dir, "team-payments"), ADDON_JSON, "{}")
        # A root now, so it reads before the dept; left out all the same.
        expected[1:] = [("team-payments", None, _TAMPERED), expected[1]]
    elif case == "pin-parent-wins-over-its-store":
        # The pins name the parents: the stores' manifests are never asked.
        team = addon_json("team-payments", requires={"addons": {"company@acme": "1"}})
        edit_store(store_of(config_dir, addons_dir, "team-payments"), ADDON_JSON, json.dumps(team))
        remove_from_store(store_of(config_dir, addons_dir, "platform-dept"), ADDON_JSON)
    else:
        edit_addons_yaml(config_dir, lambda w: w["addons"].update({TREE[2]: False}))

    known = load.for_item(pins, config_dir, addons_dir, repo_id=None)

    tree = [p for p in known if p.namespace in pins]
    assert [(p.namespace, p.parent, p.left_out) for p in tree] == expected
    assert not any(p.quiet for p in tree)
    if any(left_out for *_, left_out in expected):
        # Left out by a fault, not built as a root: a launch that reads it stops.
        with pytest.raises(agent.HarnessUnavailable, match=_TAMPERED):
            launch_task(pins, skill=_SKILL)


def test_a_pinned_addon_wins_over_a_live_one_of_its_id(tree_home):
    """The instance moved team-payments to 1.1.0 under a new alias `pay`
    after intake, so the item's add-ons hold its id twice: pinned, and live
    under `pay`. Every lookup by id (`inherit.ancestry`, the library's
    builds) takes the last of an id, so the pinned one comes last, wherever
    the parents-first order put the live one. Here the pin is an old one
    whose store is gone, a root by its pin, while the live one is a
    grandchild."""
    repo, config_dir, addons_dir = tree_home
    pins = resolve_chain(config_dir, addons_dir, "team-payments:default").addons
    pins = {n: {k: v for k, v in p.items() if k != "parent"} for n, p in pins.items()}
    old = store_of(config_dir, addons_dir, "team-payments")
    requires = {"kraft": "2", "harnesses": ["codex"], "addons": {TREE[1]: "1"}}
    publish(repo, "team-payments", version="1.1.0", manifest={"requires": requires})
    update_addons(config_dir, addons_dir, [TREE[2]])
    lock = yaml.safe_load((config_dir / "addons.lock").read_text())
    lock["addons"][TREE[2]]["namespace"] = "pay"
    (config_dir / "addons.lock").write_text(yaml.safe_dump(lock))
    drop_store(old)

    known = load.for_item(pins, config_dir, addons_dir, repo_id=None)

    team = [(a.namespace, a.version) for a in known if a.id == TREE[2]]
    assert sorted(team) == [("pay", "1.1.0"), ("team-payments", "1.0.0")]
    assert team[-1] == ("team-payments", "1.0.0")
