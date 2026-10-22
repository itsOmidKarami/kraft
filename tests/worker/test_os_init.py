"""The os sandbox's launcher and init, run as scripts with a stand-in for bwrap
(Linux) or sandbox-exec (macOS). The macOS branches also run on Linux, under a
driver that claims `sys.platform == "darwin"` and stubs what needs a real Mac."""

import json
import os
import resource
import shlex
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
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="a Linux-only fence")

# Stands in for bwrap: checks `--seccomp <fd>` follows argv[0] and that fd is
# open, then runs the rest. It rebuilds the environment from /proc so Python's
# own locale coercion cannot add a name.
BWRAP = """#!@PYTHON@
import os, sys
assert sys.argv[1] == "--seccomp", sys.argv
os.fstat(int(sys.argv[2]))
raw = open("/proc/self/environ", "rb").read().split(b"\\0")
env = dict(kv.split(b"=", 1) for kv in raw if kv)
os.execve(sys.argv[3], sys.argv[3:], env)
"""

# Stands in for sandbox-exec: records its argv and environment beside itself, skips
# `-f FILE` and `-D K=V`, then runs the rest.
SANDBOX_EXEC = """#!@PYTHON@
import json, os, sys
here = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(here, "record.json"), "w") as f:
    json.dump({"argv": sys.argv, "env": dict(os.environ)}, f)
i = 1
while sys.argv[i] in ("-D", "-f"):
    i += 2
os.execve(sys.argv[i], sys.argv[i:], os.environ)
"""

# What a launcher or init run "on a Mac" is, off one: the platform claimed, and
# the pieces that need libsandbox stubbed (a sweep records its markers and removes
# the marker as the real one does). The real machine check stays.
DRIVER = """
import importlib.util, os, sys
real = sys.platform
sys.platform = "darwin"
log, mode, *rest = sys.argv[1:]
spec = importlib.util.spec_from_file_location("os_init", @OS_INIT@)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
def sweep(marker, control, passes=10):
    with open(log, "a") as f:
        f.write(marker + "\\n" + control + "\\n")
    os.path.exists(marker) and os.unlink(marker)
    return []
m.sweep = sweep
if real != "darwin":
    m._sandbox_check = lambda: None
sys.exit(m.main([mode, *rest]))
"""

ENV_NAMES = """
import os
if os.path.exists('/proc/self/environ'):
    raw = open('/proc/self/environ', 'rb').read().split(b'\\0')
    print(sorted(kv.split(b'=')[0].decode() for kv in raw if kv))
else:
    print(sorted(os.environ))
"""


def claiming(*claims):
    """A `-c` source that runs os_init.py as __main__ after the given statements."""
    body = "\n".join(claims)
    run_path = f"runpy.run_path({str(OS_INIT)!r}, run_name='__main__')"
    return f"import platform, runpy, sys\n{body}\n{run_path}"


def _script(path, text):
    path.write_text(text.replace("@PYTHON@", os.path.realpath(sys.executable)))
    path.chmod(0o755)
    return str(path)


def session_files(tmp_path):
    """(marker, control, tmpdir) a darwin launcher is given."""
    root = tmp_path / "session"
    return root / "markers" / "s", root / "markers" / "control", root / "tmp" / "s"


