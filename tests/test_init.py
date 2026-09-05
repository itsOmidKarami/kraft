"""`kraft init` — what it writes, and what it refuses to touch."""

from __future__ import annotations

import json

import pytest

from kraft import init


class _Recorder:
    """Stands in for subprocess.run so no test shells out to a real `claude`."""

    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)

        class Result:
            pass

        result = Result()
        result.returncode = self.returncode
        result.stdout = ""
        result.stderr = "" if self.returncode == 0 else "claude: no such command"
        return result


def test_repo_scope_writes_mcp_json_and_a_skill(tmp_path):
    written = init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())

    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert config["mcpServers"]["kraft"]["command"] == "kraft"
    assert config["mcpServers"]["kraft"]["args"] == ["mcp"]
    assert (tmp_path / ".claude" / "skills" / "kraft" / "SKILL.md").is_file()
    assert str(tmp_path / ".mcp.json") in written


def test_repo_scope_does_not_touch_claude_md(tmp_path):
    """Design §1.4: Kraft's process never lands in an ambient, repo-owned file.
    `kraft init` writes files a human opted into, and CLAUDE.md is not one."""
    (tmp_path / "CLAUDE.md").write_text("# mine\n")
    init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())
    assert (tmp_path / "CLAUDE.md").read_text() == "# mine\n"


def test_repo_scope_preserves_other_servers_in_an_existing_mcp_json(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-server"}}})
    )
    init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())

    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"other", "kraft"}


def test_user_scope_delegates_registration_to_the_claude_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    recorder = _Recorder()
    init.install(repo_scope=False, cwd=tmp_path, run=recorder)

    assert recorder.calls, "user scope must shell out rather than edit ~/.claude.json"
    cmd = recorder.calls[0]
    assert cmd[:3] == ["claude", "mcp", "add"]
    assert "--scope" in cmd and "user" in cmd
    # ~/.claude.json is large shared user state; an installer must not rewrite it
    assert not (tmp_path / ".claude.json").exists()


def test_user_scope_still_installs_the_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    init.install(repo_scope=False, cwd=tmp_path, run=_Recorder())
    assert (tmp_path / ".claude" / "skills" / "kraft" / "SKILL.md").is_file()


def test_a_missing_claude_cli_reports_the_command_instead_of_guessing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        init.install(repo_scope=False, cwd=tmp_path, run=_Recorder(returncode=1))
    assert "claude mcp add" in str(exc.value)
