"""A work item that reads a child add-on keeps its whole tree: its pins
carry every ancestor, root first, and a launch reads each pinned version,
whatever the instance has taken since."""

import json
import re

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
    task_node,
    tree_collection,
    update_addons,
)

from kraft.adapters import agent
from kraft.addons import load
from kraft.addons.manifest import ADDON_JSON
from kraft.drafts import item as item_draft
from kraft.executor import dispatch
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import MaterializedChain
from kraft.templates.revision import Add, AddedNode, ChangeSet, RevisionError, revise

#: The skill team-payments inherits from company, named by the team's name.
_SKILL = "team-payments:deploy-review"
#: A local agent task that reads company only through team-payments.
_READS_SKILL = {**AGENT, "skill": _SKILL}


@pytest.fixture
def tree_home(tmp_path, monkeypatch):
    """A Kraft home, this process's own, with the example tree (`TREE`)
    installed root first from the collection `acme`: `(collection repo,
    config dir, run/addons)`. Company ships the task `security_scan` (skill
    `deploy-review`, "company method"), a chain `default` that runs it, and
    the profiles `slow` (model `o3`, falling back to `company:fast`) and
    `fast` (`gpt-5`), both models the codex harness takes; team-payments
    inherits all of it. Dept and team ship an empty `library.yaml`: an
    install refuses an add-on that would add nothing. The local chain
    `inherited-skill` is one agent task naming `team-payments:deploy-review`
    (`_SKILL`), so it reads company only through the child."""
    company = {
        "chains": {"default": chain("security_scan")},
        "profiles": {
            "slow": {
                "model": {"codex": "o3"},
                "fallback": [{"profile": "company:fast"}],
            },
            "fast": {"model": {"codex": "gpt-5"}},
        },
    }
    repo = tree_collection(tmp_path, company=company, dept={"library": {}}, team={"library": {}})
    reads_skill = {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", **_READS_SKILL}]}]}
    config_dir, addons_dir = instance(
        tmp_path, {"acme": {"git": repo.as_uri()}}, chains={"inherited-skill": reads_skill}
    )
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(addons_dir.parent))
    for addon_id in TREE:
        update_addons(config_dir, addons_dir, [addon_id], install=True)
    return repo, config_dir, addons_dir


def test_a_launch_reads_an_inherited_skill_under_the_childs_name(tree_home):
    """A local task names the skill team-payments inherits from company by
    the team's name: the launch injects company's file."""
    repo, config_dir, addons_dir = tree_home
    resolved = resolve_chain(config_dir, addons_dir, "inherited-skill")

    inv = launch_task(resolved.addons, skill=_SKILL)

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

_TAMPERED = (
    ".kraft/addon.json does not match its digest; the store was changed after it was installed"
)


