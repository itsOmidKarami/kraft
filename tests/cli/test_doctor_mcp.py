"""`kraft doctor`'s mcp server row, repo by repo: the rest of it is in
`test_doctor.py`, which is at its line budget."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from support.harness import make_repo

from kraft import client, doctor

# `app` fixture: tests/conftest.py. It wires client.transport.http() to the ASGI app.


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_mcp_check_warns_naming_a_repo_whose_committed_settings_turn_the_plugin_off(app, tmp_path):
    """A user-level plugin is not enough: a connected repo's committed
    `.claude/settings.json` wins for its workers, which are then refused. A
    warning naming the harness, not a failure: a repo whose items run only
    sandboxed or on another harness never meets that refusal (Kraft-9efnk.31)."""
    home = Path.home()
    (home / ".claude.json").unlink()
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    _write(
        home / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"kraft@kraft": [{"scope": "user"}]}},
    )
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    _write(repo / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": False}})
    subprocess.run(["git", "add", ".claude"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "off"], cwd=repo, check=True)
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "mcp server")
    assert check["ok"] is True and check["warn"] is True
    assert str(repo) in check["detail"]
    assert "on harness claude" in check["detail"]
    assert "Claude Code's registration only" in check["detail"]
