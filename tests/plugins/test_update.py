"""What a candidate plugin may not carry, checked before anything is written."""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from support.harness import git
from support.plugins import AGENT, chain, instance, make_collection, plugin_json, publish

from kraft.config import read_yaml, write_yaml
from kraft.plugins import load, manifest, update
from kraft.plugins.config import PluginEntry, PluginsLock
from kraft.policy import PolicyError


def _files(library=None, chains=None):
    files = {}
    if library is not None:
        files["library.yaml"] = ("100644", yaml.safe_dump(library).encode())
    for name, body in (chains or {}).items():
        files[f"chains/{name}.yaml"] = ("100644", yaml.safe_dump(body).encode())
    return files


def _manifest(**requires):
    data = plugin_json("release", requires={"kraft": "2", "harnesses": ["codex"], **requires})
    return manifest.plugin(data, "plugin.json")


def _task(**fields):
    return {"tasks": {"base": {**AGENT, **fields}}}


def _gate_chain(**gate):
    return {"nodes": [*chain("base")["nodes"], {"id": "ok", "kind": "gate", **gate}]}


JUDGE = {
    "nodes": {
        "fix": {
            "kind": "exec",
            "tasks": [{"id": "t", "extends": "base"}],
            "fix_loop": {
                "tasks": [{"id": "repair", "extends": "base"}],
                "judge": {**AGENT, "id": "judge", "policy": {"grants": ["git-push"]}},
            },
        }
    }
}


@pytest.mark.parametrize(
    ("library", "chains", "why"),
    [
        ({"tasks": {"run": {"kind": "subprocess", "command": "make"}}}, {}, "subprocess task"),
        (_task(policy={"sandbox": {"image": "x"}}), {}, "policy.sandbox is not a limit"),
        (_task(policy={"unrestricted_network": True}), {}, "policy.unrestricted_network"),
        (_task(policy={"grants": ["git-push"]}), {}, "policy.grants is not a limit"),
        (_task(policy={"allowed_tools": ["Bash"]}), {}, "policy.allowed_tools"),
        (_task(policy={"allowed_harnesses": ["codex"]}), {}, "policy.allowed_harnesses"),
        (
            {"tasks": {"base": AGENT}},
            {"ship": {**chain("base"), "policy": {"escalation_harness": "codex"}}},
            "policy.escalation_harness",
        ),
        (_task(policy={"something_new": 1}), {}, "policy.something_new is not a limit"),
        (
            {"tasks": {"base": AGENT}},
            {"ship": _gate_chain(policy={"grants": ["x"]})},
            r"nodes\[ok\]: policy.grants",
        ),
        ({"tasks": {"base": AGENT}, **JUDGE}, {}, r"nodes.fix.fix_loop.judge: policy.grants"),
        (_task(harness="claude"), {}, "harness 'claude' is not listed"),
        (_task(extends="other:base"), {}, "reaches outside the plugin"),
    ],
    ids=[
        "subprocess",
        "sandbox",
        "unrestricted-network",
        "grants",
        "allowed-tools",
        "allowed-harnesses",
        "escalation-harness",
        "unknown-policy-key",
        "gate-scope",
        "judge-task",
        "unlisted-harness",
        "reference-to-another-plugin",
    ],
)
def test_a_plugin_cannot_carry(library, chains, why):
    with pytest.raises(update.Refused, match=why):
        update.check(_manifest(), _files(library, chains))


@pytest.mark.parametrize(
    "policy",
    [{"time_cap_minutes": 90}, {"budget_usd": 8, "token_budget": 100000}, {"deny_tools": ["Bash"]}],
    ids=["time-cap", "budget", "deny-tools"],
)
def test_a_plugin_may_set_limits(policy):
    """Limits are not permissions: how long, how much and how many times are
    the author's to set, and a task named like a field is still a task."""
    library = {"tasks": {"base": {**AGENT, "policy": policy}, "policy": AGENT, "harness": AGENT}}
    assert update.problems(_manifest(), _files(library, {"ship": chain("base")})) == []


