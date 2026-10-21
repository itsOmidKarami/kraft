"""The os sandbox's launcher and init. Stdlib only, Python 3.10+.

Run as `python -I -S os_init.py MODE ...` so no PYTHONPATH, site directory or
current directory can steer its imports. It imports nothing of Kraft's.

  launch  --keep N[,N...] --seccomp-file F [--started FILE] -- BWRAP_ARGV...
  oneshot --keep N[,N...] --seccomp-file F -- BWRAP_ARGV...
  init    [--sock PATH --port N] -- CMD...
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import select
import signal
import socket
import subprocess
import sys
import threading

HINT = (
    "kraft: a command was not found inside the os sandbox; "
    "add its directory to the sandbox's expose list"
)
KEYCTL_NR = {"x86_64": 250, "aarch64": 219}
KEYCTL_JOIN_SESSION_KEYRING = 1
PR_SET_CHILD_SUBREAPER = 36
STARTED_ENV = "OS_INIT_STARTED_FD"


def _parse(argv, flags):
    """Split argv at `--`; return ({flag: value}, rest). Flags all take one value."""
    opts = {}
    i = 0
    while i < len(argv) and argv[i] != "--":
        if argv[i] not in flags or i + 1 >= len(argv):
            raise SystemExit(f"os_init: bad argument {argv[i]!r}")
        opts[argv[i]] = argv[i + 1]
        i += 2
    return opts, argv[i + 1 :]


def _exit_code(status):
    return 128 + os.WTERMSIG(status) if os.WIFSIGNALED(status) else os.WEXITSTATUS(status)


def _join_new_keyring():
    nr = KEYCTL_NR.get(platform.machine())
    if nr is None:
        return  # shortcut: no join on an unknown arch; add its number with its seccomp table
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.syscall(nr, KEYCTL_JOIN_SESSION_KEYRING, None) == -1:
        err = ctypes.get_errno()
        if err != errno.ENOSYS:  # ENOSYS: the kernel has no keyrings
            raise OSError(err, "keyctl: " + os.strerror(err))


def _write_all(fd, data):
    while data:
        data = data[os.write(fd, data) :]


def _launch(mode, argv):
    opts, bwrap = _parse(argv, ("--keep", "--seccomp-file", "--started"))
    if not bwrap or "--seccomp-file" not in opts or (mode == "oneshot" and "--started" in opts):
        raise SystemExit(f"os_init {mode}: usage error")
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        # Ignored stays ignored across exec, so bwrap outlives a group signal.
        signal.signal(sig, signal.SIG_IGN)
    keep = [n for n in opts.get("--keep", "").split(",") if n]
    env = {n: os.environ[n] for n in keep if n in os.environ}
    try:
        _join_new_keyring()
    except OSError as e:
        print(f"kraft: os sandbox: {e}", file=sys.stderr)
        return 1
    seccomp = os.open(opts["--seccomp-file"], os.O_RDONLY)
    pass_fds = [seccomp]
    kw = {}
    if mode == "launch":
        out_r, out_w = os.pipe()
        st_r, st_w = os.pipe()
        env[STARTED_ENV] = str(st_w)
        pass_fds.append(st_w)
        kw = {"stdout": out_w, "stderr": out_w}
    cmd = [bwrap[0], "--seccomp", str(seccomp), *bwrap[1:]]
    proc = subprocess.Popen(cmd, env=env, pass_fds=pass_fds, **kw)
    os.close(seccomp)
    if mode == "oneshot":
        return _exit_code_of(proc.wait())
    os.close(out_w)
    os.close(st_w)
    _copy(out_r, st_r, opts.get("--started"))
    return _exit_code_of(proc.wait())


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
                    with open(started_file, "w") as f:
                        f.write("started\n")
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
    try:
        up = socket.socket(socket.AF_UNIX)
        up.connect(path)
    except OSError:
        conn.close()
        return
    threading.Thread(target=_pump, args=(up, conn), daemon=True).start()
    _pump(conn, up)


def _start_relay(path, port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(64)

    def accept_loop():
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=_relay_conn, args=(conn, path), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()


def _init(argv):
    opts, cmd = _parse(argv, ("--sock", "--port"))
    if not cmd:
        raise SystemExit("os_init init: no command")
    ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    os.environ["TMPDIR"] = "/tmp"
    if os.environ.get("HOME"):
        os.makedirs(os.environ["HOME"], exist_ok=True)
    if "--sock" in opts:
        _start_relay(opts["--sock"], int(opts["--port"]))
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
