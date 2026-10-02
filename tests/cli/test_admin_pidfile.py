"""`kraft admin stop`, `restart` and `start` against a pidfile that is not, or
is no longer, this run dir's server: they never signal it, and they clear it."""

from __future__ import annotations

import os
import signal
import subprocess
import time

import pytest
from support.harness import fake_templates_dir, isolated_bd
from support.server import running_server

from kraft import cli
from kraft.paths import RunDirs


@pytest.fixture
def decoy(tmp_path, monkeypatch):
    """A `sleep` this test started, named by the run dir's pidfile the way a
    reused pid would be after a server died without removing it."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    proc = subprocess.Popen(["sleep", "60"])
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid))
    os.utime(pid_path, (time.time() + 5, time.time() + 5))
    yield proc
    proc.kill()
    proc.wait()


@pytest.mark.parametrize("verb", ["stop", "restart"])
def test_a_pidfile_naming_another_program_is_cleared_not_signalled(
    decoy, tmp_path, monkeypatch, capsys, verb
):
    monkeypatch.setattr(cli.admin, "_service_installed", lambda: False)
    monkeypatch.setattr(cli.admin, "_confirm_running_agents", lambda *a, **k: None)

    cli.main(["admin", verb, "-y"] if verb == "restart" else ["admin", verb])

    out = capsys.readouterr()
    assert decoy.poll() is None, "signalled a process that is not Kraft"
    assert "no server running" in out.out
    assert f"removed a stale pidfile: pid {decoy.pid} is sleep, not Kraft" in out.err
    assert not RunDirs(tmp_path / "run").pid.exists()


def test_start_is_not_refused_by_a_pidfile_naming_another_program(decoy, tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    started = []
    monkeypatch.setattr(
        cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: started.append(True)
    )

    cli.admin._serve()

    assert started == [True]
    assert decoy.poll() is None


@pytest.mark.slow
def test_a_sigterm_leaves_no_pidfile(tmp_path):
    """uvicorn re-raises the SIGTERM it drained on, so `_serve`'s `finally`
    never ran on a plain `kill`, a service stop or a reboot, and the next
    `stop` signalled whatever process had the pid by then. The server still
    dies by the signal, which a service manager reads as a clean stop."""
    run_dir = tmp_path / "run"
    templates = fake_templates_dir(tmp_path, "true")
    with running_server(
        run_dir=run_dir, templates_dir=templates, bd_cwd=isolated_bd(tmp_path)
    ) as srv:
        assert RunDirs(run_dir).pid.read_text() == str(srv.proc.pid)
        srv.proc.send_signal(signal.SIGTERM)
        srv.proc.wait(timeout=30)

    assert srv.proc.returncode == -signal.SIGTERM
    assert not RunDirs(run_dir).pid.exists()
    assert not RunDirs(run_dir).mode.exists()
