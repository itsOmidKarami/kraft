"""The built-in kraft-default add-on's source and its reading."""

import os
import shutil
from pathlib import Path

import pytest

from kraft import paths, update
from kraft.addons import builtin, fetch

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