@pytest.mark.parametrize(
    ("skill", "installed", "refused"),
    [
        ("deploy-review", {"mobile"}, False),
        ("release:deploy-review", {"mobile"}, False),
        ("kraft:spec", {"mobile"}, False),
        ("superpowers:brainstorming", {"mobile"}, False),
        ("mobile:deploy-review", {"mobile"}, True),
    ],
    ids=["bare", "own-name", "kraft", "another-tools-plugin", "another-kraft-plugin"],
)
def test_a_skill_may_not_reach_into_another_kraft_plugin(skill, installed, refused):
    found = update.problems(_manifest(), _files(_task(skill=skill)), other_namespaces=installed)
    assert bool(found) is refused


@pytest.mark.parametrize(
    ("fallback", "why"),
    [
        ({"harness": "claude"}, "not listed in requires.harnesses"),
        ({"profile": "other:deep"}, "reaches outside the plugin"),
    ],
    ids=["unlisted-harness", "another-plugins-profile"],
)
def test_a_plugins_profiles_are_checked_like_its_tasks(fallback, why):
    files = _files({"tasks": {"base": AGENT}}, {"ship": chain("base")})
    # A profile named like a field is still a profile.
    profiles = {"profiles": {"deep": {"fallback": [fallback]}, "policy": {"model": {"codex": "m"}}}}
    files["profiles.yaml"] = ("100644", yaml.safe_dump(profiles).encode())
    (found,) = update.problems(_manifest(), files)
    assert why in found and found.startswith("profiles.yaml: profiles.deep")


def test_a_file_nested_too_deep_is_refused_like_any_other():
    """Not a RecursionError that ends the whole run, every other plugin's update with it."""
    deep = 5000
    files = {"library.yaml": ("100644", b"a: " + b"[" * deep + b"]" * deep)}
    assert "cannot parse" in update.problems(_manifest(), files)[0]
    with pytest.raises(manifest.ManifestError, match="nested deeper"):
        manifest.parse('{"a":' * deep + "1" + "}" * deep, "plugin.json")


# ── the update pipeline ──

RELEASE = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}
ID = "release@acme"


@pytest.fixture
def acme(tmp_path):
    """A git collection `acme` publishing `release` 1.0.0, a second plugin
    `tools`, and a home that knows the collection and has installed neither."""
    repo = make_collection(tmp_path, {"release": RELEASE, "tools": RELEASE})
    return repo, instance(tmp_path, {"acme": {"git": repo.as_uri()}})


def _run(home, ids=(ID,), *, accept=True, install=(), **kw):
    """`update` on `home`, answering every review with `accept`; `install`
    names the ids given a new `plugins.yaml` entry. Results by plugin id."""
    config_dir, plugins_dir = home
    reviews = []

    def answer(review):
        reviews.append(review)
        return accept(review) if callable(accept) else accept

    entry = kw.pop("entry", True)
    try:
        results = update.update(
            None if ids is None else list(ids),
            accept=answer,
            entries={i: entry for i in install} or None,
            config_dir=config_dir,
            plugins_root=plugins_dir,
            **kw,
        )
    except update.Refused as exc:
        results = [update.Result(i, "refused", problems=tuple(exc.problems)) for i in ids]
    return {r.plugin_id: r for r in results}


def _written(home):
    """Everything an update may write: both files and the store's entries."""
    config_dir, plugins_dir = home
    store = plugins_dir / "store"
    return (
        (config_dir / "plugins.yaml").read_text(),
        (config_dir / "plugins.lock").read_text() if (config_dir / "plugins.lock").exists() else "",
        sorted(p.name for p in store.iterdir()) if store.is_dir() else [],
    )


def _locked(home, plugin_id=ID):
    return PluginsLock.load(home[0] / "plugins.lock").plugins[plugin_id]


def test_install_locks_the_resolved_commit(acme):
    repo, home = acme
    head = git(repo, "rev-parse", "HEAD")

    assert _run(home, install=[ID])[ID].outcome == "applied"

    locked = _locked(home)
    assert (locked.commit, locked.version, locked.namespace, locked.ref) == (
        head,
        "1.0.0",
        "release",
        None,
    )
    assert [(p.id, p.left_out) for p in load.installed(*home, verify=True)] == [(ID, None)]
    mirror = next((home[1] / "mirrors").iterdir())
    assert git(mirror, "rev-parse", f"refs/kraft/pinned/{head}") == head


