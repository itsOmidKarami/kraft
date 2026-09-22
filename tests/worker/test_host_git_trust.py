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

from kraft import builtins, review
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
    """A Kraft-shaped linked worktree: `.git` a file, its gitdir under the
    repo, an `origin` to push to, and a second branch for a checkout."""
    repo = make_repo(tmp_path)
    origin = tmp_path / "origin.git"
    _w(tmp_path, "init", "--bare", "-q", "-b", "main", str(origin))
    _w(repo, "remote", "add", "origin", str(origin))
    _w(repo, "push", "-q", "origin", "main")
    _w(repo, "branch", "kraft/other")
    wt = tmp_path / "worktrees" / "w1"
    _w(repo, "worktree", "add", "-q", "-b", "kraft/w1", str(wt))
    return wt


def _run(marker: Path) -> str:
    """A shell command that leaves `marker` behind and passes stdin through,
    so it serves as a clean/smudge filter, a textconv or a hook alike."""
    return f"touch {shlex.quote(str(marker))}; cat"


def _plant_gitlink(
    wt: Path, config: dict[str, str], *, name: str = "evil", gitmodules: bool = False
) -> Path:
    """review-g1's repro, from inside the worktree: a nested repository with
    `.gitattributes` selecting driver `evil`, committed as a gitlink, then
    its config armed and a tracked file dirtied so a recursing `status` has to
    run the clean filter to compare it.

    `gitmodules` also commits a `.gitmodules` naming it, with `ignore =
    none` -- the per-path override that beats a `diff.ignoreSubmodules`
    pinned in the environment, and what fetch recursion needs to find it."""
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
    if gitmodules:
        (wt / ".gitmodules").write_text(
            f'[submodule "{name}"]\n\tpath = {name}\n\turl = ./{name}\n\tignore = none\n'
        )
        _w(wt, "add", ".gitmodules")
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


#: Config an operator may well have in `~/.gitconfig`, each of which makes
#: git walk into a submodule on its own. Kraft's pins must beat all of them.
_OPERATOR_CONFIG = """\
[submodule]
\trecurse = true
[fetch]
\trecurseSubmodules = yes
[push]
\trecurseSubmodules = on-demand
[diff]
\tsubmodule = diff
\tignoreSubmodules = none
[status]
\tsubmoduleSummary = true
"""


def _hook_dir(tmp_path: Path, marker: Path) -> str:
    hooks = tmp_path / "planted-hooks"
    hooks.mkdir()
    for name in ("post-checkout", "pre-commit", "pre-push"):
        (hooks / name).write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
        (hooks / name).chmod(0o755)
    return str(hooks)


def _include(tmp_path: Path, marker: Path) -> str:
    extra = tmp_path / "planted-include"
    extra.write_text(f'[filter "evil"]\n\tclean = {_run(marker)}\n')
    return str(extra)


_STATUS = ["status", "--porcelain", "--ignore-submodules=none"]
_SUB_DIFF = ["-c", "diff.submodule=diff", "diff", "HEAD~1", "HEAD"]
_SUB_CHECKOUT = ["submodule", "update", "--init", "--force", "--checkout", "--", "evil"]
_RECURSE_FETCH = ["-c", "fetch.recurseSubmodules=yes", "fetch", "-q", "origin"]

#: Every program-source family a nested repository's config can carry:
#: (its config, given the marker and tmp_path; a git command that, run by
#: anything that *does* recurse, executes it -- the positive control).
FAMILIES = {
    "filter-clean": (lambda m, t: {"filter.evil.clean": _run(m)}, _STATUS),
    "filter-process": (lambda m, t: {"filter.evil.process": _run(m)}, _STATUS),
    "filter-smudge": (lambda m, t: {"filter.evil.smudge": _run(m)}, _SUB_CHECKOUT),
    "fsmonitor": (lambda m, t: {"core.fsmonitor": _run(m)}, _STATUS),
    "include-path": (lambda m, t: {"include.path": _include(t, m)}, _STATUS),
    "textconv": (lambda m, t: {"diff.evil.textconv": _run(m)}, _SUB_DIFF),
    "diff-command": (lambda m, t: {"diff.evil.command": _run(m)}, _SUB_DIFF),
    "hooks": (lambda m, t: {"core.hooksPath": _hook_dir(t, m)}, _SUB_CHECKOUT),
    "ext-remote": (
        lambda m, t: {
            "remote.origin.url": f"ext::sh -c touch% {m}",
            "protocol.ext.allow": "always",
        },
        _RECURSE_FETCH,
    ),
    "ssh-command": (
        lambda m, t: {
            "remote.origin.url": "ssh://kraft.invalid/x",
            "core.sshCommand": _run(m),
        },
        _RECURSE_FETCH,
    ),
}


async def _every_host_call(wt: Path) -> None:
    """Each host-side git Kraft runs in a worktree between two sessions, the
    ones a door does not stop first: the straggler sweep and branch restore
    after an agent, the clean check and push `open_mr` makes, and the diff
    endpoint (which a human can hit while a worker still runs)."""

    review.read_change(wt, "HEAD")
    review.read_change(wt, "HEAD~1", head="HEAD")
    for call in (
        forge.commit_stragglers(wt, message="wip"),
        forge.assert_clean(wt),
        forge.git.push(wt, "kraft/w1"),
    ):
        try:
            await call
        except forge.ForgeError:
            pass
    builtins.restore_branch(wt, "kraft/other")


@pytest.mark.parametrize("family", FAMILIES)
def test_no_host_call_runs_a_program_planted_in_a_nested_repository(
    tmp_path, hardened, family, monkeypatch
):
    """Kraft-nx4id, one family at a time, under an operator config that asks
    git to recurse every way it can. Then the control: the same plant, under
    a git that does recurse, runs -- so each pass here is the guard's."""
    config, trigger = FAMILIES[family]
    kraft, control = tmp_path / "kraft", tmp_path / "control"
    for side in (kraft, control):
        side.mkdir()
        _plant_gitlink(_worktree(side), config(side / "PWNED", side), gitmodules=True)
    operator = tmp_path / "operator.gitconfig"
    operator.write_text(_OPERATOR_CONFIG)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(operator))

    asyncio.run(_every_host_call(kraft / "worktrees" / "w1"))
    subprocess.run(
        ["git", *trigger], cwd=control / "worktrees" / "w1", capture_output=True, env=_worker_env()
    )

    assert not (kraft / "PWNED").exists()
    assert (control / "PWNED").exists(), f"control: {trigger} never ran the planted {family}"
