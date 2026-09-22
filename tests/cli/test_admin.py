"""health and reindex — the server's own view, from a terminal."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
import yaml
from support.harness import fake_templates_dir
from support.server import child_env

from kraft import cli, client
from kraft import harness as _harness
from kraft.adapters import agent as _agent
from kraft.paths import RunDirs
from kraft.templates.environment import HarnessProfileTable
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import AgentTask

# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.transport.http()
# to the ASGI app with the lifespan entered per client.

#: `tests/cli/test_admin.py` is one level under `tests/`, so the repo root --
#: where the real shipped `templates/` lives -- is `parents[2]`, not
#: `parents[1]` (CLAUDE.md: resolve nested-test paths against the filesystem,
#: never trust the arithmetic).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED_TEMPLATES = _REPO_ROOT / "templates"


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


def test_reload_templates_returns_the_template_set(app):
    payload = asyncio.run(client.reload_templates())
    assert set(payload) == {"valid", "invalid_templates", "refused_policy"}
    assert {"quick-task", "default"} <= set(payload["valid"])


def test_reload_prints_the_count(app, capsys):
    cli.main(["admin", "reload"])
    out = capsys.readouterr().out
    assert "reloaded 2 template(s)" in out


def test_reload_reports_invalid_templates_and_exits_1(app, capsys):
    templates_dir = Path(os.environ["KRAFT_TEMPLATES_DIR"])
    (templates_dir / "library.yaml").write_text("tasks: [unclosed\n")
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "reload"])
    assert caught.value.code == 1
    assert "invalid: library.yaml" in capsys.readouterr().out


def test_reload_reports_a_refused_policy_and_exits_1(app, capsys):
    templates_dir = Path(os.environ["KRAFT_TEMPLATES_DIR"])
    (templates_dir / "policy.yaml").write_text("default: [unclosed\n")
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "reload"])
    assert caught.value.code == 1
    assert "refused: policy.yaml" in capsys.readouterr().out


def test_reload_refused_by_the_server_is_a_kraft_message(app, monkeypatch, capsys):
    # Mocking the client call, like `test_health_exit_code_follows_status`
    # does, isolates the thing this test checks: `main()`'s ValueError-to-exit-1
    # handling of a refused request.
    async def broken():
        raise ValueError("kraft 422: the server refused the reload")

    monkeypatch.setattr(client, "reload_templates", broken)
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "reload"])
    assert caught.value.code == 1
    assert "422" in capsys.readouterr().err


def test_admin_templates_lint_of_a_clean_library_exits_0(app, capsys):
    cli.main(["admin", "templates", "lint"])
    assert "no errors" in capsys.readouterr().out


def test_admin_templates_lint_prints_each_error_and_exits_1(app, capsys):
    chains = Path(os.environ["KRAFT_TEMPLATES_DIR"]) / "chains"
    (chains / "garbled.yaml").write_text("nodes: [unclosed\n")
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "templates", "lint"])
    assert caught.value.code == 1
    assert "garbled: " in capsys.readouterr().out


def test_admin_templates_show_resolved_prints_the_expanded_chain(app, capsys):
    cli.main(["admin", "templates", "show", "default", "--resolved"])
    printed = yaml.safe_load(capsys.readouterr().out)
    assert printed["id"] == "default"
    # Expanded out of library.yaml, which the chain file only names.
    assert printed["nodes"][0]["tasks"][0]["skill"] == "kraft:spec"


def test_admin_templates_show_prints_the_saved_template(app, capsys):
    """Without `--resolved`: the chain file as its author wrote it."""
    cli.main(["admin", "templates", "show", "default"])
    saved = (Path(os.environ["KRAFT_TEMPLATES_DIR"]) / "chains" / "default.yaml").read_text()
    assert capsys.readouterr().out == saved


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

    def fake_run(self, *args, **kwargs):
        seen["pid"] = RunDirs(tmp_path / "run").pid.read_text().strip()

    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", fake_run)
    cli.admin._serve()
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
    monkeypatch.setattr(
        cli.admin._SignalLoggingServer,
        "run",
        lambda self, *a, **k: pytest.fail("started anyway"),
    )
    with pytest.raises(SystemExit):
        cli.admin._serve()
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
    monkeypatch.setattr(
        cli.admin._SignalLoggingServer,
        "run",
        lambda self, *a, **k: started.append(True),
    )
    cli.admin._serve()
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

    monkeypatch.setattr(os, "kill", fake_kill)
    cli.main(["admin", "stop"])
    assert signalled == [(4171, signal.SIGTERM)]
    assert "4171" in capsys.readouterr().out


def test_stop_waits_for_the_process_to_actually_exit(tmp_path, monkeypatch, capsys):
    """The review finding behind this: the pidfile used to be unlinked at
    signal *delivery*, so `stop` printed "stopped" while the old process was
    still draining with the databases open. Real process, real SIGTERM, and
    a deliberately slow exit."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,sys,time\n"
            "signal.signal(signal.SIGTERM, lambda *a: (time.sleep(1.5), sys.exit(0)))\n"
            "time.sleep(30)\n",
        ]
    )
    # Reaped as it exits: an unwaited child stays a zombie, and `os.kill(pid, 0)`
    # succeeds on a zombie. The real daemon is never the stopping shell's child.
    reaper = threading.Thread(target=proc.wait, daemon=True)
    reaper.start()
    try:
        pid_path.write_text(str(proc.pid))
        cli.main(["admin", "stop"])
        assert proc.poll() is not None, "stop returned while the process was still alive"
        assert f"stopped (pid {proc.pid})" in capsys.readouterr().out
    finally:
        if proc.poll() is None:
            proc.kill()
        reaper.join(timeout=5)


