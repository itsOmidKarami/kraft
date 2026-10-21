"""The os sandbox's launcher and init, run as scripts with a stand-in for bwrap."""

import os
import resource
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

OS_INIT = Path(__file__).parents[2] / "src/kraft/worker/os_init.py"
PY = [sys.executable, "-I", "-S"]
HINT = (
    "kraft: a command was not found inside the os sandbox; "
    "add its directory to the sandbox's expose list"
)

# Stands in for bwrap: checks `--seccomp <fd>` follows argv[0] and that fd is
# open, then runs the rest. It rebuilds the environment from /proc so Python's
# own locale coercion cannot add a name.
STAND_IN = """#!{python}
import os, sys
assert sys.argv[1] == "--seccomp", sys.argv
os.fstat(int(sys.argv[2]))
raw = open("/proc/self/environ", "rb").read().split(b"\\0")
env = dict(kv.split(b"=", 1) for kv in raw if kv)
os.execve(sys.argv[3], sys.argv[3:], env)
"""


@pytest.fixture
def bwrap(tmp_path):
    path = tmp_path / "bwrap"
    path.write_text(STAND_IN.format(python=os.path.realpath(sys.executable)))
    path.chmod(0o755)
    return path


@pytest.fixture
def filt(tmp_path):
    path = tmp_path / "filter.bpf"
    path.write_bytes(b"\0" * 8)
    return path


def py(code, *args):
    return [*PY, "-c", code, *args]


def launcher(mode, bwrap, filt, inner, *, keep="", started=None):
    argv = [*PY, str(OS_INIT), mode, "--keep", keep, "--seccomp-file", str(filt)]
    if started:
        argv += ["--started", str(started)]
    return [*argv, "--", str(bwrap), *inner]


def init_argv(*cmd, sock=None, port=None):
    relay = ["--sock", str(sock), "--port", str(port)] if sock else []
    return [*PY, str(OS_INIT), "init", *relay, "--", *cmd]


def run(argv, **kw):
    kw.setdefault("timeout", 30)
    return subprocess.run(argv, capture_output=True, text=True, env=kw.pop("env", {}), **kw)


def wait_for(path, timeout=10):
    end = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < end, f"{path} never appeared"
        time.sleep(0.02)


@pytest.mark.parametrize(
    ("under_init", "want_fd"), [(False, True), (True, False)], ids=["launcher", "init-drops-fd"]
)
def test_launch_env_filtered(bwrap, filt, under_init, want_fd):
    code = (
        "raw = open('/proc/self/environ', 'rb').read().split(b'\\0')\n"
        "print(sorted(kv.split(b'=')[0].decode() for kv in raw if kv))"
    )
    env = {"FOO": "1", "BAR": "2", "SECRET": "x"}
    inner = init_argv(*py(code)) if under_init else py(code)
    out = run(launcher("launch", bwrap, filt, inner, keep="FOO,MISSING"), env=env).stdout
    assert ("'FOO'" in out, "BAR" in out or "SECRET" in out) == (True, False)
    assert ("OS_INIT_STARTED_FD" in out) is want_fd


@pytest.mark.parametrize(
    ("inner", "wrote"),
    [
        (py("import os; os.write(int(os.environ['OS_INIT_STARTED_FD']), b'x')"), True),
        (init_argv("/nonexistent/cmd"), False),
        (init_argv("true"), True),
    ],
    ids=["child-writes", "exec-fails", "init-starts"],
)
def test_launch_started(bwrap, filt, tmp_path, inner, wrote):
    started = tmp_path / "started"
    r = run(launcher("launch", bwrap, filt, inner, started=started))
    assert started.exists() is wrote
    if wrote:
        assert started.read_text() == "started\n"
    else:
        assert r.returncode == 127


@pytest.mark.parametrize(
    "sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP], ids=["int", "term", "hup"]
)
def test_launch_outlives_signal(bwrap, filt, tmp_path, sig):
    ready = tmp_path / "ready"
    code = (
        "import signal, time, pathlib, sys\n"
        f"signal.signal({int(sig)}, lambda *a: (time.sleep(0.5), sys.exit(7)))\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(30)\n"
    )
    p = subprocess.Popen(launcher("launch", bwrap, filt, py(code)), start_new_session=True, env={})
    wait_for(ready)
    os.killpg(p.pid, sig)
    assert p.wait(timeout=20) == 7


COPIES = (
    "import os, sys\n"
    "print('one', flush=True); print('two', file=sys.stderr, flush=True)\n"
    "print(os.readlink('/proc/self/fd/1').startswith('pipe:'), flush=True)\n"
    "try:\n    open('/proc/self/fd/1', 'a').write('X')\nexcept OSError: pass\n"
)
LOG_FAILS = (
    "import pathlib; print('x' * 200000, flush=True); pathlib.Path(DONE).touch()\n"
    "raise SystemExit(5)"
)
KILLED = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"