@pytest.mark.parametrize(
    "directory, version, outcome, locks",
    [
        (False, "1.0.0", "current", "1.0.0"),
        (False, "1.1.0", "applied", "1.1.0"),
        (False, "0.9.0", "applied", "0.9.0"),
        (True, "1.0.0", "applied", "1.0.0"),
    ],
    ids=[
        "same-version-new-commit",
        "version-raised",
        "version-lowered",
        "directory-collection-updates-on-change",
    ],
)
def test_an_update_waits_for_a_version_change(tmp_path, directory, version, outcome, locks):
    repo = make_collection(tmp_path, {"release": RELEASE})
    source = {"path": str(repo)} if directory else {"git": repo.as_uri()}
    home = instance(tmp_path, {"acme": source})
    _run(home, install=[ID])
    first = _locked(home)
    publish(repo, "release", version=version, library={"tasks": {"base": AGENT, "more": AGENT}})

    result = _run(home)[ID]

    assert (result.outcome, _locked(home).version) == (outcome, locks)
    assert (_locked(home).digest == first.digest) == (outcome == "current")
    if outcome == "current":
        assert "its files changed" in result.note and "--re-install" in result.note
    if version == "0.9.0":
        assert result.review.reach == ("downgrade: 1.0.0 -> 0.9.0",)


def test_a_collection_moved_to_another_url_is_reviewed_as_reach(tmp_path):
    """Another repository under the same name is a change a person reads,
    whatever its version says."""
    repo = make_collection(tmp_path, {"release": RELEASE})
    home = instance(tmp_path, {"acme": {"git": repo.as_uri()}})
    _run(home, install=[ID])
    fork = tmp_path / "fork"
    shutil.copytree(repo, fork)
    config = read_yaml(home[0] / "plugins.yaml")
    config["collections"]["acme"]["git"] = fork.as_uri()
    write_yaml(home[0] / "plugins.yaml", config)

    result = _run(home)[ID]

    assert result.outcome == "applied" and _locked(home).git == fork.as_uri()
    assert result.review.reach[0] == f"collection URL changed: {repo.as_uri()} -> {fork.as_uri()}"
    assert _run(home)[ID].outcome == "current"


def test_a_lock_that_recorded_no_url_is_not_a_moved_collection(acme):
    repo, home = acme
    _run(home, install=[ID])
    lock = read_yaml(home[0] / "plugins.lock")
    del lock["plugins"][ID]["git"]
    write_yaml(home[0] / "plugins.lock", lock)

    assert _run(home)[ID].outcome == "current"


def test_an_installed_copy_that_does_not_read_is_said_in_the_review(acme):
    """Everything is then shown as new: the review says the comparison is
    missing, and that line holds an auto-update."""
    repo, home = acme
    _run(home, install=[ID])
    store = home[1] / "store" / _locked(home).digest.removeprefix("sha256:")
    manifest_file = store / ".kraft" / "plugin.json"
    manifest_file.parent.chmod(0o755)
    manifest_file.chmod(0o644)
    manifest_file.write_text("{")
    publish(repo, "release", version="1.1.0", library={"tasks": {"base": AGENT, "more": AGENT}})

    found = _run(home)[ID].review

    assert found.reach[0] == "the installed 1.0.0 could not be read to compare with"
    assert update.may_apply_unattended(found) is not None


def test_re_install_takes_the_newest_commit(acme):
    repo, home = acme
    _run(home, install=[ID])
    head = publish(repo, "release", library={"tasks": {"base": AGENT, "more": AGENT}})

    assert _run(home, re_install=True)[ID].outcome == "applied"

    assert _locked(home).commit == head


