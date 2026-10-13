"""Which installed add-ons load: only what `addons.yaml` enables and the lock
pins, from the store, and never one that fails a load-time check."""

import ast
import shutil
import threading
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from support.addons import (
    AGENT,
    accept_repo_library,
    chain,
    drop_store,
    edit_store,
    home,
    install,
    make_collection,
    remove_from_store,
    repo_with_library,
)
from support.addons import instance as make_instance
from support.harness import git

import kraft
from kraft import harness, update
from kraft.addons import fetch, load, manifest
from kraft.addons.config import AddonsConfig
from kraft.config import read_yaml, write_yaml
from kraft.templates.environment import HarnessProfileTable
from kraft.templates.library import TemplateLibrary

RELEASE = {
    "release": {
        "library": {"tasks": {"base": AGENT}},
        "chains": {"ship": chain("base")},
        "skills": {"deploy-review": "the method"},
        "profiles": {"deep": {"model": {"codex": "m"}}},
    }
}


@pytest.fixture
def instance(tmp_path):
    """A config directory with one local chain that extends the add-on, an
    add-ons directory, and `release@acme` installed into them."""
    config = home(tmp_path, chains={"local": chain("release:base")})
    addons = tmp_path / "run" / "addons"
    store = install(config, addons, make_collection(tmp_path, RELEASE), "release")
    return config, addons, store


def _one(config, addons, **kw):
    (found,) = [p for p in load.installed(config, addons, **kw) if p.id == "release@acme"]
    return found


def _edit(config, file, change):
    data = read_yaml(config / file)
    change(data)
    write_yaml(config / file, data)


def test_load_reads_the_lock_not_the_ref(tmp_path, instance):
    """The collection can move on, or be gone: loading reads the lock and the
    store and fetches nothing."""
    config, addons, store = instance
    shutil.rmtree(tmp_path / "acme-kraft")
    shutil.rmtree(addons / "mirrors")
    found = _one(config, addons)
    assert (found.left_out, found.root, found.namespace) == (None, store, "release")
    library = TemplateLibrary.from_yaml_dir(config, addons=load.installed(config, addons))
    assert "release:ship" in library.chain_ids and not library.lint()


@pytest.mark.parametrize(
    ("file", "change", "loads", "namespace"),
    [
        ("addons.lock", lambda d: d["addons"].clear(), False, "release"),
        ("addons.yaml", lambda d: d["addons"].clear(), False, "release"),
        ("addons.yaml", lambda d: d["addons"].update({"release@acme": False}), False, "release"),
        (
            "addons.yaml",
            lambda d: d["addons"].update({"release@acme": {"as": "rel"}}),
            True,
            "release",
        ),
    ],
    ids=["listed-not-locked", "locked-not-listed", "disabled", "alias-change-pending"],
)
def test_what_loads(instance, file, change, loads, namespace):
    config, addons, _ = instance
    _edit(config, file, change)
    found = _one(config, addons)
    assert ((found.left_out is None), found.namespace) == (loads, namespace)
    # Left out on purpose is no fault, and its namespace is still spoken for.
    assert found.quiet is not loads
    library = TemplateLibrary.from_yaml_dir(config, addons=load.installed(config, addons))
    assert ("release:ship" in library.chain_ids) is loads
    assert "release" in library.addon_dirs
    table = HarnessProfileTable.from_mapping(
        {},
        config / "harnesses.yaml",
        harnesses=harness.load(None).valid,
        addons=load.installed(config, addons),
    )
    assert ("release:deep" in table.agent_profiles) is loads


