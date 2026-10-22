"""The marker sweep and its libsandbox calls (`os_init.sweep`, `session_pids`).
The first tests run anywhere, with libsandbox faked; the e2e ones run real
sandboxes on a Mac."""

import os
import struct
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from kraft.worker import os_init

OS_INIT = Path(os_init.__file__)
PY = [sys.executable, "-I", "-S"]


class _Fn:
    """A callable that, like a ctypes function, takes `argtypes`, and keeps its calls."""

    def __init__(self, fn=lambda *args: 0):
        self.fn, self.calls = fn, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.fn(*args)


@pytest.mark.parametrize(
    ("machine", "sees", "types"),
    [
        ("arm64", (1, b"op", 2, 0, 0, 0, 0, 0, b"/p"), 9),
        ("x86_64", (1, b"op", 2, b"/p"), 4),
    ],
    ids=["arm64-path-ninth", "x86_64-path-fourth"],
)
def test_sandbox_check_declaration(monkeypatch, machine, sees, types):
    fn = _Fn()
    monkeypatch.setattr(os_init.platform, "machine", lambda: machine)
    monkeypatch.setattr(os_init, "_libc", lambda: SimpleNamespace(sandbox_check=fn))
    os_init._sandbox_check()(1, b"op", 2, b"/p")
    assert (fn.calls, len(fn.argtypes)) == ([sees], types)


@pytest.mark.parametrize(
    ("machine", "libc", "want"),
    [
        (
            "riscv64",
            SimpleNamespace(sandbox_check=_Fn()),
            "supports arm64 and x86_64 Macs, not riscv64",
        ),
        ("arm64", SimpleNamespace(), "has no sandbox_check"),
    ],
    ids=["other-machine", "no-symbol"],
)
def test_sandbox_check_refused(monkeypatch, machine, libc, want):
    monkeypatch.setattr(os_init.platform, "machine", lambda: machine)
    monkeypatch.setattr(os_init, "_libc", lambda: libc)
    with pytest.raises(OSError, match=want):
        os_init._sandbox_check()


@pytest.mark.parametrize(
    ("scans", "passes", "killed", "left", "marker_stays"),
    [
        ([[], [], []], 10, [], [], False),
        ([[7, 8], [], [], []], 10, [(7, "KILL"), (8, "KILL")], [], False),
        # one empty scan can fall between a fork loop's generations
        ([[], [7], [], [], []], 10, [(7, "KILL")], [], False),
        ([[7]] * 4, 3, [(7, "KILL")] * 3, [7], True),
    ],
    ids=["nothing-to-do", "kills-then-confirms", "one-quiet-scan-is-not-enough", "stubborn"],
)
def test_sweep_passes(monkeypatch, tmp_path, scans, passes, killed, left, marker_stays):
    marker, control = tmp_path / "markers" / "s", tmp_path / "markers" / "control"
    os_init._touch(str(marker))
    sent, stops = [], []
    monkeypatch.setattr(
        os_init, "session_pids", lambda m, c, stop: stops.append(stop) or scans.pop(0)
    )
    monkeypatch.setattr(
        os_init, "_signal_all", lambda pids, sig: sent.extend((p, sig.name[3:]) for p in pids)
    )
    assert os_init.sweep(str(marker), str(control), passes) == left
    assert (sent, marker.exists(), control.exists()) == (killed, marker_stays, True)
    assert set(stops) == {True}  # each scan stops what it finds, before the next generation forks


