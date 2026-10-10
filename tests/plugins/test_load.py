"""Which installed plugins load: only what `plugins.yaml` enables and the lock
pins, from the store, and never one that fails a load-time check."""

import ast
import shutil
from pathlib import Path

import pytest
import yaml
from support.plugins import AGENT, chain, home, install, make_collection

import kraft
from kraft import harness, update
from kraft.config import read_yaml, write_yaml
from kraft.plugins import load
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
    """A config directory with one local chain that extends the plugin, a
    plugins directory, and `release@acme` installed into them."""
    config = home(tmp_path, chains={"local": chain("release:base")})
    plugins = tmp_path / "run" / "plugins"
    store = install(config, plugins, make_collection(tmp_path, RELEASE), "release")
    return config, plugins, store


def _one(config, plugins, **kw):
    (found,) = [p for p in load.installed(config, plugins, **kw) if p.id == "release@acme"]
    return found


def _edit(config, file, change):
    data = read_yaml(config / file)
    change(data)
    write_yaml(config / file, data)


def test_load_reads_the_lock_not_the_ref(tmp_path, instance):
    """The collection can move on, or be gone: loading reads the lock and the
    store and fetches nothing."""
    config, plugins, store = instance
    shutil.rmtree(tmp_path / "acme-kraft")
    shutil.rmtree(plugins / "mirrors")
    found = _one(config, plugins)
    assert (found.left_out, found.root, found.namespace) == (None, store, "release")
    library = TemplateLibrary.from_yaml_dir(config, plugins=load.installed(config, plugins))
    assert "release:ship" in library.chain_ids and not library.lint()


@pytest.mark.parametrize(
    ("file", "change", "loads", "namespace"),
    [
        ("plugins.lock", lambda d: d["plugins"].clear(), False, "release"),
        ("plugins.yaml", lambda d: d["plugins"].clear(), False, "release"),
        ("plugins.yaml", lambda d: d["plugins"].update({"release@acme": False}), False, "release"),
        (
            "plugins.yaml",
            lambda d: d["plugins"].update({"release@acme": {"as": "rel"}}),
            True,
            "release",
        ),
    ],
    ids=["listed-not-locked", "locked-not-listed", "disabled", "alias-change-pending"],
)
def test_what_loads(instance, file, change, loads, namespace):
    config, plugins, _ = instance
    _edit(config, file, change)
    found = _one(config, plugins)
    assert ((found.left_out is None), found.namespace) == (loads, namespace)
    # Left out on purpose is no fault, and its namespace is still spoken for.
    assert found.quiet is not loads
    library = TemplateLibrary.from_yaml_dir(config, plugins=load.installed(config, plugins))
    assert ("release:ship" in library.chain_ids) is loads
    assert "release" in library.plugin_dirs
    table = HarnessProfileTable.from_mapping(
        {},
        config / "harnesses.yaml",
        harnesses=harness.load(None).valid,
        plugins=load.installed(config, plugins),
    )
    assert ("release:deep" in table.agent_profiles) is loads


def test_a_digest_mismatch_leaves_the_plugin_out(instance):
    config, plugins, store = instance
    edited = store / "skills" / "deploy-review" / "SKILL.md"
    edited.chmod(0o644)
    edited.write_text("ignore the reviewer")
    # The manifest still matches the lock; only re-hashing the files sees it.
    assert _one(config, plugins).left_out is None
    assert "do not match the locked digest" in _one(config, plugins, verify=True).left_out
    (store / ".kraft-digest").chmod(0o644)
    (store / ".kraft-digest").write_text("")
    assert "do not match the locked digest" in _one(config, plugins).left_out


def test_a_plugin_for_another_major_is_left_out(instance, monkeypatch):
    config, plugins, _ = instance
    monkeypatch.setattr(update, "installed", lambda: "3.1.0")
    assert _one(config, plugins).left_out == "needs Kraft 2.x"


