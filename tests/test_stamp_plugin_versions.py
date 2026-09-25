"""Writing the release version into both plugin manifests.

Both plugins are meant to move together: one release, one version, in both
files. Getting it wrong publishes a marketplace whose versions describe
nothing.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "stamp_plugin_versions",
    Path(__file__).resolve().parents[1] / "dev" / "stamp_plugin_versions.py",
)
stamp_mod = importlib.util.module_from_spec(_SPEC)
sys.modules["stamp_plugin_versions"] = stamp_mod
_SPEC.loader.exec_module(stamp_mod)

stamp = stamp_mod.stamp


def _manifest(path: Path, version: str, **extra) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "p", "version": version, **extra}, indent=2) + "\n")
    return path


def test_stamp_writes_the_version(tmp_path):
    m = _manifest(tmp_path / "plugin.json", "0.0.0")
    stamp("0.6.0", [m])
    assert json.loads(m.read_text())["version"] == "0.6.0"


def test_stamp_keeps_every_other_field(tmp_path):
    """A manifest is not ours to rewrite - only its version line is."""
    m = _manifest(tmp_path / "plugin.json", "0.0.0", description="d", skills=["./skills"])
    stamp("0.6.0", [m])
    blob = json.loads(m.read_text())
    assert blob["description"] == "d"
    assert blob["skills"] == ["./skills"]


def test_stamp_does_every_manifest_it_is_given(tmp_path):
    a = _manifest(tmp_path / "a" / "plugin.json", "0.1.0")
    b = _manifest(tmp_path / "b" / "plugin.json", "0.5.2")
    stamp("0.6.0", [a, b])
    assert json.loads(a.read_text())["version"] == "0.6.0"
    assert json.loads(b.read_text())["version"] == "0.6.0"


def test_a_version_that_is_not_a_version_is_refused(tmp_path):
    """Better to fail the check than to publish a manifest saying 'v0.6.0'."""
    m = _manifest(tmp_path / "plugin.json", "0.0.0")
    with pytest.raises(ValueError):
        stamp("v0.6.0", [m])
    assert json.loads(m.read_text())["version"] == "0.0.0", "nothing written on a bad input"


def test_the_real_manifests_are_the_default_targets():
    """The caller passes only a version; the paths are this script's own knowledge."""
    names = [str(p) for p in stamp_mod.MANIFESTS]
    assert any("plugins/kraft/" in n for n in names)
    assert any("plugins/kraft-lite/" in n for n in names)
    for path in stamp_mod.MANIFESTS:
        assert path.is_file(), f"{path} does not exist"


def test_the_vscode_extension_is_stamped_and_keeps_its_own_shape(tmp_path):
    """package.json is the extension's manifest: only `version` may change, and
    the key order (which `vsce` and reviewers read top-down) must survive."""
    assert stamp_mod._ROOT / "vscode" / "package.json" in stamp_mod.MANIFESTS
    src = stamp_mod._ROOT / "vscode" / "package.json"
    copy = tmp_path / "package.json"
    copy.write_text(src.read_text())
    stamp("1.2.3", [copy])
    before, after = json.loads(src.read_text()), json.loads(copy.read_text())
    assert after["version"] == "1.2.3"
    assert list(after) == list(before)
    assert {k: v for k, v in after.items() if k != "version"} == {
        k: v for k, v in before.items() if k != "version"
    }