@pytest.fixture
def launcher(tmp_path):
    """Build the launcher's argv around a stand-in confiner. `darwin` picks the macOS
    form (default: this machine's); off a Mac that runs under the driver, and a
    test on a Mac may ask for the driver too, to see the sweeps (`sweeps=FILE`)."""

    def build(
        mode,
        inner,
        *,
        keep="",
        started=None,
        setenv=(),
        relay=None,
        darwin=None,
        sweeps=None,
        plain=False,
    ):
        real = sys.platform == "darwin"
        darwin = real if darwin is None else darwin
        argv = ["--keep", keep]
        if darwin:
            marker, control, tmpdir = session_files(tmp_path)
            argv += ["--marker", str(marker), "--control", str(control), "--tmpdir", str(tmpdir)]
            argv += ["--relay-sock", str(relay)] if relay else []
            confiner = [_script(tmp_path / "sandbox-exec", SANDBOX_EXEC), "-f", "profile.sb"]
        else:
            (tmp_path / "filter.bpf").write_bytes(b"\0" * 8)
            argv += ["--seccomp-file", str(tmp_path / "filter.bpf")]
            confiner = [_script(tmp_path / "bwrap", BWRAP)]
        for pair in setenv:
            argv += ["--setenv", pair]
        argv += ["--started", str(started)] if started else []
        argv += ["--", *confiner, *inner]
        if darwin and (sweeps or not real) and not plain:
            src = DRIVER.replace("@OS_INIT@", repr(str(OS_INIT)))
            return [*PY, "-c", src, str(sweeps or tmp_path / "sweeps.log"), mode, *argv]
        return [*PY, str(OS_INIT), mode, *argv]

    return build


def init_argv(*cmd, sock=None, port=None, darwin=False, chdir=None):
    relay = ["--sock", str(sock), "--port", str(port)] if sock else []
    argv = [
        *PY,
        str(OS_INIT),
        "init",
        *relay,
        *(["--chdir", str(chdir)] if chdir else []),
        "--",
        *cmd,
    ]
    if darwin and sys.platform != "darwin":
        return [*PY, "-c", claiming("sys.platform = 'darwin'"), *argv[4:]]
    return argv


def py(code, *args):
    return [*PY, "-c", code, *args]


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
def test_launch_env_filtered(launcher, under_init, want_fd):
    env = {"FOO": "1", "BAR": "2", "SECRET": "x"}
    inner = init_argv(*py(ENV_NAMES)) if under_init else py(ENV_NAMES)
    out = run(launcher("launch", inner, keep="FOO,MISSING"), env=env).stdout
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
def test_launch_started(launcher, tmp_path, inner, wrote):
    started = tmp_path / "started"
    r = run(launcher("launch", inner, started=started))
    assert started.exists() is wrote
    if wrote:
        assert started.read_text() == "started\n"
    else:
        assert r.returncode == 127


@pytest.mark.parametrize(
    "sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP], ids=["int", "term", "hup"]
)
def test_launch_outlives_signal(launcher, tmp_path, sig):
    ready = tmp_path / "ready"
    code = (
        "import signal, time, pathlib, sys\n"
        f"signal.signal({int(sig)}, lambda *a: (time.sleep(0.5), sys.exit(7)))\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(30)\n"
    )
    p = subprocess.Popen(launcher("launch", py(code)), start_new_session=True, env={})
    wait_for(ready)
    os.killpg(p.pid, sig)
    assert p.wait(timeout=20) == 7


COPIES = (
    "import os, stat, sys\n"
    "print('one', flush=True); print('two', file=sys.stderr, flush=True)\n"
    "print(stat.S_ISFIFO(os.fstat(1).st_mode), flush=True)\n"
    "try:\n    open('/dev/fd/1', 'a').write('X')\nexcept OSError: pass\n"
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
def test_launch_copies_output(launcher, tmp_path, code, sink, want_rc, want):
    log, done = tmp_path / "log", tmp_path / "done"
    argv = launcher("launch", py(code.replace("DONE", repr(str(done)))))
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


def test_oneshot_passes_stdio_through(launcher):
    code = "import os, sys; print(sys.stdin.read().upper(), 'OS_INIT_STARTED_FD' in os.environ)"
    r = run(launcher("oneshot", py(code)), input="hi")
    assert r.stdout.strip() == "HI False"


@linux_only
def test_launch_keyring(launcher):
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
    r = run([*PY, "-c", driver, *launcher("launch", py(probe))])
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
        pytest.param(
            "(sh -c 'echo $$ > {pid}; exit 0' &); sleep 0.5; test ! -e /proc/$(cat {pid})",
            20,
            marks=linux_only,
        ),
        # Init ends with CMD; it does not wait out an orphan that is still running.
        (
            f"({shlex.quote(sys.executable)} -c 'import os, time; os.setsid(); time.sleep(30)'"
            " >/dev/null 2>&1 &); exit 0",
            10,
        ),
    ],
    ids=["reaps-zombie", "exits-with-cmd-not-orphans"],
)
def test_init_reaps(tmp_path, script, limit):
    start = time.monotonic()
    r = run(init_argv("/bin/sh", "-c", script.format(pid=tmp_path / "pid")))
    assert r.returncode == 0, "the orphan stayed a zombie"
    assert time.monotonic() - start < limit