def test_session_pids_filters(monkeypatch):
    # pid: (user, sandboxed, may read the marker, may read the control marker); None: gone
    me, uid = os.getpid(), os.geteuid()
    procs = {
        me: (uid, True, True, False),
        101: (uid, True, True, False),
        102: (uid + 1, True, True, False),
        103: None,
        104: (uid, False, True, False),
        105: (uid, True, False, False),
        106: (uid, True, True, True),
    }
    # born after the listing: a fork loop's newest generations, and one too far on to be its
    top = max(procs) + 1
    procs[top] = None  # listed, gone before it was looked at
    late = {top + 3: procs[101], top + 4: procs[101], top + 300: procs[101]}

    def listing(buf, size):
        for i, pid in enumerate(procs if buf is not None else ()):
            buf[i] = pid
        return len(procs)

    def info(pid, flavor, arg, buf, size):
        if (procs | late).get(pid) is None:  # an exited process, a zombie too, gives no info
            return 0
        struct.pack_into("=I", buf, 20, (procs | late)[pid][0])
        return 136

    def check(pid, operation, kind, path):
        _, sandboxed, marker, control = (procs | late)[pid]
        if operation is None:
            return int(sandboxed)
        return 0 if (marker, control)[path == b"control"] else 1

    libc = SimpleNamespace(proc_listallpids=_Fn(listing), proc_pidinfo=_Fn(info))
    stopped = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os_init, "_libc", lambda: libc)
    monkeypatch.setattr(os_init, "_sandbox_check", lambda: check)
    monkeypatch.setattr(os_init, "_signal_all", lambda pids, sig: stopped.extend(pids))
    assert os_init.session_pids("marker", "control") == [101, top + 3, top + 4]
    assert stopped == []
    assert os_init.session_pids("marker", "control", stop=True) == [101, top + 3, top + 4]
    assert stopped == [101, top + 3, top + 4]


# --- on a Mac: the real libsandbox -------------------------------------------------

ESCAPEE = (
    "import os, time\n"
    "if os.fork() == 0:\n"
    "    os.setsid()\n"
    "    if os.fork() == 0:\n"
    "        print(os.getpid(), flush=True)\n"
    "        os.close(1)\n"
    "        time.sleep(120)\n"
    "    os._exit(0)\n"
    "os.wait()\n"
)


def _profile(*readable):
    """A deny-default profile that runs Kraft's interpreter and reads `readable`."""
    roots = ("/usr", "/bin", "/System", "/private/etc", "/dev", "/private/var/db/dyld")
    allow = [f'(subpath "{r}")' for r in (*roots, os.path.realpath(sys.base_prefix))]
    allow += [f'(literal "{r}")' for r in ("/", *map(os.path.realpath, readable))]
    return (
        "(version 1)(deny default)(allow process-exec*)(allow process-fork)(allow sysctl-read)"
        f"(allow mach-lookup)(allow file-read-metadata)(allow file-read* {''.join(allow)})"
    )


@pytest.fixture
def markers(tmp_path):
    d = tmp_path / "markers"
    d.mkdir()
    made = {name: d / name for name in ("a", "b", "control")}
    for path in made.values():
        path.touch()
    return made


@pytest.fixture
def started():
    """The processes a test started inside a sandbox, killed whatever happens."""
    procs = []
    yield procs
    for proc in procs:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass


@pytest.fixture
def escapes(started):
    """Start sandboxed escapees, each in its own session and orphaned."""

    def start(*readable):
        argv = ["sandbox-exec", "-p", _profile(*readable), os.path.realpath(sys.executable)]
        p = subprocess.Popen([*argv, "-I", "-S", "-c", ESCAPEE], stdout=subprocess.PIPE, text=True)
        started.append(psutil.Process(int(p.stdout.readline())))
        p.wait(timeout=30)
        p.stdout.close()
        return started[-1]

    return start


def gone(proc, timeout=10):
    end = time.monotonic() + timeout
    while proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
        if time.monotonic() > end:
            return False
        time.sleep(0.05)
    return True


@pytest.mark.e2e("sandbox-exec")
def test_sweep_kills_escapee(markers, escapes):
    escapee = escapes(markers["a"])
    bystander = subprocess.Popen(["sleep", "60"])  # not sandboxed: never the session's
    try:
        a, control = str(markers["a"]), str(markers["control"])
        assert os_init.session_pids(a, control) == [escapee.pid]
        assert os_init.sweep(a, control) == []
        assert gone(escapee)
        assert (markers["a"].exists(), bystander.poll()) == (False, None)
    finally:
        bystander.kill()
        bystander.wait()