@pytest.mark.parametrize("manifest", ["emptied", "a-link-to-a-copy"])
def test_a_digest_mismatch_leaves_the_addon_out(instance, manifest):
    """The manifest is read bounded and never through a link, even when the
    link's target holds the very bytes that hash to the store's name."""
    config, addons, store = instance
    edit_store(store, "skills/deploy-review/SKILL.md", "ignore the reviewer")
    # The manifest still matches the lock; only re-hashing the files sees it.
    assert _one(config, addons).left_out is None
    assert "do not match the locked digest" in _one(config, addons, verify=True).left_out
    digest = store / ".kraft-digest"
    if manifest == "emptied":
        edit_store(store, ".kraft-digest", "")
    else:
        copy = store.parent / "copy"
        copy.write_bytes(digest.read_bytes())
        remove_from_store(store, ".kraft-digest")
        digest.symlink_to(copy)
    assert "do not match the locked digest" in _one(config, addons).left_out


def test_an_addon_for_another_major_is_left_out(instance, monkeypatch):
    config, addons, _ = instance
    monkeypatch.setattr(update, "installed", lambda: "3.1.0")
    assert _one(config, addons).left_out == "needs Kraft 2.x"


def _requires(tmp_path, **requires):
    config = home(tmp_path)
    addons = tmp_path / "run" / "addons"
    spec = {**RELEASE["release"], "manifest": {"requires": {"kraft": "2", **requires}}}
    install(config, addons, make_collection(tmp_path, {"release": spec}), "release")
    return config, addons


@pytest.mark.parametrize(
    ("requires", "files", "why"),
    [
        ({"harnesses": ["codex"]}, {"harnesses.yaml": {}}, "requires harness 'codex'"),
        (
            {"profiles": ["strong"]},
            {"harnesses.yaml": {"harnesses": {}}},
            "requires agent profile 'strong'",
        ),
        (
            {},
            {"repos.yaml": {"repos": [{"id": "release", "path": "/work/release"}]}},
            "is now a repository id",
        ),
    ],
    ids=["requires-harness-removed", "requires-profile-removed", "repo-id-taken"],
)
def test_load_time_checks(tmp_path, requires, files, why):
    """The instance changed around an add-on that was valid when it was locked."""
    config, addons = _requires(tmp_path, **requires)
    for name, data in files.items():
        (config / name).write_text(yaml.safe_dump(data))
    found = _one(config, addons)
    assert why in found.left_out and not found.quiet


def test_a_broken_addon_library_affects_only_that_addon(tmp_path, instance):
    """An add-on whose own file does not load is left out with the reason; the
    local library and the other add-ons load."""
    config, addons, store = instance
    other = make_collection(tmp_path, {"tools": RELEASE["release"]}, name="other")
    install(config, addons, other, "tools")
    (store / "library.yaml").chmod(0o644)
    (store / "library.yaml").write_text("tasks: {'x:y': {}}\n")
    found = {p.id: p for p in load.installed(config, addons)}
    assert str(store / "library.yaml") in found["release@acme"].left_out
    assert found["tools@other"].left_out is None
    library = TemplateLibrary.from_yaml_dir(config, addons=tuple(found.values()))
    assert {"tools:ship", "local"} <= set(library.chain_ids)
    assert [issue.chain for issue in library.lint()] == ["local"]


@pytest.mark.parametrize("file", ["addons.yaml", "addons.lock"])
def test_an_unreadable_addons_file_loads_none_and_says_why(instance, file):
    config, addons, _ = instance
    (config / file).write_text("addons: [not, a, mapping]\n")
    assert load.installed(config, addons) == ()
    assert load.config_problem(config).startswith(file)


# ── every reader of the library and the profile table ──


def test_every_construction_site_passes_addons():
    """A site that builds the library or the profile table without the
    installed add-ons silently drops them: a local `extends: release:x` then
    stops resolving there and nowhere else."""
    source = Path(kraft.__file__).parent
    builders = {"from_yaml_dir", "from_mappings", "lint_dir", "from_yaml", "from_mapping"}
    missing = []
    for file in sorted(source.rglob("*.py")):
        for node in ast.walk(ast.parse(file.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in builders
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("TemplateLibrary", "HarnessProfileTable")
                and not any(k.arg == "addons" for k in node.keywords)
            ):
                missing.append(f"{file.relative_to(source)}:{node.lineno}")
    assert missing == []


@pytest.fixture
def process(instance, monkeypatch):
    """`instance` as this process's own: what a launch, doctor and the CLI
    find through the environment, with no server state to ask."""
    config, addons, _ = instance
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(addons.parent))
    return config


