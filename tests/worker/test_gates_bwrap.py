"""A `kind: os` worker against real bubblewrap: it reaches Kraft only through its
channel (`sandbox-callbacks-are-scoped-to-their-session`,
`sandbox-egress-is-deny-by-default-when-declared`).

The daemon is the real app, its lifespan entered, serving the shim's calls over
the session's channel. One session runs a fake agent that tries every way out
and writes what happened to its result file; the test reads it back by name.
`kind: os` refuses root, so this runs as a normal user only:

    KRAFT_E2E_REQUIRE=bwrap just test tests/worker/test_gates_bwrap.py --no-testmon
"""

import http.server
import json
import os
import stat
import threading
import time

import psutil
import pytest
from support.api import _client, _force_node
from support.harness import git, make_repo
from support.permissions import PATH, seed_session, templates
from support.session_files import own_files

from kraft import events
from kraft.adapters import subprocess as sp
from kraft.vocab import SandboxEvent
from kraft.worker.backends import bwrap

# Shared, not copied: the run dir short enough for a unix socket path.
from worker.test_egress_docker import _unavailable, short_run  # noqa: F401

SANDBOX = {"kind": "os", "network": {"runtime": {"allow": ["x.io"]}}}
SECRET = "kraft-test-secret-value"
WAIT_S = 120
CHECKS = (
    *("shim-works", "loopback-api", "kraft-home", "operator-home", "pid1-environ"),
    *("log-untouchable", "nested-userns", "shm-capped", "approve-refused", "commit-lands"),
    "no-orphans",
)


class _Health(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def do_GET(self):
        self.hits.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_):
        pass


@pytest.fixture
def daemon(short_run, tmp_path, monkeypatch):  # noqa: F811
    """`SimpleNamespace(launch, agent, ...)`: `launch(script, sid)` starts
    `run_task` of `script` (an agent script body) in a `kind: os` sandbox on item
    `w1`, whose worktree is a real linked worktree of a repo, and returns a future
    of `(status, events)`. The host is planted with what the sandbox must not
    reach: a file under the Kraft home and one under the operator's home."""
    if reason := bwrap.probe_host():
        _unavailable("bwrap", f"e2e: {reason}")
    home = tmp_path / "home"
    kraft_home = home / ".kraft"
    for planted in (kraft_home / "run" / "mcp-token", home / ".ssh" / "kraft-sentinel"):
        planted.parent.mkdir(parents=True)
        planted.write_text("secret\n")
    monkeypatch.setenv("HOME", str(home))
    tpl = templates(short_run.base, monkeypatch)
    # After `templates`, which points KRAFT_HOME at a fake harness home.
    monkeypatch.setenv("KRAFT_HOME", str(kraft_home))
    # In the daemon's environment and passed through to nothing.
    monkeypatch.setenv("KRAFT_TEST_SECRET", SECRET)
    # Beside the Kraft home's folder, never under it, and not named `bin`.
    agents = tmp_path / "agents"
    agents.mkdir()
    api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    _Health.hits = []
    threading.Thread(target=api.serve_forever, daemon=True).start()
    repo = make_repo(tmp_path)
    with _client(short_run.base, monkeypatch, templates_dir=tpl) as client:
        app = client.app
        seed_session()
        _force_node("w1", "implementation", "active")
        work = app.state.run_dirs.worktrees / "w1"
        git(repo, "worktree", "add", "-q", "-b", "kraft/t-w1", str(work))

        def launch(script: str, sid: str):
            path = agents / f"{sid}.sh"
            path.write_text("#!/bin/sh\n" + script)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

            async def run():
                status = await sp.run_task(
                    app.state.db,
                    app.state.run_dirs,
                    session_id=sid,
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point=PATH,
                    cmd=[str(path)],
                    cwd=work,
                    sandbox=SANDBOX,
                )
                return status, app.state.db.read(lambda c: events.read_after(c, 0, "w1"))

            return client.portal.start_task_soon(run)

        class Ns:
            pass

        ns = Ns()
        ns.client, ns.launch, ns.work, ns.repo = client, launch, work, repo
        ns.run_dirs, ns.home, ns.kraft_home = app.state.run_dirs, home, kraft_home
        ns.api_port = api.server_address[1]
        yield ns
    api.shutdown()


def _running(client, sid: str) -> dict:
    """The session's row once its launcher has a pid."""
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        # The reader connection belongs to the app's loop thread.
        row = client.portal.call(
            lambda: client.app.state.db.read(
                lambda c: c.execute(
                    "SELECT pid, status FROM worker_sessions WHERE id = ?", (sid,)
                ).fetchone()
            )
        )
        if row and row["pid"]:
            return dict(row)
        time.sleep(0.1)
    raise AssertionError(f"session {sid} never started")


