"""A `kind: os` worker against real Seatbelt: it reaches Kraft only through its
channel (`sandbox-callbacks-are-scoped-to-their-session`,
`sandbox-egress-is-deny-by-default-when-declared`). The twin of
`test_gates_bwrap.py`, with the ways out a Mac has instead of a namespace's.

The daemon is the real app, its lifespan entered, serving the shim's calls over
the session's channel. One session runs a fake agent that tries every way out
and records each command's exit status and output under its own name; the test
reads them back and decides, by name, whether the fence held. What only the
host can say (a signal that arrived, a job that was scheduled) it reads from
outside the sandbox. `kind: os` refuses root, so this runs as a normal user:

    KRAFT_E2E_REQUIRE=sandbox-exec just test tests/worker/test_gates_seatbelt.py --no-testmon

It touches the operator's `crontab` and `at` queue only to compare them before
and after; `Outside` puts both back if a check left them changed.
"""

import contextlib
import os
import pty
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path

import psutil
import pytest
from support.harness import git
from support.os_gates import WAIT_S, os_daemon, procs, running, wait_for
from support.session_files import own_files

from kraft.adapters import subprocess as sp
from kraft.vocab import SandboxEvent
from kraft.worker import host_view
from kraft.worker.backends import seatbelt

# Shared, not copied: the run dir short enough for a unix socket path.
from worker.test_egress_docker import _unavailable, short_run  # noqa: F401

SANDBOX = {"kind": "os", "network": {"runtime": {"allow": ["x.io"]}}}
ORPHAN = "3141"
CHECKS = (
    *("shim-works", "loopback-api", "other-loopback", "kraft-home", "daemon-run-dir"),
    *("operator-home", "firmlink-home", "usr-local", "keychain", "launchservices", "pasteboard"),
    *("log-untouchable", "dev-tty", "tiocsti", "signal-launcher", "relay-token", "unix-socket"),
    *("approve-refused", "commit-lands", "main-unmoved", "tag-refused", "launchctl", "pool"),
    *("crontab", "at", "orphan-started", "no-orphans"),
)

#: The agent's snippets, written beside it: one per way out that needs more than a command.
SNIPPETS = {
    # The cost line is on fd 1 as a pipe the launcher copies to the log.
    "log.py": """
import os
for call in (
    lambda: os.ftruncate(1, 0),
    lambda: os.pwrite(1, b"x", 0),
    lambda: os.close(os.open("/dev/fd/1", os.O_RDWR | os.O_TRUNC)),
):
    try:
        call()
    except OSError:
        pass
print("LOG-MARKER-AFTER")
""",
    "devtty.py": """
import os, sys
try:
    os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
except PermissionError:
    print("refused")
else:
    raise SystemExit("opened " + sys.argv[1])
""",
    # Every descriptor the agent has, and the controlling terminal if it has one.
    "tiocsti.py": """
import errno, fcntl, os, termios
for target in (0, 1, 2, "/dev/tty"):
    try:
        fd = target if isinstance(target, int) else os.open(target, os.O_RDWR)
        fcntl.ioctl(fd, termios.TIOCSTI, b"x")
    except OSError as exc:
        # EPERM is the sandbox; a descriptor that is no terminal would say ENOTTY.
        if exc.errno != errno.EPERM:
            raise SystemExit(f"{target}: {exc}")
        print(target, "refused", exc.errno)
    else:
        raise SystemExit(f"injected into {target}")
""",
    # signal 0 and one a process ignores by default: neither may reach a process outside.
    "signal.py": """
import os, signal
for pid in map(int, open("signal.pids").read().split()):
    for sig in (0, signal.SIGURG):
        try:
            os.kill(pid, sig)
        except PermissionError:
            print(pid, sig, "refused")
        else:
            raise SystemExit(f"signal {sig} reached {pid}")
""",
    "sockets.py": """
import os, socket, sys
path = os.path.join(os.environ["TMPDIR"], "s.sock")
srv = socket.socket(socket.AF_UNIX)
srv.bind(path)
srv.listen(1)
cli = socket.socket(socket.AF_UNIX)
cli.connect(path)
conn, _ = srv.accept()
cli.sendall(b"hi")
assert conn.recv(2) == b"hi"
print("serves under TMPDIR")
elsewhere, theirs = sys.argv[1:3]
for how, args in (("bind", elsewhere), ("connect", theirs)):
    try:
        getattr(socket.socket(socket.AF_UNIX), how)(args)
    except PermissionError:
        print(how, "refused")
    else:
        raise SystemExit(f"{how} {args} worked")
""",
    "pool.py": """
import multiprocessing, signal
if __name__ == "__main__":
    signal.alarm(30)
    with multiprocessing.Pool(2) as pool:
        assert pool.map(abs, [-1, -2]) == [1, 2]
    from multiprocessing import shared_memory
    block = shared_memory.SharedMemory(create=True, size=8)
    block.close()
    block.unlink()
    print("pool finished")
""",
}