def test_handle_exit_leaves_the_pidfile_for_the_drain(tmp_path):
    """`handle_exit` runs at signal delivery, before uvicorn drains -- the
    pidfile has to keep naming the still-running process until it is gone."""
    pid_path = tmp_path / "kraft.pid"
    pid_path.write_text(str(os.getpid()))
    server = cli.admin._SignalLoggingServer(uvicorn.Config("kraft.api:app"))
    server.handle_exit(signal.SIGTERM, None)
    assert server.should_exit
    assert pid_path.read_text() == str(os.getpid())


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


@pytest.mark.parametrize("other", [None, "/opt/homebrew/bin/kraft"], ids=["alone", "shadowed"])
def test_admin_update_warns_when_another_kraft_is_first_on_path(monkeypatch, capsys, other):
    """Kraft-xs3ri: the update lands in this install; an MCP session running
    `kraft` by name keeps the shadowing one, so say so where the human looks."""
    from kraft import update

    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v0.5.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.4.0")
    monkeypatch.setattr(update, "perform", lambda *a, **k: 0)
    monkeypatch.setattr(update, "shadowing_kraft", lambda: other)
    cli.main(["admin", "update"])
    out = capsys.readouterr().out
    assert ("/opt/homebrew/bin/kraft" in out) is (other is not None)


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
    cli.admin._update_notice()
    assert "9.9.9" in capsys.readouterr().out


