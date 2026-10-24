"""A sandbox backend that shares no filesystem with Kraft, for tests of the seam."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from kraft import ids
from kraft.worker.sandbox import SandboxNotReady, SandboxRefused


class Remote:
    """A backend sharing no filesystem with Kraft: the command writes its
    result somewhere of its own, and only `collect` brings it back."""

    kind = "remote"

    def __init__(self, tmp_path: Path):
        self.outbox = tmp_path / "remote-outbox"
        self.outbox.mkdir()
        self.closed: list[str] = []
        #: What `prepare` hands back (or, a str, the problem it raises), and
        #: the `ca_bundle` each `wrap` was given.
        self.prepared: Path | str | None = None
        self.wrapped_with: list = []
        #: Every session call, in order, and what `open_session` answers.
        self.calls: list[str] = []
        self.route = {"HTTPS_PROXY": "http://127.0.0.1:3128"}
        self.transport = "unix"
        self.wrapped_env: list[dict] = []
        #: What `owner_refusal` answers.
        self.foreign: str | None = None
        #: The reason `wrap` raises `SandboxRefused` with, when set.
        self.refuse: str | None = None
        #: Every backend method called, in order: what a step that runs elsewhere never touches.
        self.touched: list[str] = []
        #: What `probe` answers (a refusal), and what `oom_killed` answers.
        self.unrunnable: str | None = None
        self.oom = None
        #: The ref stores `code_in` hands back.
        self.refs: tuple = ()

    def home(self, run_dirs, work_item_id):
        return run_dirs.base / "remote-home" / work_item_id

    async def owner_refusal(self, run_dirs, cwd, work_item_id, result_path, **kw):
        self.touched.append("owner_refusal")
        return self.foreign

    async def probe(self, sandbox, executable, env):
        self.touched.append("probe")
        self.probed_env = env
        return self.unrunnable

    async def prepare(self, sandbox, *, kraft_ca=None):
        self.touched.append("prepare")
        self.kraft_ca = kraft_ca
        if isinstance(self.prepared, str):
            raise SandboxNotReady(self.prepared)
        return self.prepared

    def code_in(self, run_base, cwd, branch, **kw):
        self.touched.append("code_in")
        return self.refs

    def wrap(self, cmd, cwd, sandbox, results_dir, env=None, *, session_id, work_item_id, **kw):
        self.touched.append("wrap")
        if self.refuse:
            raise SandboxRefused(self.refuse)
        self.wrapped_with.append(kw.get("ca_bundle"))
        self.wrapped_env.append(env)
        # Off the event loop: a backend's wrap may walk the trees it mounts.
        try:
            asyncio.get_running_loop()
            self.calls.append("wrap on the event loop")
        except RuntimeError:
            self.calls.append("wrap")
        sent = self.outbox / f"{ids.qualified(work_item_id, session_id)}.json"
        return ["env", f"KRAFT_RESULT_PATH={sent}", *cmd]

    def client_cwd(self, session_id, *, work_item_id):
        return None

    def launch_failed(self, cidfile, returncode=None):
        return False

    def code_out(self, refs, session_id, *, work_item_id):
        self.touched.append("code_out")
        return []

    def code_out_item(self, run_base, work_item_id):
        return []

    async def collect(self, session_id, result_path, *, work_item_id):
        self.touched.append("collect")
        sent = self.outbox / f"{ids.qualified(work_item_id, session_id)}.json"
        if sent.exists():
            shutil.copyfile(sent, result_path)

    async def oom_killed(self, session_id, *, work_item_id):
        self.touched.append("oom_killed")
        return self.oom

    async def close(self, session_id, *, work_item_id):
        self.touched.append("close")
        self.closed.append(session_id)
        self.calls.append("close")

    async def egress_transport(self):
        self.touched.append("egress_transport")
        return self.transport

    async def open_session(self, session_id, sandbox, sock_path, *, work_item_id):
        self.touched.append("open_session")
        self.calls.append("open_session")
        self.sock_path = sock_path
        return self.route if sandbox.get("network") else {}

    async def close_session(self, session_id, *, work_item_id):
        self.touched.append("close_session")
        self.calls.append("close_session")

    async def sweep(self, keep_sessions):
        return []

    def release(self, run_dirs, worktree, work_item_id):
        pass

    async def health(self, sandbox, *, refresh=True):
        return True, "remote"
