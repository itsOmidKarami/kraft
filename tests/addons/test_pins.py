"""A work item keeps the add-on versions it started with: its chain's
snapshot pins them, and every launch reads the pinned store, whatever was
installed since."""

import json
import shutil

import pytest
import yaml
from support.addons import (
    AGENT,
    TREE,
    accept_repo_library,
    chain,
    drop_store,
    edit_addons_yaml,
    edit_store,
    install,
    instance,
    make_collection,
    publish,
    repo_with_library,
    tree_collection,
)

from kraft.adapters import agent
from kraft.addons import load, update
from kraft.executor import dispatch
from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.skill import SkillError
from kraft.templates.environment import WorkItemTarget
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import AgentTask, MaterializedChain
from kraft.templates.retry import validate_retry_override
from kraft.templates.revision import (
    Add,
    AddedNode,
    ChangeSet,
    Override,
    RevisionError,
    revise,
)

ID = "release@acme"


def _node(extends):
    return {"kind": "exec", "tasks": [{"id": "t", "extends": extends}]}


def _release(version, model):
    task = {**AGENT, "skill": "notes", "profile": "release:deep"}
    return {
        "version": version,
        "library": {"tasks": {"base": task}, "nodes": {"stage": _node("base")}},
        "chains": {"ship": chain("base")},
        "skills": {"notes": f"the {version} method"},
        "profiles": {"deep": {"model": {"codex": model}}},
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A Kraft home, this process's own, with `release` 1.0.0 and `tools`
    installed and a local chain `mine` that extends `release`."""
    repo = make_collection(
        tmp_path,
        {"release": _release("1.0.0", "gpt-5"), "tools": {"library": {"tasks": {"base": AGENT}}}},
    )
    config_dir, addons_dir = instance(
        tmp_path, {"acme": {"git": repo.as_uri()}}, chains={"mine": chain("release:base")}
    )
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(addons_dir.parent))
    _update(config_dir, addons_dir, [ID, "tools@acme"], install=True)
    return repo, config_dir, addons_dir


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
        _update(config_dir, addons_dir, [addon_id], install=True)
    return repo, config_dir, addons_dir


def _update(config_dir, addons_dir, ids, install=False):
    results = update.update(
        ids,
        accept=lambda review: True,
        entries={i: True for i in ids} if install else None,
        config_dir=config_dir,
        addons_root=addons_dir,
    )
    assert [r.outcome for r in results] == ["applied"] * len(ids), results


def _stored(resolved):
    """The snapshot as intake writes it into the work item's row."""
    return resolved.materialize(
        WorkItemTarget.for_repository("r"), InstancePolicy.from_input(InstancePolicyInput())
    ).to_json()


def _resolved(config_dir, addons_dir, chain_id):
    library = TemplateLibrary.from_yaml_dir(
        config_dir, addons=load.installed(config_dir, addons_dir)
    )
    return library.resolve_chain(chain_id)


def test_a_chain_that_only_extends_an_addon_pins_it_and_a_trim_keeps_the_pin(home):
    """Nothing in the expanded chain names `tools`: the reference is what does."""
    repo, config_dir, addons_dir = home
    two = {
        "nodes": [
            {**n, "id": i} for i, n in zip("ab", chain("tools:base")["nodes"] * 2, strict=True)
        ]
    }
    (config_dir / "chains" / "two.yaml").write_text(json.dumps(two))

    resolved = _resolved(config_dir, addons_dir, "two")

    assert list(resolved.addons) == ["tools"]
    assert list(resolved.without_nodes({"a"}).addons) == ["tools"]


@pytest.mark.parametrize("chain_id", ["mine", "release:ship"], ids=["local-chain", "addon-chain"])
def test_a_resolved_chain_pins_the_addons_it_reads(home, chain_id):
    """Only what the chain reads through, with what a restore would need; and
    the pin survives the snapshot's round trip and a trim."""
    repo, config_dir, addons_dir = home
    locked = load.installed(config_dir, addons_dir)[0]

    resolved = _resolved(config_dir, addons_dir, chain_id)

    assert resolved.addons == {
        "release": {
            "id": ID,
            "commit": locked.commit,
            "tree": locked.tree,
            "source": "./addons/release",
            "digest": f"sha256:{locked.root.name}",
            "version": "1.0.0",
        }
    }
    assert locked.commit and locked.tree
    snapshot = MaterializedChain.from_json(_stored(resolved))
    assert snapshot.chain.addons == resolved.addons


def test_a_chain_through_a_repo_library_pins_its_commit_and_is_restored(home, tmp_path):
    """A repo library is pinned as an add-on is: its id, the commit and tree
    it was accepted at, and `./.kraft`; a missing store is read back from the
    connected checkout."""
    _repo, config_dir, addons_dir = home
    app = repo_with_library(tmp_path)
    accept_repo_library(config_dir, addons_dir, app, "app")
    library = next(p for p in load.installed(config_dir, addons_dir) if p.repo_id)

    resolved = _resolved(config_dir, addons_dir, "app:ship")

    assert resolved.addons == {
        "app": {
            "id": "library@repo:app",
            "commit": library.commit,
            "tree": library.tree,
            "source": "./.kraft",
            "digest": f"sha256:{library.root.name}",
            "version": library.version,
        }
    }
    assert library.commit and library.tree
    drop_store(library.root)
    (pinned,) = [
        p for p in load.for_item(resolved.addons, config_dir, addons_dir, repo_id=None) if p.repo_id
    ]
    assert pinned.left_out is not None

    load.restore(pinned, config_dir, addons_dir)

    assert library.root.is_dir()


@pytest.mark.parametrize("rebuild", ["retry-override", "chain-revision"])
def test_a_rebuilt_snapshot_keeps_the_items_pins(home, rebuild):
    """A retry override and an approved revision rebuild the persisted chain;
    neither may drop the pins and put the item back on the lock's versions."""
    repo, config_dir, addons_dir = home
    resolved = _resolved(config_dir, addons_dir, "mine")
    snapshot = MaterializedChain.from_json(_stored(resolved))
    path = resolved.task_paths[0]

    if rebuild == "retry-override":
        rebuilt = validate_retry_override(snapshot, path, task_config={"model": "gpt-5"}).chain
    else:
        changes = ChangeSet(rationale="r", overrides={path: Override(model="gpt-5", evidence="e")})
        rebuilt = revise(snapshot, changes, at=None, library=None)

    assert resolved.addons
    assert rebuilt.chain.addons == resolved.addons


def test_a_local_profiles_fallback_pins_the_addon_it_names(home):
    """`profile:` is read live: the add-on profile a local one falls back to
    is read from the add-on at launch, so the item has to pin it."""
    repo, config_dir, addons_dir = home
    fast = {"model": {"codex": "gpt-5"}, "fallback": [{"profile": "release:deep"}]}
    (config_dir / "harnesses.yaml").write_text(
        yaml.safe_dump({"harnesses": {"codex": {"provider": "codex"}}, "profiles": {"fast": fast}})
    )
    task = {**AGENT, "profile": "fast"}
    (config_dir / "chains" / "quick.yaml").write_text(
        json.dumps({"nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", **task}]}]})
    )

    resolved = _resolved(config_dir, addons_dir, "quick")

    assert list(resolved.addons) == ["release"]


