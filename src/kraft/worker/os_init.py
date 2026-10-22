"""The os sandbox's launcher and init. Stdlib only, Python 3.10+.

Run as `python -I -S os_init.py MODE ...` so no PYTHONPATH, site directory or
current directory can steer its imports. It imports nothing of Kraft's.

  launch  --keep N[,N...] --seccomp-file F [--started FILE] -- BWRAP_ARGV...
  oneshot --keep N[,N...] --seccomp-file F -- BWRAP_ARGV...
  init    [--sock PATH --port N] [--chdir DIR] -- CMD...

On macOS the launcher runs `sandbox-exec` instead of bwrap and takes
`--marker FILE --control FILE --tmpdir DIR [--relay-sock PATH]` in place of
`--seccomp-file`: it is the relay, and it sweeps every process of the session
when the command exits (see `sweep`). `--setenv NAME=VALUE` (repeatable) sets a
literal in the command's environment.
"""

from __future__ import annotations

import ctypes
import errno
import functools
import os
import platform
import resource
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

HINT = (
    "kraft: a command was not found inside the os sandbox; "
    "add its directory to the sandbox's expose list"
)
KEYCTL_NR = {"x86_64": 250, "aarch64": 219}
KEYCTL_JOIN_SESSION_KEYRING = 1
PR_SET_CHILD_SUBREAPER = 36
STARTED_ENV = "OS_INIT_STARTED_FD"
SANDBOX_FILTER_PATH = 1
SANDBOX_CHECK_NO_REPORT = 0x40000000
PROC_PIDTBSDINFO = 3
SZOMB = 5
#: The worker's proxy environment under `network:` on macOS, as `mounts.RELAY_PROXY_ENV`
#: spells it for Linux, with the port the launcher's relay was given.
PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
LAUNCH_FLAGS = (
    "--keep",
    "--setenv",
    "--started",
    "--seccomp-file",
    "--marker",
    "--control",
    "--tmpdir",
    "--relay-sock",
)


def _parse(argv, flags, repeat=()):
    """Split argv at `--`; return ({flag: value}, rest). Flags all take one value;
    a `repeat` flag collects its values in a list."""
    opts = {}
    i = 0
    while i < len(argv) and argv[i] != "--":
        if argv[i] not in flags or i + 1 >= len(argv):
            raise SystemExit(f"os_init: bad argument {argv[i]!r}")
        if argv[i] in repeat:
            opts.setdefault(argv[i], []).append(argv[i + 1])
        else:
            opts[argv[i]] = argv[i + 1]
        i += 2
    return opts, argv[i + 1 :]


def _exit_code(status):
    return 128 + os.WTERMSIG(status) if os.WIFSIGNALED(status) else os.WEXITSTATUS(status)


def _join_new_keyring():
    nr = KEYCTL_NR.get(platform.machine())
    if nr is None:  # failing open would leave the command able to see the daemon's keys
        print(
            f"kraft: no keyctl number for {platform.machine()}; refusing to start the sandbox",
            file=sys.stderr,
        )
        raise SystemExit(125)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.syscall(nr, KEYCTL_JOIN_SESSION_KEYRING, None) == -1:
        err = ctypes.get_errno()
        if err != errno.ENOSYS:  # ENOSYS: the kernel has no keyrings
            raise OSError(err, "keyctl: " + os.strerror(err))


def _write_all(fd, data):
    while data:
        data = data[os.write(fd, data) :]


def _launch(mode, argv):
    opts, cmd = _parse(argv, LAUNCH_FLAGS, repeat=("--setenv",))
    darwin = sys.platform == "darwin"
    need = ("--marker", "--control", "--tmpdir") if darwin else ("--seccomp-file",)
    if not cmd or not all(n in opts for n in need) or (mode == "oneshot" and "--started" in opts):
        raise SystemExit(f"os_init {mode}: usage error")
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        # Ignored stays ignored across exec, so the sandbox outlives a group signal.
        signal.signal(sig, signal.SIG_IGN)
    keep = [n for n in opts.get("--keep", "").split(",") if n]
    env = {n: os.environ[n] for n in keep if n in os.environ}
    for pair in opts.get("--setenv", ()):
        name, _, value = pair.partition("=")
        env[name] = value
    pass_fds = []
    seccomp = None
    if darwin:
        try:
            _sandbox_check()  # a machine it cannot sweep on is refused before anything starts
            cmd = _prepare_darwin(opts, env, cmd)
        except OSError as e:
            print(f"kraft: os sandbox: {e}", file=sys.stderr)
            return 125
    else:
        try:
            _join_new_keyring()
        except OSError as e:
            print(f"kraft: os sandbox: {e}", file=sys.stderr)
            return 1
        seccomp = os.open(opts["--seccomp-file"], os.O_RDONLY)
        pass_fds.append(seccomp)
        cmd = [cmd[0], "--seccomp", str(seccomp), *cmd[1:]]
    kw = {}
    if mode == "launch":
        out_r, out_w = os.pipe()
        st_r, st_w = os.pipe()
        env[STARTED_ENV] = str(st_w)
        pass_fds.append(st_w)
        kw = {"stdout": out_w, "stderr": out_w}
    # A oneshot's client may hold the operator's terminal: a new session takes it off.
    proc = subprocess.Popen(
        cmd, env=env, pass_fds=pass_fds, start_new_session=darwin and mode == "oneshot", **kw
    )
    if seccomp is not None:
        os.close(seccomp)

    def wait():
        code = _exit_code_of(proc.wait())
        if darwin:
            _end_session(opts)
        return code

    if mode == "oneshot":
        return wait()
    os.close(out_w)
    os.close(st_w)
    # Orphans hold the output pipe open, so the sweep that ends them runs as soon
    # as the command does, not at EOF.
    result = []
    waiter = threading.Thread(target=lambda: result.append(wait()))
    waiter.start()
    _copy(out_r, st_r, opts.get("--started"))
    waiter.join()
    return result[0]


