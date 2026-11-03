"""The built-in kraft-default add-on's source and its reading."""

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import kraft
from kraft import harness, paths, update
from kraft.addons import builtin, fetch, load
from kraft.templates.environment import HarnessProfileTable

CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.mark.parametrize(
    ("version", "bundled", "expect_bundle", "checkout"),
    [
        pytest.param("2.6.0", True, True, True, id="installed"),
        pytest.param("0.0.0+source", False, False, True, id="source-checkout"),
        pytest.param("0.0.0+source", True, False, True, id="source-build-ignores-a-stale-bundle"),
        pytest.param("2.6.0", False, False, True, id="release-version-without-a-bundle"),
        # `just install` off a tag: a dev version, the bundle, and no checkout beside it.
        pytest.param("2.5.1.dev10+gabc", True, True, False, id="dev-tool-install"),
    ],
)
def test_the_source(monkeypatch, tmp_path, version, bundled, expect_bundle, checkout):
    if bundled:
        (tmp_path / "kraft-default").mkdir()
    if not checkout:
        venv = tmp_path / "venv" / "lib" / "site-packages" / "kraft" / "__init__.py"
        monkeypatch.setattr(kraft, "__file__", str(venv))
    monkeypatch.setattr(paths, "BUNDLED", tmp_path)
    monkeypatch.setattr(update, "installed", lambda: version)
    assert builtin.source_dir() == (tmp_path / "kraft-default" if expect_bundle else CONFIG)


def test_the_extracted_files_are_the_addon_layout():
    files = builtin.extracted().files
    assert ".kraft/addon.json" in files and "chains/default.yaml" in files
    assert "policy.yaml" not in files


@pytest.fixture
def checkout_copy(monkeypatch, tmp_path):
    copy = tmp_path / "config"
    shutil.copytree(CONFIG, copy)
    monkeypatch.setattr(builtin, "source_dir", lambda: copy)
    return copy


@pytest.mark.parametrize("edited", ["library.yaml", "chains/default.yaml"])
def test_an_edited_checkout_is_read_again(checkout_copy, monkeypatch, edited):
    monkeypatch.setattr(update, "installed", lambda: "0.0.0+source")
    before = fetch.digest(builtin.extracted().files)
    file = checkout_copy / edited
    file.write_text(file.read_text() + "\n# edited\n")
    assert fetch.digest(builtin.extracted().files) != before
    assert load._builtin_stamps() != ()


def test_an_installed_release_keeps_the_bundle_it_started_with(checkout_copy, monkeypatch):
    """`uv tool install` replaces the bundle under a running server; the old code
    must not read the new release's chains."""
    monkeypatch.setattr(update, "installed", lambda: "2.6.0")
    first = builtin.extracted()
    file = checkout_copy / "chains" / "default.yaml"
    file.write_text(file.read_text() + "\n# the next release\n")
    monkeypatch.setattr(update, "installed", lambda: "2.7.0")  # the metadata moved too
    assert builtin.extracted() is first
    assert load._builtin_stamps() == ()


def test_an_installed_release_keeps_the_harness_profiles_it_started_with(
    checkout_copy, monkeypatch
):
    """The packaged `harnesses.yaml` the fallback fills from is frozen with the bundle."""
    monkeypatch.setattr(update, "installed", lambda: "2.6.0")
    before, _ = builtin.harness_fallback({})
    file = checkout_copy / "harnesses.yaml"
    file.write_text(file.read_text().replace("model: sonnet", "model: the-next-release"))
    monkeypatch.setattr(update, "installed", lambda: "2.7.0")
    after, _ = builtin.harness_fallback({})
    assert after == before
    assert "the-next-release" not in str(after)


@pytest.mark.parametrize(
    "who",
    [
        pytest.param("builtin", id="builtin-accepts"),
        pytest.param("operator", id="operator-dir-still-refuses"),
    ],
)
def test_hard_linked_package_files_load(checkout_copy, tmp_path, who):
    os.link(checkout_copy / "library.yaml", tmp_path / "second-name")
    if who == "builtin":
        assert ".kraft/addon.json" in builtin.extracted().files
    else:
        with pytest.raises(fetch.AddonRefused, match="second hard link"):
            fetch.extract_dir(checkout_copy)


def _packaged():
    return yaml.safe_load((CONFIG / "harnesses.yaml").read_text())


def _without(*, harnesses=(), profiles=()):
    data = _packaged()
    for name in harnesses:
        del data["harnesses"][name]
    for name in profiles:
        del data["profiles"][name]
    return data


