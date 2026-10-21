"""The os sandbox's launcher and init, run as scripts with a stand-in for bwrap."""

import os
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


def test_launch_env_filtered(bwrap, filt):
    code = (
        "raw = open('/proc/self/environ', 'rb').read().split(b'\\0')\n"
        "print(sorted(kv.split(b'=')[0].decode() for kv in raw if kv))"
    )
    env = {"FOO": "1", "BAR": "2", "SECRET": "x"}
    r = run(launcher("launch", bwrap, filt, py(code), keep="FOO,MISSING"), env=env)
    assert r.stdout.strip() == "['FOO', 'OS_INIT_STARTED_FD']"


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
    inner = [*inner[:0], *inner] if inner[0] != "true" else inner
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


def test_launch_copies_output(bwrap, filt, tmp_path):
    log = tmp_path / "log"
    code = (
        "import os, sys\n"
        "print('one', flush=True); print('two', file=sys.stderr, flush=True)\n"
        "print(os.readlink('/proc/self/fd/1').startswith('pipe:'), flush=True)\n"
        "try:\n    open('/proc/self/fd/1', 'a').write('X')\nexcept OSError: pass\n"
    )
    with log.open("wb") as f:
        r = subprocess.run(launcher("launch", bwrap, filt, py(code)), stdout=f, env={}, timeout=30)
    assert r.returncode == 0
    assert log.read_text() == "one\ntwo\nTrue\nX"  # X went into the pipe, so it is only appended


def test_launch_log_write_fails(bwrap, filt, tmp_path):
    done = tmp_path / "done"
    code = (
        f"import pathlib; print('x' * 200000, flush=True); pathlib.Path({str(done)!r}).touch()\n"
        "raise SystemExit(5)"
    )
    r_fd, w_fd = os.pipe()
    os.close(r_fd)
    try:
        r = subprocess.run(
            launcher("launch", bwrap, filt, py(code)), stdout=w_fd, env={}, timeout=30
        )
    finally:
        os.close(w_fd)
    assert (r.returncode, done.exists()) == (5, True)


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


def test_init_forwards_signal_to_detached_child(tmp_path):
    ready = tmp_path / "ready"
    code = (
        f"import os, pathlib, time; os.setsid(); pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(30)"
    )
    p = subprocess.Popen(init_argv(*py(code)), env={})
    wait_for(ready)
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=20) == 143


def test_init_reaps(tmp_path):
    pidfile = tmp_path / "pid"
    inner = f"echo $$ > {pidfile}; exit 0"
    # The subshell exits at once, orphaning the grandchild onto the init (a subreaper).
    script = f"(sh -c '{inner}' &); sleep 0.5; test ! -e /proc/$(cat {pidfile})"
    start = time.monotonic()
    r = run(init_argv("/bin/sh", "-c", script))
    assert r.returncode == 0, "the orphan stayed a zombie"
    assert time.monotonic() - start < 20


@pytest.mark.parametrize(
    "cmd",
    [["/nonexistent/cmd"], [*PY, "-c", "raise SystemExit(127)"]],
    ids=["missing", "exits-127"],
)
def test_init_not_found_hint(cmd):
    r = run(init_argv(*cmd))
    assert (r.returncode, r.stderr.splitlines()[-1]) == (127, HINT)


def _unix_server(path):
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.sendall(b"pong:" + conn.recv(16))
        conn.close()
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
