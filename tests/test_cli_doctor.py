"""`kraft doctor` — one line per check, exit 1 on any failure."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from support.harness import make_repo

from kraft import auth, cli, client, doctor

# `app` fixture: tests/conftest.py. It wires client.transport.http() to the ASGI app.


def _names(rows):
    return [row["name"] for row in rows]


def _by_name(rows, name):
    return next(row for row in rows if row["name"] == name)


def _prime(tmp_path):
    """One call through the app, so its lifespan has created the run dir and the
    MCP token the way a real first start does."""
    asyncio.run(client.health())
    return tmp_path / "run" / auth.MCP_TOKEN_FILE


def test_doctor_on_a_live_instance_reaches_every_check(app, tmp_path):
    _prime(tmp_path)
    rows = asyncio.run(doctor.run_checks())
    assert _by_name(rows, "server")["ok"]
    for name in (
        "health",
        "templates",
        "access.yaml",
        "mcp token",
        "agent cli",
        "mcp server",
        "shell completion",
        "bd",
        "worktrees",
    ):
        assert name in _names(rows)


def test_a_world_readable_mcp_token_fails(app, tmp_path):
    _prime(tmp_path).chmod(0o644)
    row = _by_name(asyncio.run(doctor.run_checks()), "mcp token")
    assert not row["ok"]
    assert "0644" in row["detail"]


def test_doctor_reports_a_missing_bd_without_failing(app, tmp_path, monkeypatch):
    """Kraft-7gy: bd is optional. A deliberately bd-less install is not broken,
    so the line reports and does not set doctor's exit code."""
    _prime(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    rows = asyncio.run(doctor.run_checks())
    row = _by_name(rows, "bd")
    assert row["ok"]
    assert "not installed" in row["detail"]


def test_a_dead_server_is_one_failure_and_the_rest_are_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path / "templates"))

    async def dead():
        raise ValueError("no Kraft server at http://127.0.0.1:1 — start one with `kraft serve`")

    monkeypatch.setattr(client, "health", dead)
    rows = asyncio.run(doctor.run_checks())
    failed = {row["name"] for row in rows if not row["ok"]}
    # the local checks still run and still fail on their own merits — but
    # nothing downstream of the server does, or one dead server reads as five
    # problems. (`agent cli` is deliberately not asserted either way: whether
    # `claude` is installed is a fact about the machine, not about doctor.)
    assert "server" in failed
    assert {"templates", "mcp token"} <= failed
    assert all(_by_name(rows, name)["skipped"] for name in ("health", "repos", "worktrees"))
    assert not failed & {"health", "repos", "worktrees"}


def test_a_disconnected_repo_path_fails(app, tmp_path, monkeypatch):
    repo = make_repo(tmp_path, name="gone")
    asyncio.run(client.ensure_repo(str(repo)))
    (repo / ".git").rename(repo / "not-git")
    row = next(r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("repo "))
    assert not row["ok"]
    assert "no longer a git repo" in row["detail"]


def _bind_auto_forge(tmp_path):
    """Put a `backend: auto` forge hook in the registry the server loaded.

    `conftest.app` points KRAFT_TEMPLATES_DIR at `fake_templates_dir`, whose
    back half is all `builtin: noop` — so without this the forge check is
    correctly silent and neither test below would exercise anything.
    """
    path = tmp_path / "templates" / "registry.yaml"
    data = yaml.safe_load(path.read_text())
    data["hooks"]["on.mr.open"] = {"kind": "forge", "handler": "open_mr", "backend": "auto"}
    path.write_text(yaml.safe_dump(data))


def test_doctor_fails_when_an_auto_hook_has_no_forge_recorded(app, tmp_path):
    """make_repo never adds an origin, so `probe_repo` records forge: None —
    the state every pre-forge repos.yaml entry is already in."""
    repo = make_repo(tmp_path, name="noforge")
    asyncio.run(client.ensure_repo(str(repo)))
    _bind_auto_forge(tmp_path)

    row = _by_name(asyncio.run(doctor.run_checks()), "forge noforge")

    assert not row["ok"]
    assert "repos.yaml" in row["detail"]