@pytest.mark.parametrize(
    ("data", "filled"),
    [
        pytest.param(
            {k: v for k, v in _without().items() if k != "profiles"},
            {"strong", "fast"},
            id="no-profiles-section",
        ),
        pytest.param(_without(profiles=["fast"]), {"fast"}, id="fast-missing"),
        pytest.param(_without(harnesses=["claude"]), set(), id="claude-harness-missing"),
        pytest.param(_packaged(), set(), id="operator-defined-wins"),
    ],
)
def test_the_builtins_profiles_fall_back_to_the_package(data, filled):
    table = HarnessProfileTable.from_mapping(
        data, "harnesses.yaml", harnesses=harness.load(None).valid
    )
    assert {"strong", "fast"} <= set(table.agent_profiles)
    assert "claude" in table.profiles  # the harness half of the fill
    assert table.filled == filled
    assert table.agent_profiles["strong"].providers or table.agent_profiles["strong"].model


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("harnesses.yaml", ": : ["),
        ("harnesses.yaml", "- a list"),
        (".kraft/addon.json", "{not json"),
        (".kraft/addon.json", "[]"),
    ],
    ids=["yaml-syntax", "yaml-list", "json-syntax", "json-list"],
)
def test_a_malformed_packaged_file_fills_nothing(tmp_path, monkeypatch, name, text):
    shipped = tmp_path / "shipped"
    shutil.copytree(builtin.source_dir(), shipped)
    (shipped / name).write_text(text)
    monkeypatch.setattr(builtin, "source_dir", lambda: shipped)
    data = _without(profiles=["fast"])  # a healthy package would fill `fast`
    assert builtin.harness_fallback(data) == (_without(profiles=["fast"]), ())


def test_an_operator_profile_is_never_replaced():
    data = _packaged()
    data["profiles"]["strong"] = {"providers": {"claude": {"model": "mine"}}}
    out, filled = builtin.harness_fallback(data)
    assert filled == () and out["profiles"]["strong"] == data["profiles"]["strong"]


def test_a_non_mapping_comes_back_unchanged():
    assert builtin.harness_fallback(None) == (None, ())


@pytest.mark.parametrize(
    ("builtin_tree", "why"),
    [
        pytest.param(True, None, id="builtin"),
        pytest.param(False, "requires agent profile 'strong'", id="other"),
    ],
)
def test_only_the_builtin_tree_gets_the_fallback_at_load(tmp_path, builtin_tree, why):
    requires = SimpleNamespace(harnesses=(), profiles=("strong",))
    got = load.instance_problem(
        "x", requires, tmp_path, harnesses={}, repos={}, builtin_tree=builtin_tree
    )
    assert (got is None) if why is None else got.startswith(f"{why}")


# ── loading it on every home ──

from support import addons as fx  # noqa: E402

from kraft.addons import flow  # noqa: E402
from kraft.addons import update as addon_update  # noqa: E402
from kraft.config import write_yaml  # noqa: E402
from kraft.templates.library import TemplateLibrary  # noqa: E402


def _home(tmp_path, collections=None):
    config, addons = fx.instance(tmp_path, collections)
    return config, addons


def _builtin_in(config, addons):
    found = load.installed(config, addons)
    (mine,) = [a for a in found if builtin.is_builtin(a)]
    return mine, found


def _resolves(config, found):
    library = TemplateLibrary.from_mappings(
        {}, (), library_path=config / "library.yaml", addons=list(found)
    )
    return "kraft-default:default" in library.chain_ids


def _with_addons(tmp_path, config, addons):
    repo = fx.make_collection(tmp_path, {"other": {"library": {"tasks": {"base": fx.AGENT}}}})
    fx.install(config, addons, repo, "other")


def _bad_yaml(tmp_path, config, addons):
    (config / "addons.yaml").write_text(": :")


def _repo_id(tmp_path, config, addons):
    write_yaml(config / "repos.yaml", {"repos": [{"id": "kraft-default", "path": "/x"}]})


def _maxima(tmp_path, config, addons):
    policy = yaml.safe_load((CONFIG / "policy.yaml").read_text())
    write_yaml(config / "policy.yaml", {**policy, "maxima": {"tasks": {"time_cap_minutes": 5}}})


def _unmet(tmp_path, config, addons):
    write_yaml(config / "harnesses.yaml", {})


def _newer_requires(tmp_path, config, addons, monkeypatch):
    copy = tmp_path / "newer"
    shutil.copytree(CONFIG, copy)
    manifest_path = copy / ".kraft" / "addon.json"
    manifest_path.write_text(manifest_path.read_text().replace('"kraft": "2.5"', '"kraft": "9.0"'))
    monkeypatch.setattr(builtin, "source_dir", lambda: copy)
    monkeypatch.setattr(update, "installed", lambda: "2.6.0")  # a release, not a source build


def _corrupt(tmp_path, config, addons):
    victim = next(p for p in builtin.ensure_store(addons).rglob("*.yaml"))
    victim.chmod(0o644)
    victim.write_text("")


