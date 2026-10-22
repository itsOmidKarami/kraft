"""What `test_gates_bwrap.py` and `test_gates_seatbelt.py` share: the real app
serving a `kind: os` session over its channel, and the pollers around it."""

from __future__ import annotations

import http.server
import stat
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import psutil

from kraft import events
from kraft.adapters import subprocess as sp
from support.api import _client, _force_node
from support.harness import git, make_repo
from support.permissions import PATH, seed_session, templates

SECRET = "kraft-test-secret-value"
WAIT_S = 60  # under pyproject's per-test timeout, so a finally still runs


class Health(http.server.BaseHTTPRequestHandler):
    """A loopback listener the sandbox must not reach. One class per listener
    (`hits` is the class's): `Health.listener()`."""

    hits: list = []

    def do_GET(self):
        self.hits.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_):
        pass

    @classmethod
    def listener(cls) -> tuple[type[Health], http.server.ThreadingHTTPServer]:
        own = type("Health", (cls,), {"hits": []})
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), own)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return own, srv


def os_daemon(short_run, tmp_path, monkeypatch, sandbox: dict) -> Iterator[SimpleNamespace]:
    """`SimpleNamespace(launch, ...)`: `launch(script, sid)` starts `run_task` of
    `script` (an agent script body) in a `kind: os` `sandbox` on item `w1`, whose
    worktree is a real linked worktree of a repo, and returns a future of
    `(status, events)`. The host is planted with what the sandbox must not
    reach: a file under the Kraft home and one under the operator's home, and
    two loopback listeners (`api` and `other`, with their ports)."""
    home = tmp_path / "home"
    kraft_home = home / ".kraft"
    for planted in (kraft_home / "run" / "mcp-token", home / ".ssh" / "kraft-sentinel"):
        planted.parent.mkdir(parents=True)
        planted.write_text("secret\n")
    monkeypatch.setenv("HOME", str(home))
    tpl = templates(short_run.base, monkeypatch)
    # After `templates`, which points KRAFT_HOME at a fake harness home.
    monkeypatch.setenv("KRAFT_HOME", str(kraft_home))
    # In the daemon's environment and passed through to nothing. `worker_env` drops it
    # before the launcher starts, so `KRAFT_HOME`, which does reach the launcher, is
    # what shows the launcher filtering.
    monkeypatch.setenv("KRAFT_TEST_SECRET", SECRET)
    # Beside the Kraft home's folder, never under it, and not named `bin`.
    agents = tmp_path / "agents"
    agents.mkdir()
    api, api_srv = Health.listener()
    other, other_srv = Health.listener()
    repo = make_repo(tmp_path)
    with _client(short_run.base, monkeypatch, templates_dir=tpl) as client:
        app = client.app
        seed_session()
        # The daemon's own run dir (databases, token) is not under the Kraft home here.
        (app.state.run_dirs.base / "mcp-token").write_text("secret\n")
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
                    sandbox=sandbox,
                )
                return status, app.state.db.read(lambda c: events.read_after(c, 0, "w1"))

            return client.portal.start_task_soon(run)

        ns = SimpleNamespace(
            client=client,
            launch=launch,
            work=work,
            repo=repo,
            run_dirs=app.state.run_dirs,
            home=home,
            kraft_home=kraft_home,
            api=api,
            api_port=api_srv.server_address[1],
            other=other,
            other_port=other_srv.server_address[1],
        )
        yield ns
    api_srv.shutdown()
    other_srv.shutdown()


def running(client, sid: str) -> dict:
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


def wait_for(path: Path, what: str):
    deadline = time.monotonic() + WAIT_S
    while not path.exists():
        assert time.monotonic() < deadline, f"{what} never appeared"
        time.sleep(0.1)


def procs(*argv: str) -> list[psutil.Process]:
    return [p for p in psutil.process_iter(["cmdline"]) if p.info["cmdline"] == list(argv)]