def test_start_notice_is_silenced_by_the_env_var(monkeypatch, capsys):
    from kraft import update

    monkeypatch.setenv("KRAFT_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(update, "latest", lambda **_: pytest.fail("checked with the env var set"))
    cli.admin._update_notice()
    assert capsys.readouterr().out == ""


def test_serve_exports_its_identity_for_workers(tmp_path, monkeypatch):
    """A worker spawned later inherits KRAFT_DAEMON_PID/PORT, so it can check
    whatever it finds on a port against the daemon it actually is, rather than
    assume it is stale and kill it (Kraft-f8u3)."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    monkeypatch.setenv("KRAFT_PORT", "9321")
    # setenv, not delenv(raising=False): monkeypatch only records an undo for a
    # key that existed before the call, so delenv on an already-absent var
    # leaves `_serve`'s os.environ writes below to leak into every later test
    # in the process (Kraft-6um8). setenv always has something to restore.
    monkeypatch.setenv("KRAFT_DAEMON_PID", "unset")
    monkeypatch.setenv("KRAFT_DAEMON_PORT", "unset")
    # `_serve` runs a `_SignalLoggingServer`, not `uvicorn.run` -- patching the
    # module function would let this test start a real server and never return.
    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: None)
    cli.admin._serve()
    assert os.environ["KRAFT_DAEMON_PID"] == str(os.getpid())
    assert os.environ["KRAFT_DAEMON_PORT"] == "9321"


def test_serve_records_attached_mode_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    monkeypatch.delenv("KRAFT_DETACHED", raising=False)
    seen = {}
    mode_path = RunDirs(tmp_path / "run").mode

    def fake_run(self, *args, **kwargs):
        seen["mode"] = mode_path.read_text().strip()

    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", fake_run)
    cli.admin._serve()
    assert seen["mode"] == "attached"
    assert not mode_path.exists()  # cleared alongside the pidfile on exit


def test_serve_records_detached_mode_when_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    monkeypatch.setenv("KRAFT_DETACHED", "1")
    seen = {}
    mode_path = RunDirs(tmp_path / "run").mode

    def fake_run(self, *args, **kwargs):
        seen["mode"] = mode_path.read_text().strip()

    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", fake_run)
    cli.admin._serve()
    assert seen["mode"] == "detached"


def test_restart_with_no_server_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(cli.admin, "_service_installed", lambda: False)
    cli.main(["admin", "restart"])
    assert "no server running" in capsys.readouterr().out


def test_restart_brings_a_detached_server_back_detached(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    run_dirs = RunDirs(tmp_path / "run")
    run_dirs.pid.parent.mkdir(parents=True, exist_ok=True)
    run_dirs.pid.write_text("4171")
    run_dirs.mode.write_text("detached")
    alive = [True]

    def fake_kill(pid, sig):
        if sig == 0 and not alive[0]:
            raise ProcessLookupError
        if sig != 0:
            alive[0] = False

    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(cli.admin, "_service_installed", lambda: False)
    started = []
    monkeypatch.setattr(cli.admin, "_start_detached", lambda: started.append(True))
    cli.main(["admin", "restart"])
    assert started == [True]
    assert "stopped (pid 4171)" in capsys.readouterr().out


def test_restart_refuses_to_guess_at_an_attached_server(tmp_path, monkeypatch, capsys):
    """It can't hand a foreground server back to the terminal it was running
    in -- stop it, and say so, rather than silently starting it detached."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    run_dirs = RunDirs(tmp_path / "run")
    run_dirs.pid.parent.mkdir(parents=True, exist_ok=True)
    run_dirs.pid.write_text("4171")
    run_dirs.mode.write_text("attached")
    alive = [True]

    def fake_kill(pid, sig):
        if sig == 0 and not alive[0]:
            raise ProcessLookupError
        if sig != 0:
            alive[0] = False

    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(cli.admin, "_service_installed", lambda: False)
    monkeypatch.setattr(
        cli.admin, "_start_detached", lambda: pytest.fail("started detached anyway")
    )
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "restart"])
    assert caught.value.code == 1
    assert "attached to a terminal" in capsys.readouterr().err