def test_a_launch_and_doctor_read_the_installed_addons(tmp_path, process):
    from kraft import doctor, harness
    from kraft.adapters import profiles

    assert doctor._chains_check(process)["ok"]
    spec = {**RELEASE["release"], "profiles": {"deep": {"model": {"codex": "m"}}}}
    install(
        process, load.addons_dir(), make_collection(tmp_path / "v2", {"release": spec}), "release"
    )
    table, _ = profiles.harness_table(harness.load(None))
    assert table.agent_profiles["release:deep"].model == {"codex": "m"}


def test_offline_lint_says_which_chains_it_did_not_check(process, capsys):
    from kraft import cli

    cli.main(["admin", "templates", "lint", "--dir", str(process)])
    out = capsys.readouterr().out
    assert "1 chain(s), no errors" not in out and "0 chain(s), no errors" in out
    assert "not checked (add-on)" in out


SHIPPED_POLICY = Path(kraft.__file__).resolve().parents[2] / "config" / "policy.yaml"


@pytest.fixture
def capped(tmp_path):
    """`release@acme` with a task that sets budget_usd 8, under a maximum of 9."""
    config = home(tmp_path)
    addons = tmp_path / "run" / "addons"
    release = {
        "library": {"tasks": {"base": {**AGENT, "policy": {"budget_usd": 8}}}},
        "chains": {"ship": chain("base")},
    }
    store = install(config, addons, make_collection(tmp_path, {"release": release}), "release")
    _maximum(config, 9)
    return config, addons, store


def _maximum(config, usd):
    maxima = {"tasks": {"budget_usd": usd}}
    write_yaml(config / "policy.yaml", {**read_yaml(SHIPPED_POLICY), "maxima": maxima})


def test_a_limit_above_a_lowered_maximum_leaves_the_addon_out(capped):
    """The operator lowered `maxima:` after the add-on was installed: the
    add-on drops out, named, and is not run past the new ceiling."""
    config, addons, _ = capped
    assert _one(config, addons).left_out is None

    _maximum(config, 5)

    found = _one(config, addons)
    assert "sets budget_usd 8 > the administrator maximum 5" in found.left_out and not found.quiet


# ── the answer is kept until an input changes ──


def _counting(monkeypatch):
    calls = []
    real = load._installed

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(load, "_installed", counted)
    return calls


def _disable(config, addons, store):
    _edit(config, "addons.yaml", lambda d: d["addons"].update({"release@acme": False}))


def _bump_lock(config, addons, store):
    _edit(config, "addons.lock", lambda d: d["addons"]["release@acme"].update(version="9.9.9"))


def _edit_harnesses(config, addons, store):
    _edit(config, "harnesses.yaml", lambda d: d["harnesses"].clear())


def _edit_repos(config, addons, store):
    write_yaml(config / "repos.yaml", {"repos": [{"id": "release", "path": "/work/release"}]})


def _edit_policy(config, addons, store):
    _maximum(config, 5)


def _mark_unrestored(config, addons, store):
    load._unrestored[store.name] = "x"