@pytest.mark.e2e("sandbox-exec")
def test_sweep_spares_other_session(markers, escapes):
    # Two sessions of one item (co-tasks) share nothing but the control marker.
    first, second = escapes(markers["a"]), escapes(markers["b"])
    a, b, control = (str(markers[n]) for n in ("a", "b", "control"))
    assert os_init.sweep(a, control) == []
    assert (gone(first), second.is_running(), markers["b"].exists()) == (True, True, True)
    assert os_init.session_pids(b, control) == [second.pid]
    assert os_init.sweep(b, control) == []
    assert gone(second)


@pytest.mark.e2e("sandbox-exec")
def test_session_pids_skips_whoever_reads_the_control_marker(markers, escapes):
    # Apple's own sandboxed apps may read anywhere under /private: they pass every
    # test but this one.
    escapes(markers["a"], markers["control"])
    assert os_init.session_pids(str(markers["a"]), str(markers["control"])) == []


# The command exits at once and leaves a child that keeps the output pipe open.
HOLDER = (
    "import os, time\n"
    "if os.fork() == 0:\n"
    "    os.setsid()\n"
    "    print(os.getpid(), flush=True)\n"
    "    time.sleep(120)\n"
)


@pytest.mark.e2e("sandbox-exec")
def test_launch_sweeps_when_the_command_exits(tmp_path, markers, started):
    # The orphan holds the log pipe open, so waiting for its end would wait out the orphan.
    sandbox = ["sandbox-exec", "-p", _profile(markers["a"]), os.path.realpath(sys.executable)]
    argv = [*PY, str(OS_INIT), "launch", "--keep", "", "--marker", str(markers["a"])]
    argv += ["--control", str(markers["control"]), "--tmpdir", str(tmp_path / "tmp")]
    p = subprocess.Popen(
        [*argv, "--", *sandbox, "-I", "-S", "-c", HOLDER], stdout=subprocess.PIPE, text=True, env={}
    )
    started.append(psutil.Process(int(p.stdout.readline())))
    assert p.wait(timeout=30) == 0
    assert (gone(started[0]), markers["a"].exists(), (tmp_path / "tmp").exists()) == (
        True,
        False,
        False,
    )


# Every generation forks and exits, hopping pid for pid; it ends itself after 8 s, so a
# sweep that fails to catch it leaves nothing behind for long.
HOPPER = (
    "import os, time\n"
    "end = time.time() + 8\n"
    "while time.time() < end:\n"
    "    if os.fork():\n"
    "        os._exit(0)\n"
)


@pytest.mark.e2e("sandbox-exec")
def test_sweep_outruns_a_fork_loop(markers):
    sandbox = ["sandbox-exec", "-p", _profile(markers["a"]), os.path.realpath(sys.executable)]
    p = subprocess.Popen([*sandbox, "-I", "-S", "-c", HOPPER], stdout=subprocess.PIPE)
    time.sleep(0.3)
    began = time.monotonic()
    assert os_init.sweep(str(markers["a"]), str(markers["control"])) == []
    p.stdout.read()  # EOF once no generation holds the pipe
    assert time.monotonic() - began < 3
    p.wait()


def test_end_session_keeps_the_exit_path_on_a_failed_sweep(monkeypatch, tmp_path, capsys):
    def broken(marker, control):
        raise PermissionError("no")

    monkeypatch.setattr(os_init, "sweep", broken)
    (tmp_path / "tmp").mkdir()
    opts = {"--marker": "m", "--control": "c", "--tmpdir": str(tmp_path / "tmp")}
    os_init._end_session(opts)
    assert (capsys.readouterr().err.strip(), (tmp_path / "tmp").exists()) == (
        "kraft: os sandbox: processes survived the sweep: the sweep failed: no",
        False,
    )
