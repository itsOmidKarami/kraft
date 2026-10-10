"""A work item keeps the plugin versions it started with: its chain's
snapshot pins them, and every launch reads the pinned store, whatever was
installed since."""

import json
import shutil

import pytest
from support.plugins import AGENT, chain, drop_store, instance, make_collection, publish

from kraft.adapters import agent
from kraft.executor import dispatch
from kraft.plugins import load, update
from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates.environment import WorkItemTarget
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import AgentTask, MaterializedChain
from kraft.templates.retry import validate_retry_override
from kraft.templates.revision import Add, AddedNode, ChangeSet, Override, revise

ID = "release@acme"


def _release(version, model):
    task = {**AGENT, "skill": "notes", "profile": "release:deep"}
    return {
        "version": version,
        "library": {"tasks": {"base": task}},
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
    config_dir, plugins_dir = instance(
        tmp_path, {"acme": {"git": repo.as_uri()}}, chains={"mine": chain("release:base")}
    )
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(plugins_dir.parent))
    _update(config_dir, plugins_dir, [ID, "tools@acme"], install=True)
    return repo, config_dir, plugins_dir


def _update(config_dir, plugins_dir, ids, install=False):
    results = update.update(
        ids,
        accept=lambda review: True,
        entries={i: True for i in ids} if install else None,
        config_dir=config_dir,
        plugins_root=plugins_dir,
    )
    assert [r.outcome for r in results] == ["applied"] * len(ids), results


def _stored(resolved):
    """The snapshot as intake writes it into the work item's row."""
    return resolved.materialize(
        WorkItemTarget.for_repository("r"), InstancePolicy.from_input(InstancePolicyInput())
    ).to_json()


def _resolved(config_dir, plugins_dir, chain_id):
    library = TemplateLibrary.from_yaml_dir(
        config_dir, plugins=load.installed(config_dir, plugins_dir)
    )
    return library.resolve_chain(chain_id)


def test_a_chain_that_only_extends_a_plugin_pins_it_and_a_trim_keeps_the_pin(home):
    """Nothing in the expanded chain names `tools`: the reference is what does."""
    repo, config_dir, plugins_dir = home
    two = {
        "nodes": [
            {**n, "id": i} for i, n in zip("ab", chain("tools:base")["nodes"] * 2, strict=True)
        ]
    }
    (config_dir / "chains" / "two.yaml").write_text(json.dumps(two))

    resolved = _resolved(config_dir, plugins_dir, "two")

    assert list(resolved.plugins) == ["tools"]
    assert list(resolved.without_nodes({"a"}).plugins) == ["tools"]


@pytest.mark.parametrize("chain_id", ["mine", "release:ship"], ids=["local-chain", "plugin-chain"])
def test_a_resolved_chain_pins_the_plugins_it_reads(home, chain_id):
    """Only what the chain reads through, with what a restore would need; and
    the pin survives the snapshot's round trip and a trim."""
    repo, config_dir, plugins_dir = home
    locked = load.installed(config_dir, plugins_dir)[0]

    resolved = _resolved(config_dir, plugins_dir, chain_id)

    assert resolved.plugins == {
        "release": {
            "id": ID,
            "commit": locked.commit,
            "tree": locked.tree,
            "source": "./plugins/release",
            "digest": f"sha256:{locked.root.name}",
            "version": "1.0.0",
        }
    }
    assert locked.commit and locked.tree
    snapshot = MaterializedChain.from_json(_stored(resolved))
    assert snapshot.chain.plugins == resolved.plugins


@pytest.mark.parametrize("rebuild", ["retry-override", "chain-revision"])
def test_a_rebuilt_snapshot_keeps_the_items_pins(home, rebuild):
    """A retry override and an approved revision rebuild the persisted chain;
    neither may drop the pins and put the item back on the lock's versions."""
    repo, config_dir, plugins_dir = home
    resolved = _resolved(config_dir, plugins_dir, "mine")
    snapshot = MaterializedChain.from_json(_stored(resolved))
    path = resolved.task_paths[0]

    if rebuild == "retry-override":
        rebuilt = validate_retry_override(snapshot, path, task_config={"model": "gpt-5"}).chain
    else:
        changes = ChangeSet(rationale="r", overrides={path: Override(model="gpt-5", evidence="e")})
        rebuilt = revise(snapshot, changes, at=None, library=None)

    assert resolved.plugins
    assert rebuilt.chain.plugins == resolved.plugins


def test_a_chain_that_reads_no_plugin_stores_no_pins(home):
    repo, config_dir, plugins_dir = home
    (config_dir / "chains" / "plain.yaml").write_text(json.dumps(chain("base")))
    resolved = _resolved(config_dir, plugins_dir, "plain")
    assert resolved.plugins == {}
    stored = _stored(resolved)
    assert "plugins" not in json.loads(stored)


def _launch(pins):
    task = AgentTask.model_validate(
        {"id": "t", **AGENT, "skill": "release:notes", "profile": "release:deep"}
    )
    return agent.resolve_agent_task(task, None, steering={}, plugins=pins)