@pytest.fixture
def daemon(short_run, tmp_path, monkeypatch):  # noqa: F811
    if reason := seatbelt.probe_darwin():
        _unavailable("sandbox-exec", f"e2e: {reason}")
    # The system's tools, which the sandbox shows: a Homebrew one first on PATH
    # (a runner has git there) would be refused, not skipped, inside it.
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    yield from os_daemon(short_run, tmp_path, monkeypatch, SANDBOX)


def _out(*argv: str) -> tuple[int, str]:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    return done.returncode, done.stdout


class Outside:
    """What the test sets up and reads from outside the sandbox: a terminal
    the operator owns, a unix socket in `/private/tmp`, a keychain, the
    operator's `crontab` and `at` queue, and anything a failed fence would have
    started. Each is undone on its own, so one failing step skips none of the rest."""

    def __init__(self, daemon, tmp_path):
        self.mark = uuid.uuid4().hex[:12]
        self.secret = f"hunter-{self.mark}"
        self.work = daemon.work
        self.keychain = daemon.work / "kraft.keychain-db"
        self.touched = tmp_path / f"launchctl-{self.mark}"
        self.label = f"kraft-e2e-{self.mark}"
        self.sock = Path(f"/private/tmp/kraft-e2e-{self.mark}.sock")
        self.sock_b = Path(f"/private/tmp/kraft-e2e-{self.mark}-b.sock")
        self.tmp_path = tmp_path
        self.calculator = {
            p.pid for p in psutil.process_iter(["name"]) if p.info["name"] == "Calculator"
        }

    def __enter__(self):
        with contextlib.ExitStack() as stack:  # a failure part-way unwinds what was made
            self._setup(stack)
            self._stack = stack.pop_all()
        return self

    def __exit__(self, *exc):
        self._stack.close()

    def _setup(self, stack):
        # Saved first, beside a copy on disk in case the run dies before `__exit__`.
        self.crontab = _out("crontab", "-l")
        (self.tmp_path / "crontab.saved").write_text(self.crontab[1])
        stack.callback(self.restore_crontab)
        self.atq = _out("atq")[1].splitlines()
        stack.callback(self.remove_at_jobs)
        stack.callback(_out, "launchctl", "remove", self.label)  # a no-op unless a fence is open
        self.tty_master, tty_slave = pty.openpty()
        stack.callback(os.close, self.tty_master)
        self.tty = os.ttyname(tty_slave)
        os.close(os.open(self.tty, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY))  # the operator can
        os.close(tty_slave)
        listener = socket.socket(socket.AF_UNIX)
        stack.callback(listener.close)
        stack.callback(self.sock.unlink, missing_ok=True)
        stack.callback(self.sock_b.unlink, missing_ok=True)  # bound only if a fence is open
        listener.bind(str(self.sock))
        listener.listen(1)
        # A keychain the agent can read as a file but has no security server to open:
        # `security` reads it outside, which is what makes its failure inside mean something.
        kc, service = str(self.keychain), ("-a", "acct", "-s", "kraft-e2e")
        stack.callback(_out, "security", "delete-keychain", kc)
        _out("security", "create-keychain", "-p", "pw", kc)
        _out("security", "add-generic-password", *service, "-w", self.secret, kc)
        _out("security", "unlock-keychain", "-p", "pw", kc)
        assert (
            _out("security", "find-generic-password", *service, "-w", kc)[1].strip() == self.secret
        )
        # The comment line is the whole change a successful `crontab FILE` would make.
        (self.work / "crontab.new").write_text(f"{self.crontab[1]}# kraft-e2e-{self.mark}\n")

    def restore_crontab(self):
        if _out("crontab", "-l") == self.crontab:
            return
        if self.crontab[0] == 0:
            subprocess.run(["crontab", "-"], input=self.crontab[1], text=True, check=True)
        else:
            subprocess.run(["crontab", "-r"], check=True)

    def at_jobs(self) -> list[str]:
        return [j for j in _out("atq")[1].splitlines() if j not in self.atq]

    def remove_at_jobs(self):
        for job in self.at_jobs():
            ident = job.split()[0]
            if self.mark in _out("at", "-c", ident)[1]:
                _out("atrm", ident)

    def relay_answer(self, port: int) -> bytes:
        """The relay's reply to a request that carries no token, from here."""
        with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
            conn.sendall(b"GET http://x.io/ HTTP/1.1\r\nHost: x.io\r\n\r\n")
            return conn.recv(200)