@pytest.mark.parametrize(
    ("code", "sink", "want_rc", "want"),
    [
        # X went into the pipe, so it is only appended
        (COPIES, "file", 0, "one\ntwo\nTrue\nX"),
        (LOG_FAILS, "closed-pipe", 5, True),
        (KILLED, "file", 137, ""),
    ],
    ids=["copies", "log-write-fails", "bwrap-killed-by-signal"],
)
def test_launch_copies_output(bwrap, filt, tmp_path, code, sink, want_rc, want):
    log, done = tmp_path / "log", tmp_path / "done"
    argv = launcher("launch", bwrap, filt, py(code.replace("DONE", repr(str(done)))))
    if sink == "file":
        with log.open("wb") as f:
            r = subprocess.run(argv, stdout=f, env={}, timeout=30)
        got = log.read_text()
    else:
        r_fd, w_fd = os.pipe()
        os.close(r_fd)
        try:
            r = subprocess.run(argv, stdout=w_fd, env={}, timeout=30)
        finally:
            os.close(w_fd)
        got = done.exists()
    assert (r.returncode, got) == (want_rc, want)


def test_oneshot_passes_stdio_through(bwrap, filt):
    code = "import os, sys; print(sys.stdin.read().upper(), 'OS_INIT_STARTED_FD' in os.environ)"
    r = run(launcher("oneshot", bwrap, filt, py(code)), input="hi")
    assert r.stdout.strip() == "HI False"


def test_launch_keyring(bwrap, filt):
    # The driver puts a key in its own session keyring, then becomes the launcher.
    driver = (
        "import ctypes, os, sys\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "nr = {'x86_64': 250, 'aarch64': 219}[os.uname().machine]\n"
        "assert libc.syscall(nr, 1, None) != -1\n"  # own session keyring
        "add = {'x86_64': 248, 'aarch64': 217}[os.uname().machine]\n"
        "assert libc.syscall(add, b'user', b'kraft-test', b'v', 1, -3) > 0\n"
        "os.execv(sys.argv[1], sys.argv[1:])\n"
    )
    probe = (
        "import ctypes, os\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "nr = {'x86_64': 250, 'aarch64': 219}[os.uname().machine]\n"
        "print(libc.syscall(nr, 10, -3, b'user', b'kraft-test', 0))\n"  # KEYCTL_SEARCH
    )
    probe_alone = run(py(driver.replace("os.execv(sys.argv[1], sys.argv[1:])", "") + probe))
    assert int(probe_alone.stdout) > 0, probe_alone  # the key is findable without the launcher
    r = run([*PY, "-c", driver, *launcher("launch", bwrap, filt, py(probe))])
    assert int(r.stdout) == -1, r


@pytest.mark.parametrize("ignore_term", [False, True], ids=["plain", "term-ignored-inherited"])
@pytest.mark.parametrize(
    ("code", "want"),
    [("raise SystemExit(3)", 3), ("import os, signal; os.kill(os.getpid(), signal.SIGTERM)", 143)],
    ids=["exit-3", "killed-term"],
)
def test_init_exit_codes(code, want, ignore_term):
    pre = (lambda: signal.signal(signal.SIGTERM, signal.SIG_IGN)) if ignore_term else None
    r = subprocess.run(init_argv(*py(code)), preexec_fn=pre, env={}, timeout=30)
    assert r.returncode == want


@pytest.mark.parametrize(
    ("detached", "group"),
    [(True, False), (False, True)],
    ids=["detached-child-forwarded", "same-group-child-not-doubled"],
)
def test_init_forwards_signal(tmp_path, detached, group):
    # A detached child gets the signal forwarded; one in init's group already
    # got it, and a second would cut short the cost write a pause protects.
    ready, count = tmp_path / "ready", tmp_path / "count"
    code = (
        "import os, pathlib, signal, time\n"
        f"{'os.setsid()' if detached else ''}\n"
        "n = []\n"
        "signal.signal(signal.SIGINT, lambda *_: n.append(1))\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(1.5)\n"
        f"pathlib.Path({str(count)!r}).write_text(str(len(n)))"
    )
    p = subprocess.Popen(init_argv(*py(code)), env={}, start_new_session=True)
    wait_for(ready)
    if group:
        os.killpg(p.pid, signal.SIGINT)
    else:
        p.send_signal(signal.SIGINT)
    assert p.wait(timeout=20) == 0
    assert count.read_text() == "1"


