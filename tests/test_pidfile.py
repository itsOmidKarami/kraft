"""`kraft.pidfile`: a pid is this run dir's server only while the server holds
the file's lock, or, for a server from before the lock, while the pid passes
the checks a reused pid fails."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest
from support.pidfile import hold_pidfile

from kraft import pidfile


@pytest.fixture
def started(tmp_path):
    """`started(*argv)`: a process this test owns, killed at teardown."""
    procs = []

    def start(*argv: str) -> subprocess.Popen:
        proc = subprocess.Popen(argv)
        procs.append(proc)
        return proc

    yield start
    for proc in procs:
        proc.kill()
        proc.wait()


def _pidfile_after(tmp_path, proc) -> object:
    """A pidfile naming `proc`, written after it started, as a server writes its own."""
    path = tmp_path / "kraft.pid"
    path.write_text(str(proc.pid))
    os.utime(path, (time.time() + 5, time.time() + 5))
    return path


def test_a_held_pidfile_is_the_running_server(tmp_path):
    held = hold_pidfile(tmp_path / "kraft.pid", 4171)

    assert pidfile.read(tmp_path / "kraft.pid") == pidfile.State(4171, True)
    held.release()
    assert not pidfile.read(tmp_path / "kraft.pid").running


def test_an_unlocked_pidfile_naming_another_program_is_stale(tmp_path, started):
    path = _pidfile_after(tmp_path, started("sleep", "60"))

    state = pidfile.read(path)

    assert not state.running
    assert state.why == "is sleep, not Kraft"


def test_a_pid_reused_after_the_pidfile_was_written_is_stale(tmp_path, started):
    """A process younger than the file cannot have written it, whatever it runs."""
    path = tmp_path / "kraft.pid"
    proc = started(sys.executable, "-c", "import time; time.sleep(60)", "kraft", "admin", "start")
    path.write_text(str(proc.pid))
    os.utime(path, (time.time() - 600, time.time() - 600))

    state = pidfile.read(path)

    assert not state.running
    assert "reused" in state.why


def test_a_server_from_before_the_lock_is_still_found(tmp_path, started):
    """1.5.0rc13 and older write the pid but take no lock, and `kraft admin
    restart` right after an update is talking to exactly that server."""
    script = tmp_path / "bin" / "kraft"  # the console script, as a Python runs it
    script.parent.mkdir()
    script.write_text("import time\ntime.sleep(60)\n")
    proc = started(sys.executable, str(script), "admin", "start")

    assert pidfile.read(_pidfile_after(tmp_path, proc)) == pidfile.State(proc.pid, True)


def test_a_process_that_only_mentions_kraft_is_stale(tmp_path, started):
    """Its argv holds the word, but it runs no Kraft: `kraft admin stop`
    would have signalled it."""
    proc = started(sys.executable, "-c", "import time; time.sleep(60)", "kraft", "admin", "start")

    state = pidfile.read(_pidfile_after(tmp_path, proc))

    assert not state.running
    assert state.why.endswith(", not Kraft")


@pytest.mark.parametrize(
    ("cmdline", "server"),
    [
        (["/home/u/.local/bin/kraft"], True),
        (["/home/u/.local/bin/kraft", "--host", "0.0.0.0"], True),
        (["/venv/bin/python3", "/home/u/.local/bin/kraft", "admin", "start"], True),
        (["/venv/bin/python", "-P", "-m", "kraft", "admin", "start", "--port", "1"], True),
        (["python3.13", "-m", "kraft"], True),
        (
            [
                "/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/"
                "Contents/MacOS/Python",
                "/Users/u/.local/bin/kraft",
                "admin",
                "start",
            ],
            True,
        ),
        (["/usr/local/bin/python3", "-I", "/home/u/.local/bin/kraft"], True),
        (["/usr/bin/python3", "-s", "/home/u/.local/bin/kraft", "admin", "start"], True),
        (["python3", "-X", "dev", "-m", "kraft", "admin", "start"], True),
        (["python3", "-Im", "kraft"], True),
        (["pypy3", "-m", "kraft"], True),
        (["python3", "-c", "import time", "kraft", "admin", "start"], False),
        (["/bin/bash", "-c", "cd /tmp/kraft-docs-review && kraft admin restart -y"], False),
        (["tail", "-f", "/home/u/.kraft/run/kraft.log"], False),
        (["vim", "/home/u/src/kraft"], False),
        (["/venv/bin/python", "-I", "-m", "kraft", "admin", "permission-hook", "cursor"], False),
        (["/home/u/.local/bin/kraft", "view", "list"], False),
    ],
    ids=[
        "bare",
        "bare-with-options",
        "script-admin-start",
        "module-admin-start",
        "dev-module",
        "macos-framework-python",
        "isolated-script",
        "shebang-dash-s",
        "x-option-before-module",
        "combined-flags-module",
        "pypy",
        "a-program-string",
        "a-shell-in-a-kraft-directory",
        "tail-of-its-log",
        "an-editor-on-a-checkout",
        "a-hook",
        "another-verb",
    ],
)
def test_only_the_servers_own_command_line_counts_as_kraft(cmdline, server):
    """Any argv holding the text "kraft" passed, so a stale pidfile naming the
    caller's own shell, started in a `kraft-*` directory, had `kraft admin
    restart` SIGTERM that shell."""
    assert pidfile.runs_the_server(cmdline) is server


@pytest.mark.parametrize(
    "process",
    [
        SimpleNamespace(uids=lambda: SimpleNamespace(real=os.getuid() + 1)),
        SimpleNamespace(uids=lambda: (_ for _ in ()).throw(psutil.AccessDenied(1))),
    ],
    ids=["another-uid", "access-denied"],
)
def test_another_users_process_is_never_this_users_server(tmp_path, monkeypatch, process):
    """pid 1, say: a server this user started runs as this user."""
    monkeypatch.setattr(pidfile.psutil, "Process", lambda pid: process)
    path = tmp_path / "kraft.pid"
    path.write_text("1")

    assert pidfile.read(path) == pidfile.State(1, False, "belongs to another user")


def test_a_second_hold_fails_while_the_first_is_held(tmp_path):
    path = tmp_path / "kraft.pid"
    first = pidfile.hold(path)

    assert first is not None and path.read_text() == str(os.getpid())
    assert pidfile.hold(path, attempts=2) is None
    os.close(first)
    second = pidfile.hold(path)
    assert second is not None
    os.close(second)


def test_clear_stale_leaves_a_held_pidfile(tmp_path):
    held = hold_pidfile(tmp_path / "kraft.pid", 4171)

    assert not pidfile.clear_stale(held.path, 4171)
    held.release()
    assert pidfile.clear_stale(held.path, 4171)
    assert not held.path.exists()