@pytest.mark.parametrize(
    ("env", "code", "want", "darwin"),
    [
        pytest.param(
            {"TMPDIR": "/elsewhere"},
            "import os; print(os.environ['TMPDIR'])",
            "/tmp",
            False,
            marks=linux_only,
            id="tmpdir-is-tmp",
        ),
        pytest.param(
            {"TMPDIR": "/the/session"},
            "import os; print(os.environ['TMPDIR'])",
            "/the/session",
            True,
            id="tmpdir-kept-on-macos",
        ),
        pytest.param(
            {"HOME": "HOMEDIR/made/here"},
            "import os; print(os.path.isdir(os.environ['HOME']))",
            "True",
            False,
            id="home-created",
        ),
    ],
)
def test_init_environment(tmp_path, env, code, want, darwin):
    env = {k: v.replace("HOMEDIR", str(tmp_path)) for k, v in env.items()}
    assert run(init_argv(*py(code), darwin=darwin), env=env).stdout.strip() == want


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


@pytest.mark.parametrize(
    ("sub", "code"), [("", 0), ("none", 125)], ids=["enters", "refuses-missing"]
)
def test_init_chdir(tmp_path, sub, code):
    here = tmp_path.resolve() / sub
    r = run(init_argv(*py("import os; print(os.getcwd())"), chdir=here))
    assert (r.returncode, r.stdout.strip(), "cannot enter" in r.stderr) == (
        code,
        "" if code else str(here),
        bool(code),
    )


@pytest.mark.parametrize(
    ("claims", "want"),
    [
        pytest.param(
            [],
            "kraft: no keyctl number for riscv64; refusing to start the sandbox",
            marks=linux_only,
            id="linux-keyctl",
        ),
        pytest.param(
            ["sys.platform = 'darwin'"],
            "kraft: os sandbox: kind: os supports arm64 and x86_64 Macs, not riscv64",
            id="macos-sandbox-check",
        ),
    ],
)
def test_launch_refuses_unknown_arch(launcher, claims, want):
    # Run the launcher with platform.machine() faked, so nothing can be started on it.
    argv = launcher("launch", py("print('ran')"), darwin=bool(claims), plain=True)
    r = run([*PY, "-c", claiming("platform.machine = lambda: 'riscv64'", *claims), *argv[4:]])
    assert (r.returncode, r.stdout, r.stderr.strip()) == (125, "", want)


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
    """What the relay answers a connection with, retried until it does: right after
    descriptor exhaustion it may still drop connections it has no descriptor for."""
    end = time.monotonic() + 10
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
                c.sendall(b"ping")
                buf = b""
                while data := c.recv(100):
                    buf += data
            if buf:
                return buf
        except OSError:
            pass
        assert time.monotonic() < end, "relay never answered"
        time.sleep(0.05)


@linux_only  # on macOS the launcher is the relay
def test_relay_reconnects_per_connection(short_run):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    sock = short_run.base / "s.sock"
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


@linux_only
def test_relay_survives_descriptor_exhaustion(short_run):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    sock = short_run.base / "s.sock"
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


def record(tmp_path):
    return json.loads((tmp_path / "record.json").read_text())


RELAY_CLIENT = (
    "import os, socket, urllib.parse\n"
    "u = urllib.parse.urlparse(os.environ['HTTPS_PROXY'])\n"
    "c = socket.create_connection((u.hostname, u.port), timeout=10)\n"
    "c.sendall(b'ping')\n"
    "c.shutdown(socket.SHUT_WR)\n"
    "buf = b''\n"
    "while data := c.recv(100):\n"
    "    buf += data\n"
    "print(buf.decode())\n"
)


