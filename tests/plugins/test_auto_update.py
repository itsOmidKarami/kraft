"""Auto-update: what a plugin set to update itself may take with nobody
watching, what is held for a person, and where the outcome is recorded."""

import shutil

import pytest
from support.harness import git
from support.plugins import AGENT, chain, edit_plugins_yaml, instance, make_collection, publish

from kraft.plugins import fetch, update
from kraft.plugins.config import PluginEntry, PluginsLock

ID = "release@acme"
GATED = {"nodes": [*chain("base")["nodes"], {"id": "ok", "kind": "gate"}]}
RELEASE = {
    "library": {"tasks": {"base": {**AGENT, "policy": {"budget_usd": 5}}}},
    "chains": {"ship": GATED},
}


@pytest.fixture
def home(tmp_path):
    """`release@acme` installed and set to update itself, `tools@acme`
    installed and not; the collection, the config directory and `run/plugins`."""
    repo = make_collection(tmp_path, {"release": RELEASE, "tools": RELEASE})
    config_dir, plugins_dir = instance(tmp_path, {"acme": {"git": repo.as_uri()}})
    results = update.update(
        [ID, "tools@acme"],
        accept=lambda review: True,
        entries={ID: PluginEntry(auto_update=True), "tools@acme": True},
        config_dir=config_dir,
        plugins_root=plugins_dir,
    )
    assert [r.outcome for r in results] == ["applied", "applied"]
    return repo, config_dir, plugins_dir


def _versions(config_dir):
    return {i: e.version for i, e in PluginsLock.load(config_dir / "plugins.lock").plugins.items()}


def _collection_on(written):
    written["collections"]["acme"]["auto_update"] = True
    written["plugins"] = {ID: True, "tools@acme": True}


def _neither(written):
    written["plugins"][ID] = True


@pytest.mark.parametrize(
    "setting, updated",
    [(None, [ID]), (_collection_on, [ID, "tools@acme"]), (_neither, [])],
    ids=["plugin-on", "collection-on", "neither-is-not-fetched"],
)
def test_who_auto_updates(home, monkeypatch, setting, updated):
    repo, config_dir, plugins_dir = home
    if setting:
        edit_plugins_yaml(config_dir, setting)
    for name in ("release", "tools"):
        publish(repo, name, version="1.1.0", skills={"notes": "new in 1.1.0"})
    if not updated:
        monkeypatch.setattr(fetch, "fetch", lambda *a: pytest.fail("fetched a collection"))

    outcomes = update.auto_update(config_dir, plugins_dir)

    assert {i: o["outcome"] for i, o in outcomes.items()} == dict.fromkeys(updated, "applied")
    assert [i for i, v in _versions(config_dir).items() if v == "1.1.0"] == updated
    assert sorted(update.read_status(plugins_dir)) == sorted(updated)


OTHER = {"kraft": "2", "harnesses": ["codex"], "profiles": []}


@pytest.mark.parametrize(
    "new, why",
    [
        ({"version": "1.1.0", "chains": {"ship": chain("base")}}, "gate removed"),
        (
            {
                "version": "1.1.0",
                "library": {"tasks": {"base": {**AGENT, "policy": {"budget_usd": 9}}}},
            },
            "limit budget_usd raised, 5 -> 9",
        ),
        ({"version": "1.1.0", "manifest": {"requires": OTHER}}, "requires changed"),
        ({"version": "0.9.0", "skills": {"notes": "older"}}, "downgrade: 1.0.0 -> 0.9.0"),
        ({"version": "1.1.0-rc.1", "skills": {"notes": "a candidate"}}, "moves onto a pre-release"),
    ],
    ids=["gate-removed", "limit-raised", "requires", "downgrade", "pre-release"],
)
def test_auto_update_holds(home, new, why):
    """Anything that can change how far a run reaches waits for a person."""
    repo, config_dir, plugins_dir = home
    publish(repo, "release", **new)
    before = (config_dir / "plugins.lock").read_text()

    outcome = update.auto_update(config_dir, plugins_dir)[ID]

    assert outcome["outcome"] == "held" and why in outcome["message"], outcome
    assert outcome["message"].endswith("; run kraft admin plugin update")
    assert (config_dir / "plugins.lock").read_text() == before
    assert update.read_status(plugins_dir)[ID]["outcome"] == "held"