def _worker(tmp_path, config, addons, monkeypatch):
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "w1")
    return "store"


def _locked(tmp_path, config, addons, monkeypatch):
    addons.mkdir(parents=True)
    addons.chmod(0o500)
    return "its store could not be written"


def _no_source(tmp_path, config, addons, monkeypatch):
    def gone():
        raise FileNotFoundError("no kraft-default here")

    monkeypatch.setattr(builtin, "source_dir", gone)
    return "no kraft-default here"


def _no_source_with_addon(tmp_path, config, addons, monkeypatch):
    _with_addons(tmp_path, config, addons)
    return _no_source(tmp_path, config, addons, monkeypatch)


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(None, id="fresh-home"),
        pytest.param(_with_addons, id="with-addons"),
        pytest.param(_corrupt, id="corrupt-store-is-rebuilt"),
        pytest.param(_bad_yaml, id="unreadable-addons-yaml"),
        pytest.param(_repo_id, id="repo-id-taken"),
        pytest.param(_maxima, id="maxima-below-its-caps"),
        pytest.param(_unmet, id="requires-unmet"),
        pytest.param(_newer_requires, id="requires-kraft-above-the-running-one"),
        pytest.param(_worker, id="worker-with-store-missing"),
        pytest.param(
            _locked,
            id="store-unwritable",
            marks=pytest.mark.skipif(os.geteuid() == 0, reason="root writes a mode-0500 directory"),
        ),
        pytest.param(_no_source, id="no-source"),
        pytest.param(_no_source_with_addon, id="no-source-with-another-addon"),
    ],
)
def test_the_builtin_always_loads(tmp_path, monkeypatch, setup):
    config, addons = _home(tmp_path)
    expect = None
    if setup in (_worker, _locked, _no_source, _no_source_with_addon, _newer_requires):
        expect = setup(tmp_path, config, addons, monkeypatch)
    elif setup:
        setup(tmp_path, config, addons)
    mine, found = _builtin_in(config, addons)
    if addons.exists():
        addons.chmod(0o700)
    if expect is None:
        assert mine.left_out is None
        assert mine.root.is_dir() and _resolves(config, found)
        fetch.verify_store(mine.root)
        return
    assert expect in mine.left_out and not mine.quiet
    if setup is _worker:
        assert not (addons / "store").exists()
    if setup is _no_source_with_addon:
        assert [a.left_out for a in fx.others(found)] == [None]


def _copy_home(tmp_path, with_child):
    specs = {"kraft-default@kraft-sdlc": {"library": {"tasks": {"base": fx.AGENT}}}}
    if with_child:
        specs["kraft-superpowers@kraft-sdlc"] = {
            "library": {"tasks": {"b": fx.AGENT}},
            "parent": ("kraft-default@kraft-sdlc", "1"),
        }
    return fx.make_tree(tmp_path, specs)[1]


@pytest.mark.parametrize("with_child", [False, True], ids=["copy-alone", "child-of-copy"])
def test_a_github_kraft_default_is_left_out_by_the_builtin(tmp_path, with_child):
    config, addons = _copy_home(tmp_path, with_child)
    mine, found = _builtin_in(config, addons)
    by_id = {a.id: a for a in found}
    copy = by_id["kraft-default@kraft-sdlc"]
    assert copy.left_out == builtin.SHIPS_IT.format(id=copy.id) and copy.quiet
    if with_child:
        assert "kraft-default@kraft-sdlc" in by_id["kraft-superpowers@kraft-sdlc"].left_out
    assert mine.left_out is None and _resolves(config, found)


def test_people_never_see_the_builtin_as_an_addon(tmp_path, monkeypatch):
    from kraft import doctor

    config, addons = _home(tmp_path)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(addons.parent))
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config))
    assert flow.addon_rows() == []
    row = next(c for c in doctor._addon_checks(config) if c["name"] == "add-ons")
    assert row["detail"] == "0 add-on(s) load"


def test_installing_a_github_copy_is_refused(tmp_path):
    repo = fx.make_collection(tmp_path, {"kraft-default": {"library": {"tasks": {"b": fx.AGENT}}}})
    config, addons = _home(tmp_path, {"acme": {"git": repo.as_uri()}})
    [result] = addon_update.update(
        ["kraft-default@acme"],
        accept=lambda review: True,
        entries={"kraft-default@acme": True},
        config_dir=config,
        addons_root=addons,
    )
    assert result.outcome == "refused" and builtin.REFUSED in result.problems