def test_a_ref_change_is_reviewed_whatever_the_version(tmp_path):
    repo = make_collection(tmp_path, {"release": RELEASE})
    git(repo, "tag", "v1")
    home = instance(tmp_path, {"acme": {"git": repo.as_uri(), "ref": "v1"}})
    _run(home, install=[ID])
    head = publish(repo, "release", library={"tasks": {"base": AGENT, "more": AGENT}})
    assert _run(home)[ID].outcome == "current"  # v1 still names the first commit
    config = read_yaml(home[0] / "plugins.yaml")
    config["collections"]["acme"]["ref"] = "main"
    write_yaml(home[0] / "plugins.yaml", config)
    assert load.installed(*home)[0].left_out is None  # still loads, from its lock

    result = _run(home)[ID]

    assert (result.outcome, result.review.content) == ("applied", ("component tasks.more added",))
    assert (_locked(home).ref, _locked(home).commit) == ("main", head)


@pytest.mark.parametrize("installed_first", [False, True], ids=["install", "update"])
def test_a_declined_update_writes_nothing(acme, installed_first):
    repo, home = acme
    if installed_first:
        _run(home, install=[ID])
        publish(repo, "release", version="1.1.0")
    before = _written(home)

    result = _run(home, accept=False, install=[] if installed_first else [ID])[ID]

    assert (result.outcome, result.review.new_version) == (
        "declined",
        "1.1.0" if installed_first else "1.0.0",
    )
    assert _written(home) == before


def test_a_batch_continues_past_a_refused_plugin(acme):
    repo, home = acme
    _run(home, [ID, "tools@acme"], install=[ID, "tools@acme"])
    publish(repo, "release", version="1.1.0", library=_task(kind="subprocess"))
    publish(repo, "tools", version="1.1.0")

    results = _run(home, [ID, "tools@acme"])

    assert (results[ID].outcome, results["tools@acme"].outcome) == ("refused", "applied")
    assert "subprocess" in results[ID].problems[0]
    assert (_locked(home).version, _locked(home, "tools@acme").version) == ("1.0.0", "1.1.0")


SHIP_GONE = {"version": "1.1.0", "files": {"chains/ship.yaml": yaml.safe_dump(chain("nope"))}}


@pytest.mark.parametrize(
    "local, config_file, new, names",
    [
        (
            {"chains": {"mine": chain("release:base")}},
            None,
            {"version": "1.1.0", "library": {"tasks": {"other": AGENT}}, "chains": {}},
            "chain mine would stop resolving",
        ),
        (
            {},
            ("repos.yaml", {"repos": [{"path": "/r", "id": "r", "default_chain": "release:ship"}]}),
            SHIP_GONE,
            "repos.yaml: repos[r]: default_chain 'release:ship' would stop resolving",
        ),
        (
            {},
            ("intake.yaml", {"schedules": [{"id": "nightly", "chain": "release:ship"}]}),
            SHIP_GONE,
            "intake.yaml: schedules[nightly]: chain 'release:ship' would stop resolving",
        ),
    ],
    ids=["local-chain", "repos-default-chain", "intake-schedule"],
)
def test_an_update_that_breaks_a_reference_is_refused(tmp_path, local, config_file, new, names):
    repo = make_collection(tmp_path, {"release": RELEASE})
    home = instance(tmp_path, {"acme": {"git": repo.as_uri()}}, **local)
    if config_file:
        write_yaml(home[0] / config_file[0], config_file[1])
    _run(home, install=[ID])
    publish(repo, "release", **new)
    before = _written(home)

    result = _run(home)[ID]

    assert result.outcome == "refused"
    assert any(names in why for why in result.problems), result.problems
    assert _written(home) == before


def _refused(repo, home, why, **new):
    """Publish `new` as `release` 1.1.0 over an installed 1.0.0 and assert the
    update is refused for `why`, with the lock where it was."""
    _run(home, install=[ID])
    publish(repo, "release", version="1.1.0", **new)
    before = _written(home)
    result = _run(home)[ID]
    assert result.outcome == "refused"
    assert any(why in problem for problem in result.problems), result.problems
    assert _written(home) == before


def test_a_broken_profile_pairing_is_refused(acme):
    """Lint does not check `profile:`; the launch check does."""
    repo, home = acme
    harnesses = read_yaml(home[0] / "harnesses.yaml")
    write_yaml(
        home[0] / "harnesses.yaml", {**harnesses, "profiles": {"fast": {"model": {"claude": "m"}}}}
    )
    _refused(
        repo,
        home,
        "its chain release:ship does not resolve: chain 'release:ship' task 'n.main.t': "
        "profile 'fast' has no model for provider 'codex'",
        library=_task(profile="fast"),
    )


