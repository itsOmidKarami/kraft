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


SKILL_NAMES = ("handoff", "board", "gates", "status")


def _plugin_root(base):
    return base / "skills" / "kraft"


def test_repo_scope_writes_mcp_json_and_the_plugin(tmp_path):
    written = init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())

    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert config["mcpServers"]["kraft"]["command"] == "kraft"
    assert config["mcpServers"]["kraft"]["args"] == ["admin", "mcp"]
    assert _plugin_root(tmp_path / ".claude").is_dir()
    assert str(tmp_path / ".mcp.json") in written


def test_the_manifest_is_what_makes_the_directory_a_namespace(tmp_path):
    """A skills directory without .claude-plugin/plugin.json loads unprefixed.
    The manifest's `name` IS the slash-command prefix, so if it drifts every
    /kraft:* invocation silently becomes something else."""
    init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())

    manifest = json.loads(
        (_plugin_root(tmp_path / ".claude") / ".claude-plugin" / "plugin.json").read_text()
    )
    assert manifest["name"] == "kraft"
    assert manifest["skills"] == ["./"]
    assert manifest["version"]


def test_every_skill_is_installed_and_names_itself_after_its_directory(tmp_path):
    """Claude matches the frontmatter `name`, not the path. A mismatch means the
    skill is invoked as something other than its directory suggests."""
    init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())

    for skill in SKILL_NAMES:
        path = _plugin_root(tmp_path / ".claude") / "skills" / skill / "SKILL.md"
        assert path.is_file(), f"{skill} not installed"
        assert f"name: {skill}\n" in path.read_text()


def test_every_skill_says_when_to_use_it(tmp_path):
    """`description` is what the model matches a request against. An empty or
    stub one means the skill never fires."""
    init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())

    for skill in SKILL_NAMES:
        text = (_plugin_root(tmp_path / ".claude") / "skills" / skill / "SKILL.md").read_text()
        description = text.split("description:", 1)[1].split("---", 1)[0]
        assert len(description.strip()) > 40, f"{skill} description is too thin to match on"
        assert "TODO" not in description


def test_a_stale_flat_skill_is_removed(tmp_path):
    """`kraft init` used to write a single SKILL.md at the plugin root. Left in
    place it shadows nothing but lingers as a second, unprefixed /kraft."""
    stale = _plugin_root(tmp_path / ".claude")
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("---\nname: kraft\n---\nold\n")

    init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())
    assert not (stale / "SKILL.md").exists()


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


def test_user_scope_installs_the_same_plugin(tmp_path, monkeypatch):
    """Verified against the CLI: .claude/skills/<name>/ with a manifest loads as
    <name>@skills-dir in user and project scope alike."""
    monkeypatch.setenv("HOME", str(tmp_path))
    init.install(repo_scope=False, cwd=tmp_path, run=_Recorder())

    root = _plugin_root(tmp_path / ".claude")
    assert (root / ".claude-plugin" / "plugin.json").is_file()
    for skill in SKILL_NAMES:
        assert (root / "skills" / skill / "SKILL.md").is_file()


def test_a_missing_claude_cli_reports_the_command_instead_of_guessing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        init.install(repo_scope=False, cwd=tmp_path, run=_Recorder(returncode=1))
    assert "claude mcp add" in str(exc.value)


def test_the_status_skill_carries_the_phase_line_and_a_bounded_follow(tmp_path):
    """Two things make this skill worth installing: a one-line phase report that
    says what comes *next* (which needs `next_node_id`, Kraft-9rs), and a follow
    that ends itself. An unfiltered `-f` stops on work_item_completed /
    work_item_abandoned; a `--type` filter would go silent through exactly the
    escalation a person needs to hear about."""
    init.install(repo_scope=True, cwd=tmp_path, run=_Recorder())

    body = (_plugin_root(tmp_path / ".claude") / "skills" / "status" / "SKILL.md").read_text()
    assert "next_node_id" in body
    assert "kraft events ID -f" in body
    assert "Do not pass `--type`" in body


def test_skills_dir_prefers_the_bundled_copy(monkeypatch, tmp_path):
    """An installed Kraft has no `plugins/` beside it - only what the wheel ships.

    `just bundle` copies plugins/kraft/skills into `_bundled/plugin-skills`, the
    same way it copies the built SPA, so package-data carries it.
    """
    bundled = tmp_path / "plugin-skills"
    (bundled / "board").mkdir(parents=True)
    (bundled / "board" / "SKILL.md").write_text("bundled\n")
    monkeypatch.setattr(init, "BUNDLED", tmp_path)
    assert init.skills_dir() == bundled


def test_skills_dir_falls_back_to_the_source_tree(monkeypatch, tmp_path):
    """A checkout that never ran `just bundle` still has the real skills."""
    monkeypatch.setattr(init, "BUNDLED", tmp_path / "never-bundled")
    found = init.skills_dir()
    assert found.name == "skills"
    assert (found / "board" / "SKILL.md").is_file()
