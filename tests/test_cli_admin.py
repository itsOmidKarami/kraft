"""health and reindex — the server's own view, from a terminal."""

from __future__ import annotations

import asyncio
import json
import os
import signal

import pytest
from support.harness import fake_templates_dir

from kraft import cli, client
from kraft.paths import RunDirs

# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.http()
# to the ASGI app with the lifespan entered per client.


def test_health_returns_the_status_block(app):
    payload = asyncio.run(client.health())
    assert payload["status"] in ("ok", "degraded")
    assert "index" in payload and "bind" in payload


def test_reindex_all_returns_totals(app):
    payload = asyncio.run(client.reindex())
    assert payload["repo"] is None
    assert set(payload["stats"]) == {"inserted", "updated", "renamed", "deleted"}


def test_reindex_unknown_repo_is_a_readable_404(app):
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.reindex("/no/such/repo"))


def test_health_exit_code_follows_status(app, monkeypatch, capsys):
    async def degraded():
        return {
            "status": "degraded",
            "bind": "127.0.0.1",
            "invalid_templates": {"x.yaml": "bad"},
            "invalid_policy": None,
            "reattach_summary": {
                "scanned": 0,
                "adopted": [],
                "resolved_from_file": [],
                "unknown": [],
                "resumed_work_items": [],
            },
            "index": {
                "documents": 0,
                "repos_scanned": 0,
                "last_scan_at": None,
                "embeddings": {"available": True, "model": "m", "chunks": 0, "reason": None},
                "errors": [],
            },
        }

    monkeypatch.setattr(client, "health", degraded)
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "health"])
    assert caught.value.code == 1
    assert "x.yaml" in capsys.readouterr().out  # the reason is on stdout, it is not an error


def test_health_ok_exits_zero(app, capsys):
    cli.main(["admin", "health"])  # a fresh fixture home is healthy; no SystemExit
    assert "ok" in capsys.readouterr().out


def test_health_json_is_the_raw_payload(app, capsys):
    cli.main(["admin", "health", "--json"])
    printed = json.loads(capsys.readouterr().out)
    direct = asyncio.run(client.health())
    # last_scan_at moves between two calls; everything else is the payload verbatim
    for payload in (printed, direct):
        payload["index"].pop("last_scan_at")
    assert printed == direct


def test_reindex_prints_the_counts(app, capsys):
    cli.main(["admin", "reindex"])
    out = capsys.readouterr().out
    for key in ("inserted", "updated", "renamed", "deleted"):
        assert key in out


def test_reindex_unknown_repo_is_a_kraft_message(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "reindex", "--repo", "/no/such/repo"])
    assert caught.value.code == 1
    assert "404" in capsys.readouterr().err


def test_serve_writes_and_clears_the_pidfile(tmp_path, monkeypatch):
    """`kraft admin stop` needs a pid, and a stopped server must leave none."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    seen = {}

    def fake_run(*args, **kwargs):
        seen["pid"] = RunDirs(tmp_path / "run").pid.read_text().strip()

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    cli._serve()
    assert seen["pid"] == str(os.getpid())
    assert not RunDirs(tmp_path / "run").pid.exists()


def test_a_second_serve_refuses_while_one_is_live(tmp_path, monkeypatch, capsys):
    """Two servers on one KRAFT_HOME share databases and worktrees with no port
    conflict to reveal it."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: pytest.fail("started anyway"))
    with pytest.raises(SystemExit):
        cli._serve()
    assert "already running" in capsys.readouterr().err
    assert pid_path.exists()


def test_a_stale_pidfile_does_not_block_serve(tmp_path, monkeypatch):
    """A pidfile that outlived a SIGKILLed server is stale, not a conflict."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("999999")
    started = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: started.append(True))
    cli._serve()
    assert started == [True]


def test_stop_signals_the_running_pid(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4171")
    signalled, alive = [], [True]

    def fake_kill(pid, sig):
        if sig == 0 and not alive[0]:
            raise ProcessLookupError
        if sig != 0:
            signalled.append((pid, sig))
            alive[0] = False

    monkeypatch.setattr(cli.os, "kill", fake_kill)
    cli.main(["admin", "stop"])
    assert signalled == [(4171, signal.SIGTERM)]
    assert "4171" in capsys.readouterr().out


def test_stop_with_no_server_is_not_an_error(tmp_path, monkeypatch, capsys):
    """Safe to run twice: a teardown script must not fail on the second call."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    cli.main(["admin", "stop"])
    assert "no server running" in capsys.readouterr().out


def test_stop_reports_a_stale_pidfile_and_clears_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("999999")
    cli.main(["admin", "stop"])
    assert "no server running" in capsys.readouterr().out
    assert not pid_path.exists()


def test_admin_update_when_current_does_nothing(monkeypatch, capsys):
    from kraft import update

    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v0.4.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.4.0")
    monkeypatch.setattr(
        update, "perform", lambda *_a, **_k: pytest.fail("installed over a current version")
    )
    cli.main(["admin", "update"])
    assert "up to date" in capsys.readouterr().out


def test_admin_update_force_installs_anyway(monkeypatch, capsys):
    from kraft import update

    called = []
    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v0.4.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.4.0")
    monkeypatch.setattr(update, "perform", lambda *a, **k: called.append(a) or 0)
    cli.main(["admin", "update", "--force"])
    assert called


def test_admin_update_with_no_release_known_exits_1(monkeypatch, capsys):
    from kraft import update

    monkeypatch.setattr(update, "latest", lambda **_: None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["admin", "update"])
    assert exc.value.code == 1
    assert "could not reach" in capsys.readouterr().err


def test_start_prints_the_update_notice(monkeypatch, capsys):
    from kraft import update

    monkeypatch.delenv("KRAFT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v9.9.9", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.1.0")
    cli._update_notice()
    assert "9.9.9" in capsys.readouterr().out


def test_start_notice_is_silenced_by_the_env_var(monkeypatch, capsys):
    from kraft import update

    monkeypatch.setenv("KRAFT_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(update, "latest", lambda **_: pytest.fail("checked with the env var set"))
    cli._update_notice()
    assert capsys.readouterr().out == ""