def _agent_checks(d, out: Outside) -> str:
    """The fake agent: `run NAME cmd...` records the command's status in
    `r.NAME.rc` and its output in `r.NAME.out`."""
    py = host_view.init_interpreter()[0]
    firmlink = f"/System/Volumes/Data{os.path.realpath(d.home)}/.ssh/kraft-sentinel"
    return f"""
run() {{ name=$1; shift; "$@" > "r.$name.out" 2>&1 < /dev/null; echo $? > "r.$name.rc"; }}
PY='{py}'
echo LOG-MARKER-BEFORE
echo "${{HTTPS_PROXY##*:}}" > relay.port
touch ready
i=0; while [ ! -e go ] && [ $i -lt 600 ]; do sleep 0.1; i=$((i+1)); done

run shim-works kraft view show
run loopback-api curl --noproxy '*' -m 2 -s http://127.0.0.1:{d.api_port}/api/health
run other-loopback curl --noproxy '*' -m 2 -s http://127.0.0.1:{d.other_port}/api/health
run kraft-home cat {d.kraft_home}/run/mcp-token
run daemon-run-dir cat {d.run_dirs.base}/mcp-token
run operator-home cat {d.home}/.ssh/kraft-sentinel
run firmlink-home cat {firmlink}
run usr-local ls /usr/local
run keychain security find-generic-password -a acct -s kraft-e2e -w {out.keychain}
run launchservices /usr/bin/open -g -j -a Calculator
run pasteboard pbpaste
# Not through `run`: fd 1 here is the pipe the launcher copies to the log.
$PY -I -S log.py; echo $? > r.log-untouchable.rc; : > r.log-untouchable.out
run dev-tty $PY -I -S devtty.py {out.tty}
run tiocsti $PY -I -S tiocsti.py
run signal-launcher $PY -I -S signal.py
run unix-socket $PY -I -S sockets.py {out.sock_b} {out.sock}
run pool $PY -I -S pool.py
run launchctl launchctl submit -l {out.label} -- /usr/bin/touch {out.touched}
run crontab crontab crontab.new
run at sh -c "echo ': kraft-e2e-{out.mark}' | at now + 1 hour"
url=http://kraft/api/work-items/w1/gates/x/approve
curl -q -sS -m 5 -X POST -o approve.body -w '%{{http_code}}' "$url" > approve.out 2>&1
echo landed > landed.txt && git add landed.txt
run commit-lands git -c user.name=w -c user.email=w@x commit -qm landed
run main-unmoved git update-ref refs/heads/main HEAD
run tag-refused git tag kraft-e2e-tag
$PY -I -S -c 'import os; os.setsid(); os.execvp("sleep", ["sleep", "{ORPHAN}"])' &
orphan=$!
sleep 0.3
run orphan-started kill -0 $orphan
echo '{{"status": "done"}}' > "$KRAFT_RESULT_PATH"
exit 0
"""


def _ran(work: Path) -> dict[str, tuple[int, str]]:
    return {
        f.name[2:-3]: (int(f.read_text()), f.with_suffix(".out").read_text())
        for f in work.glob("r.*.rc")
    }


def _wait_ready(daemon, fut):
    """The agent's `ready`, or why it never came."""
    ready = daemon.work / "ready"
    deadline = time.monotonic() + WAIT_S
    while not ready.exists():
        if fut.done():
            raise AssertionError(f"the agent ended before it was ready: {fut.result()[0]}")
        assert time.monotonic() < deadline, "the agent never became ready"
        time.sleep(0.1)


def _launcher(daemon, sid: str) -> psutil.Process:
    wrapper = psutil.Process(running(daemon.client, sid)["pid"])
    (launcher,) = [c for c in wrapper.children() if any("os_init.py" in a for a in c.cmdline())]
    return launcher