def _prepare_darwin(opts, env, cmd):
    """Make the session's marker, control marker and temporary directory; under
    `--relay-sock` bind the relay's port and name it to the profile and the proxy
    variables. Returns the sandbox-exec argv to run."""
    for path in (opts["--marker"], opts["--control"]):
        _touch(path)
    os.makedirs(opts["--tmpdir"], mode=0o700, exist_ok=True)
    env["TMPDIR"] = opts["--tmpdir"]
    if "--relay-sock" not in opts:
        return cmd
    listener = _listen(0)  # the kernel picks: the profile cannot say "the port I bound"
    port = listener.getsockname()[1]
    env.update({n: f"http://127.0.0.1:{port}" for n in PROXY_NAMES})
    env.update({"NO_PROXY": "", "no_proxy": "", "NODE_USE_ENV_PROXY": "1"})
    _start_relay(opts["--relay-sock"], listener)
    return [cmd[0], "-D", f"RELAY_PORT={port}", *cmd[1:]]


def _touch(path):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))


def _end_session(opts):
    left = sweep(opts["--marker"], opts["--control"])
    if left:  # the marker stays, so a later sweep finds them
        print(f"kraft: os sandbox: processes survived the sweep: {left}", file=sys.stderr)
    shutil.rmtree(opts["--tmpdir"], ignore_errors=True)


def _exit_code_of(returncode):
    return 128 - returncode if returncode < 0 else returncode


def _copy(out_r, st_r, started_file):
    """Copy the output pipe to fd 1 until EOF; note the first started byte."""
    watch = [out_r, st_r]
    broken = False
    while out_r in watch:
        for fd in select.select(watch, [], [])[0]:
            data = os.read(fd, 65536)
            if not data:
                watch.remove(fd)
            elif fd == st_r:
                watch.remove(fd)
                if started_file:
                    try:
                        with open(started_file, "w") as f:
                            f.write("started\n")
                    except OSError:
                        pass  # no record: the caller reads that as a launch that failed
            elif not broken:
                try:
                    _write_all(1, data)
                except OSError:
                    broken = True  # keep draining so the agent never blocks
    os.close(out_r)
    os.close(st_r)


def _pump(src, dst):
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    try:
        dst.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def _relay_conn(conn, path):
    up = None
    try:
        up = socket.socket(socket.AF_UNIX)
        up.connect(path)
        t = threading.Thread(target=_pump, args=(up, conn), daemon=True)
        t.start()
        _pump(conn, up)
        t.join()
    except OSError:
        pass
    finally:
        conn.close()
        if up is not None:
            up.close()