@pytest.mark.parametrize(
    ("script", "limit"),
    [
        # The subshell exits at once, orphaning the grandchild onto the init (a subreaper).
        ("(sh -c 'echo $$ > {pid}; exit 0' &); sleep 0.5; test ! -e /proc/$(cat {pid})", 20),
        # Init ends with CMD; it does not wait out an orphan that is still running.
        ("(setsid sleep 30 >/dev/null 2>&1 &); exit 0", 10),
    ],
    ids=["reaps-zombie", "exits-with-cmd-not-orphans"],
)
def test_init_reaps(tmp_path, script, limit):
    start = time.monotonic()
    r = run(init_argv("/bin/sh", "-c", script.format(pid=tmp_path / "pid")))
    assert r.returncode == 0, "the orphan stayed a zombie"
    assert time.monotonic() - start < limit


@pytest.mark.parametrize(
    ("env", "code", "want"),
    [
        ({"TMPDIR": "/elsewhere"}, "import os; print(os.environ['TMPDIR'])", "/tmp"),
        (
            {"HOME": "HOMEDIR/made/here"},
            "import os; print(os.path.isdir(os.environ['HOME']))",
            "True",
        ),
    ],
    ids=["tmpdir-is-tmp", "home-created"],
)
def test_init_environment(tmp_path, env, code, want):
    env = {k: v.replace("HOMEDIR", str(tmp_path)) for k, v in env.items()}
    assert run(init_argv(*py(code)), env=env).stdout.strip() == want


@pytest.mark.parametrize(
    ("cmd", "code", "last"),
    [
        (["/nonexistent/cmd"], 127, HINT),
        ([*PY, "-c", "raise SystemExit(127)"], 127, HINT),
        (["/etc/passwd"], 126, "kraft: /etc/passwd is not executable"),
    ],
    ids=["missing", "exits-127", "not-executable"],
)
def test_init_exec_failure(cmd, code, last):
    r = run(init_argv(*cmd))
    assert (r.returncode, r.stderr.splitlines()[-1]) == (code, last)


def test_launch_refuses_unknown_arch(bwrap, filt):
    # Run the launcher with platform.machine() faked, so no keyring join is possible.
    driver = (
        "import platform, runpy, sys\n"
        "platform.machine = lambda: 'riscv64'\n"
        f"runpy.run_path({str(OS_INIT)!r}, run_name='__main__')"
    )
    argv = launcher("launch", bwrap, filt, py("print('ran')"))
    r = run([*PY, "-c", driver, *argv[4:]])
    assert (r.returncode, r.stdout, r.stderr.strip()) == (
        125,
        "",
        "kraft: no keyctl number for riscv64; refusing to start the sandbox",
    )


def _unix_server(path, conns=1):
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(conns)

    def handle(conn):
        try:
            conn.sendall(b"pong:" + conn.recv(16))
        except OSError:
            pass
        finally:
            conn.close()

    def serve():
        for _ in range(conns):
            conn, _ = srv.accept()
            threading.Thread(target=handle, args=(conn,), daemon=True).start()
        srv.close()

    threading.Thread(target=serve, daemon=True).start()


def _ask(port):
    end = time.monotonic() + 10
    while True:
        try:
            c = socket.create_connection(("127.0.0.1", port), timeout=5)
            break
        except OSError:
            assert time.monotonic() < end, "relay never listened"
            time.sleep(0.05)
    with c:
        c.sendall(b"ping")
        buf = b""
        while data := c.recv(100):
            buf += data
    return buf


def test_relay_reconnects_per_connection(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    sock = tmp_path / "s.sock"
    _unix_server(sock)
    p = subprocess.Popen(
        init_argv(*py("import time; time.sleep(30)"), sock=sock, port=port),
        env={},
        start_new_session=True,
    )
    try:
        assert _ask(port) == b"pong:ping"
        sock.unlink()  # the daemon restarted and re-created its socket
        _unix_server(sock)
        assert _ask(port) == b"pong:ping"
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_relay_survives_descriptor_exhaustion(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    sock = tmp_path / "s.sock"
    _unix_server(sock, conns=100)

    def small_limit():
        resource.setrlimit(resource.RLIMIT_NOFILE, (40, 40))

    p = subprocess.Popen(
        init_argv(*py("import time; time.sleep(30)"), sock=sock, port=port),
        env={},
        start_new_session=True,
        preexec_fn=small_limit,
    )
    try:
        assert _ask(port) == b"pong:ping"
        held = [socket.create_connection(("127.0.0.1", port)) for _ in range(30)]  # 60 > 40
        time.sleep(0.5)  # the relay hits EMFILE accepting these
        for c in held:
            c.close()
        assert _ask(port) == b"pong:ping"
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