def test_a_limit_above_the_instance_maxima_is_refused(acme):
    repo, home = acme
    shipped = read_yaml(Path(__file__).resolve().parents[2] / "config" / "policy.yaml")
    write_yaml(home[0] / "policy.yaml", {**shipped, "maxima": {"tasks": {"budget_usd": 5}}})
    _refused(
        repo,
        home,
        "sets budget_usd 8 > the administrator maximum 5 (maxima.tasks.budget_usd)",
        library=_task(policy={"budget_usd": 8}),
    )


def test_a_policy_that_does_not_load_stops_the_update(acme):
    """A candidate is never judged without the maxima: no result, no write."""
    repo, home = acme
    _run(home, install=[ID])
    publish(repo, "release", version="1.1.0")
    (home[0] / "policy.yaml").write_text("default: [\n")
    before = _written(home)

    with pytest.raises(PolicyError):
        _run(home)

    assert _written(home) == before


@pytest.mark.parametrize(
    "local, outcome",
    [({}, "applied"), ({"chains": {"mine": chain("release:base")}}, "refused")],
    ids=["own-chains-go", "local-chain-still-extends-the-old-namespace"],
)
def test_an_alias_change_only_breaks_what_is_not_the_plugins(tmp_path, local, outcome):
    repo = make_collection(tmp_path, {"release": RELEASE})
    home = instance(tmp_path, {"acme": {"git": repo.as_uri()}}, **local)
    _run(home, install=[ID])
    before = _written(home)

    result = _run(home, install=[ID], entry=PluginEntry(**{"as": "rel"}))[ID]

    assert result.outcome == outcome, result
    if outcome == "applied":
        assert _locked(home).namespace == "rel"
    if outcome == "refused":
        assert any("chain mine would stop resolving" in why for why in result.problems)
        assert _written(home) == before


def test_requires_must_be_satisfied(acme):
    repo, home = acme
    _refused(
        repo,
        home,
        "requires agent profile 'deep', which harnesses.yaml does not define",
        manifest={"requires": {"kraft": "2", "harnesses": ["codex"], "profiles": ["deep"]}},
    )


def test_an_update_for_another_major_keeps_the_lock(acme, monkeypatch):
    import kraft.update

    repo, home = acme
    monkeypatch.setattr(kraft.update, "installed", lambda: "2.4.0")
    _refused(
        repo,
        home,
        "this is Kraft 2.4.0",
        manifest={"requires": {"kraft": "3", "harnesses": ["codex"]}},
    )


def test_a_left_out_plugin_still_updates(acme):
    repo, home = acme
    _run(home, install=[ID])
    config = read_yaml(home[0] / "harnesses.yaml")
    write_yaml(home[0] / "harnesses.yaml", {"harnesses": {}})
    assert "requires harness 'codex'" in load.installed(*home)[0].left_out
    write_yaml(home[0] / "harnesses.yaml", {"harnesses": {"other": config["harnesses"]["codex"]}})
    publish(
        repo,
        "release",
        version="1.1.0",
        manifest={"requires": {"kraft": "2", "harnesses": ["other"]}},
        library=_task(harness="other"),
    )

    assert _run(home)[ID].outcome == "applied"

    assert [(p.version, p.left_out) for p in load.installed(*home)] == [("1.1.0", None)]


def _alias_released(home):
    """`tools@acme` drops its alias `release` by hand: pending, so its lock still holds it."""
    config = read_yaml(home[0] / "plugins.yaml")
    config["plugins"]["tools@acme"] = True
    write_yaml(home[0] / "plugins.yaml", config)


def _repo_named_release(home):
    write_yaml(home[0] / "repos.yaml", {"repos": [{"path": "/r", "id": "release"}]})


