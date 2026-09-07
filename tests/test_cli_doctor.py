"""`kraft doctor` — one line per check, exit 1 on any failure."""

from __future__ import annotations

import asyncio

import pytest
from support.harness import make_repo

from kraft import auth, cli, client, doctor

# `app` fixture: tests/conftest.py. It wires client.http() to the ASGI app.


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
    for name in ("health", "templates", "access.yaml", "mcp token", "agent cli", "worktrees"):
        assert name in _names(rows)


def test_a_world_readable_mcp_token_fails(app, tmp_path):
    _prime(tmp_path).chmod(0o644)
    row = _by_name(asyncio.run(doctor.run_checks()), "mcp token")
    assert not row["ok"]
    assert "0644" in row["detail"]


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
        cli.main(["doctor"])
    assert caught.value.code == 1
    out = capsys.readouterr().out
    assert "wi-ghost" in out and "FAIL" in out


def test_doctor_json_is_the_check_list(app, tmp_path, capsys):
    import json

    _prime(tmp_path)
    try:
        cli.main(["doctor", "--json"])
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