@pytest.mark.parametrize(
    "task",
    [
        pytest.param({}, id="plain"),
        pytest.param(
            {"prompt": "release: write the notes", "model": "release:x"},
            id="prose-names-a-namespace",
        ),
    ],
)
def test_a_chain_that_reads_no_addon_stores_no_pins(home, task):
    repo, config_dir, addons_dir = home
    body = {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", **AGENT, **task}]}]}
    (config_dir / "chains" / "plain.yaml").write_text(json.dumps(body))
    resolved = _resolved(config_dir, addons_dir, "plain")
    assert resolved.addons == {}
    stored = _stored(resolved)
    assert "addons" not in json.loads(stored)


def _launch(pins, **fields):
    task = AgentTask.model_validate(
        {"id": "t", **AGENT, **(fields or {"skill": "release:notes", "profile": "release:deep"})}
    )
    return agent.resolve_agent_task(task, None, steering={}, addons=pins)


def test_a_launch_reads_the_version_the_item_started_with(home):
    """The item was filed on 1.0.0; the instance has since taken 1.1.0."""
    repo, config_dir, addons_dir = home
    pins = _resolved(config_dir, addons_dir, "mine").addons
    publish(repo, "release", **_release("1.1.0", "o3"))
    _update(config_dir, addons_dir, [ID])

    started, filed_now = _launch(pins), _launch(None)

    assert ("the 1.0.0 method" in started.method_text, started.model) == (True, "gpt-5")
    assert ("the 1.1.0 method" in filed_now.method_text, filed_now.model) == (True, "o3")


def test_a_pinned_version_that_is_gone_is_refused_not_delegated(home):
    """Its store was removed: the skill is not handed to the agent as another
    tool's, and not read from the newer version either."""
    repo, config_dir, addons_dir = home
    pins = _resolved(config_dir, addons_dir, "mine").addons
    gone = {"release": {**pins["release"], "digest": "sha256:" + "0" * 64}}

    with pytest.raises(agent.HarnessUnavailable) as stop:
        _launch(gone)

    assert str(stop.value) == (
        "add-on release@acme 1.0.0 could not be read: the version this work item started "
        "with is no longer in the store"
    )


_EDITED = "profiles.yaml does not match its digest"
_FAST = {"model": {"codex": "gpt-5"}, "fallback": [{"profile": "release:deep"}]}


@pytest.mark.parametrize(
    ("damage", "pinned", "fields", "stops"),
    [
        ("gone", True, {"skill": "release:notes"}, "could not be read"),
        ("gone", True, {"profile": "release:deep"}, "could not be read"),
        ("gone", True, {"fallback": [{"profile": "release:deep"}]}, "could not be read"),
        ("gone", True, {"prompt": "unrelated"}, None),
        ("edited", True, {"profile": "release:deep"}, _EDITED),
        ("edited", True, {"prompt": "unrelated"}, None),
        ("edited", True, {"profile": "fast"}, _EDITED),
        ("edited", False, {"profile": "fast"}, _EDITED),
        ("edited", False, {"prompt": "unrelated"}, None),
        ("disabled", False, {"prompt": "unrelated"}, "profile 'release:deep' is not defined"),
    ],
    ids=[
        "skill-from-it-stops",
        "profile-from-it-stops",
        "fallback-to-it-stops",
        "unrelated-task-launches",
        "edited-profiles-stop-its-profile",
        "edited-profiles-spare-an-unrelated-task",
        "a-local-fallback-into-it-stops",
        "unpinned-local-fallback-into-it-stops",
        "unpinned-edited-profiles-spare-an-unrelated-task",
        "a-fallback-into-a-disabled-addon-is-the-files-mistake",
    ],
)
def test_a_missing_pin_the_task_does_not_read_does_not_stop_it(home, damage, pinned, fields, stops):
    """An escalation or a review of an item that pinned `release` reads nothing
    from it: its store being gone, or its `profiles.yaml` edited after it was
    written, is no reason to refuse them, even beside a `harnesses.yaml`
    profile that falls back into it. One disabled on purpose is not damage: a
    fallback naming it is the file's mistake, as it always was."""
    repo, config_dir, addons_dir = home
    pins = _resolved(config_dir, addons_dir, "mine").addons
    store = load.installed(config_dir, addons_dir)[0].root
    harnesses = yaml.safe_load((config_dir / "harnesses.yaml").read_text()) or {}
    harnesses["profiles"] = {**(harnesses.get("profiles") or {}), "fast": _FAST}
    (config_dir / "harnesses.yaml").write_text(yaml.safe_dump(harnesses))
    if damage == "gone":
        drop_store(store)
        pins = {"release": {**pins["release"], "digest": "sha256:" + "0" * 64}}
    elif damage == "edited":
        edit_store(store, "profiles.yaml", "profiles: {deep: {model: {codex: evil}}}\n")
    else:
        edit_addons_yaml(config_dir, lambda w: w["addons"].update({ID: False}))

    if stops:
        with pytest.raises(agent.HarnessUnavailable, match=stops):
            _launch(pins if pinned else None, **fields)
    else:
        assert _launch(pins if pinned else None, **fields).harness


def test_every_launch_is_handed_the_items_pins(home):
    """`frozen_steering` is what dispatch, a gate's review, an escalation and a
    review reply all pass to `resolve_agent_task`."""
    repo, config_dir, addons_dir = home
    resolved = _resolved(config_dir, addons_dir, "mine")
    stored = _stored(resolved)

    assert dispatch.frozen_steering({"materialized_chain": stored})["addons"] == resolved.addons
    assert dispatch.frozen_steering({"materialized_chain": None})["addons"] is None


async def test_a_launch_restores_the_pinned_store_it_needs(home):
    """GC took the old version after the lock moved on, or the run directory
    was cleared: the item's own version is put back before it is read."""
    repo, config_dir, addons_dir = home
    resolved = _resolved(config_dir, addons_dir, "mine")
    old = load.installed(config_dir, addons_dir)[0].root
    publish(repo, "release", **_release("1.1.0", "o3"))
    _update(config_dir, addons_dir, [ID])
    drop_store(old)
    row = {"materialized_chain": _stored(resolved)}
    with pytest.raises(agent.HarnessUnavailable, match="no longer in the store"):
        _launch(resolved.addons)

    await dispatch.restore_pins(row)

    assert "the 1.0.0 method" in _launch(resolved.addons).method_text


async def test_an_unrestorable_pin_stops_the_launch_and_says_why(home):
    """`HarnessUnavailable` is what dispatch records as the `config` stop."""
    repo, config_dir, addons_dir = home
    resolved = _resolved(config_dir, addons_dir, "mine")
    drop_store(load.installed(config_dir, addons_dir)[0].root)
    shutil.rmtree(repo)
    shutil.rmtree(addons_dir / "mirrors")

    await dispatch.restore_pins({"materialized_chain": _stored(resolved)})

    with pytest.raises(agent.HarnessUnavailable) as stop:
        _launch(resolved.addons)
    assert "add-on release@acme 1.0.0 could not be read: release@acme 1.0.0: locked commit" in str(
        stop.value
    )
    assert "could not be fetched" in str(stop.value)


_VIA = {
    "tasks": {"base": AGENT},
    "nodes": {f"via_{n}": _node(f"{n}:base") for n in ("release", "tools")},
}


def _store_of(config_dir, addons_dir, namespace):
    return next(a.root for a in load.installed(config_dir, addons_dir) if a.namespace == namespace)


def _task(extends):
    return {"id": "x", "tasks": [{"id": "t", "extends": extends}]}


@pytest.mark.parametrize(
    "added, pinned, disable",
    [
        ({"id": "x", "extends": "via_release"}, ["release"], False),
        ({"id": "x", "extends": "via_tools"}, ["release", "tools"], False),
        ({"id": "x", "extends": "release:stage"}, ["release"], False),
        (_task("release:base"), ["release"], False),
        (_task("tools:base"), ["release", "tools"], False),
        ({"id": "x", "extends": "release:stage"}, ["release"], True),
        ({"id": "x", "extends": "release:stage"}, ["release"], "edited"),
    ],
    ids=[
        "through-a-pinned-addon",
        "through-an-addon-not-touched-yet",
        "added-node-extends-an-addon",
        "added-task-extends-an-addon",
        "added-task-extends-an-addon-not-touched-yet",
        "pinned-addon-disabled-since",
        "another-addons-store-edited-since",
    ],
)
def test_a_revision_resolves_against_pinned_addons(home, added, pinned, disable):
    """A node a revision adds runs on the versions the item runs on, whether it
    names an add-on's component or a local one that does; an add-on the item
    had not read from yet joins its pins at the lock's version. An add-on the
    item pinned stays loaded for it after it is disabled, as at launch, and
    another add-on's store edited since the library loaded is left out, not
    the revision."""
    repo, config_dir, addons_dir = home
    (config_dir / "library.yaml").write_text(json.dumps(_VIA))
    snapshot = MaterializedChain.from_json(_stored(_resolved(config_dir, addons_dir, "mine")))
    newer = _release("1.1.0", "o3")
    newer["library"]["tasks"]["base"]["prompt"] = "the 1.1.0 prompt"
    publish(repo, "release", **newer)
    _update(config_dir, addons_dir, [ID])
    if disable is True:
        edit_addons_yaml(config_dir, lambda w: w["addons"].update({ID: False}))
    live = TemplateLibrary.from_yaml_dir(config_dir, addons=load.installed(config_dir, addons_dir))
    if disable == "edited":
        edit_store(_store_of(config_dir, addons_dir, "tools"), "library.yaml", "tasks: {}\n")
    add = Add(after="n", node=AddedNode.model_validate(added), evidence="e")

    revised = revise(snapshot, ChangeSet(rationale="r", add=[add]), at=None, library=live)

    # 1.0.0's task, like `tools`', carries the fixture prompt; 1.1.0's does not.
    assert revised.chain.chain.nodes[1].tasks[0].prompt == AGENT["prompt"]
    assert sorted(revised.chain.addons) == pinned
    assert revised.chain.addons["release"]["version"] == "1.0.0"


@pytest.mark.parametrize("gone", ["disabled", "pinned-version-gone", "store-edited-since"])
def test_a_revision_cannot_add_from_an_addon_that_is_not_loaded(home, gone):
    """Refused with the resolver's message, never resolved against another
    version, and never a library error (a 500) for a store edited since."""
    repo, config_dir, addons_dir = home
    snapshot = MaterializedChain.from_json(_stored(_resolved(config_dir, addons_dir, "mine")))
    if gone == "disabled":
        edit_addons_yaml(config_dir, lambda w: w["addons"].update({"tools@acme": False}))
        added = _task("tools:base")
    elif gone == "pinned-version-gone":
        old = load.installed(config_dir, addons_dir)[0].root
        publish(repo, "release", **_release("1.1.0", "o3"))
        _update(config_dir, addons_dir, [ID])
        drop_store(old)
        added = {"id": "x", "extends": "release:stage"}
    live = TemplateLibrary.from_yaml_dir(config_dir, addons=load.installed(config_dir, addons_dir))
    if gone == "store-edited-since":
        edit_store(_store_of(config_dir, addons_dir, "tools"), "library.yaml", "tasks: {}\n")
        added = _task("tools:base")
    add = Add(after="n", node=AddedNode.model_validate(added), evidence="e")

    with pytest.raises(RevisionError, match=r"extends no (node|task) named"):
        revise(snapshot, ChangeSet(rationale="r", add=[add]), at=None, library=live)


#: A repo library's default `library.yaml` holds the task `base` and no node.
_DIRECT = AddedNode(id="x", tasks=[{"id": "t", "extends": "app:base"}])


@pytest.mark.parametrize(
    "node, repo_id",
    [
        pytest.param(AddedNode(id="x", extends="via_app"), "app", id="own-repo"),
        pytest.param(AddedNode(id="x", extends="via_app"), "web", id="another-repo"),
        pytest.param(_DIRECT, "web", id="another-repo-direct"),
        pytest.param(_DIRECT, None, id="no-repo"),
    ],
)
def test_a_revision_adds_only_from_the_items_own_repo_library(home, tmp_path, node, repo_id):
    """`app:base` is in app's repo library: an added node reads it only for
    an item in app, whether through a local node or directly."""
    repo, config_dir, addons_dir = home
    accept_repo_library(config_dir, addons_dir, repo_with_library(tmp_path), "app")
    repos = yaml.safe_load((config_dir / "repos.yaml").read_text())
    repos["repos"].append({"path": "/web", "id": "web"})
    (config_dir / "repos.yaml").write_text(yaml.safe_dump(repos))
    via = {"kind": "exec", "tasks": [{"id": "t", "extends": "app:base"}]}
    library = {"tasks": {"base": AGENT}, "nodes": {"via_app": via}}
    (config_dir / "library.yaml").write_text(json.dumps(library))
    snapshot = MaterializedChain.from_json(_stored(_resolved(config_dir, addons_dir, "mine")))
    live = TemplateLibrary.from_yaml_dir(config_dir, addons=load.installed(config_dir, addons_dir))
    changes = ChangeSet(rationale="r", add=[Add(after="n", node=node, evidence="e")])

    if repo_id == "app":
        revised = revise(snapshot, changes, at=None, library=live, repo_id=repo_id)
        assert sorted(revised.chain.addons) == ["app", "release"]
        return
    with pytest.raises(RevisionError, match="visible only to items in app"):
        revise(snapshot, changes, at=None, library=live, repo_id=repo_id)


@pytest.fixture
def two_repos(home, tmp_path):
    """`home`, plus `app`'s repo library (skill `notes`, profile `deep`)
    accepted, and `web` connected with no library. The pin of a chain that
    read app's library, for an item filed before the scope rule."""
    repo, config_dir, addons_dir = home
    deep = {"profiles": {"deep": {"model": {"codex": "gpt-5"}}}}
    app = repo_with_library(
        tmp_path,
        skills={"notes": "the app method"},
        extra={".kraft/profiles.yaml": yaml.safe_dump(deep)},
    )
    store = accept_repo_library(config_dir, addons_dir, app, "app")
    repos = yaml.safe_load((config_dir / "repos.yaml").read_text())
    repos["repos"].append({"path": "/web", "id": "web"})
    (config_dir / "repos.yaml").write_text(yaml.safe_dump(repos))
    (library,) = [p for p in load.installed(config_dir, addons_dir) if p.repo_id == "app"]
    return {"app": library.pin}, store, config_dir


_SCOPED = "visible only to items in app"


@pytest.mark.parametrize(
    "fields, pinned, repo_id, read, stops",
    [
        pytest.param(
            {"skill": "app:notes"}, False, "app", "the app method", None, id="own-repo-skill"
        ),
        pytest.param({"skill": "app:notes"}, False, "web", None, _SCOPED, id="another-repos-skill"),
        pytest.param(
            {"profile": "app:deep"}, False, "web", None, _SCOPED, id="another-repos-profile"
        ),
        pytest.param(
            {"skill": "app:notes"},
            True,
            "web",
            "the app method",
            None,
            id="pinned-before-the-change",
        ),
        pytest.param(
            {"skill": "web:notes"},
            False,
            "app",
            None,
            "repository 'web' has no accepted repo library",
            id="reserved-id-not-delegated",
        ),
        pytest.param(
            {"skill": "superpowers:brainstorm"},
            False,
            "app",
            "Follow the superpowers:brainstorm skill",
            None,
            id="another-tools-plugin",
        ),
        pytest.param(
            {"profile": "app:deep"},
            False,
            "app",
            None,
            "profiles.yaml does not match its digest",
            id="own-repo-edited-store",
        ),
        pytest.param(
            {"skill": "kraft:spec"}, False, "app", "name: spec", None, id="kraft-id-is-not-reserved"
        ),
        pytest.param(
            {"profile": "fast"}, False, "web", None, _SCOPED, id="fallback-into-another-repo"
        ),
        pytest.param({}, False, "web", "", None, id="unrelated-task-beside-that-fallback"),
        pytest.param({}, False, None, "", None, id="instance-table-beside-that-fallback"),
    ],
)
def test_a_launch_reads_only_its_own_repos_library(
    request, two_repos, fields, pinned, repo_id, read, stops
):
    """Another repository's library, and a connected id with none, stop the
    launch with that library's own reason, never delegated as another tool's
    plugin. An own-repo store edited after it was accepted stops with the
    digest's reason, never the scope's (D13). `kraft` or an add-on's namespace
    as a repo id (set by hand, or minted before ids skipped them) reserves
    nothing, and a `harnesses.yaml` fallback into another repository's library
    stops only the launch that walks into it."""
    pins, store, config_dir = two_repos
    case = request.node.callspec.id
    if stops and "digest" in stops:
        edit_store(store, "profiles.yaml", "profiles: {deep: {model: {codex: evil}}}\n")
    if case == "kraft-id-is-not-reserved":
        repos = yaml.safe_load((config_dir / "repos.yaml").read_text())
        repos["repos"].append({"path": "/kraft", "id": "kraft"})
        (config_dir / "repos.yaml").write_text(yaml.safe_dump(repos))
    if "fallback" in case:
        fast = {"model": {"codex": "gpt-5"}, "fallback": [{"profile": "app:deep"}]}
        harnesses = yaml.safe_load((config_dir / "harnesses.yaml").read_text()) or {}
        harnesses["profiles"] = {**(harnesses.get("profiles") or {}), "fast": fast}
        (config_dir / "harnesses.yaml").write_text(yaml.safe_dump(harnesses))
    task = AgentTask.model_validate({"id": "t", **AGENT, **fields})

    def launch():
        return agent.resolve_agent_task(
            task, None, steering={}, addons=pins if pinned else None, repo_id=repo_id
        )

    if read is not None:
        assert read in (launch().method_text or "")
        return
    with pytest.raises((SkillError, agent.HarnessUnavailable)) as stop:
        launch()
    assert stops in str(stop.value)
    assert (_SCOPED in str(stop.value)) == (stops == _SCOPED)


@pytest.mark.parametrize(
    "chain_id", ["team:ship"], ids=["child-pin-resolves-with-current-ancestors"]
)
def test_a_pinned_child_builds_with_its_parent(home, tmp_path, chain_id):
    """A work item that pinned a child add-on rebuilds it as a child: its
    pinned store's manifest names the parent, and the instance's installed
    ancestors stand in for pins it does not have until slice 3. `team:ship`
    exists only as the chain `team` inherits from `release`."""
    repo, config_dir, addons_dir = home
    requires = {"kraft": "2", "harnesses": ["codex"], "addons": {ID: "1"}}
    payments = make_collection(tmp_path, {"team": {"manifest": {"requires": requires}}}, name="pay")
    install(config_dir, addons_dir, payments, "team")
    library = TemplateLibrary.from_yaml_dir(
        config_dir, addons=load.installed(config_dir, addons_dir)
    )
    pins = library.resolve_chain(chain_id).addons

    rebuilt = library.with_addons(load.for_item(pins, config_dir, addons_dir, repo_id=None))

    assert "team" in pins
    assert rebuilt.resolve_chain(chain_id).nodes[0].id == "n"


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
    ],
)
async def test_an_item_keeps_its_tree_across_a_parent_update(tree_home, case):
    """An item that read `team-payments` keeps the company version it started
    with after company moves to 1.1.0, which the team's range `1` accepts: its
    pins carry the whole tree, a launch reads the pinned ancestor, and a pinned
    ancestor whose store is gone leaves its descendants out with its reason
    until it is restored."""
    repo, config_dir, addons_dir = tree_home
    resolved = _resolved(config_dir, addons_dir, "team-payments:default")
    pins = resolved.addons
    skill = next(iter(resolved.nodes[0].tasks())).task.skill
    old = _store_of(config_dir, addons_dir, "company")
    publish(repo, "company", version="1.1.0", skills={"deploy-review": "company 1.1.0 method"})
    _update(config_dir, addons_dir, ["company@acme"])

    if case == "launch-reads-pinned-ancestor":
        started, filed_now = _launch(pins, skill=skill), _launch(None, skill=skill)
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
            _launch(pins, fallback=[{"profile": "team-payments:slow"}])
        assert str(stop.value) == f"add-on team-payments@acme 1.0.0 could not be read: {reason}"

        await dispatch.restore_pins({"materialized_chain": _stored(resolved)})

        assert "company method" in _launch(pins, skill=skill).method_text
        return
    if case == "for-item-tree-order":
        pins = dict(reversed(pins.items()))
    elif case == "pin-without-parent":
        # A snapshot stored before pins recorded parents.
        pins = {n: {k: v for k, v in p.items() if k != "parent"} for n, p in pins.items()}
    else:
        edit_addons_yaml(config_dir, lambda w: w["addons"].update({TREE[2]: False}))

    known = load.for_item(pins, config_dir, addons_dir, repo_id=None)

    tree = [p for p in known if p.namespace in pins]
    assert [p.namespace for p in tree] == _WHOLE_TREE
    assert [(p.parent, p.left_out) for p in tree] == [
        (None, None),
        ("company@acme", None),
        ("platform-dept@acme", None),
    ]
