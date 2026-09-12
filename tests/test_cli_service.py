"""kraft admin install-service / uninstall-service (spec §1, Kraft-u82w)."""

from __future__ import annotations

import os
import pathlib
import plistlib
import shutil
import subprocess
import sys
import time

import pytest
from support.harness import fake_templates_dir

from kraft import cli
from kraft.paths import RunDirs


def _operator_has_a_real_service() -> bool:
    """True when this machine already has `com.kraft.daemon` / `kraft.service`
    installed for real.

    The label and the unit name are global (one daemon, one name), so the
    real-service tests below cannot be sandboxed away from an operator's own
    registration: their `load -w` collides with it, and their teardown
    `unload -w` would unload *and persistently disable* the live daemon.
    Checked against the real `$HOME`, before any monkeypatching.
    """
    if sys.platform == "darwin":
        if (pathlib.Path.home() / "Library/LaunchAgents/com.kraft.daemon.plist").is_file():
            return True
        return (
            subprocess.run(
                ["launchctl", "list", "com.kraft.daemon"], capture_output=True
            ).returncode
            == 0
        )
    if (pathlib.Path.home() / ".config/systemd/user/kraft.service").is_file():
        return True
    if not shutil.which("systemctl"):
        return False
    return (
        subprocess.run(
            ["systemctl", "--user", "is-enabled", "kraft.service"],
            capture_output=True,
        ).returncode
        == 0
    )


def _wait_for(predicate, timeout=15.0, interval=0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_install_service_refuses_while_a_daemon_is_running(tmp_path, monkeypatch, capsys):
    """Installing while a daemon runs must not produce two daemons on one
    run dir -- and must not touch the filesystem at all, on any platform."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("touched the service manager")
    )
    with pytest.raises(SystemExit, match="already running"):
        cli.main(["admin", "install-service"])
    assert not cli.admin._launchd_plist_path().exists()
    assert not cli.admin._systemd_unit_path().exists()


def test_launchd_plist_content(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "kraft-home"))
    path = cli.admin._write_launchd_plist("/usr/local/bin/kraft")
    text = path.read_text()
    assert "<string>/usr/local/bin/kraft</string>" in text
    assert "<string>admin</string>" in text
    assert "<string>start</string>" in text
    assert "<key>KeepAlive</key>" in text and "<true/>" in text
    assert f"<string>{tmp_path / 'kraft-home'}</string>" in text


def test_systemd_unit_content(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "kraft-home"))
    path = cli.admin._write_systemd_unit("/usr/local/bin/kraft")
    text = path.read_text()
    assert "ExecStart=/usr/local/bin/kraft admin start" in text
    assert "Restart=always" in text
    assert f'Environment="KRAFT_HOME={tmp_path / "kraft-home"}"' in text


def test_service_files_survive_a_special_character_in_the_environment(tmp_path, monkeypatch):
    """An `&` in PATH or KRAFT_HOME used to produce a plist launchd cannot
    parse, and a `"` a systemd unit that fails to load."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    home = tmp_path / 'a&b "c"'
    monkeypatch.setenv("KRAFT_HOME", str(home))

    plist = plistlib.loads(cli.admin._write_launchd_plist("/usr/local/bin/kraft").read_bytes())
    assert plist["EnvironmentVariables"]["KRAFT_HOME"] == str(home)
    assert plist["ProgramArguments"] == ["/usr/local/bin/kraft", "admin", "start"]

    unit = cli.admin._write_systemd_unit("/usr/local/bin/kraft").read_text()
    assert f'Environment="KRAFT_HOME={str(home).replace(chr(34), chr(92) + chr(34))}"' in unit


@pytest.mark.skipif(sys.platform != "darwin", reason="launchd is macOS-only")
@pytest.mark.skipif(not shutil.which("kraft"), reason="no installed `kraft` on PATH to supervise")
@pytest.mark.skipif(
    _operator_has_a_real_service(),
    reason="this machine has a real kraft service installed; the test would unload it",
)
def test_install_and_uninstall_service_use_the_real_launchd(tmp_path, monkeypatch):
    """Not faked: a real `launchctl load`, a real supervised `kraft admin
    start`, a real `launchctl unload`. Fully sandboxed -- the plist's own
    EnvironmentVariables point the supervised process at this test's
    isolated KRAFT_HOME/KRAFT_PORT, baked in by `_instance_env()` reading
    the test's own monkeypatched environment at write time.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "kraft-home"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    run_dir = tmp_path / "kraft-home" / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
    monkeypatch.setenv("KRAFT_PORT", "0")  # replaced below with a real free one
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("KRAFT_PORT", str(port))

    try:
        cli.main(["admin", "install-service"])
        pid_path = RunDirs(run_dir).pid
        assert _wait_for(lambda: cli.admin._read_pid(pid_path) is not None), (
            "launchd never brought the daemon up"
        )
    finally:
        cli.main(["admin", "uninstall-service"])
        assert not cli.admin._launchd_plist_path().exists()
        stopped = _wait_for(lambda: cli.admin._read_pid(RunDirs(run_dir).pid) is None)
        assert stopped, "uninstall-service did not stop the supervised daemon"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="systemd --user is Linux-only")
@pytest.mark.skipif(not shutil.which("kraft"), reason="no installed `kraft` on PATH to supervise")
@pytest.mark.skipif(not shutil.which("systemctl"), reason="no systemctl on this box")
@pytest.mark.skipif(
    _operator_has_a_real_service(),
    reason="this machine has a real kraft service installed; the test would unload it",
)
def test_install_and_uninstall_service_use_real_systemd_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "kraft-home"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    run_dir = tmp_path / "kraft-home" / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("KRAFT_PORT", str(port))

    try:
        cli.main(["admin", "install-service"])
        pid_path = RunDirs(run_dir).pid
        assert _wait_for(lambda: cli.admin._read_pid(pid_path) is not None), (
            "systemd never brought the daemon up"
        )
    finally:
        cli.main(["admin", "uninstall-service"])
        assert not cli.admin._systemd_unit_path().exists()
        stopped = _wait_for(lambda: cli.admin._read_pid(RunDirs(run_dir).pid) is None)
        assert stopped, "uninstall-service did not stop the supervised daemon"