@pytest.mark.parametrize(
    "first, entry, prepare, why",
    [
        (None, PluginEntry(**{"as": "kraft"}), None, "'kraft' is Kraft's own namespace"),
        ((ID, True), PluginEntry(**{"as": "release"}), None, "namespace 'release' is taken by"),
        (
            ("tools@acme", PluginEntry(**{"as": "release"})),
            True,
            _alias_released,
            "namespace 'release' is still held by tools@acme",
        ),
        (None, True, _repo_named_release, "its namespace 'release' is now a repository id"),
        ((ID, True), None, None, None),
    ],
    ids=[
        "kraft",
        "taken-by-alias",
        "held-by-a-pending-alias",
        "taken-by-repo-id",
        "same-id-is-not-a-clash",
    ],
)
def test_a_namespace_is_refused(acme, first, entry, prepare, why):
    """`first` is installed, then `release` (or, where `release` is `first`,
    `tools`) is installed with `entry`; None re-installs `first` itself."""
    repo, home = acme
    if first:
        _run(home, [first[0]], install=[first[0]], entry=first[1])
    if prepare:
        prepare(home)
    target = "tools@acme" if first and first[0] == ID and entry is not None else ID

    result = _run(
        home, [target], install=[target] if entry is not None else [], entry=entry, re_install=True
    )[target]

    assert (result.outcome, any(why in p for p in result.problems) if why else None) == (
        ("refused", True) if why else ("applied", None)
    ), result


def test_a_renamed_collection_is_refused(tmp_path):
    repo = make_collection(tmp_path, {"release": RELEASE})
    home = instance(tmp_path, {"acme-co": {"git": repo.as_uri()}})

    result = _run(home, ["release@acme-co"], install=["release@acme-co"])["release@acme-co"]

    assert result.outcome == "refused"
    assert "now calls itself acme; re-add it under that name" in result.problems[0]
    assert not (home[1] / "store").exists()


def test_an_unpublished_plugin_stays_locked(acme):
    repo, home = acme
    _run(home, install=[ID])
    before = _written(home)
    listed = json.loads((repo / ".kraft/collection.json").read_text())
    listed["plugins"] = [e for e in listed["plugins"] if e["name"] != "release"]
    (repo / ".kraft/collection.json").write_text(json.dumps(listed))
    publish(repo, "tools", version="1.1.0")

    result = _run(home)[ID]

    assert (result.outcome, result.problems) == (
        "unpublished",
        ("is no longer published by acme; it stays on 1.0.0",),
    )
    assert _written(home) == before
    assert load.installed(*home)[0].left_out is None


def test_a_collection_that_cannot_be_fetched_fails_only_its_plugins(acme, tmp_path):
    repo, home = acme
    config = read_yaml(home[0] / "plugins.yaml")
    config["collections"]["gone"] = {"git": (tmp_path / "nowhere").as_uri()}
    write_yaml(home[0] / "plugins.yaml", config)

    results = _run(home, [ID, "x@gone"], install=[ID, "x@gone"])

    assert (results[ID].outcome, results["x@gone"].outcome, results["x@gone"].kind) == (
        "applied",
        "failed",
        "network",
    )
    assert list(read_yaml(home[0] / "plugins.yaml")["plugins"]) == [ID]


def test_a_collection_that_is_not_utf8_fails_only_its_plugins(acme, tmp_path):
    repo, home = acme
    bad = tmp_path / "bad"
    (bad / ".kraft").mkdir(parents=True)
    (bad / ".kraft" / "collection.json").write_bytes(b"\xff\xfe")
    config = read_yaml(home[0] / "plugins.yaml")
    config["collections"]["bad"] = {"path": str(bad)}
    write_yaml(home[0] / "plugins.yaml", config)

    results = _run(home, [ID, "x@bad"], install=[ID, "x@bad"])

    assert (results[ID].outcome, results["x@bad"].outcome) == ("applied", "refused")
    assert "not valid UTF-8" in results["x@bad"].problems[0]


def test_a_second_writer_waits_then_fails(acme, monkeypatch):
    repo, home = acme
    monkeypatch.setattr(update, "LOCK_WAIT_S", 0.2)
    before = _written(home)

    with update.write_lock(home[1]):
        result = _run(home, install=[ID])[ID]

    assert (result.outcome, result.kind, result.problems) == (
        "failed",
        "busy",
        ("another plugin change is running; try again",),
    )
    assert _written(home) == before
    assert _run(home, install=[ID])[ID].outcome == "applied"  # released, the next writer gets in


