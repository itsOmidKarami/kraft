"""tests/support/harness.py's own git plumbing, not the fixtures it builds."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from support import harness
from support.fake_beads import ON_FAKE_AND_REAL_BD
from support.harness import git, isolated_bd, make_repo


def test_git_commits_with_no_ambient_identity(tmp_path, monkeypatch):
    """Kraft-f5it: test_resume_marks_needs_human_on_a_rebase_conflict failed
    with `git commit` exiting 128 on a clean checkout. `_isolated_kraft_home`
    (tests/conftest.py) already points HOME at a directory with no
    .gitconfig for every test in the suite; this drives `support.harness.git` directly, with
    none of `make_repo`'s own `git config user.email/name` calls, to prove
    `git` no longer depends on any config existing anywhere.
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
    git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "no ambient identity")  # must not exit 128
    log = Path.read_text(repo / ".git" / "HEAD")
    assert log  # the commit landed; a 128 exit would have raised in git first


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


def test_make_repo_disables_git_auto_maintenance(tmp_path):
    """`git commit` spawns git's detached auto-maintenance, which creates and
    deletes `.git/objects/maintenance.lock` in the background; a `copytree` of
    the template (or a copy made from it, since config is inherited) that
    lists that file and then finds it gone fails (PR #168's flake). The
    template's commit must be made with both keys already off."""
    repo = make_repo(tmp_path)
    for key, want in (("maintenance.auto", "false"), ("gc.auto", "0")):
        out = subprocess.run(
            ["git", "config", "--get", key], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert out == want


def test_a_server_child_finds_the_loud_bd_stub_before_the_real_one(
    tmp_path, monkeypatch, real_binary_guard
):
    """Kraft-vrcw3: the beads fake cannot reach a `python -m kraft` child, so a
    unit test's child must resolve `bd` to the stub that refuses loudly, never
    the real binary; an `e2e("bd")` test's child keeps the real one. A call
    outside `running_server` lands in the real-binary guard's log, which
    fails the test at teardown; this one is read and forgotten here."""
    from support import server

    env = server.child_env()
    stub = shutil.which("bd", path=env["PATH"])
    assert stub == str(server._bd_stub_dir() / "bd")
    refused = subprocess.run([stub, "create"], capture_output=True, text=True, env=env)
    assert refused.returncode == 127
    assert server.BD_STUB_MESSAGE in refused.stderr
    assert [c.split("\t")[::2] for c in real_binary_guard.take()] == [["bd", "create"]]

    monkeypatch.setattr(harness, "REAL_BD", True)
    real_env = server.child_env()
    assert str(server._bd_stub_dir()) not in real_env["PATH"].split(os.pathsep)


def test_a_child_with_no_bd_finds_none(tmp_path, monkeypatch):
    """`child_env(bd=False)`: no stub, no `KRAFT_BD_CWD`, and no `PATH`
    directory holding a real `bd`, so intake takes the "bd is not installed"
    path instead of making a call the stub refuses."""
    from support import server

    real = tmp_path / "bin"
    real.mkdir()
    (real / "bd").write_text("#!/bin/sh\nexit 0\n")
    (real / "bd").chmod(0o755)
    monkeypatch.setenv("PATH", f"{real}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("KRAFT_BD_CWD", str(tmp_path))

    env = server.child_env(bd=False)

    assert shutil.which("bd", path=env["PATH"]) is None
    assert "KRAFT_BD_CWD" not in env
    assert shutil.which("git", path=env["PATH"])


@pytest.mark.slow
def test_a_bd_call_from_a_server_child_shows_in_the_test_result(tmp_path):
    """Kraft-vrcw3's stub refuses loudly in the child's log, and Kraft degrades
    on the refusal, so the test would pass. `running_server` raises
    `BdStubRefused` with each call instead, failing the test."""
    from support.server import BdStubRefused, running_server

    repo = make_repo(tmp_path)
    templates = harness.fake_templates_dir(tmp_path, "true")
    harness.connect_repo(repo, templates)
    with pytest.raises(BdStubRefused, match="create --json --title filed from a child"):
        with running_server(
            run_dir=tmp_path / "run", templates_dir=templates, bd_cwd=isolated_bd(tmp_path)
        ) as srv:
            r = srv.client.post(
                "/api/work-items",
                json={"title": "filed from a child", "repo": str(repo), "autostart": False},
            )
            assert r.status_code == 201, r.text
            assert r.json()["bead_warning"]


@ON_FAKE_AND_REAL_BD
def test_the_beads_fake_and_real_bd_agree(bd, tmp_path):
    """One scenario, run against the autouse fake (tests/support/fake_beads.py)
    and against real bd: filing, blocking, closing, `ready`, `blocked_by` and
    `search` must answer the same from both, per workspace, and both must
    refuse to file a bead where there is no workspace. The `bd` case is
    what keeps the fake from drifting from bd -- if bd's answers change, this
    test fails there, next to the fake it has to change with."""
    from kraft.adapters import beads

    ws, other = str(isolated_bd(tmp_path)), str(isolated_bd(tmp_path, "other"))

    async def scenario():
        a = await beads.intake("the blocker", cwd=ws)
        b = await beads.intake("the blocked", description="brief", cwd=ws)
        assert a != b
        bd.block(b, a, cwd=ws)
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