def _listed_not_locked(repo, config_dir):
    publish(repo, "extra", **RELEASE)
    edit_plugins_yaml(
        config_dir, lambda w: w["plugins"].update({"extra@acme": {"auto_update": True}})
    )


def _same_version_new_commit(repo, config_dir):
    publish(repo, "release", skills={"notes": "changed without a version"})


def _ref_changed_by_hand(repo, config_dir):
    git(repo, "tag", "v2")
    publish(repo, "release", version="1.1.0", skills={"notes": "new"})
    edit_plugins_yaml(config_dir, lambda w: w["collections"]["acme"].update(ref="main"))


def _url_changed_by_hand(repo, config_dir):
    fork = repo.parent / "fork"
    shutil.copytree(repo, fork)
    publish(fork, "release", version="1.1.0", skills={"notes": "from somewhere else"})
    edit_plugins_yaml(config_dir, lambda w: w["collections"]["acme"].update(git=fork.as_uri()))


def _orphaned_in_the_lock(repo, config_dir):
    edit_plugins_yaml(config_dir, lambda w: w["plugins"].pop("tools@acme"))


@pytest.mark.parametrize(
    "happens, outcome",
    [
        (_listed_not_locked, None),
        (_orphaned_in_the_lock, None),
        (_same_version_new_commit, "up to date"),
        (_ref_changed_by_hand, "held: ref"),
        (_url_changed_by_hand, "held: collection URL"),
    ],
    ids=["installs", "an-orphan", "re-installs", "applies-a-ref-change", "follows-a-new-url"],
)
def test_auto_update_never(home, happens, outcome):
    repo, config_dir, plugins_dir = home
    happens(repo, config_dir)
    before = (config_dir / "plugins.lock").read_text()

    outcomes = update.auto_update(config_dir, plugins_dir)

    assert sorted(outcomes) == [ID]
    state, _, what = (outcome or "up to date").partition(": ")
    assert outcomes[ID]["outcome"] == state
    assert (config_dir / "plugins.lock").read_text() == before
    if state == "held":
        assert outcomes[ID]["message"] == f"{what} change pending; run kraft admin plugin update"


def test_a_failed_auto_update_keeps_the_lock_and_records_why(home):
    repo, config_dir, plugins_dir = home
    repo.rename(repo.parent / "gone")
    before = _versions(config_dir)

    outcome = update.auto_update(config_dir, plugins_dir)[ID]

    assert (outcome["outcome"], outcome["kind"]) == ("failed", "network")
    assert _versions(config_dir) == before
    recorded = update.read_status(plugins_dir)[ID]
    assert (recorded["outcome"], recorded["kind"]) == ("failed", "network") and recorded["at"]


def test_a_newer_pre_release_follows_a_pre_release(home):
    """A plugin already on a candidate takes the next one; only the move
    onto a pre-release is held."""
    repo, config_dir, plugins_dir = home
    publish(repo, "release", version="1.1.0-rc.1", skills={"notes": "a candidate"})
    update.update([ID], accept=lambda r: True, config_dir=config_dir, plugins_root=plugins_dir)
    publish(repo, "release", version="1.1.0-rc.2", skills={"notes": "the next candidate"})

    assert update.auto_update(config_dir, plugins_dir)[ID]["outcome"] == "applied"
    assert _versions(config_dir)[ID] == "1.1.0-rc.2"


def test_a_person_taking_a_held_update_clears_its_warning(home):
    """Health and doctor stop saying "held" once the update it named is applied."""
    repo, config_dir, plugins_dir = home
    publish(repo, "release", version="1.1.0", chains={"ship": chain("base")})
    assert update.auto_update(config_dir, plugins_dir)[ID]["outcome"] == "held"
    publish(repo, "tools", version="1.1.0", chains={"ship": chain("base")})

    update.update([ID], accept=lambda r: True, config_dir=config_dir, plugins_root=plugins_dir)

    assert update.read_status(plugins_dir) == {}
    assert _versions(config_dir)[ID] == "1.1.0"