def test_a_launch_reads_the_version_the_item_started_with(home):
    """The item was filed on 1.0.0; the instance has since taken 1.1.0."""
    repo, config_dir, plugins_dir = home
    pins = _resolved(config_dir, plugins_dir, "mine").plugins
    publish(repo, "release", **_release("1.1.0", "o3"))
    _update(config_dir, plugins_dir, [ID])

    started, filed_now = _launch(pins), _launch(None)

    assert ("the 1.0.0 method" in started.method_text, started.model) == (True, "gpt-5")
    assert ("the 1.1.0 method" in filed_now.method_text, filed_now.model) == (True, "o3")


def test_a_pinned_version_that_is_gone_is_refused_not_delegated(home):
    """Its store was removed: the skill is not handed to the agent as another
    tool's, and not read from the newer version either."""
    repo, config_dir, plugins_dir = home
    pins = _resolved(config_dir, plugins_dir, "mine").plugins
    gone = {"release": {**pins["release"], "digest": "sha256:" + "0" * 64}}

    with pytest.raises(agent.HarnessUnavailable) as stop:
        _launch(gone)

    assert str(stop.value) == (
        "plugin release@acme 1.0.0 could not be read: the version this work item started "
        "with is no longer in the store"
    )


def test_every_launch_is_handed_the_items_pins(home):
    """`frozen_steering` is what dispatch, a gate's review, an escalation and a
    review reply all pass to `resolve_agent_task`."""
    repo, config_dir, plugins_dir = home
    resolved = _resolved(config_dir, plugins_dir, "mine")
    stored = _stored(resolved)

    assert dispatch.frozen_steering({"materialized_chain": stored})["plugins"] == resolved.plugins
    assert dispatch.frozen_steering({"materialized_chain": None})["plugins"] is None


async def test_a_launch_restores_the_pinned_store_it_needs(home):
    """GC took the old version after the lock moved on, or the run directory
    was cleared: the item's own version is put back before it is read."""
    repo, config_dir, plugins_dir = home
    resolved = _resolved(config_dir, plugins_dir, "mine")
    old = load.installed(config_dir, plugins_dir)[0].root
    publish(repo, "release", **_release("1.1.0", "o3"))
    _update(config_dir, plugins_dir, [ID])
    drop_store(old)
    row = {"materialized_chain": _stored(resolved)}
    with pytest.raises(agent.HarnessUnavailable, match="no longer in the store"):
        _launch(resolved.plugins)

    await dispatch.restore_pins(row)

    assert "the 1.0.0 method" in _launch(resolved.plugins).method_text


async def test_an_unrestorable_pin_stops_the_launch_and_says_why(home):
    """`HarnessUnavailable` is what dispatch records as the `config` stop."""
    repo, config_dir, plugins_dir = home
    resolved = _resolved(config_dir, plugins_dir, "mine")
    drop_store(load.installed(config_dir, plugins_dir)[0].root)
    shutil.rmtree(repo)
    shutil.rmtree(plugins_dir / "mirrors")

    await dispatch.restore_pins({"materialized_chain": _stored(resolved)})

    with pytest.raises(agent.HarnessUnavailable) as stop:
        _launch(resolved.plugins)
    assert "plugin release@acme 1.0.0 could not be read: release@acme 1.0.0: locked commit" in str(
        stop.value
    )
    assert "could not be fetched" in str(stop.value)


@pytest.mark.parametrize(
    "extends, pinned",
    [("release:base", ["release"]), ("tools:base", ["release", "tools"])],
    ids=["through-a-pinned-plugin", "through-a-plugin-not-touched-yet"],
)
def test_a_revision_resolves_against_pinned_plugins(home, extends, pinned):
    """A node a revision adds runs on the versions the item runs on; a plugin
    the item had not read from yet joins its pins at the lock's version."""
    repo, config_dir, plugins_dir = home
    library = {
        "tasks": {"base": AGENT},
        "nodes": {"extra": {"kind": "exec", "tasks": [{"id": "t", "extends": extends}]}},
    }
    (config_dir / "library.yaml").write_text(json.dumps(library))
    snapshot = MaterializedChain.from_json(_stored(_resolved(config_dir, plugins_dir, "mine")))
    newer = _release("1.1.0", "o3")
    newer["library"]["tasks"]["base"]["prompt"] = "the 1.1.0 prompt"
    publish(repo, "release", **newer)
    _update(config_dir, plugins_dir, [ID])
    live = TemplateLibrary.from_yaml_dir(
        config_dir, plugins=load.installed(config_dir, plugins_dir)
    )
    add = Add(after="n", node=AddedNode(id="x", extends="extra"), evidence="e")

    revised = revise(snapshot, ChangeSet(rationale="r", add=[add]), at=None, library=live)

    # 1.0.0's task, like `tools`', carries the fixture prompt; 1.1.0's does not.
    assert revised.chain.chain.nodes[1].tasks[0].prompt == AGENT["prompt"]
    assert sorted(revised.chain.plugins) == pinned
    assert revised.chain.plugins["release"]["version"] == "1.0.0"
