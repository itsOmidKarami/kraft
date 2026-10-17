"""What the server does to existing state as it starts."""

import json
import sys

import pytest
from support.harness import git, make_repo, write

from kraft.adapters import hook_install

_OLD = [sys.executable, "-m", "kraft", "admin", "permission-hook", "cursor"]


@pytest.fixture
def old_cursor_hook(tmp_path):
    """A worktree whose `.cursor/hooks.json` an earlier Kraft wrote, before
    the server starts: its entry ran the hook with plain `-m`."""
    path = tmp_path / "run/worktrees/w1/.cursor/hooks.json"
    path.parent.mkdir(parents=True)
    entry = {"command": hook_install.command_of(_OLD), "timeout": 10, "failClosed": True}
    path.write_text(json.dumps({"version": 1, "hooks": {"preToolUse": [entry]}}))
    return path


def test_start_up_gives_an_earlier_krafts_cursor_hook_safe_path(old_cursor_hook, client):
    """An upgrade must reach worktrees that already exist, or a session adopted
    across the restart keeps a hook a planted module can answer."""
    (entry,) = json.loads(old_cursor_hook.read_text())["hooks"]["preToolUse"]
    assert entry["command"] == hook_install.command_of([sys.executable, "-I", *_OLD[1:]])


@pytest.fixture
def refresh_raises(monkeypatch):
    def boom(_worktrees):
        raise RecursionError("a hooks file built to break the parser")

    monkeypatch.setattr(hook_install, "refresh_cursor_hooks", boom)


def test_a_failed_hook_refresh_never_stops_the_server_starting(refresh_raises, client):
    """Every file the refresh reads is one a worker wrote: one that broke it
    must not stop every later start until someone deletes it."""
    assert client.get("/api/health").status_code == 200


@pytest.fixture
def stale_scratch(tmp_path, request):
    """What a server killed mid-rebase left under `run/scratch`: a throwaway
    worktree holding an in-place item's branch, one git no longer knows, and a
    link someone put there. `request.param`: the repository's git dir is
    apart from its checkout, as a submodule's is."""
    repo = make_repo(tmp_path)
    if getattr(request, "param", False):
        git(repo, "init", "-q", f"--separate-git-dir={tmp_path / 'apart.git'}")
    git(repo, "branch", "kraft/w1")
    scratch = tmp_path / "run/scratch"
    git(repo, "worktree", "add", "-q", str(scratch / "w1-0dead0ff"), "kraft/w1")
    (scratch / "w2-0dead0ff").mkdir()
    # A link to a worktree of the person's: never Kraft's to remove.
    git(repo, "worktree", "add", "-q", "-b", "theirs", str(tmp_path / "mine"))
    write(tmp_path / "mine", "keep.txt", "the person's\n")
    (scratch / "w3-0dead0ff").symlink_to(tmp_path / "mine")
    return repo


@pytest.mark.parametrize(
    "stale_scratch", [False, True], ids=["plain", "git-dir-apart"], indirect=True
)
def test_start_up_removes_the_rebase_worktrees_a_killed_server_left(stale_scratch, client):
    """One would pin its item's branch until that item's next rebase."""
    assert client.get("/api/health").status_code == 200
    assert git(stale_scratch, "worktree", "list", "--porcelain").count("worktree ") == 2
    scratch = stale_scratch.parent / "run/scratch"
    assert [p.name for p in scratch.iterdir()] == ["w3-0dead0ff"], "a link is never Kraft's"
    assert (stale_scratch.parent / "mine/keep.txt").exists()
