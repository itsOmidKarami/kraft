"""Host git never runs what a sandboxed worker planted (Kraft-nx4id, Kraft-ju36l).

A sandboxed worker writes its worktree, and nothing stops it building a git
repository of its own in there -- a `filter.<x>.clean`, a `core.fsmonitor`, a
textconv in that repository's config -- and committing a gitlink to it with
`git update-index --cacheinfo 160000,...`. No `.gitmodules`, no declared
submodule. Host git that recurses into that gitlink loads the nested config
and runs its programs as the operator (review-g1 finding 1).

Every test here plants from the worker's side only (inside the worktree, with
an environment carrying none of Kraft's pins) and drives Kraft's own host-side
calls under the hardened environment the daemon runs with. The marker file is
the payload; nothing destructive is ever planted.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from support.harness import make_repo

from kraft.adapters import forge
from kraft.worker import sandbox


def _worker_env() -> dict[str, str]:
    """The worker's side: none of Kraft's `GIT_CONFIG_*` pins."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}


def _w(cwd: Path, *args: str) -> str:
    """git as the sandboxed worker runs it, inside its own worktree."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=_worker_env()
    ).stdout.strip()


@pytest.fixture
def hardened():
    """The daemon's environment: `api.startup` pins it once on the process,
    and every git Kraft spawns inherits it. `tests/conftest.py` restores
    `GIT_CONFIG_*` afterwards."""
    sandbox.harden_host_git_env()


def _worktree(tmp_path: Path) -> Path:
    """A Kraft-shaped linked worktree: `.git` a file, its gitdir under the repo."""
    repo = make_repo(tmp_path)
    wt = tmp_path / "worktrees" / "w1"
    _w(repo, "worktree", "add", "-q", "-b", "kraft/w1", str(wt))
    return wt


def _run(marker: Path) -> str:
    """A shell command that leaves `marker` behind and passes stdin through,
    so it serves as a clean/smudge filter, a textconv or a hook alike."""
    return f"touch {shlex.quote(str(marker))}; cat"


def _plant_gitlink(wt: Path, config: dict[str, str], *, name: str = "evil") -> Path:
    """review-g1's repro, from inside the worktree: a nested repository with
    `.gitattributes` selecting driver `evil`, committed as a gitlink, then
    its config armed and a tracked file dirtied so a recursing `status` has to
    run the clean filter to compare it."""
    nested = wt / name
    nested.mkdir()
    _w(nested, "init", "-q")
    _w(nested, "config", "user.email", "w@w")
    _w(nested, "config", "user.name", "w")
    (nested / ".gitattributes").write_text("* filter=evil diff=evil\n")
    (nested / "payload.txt").write_text("payload\n")
    _w(nested, "add", "-A")
    _w(nested, "commit", "-q", "-m", "x")
    sha = _w(nested, "rev-parse", "HEAD")
    _w(wt, "update-index", "--add", "--cacheinfo", f"160000,{sha},{name}")
    _w(wt, "commit", "-q", "-m", "worker commits its gitlink, as instructed")
    for key, value in config.items():
        _w(nested, "config", key, value)
    (nested / "payload.txt").write_text("changed\n")
    return nested


def test_the_planted_filter_is_live(tmp_path):
    """The positive control for everything below: the exact command
    `assert_clean` used to run does execute the planted filter, even under
    the hooks-and-fsmonitor hardening that came before this fix."""
    wt = _worktree(tmp_path)
    marker = tmp_path / "PWNED"
    _plant_gitlink(wt, {"filter.evil.clean": _run(marker)})
    env = dict(os.environ)
    sandbox.harden_host_git_env(env)
    subprocess.run(
        ["git", "status", "--porcelain", "--ignore-submodules=none"],
        cwd=wt,
        capture_output=True,
        env=env,
    )
    assert marker.exists()


def test_assert_clean_never_runs_a_filter_planted_behind_a_gitlink(tmp_path, hardened):
    """review-g1 finding 1, as Kraft runs it: `open_mr`'s clean check."""
    wt = _worktree(tmp_path)
    marker = tmp_path / "PWNED"
    _plant_gitlink(wt, {"filter.evil.clean": _run(marker)})
    try:
        asyncio.run(forge.assert_clean(wt))
    except forge.ForgeError:
        pass
    assert not marker.exists()