@pytest.mark.parametrize(
    ("before", "change"),
    [
        (None, _disable),
        (None, _bump_lock),
        (None, _edit_harnesses),
        (None, _edit_repos),
        (None, _edit_policy),
        (None, lambda config, addons, store: drop_store(store)),
        (lambda config, addons, store: drop_store(store), _mark_unrestored),
    ],
    ids=[
        "addons-yaml",
        "addons-lock",
        "harnesses-yaml",
        "repos-yaml",
        "policy-yaml",
        "dropped-store",
        "unrestored-reason",
    ],
)
def test_installed_is_answered_from_a_cache_until_an_input_changes(
    capped, monkeypatch, before, change
):
    config, addons, store = capped
    monkeypatch.setattr(load, "_unrestored", {})
    if before:
        before(config, addons, store)
    calls = _counting(monkeypatch)
    first = load.installed(config, addons)
    n = len(calls)
    assert n >= 1
    assert load.installed(config, addons) == first
    assert len(calls) == n  # nothing recomputed

    change(config, addons, store)

    after = load.installed(config, addons)
    assert len(calls) > n  # recomputed
    assert after != first  # and the answer reflects it


def test_verify_and_an_unsaved_config_never_hit_the_cache(instance, monkeypatch):
    config, addons, _ = instance
    calls = _counting(monkeypatch)
    load.installed(config, addons)
    n = len(calls)
    load.installed(config, addons, verify=True)
    load.installed(config, addons, config=AddonsConfig.load(config / "addons.yaml"))
    assert len(calls) == n + 2  # each of the two calls recomputed


# ── restoring a store that is gone ──


def _locked(tmp_path, *, directory=False):
    """`release@acme` installed by an update, its collection, and the home."""
    from support.addons import instance, publish

    from kraft.addons import update as addon_update

    repo = make_collection(tmp_path, RELEASE)
    source = {"path": str(repo)} if directory else {"git": repo.as_uri()}
    config, addons = instance(tmp_path, {"acme": source})
    addon_update.update(
        ["release@acme"],
        accept=lambda review: True,
        entries={"release@acme": True},
        config_dir=config,
        addons_root=addons,
    )
    (found,) = load.installed(config, addons)
    assert found.left_out is None
    return repo, config, addons, found, publish


@pytest.mark.parametrize("mirror_too", [False, True], ids=["mirror-has-it", "fetched-by-its-id"])
def test_a_missing_store_is_restored_from_the_locked_commit(tmp_path, monkeypatch, mirror_too):
    """The collection has moved on; what comes back is what was locked."""
    repo, config, addons, found, publish = _locked(tmp_path)
    publish(repo, "release", version="2.0.0", skills={"deploy-review": "a newer method"})
    drop_store(found.root)
    if mirror_too:
        shutil.rmtree(addons / "mirrors")
    else:  # the mirror kept the commit: the remote is not asked
        shutil.rmtree(repo)
        monkeypatch.setattr(fetch, "fetch", lambda *a: pytest.fail("fetched from the remote"))
    assert "store is missing" in _one(config, addons).left_out

    assert load.restore_missing(load.installed(config, addons), config, addons) == {}

    restored = _one(config, addons, verify=True)
    assert (restored.left_out, restored.root, restored.version) == (None, found.root, "1.0.0")
    assert (restored.root / "skills/deploy-review/SKILL.md").read_text() == "the method"
    (mirror,) = (addons / "mirrors").iterdir()
    assert git(mirror, "rev-parse", f"refs/kraft/pinned/{found.commit}") == found.commit


def _remote_gone(repo, config, addons):
    shutil.rmtree(repo)
    shutil.rmtree(addons / "mirrors")


def _source_not_recorded(repo, config, addons):
    """An older pin: it names a commit, and not the directory in it."""
    (found,) = load.installed(config, addons)
    return replace(found, source=None)


def _collection_removed(repo, config, addons):
    written = read_yaml(config / "addons.yaml")
    written["collections"]["other"] = written["collections"].pop("acme")
    written["addons"] = {}
    write_yaml(config / "addons.yaml", written)


