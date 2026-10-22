"""A `kind: os` worker against real bubblewrap: it reaches Kraft only through its
channel (`sandbox-callbacks-are-scoped-to-their-session`,
`sandbox-egress-is-deny-by-default-when-declared`).

The daemon is the real app, its lifespan entered, serving the shim's calls over
the session's channel. One session runs a fake agent that tries every way out
and writes what happened to its result file; the test reads it back by name.
`kind: os` refuses root, so this runs as a normal user only:

    KRAFT_E2E_REQUIRE=bwrap just test tests/worker/test_gates_bwrap.py --no-testmon
"""

import json
import time

import psutil
import pytest
from support.harness import git
from support.os_gates import WAIT_S, os_daemon, procs, running, wait_for
from support.session_files import own_files

from kraft.adapters import subprocess as sp
from kraft.vocab import SandboxEvent
from kraft.worker.backends import bwrap

# Shared, not copied: the run dir short enough for a unix socket path.
from worker.test_egress_docker import _unavailable, short_run  # noqa: F401

SANDBOX = {"kind": "os", "network": {"runtime": {"allow": ["x.io"]}}}
CHECKS = (
    *("shim-works", "loopback-api", "kraft-home", "daemon-run-dir", "operator-home"),
    "pid1-environ",
    *("log-untouchable", "nested-userns", "shm-capped", "approve-refused", "commit-lands"),
    *("orphan-started", "no-orphans"),
)


@pytest.fixture
def daemon(short_run, tmp_path, monkeypatch):  # noqa: F811
    if reason := bwrap.probe_host():
        _unavailable("bwrap", f"e2e: {reason}")
    yield from os_daemon(short_run, tmp_path, monkeypatch, SANDBOX)


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
check daemon-run-dir test ! -e {d.run_dirs.base}/mcp-token
check operator-home test ! -e {d.home}/.ssh/kraft-sentinel
environ=$(tr '\\0' '\\n' < /proc/1/environ) || environ=KRAFT_TEST_SECRET
! printf %s "$environ" | grep -qE 'KRAFT_TEST_SECRET|^KRAFT_HOME='
echo "pid1-environ $?" >> "$out"
python3 -c '
import os
fd = os.open("/proc/self/fd/1", os.O_RDWR)
try:
    os.ftruncate(fd, 0)
except OSError:
    pass
'
echo "log-untouchable $?" >> "$out"
command -v unshare >/dev/null && ! unshare -Ur true >/dev/null 2>&1
echo "nested-userns $?" >> "$out"
[ "$(df -Pk /dev/shm | awk 'NR==2 {{print $2}}')" -le 262144 ]; echo "shm-capped $?" >> "$out"
url=http://kraft/api/work-items/w1/gates/x/approve
curl -q -sS -m 5 -X POST -o approve.body -w '%{{http_code}}' "$url" > approve.out 2>&1
[ "$(cat approve.out)" = 403 ]; echo "approve-refused $?" >> "$out"
echo landed > landed.txt && git add landed.txt
git -c user.name=w -c user.email=w@x commit -qm landed
echo "commit-lands $?" >> "$out"
setsid sleep 3141 &
sleep 0.3; kill -0 $!; echo "orphan-started $?" >> "$out"
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
    held["loopback-api"] &= not daemon.api.hits  # the host's listener was never reached
    held["log-untouchable"] &= "LOG-MARKER-BEFORE" in log  # the host log keeps its earlier lines
    held["approve-refused"] = ran.get("approve-refused", 1) == 0 and any(
        "approve" in json.dumps(p) for p in refused
    )
    held["commit-lands"] &= git(daemon.repo, "log", "-1", "--format=%s", "kraft/t-w1") == "landed"
    held["no-orphans"] = not procs("sleep", "3141")  # the agent left one behind
    for p in procs("sleep", "3141"):
        p.kill()
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


@pytest.mark.e2e("bwrap")
def test_launcher_killed(daemon, monkeypatch):
    sid = "s-killed"

    async def no_group_kill(*_a, **_kw):  # Kraft's own group kill would end it too
        pass

    monkeypatch.setattr(sp, "_kill_group", no_group_kill)
    fut = daemon.launch("echo ready > ready\nsetsid sleep 3142 &\nwait\n", sid)
    try:
        wait_for(daemon.work / "ready", "the agent")
        deadline = time.monotonic() + 15
        while not procs("sleep", "3142") and time.monotonic() < deadline:
            time.sleep(0.1)
        assert procs("sleep", "3142"), "the agent is not running"

        wrapper = psutil.Process(running(daemon.client, sid)["pid"])
        (launcher,) = [c for c in wrapper.children() if any("os_init.py" in a for a in c.cmdline())]
        launcher.kill()

        deadline = time.monotonic() + 15
        while procs("sleep", "3142") and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not procs("sleep", "3142"), "the sandbox outlived its killed launcher"
    finally:
        for p in procs("sleep", "3142"):  # a failed run must not hang the fixture's teardown
            p.kill()
    fut.result(WAIT_S)
