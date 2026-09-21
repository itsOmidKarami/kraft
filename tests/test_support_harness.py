"""tests/support/harness.py's own git plumbing, not the fixtures it builds."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from support.harness import _git, isolated_bd


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


@pytest.mark.parametrize("backend", ["fake", pytest.param("bd", marks=pytest.mark.e2e("bd"))])
def test_the_beads_fake_and_real_bd_agree(backend, fake_beads, tmp_path):
    """One scenario, run against the autouse fake (tests/support/fake_beads.py)
    and against real bd: filing, blocking, closing, `ready`, `blocked_by` and
    `search` must answer the same from both, per workspace. The `bd` case is
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

    asyncio.run(scenario())
