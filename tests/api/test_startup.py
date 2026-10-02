"""What the server does to existing state as it starts."""

import json
import sys

import pytest

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
