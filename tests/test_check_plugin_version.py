"""The lint-job gate: a release-labelled pull request must already carry
the version its own label would produce.

Runs the script as a subprocess, not by importing its functions -- what
matters here is the exit code a CI step actually reacts to.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_plugin_version.py"


def _manifest(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "p", "version": version}, indent=2) + "\n")
    return path


def _run(previous: str, labels: str, manifests: list[Path]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), previous, labels, *[str(m) for m in manifests]],
        capture_output=True,
        text=True,
    )


def test_matching_manifests_pass(tmp_path):
    a = _manifest(tmp_path / "a" / "plugin.json", "0.4.0")
    b = _manifest(tmp_path / "b" / "plugin.json", "0.4.0")
    result = _run("v0.3.2", "release::minor", [a, b])
    assert result.returncode == 0, result.stderr


def test_stale_manifest_fails(tmp_path):
    a = _manifest(tmp_path / "a" / "plugin.json", "0.3.2")
    b = _manifest(tmp_path / "b" / "plugin.json", "0.4.0")
    result = _run("v0.3.2", "release::minor", [a, b])
    assert result.returncode == 1
    assert "0.4.0" in result.stderr
    assert "stamp_plugin_versions.py 0.4.0" in result.stderr


def test_no_release_label_is_not_checked(tmp_path):
    a = _manifest(tmp_path / "a" / "plugin.json", "0.3.2")
    b = _manifest(tmp_path / "b" / "plugin.json", "0.3.2")
    result = _run("v0.3.2", "bug,needs-review", [a, b])
    assert result.returncode == 0, result.stderr


def test_release_none_is_not_checked(tmp_path):
    a = _manifest(tmp_path / "a" / "plugin.json", "0.3.2")
    result = _run("v0.3.2", "release::none", [a])
    assert result.returncode == 0, result.stderr


def test_two_release_labels_is_an_error(tmp_path):
    a = _manifest(tmp_path / "a" / "plugin.json", "0.4.0")
    result = _run("v0.3.2", "release::minor,release::patch", [a])
    assert result.returncode != 0
