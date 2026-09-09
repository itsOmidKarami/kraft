"""The two git guards, against a real `git` and a real remote.

Kraft-8mi8. `_assert_clean` and `_assert_pushed` are the last two things
standing between a chain and landing an empty or a stale merge request, and
every other test drives them through a stub `git` on PATH that answers with
whatever the test wrote into it — so they prove the caller reacts to a string,
never that the string is what `git` says. Here `git` is the real binary and
`origin` is a real `git init --bare` inside tmp_path. Only the forge CLI is
stubbed, and nothing here touches a network.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest
from support.harness import make_repo

from kraft.adapters import forge

BRANCH = "kraft/abc"

GLAB_MR_VIEW = (
    '{"iid":54,"target_branch":"main","source_branch":"kraft/abc","state":"opened",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}'
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _repo_with_origin(tmp_path: Path) -> Path:
    """A real repo on BRANCH, really pushed to a real bare origin in tmp_path."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    repo = make_repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "checkout", "-q", "-b", BRANCH)
    (repo / "work.txt").write_text("the work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the work")
    _git(repo, "push", "-q", "-u", "origin", BRANCH)
    return repo


def _stub_glab(tmp_path: Path, monkeypatch, stdout: str = GLAB_MR_VIEW) -> None:
    """A `glab` on PATH that records its argv. `git` stays the real binary:
    tmp_path is put first on PATH and holds no `git`, so the real one still
    resolves further down."""
    argv = tmp_path / "glab.argv"
    p = tmp_path / "glab"
    p.write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> {argv}; done\n'
        f"cat <<'STUBEOF'\n{stdout}\nSTUBEOF\nexit 0\n"
    )
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")


def _glab_argv(tmp_path: Path) -> list[str]:
    path = tmp_path / "glab.argv"
    return path.read_text().splitlines() if path.exists() else []


def test_open_mr_refuses_a_dirty_worktree_against_real_git(tmp_path, monkeypatch):
    """`git status --porcelain` from the real binary, over a worktree with a
    file the agent never `git add`ed — work item 5163dd1b's failure."""
    repo = _repo_with_origin(tmp_path)
    _stub_glab(tmp_path, monkeypatch)
    (repo / "forgotten.py").write_text("never added\n")

    with pytest.raises(forge.ForgeError, match="forgotten.py"):
        asyncio.run(forge.GlabCli().open_mr(repo=repo, branch=BRANCH, title="t", body="b"))

    assert _glab_argv(tmp_path) == [], "glab ran over an uncommitted worktree"


def test_open_mr_ignores_gitignored_paths_against_real_git(tmp_path, monkeypatch):
    """The other half: `.pytest_cache/` and `.engineering/sessions/` must not
    fail a node. Real git does that exclusion, so only real git can prove it."""
    repo = _repo_with_origin(tmp_path)
    (repo / ".gitignore").write_text("junk/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore junk")
    _git(repo, "push", "-q", "origin", BRANCH)
    (repo / "junk").mkdir()
    (repo / "junk" / "cache.txt").write_text("noise\n")
    _stub_glab(tmp_path, monkeypatch)

    mr = asyncio.run(forge.GlabCli().open_mr(repo=repo, branch=BRANCH, title="t", body="b"))

    assert mr.number == 54
    assert _glab_argv(tmp_path)[:2] == ["mr", "create"]


def test_merge_refuses_an_unpushed_head_against_real_git(tmp_path, monkeypatch):
    """A commit made in the worktree after the last push — Kraft-bxj8's exact
    shape — and `git rev-list --count origin/BRANCH..HEAD` from real git."""
    repo = _repo_with_origin(tmp_path)
    (repo / "late.txt").write_text("committed after the push\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "late")
    _stub_glab(tmp_path, monkeypatch, stdout="")

    with pytest.raises(forge.ForgeError, match="ahead of origin/kraft/abc by 1"):
        asyncio.run(forge.GlabCli().merge(repo=repo, branch=BRANCH, mr=forge.MR(0, "")))

    assert _glab_argv(tmp_path) == [], "glab merged a head the forge has never seen"


def test_merge_accepts_a_pushed_head_against_real_git(tmp_path, monkeypatch):
    """The ordinary path: really pushed, so the guard lets the CLI run."""
    repo = _repo_with_origin(tmp_path)
    _stub_glab(tmp_path, monkeypatch, stdout="")

    asyncio.run(forge.GlabCli().merge(repo=repo, branch=BRANCH, mr=forge.MR(0, "")))

    assert _glab_argv(tmp_path)[:2] == ["mr", "merge"]