@pytest.mark.parametrize(
    ("chain_id", "expected"),
    [
        pytest.param("team-payments:default", _WHOLE_TREE, id="child-chain"),
        pytest.param("outside", _WHOLE_TREE, id="outside-extends"),
        pytest.param("inherited-skill", _WHOLE_TREE, id="outside-skill"),
        pytest.param("quick", _WHOLE_TREE, id="fallback-into-inherited-profile"),
        pytest.param("company:default", ["company"], id="root-only"),
        pytest.param("team-payments:default", _WHOLE_TREE, id="lock-lists-the-child-first"),
    ],
)
def test_an_item_pins_its_whole_tree(request, tree_home, chain_id, expected):
    """A chain that reads a child add-on pins every ancestor too, root first,
    and each child's pin names its parent. A root's pin is what it always was."""
    _repo, config_dir, addons_dir = tree_home
    case = request.node.callspec.id
    locked = load.installed(config_dir, addons_dir)
    if case == "outside-extends":
        # Names only the child; the dept is read through it and named nowhere.
        (config_dir / "chains" / "outside.yaml").write_text(
            json.dumps(chain("team-payments:security_scan"))
        )
    elif case == "fallback-into-inherited-profile":
        _fast_falls_back_to(config_dir, "team-payments:slow")
        _write_chain(config_dir, "quick", profile="fast")
    elif case == "lock-lists-the-child-first":
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
    if case == "fallback-into-inherited-profile":
        # The profile it falls back to is company's, read under the child's name.
        assert launch_task(pins, profile="team-payments:slow").model == "o3"


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
        "launch-names-the-pinned-parent-that-is-gone",
    ],
)
async def test_an_item_keeps_its_tree_across_a_parent_update(tree_home, case):
    """An item that read `team-payments` keeps the company version it started
    with after company moves to 1.1.0, which the team's range `1` accepts: its
    pins carry the whole tree, a launch reads the pinned ancestor, and a pinned
    ancestor whose store is gone leaves its descendants out with its reason
    until it is restored. Each pinned child builds on the parent its pin
    names; an older pin with none reads its store's manifest, and one whose
    manifest does not read is left out with why."""
    repo, config_dir, addons_dir = tree_home
    resolved = resolve_chain(config_dir, addons_dir, "team-payments:default")
    pins = resolved.addons
    skill = next(iter(resolved.nodes[0].tasks())).task.skill
    # Filed beside it: a task that reads company only through the child.
    reads_skill = resolve_chain(config_dir, addons_dir, "inherited-skill").addons
    old = store_of(config_dir, addons_dir, "company")
    publish(repo, "company", version="1.1.0", skills={"deploy-review": "company 1.1.0 method"})
    update_addons(config_dir, addons_dir, ["company@acme"])

    if case == "launch-reads-pinned-ancestor":
        # Only the pins' ancestor closure keeps company 1.0.0 for it.
        started = launch_task(reads_skill, skill=_SKILL)
        filed_now = launch_task(None, skill=_SKILL)
        assert "company method" in started.method_text
        assert "company 1.1.0 method" in filed_now.method_text
        return
    if case == "launch-names-the-pinned-parent-that-is-gone":
        drop_store(store_of(config_dir, addons_dir, "platform-dept"))
        with pytest.raises(agent.HarnessUnavailable) as stop:
            launch_task(reads_skill, skill=_SKILL)
        assert str(stop.value) == (
            "add-on team-payments@acme 1.0.0 could not be read: its parent platform-dept@acme "
            "is not loaded: the version this work item started with is no longer in the store"
        )
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
    grandchild. The live one is left out (another version of a pinned id),
    but it is still listed, and still before the pinned one."""
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


#: Local library tasks a revision can add: one reading the team's inherited
#: skill, one on the local profile `fast` (`_fast_falls_back_to`).
_LOCAL_TASKS = {"tasks": {"reads_skill": _READS_SKILL, "quick": {**AGENT, "profile": "fast"}}}

_MIXED = (
    "team-payments@acme 1.0.0 builds on company@acme 1.1.0, and this work item runs "
    "company@acme 1.0.0; a work item never mixes versions of one add-on tree"
)


def _company_only_item(
    repo, config_dir, addons_dir, *, update="1.1.0", filed_on="company:default", moved=TREE[0]
):
    """An item filed on `filed_on`, by default `company:default`, so it pins
    company only, with the local tasks `_LOCAL_TASKS` and `fast` falling back
    to `team-payments:slow`; then, unless `update` is None, a new `moved`
    (company by default) taken at that version, which its children's range
    `1` accepts, so their locks do not move (`1.0.0`: new files under the
    same version). `<version> as <alias>` also moves it to the namespace
    `alias` in the lock. Returns the snapshot and the live library."""
    (config_dir / "library.yaml").write_text(json.dumps(_LOCAL_TASKS))
    _fast_falls_back_to(config_dir, "team-payments:slow")
    resolved = resolve_chain(config_dir, addons_dir, filed_on)
    assert list(resolved.addons) == _WHOLE_TREE[: TREE.index(moved) + 1]
    if update:
        version, _, alias = update.partition(" as ")
        name = moved.partition("@")[0]
        if moved == TREE[0]:
            publish(repo, name, version=version, skills={"deploy-review": f"{update} method"})
        else:
            # A child's manifest is rewritten whole: it keeps its parent.
            parent = TREE[TREE.index(moved) - 1]
            requires = {"kraft": "2", "harnesses": ["codex"], "addons": {parent: "1"}}
            publish(repo, name, version=version, manifest={"requires": requires})
        update_addons(config_dir, addons_dir, [moved], re_install=version == "1.0.0")
        if alias:
            lock = yaml.safe_load((config_dir / "addons.lock").read_text())
            lock["addons"][moved]["namespace"] = alias
            (config_dir / "addons.lock").write_text(yaml.safe_dump(lock))
    live = TemplateLibrary.from_yaml_dir(config_dir, addons=load.installed(config_dir, addons_dir))
    return MaterializedChain.from_json(stored_snapshot(resolved)), live


def _adding(node):
    """A change set adding `node` after `n`."""
    add = Add(after="n", node=AddedNode.model_validate(node), evidence="e")
    return ChangeSet(rationale="r", add=[add])


@pytest.mark.parametrize(
    ("extends", "update", "refused"),
    [
        pytest.param("team-payments:security_scan", "1.1.0", _MIXED, id="ancestor-moved"),
        pytest.param(
            "team-payments:security_scan", "1.1.0", _MIXED, id="ancestor-moved-in-a-draft"
        ),
        pytest.param(
            "team-payments:security_scan",
            "1.0.0",
            "builds on company@acme 1.0.0, and this work item runs company@acme 1.0.0;",
            id="ancestor-changed-under-its-version",
        ),
        pytest.param(
            "team-payments:security_scan", "1.1.0 as co", _MIXED, id="ancestor-moved-to-an-alias"
        ),
        pytest.param(
            "team-payments:security_scan",
            "1.1.0",
            "team-payments@acme 1.0.0 builds on platform-dept@acme 1.1.0, and this work item "
            "runs platform-dept@acme 1.0.0; a work item never mixes versions of one add-on tree",
            id="middle-ancestor-moved",
        ),
        pytest.param(
            "co:security_scan",
            "1.1.0 as co",
            "company@acme 1.1.0 is installed under co, and this work item runs company@acme "
            "1.0.0; a work item never mixes versions of one add-on",
            id="root-moved-to-an-alias",
        ),
        pytest.param(
            "pay:security_scan",
            "1.1.0 as pay",
            "team-payments@acme 1.1.0 is installed under pay, and this work item runs "
            "team-payments@acme 1.0.0; a work item never mixes versions of one add-on",
            id="child-moved-to-an-alias",
        ),
        pytest.param("team-payments:security_scan", None, None, id="ancestor-unchanged"),
        pytest.param("reads_skill", "1.1.0", "installed but not loaded", id="added-task-skill"),
        pytest.param(
            "quick",
            "1.1.0",
            "it reads add-on team-payments@acme, which is not loaded for this work item: " + _MIXED,
            id="fallback-into-a-mixed-profile",
        ),
    ],
)
def test_a_revision_never_mixes_ancestor_versions(request, tree_home, extends, update, refused):
    """An item that pinned company only adds a node reading team-payments,
    which it had not read: the team is pinned at the lock's version, with its
    ancestors, only while its locked company is the one the item runs.
    Otherwise the revision is refused naming both versions, never run on
    company 1.0.0 beside a team built on 1.1.0: matched by id, whatever its
    namespace is now, and compared by digest, which changes under one
    version too. An item draft goes through the
    same revision. A skill refused this way says only that its add-on is not
    loaded (the plan's known gap)."""
    repo, config_dir, addons_dir = tree_home
    case = request.node.callspec.id
    if case == "middle-ancestor-moved":
        # Filed on the dept's inherited task: it pins company and the dept,
        # and the dept moves while company stays where the item has it.
        (config_dir / "chains" / "dept.yaml").write_text(
            json.dumps(chain("platform-dept:security_scan"))
        )
        snapshot, live = _company_only_item(
            repo, config_dir, addons_dir, update=update, filed_on="dept", moved=TREE[1]
        )
    elif case == "child-moved-to-an-alias":
        # Filed on the team's chain: it pins the whole tree, and the team
        # moves to 1.1.0 under the alias `pay`.
        snapshot, live = _company_only_item(
            repo,
            config_dir,
            addons_dir,
            update=update,
            filed_on="team-payments:default",
            moved=TREE[2],
        )
    else:
        snapshot, live = _company_only_item(repo, config_dir, addons_dir, update=update)
    added = task_node(extends)

    if case.endswith("in-a-draft"):
        op = {"op": "add_node", "after": "n", "node": added}
        problems = item_draft.evaluate(snapshot, None, [op], live).problems
        assert [p["op"] for p in problems] == [0]
        assert refused in problems[0]["message"]
        return
    changes = _adding(added)
    if refused:
        with pytest.raises(RevisionError, match=re.escape(refused)):
            revise(snapshot, changes, at=None, library=live)
        return
    revised = revise(snapshot, changes, at=None, library=live)
    assert list(revised.chain.addons) == _WHOLE_TREE
    assert revised.chain.addons["company"]["version"] == "1.0.0"


@pytest.mark.parametrize(
    ("damage", "refused"),
    [
        pytest.param(
            "store-edited",
            "it reads add-on team-payments@acme, which is not loaded for this work item: "
            "profiles.yaml",
            id="store-edited-since",
        ),
        pytest.param("disabled", None, id="disabled-is-the-files-mistake"),
        pytest.param("pinned-store-gone", None, id="pinned-store-gone"),
    ],
)
async def test_a_revision_reading_a_left_out_addon_is_refused_unless_left_out_on_purpose(
    tree_home, damage, refused
):
    """A revision adding a fallback into an add-on whose store was edited is
    refused, instead of pinning nothing and stopping at launch. One disabled
    on purpose is no fault: the fallback naming it is the file's mistake, as
    at launch, and the revision is accepted with nothing more pinned. One the
    item already pinned is no refusal either, whatever its store: it stays
    pinned, and the launch restores the store it started with."""
    repo, config_dir, addons_dir = tree_home
    if damage == "pinned-store-gone":
        # Filed on the team's inherited skill: the item pins the whole tree.
        (config_dir / "library.yaml").write_text(json.dumps(_LOCAL_TASKS))
        _fast_falls_back_to(config_dir, "team-payments:slow")
        resolved = resolve_chain(config_dir, addons_dir, "inherited-skill")
        snapshot = MaterializedChain.from_json(stored_snapshot(resolved))
        drop_store(store_of(config_dir, addons_dir, "team-payments"))
        live = TemplateLibrary.from_yaml_dir(
            config_dir, addons=load.installed(config_dir, addons_dir)
        )
        revised = revise(snapshot, _adding(task_node("quick")), at=None, library=live)
        assert revised.chain.addons == resolved.addons
        await dispatch.restore_pins({"materialized_chain": revised.to_json()})
        assert launch_task(revised.chain.addons, profile="team-payments:slow").model == "o3"
        return
    snapshot, live = _company_only_item(repo, config_dir, addons_dir, update=None)
    if damage == "store-edited":
        edit_store(store_of(config_dir, addons_dir, "team-payments"), "profiles.yaml", "{}\n")
    else:
        edit_addons_yaml(config_dir, lambda w: w["addons"].update({TREE[2]: False}))
    changes = _adding(task_node("quick"))

    if refused:
        with pytest.raises(RevisionError, match=re.escape(refused)):
            revise(snapshot, changes, at=None, library=live)
    else:
        assert list(revise(snapshot, changes, at=None, library=live).chain.addons) == ["company"]


@pytest.mark.parametrize("case", ["launch-fallback-into-a-mixed-tree"])
def test_a_launch_never_runs_a_child_on_an_older_ancestor(tree_home, case):
    """A `harnesses.yaml` fallback into the team's profile, written after an
    item that pinned company 1.0.0 was filed, while the instance took company
    1.1.0: the launch stops naming the mismatch, never running the team built
    on 1.1.0 on the item's 1.0.0."""
    repo, config_dir, addons_dir = tree_home
    pins = resolve_chain(config_dir, addons_dir, "company:default").addons
    publish(repo, "company", version="1.1.0")
    update_addons(config_dir, addons_dir, ["company@acme"])
    _fast_falls_back_to(config_dir, "team-payments:slow")

    with pytest.raises(agent.HarnessUnavailable) as stop:
        launch_task(pins, profile="fast")

    assert str(stop.value) == f"add-on team-payments@acme 1.0.0 could not be read: {_MIXED}"


_LIBRARY_EDITED = "library.yaml does not match its digest"


@pytest.mark.parametrize(
    ("edited", "pinned", "fields", "stops"),
    [
        pytest.param("company", False, {"prompt": "unrelated"}, None, id="ancestor-library-edited"),
        pytest.param(
            "company", True, {"prompt": "unrelated"}, None, id="pinned-ancestor-library-edited"
        ),
        pytest.param(
            "company", False, {"profile": "fast"}, _LIBRARY_EDITED, id="ancestor-edited-read-stops"
        ),
        pytest.param(
            "team-payments", False, {"profile": "company:slow"}, None, id="own-library-edited"
        ),
        pytest.param(
            "team-payments", False, {"profile": "fast"}, _LIBRARY_EDITED, id="own-edited-read-stops"
        ),
    ],
)
def test_an_edited_library_leaves_only_its_tree_out(tree_home, edited, pinned, fields, stops):
    """`harnesses.yaml`'s `fast` falls back to `team-payments:slow`, and then
    a `library.yaml` in the tree is edited after it was installed: that
    add-on is left out with the store's reason, and its descendants with it,
    and nothing else. A launch that reads none of it runs, whatever profile
    table the rest builds; one that reads it stops saying which store, never
    that the fallback names a profile that is not defined."""
    repo, config_dir, addons_dir = tree_home
    pins = resolve_chain(config_dir, addons_dir, "inherited-skill").addons if pinned else None
    _fast_falls_back_to(config_dir, "team-payments:slow")
    edit_store(store_of(config_dir, addons_dir, edited), "library.yaml", "tasks: {}\n")

    if stops:
        with pytest.raises(agent.HarnessUnavailable) as stop:
            launch_task(pins, **fields)
        assert stops in str(stop.value)
        assert "not defined" not in str(stop.value)
    else:
        assert launch_task(pins, **fields).harness