@pytest.mark.parametrize(
    ("version", "loads"),
    [
        pytest.param("2.6.0", True, id="in-major"),
        pytest.param("3.0.0", False, id="other-major"),
        pytest.param("2.6.0rc1", True, id="rc"),
        pytest.param("0.0.0+source", True, id="source-checkout"),
        pytest.param("2.4.1.dev70+gabc", True, id="dev-checkout"),
    ],
)
def test_a_builtin_parent_range(tmp_path, monkeypatch, version, loads):
    monkeypatch.setattr(update, "installed", lambda: version)
    config, addons = _home(tmp_path)
    spec = {
        "library": {"tasks": {"b": fx.AGENT}},
        "manifest": fx.child_manifest("kraft-default@kraft", "2"),
    }
    fx.install(config, addons, fx.make_collection(tmp_path, {"child": spec}), "child")
    found = {a.id: a for a in load.installed(config, addons)}
    assert (found["child@acme"].left_out is None) is loads


def test_the_builtins_store_survives_a_collection_and_no_release_drops_it(tmp_path):
    config, addons = _home(tmp_path)
    mine, _ = _builtin_in(config, addons)
    assert load.gc(set, addons) == [] and mine.root.is_dir()
    assert load.left_out_by("3.0.0", config) == []


@pytest.mark.parametrize("asked", ["kraft-default@acme", "kid@acme"], ids=["judge", "pull-in"])
def test_the_builtin_wins_the_namespace_over_a_lock_that_still_holds_a_copy(tmp_path, asked):
    """A 2.5 home's lock holds `kraft-default@sdlc`: a copy from another
    collection, asked for or pulled in as a parent, is refused with `REFUSED`,
    not sent to update the held one (which is refused too)."""
    kid = {
        "library": {"tasks": {"k": fx.AGENT}},
        "manifest": fx.child_manifest("kraft-default@acme", "1"),
    }
    acme = fx.make_collection(
        tmp_path, {"kraft-default": {"library": {"tasks": {"b": fx.AGENT}}}, "kid": kid}
    )
    held = fx.make_collection(
        tmp_path, {"kraft-default": {"library": {"tasks": {"b": fx.AGENT}}}}, name="sdlc"
    )
    config, addons = _home(tmp_path, {"acme": {"git": acme.as_uri()}})
    fx.install(config, addons, held, "kraft-default")
    listed = yaml.safe_load((config / "addons.yaml").read_text())
    del listed["addons"]["kraft-default@sdlc"]  # only the lock still holds it
    write_yaml(config / "addons.yaml", listed)
    results = addon_update.update(
        [asked],
        accept=lambda review: True,
        entries={asked: True},
        config_dir=config,
        addons_root=addons,
    )
    problems = [p for r in results if r.outcome == "refused" for p in r.problems]
    assert problems and all(builtin.REFUSED in p for p in problems)


@pytest.mark.parametrize(
    ("version", "digest", "expect"),
    [
        pytest.param("2.6.0", None, None, id="current-is-rebuilt"),
        pytest.param("2.6.0", None, ("worker",), id="a-worker-fails-cleanly"),
        pytest.param("2.6.0", None, ("no source",), id="no-source"),
        pytest.param(
            "2.5.0", "f" * 64, ("no longer installed", "retry"), id="old-release-says-retry"
        ),
    ],
)
def test_restoring_the_builtin(tmp_path, monkeypatch, version, digest, expect):
    config, addons = _home(tmp_path)
    mine = builtin.entry(addons / "store")
    shutil.rmtree(mine.root, ignore_errors=True)
    if expect == ("worker",):
        monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "x")
        expect = ("does not write it",)
    if expect == ("no source",):
        monkeypatch.setattr(
            builtin, "source_dir", lambda: (_ for _ in ()).throw(FileNotFoundError("no source"))
        )
    if digest:
        mine = load.InstalledAddon(
            mine.id,
            mine.name,
            mine.namespace,
            version,
            addons / "store" / digest,
            source=mine.source,
        )
    if expect is None:
        assert not mine.root.exists()
        assert load.restore(mine, config, addons) == mine.root
        assert mine.root.is_dir()
        return
    with pytest.raises(load.RestoreError) as err:
        load.restore(mine, config, addons)
    assert all(part in str(err.value) for part in expect)


def test_an_installed_github_copy_is_told_how_to_move_off_it(tmp_path):
    repo = fx.make_collection(
        tmp_path, {"kraft-default": {"library": {"tasks": {"b": fx.AGENT}}}}, name="sdlc"
    )
    config, addons = _home(tmp_path, {"sdlc": {"git": repo.as_uri()}})
    fx.install(config, addons, repo, "kraft-default")
    fx.publish(repo, "kraft-default", version="1.1.0")
    asked = "kraft-default@sdlc"
    [result] = addon_update.update(
        [asked],
        accept=lambda review: True,
        entries={asked: True},
        config_dir=config,
        addons_root=addons,
    )
    assert result.outcome == "refused" and builtin.SHIPS_IT.format(id=asked) in result.problems
