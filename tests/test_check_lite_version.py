"""The guard that stops plugins/kraft-lite shipping without a version bump.

Built after !48 changed kl.py and all four skills while leaving plugin.json at
0.2.0, which nothing caught until `just lite-publish` refused to tag it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "dev" / "check_lite_version.py"
MANIFEST = "plugins/kraft-lite/.claude-plugin/plugin.json"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _write(repo: Path, relative: str, body: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _check(repo: Path, base: str, head: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), base, head],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A repo holding the plugin at 0.2.0, with one commit as the base."""
    _git(tmp_path, "init", "-q", ".")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, MANIFEST, json.dumps({"name": "kraft-lite", "version": "0.2.0"}))
    _write(tmp_path, "plugins/kraft-lite/kl.py", "print('one')\n")
    _write(tmp_path, "plugins/kraft-lite/README.md", "docs\n")
    base = _commit(tmp_path, "base")
    return tmp_path, base


def test_a_surface_change_without_a_bump_fails(repo):
    path, base = repo
    _write(path, "plugins/kraft-lite/kl.py", "print('two')\n")
    head = _commit(path, "change the helper")
    done = _check(path, base, head)
    assert done.returncode != 0
    assert "still 0.2.0" in done.stderr
    assert "plugins/kraft-lite/kl.py" in done.stderr, "the offending files are named"


def test_a_skill_change_without_a_bump_fails(repo):
    """Skills are the executor, so their prose is as published as the code."""
    path, base = repo
    _write(path, "plugins/kraft-lite/skills/next/SKILL.md", "---\nname: next\n---\n")
    head = _commit(path, "change a skill")
    done = _check(path, base, head)
    assert done.returncode != 0
    assert "SKILL.md" in done.stderr


def test_a_surface_change_with_a_bump_passes(repo):
    path, base = repo
    _write(path, "plugins/kraft-lite/kl.py", "print('two')\n")
    _write(path, MANIFEST, json.dumps({"name": "kraft-lite", "version": "0.3.0"}))
    head = _commit(path, "change the helper and bump")
    done = _check(path, base, head)
    assert done.returncode == 0, done.stderr
    assert "0.2.0 -> 0.3.0" in done.stdout


def test_a_docs_only_change_needs_no_bump(repo):
    """Demanding a release for a typo fix trains people to bump without meaning it."""
    path, base = repo
    _write(path, "plugins/kraft-lite/README.md", "docs, but better\n")
    head = _commit(path, "fix a typo")
    done = _check(path, base, head)
    assert done.returncode == 0, done.stderr
    assert "published surface" in done.stdout


def test_a_change_outside_the_plugin_needs_no_bump(repo):
    path, base = repo
    _write(path, "src/kraft/api.py", "# unrelated\n")
    head = _commit(path, "touch the backend")
    done = _check(path, base, head)
    assert done.returncode == 0, done.stderr


def test_a_first_release_has_nothing_to_bump_against(tmp_path):
    """The commit that introduces the manifest cannot have bumped it."""
    _git(tmp_path, "init", "-q", ".")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, "README.md", "repo\n")
    base = _commit(tmp_path, "before the plugin existed")
    _write(tmp_path, MANIFEST, json.dumps({"name": "kraft-lite", "version": "0.1.0"}))
    _write(tmp_path, "plugins/kraft-lite/kl.py", "print('one')\n")
    head = _commit(tmp_path, "add the plugin")
    done = _check(tmp_path, base, head)
    assert done.returncode == 0, done.stderr
    assert "nothing to bump against" in done.stdout