def test_launch_darwin_relay(launcher, tmp_path, short_run):
    sock = short_run.base / "s.sock"
    _unix_server(sock)
    r = run(launcher("launch", py(RELAY_CLIENT), relay=sock, darwin=True))
    seen = record(tmp_path)
    port = int(seen["argv"][2].removeprefix("RELAY_PORT="))
    assert (r.stdout.strip(), seen["argv"][1], port > 0) == ("pong:ping", "-D", True)
    assert {seen["env"][n] for n in ("HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "https_proxy")} == {
        f"http://127.0.0.1:{port}"
    }
    assert (seen["env"]["NO_PROXY"], seen["env"]["NODE_USE_ENV_PROXY"]) == ("", "1")


def test_launch_darwin_env(launcher, tmp_path):
    env = {"FOO": "1", "SECRET": "x", "HTTPS_PROXY": "http://elsewhere"}
    argv = launcher(
        "launch", py("pass"), keep="FOO", setenv=["HOME=/x", "EMPTY=", "A=b=c"], darwin=True
    )
    run(argv, env=env)
    seen = record(tmp_path)
    got = {n: seen["env"].get(n) for n in ("FOO", "SECRET", "HOME", "EMPTY", "A", "HTTPS_PROXY")}
    assert got == {
        "FOO": "1",
        "SECRET": None,
        "HOME": "/x",
        "EMPTY": "",
        "A": "b=c",
        "HTTPS_PROXY": None,
    }
    assert seen["env"]["TMPDIR"] == str(session_files(tmp_path)[2])
    assert "OS_INIT_STARTED_FD" in seen["env"]
    assert "-D" not in seen["argv"]  # no relay without --relay-sock


SESSION_STATE = (
    "import os, sys\n"
    "marker, control, tmpdir = sys.argv[1:]\n"
    "mode = lambda p: oct(os.stat(p).st_mode & 0o777)\n"
    "print(mode(marker), os.path.getsize(marker), mode(control), mode(tmpdir))\n"
    "print(os.getsid(0) == os.getpid())\n"
    "raise SystemExit(3)\n"
)


@pytest.mark.parametrize(
    ("mode", "new_session"), [("launch", False), ("oneshot", True)], ids=["launch", "oneshot"]
)
def test_launch_darwin_session(launcher, tmp_path, mode, new_session):
    marker, control, tmpdir = session_files(tmp_path)
    sweeps = tmp_path / "sweeps"
    argv = launcher(
        mode, py(SESSION_STATE, str(marker), str(control), str(tmpdir)), darwin=True, sweeps=sweeps
    )
    r = run(argv)
    # made before the sandbox, empty and private; swept and cleaned after, whatever the status
    assert (r.returncode, r.stdout.split()) == (
        3,
        ["0o600", "0", "0o600", "0o700", str(new_session)],
    )
    assert sweeps.read_text().split() == [str(marker), str(control)]
    assert not tmpdir.exists()


def test_launch_darwin_returns_with_a_survivor(launcher, tmp_path):
    # What the sweep cannot end must not hold the launcher: it holds the pipe open.
    code = "import subprocess; subprocess.Popen(['sleep', '8']); print('out')"
    began = time.monotonic()
    r = run(launcher("launch", py(code), darwin=True, sweeps=tmp_path / "sweeps"))
    assert (r.stdout, time.monotonic() - began < 5) == ("out\n", True)


@pytest.mark.parametrize(
    ("argv", "want"),
    [
        (
            lambda build, t: init_argv("true", sock=t / "s", port=1, darwin=True),
            "no relay on macOS",
        ),
        (
            lambda build, t: build("oneshot", py("pass"), relay=t / "s", darwin=True),
            "oneshot: usage error",
        ),
    ],
    ids=["init-sock", "oneshot-relay"],
)
def test_macos_refuses_what_it_does_not_do(launcher, tmp_path, argv, want):
    r = run(argv(launcher, tmp_path))
    assert (r.returncode != 0, want in r.stderr) == (True, True)