def test_doctor_reports_the_resolved_forge_cli(app, tmp_path, monkeypatch):
    repo = make_repo(tmp_path, name="onforge")
    subprocess.run(
        ["git", "remote", "add", "origin", "git@gitlab.com:group/repo.git"],
        cwd=repo,
        check=True,
    )
    asyncio.run(client.ensure_repo(str(repo)))
    _bind_auto_forge(tmp_path)
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    glab = stub_dir / "glab"
    glab.write_text("#!/bin/sh\nexit 0\n")
    glab.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ['PATH']}")

    row = _by_name(asyncio.run(doctor.run_checks()), "forge onforge")

    assert row["ok"]
    assert row["detail"] == "gitlab · glab"


def test_no_forge_check_when_nothing_is_bound_to_auto(app, tmp_path):
    """The check is about a binding the operator actually has: a registry with
    no `auto` forge hook must not grow a row per repo telling them to fix
    something they are not using."""
    repo = make_repo(tmp_path, name="quiet")
    asyncio.run(client.ensure_repo(str(repo)))

    names = _names(asyncio.run(doctor.run_checks()))

    assert "repo quiet" in names
    assert "forge quiet" not in names


def test_an_orphaned_worktree_is_reported(app, tmp_path):
    _prime(tmp_path)
    (tmp_path / "run" / "worktrees" / "wi-ghost").mkdir(parents=True)
    row = _by_name(asyncio.run(doctor.run_checks()), "worktrees")
    assert not row["ok"]
    assert "wi-ghost" in row["detail"]


def test_degraded_health_is_spelled_out_one_reason_per_line(app, monkeypatch):
    async def degraded():
        return {
            "status": "degraded",
            "invalid_templates": {"quick-task.yaml": "bad node"},
            "invalid_policy": "budget: not a number",
            "index": {"errors": ["scan failed"], "embeddings": {"available": True}},
            "reattach_summary": {"unknown": []},
        }

    monkeypatch.setattr(client, "health", degraded)
    details = [row["detail"] for row in asyncio.run(doctor.run_checks()) if row["name"] == "health"]
    assert len(details) == 3
    assert any("quick-task.yaml" in d for d in details)


def test_doctor_exits_1_and_prints_the_failures(app, tmp_path, capsys):
    _prime(tmp_path)
    (tmp_path / "run" / "worktrees" / "wi-ghost").mkdir(parents=True)
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "doctor"])
    assert caught.value.code == 1
    out = capsys.readouterr().out
    assert "wi-ghost" in out and "FAIL" in out


def test_doctor_json_is_the_check_list(app, tmp_path, capsys):
    import json

    _prime(tmp_path)
    try:
        cli.main(["admin", "doctor", "--json"])
    except SystemExit:
        pass
    rows = json.loads(capsys.readouterr().out)
    assert {"name", "ok", "detail", "skipped"} <= set(rows[0])


def test_hooks_check_names_a_hook_left_on_noop(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "registry.yaml").write_text(
        "hooks:\n"
        "  on.spec.requested: { kind: agent, command: claude, skill: spec, artifact: spec }\n"
    )
    live = tmp_path / "templates"
    live.mkdir()
    (live / "registry.yaml").write_text(
        "hooks:\n  on.spec.requested: { kind: builtin, handler: noop }\n"
    )
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "hooks")
    assert check["ok"] is True  # an operator's choice, not a failure
    assert "on.spec.requested" in check["detail"]


def test_hooks_check_names_a_hook_missing_from_the_live_registry(tmp_path, monkeypatch):
    """The shape a registry seeded before a hook point existed takes: the key is
    not on the placeholder, it is not there at all. `live.get(h)` returns None,
    which is not a noop, so this case used to exempt itself from the very check
    written for it (Kraft-zmb)."""
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "registry.yaml").write_text(
        "hooks:\n"
        "  on.spec.requested: { kind: agent, command: claude, skill: spec, artifact: spec }\n"
    )
    live = tmp_path / "templates"
    live.mkdir()
    (live / "registry.yaml").write_text(
        "hooks:\n  on.env.prepare: { kind: builtin, handler: env_setup }\n"
    )
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "hooks")
    assert check["ok"] is True
    assert "on.spec.requested" in check["detail"]
    assert "missing entirely" in check["detail"]


def test_every_config_check_reports_even_with_no_templates_dir(tmp_path, monkeypatch):
    """CI has no `$KRAFT_HOME/templates`, and `_config_checks` returns early
    there. Every row it can emit must still emit, or a caller reading the run by
    name gets StopIteration instead of an answer — which is how this reached a
    red pipeline while passing on a developer machine that had the directory.
    """
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")

    rows = asyncio.run(doctor.run_checks())
    assert _by_name(rows, "templates")["ok"] is False
    hooks = _by_name(rows, "hooks")
    assert hooks["ok"] is True and hooks["skipped"] is True


