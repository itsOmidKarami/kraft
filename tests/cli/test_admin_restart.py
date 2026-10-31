"""`kraft admin restart --confirm`, the helper an update when Kraft is free hands
the restart to, and `restart_kind`, which says how the running server can
restart. A split-off sibling of `test_admin.py`, kept under the line budget."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from kraft import cli, client
from kraft.paths import RunDirs


@pytest.mark.parametrize(
    ("managed", "mode", "unit_installed", "kind"),
    [
        pytest.param(True, "attached", True, "service", id="launchd-service"),
        pytest.param(False, "detached", False, "detached", id="detached"),
        pytest.param(False, "detached", True, "detached", id="unit-installed-but-hand-started"),
        pytest.param(False, "attached", True, "attached", id="terminal"),
        pytest.param(False, None, False, "attached", id="no-mode-file"),
    ],
)
def test_restart_kind(tmp_path, monkeypatch, managed, mode, unit_installed, kind):
    """A service's mode file reads `attached`, so it cannot decide the service
    case; and a unit that is merely installed does not make a hand-started
    server a service."""
    from kraft import apply

    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    run_dirs = RunDirs(tmp_path / "run")
    run_dirs.base.mkdir(parents=True, exist_ok=True)
    if mode:
        run_dirs.mode.write_text(mode)
    monkeypatch.setattr(apply, "managed", lambda: managed)
    monkeypatch.setattr(cli.admin, "_service_installed", lambda: unit_installed)
    assert cli.admin.restart_kind() == kind


@pytest.fixture
def update_attempt(tmp_path, monkeypatch):
    """An update that installed and snapshotted, as `when_free` leaves it for
    the helper: a config directory and a database as they were, then changed by
    the new server. `calls` records the installer and the service manager."""
    import sqlite3
    from types import SimpleNamespace

    from kraft import update, when_free

    run_dirs = RunDirs(tmp_path / "run").ensure()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "access.yaml").write_text("port: 8765\n")
    with sqlite3.connect(run_dirs.db) as c:
        c.execute("CREATE TABLE t (v TEXT)")
        c.execute("INSERT INTO t VALUES ('old')")
    when_free.write_rollback(
        run_dirs,
        old_pid=1,
        from_version="2.2.0",
        target="2.3.0",
        restart_kind="service",
        kind="release",
        key="v2.3.0",
        requirement={"name": "kraft-sdlc", "extras": ["vector"], "specifier": "==2.2.0"},
    )
    when_free.write_rollback(run_dirs, **when_free.snapshot(run_dirs, config_dir))
    # What the new server did before it failed to confirm.
    (config_dir / "access.yaml").write_text("port: 9999\n")
    with sqlite3.connect(run_dirs.db) as c:
        c.execute("UPDATE t SET v = 'migrated'")
    Path(f"{run_dirs.db}-wal").write_text("stale")

    calls = []
    health = {"payloads": []}

    async def fake_health():
        payloads = health["payloads"]
        if not payloads:
            raise ConnectionError("down")
        return payloads[0] if len(payloads) == 1 else payloads.pop(0)

    async def fake_confirm():
        calls.append("confirm")
        return {"confirmed": True}

    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dirs.base))
    monkeypatch.setattr(client, "health", fake_health)
    monkeypatch.setattr(client, "confirm_update", fake_confirm)
    monkeypatch.setattr(cli.admin.time, "sleep", lambda _s: None)
    monkeypatch.setattr(when_free, "CONFIRM_S", 0.05)
    monkeypatch.setattr(update, "_python", lambda: "3.13")
    monkeypatch.setattr(cli.admin, "_restart_service", lambda: calls.append("restart"))
    monkeypatch.setattr(cli.admin, "_stop_service", lambda: calls.append("stop"))
    monkeypatch.setattr(cli.admin, "_start_service", lambda: calls.append("start"))
    monkeypatch.setattr(
        cli.admin.subprocess,
        "run",
        lambda argv, **_k: calls.append(argv) or subprocess.CompletedProcess(argv, 0),
    )
    monkeypatch.setattr(
        cli.admin,
        "_confirm_running_agents",
        lambda *_a, **_k: pytest.fail("asked about running agents"),
    )
    monkeypatch.setattr("builtins.input", lambda *_a: pytest.fail("asked a question"))
    return SimpleNamespace(
        run_dirs=run_dirs,
        state=SimpleNamespace(run_dirs=run_dirs),
        config_dir=config_dir,
        calls=calls,
        health=health,
    )


def _restart_confirm():
    return cli.main(["admin", "restart", "--confirm"])


def test_restart_confirm_new_pid_confirms(update_attempt):
    from kraft import when_free

    update_attempt.health["payloads"] = [{"pid": 2, "version": "2.3.0"}]

    _restart_confirm()

    assert update_attempt.calls == ["restart", "confirm"]
    # The new server, once confirmed, clears the record and the outcome; the
    # helper changes nothing it put in place.
    assert when_free.read_rollback(update_attempt.run_dirs) is not None
    assert (update_attempt.config_dir / "access.yaml").read_text() == "port: 9999\n"


@pytest.mark.parametrize(
    ("kind", "payload", "installs", "database"),
    [
        pytest.param(
            "release",
            {"pid": 1, "version": "2.2.0"},
            True,
            "old",
            id="same-pid-times-out-and-rolls-back",
        ),
        pytest.param(
            "release",
            {"pid": 2, "version": "2.2.0"},
            True,
            "old",
            id="new-pid-wrong-version-rolls-back",
        ),
        pytest.param(
            "settings",
            {"pid": 1, "version": "2.2.0"},
            False,
            "migrated",
            id="settings-restart-restores-access-yaml",
        ),
        pytest.param(
            "dev",
            {"pid": 1, "version": "2.2.0"},
            False,
            "old",
            id="dev-restores-config-and-databases-not-the-package",
        ),
    ],
)
def test_restart_confirm_rolls_back(update_attempt, kind, payload, installs, database):
    import sqlite3

    from kraft import when_free

    when_free.write_rollback(
        update_attempt.run_dirs,
        kind=kind,
        **({} if kind == "release" else {"requirement": None, "target": None}),
    )
    update_attempt.health["payloads"] = [payload]

    with pytest.raises(SystemExit) as failed:
        _restart_confirm()

    assert failed.value.code == 1
    installed = [c for c in update_attempt.calls if isinstance(c, list)]
    assert installed == (
        [["uv", "tool", "install", "--force", "--python", "3.13", "kraft-sdlc[vector]==2.2.0"]]
        if installs
        else []
    )
    assert update_attempt.calls[:2] == ["restart", "stop"]
    assert update_attempt.calls[-1] == "start"
    assert (update_attempt.config_dir / "access.yaml").read_text() == "port: 8765\n"
    # A settings restart left the databases alone, so there is no restore to clean up for.
    assert Path(f"{update_attempt.run_dirs.db}-wal").exists() is (kind == "settings")
    with sqlite3.connect(update_attempt.run_dirs.db) as c:
        assert c.execute("SELECT v FROM t").fetchone()[0] == database
    assert when_free.read_rollback(update_attempt.run_dirs) is None
    assert when_free.outcome(update_attempt.state)["status"] == "failed"
    assert when_free.failed_key(update_attempt.state) == "v2.3.0"


@pytest.mark.parametrize(
    ("platform", "kind", "starts"),
    [
        pytest.param("linux", "service", "systemd-run", id="linux-service-escapes-the-unit"),
        pytest.param("darwin", "service", None, id="macos-service-is-plain"),
        pytest.param("linux", "detached", None, id="detached-is-plain"),
    ],
)
def test_spawn_restart(tmp_path, monkeypatch, platform, kind, starts):
    from types import SimpleNamespace

    from kraft import apply

    run_dirs = RunDirs(tmp_path / "run").ensure()
    run_dirs.logs.mkdir(parents=True, exist_ok=True)
    seen = []
    monkeypatch.setattr(apply.sys, "platform", platform)
    monkeypatch.setattr(cli.admin, "restart_kind", lambda _d=None: kind)
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(apply.subprocess, "Popen", lambda argv, **kw: seen.append((argv, kw)))

    apply.spawn_restart(SimpleNamespace(run_dirs=run_dirs), confirm=True)

    [(argv, kw)] = seen
    assert kw["start_new_session"] is True
    assert argv[-6:] == ["-P", "-m", "kraft", "admin", "restart", "--confirm"]
    if starts:
        assert argv[:3] == ["systemd-run", "--user", "--collect"]
        assert f"--setenv=KRAFT_HOME={tmp_path / 'home'}" in argv
        assert any(a.startswith("--setenv=PATH=") for a in argv)
    else:
        assert not any(a.startswith("--setenv") for a in argv)
        assert argv[0] == sys.executable


@pytest.mark.parametrize(
    "forges_yaml, passed",
    [
        pytest.param("{from: env, env: CORP_GL}", True, id="named-account-keeps-its-token"),
        pytest.param("{from: command, command: x}", False, id="removed-account-token-not-passed"),
        pytest.param("{from: file, file: /x/t}", False, id="switched-to-file-token-not-passed"),
    ],
)
def test_a_restart_the_server_spawns_keeps_its_env_tokens(
    tmp_path, monkeypatch, forges_yaml, passed
):
    from types import SimpleNamespace

    from kraft import apply
    from kraft.adapters.forge import credentials

    run_dirs = RunDirs(tmp_path / "run").ensure()
    run_dirs.logs.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config))
    (config / "forges.yaml").write_text(
        "accounts:\n  gl:\n    forge: gitlab\n    client: api\n"
        f"    web_url: https://gl.example\n    token: {forges_yaml}\n"
    )
    seen = []
    monkeypatch.setattr(credentials, "_TAKEN", {})
    monkeypatch.setenv("CORP_GL", "tok-0123456789")
    credentials.take_env(["CORP_GL"])
    monkeypatch.setattr(cli.admin, "_kraft_executable", lambda: "/bin/kraft")
    monkeypatch.setattr(apply.subprocess, "Popen", lambda argv, **kw: seen.append((argv, kw)))
    apply.spawn_restart(SimpleNamespace(run_dirs=run_dirs))
    [(argv, kw)] = seen
    assert kw["env"].get("CORP_GL") == ("tok-0123456789" if passed else None)
    assert "CORP_GL" not in os.environ
    assert not any("tok-0123456789" in a for a in argv)


def test_spawn_restart_without_confirm_is_as_it_was(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from kraft import apply

    run_dirs = RunDirs(tmp_path / "run").ensure()
    run_dirs.logs.mkdir(parents=True, exist_ok=True)
    seen = []
    monkeypatch.setattr(cli.admin, "_kraft_executable", lambda: "/bin/kraft")
    monkeypatch.setattr(apply.subprocess, "Popen", lambda argv, **kw: seen.append(argv))
    apply.spawn_restart(SimpleNamespace(run_dirs=run_dirs))
    assert seen == [["/bin/kraft", "admin", "restart"]]