def test_restart_goes_through_the_service_manager_when_one_is_installed(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(cli.admin, "_service_installed", lambda: True)
    called = []
    monkeypatch.setattr(cli.admin, "_restart_service", lambda: called.append(True))
    monkeypatch.setattr(
        cli.admin, "_read_pid", lambda *_a: pytest.fail("looked at the pidfile anyway")
    )
    cli.main(["admin", "restart"])
    assert called == [True]


def test_admin_update_with_restart_flag_restarts(monkeypatch, capsys):
    from kraft import update

    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v0.4.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.3.0")
    monkeypatch.setattr(update, "perform", lambda *a, **k: 0)
    called = []
    monkeypatch.setattr(cli.admin, "_cmd_restart", lambda ns: called.append(True))
    cli.main(["admin", "update", "--restart"])
    assert called == [True]


def test_admin_update_without_restart_flag_just_prints_the_hint(monkeypatch, capsys):
    from kraft import update

    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v0.4.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.3.0")
    monkeypatch.setattr(update, "perform", lambda *a, **k: 0)
    monkeypatch.setattr(
        cli.admin, "_cmd_restart", lambda ns: pytest.fail("restarted without being asked")
    )
    cli.main(["admin", "update"])
    assert "kraft admin restart" in capsys.readouterr().out


def _wait_for(predicate, timeout=10.0, interval=0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_kraft_9oab_sigterm_stops_the_real_server(tmp_path):
    """Kraft-9oab: reproduce against the current build before concluding
    anything (the bead's own last line, and the spec's). A mock cannot tell
    us whether a real process actually exits — this spawns `kraft admin
    start` for real, sends a real SIGTERM, and times the real exit.

    `_isolated_kraft_home` (autouse, conftest.py) already points KRAFT_HOME
    and KRAFT_PORT at an isolated tmp dir and a free ephemeral port; this
    only adds KRAFT_RUN_DIR/KRAFT_TEMPLATES_DIR on top, same as every other
    test in this file.
    """
    run_dir = tmp_path / "run"
    templates = fake_templates_dir(tmp_path, "true")
    proc = subprocess.Popen(
        [sys.executable, "-m", "kraft", "admin", "start"],
        env=child_env({"KRAFT_RUN_DIR": str(run_dir), "KRAFT_TEMPLATES_DIR": str(templates)}),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        pid_path = RunDirs(run_dir).pid
        started = _wait_for(pid_path.is_file, timeout=10.0)
        assert started, f"server never wrote a pidfile:\n{proc.stdout.read()}"
        started_at = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        stopped = _wait_for(lambda: proc.poll() is not None, timeout=14.0)
        elapsed = time.monotonic() - started_at
        assert stopped, (
            f"kraft admin start did not exit within 14s of SIGTERM (Kraft-9oab): "
            f"{proc.stdout.read()}"
        )
        # A clean exit is near-instant (measured 1.2s on 2026-09-12); anything
        # past ~8s means uvicorn's own 10s timeout_graceful_shutdown backstop
        # ended the process instead of a graceful return. Assert the bound
        # instead of printing it, so a regression back to the backstop path
        # fails the test instead of a number nobody reads (Kraft-o8vs).
        assert elapsed < 5.0, (
            f"kraft-9oab: exited {elapsed:.1f}s after SIGTERM -- past the "
            "graceful shutdown path, into uvicorn's backstop"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.parametrize(
    "squatter_host",
    # the wildcard is the exact Kraft-kquf shape: an existing daemon bound
    # *:PORT, and we are about to bind the specific loopback address next to it
    ["127.0.0.1", "0.0.0.0"],
    ids=["same-address", "wildcard-probed-from-loopback"],
)
def test_serve_refuses_when_the_address_already_answers(tmp_path, monkeypatch, squatter_host):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind((squatter_host, 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]
        monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
        monkeypatch.setenv("KRAFT_PORT", str(port))
        with pytest.raises(SystemExit, match="already answering"):
            cli.admin._serve()


def test_serve_probes_127_0_0_1_when_its_own_bind_is_a_wildcard(tmp_path, monkeypatch):
    """The other direction: we are about to bind wildcard ourselves, and
    something already sits on loopback -- connecting to 0.0.0.0 itself is
    not a reliable client target, so the probe has to translate."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    templates_dir = fake_templates_dir(tmp_path, "true")
    # A wildcard bind is refused outright without a password (_bind, unrelated
    # to this test) -- set one so the address check is what actually runs.
    (templates_dir / "access.yaml").write_text("password_hash: x\n")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]
        monkeypatch.setenv("KRAFT_HOST", "0.0.0.0")
        monkeypatch.setenv("KRAFT_PORT", str(port))
        with pytest.raises(SystemExit, match="already answering"):
            cli.admin._serve()


def test_serve_proceeds_when_the_address_is_free(tmp_path, monkeypatch):
    """A free ephemeral port (nothing bound it) must not be refused."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # freed before _serve ever runs
    monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
    monkeypatch.setenv("KRAFT_PORT", str(port))
    started = []
    monkeypatch.setattr(
        cli.admin._SignalLoggingServer,
        "run",
        lambda self, *a, **k: started.append(True),
    )
    cli.admin._serve()
    assert started == [True]


def test_shutdown_logs_the_signal(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    monkeypatch.setenv("KRAFT_LOG_REDIRECTED", "1")  # keep this test's own stderr intact

    def fake_run(self, *a, **k):
        self.handle_exit(signal.SIGTERM, None)

    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", fake_run)
    cli.admin._serve()
    assert "signal SIGTERM" in capsys.readouterr().err


def test_shutdown_logs_an_unhandled_exception(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    monkeypatch.setenv("KRAFT_LOG_REDIRECTED", "1")

    def fake_run(self, *a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", fake_run)
    with pytest.raises(RuntimeError):
        cli.admin._serve()
    err = capsys.readouterr().err
    assert "unhandled exception" in err
    assert "boom" in err
    # the pidfile still comes down even though it crashed
    assert not RunDirs(tmp_path / "run").pid.exists()


def test_a_foreground_start_writes_server_log_too(tmp_path, monkeypatch, capfd):
    """Kraft-mqwg: only --detach used to leave a file.

    `_redirect_output_to_log` dup2s the real process fds 1/2 -- pytest's own
    fd-level capture already has those dup'd to its own buffer, so the
    exercise has to run with that capture suspended (`capfd.disabled()`), or
    it corrupts capture for every test that runs after this one in-process.
    """
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    monkeypatch.delenv("KRAFT_LOG_REDIRECTED", raising=False)

    def fake_run(self, *a, **k):
        self.handle_exit(signal.SIGTERM, None)

    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", fake_run)
    with capfd.disabled():
        cli.admin._serve()
    log_path = RunDirs(tmp_path / "run").logs / "server.log"
    assert "signal SIGTERM" in log_path.read_text()


def test_large_log_is_rotated_before_a_start(tmp_path, monkeypatch, capfd):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    monkeypatch.delenv("KRAFT_LOG_REDIRECTED", raising=False)
    log_path = RunDirs(tmp_path / "run").logs / "server.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("x" * 9_000_000)
    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: None)
    with capfd.disabled():
        cli.admin._serve()
    assert log_path.with_suffix(".log.1").stat().st_size == 9_000_000
    assert log_path.stat().st_size < 9_000_000


def test_health_carries_run_dir_and_pid(app):
    payload = asyncio.run(client.health())
    assert payload["run_dir"] == os.environ["KRAFT_RUN_DIR"]
    assert payload["pid"] == os.getpid()


def test_client_refuses_a_mismatched_run_dir(app, monkeypatch):
    async def wrong_instance(*a, **k):
        return {"run_dir": "/somewhere/else", "status": "ok"}

    monkeypatch.setattr(client.transport, "_get", wrong_instance)
    with pytest.raises(ValueError, match="different instance"):
        asyncio.run(client.health())


def test_reload_exits_1_on_a_chain_that_does_not_resolve(app, capsys):
    """Kraft-n1zp9: the library parses, one chain does not resolve -- reload
    used to print "reloaded 2 template(s)" and exit 0."""
    templates_dir = Path(os.environ["KRAFT_TEMPLATES_DIR"])
    (templates_dir / "chains" / "broken.yaml").write_text(
        "id: broken\nnodes:\n  - {id: n, extends: no_such_node}\n"
    )
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "reload"])
    assert caught.value.code == 1
    out = capsys.readouterr().out
    assert "invalid: chain broken" in out and "no_such_node" in out


def _pre_v1_home(root: Path) -> tuple[Path, dict[str, bytes]]:
    """A realistic pre-V1 templates home under `root`, and its `MACHINE_CONFIG`
    files' original bytes (what `replace_pre_v1_config` must carry across
    untouched). Shaped after `~/.kraft/templates.pre-v1-20260922-192004` on
    this machine (`access.yaml`, `repos.yaml`, `intake.yaml`, `theme.yaml`,
    `steering/`, `policy.yaml`) plus a pre-V1 `harnesses.yaml` (no
    `profiles:` block -- that key did not exist before V1)."""
    home = root / "templates"
    home.mkdir()
    carried = {}

    (home / "harnesses.yaml").write_bytes(
        (_REPO_ROOT / "tests" / "fixtures" / "templates_rc" / "harnesses.yaml").read_bytes()
    )
    assert "profiles" not in yaml.safe_load((home / "harnesses.yaml").read_text())

    (home / "repos.yaml").write_text(
        "repos:\n"
        "- path: /repo\n"
        "  name: demo-repo\n"
        "  default_chain_template: default\n"
        "  test_command: just test\n"
        "  setup_command: uv sync\n"
    )
    (home / "access.yaml").write_text(
        yaml.safe_dump({"password_hash": "not-a-real-hash", "bind": "127.0.0.1:8765"})
    )
    (home / "notify.yaml").write_text(
        yaml.safe_dump({"webhook_url": "https://example.invalid/hook"})
    )
    (home / "theme.yaml").write_text(yaml.safe_dump({"palette": "slate", "mode": "system"}))
    (home / "intake.yaml").write_text(
        yaml.safe_dump({"enabled": False, "interval_s": 300, "repos": []})
    )
    (home / "steering").mkdir()
    (home / "steering" / "note.md").write_text("# never signal a process you did not start\n")
    for name in ("repos.yaml", "access.yaml", "notify.yaml", "theme.yaml", "intake.yaml"):
        carried[name] = (home / name).read_bytes()
    carried["steering/note.md"] = (home / "steering" / "note.md").read_bytes()

    # A 0.x `policy.yaml`: one key V1 still has (`budget`), one it dropped
    # (`legacy_only_cap`, never a V1 `PolicyInput` field).
    (home / "policy.yaml").write_text(
        yaml.safe_dump(
            {
                "default": {"attempts": 3, "wall_clock_s": 3600},
                "budget": {"work_item_usd": 100},
                "legacy_only_cap": 8,
            }
        )
    )
    return home, carried


def test_replace_pre_v1_config_carries_machine_config_and_installs_v1_harnesses(
    tmp_path, monkeypatch
):
    """`replace_pre_v1_config` is the guts of `kraft admin update -y` on a
    pre-V1 home (Kraft-sy1ur / review-r21.md F2). `MACHINE_CONFIG` names the
    `harnesses` *overlay directory*, never `harnesses.yaml` -- carrying the
    operator's copy of that file over the bundled V1 one would leave every
    library task selecting `profile: strong` (`implementer`,
    `repair_verification`, `repair_mr_feedback`, `repair_mr_checks`,
    `strict_judge`) unable to resolve a model, because a pre-V1
    `harnesses.yaml` has no `profiles:` block. That mistake passes every test
    that only re-reads the YAML; this one resolves the swapped-in library the
    way a real launch does."""
    from kraft.cli import admin

    home, carried = _pre_v1_home(tmp_path)
    backup = tmp_path / "templates.pre-v1-backup"
    monkeypatch.setattr(admin, "BUNDLED", _REPO_ROOT)

    admin.replace_pre_v1_config(home, backup)

    # The point of the pin, asserted first so a regression here is what the
    # test actually reports failing on: the default chain resolves, and every
    # task selecting `profile: strong` comes back as claude/sonnet/high --
    # through the same production functions a real launch resolves through
    # (`kraft.adapters.agent.select_profile`/`resolve_profile`), never by
    # re-reading `harnesses.yaml`'s raw keys. If `MACHINE_CONFIG` ever carries
    # the operator's pre-V1 `harnesses.yaml` (no `profiles:`) over the bundled
    # one, `resolve_profile` raises `ProfileUnavailable` here, naming
    # `profile 'strong' is not defined in harnesses.yaml`.
    library = TemplateLibrary.from_yaml_dir(home, skills_dir=None)
    resolved = library.resolve_chain("default")
    agent_tasks = [
        t.task
        for node in resolved.nodes
        for t in node.tasks()
        if isinstance(t.task, AgentTask) and t.task.profile == "strong"
    ]
    assert agent_tasks, "expected the shipped default chain to still select profile: strong"

    harnesses_path = home / "harnesses.yaml"
    harnesses_env = _harness.load(None)
    table = HarnessProfileTable.from_yaml(harnesses_path, harnesses=harnesses_env.valid)
    for task in agent_tasks:
        harness_profile = _agent.select_profile(table.profiles, task.harness, harnesses_path)
        model, effort = _agent.resolve_profile(
            task.profile, harness_profile, table, harnesses_env.valid
        )
        assert (harness_profile.provider, model, effort) == ("claude", "sonnet", "high")

    # Every MACHINE_CONFIG file carried across byte-for-byte.
    for name, original in carried.items():
        assert (home / Path(name)).read_bytes() == original, name

    # harnesses.yaml is the bundled V1 one, not the operator's pre-V1 copy.
    harnesses_data = yaml.safe_load((home / "harnesses.yaml").read_text())
    assert set(harnesses_data["profiles"]) == {"deep", "strong", "fast"}

    # The backup holds the old (pre-V1) home whole.
    assert yaml.safe_load((backup / "harnesses.yaml").read_text()) == yaml.safe_load(
        (_REPO_ROOT / "tests" / "fixtures" / "templates_rc" / "harnesses.yaml").read_text()
    )
    for name, original in carried.items():
        assert (backup / Path(name)).read_bytes() == original, name
    assert (backup / "policy.yaml").is_file()