@pytest.mark.e2e("sandbox-exec")
@pytest.mark.timeout(240)  # the waits below end by 2 x WAIT_S, with the cleanup after
def test_os_worker_is_fenced(daemon, tmp_path):
    sid = "s-fenced"
    for name, text in SNIPPETS.items():
        (daemon.work / name).write_text(text)
    main = git(daemon.repo, "rev-parse", "main")
    with Outside(daemon, tmp_path) as out:
        fut = daemon.launch(_agent_checks(daemon, out), sid)
        try:
            _wait_ready(daemon, fut)
            launcher = _launcher(daemon, sid)
            others = [launcher.pid, launcher.ppid(), os.getpid()]
            (daemon.work / "signal.pids").write_text(" ".join(map(str, others)))
            # From outside, as any process of the operator's would: no token, no relay.
            relay = out.relay_answer(int((daemon.work / "relay.port").read_text()))
            (daemon.work / "go").touch()
            status, evs = fut.result(WAIT_S)
            orphans = procs("sleep", ORPHAN)  # the agent left one behind
        finally:
            for p in procs("sleep", ORPHAN):
                p.kill()
            (daemon.work / "go").touch()  # a failed run must not leave the agent waiting
        at_jobs = out.at_jobs()
        crontab_after = _out("crontab", "-l")
        time.sleep(1)  # `open` hands its job to LaunchServices and returns
        calculator = [
            p.pid
            for p in psutil.process_iter(["name"])
            if p.info["name"] == "Calculator" and p.pid not in out.calculator
        ]
        crontab_changed = crontab_after != out.crontab

    ran = _ran(daemon.work)
    rc = {name: r for name, (r, _) in ran.items()}
    said = {name: text for name, (_, text) in ran.items()}
    log = own_files(daemon.run_dirs, sid).log.read_text()
    refused = [e["payload"] for e in evs if e["type"] == SandboxEvent.EGRESS_REFUSED]
    approve = (daemon.work / "approve.out").read_text()
    # 127 is a command that is not there, which would pass every "it failed" below. 126,
    # a refused exec, is the fence itself for `crontab` and `at` (setuid), told apart by its text.
    refusals = (
        *("loopback-api", "other-loopback", "kraft-home", "daemon-run-dir", "operator-home"),
        *("firmlink-home", "usr-local", "launchservices", "pasteboard", "launchctl"),
        *("main-unmoved", "tag-refused", "crontab", "at"),
    )
    held = {name: rc.get(name, 0) not in (0, 127) for name in refusals}
    for name in ("launchservices", "pasteboard", "launchctl"):
        held[name] &= rc.get(name) != 126
    # What Seatbelt itself answers, so the command failed for the fence and for nothing else.
    for name in (
        *("kraft-home", "daemon-run-dir", "operator-home", "firmlink-home", "usr-local"),
        *("crontab", "at", "main-unmoved", "tag-refused"),
    ):
        held[name] &= "not permitted" in said.get(name, "")

    held["shim-works"] = rc.get("shim-works") == 0
    held["loopback-api"] &= rc.get("loopback-api") == 7 and not daemon.api.hits  # never reached
    held["other-loopback"] &= rc.get("other-loopback") == 7 and not daemon.other.hits
    for name in ("kraft-home", "daemon-run-dir", "operator-home", "firmlink-home"):
        held[name] &= "secret" not in said.get(name, "secret")
    held["keychain"] = (
        rc.get("keychain") not in (None, 0, 126, 127) and out.secret not in said["keychain"]
    )
    # Outside, the pasteboard answers; where it does not (a runner with no GUI session)
    # its failing inside proves nothing, and the check cannot fail.
    held["pasteboard"] = rc.get("pasteboard", 0) != 0 or _out("pbpaste")[0] != 0
    held["launchservices"] &= not calculator
    held["log-untouchable"] = rc.get("log-untouchable") == 0 and all(
        marker in log for marker in ("LOG-MARKER-BEFORE", "LOG-MARKER-AFTER")
    )
    for name in ("dev-tty", "tiocsti", "signal-launcher", "unix-socket", "pool"):
        held[name] = rc.get(name) == 0
    held["relay-token"] = relay.startswith(b"HTTP/1.1 407")
    held["approve-refused"] = approve == "403" and any("approve" in str(p) for p in refused)
    held["commit-lands"] = git(daemon.repo, "log", "-1", "--format=%s", "kraft/t-w1") == "landed"
    held["main-unmoved"] &= git(daemon.repo, "rev-parse", "main") == main
    held["tag-refused"] &= not git(daemon.repo, "tag", "-l")
    held["launchctl"] &= _out("launchctl", "list", out.label)[0] != 0 and not out.touched.exists()
    held["crontab"] &= not crontab_changed
    held["at"] &= not at_jobs
    held["orphan-started"] = rc.get("orphan-started") == 0
    held["no-orphans"] = not orphans
    failed = [name for name in CHECKS if not held.get(name)]
    assert not failed, (
        f"{failed} did not hold: "
        + "; ".join(f"{n}: rc={rc.get(n)} said={said.get(n)!r}" for n in failed)
        + f"\nall={rc}\napprove.out={approve!r} refused={refused}\n{log}"
    )
    assert status == "done", log


