"""The built-in kraft-default add-on's source and its reading."""

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kraft import harness, paths, update
from kraft.addons import builtin, fetch, load
from kraft.templates.environment import HarnessProfileTable

CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.mark.parametrize(
    ("version", "bundled", "expect_bundle"),
    [
        pytest.param("2.6.0", True, True, id="installed"),
        pytest.param("0.0.0+source", False, False, id="source-checkout"),
        pytest.param("0.0.0+source", True, False, id="source-build-ignores-a-stale-bundle"),
        pytest.param("2.6.0", False, False, id="release-version-without-a-bundle"),
    ],
)
def test_the_source(monkeypatch, tmp_path, version, bundled, expect_bundle):
    if bundled:
        (tmp_path / "kraft-default").mkdir()
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


def test_an_edited_checkout_is_read_again(checkout_copy):
    before = fetch.digest(builtin.extracted().files)
    lib = checkout_copy / "library.yaml"
    lib.write_text(lib.read_text() + "\n# edited\n")
    assert fetch.digest(builtin.extracted().files) != before


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
        pytest.param(_without(harnesses=["claude"]), {"claude"}, id="claude-harness-missing"),
        pytest.param(_packaged(), set(), id="operator-defined-wins"),
    ],
)
def test_the_builtins_profiles_fall_back_to_the_package(data, filled):
    table = HarnessProfileTable.from_mapping(
        data, "harnesses.yaml", harnesses=harness.load(None).valid
    )
    assert {"strong", "fast"} <= set(table.agent_profiles)
    assert table.filled == filled
    assert table.agent_profiles["strong"].providers or table.agent_profiles["strong"].model


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