def _wait_for(path, what: str):
    deadline = time.monotonic() + WAIT_S
    while not path.exists():
        assert time.monotonic() < deadline, f"{what} never appeared"
        time.sleep(0.1)


def _procs(*argv: str) -> list[psutil.Process]:
    return [p for p in psutil.process_iter(["cmdline"]) if p.info["cmdline"] == list(argv)]


def _agent_checks(d) -> str:
    """The fake agent: every check writes `<name> <exit code>` to the result file."""
    return f"""
out=checks.txt
: > "$out"
check() {{ name=$1; shift; "$@" >/dev/null 2>&1; echo "$name $?" >> "$out"; }}
echo LOG-MARKER-BEFORE

check shim-works kraft view show
curl --noproxy '*' -m 2 -s http://127.0.0.1:{d.api_port}/api/health >/dev/null 2>&1
[ $? -eq 7 ]; echo "loopback-api $?" >> "$out"
check kraft-home test ! -e {d.kraft_home}/run/mcp-token
check operator-home test ! -e {d.home}/.ssh/kraft-sentinel
environ=$(tr '\\0' '\\n' < /proc/1/environ) || environ=KRAFT_TEST_SECRET
! printf %s "$environ" | grep -qE 'KRAFT_TEST_SECRET|^KRAFT_HOME='
echo "pid1-environ $?" >> "$out"
python3 -c 'import os; os.ftruncate(os.open("/proc/self/fd/1", os.O_RDWR), 0)' 2>/dev/null
echo "log-untouchable 0" >> "$out"
command -v unshare >/dev/null && ! unshare -Ur true >/dev/null 2>&1
echo "nested-userns $?" >> "$out"
[ "$(df -Pk /dev/shm | awk 'NR==2 {{print $2}}')" -le 262144 ]; echo "shm-capped $?" >> "$out"
url=http://kraft/api/work-items/w1/gates/x/approve
curl -q -sS -m 5 -X POST -o approve.body -w '%{{http_code}}' "$url" > approve.out 2>&1
[ "$(cat approve.out)" = 403 ]; echo "approve-refused $?" >> "$out"
echo landed > landed.txt && git add landed.txt
git -c user.name=w -c user.email=w@x commit -qm landed
echo "commit-lands $?" >> "$out"
sleep 3141 &
echo '{{"status": "done"}}' > "$KRAFT_RESULT_PATH"
exit 0
"""


@pytest.mark.e2e("bwrap")
def test_os_worker_is_fenced(daemon):
    sid = "s-fenced"
    status, evs = daemon.launch(_agent_checks(daemon), sid).result(WAIT_S)

    files = own_files(daemon.run_dirs, sid)
    lines = (daemon.work / "checks.txt").read_text().splitlines()
    ran = {name: int(rc) for name, rc in (line.split() for line in lines)}
    log = files.log.read_text()
    refused = [e["payload"] for e in evs if e["type"] == SandboxEvent.EGRESS_REFUSED]
    held = {name: rc == 0 for name, rc in ran.items()}
    held["loopback-api"] &= not _Health.hits  # the host's listener was never reached
    held["log-untouchable"] = "LOG-MARKER-BEFORE" in log  # the host log keeps its earlier lines
    held["approve-refused"] = ran.get("approve-refused", 1) == 0 and any(
        "approve" in json.dumps(p) for p in refused
    )
    held["commit-lands"] &= git(daemon.repo, "log", "-1", "--format=%s", "kraft/t-w1") == "landed"
    held["no-orphans"] = not _procs("sleep", "3141")  # the agent left one behind
    approve = (daemon.work / "approve.out").read_text()
    for name in CHECKS:
        assert held.get(name), (
            f"{name} did not hold: {lines}\napprove.out={approve!r} refused={refused}\n{log}"
        )
    assert status == "done", log


@pytest.mark.e2e("bwrap")
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
    _wait_for(daemon.work / "ready", "the agent")
    assert daemon.client.post("/api/work-items/w1/pause").status_code == 200
    status, _ = fut.result(WAIT_S)

    assert status == "paused"
    assert files.result.read_text().strip() == "flushed"


@pytest.mark.e2e("bwrap")
def test_launcher_killed(daemon):
    sid = "s-killed"
    fut = daemon.launch("echo ready > ready\nexec sleep 3142\n", sid)
    try:
        _wait_for(daemon.work / "ready", "the agent")
        assert _procs("sleep", "3142"), "the agent is not running"

        os.kill(_running(daemon.client, sid)["pid"], 9)

        deadline = time.monotonic() + 15
        while _procs("sleep", "3142") and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _procs("sleep", "3142"), "the sandbox outlived its killed launcher"
    finally:
        for p in _procs("sleep", "3142"):  # a failed run must not hang the fixture's teardown
            p.kill()
    fut.result(WAIT_S)
