"""A work item keeps the add-on versions it started with: its chain's
snapshot pins them, and every launch reads the pinned store, whatever was
installed since."""

import json
import shutil

import pytest
import yaml
from support.addons import (
    AGENT,
    accept_repo_library,
    chain,
    drop_store,
    edit_addons_yaml,
    instance,
    make_collection,
    publish,
    repo_with_library,
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


@pytest.mark.parametrize(
    ("fields", "stops"),
    [
        ({"skill": "release:notes"}, True),
        ({"profile": "release:deep"}, True),
        ({"fallback": [{"profile": "release:deep"}]}, True),
        ({"prompt": "unrelated"}, False),
    ],
    ids=[
        "skill-from-it-stops",
        "profile-from-it-stops",
        "fallback-to-it-stops",
        "unrelated-task-launches",
    ],
)
def test_a_missing_pin_the_task_does_not_read_does_not_stop_it(home, fields, stops):
    """An escalation or a review of an item that pinned `release` reads nothing
    from it: its store being gone is no reason to refuse them."""
    repo, config_dir, addons_dir = home
    pins = _resolved(config_dir, addons_dir, "mine").addons
    drop_store(load.installed(config_dir, addons_dir)[0].root)
    gone = {"release": {**pins["release"], "digest": "sha256:" + "0" * 64}}

    if stops:
        with pytest.raises(agent.HarnessUnavailable, match="could not be read"):
            _launch(gone, **fields)
    else:
        assert _launch(gone, **fields).harness


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


def _task(extends):
    return {"id": "x", "tasks": [{"id": "t", "extends": extends}]}


@pytest.mark.parametrize(
    "added, pinned",
    [
        ({"id": "x", "extends": "via_release"}, ["release"]),
        ({"id": "x", "extends": "via_tools"}, ["release", "tools"]),
        ({"id": "x", "extends": "release:stage"}, ["release"]),
        (_task("release:base"), ["release"]),
        (_task("tools:base"), ["release", "tools"]),
    ],
    ids=[
        "through-a-pinned-addon",
        "through-an-addon-not-touched-yet",
        "added-node-extends-an-addon",
        "added-task-extends-an-addon",
        "added-task-extends-an-addon-not-touched-yet",
    ],
)
def test_a_revision_resolves_against_pinned_addons(home, added, pinned):
    """A node a revision adds runs on the versions the item runs on, whether it
    names an add-on's component or a local one that does; an add-on the item
    had not read from yet joins its pins at the lock's version."""
    repo, config_dir, addons_dir = home
    (config_dir / "library.yaml").write_text(json.dumps(_VIA))
    snapshot = MaterializedChain.from_json(_stored(_resolved(config_dir, addons_dir, "mine")))
    newer = _release("1.1.0", "o3")
    newer["library"]["tasks"]["base"]["prompt"] = "the 1.1.0 prompt"
    publish(repo, "release", **newer)
    _update(config_dir, addons_dir, [ID])
    live = TemplateLibrary.from_yaml_dir(config_dir, addons=load.installed(config_dir, addons_dir))
    add = Add(after="n", node=AddedNode.model_validate(added), evidence="e")

    revised = revise(snapshot, ChangeSet(rationale="r", add=[add]), at=None, library=live)

    # 1.0.0's task, like `tools`', carries the fixture prompt; 1.1.0's does not.
    assert revised.chain.chain.nodes[1].tasks[0].prompt == AGENT["prompt"]
    assert sorted(revised.chain.addons) == pinned
    assert revised.chain.addons["release"]["version"] == "1.0.0"


@pytest.mark.parametrize("gone", ["disabled", "pinned-version-gone"])
def test_a_revision_cannot_add_from_an_addon_that_is_not_loaded(home, gone):
    """Refused with the resolver's message, never resolved against another version."""
    repo, config_dir, addons_dir = home
    snapshot = MaterializedChain.from_json(_stored(_resolved(config_dir, addons_dir, "mine")))
    if gone == "disabled":
        edit_addons_yaml(config_dir, lambda w: w["addons"].update({"tools@acme": False}))
        added = _task("tools:base")
    else:
        old = load.installed(config_dir, addons_dir)[0].root
        publish(repo, "release", **_release("1.1.0", "o3"))
        _update(config_dir, addons_dir, [ID])
        drop_store(old)
        added = {"id": "x", "extends": "release:stage"}
    live = TemplateLibrary.from_yaml_dir(config_dir, addons=load.installed(config_dir, addons_dir))
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
    return {"app": library.pin}, store


@pytest.mark.parametrize(
    "fields, pinned, repo_id, read",
    [
        pytest.param({"skill": "app:notes"}, False, "app", "the app method", id="own-repo-skill"),
        pytest.param({"skill": "app:notes"}, False, "web", None, id="another-repos-skill"),
        pytest.param({"profile": "app:deep"}, False, "web", None, id="another-repos-profile"),
        pytest.param(
            {"skill": "app:notes"}, True, "web", "the app method", id="pinned-before-the-change"
        ),
        pytest.param({"skill": "web:notes"}, False, "app", None, id="reserved-id-not-delegated"),
        pytest.param(
            {"skill": "superpowers:brainstorm"},
            False,
            "app",
            "Follow the superpowers:brainstorm skill",
            id="another-tools-plugin",
        ),
    ],
)
def test_a_launch_reads_only_its_own_repos_library(two_repos, fields, pinned, repo_id, read):
    pins, _store = two_repos
    task = AgentTask.model_validate({"id": "t", **AGENT, **fields})

    def launch():
        return agent.resolve_agent_task(
            task, None, steering={}, addons=pins if pinned else None, repo_id=repo_id
        )

    if read is not None:
        assert read in launch().method_text
        return
    with pytest.raises((SkillError, agent.ProfileUnavailable)) as stop:
        launch()
    owner = next(iter(fields.values())).partition(":")[0]
    assert owner in str(stop.value)
    if owner == "app":
        assert "visible only to items in app" in str(stop.value)