@pytest.mark.e2e("sandbox-exec")
def test_branch_symref_is_restored(daemon):
    """A worker that points its branch at `main` moves nothing, and the branch is
    put back when the session ends (`branch-symref-restored`)."""
    sid = "s-symref"
    main = git(daemon.repo, "rev-parse", "main")
    before = git(daemon.repo, "rev-parse", "kraft/t-w1")
    status, evs = daemon.launch(
        """
common=$(git rev-parse --git-common-dir)
printf 'ref: refs/heads/main\\n' > "$common/refs/heads/kraft/t-w1"
git -c user.name=w -c user.email=w@x commit --allow-empty -qm moved-main
echo '{"status": "done"}' > "$KRAFT_RESULT_PATH"
""",
        sid,
    ).result(WAIT_S)

    unsynced = [e["payload"] for e in evs if e["type"] == SandboxEvent.BRANCH_NOT_SYNCED]
    ref = Path(git(daemon.repo, "rev-parse", "--git-common-dir"), "refs/heads/kraft/t-w1")
    assert git(daemon.repo, "rev-parse", "main") == main
    assert re.fullmatch(r"[0-9a-f]{40,64}", (daemon.repo / ref).read_text().strip())
    assert git(daemon.repo, "rev-parse", "kraft/t-w1") == before
    assert [p["branch"] for p in unsynced] == ["kraft/t-w1"], status


@pytest.mark.e2e("sandbox-exec")
def test_pause_flushes(daemon):
    sid = "s-pause"
    files = own_files(daemon.run_dirs, sid)
    fut = daemon.launch(
        """
trap 'sleep 1; echo flushed > "$KRAFT_RESULT_PATH"; exit 0' INT
echo ready > ready
while :; do sleep 0.2; done
""",
        sid,
    )
    try:
        wait_for(daemon.work / "ready", "the agent")
        assert daemon.client.post("/api/work-items/w1/pause").status_code == 200
        status, _ = fut.result(WAIT_S)

        assert status == "paused"
        assert files.result.read_text().strip() == "flushed"
    finally:  # a hang must not leave the agent's loop running
        for p in procs("sleep", "0.2"):
            p.kill()
        for p in psutil.process_iter(["cmdline"]):
            if any("s-pause.sh" in a for a in p.info["cmdline"] or ()):
                p.kill()


@pytest.mark.e2e("sandbox-exec")
def test_launcher_killed(daemon, monkeypatch):
    sid = "s-killed"

    async def no_group_kill(*_a, **_kw):  # Kraft's own group kill would end it too
        pass

    monkeypatch.setattr(sp, "_kill_group", no_group_kill)
    py = host_view.init_interpreter()[0]
    fut = daemon.launch(
        f"""echo ready > ready
{py} -I -S -c 'import os; os.setsid(); os.execvp("sleep", ["sleep", "3142"])' &
wait
""",
        sid,
    )
    try:
        wait_for(daemon.work / "ready", "the agent")
        deadline = time.monotonic() + 15
        while not procs("sleep", "3142") and time.monotonic() < deadline:
            time.sleep(0.1)
        assert procs("sleep", "3142"), "the agent is not running"

        _launcher(daemon, sid).kill()
        # The launcher's own sweep died with it: the session's `close` ends the sandbox.
        fut.result(WAIT_S)
        deadline = time.monotonic() + 15
        while procs("sleep", "3142") and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not procs("sleep", "3142"), "the sandbox outlived its killed launcher"
    finally:
        for p in procs("sleep", "3142"):  # a failed run must not hang the fixture's teardown
            p.kill()