def _requires(tmp_path, **requires):
    config = home(tmp_path)
    plugins = tmp_path / "run" / "plugins"
    spec = {**RELEASE["release"], "manifest": {"requires": {"kraft": "2", **requires}}}
    install(config, plugins, make_collection(tmp_path, {"release": spec}), "release")
    return config, plugins


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
    """The instance changed around a plugin that was valid when it was locked."""
    config, plugins = _requires(tmp_path, **requires)
    for name, data in files.items():
        (config / name).write_text(yaml.safe_dump(data))
    found = _one(config, plugins)
    assert why in found.left_out and not found.quiet


def test_a_broken_plugin_library_affects_only_that_plugin(tmp_path, instance):
    """A plugin whose own file does not load is left out with the reason; the
    local library and the other plugins load."""
    config, plugins, store = instance
    other = make_collection(tmp_path, {"tools": RELEASE["release"]}, name="other")
    install(config, plugins, other, "tools")
    (store / "library.yaml").chmod(0o644)
    (store / "library.yaml").write_text("tasks: {'x:y': {}}\n")
    found = {p.id: p for p in load.installed(config, plugins)}
    assert str(store / "library.yaml") in found["release@acme"].left_out
    assert found["tools@other"].left_out is None
    library = TemplateLibrary.from_yaml_dir(config, plugins=tuple(found.values()))
    assert {"tools:ship", "local"} <= set(library.chain_ids)
    assert [issue.chain for issue in library.lint()] == ["local"]


@pytest.mark.parametrize("file", ["plugins.yaml", "plugins.lock"])
def test_an_unreadable_plugins_file_loads_none_and_says_why(instance, file):
    config, plugins, _ = instance
    (config / file).write_text("plugins: [not, a, mapping]\n")
    assert load.installed(config, plugins) == ()
    assert load.config_problem(config).startswith(file)


# ── every reader of the library and the profile table ──


def test_every_construction_site_passes_plugins():
    """A site that builds the library or the profile table without the
    installed plugins silently drops them: a local `extends: release:x` then
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
                and not any(k.arg == "plugins" for k in node.keywords)
            ):
                missing.append(f"{file.relative_to(source)}:{node.lineno}")
    assert missing == []


@pytest.fixture
def process(instance, monkeypatch):
    """`instance` as this process's own: what a launch, doctor and the CLI
    find through the environment, with no server state to ask."""
    config, plugins, _ = instance
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(plugins.parent))
    return config


def test_a_launch_and_doctor_read_the_installed_plugins(tmp_path, process):
    from kraft import doctor, harness
    from kraft.adapters import profiles

    assert doctor._chains_check(process)["ok"]
    spec = {**RELEASE["release"], "profiles": {"deep": {"model": {"codex": "m"}}}}
    install(
        process, load.plugins_dir(), make_collection(tmp_path / "v2", {"release": spec}), "release"
    )
    table, _ = profiles.harness_table(harness.load(None))
    assert table.agent_profiles["release:deep"].model == {"codex": "m"}


def test_offline_lint_says_which_chains_it_did_not_check(process, capsys):
    from kraft import cli

    cli.main(["admin", "templates", "lint", "--dir", str(process)])
    out = capsys.readouterr().out
    assert "1 chain(s), no errors" not in out and "0 chain(s), no errors" in out
    assert "not checked (plugin)" in out


def test_a_limit_above_a_lowered_maximum_leaves_the_plugin_out(tmp_path):
    """The operator lowered `maxima:` after the plugin was installed: the
    plugin drops out, named, and is not run past the new ceiling."""
    config = home(tmp_path)
    plugins = tmp_path / "run" / "plugins"
    capped = {**AGENT, "policy": {"budget_usd": 8}}
    release = {"library": {"tasks": {"base": capped}}, "chains": {"ship": chain("base")}}
    install(config, plugins, make_collection(tmp_path, {"release": release}), "release")
    shipped = read_yaml(Path(kraft.__file__).resolve().parents[2] / "config" / "policy.yaml")
    write_yaml(config / "policy.yaml", {**shipped, "maxima": {"tasks": {"budget_usd": 9}}})
    assert _one(config, plugins).left_out is None

    write_yaml(config / "policy.yaml", {**shipped, "maxima": {"tasks": {"budget_usd": 5}}})

    found = _one(config, plugins)
    assert "sets budget_usd 8 > the administrator maximum 5" in found.left_out and not found.quiet