def _listen(port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(64)
    return srv


def _start_relay(path, srv):
    """Forward each connection `srv` accepts to the unix socket at `path`."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != hard:  # each relayed connection holds two descriptors
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))

    def accept_loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:  # EMFILE and the like: wait for descriptors, never give up
                time.sleep(0.05)
                continue
            threading.Thread(target=_relay_conn, args=(conn, path), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()


@functools.cache
def _libc():
    return ctypes.CDLL(None, use_errno=True)


def _sandbox_check():
    """`sandbox_check(pid, operation, type, path) -> int` over libsystem_sandbox.
    It is variadic: arm64 passes a variadic argument on the stack, where ctypes
    would put it in a register, so five unused `c_long` fill x3-x7 and the path
    comes ninth; x86_64 passes the four in registers."""
    machine = platform.machine()
    if machine not in ("arm64", "x86_64"):
        raise OSError(f"kind: os supports arm64 and x86_64 Macs, not {machine}")
    fn = _libc().sandbox_check
    fill = (0,) * 5 if machine == "arm64" else ()
    fn.restype = ctypes.c_int
    fn.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        *[ctypes.c_long] * len(fill),
        ctypes.c_char_p,
    ]
    return lambda pid, operation, kind, path: fn(pid, operation, kind, *fill, path)


def session_pids(marker, control):
    """The pids of one session's processes: sandboxed, the caller's user, able to
    read `marker` (which only that session's profile allows) and not `control`
    (which no profile allows; an Apple app that may read anywhere under /private
    passes the other tests and fails this). Empty off macOS."""
    if sys.platform != "darwin":
        return []
    check, libc = _sandbox_check(), _libc()
    libc.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    libc.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    pids = (ctypes.c_int * (libc.proc_listallpids(None, 0) + 64))()  # slack for new processes
    pids = pids[: max(libc.proc_listallpids(pids, ctypes.sizeof(pids)), 0)]
    info = ctypes.create_string_buffer(256)  # struct proc_bsdinfo: status at 4, uid at 20
    read = SANDBOX_FILTER_PATH | SANDBOX_CHECK_NO_REPORT
    found = []
    for pid in pids:
        if pid in (0, os.getpid()) or libc.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, info, 256) < 24:
            continue
        status, uid = struct.unpack_from("=I", info, 4)[0], struct.unpack_from("=I", info, 20)[0]
        if (
            uid == os.geteuid()
            and status != SZOMB
            and check(pid, None, 0, None) == 1
            and check(pid, b"file-read-data", read, os.fsencode(marker)) == 0
            and check(pid, b"file-read-data", read, os.fsencode(control)) != 0
        ):
            found.append(pid)
    return found


def _signal_all(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:  # gone already
            pass


def sweep(marker, control, passes=10):
    """End every process of the session `marker` names: stop them all, then kill
    them all (a fork loop cannot outrun it, and no pid is reused between), and
    repeat until a pass finds none. Only an empty pass removes the marker; the pids
    still alive after `passes` are returned and the marker stays, so a later
    sweep still finds them."""
    _touch(control)  # a control marker that does not exist reads as denied for everyone
    for _ in range(passes):
        pids = session_pids(marker, control)
        if not pids:
            break
        _signal_all(pids, signal.SIGSTOP)
        _signal_all(pids, signal.SIGKILL)
    else:
        pids = session_pids(marker, control)
    if not pids:
        try:
            os.unlink(marker)
        except FileNotFoundError:
            pass
    return pids


def _init(argv):
    opts, cmd = _parse(argv, ("--sock", "--port", "--chdir"))
    if not cmd:
        raise SystemExit("os_init init: no command")
    if sys.platform != "darwin":  # macOS has no subreaper, and the launcher set TMPDIR
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        os.environ["TMPDIR"] = "/tmp"
    if os.environ.get("HOME"):
        os.makedirs(os.environ["HOME"], exist_ok=True)
    if "--chdir" in opts:  # sandbox-exec has no chdir of its own
        try:
            os.chdir(opts["--chdir"])
        except OSError as e:
            print(f"kraft: os sandbox: cannot enter {opts['--chdir']}: {e}", file=sys.stderr)
            return 125
    if "--sock" in opts:
        _start_relay(opts["--sock"], _listen(int(opts["--port"])))
    started_fd = os.environ.pop(STARTED_ENV, None)
    child = None

    def forward(sig, _frame):
        try:
            if child is not None and os.getpgid(child.pid) != os.getpgid(0):
                os.kill(child.pid, sig)
        except OSError:
            pass

    # Handlers reset to default across exec, so the command gets default actions
    # even though the launcher's ignore reached us.
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, forward)
    try:
        child = subprocess.Popen(cmd)  # raises when the exec fails: no byte then
    except PermissionError:
        print(f"kraft: {cmd[0]} is not executable", file=sys.stderr)
        return 126
    except OSError:
        print(HINT, file=sys.stderr)
        return 127
    if started_fd is not None:
        try:
            os.write(int(started_fd), b"1")
            os.close(int(started_fd))
        except OSError:
            pass
    while True:
        try:
            pid, status = os.waitpid(-1, 0)
        except ChildProcessError:
            return 127
        if pid == child.pid:
            break
    try:
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    except ChildProcessError:
        pass
    code = _exit_code(status)
    if code == 127:
        print(HINT, file=sys.stderr)
    return code


def main(argv):
    if not argv or argv[0] not in ("launch", "oneshot", "init"):
        print("usage: os_init.py launch|oneshot|init ...", file=sys.stderr)
        return 2
    mode, rest = argv[0], argv[1:]
    return _init(rest) if mode == "init" else _launch(mode, rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
