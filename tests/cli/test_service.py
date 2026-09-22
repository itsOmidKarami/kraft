"""kraft admin install-service / uninstall-service (spec §1, Kraft-u82w)."""

from __future__ import annotations

import argparse
import os
import pathlib
import plistlib
import shutil
import socket
import subprocess
import sys
import time
import uuid

import pytest
from support.harness import fake_templates_dir

from kraft import cli
from kraft.paths import RunDirs


def _real_user_manager_home() -> str | None:
    """The systemd --user manager's own $HOME, or None when there's no
    reachable user manager at all.

    Not the calling process's $HOME (which a test freely monkeypatches): the
    manager's own, queried straight from it, so a test can write a unit into
    the search path *that manager* actually reads -- the in-tree case a real
    `install-service` hits, as opposed to the out-of-tree case a sandboxed
    fake $HOME produces (Kraft-1zvs3).
    """
    if not shutil.which("systemctl"):
        return None
    try:
        done = subprocess.run(
            ["systemctl", "--user", "show-environment"], capture_output=True, text=True
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    for line in done.stdout.splitlines():
        if line.startswith("HOME="):
            return line[len("HOME=") :]
    return None


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
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
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
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    home = tmp_path / 'a&b "c"'
    monkeypatch.setenv("KRAFT_HOME", str(home))

    plist = plistlib.loads(cli.admin._write_launchd_plist("/usr/local/bin/kraft").read_bytes())
    assert plist["EnvironmentVariables"]["KRAFT_HOME"] == str(home)
    assert plist["ProgramArguments"] == ["/usr/local/bin/kraft", "admin", "start"]

    unit = cli.admin._write_systemd_unit("/usr/local/bin/kraft").read_text()
    assert f'Environment="KRAFT_HOME={str(home).replace(chr(34), chr(92) + chr(34))}"' in unit


def test_install_service_enables_systemd_unit_by_absolute_path(tmp_path, monkeypatch):
    """`enable` must be given the unit's absolute path, not its bare name.

    A bare name only resolves against the *running manager's own* search
    path, which is fixed to whatever $HOME the manager itself started with
    -- not this process's, however it's monkeypatched. On a machine where
    those differ (any sandboxed test, and confirmed to always be the case
    for GitHub Actions' persistent per-job user session, Kraft-1zvs3), a
    bare-name `enable --now` after `daemon-reload` fails "Unit file ... does
    not exist" every time, not intermittently. The absolute path sidesteps
    that: `enable` then links the file into the manager's real unit
    directory itself, the documented way to adopt an out-of-tree unit.
    """
    monkeypatch.setattr(cli.admin, "_read_pid", lambda *_: None)
    monkeypatch.setattr(cli.admin, "_kraft_executable", lambda: "/usr/local/bin/kraft")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )

    cli.admin._cmd_install_service(argparse.Namespace())

    unit_path = cli.admin._systemd_unit_path()
    enable_calls = [c for c in calls if c[:3] == ["systemctl", "--user", "enable"]]
    assert enable_calls == [["systemctl", "--user", "enable", "--now", str(unit_path)]]


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
    # systemctl --user resolves unit files under $XDG_CONFIG_HOME when set,
    # which would otherwise point outside the fake HOME above and make
    # `enable --now` unable to find the unit this test just wrote.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "kraft-home"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
    run_dir = tmp_path / "kraft-home" / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
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


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="systemd --user is Linux-only")
@pytest.mark.skipif(not shutil.which("kraft"), reason="no installed `kraft` on PATH to supervise")
@pytest.mark.skipif(not shutil.which("systemctl"), reason="no systemctl on this box")
@pytest.mark.skipif(
    _real_user_manager_home() is None,
    reason="no reachable systemd --user manager on this box",
)
def test_install_service_enable_works_inside_the_managers_own_search_path(tmp_path, monkeypatch):
    """`test_install_and_uninstall_service_use_real_systemd_user` above proves
    absolute-path `enable --now` for a unit written OUTSIDE the manager's own
    search path -- a sandboxed fake $HOME. That is not the production case: a
    real `install-service` writes into the manager's *own* search path
    (`~/.config/systemd/user/kraft.service`), because a real user's $HOME
    already agrees with their own manager's. Prove that case too, using the
    manager's real $HOME and a unique unit name (so this can't collide with
    a real `kraft.service` on the same box, nor with another xdist worker
    running this same test), and prove a reinstall -- uninstall then install
    again with different content -- is what actually ends up running, not
    just what daemon-reload/enable claim to have relinked.
    """
    real_home = _real_user_manager_home()
    unique_unit = f"kraft-selftest-{uuid.uuid4().hex}.service"
    monkeypatch.setenv("HOME", real_home)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(cli.admin, "_SYSTEMD_UNIT", unique_unit)
    unit_path = cli.admin._systemd_unit_path()

    def install(run_dir: pathlib.Path) -> None:
        monkeypatch.setenv("KRAFT_HOME", str(run_dir.parent))
        monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
        monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "true")))
        monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        monkeypatch.setenv("KRAFT_PORT", str(port))
        cli.main(["admin", "install-service"])

    try:
        run_dir_1 = tmp_path / "kraft-home-1" / "run"
        install(run_dir_1)
        assert _wait_for(lambda: cli.admin._read_pid(RunDirs(run_dir_1).pid) is not None), (
            "systemd never brought the first install up inside its own search path"
        )
        assert unit_path.is_file()
        # Ask the manager itself, by the bare unit name: this only succeeds
        # for a unit truly inside its search path -- the whole thing the
        # sandboxed-$HOME test above cannot exercise.
        loaded = subprocess.run(
            ["systemctl", "--user", "show", "-p", "LoadState", "--value", unique_unit],
            capture_output=True,
            text=True,
        )
        assert loaded.stdout.strip() == "loaded"

        # Reinstall: uninstall (disable --now, the only production-supported
        # way to stop a Restart=always unit for good) then install again with
        # different content (a different KRAFT_RUN_DIR). No daemon-reload is
        # ever called explicitly in this code path any more -- if `enable`'s
        # implicit reload didn't actually happen, this second install would
        # either fail outright or silently keep serving the first unit's
        # cached definition, and the first run dir's pid would still be the
        # one that's alive.
        cli.main(["admin", "uninstall-service"])
        assert _wait_for(lambda: cli.admin._read_pid(RunDirs(run_dir_1).pid) is None), (
            "uninstall-service did not stop the first instance"
        )

        run_dir_2 = tmp_path / "kraft-home-2" / "run"
        install(run_dir_2)
        assert _wait_for(lambda: cli.admin._read_pid(RunDirs(run_dir_2).pid) is not None), (
            "systemd never brought the reinstall up with the new content"
        )
        # The proof that matters: the *new* content is what's running, not a
        # cached copy of the old one still bound to the old run dir.
        assert cli.admin._read_pid(RunDirs(run_dir_1).pid) is None
    finally:
        subprocess.run(["systemctl", "--user", "disable", "--now", unique_unit], check=False)
        if unit_path.is_file():
            unit_path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