def test_hooks_check_is_skipped_without_a_bundled_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")
    check = _by_name(asyncio.run(doctor.run_checks()), "hooks")
    assert check["skipped"] is True


def test_bundle_check_fails_when_the_spa_is_missing(monkeypatch, tmp_path):
    """A wheel built without `just bundle` serves JSON and no UI. Nothing said so."""
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")
    row = doctor._bundle_check()
    assert not row["ok"]
    assert "just bundle" in row["detail"]


def test_bundle_check_passes_when_the_spa_is_there(monkeypatch, tmp_path):
    web = tmp_path / "_bundled" / "web"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<html></html>")
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "_bundled")
    assert doctor._bundle_check()["ok"]


def test_version_check_is_never_a_failure(monkeypatch):
    """A release day must not start failing `kraft admin doctor && deploy`."""
    from kraft import update

    monkeypatch.delenv("KRAFT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v9.9.9", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.1.0")
    row = doctor._version_check()
    assert row["ok"]
    assert "9.9.9" in row["detail"] and "available" in row["detail"]


def test_version_check_says_so_when_current(monkeypatch):
    from kraft import update

    monkeypatch.delenv("KRAFT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v0.4.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.4.0")
    assert "newest" in doctor._version_check()["detail"]


def test_version_check_with_no_network_skips(monkeypatch):
    from kraft import update

    monkeypatch.delenv("KRAFT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update, "latest", lambda **_: None)
    row = doctor._version_check()
    assert row["ok"] and row["skipped"]


def test_version_check_honours_the_no_check_env_var(monkeypatch):
    """One switch silences every version check, not only the one at boot.

    Without this, `run_checks()` reaches the network - which is a test suite that
    depends on gitlab.com being up, and an air-gapped operator paying the timeout
    every time they run doctor.
    """
    from kraft import update

    monkeypatch.setenv("KRAFT_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(update, "latest", lambda **_: pytest.fail("checked with the env var set"))
    row = doctor._version_check()
    assert row["ok"] and row["skipped"]


def test_completion_check_is_skipped_outside_zsh(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    check = _by_name(asyncio.run(doctor.run_checks()), "shell completion")
    assert check["skipped"] is True


def test_completion_check_names_the_missing_eval_line(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
    (tmp_path / ".zshrc").write_text("# nothing relevant here\n")
    check = _by_name(asyncio.run(doctor.run_checks()), "shell completion")
    assert check["ok"] is True
    assert "register-python-argcomplete kraft" in check["detail"]


def test_completion_check_passes_once_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
    (tmp_path / ".zshrc").write_text('eval "$(register-python-argcomplete kraft)"\n')
    check = _by_name(asyncio.run(doctor.run_checks()), "shell completion")
    assert check["ok"] is True
    assert "registered in" in check["detail"]


def test_mcp_check_passes_on_a_user_scope_registration(app, tmp_path):
    """What `kraft admin init` produces via `claude mcp add --scope user`
    (init.py:122). `HOME` is already a throwaway directory (conftest's autouse
    `_isolated_kraft_home`), so this writes the real file the check reads."""
    home = Path.home()
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"kraft": {"command": "kraft", "args": ["admin", "mcp"]}}})
    )
    check = _by_name(asyncio.run(doctor.run_checks()), "mcp server")
    assert check["ok"] is True
    assert ".claude.json" in check["detail"]


def test_mcp_check_passes_on_a_repo_scope_mcp_json(app, tmp_path):
    """What `kraft admin init --repo` writes (init._write_repo_mcp_json)."""
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"kraft": {"command": "kraft"}}}))
    check = _by_name(asyncio.run(doctor.run_checks()), "mcp server")
    assert check["ok"] is True
    assert ".mcp.json" in check["detail"]


def test_mcp_check_fails_when_nothing_registers_kraft(app, tmp_path):
    """A real failure, not an advisory like `bd` or shell completion: every
    worker launch passes `--permission-prompt-tool mcp__kraft__permission_request`,
    and with no registration the agent CLI exits 0 ignoring the flag while a
    running chain's permission asks go unanswered."""
    check = _by_name(asyncio.run(doctor.run_checks()), "mcp server")
    assert check["ok"] is False
    assert "kraft admin init" in check["detail"]