@pytest.mark.parametrize(
    "happens, why",
    [
        (_remote_gone, "could not be fetched"),
        (_collection_removed, "collection acme is no longer in addons.yaml"),
        (_source_not_recorded, "its pin does not record where its files came from"),
    ],
    ids=["commit-not-on-the-remote", "collection-removed", "source-not-recorded"],
)
def test_a_store_that_cannot_be_restored_says_why(tmp_path, happens, why):
    repo, config, addons, found, _publish = _locked(tmp_path)
    drop_store(found.root)
    pin = happens(repo, config, addons) or found

    failed = load.restore_missing((pin,), config, addons)

    assert why in failed["release@acme"] and "release@acme 1.0.0" in failed["release@acme"]
    assert not found.root.exists()
    # The collection still being listed, a person can put it back: say how. A
    # removed collection is said to be added again, which --re-install cannot do.
    assert ("--re-install" in failed["release@acme"]) is (happens is not _collection_removed)
    if happens is _remote_gone:
        assert "kraft admin addon install release@acme --re-install" in failed["release@acme"]
        assert "could not be restored: release@acme 1.0.0" in _one(config, addons).left_out


def test_restore_missing_stops_between_addons_once_cancelled(tmp_path, monkeypatch):
    """A shutdown during the first restore: the second is not started."""
    _repo, config, addons, found, _publish = _locked(tmp_path)
    second = replace(found, id="tools@acme")
    cancel = threading.Event()
    restored = []

    def restore(addon, *_a):
        restored.append(addon.id)
        cancel.set()

    monkeypatch.setattr(load, "restore", restore)

    failed = load.restore_missing((found, second), config, addons, cancel=cancel)

    assert restored == [found.id]
    assert failed == {second.id: "cancelled"}


@pytest.mark.parametrize("changed", [False, True], ids=["unchanged", "changed"])
def test_a_directory_store_is_restored_only_if_unchanged(tmp_path, changed):
    """A folder edited since it was locked is never loaded unreviewed."""
    repo, config, addons, found, _publish = _locked(tmp_path, directory=True)
    drop_store(found.root)
    if changed:
        (repo / "addons/release/skills/deploy-review/SKILL.md").write_text("edited since")

    failed = load.restore_missing((found,), config, addons)

    assert found.root.is_dir() == (not changed)
    if changed:
        assert "no longer match the locked digest" in failed["release@acme"]
        assert "kraft admin addon update" in failed["release@acme"]
    else:
        assert failed == {} and _one(config, addons, verify=True).left_out is None


def test_a_store_that_cannot_be_written_is_a_failed_restore_not_a_crash(tmp_path, monkeypatch):
    """A full disk or a read-only run directory: the background task goes on
    to the other add-ons and says why this one is still out."""
    repo, config, addons, found, _publish = _locked(tmp_path)
    drop_store(found.root)

    def full(*_a):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fetch, "write_store", full)

    failed = load.restore_missing((found,), config, addons)

    assert "No space left on device" in failed["release@acme"]


@pytest.mark.parametrize(
    "addon_id, repo_id",
    [
        pytest.param("release@acme", None, id="an-add-on"),
        pytest.param("library@repo:app", "app", id="a-repo-library"),
    ],
)
def test_an_installed_addon_knows_whose_repo_library_it_is(tmp_path, addon_id, repo_id):
    addon = load.InstalledAddon(addon_id, addon_id.split("@")[0], "ns", "1.0.0", tmp_path)

    assert addon.repo_id == repo_id
    assert load.repo_library_id("app") == "library@repo:app"


@pytest.fixture
def accepted(tmp_path):
    """A home with `app`'s repo library accepted: `(repo, (config_dir, addons_dir))`."""
    repo = repo_with_library(tmp_path)
    found = make_instance(tmp_path)
    accept_repo_library(*found, repo, "app")
    return repo, found


def test_an_accepted_repo_library_loads_under_the_repo_id(accepted):
    _repo, found = accepted

    (library,) = load.installed(*found, verify=True)

    assert (library.id, library.namespace, library.repo_id, library.left_out) == (
        "library@repo:app",
        "app",
        "app",
        None,
    )
    templates = TemplateLibrary.from_yaml_dir(found[0], addons=[library])
    assert "app:ship" in templates.chain_ids


