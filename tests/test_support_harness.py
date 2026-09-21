"""tests/support/harness.py's own git plumbing, not the fixtures it builds."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from support import harness
from support.harness import _git, isolated_bd, make_repo


def test_git_commits_with_no_ambient_identity(tmp_path, monkeypatch):
    """Kraft-f5it: test_resume_marks_needs_human_on_a_rebase_conflict failed
    with `git commit` exiting 128 on a clean checkout. `_isolated_kraft_home`
    (tests/conftest.py) already points HOME at a directory with no
    .gitconfig for every test in the suite; this drives `_git` directly, with
    none of `make_repo`'s own `git config user.email/name` calls, to prove
    `_git` no longer depends on any config existing anywhere.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    # Block git's own username@hostname auto-detect so this test actually
    # pins spec section 6's "no ambient identity" condition, instead of
    # passing on hosts where the fallback identity resolves on its own.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "no ambient identity")  # must not exit 128
    log = Path.read_text(repo / ".git" / "HEAD")
    assert log  # the commit landed; a 128 exit would have raised in _git first


def test_a_make_repo_copy_reads_clean_to_git_plumbing(tmp_path):
    """`make_repo` copies a cached template (Kraft-qmhfc). Its index must not
    keep the template's stat data: `git diff-index` does not refresh the index
    the way porcelain does, and on a stale one it calls every tracked file
    modified, which a fresh build never would (PR #88 review, F3)."""
    repo = make_repo(tmp_path)
    out = subprocess.run(
        ["git", "diff-index", "--name-only", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out == ""


def test_a_server_child_finds_the_loud_bd_stub_before_the_real_one(tmp_path, monkeypatch):
    """Kraft-vrcw3: the beads fake cannot reach a `python -m kraft` child, so a
    unit test's child must resolve `bd` to the stub that refuses loudly, never
    the real binary; an `e2e("bd")` test's child keeps the real one."""
    from support import server

    env = server.child_env(tmp_path, tmp_path, tmp_path, 1, None)
    stub = shutil.which("bd", path=env["PATH"])
    assert stub == str(server._bd_stub_dir() / "bd")
    refused = subprocess.run([stub, "create"], capture_output=True, text=True, env=env)
    assert refused.returncode == 127
    assert server.BD_STUB_MESSAGE in refused.stderr

    monkeypatch.setattr(harness, "REAL_BD", True)
    real_env = server.child_env(tmp_path, tmp_path, tmp_path, 1, None)
    assert str(server._bd_stub_dir()) not in real_env["PATH"].split(os.pathsep)


@pytest.mark.parametrize("backend", ["fake", pytest.param("bd", marks=pytest.mark.e2e("bd"))])
def test_the_beads_fake_and_real_bd_agree(backend, fake_beads, tmp_path):
    """One scenario, run against the autouse fake (tests/support/fake_beads.py)
    and against real bd: filing, blocking, closing, `ready`, `blocked_by` and
    `search` must answer the same from both, per workspace, and both must
    refuse to file a bead where there is no workspace. The `bd` case is
    what keeps the fake from drifting from bd -- if bd's answers change, this
    test fails there, next to the fake it has to change with."""
    from kraft.adapters import beads

    if backend == "fake" and fake_beads is None:
        pytest.skip("KRAFT_TEST_REAL_BD=1: no fake installed to check")
    ws, other = str(isolated_bd(tmp_path)), str(isolated_bd(tmp_path, "other"))

    def block(bead, blocker):
        if backend == "fake":
            fake_beads.block(bead, blocker, cwd=ws)
        else:
            subprocess.run(
                ["bd", "dep", "add", bead, blocker, "--type", "blocks"],
                cwd=ws,
                capture_output=True,
                text=True,
                check=True,
            )

    async def scenario():
        a = await beads.intake("the blocker", cwd=ws)
        b = await beads.intake("the blocked", description="brief", cwd=ws)
        assert a != b
        block(b, a)
        assert await beads.blocked_by([b], cwd=ws) == [a]
        assert await beads.blocked_by([a], cwd=ws) == []
        assert [r["id"] for r in await beads.ready(cwd=ws)] == [a]
        assert await beads.ready(cwd=other) == []
        await beads.complete(a, cwd=ws)
        assert await beads.blocked_by([b], cwd=ws) == []
        assert [(r["id"], r["description"]) for r in await beads.ready(cwd=ws)] == [(b, "brief")]
        assert [(h["id"], h["status"]) for h in await beads.search("blocker", cwd=ws)] == [
            (a, "closed")
        ]
        # No `.beads/` at or above cwd: bd refuses to file, and the readers
        # answer nothing rather than raise.
        nowhere = tmp_path / "nowhere"
        nowhere.mkdir()
        with pytest.raises(RuntimeError, match="no beads database found"):
            await beads.intake("filed nowhere", cwd=str(nowhere))
        assert await beads.ready(cwd=str(nowhere)) == []
        assert await beads.search("blocker", cwd=str(nowhere)) == []
        # A cwd that does not exist, even inside a workspace: bd never
        # starts. Filing and closing raise the OSError, the readers answer
        # nothing rather than the enclosing workspace's beads.
        gone = str(Path(ws) / "gone")
        with pytest.raises(OSError):
            await beads.intake("filed nowhere", cwd=gone)
        with pytest.raises(OSError):
            await beads.complete(b, cwd=gone)
        assert await beads.ready(cwd=gone) == []
        assert await beads.blocked_by([b], cwd=gone) == []

    asyncio.run(scenario())