def test_a_lock_changed_during_review_writes_nothing(acme):
    """A review left open at its prompt holds no lock; what it reviewed may
    no longer be what is installed."""
    repo, home = acme
    _run(home, install=["tools@acme"])
    before = _written(home)

    def a_teammate_pulls_a_new_lock(review):
        with (home[0] / "plugins.lock").open("a") as fh:
            fh.write("# pulled\n")
        return True

    result = _run(home, install=[ID], accept=a_teammate_pulls_a_new_lock)[ID]

    assert (result.outcome, result.kind) == ("failed", "busy")
    assert "plugins changed while you reviewed" in result.problems[0]
    assert _written(home) == (before[0], before[1] + "# pulled\n", before[2])


def test_an_orphaned_lock_entry_is_dropped_by_the_next_update(acme):
    repo, home = acme
    _run(home, [ID, "tools@acme"], install=[ID, "tools@acme"])
    config = read_yaml(home[0] / "plugins.yaml")
    del config["plugins"]["tools@acme"]
    write_yaml(home[0] / "plugins.yaml", config)
    publish(repo, "release", version="1.1.0")

    _run(home, None)

    assert list(PluginsLock.load(home[0] / "plugins.lock").plugins) == [ID]


#: A local chain whose task names `release:notes`: another tool's plugin skill
#: until a Kraft plugin takes the namespace `release`.
_USES = {**AGENT, "id": "t", "skill": "release:notes"}
USES_A_SKILL = {"chains": {"mine": {"nodes": [{"id": "n", "kind": "exec", "tasks": [_USES]}]}}}


@pytest.mark.parametrize(
    "local, plugin, new, reach",
    [
        (
            USES_A_SKILL,
            {**RELEASE, "skills": {"notes": "# notes\n"}},
            None,
            "chains/mine.yaml: nodes[n].tasks[t]: skill 'release:notes' is now read from "
            "this plugin",
        ),
        (
            {"chains": {"mine": chain("release:base")}},
            {"library": _task(policy={"budget_usd": 5}), "chains": {"ship": chain("base")}},
            {"library": _task(policy={"budget_usd": 9})},
            "mine.nodes[n].tasks[t].policy: limit budget_usd raised, 5 -> 9",
        ),
    ],
    ids=["captured-ref", "local-chain-through-the-plugin"],
)
def test_the_review_covers_the_instances_own_chains(tmp_path, local, plugin, new, reach):
    """What the plugin's own files cannot show: a reference its namespace
    takes over on install, and a local chain that extends it."""
    repo = make_collection(tmp_path, {"release": plugin})
    home = instance(tmp_path, {"acme": {"git": repo.as_uri()}}, **local)
    result = _run(home, install=[ID])[ID]
    if new:
        publish(repo, "release", version="1.1.0", **new)
        result = _run(home)[ID]

    assert (result.outcome, reach in result.review.reach) == ("applied", True), result.review


def test_a_captured_reference_the_plugin_does_not_provide_is_refused(tmp_path):
    """`skill: release:notes` was handed to the agent as another tool's
    plugin skill; once `release` is a Kraft plugin it must provide it."""
    repo = make_collection(tmp_path, {"release": RELEASE})
    home = instance(tmp_path, {"acme": {"git": repo.as_uri()}}, **USES_A_SKILL)

    result = _run(home, install=[ID])[ID]

    assert result.outcome == "refused"
    assert "chain mine would stop resolving" in result.problems[0]
    assert not (home[0] / "plugins.lock").exists()


@pytest.mark.parametrize("plugin_id", ["tools@acme", "tools"], ids=["not-installed", "not-an-id"])
def test_an_id_that_is_not_installed_is_refused(acme, plugin_id):
    _repo, home = acme
    _run(home, install=[ID])

    result = _run(home, [plugin_id])[plugin_id]

    assert (result.outcome, result.problems) == (
        "refused",
        ("is not in plugins.yaml; install it first",),
    )