def _disconnect(found):
    write_yaml(found[0] / "repos.yaml", {"repos": []})


def _take_the_id(found):
    repos = read_yaml(found[0] / "repos.yaml")
    repos["repos"][0]["id"] = "other"
    write_yaml(found[0] / "repos.yaml", repos)


def _drop_the_harness(found):
    write_yaml(found[0] / "harnesses.yaml", {"harnesses": {}})


@pytest.mark.parametrize(
    "change, left_out, quiet",
    [
        pytest.param(_disconnect, "no longer connected", True, id="repository-gone"),
        pytest.param(_take_the_id, "no longer connected", True, id="id-renamed"),
        pytest.param(_drop_the_harness, "requires harness 'codex'", False, id="harness-removed"),
    ],
)
def test_why_a_repo_library_is_left_out(accepted, change, left_out, quiet):
    _repo, found = accepted

    change(found)

    (library,) = load.installed(*found)
    assert left_out in library.left_out
    assert library.quiet is quiet


def test_a_repo_library_is_not_left_out_for_owning_its_repo_id(accepted):
    _repo, found = accepted
    requires = manifest.Requires(kraft="2")

    assert load.instance_problem("app", requires, found[0], repo_library=True) is None
    assert "is now a repository id" in load.instance_problem("app", requires, found[0])


def test_a_missing_repo_library_store_is_restored_from_the_checkout(accepted):
    _repo, found = accepted
    (library,) = load.installed(*found)
    drop_store(library.root)

    load.restore(library, *found)

    assert load.installed(*found, verify=True)[0].left_out is None


def test_installed_notices_a_changed_repo_library_lock(accepted):
    _repo, found = accepted
    assert len(load.installed(*found)) == 1

    write_yaml(found[0] / "repo-libraries.lock", {"lock_version": 1, "repos": {}})

    assert load.installed(*found) == ()


def _pin(addon_id):
    return {"id": addon_id, "digest": "sha256:0", "version": "1"}


@pytest.mark.parametrize(
    ("pins", "repo_id", "refused"),
    [
        pytest.param({"app": _pin("library@repo:app")}, "app", None, id="own-repo"),
        pytest.param({"app": _pin("library@repo:app")}, "web", "app", id="another-repo"),
        pytest.param({"app": _pin("library@repo:app")}, None, "app", id="no-repo-id"),
        pytest.param({"release": _pin("release@acme")}, None, None, id="addon-pin-is-not-scoped"),
        pytest.param(None, "web", None, id="no-pins"),
    ],
)
def test_scope_problem(pins, repo_id, refused):
    why = load.scope_problem(pins, repo_id)
    if refused is None:
        assert why is None
    else:
        assert why == f"it reads {refused}'s repo library, visible only to items in {refused}"


@pytest.mark.parametrize(
    ("addon_id", "keep", "left_out"),
    [
        pytest.param("library@repo:app", (), True, id="another"),
        pytest.param("library@repo:web", (), False, id="own"),
        pytest.param("library@repo:app", ("app",), False, id="kept"),
        pytest.param("release@acme", (), False, id="addon"),
    ],
)
def test_scoped_leaves_another_repos_library_out(tmp_path, addon_id, keep, left_out):
    namespace = addon_id.split("@")[0] if "repo:" not in addon_id else addon_id.rsplit(":", 1)[1]
    addon = load.InstalledAddon(addon_id, "x", namespace, "1", tmp_path)
    (out,) = load.scoped([addon], "web", keep=keep)
    if left_out:
        assert out.left_out == f"{namespace}'s repo library is visible only to items in {namespace}"
        assert out.quiet and out.id == addon_id
    else:
        assert out == addon
