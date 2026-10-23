"""A job on the machine: what the server asks it to run (`JobSpec`) and the process that runs it."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import shutil
import signal as _signal
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace

from kraft.remote import protocol
from kraft.remote.paths import PathMap
from kraft.worker.env import BASELINE, worker_env

#: Names the server's own environment owns: a job never sets them on the machine.
_SERVER_ONLY = BASELINE | {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}


@dataclass
class JobSpec:
    work_item_id: str
    session_id: str
    node_id: str
    hook_point: str
    argv: list[str]
    cwd: str
    # what the launch sets itself (KRAFT_*, TEST_ENV, config dirs, identity), not the server's
    env: dict[str, str]
    repo_env: dict[str, str]  # repos.yaml `env:` literals
    passthrough: list[str]  # repos.yaml `env_passthrough:` names, looked up on the machine
    setup_command: str | None
    branch: str
    head: str
    base: str
    identity: dict[str, str]
    server_run_dir: str
    server_worktree: str
    dirs: list[str] = field(default_factory=list)  # read-write directories to create (server paths)
    rw: list[str] = field(default_factory=list)  # read-write entries that come back (server paths)

    @property
    def key(self) -> str:
        return f"{self.work_item_id}/{self.session_id}"

    def to_frame(self) -> dict:
        return asdict(self)

    @classmethod
    def from_frame(cls, frame: dict) -> JobSpec:
        return cls(**frame)


@dataclass
class Outcome:
    code: int = 0
    rejected: str | None = None  # set when the command never started
    outcome: str | None = None  # "config_error" | "failed" with `rejected`


def job_dir(area: Path, key: str) -> Path:
    return Path(area) / "jobs" / key.replace("/", "__")


class JobRunner:
    def __init__(
        self,
        spec: JobSpec,
        *,
        area: Path,
        api_port: int | None,
        kill_grace_s: float = 5.0,
        chunk: int = 1 << 16,
        poll_s: float = 0.05,
    ) -> None:
        self.spec, self.area, self.api_port = spec, Path(area), api_port
        self.root = self.area / "items" / spec.work_item_id / "repo"
        self.pathmap = PathMap(
            ((spec.server_run_dir, str(self.area / "run")), (spec.server_worktree, str(self.root)))
        )
        self.dir = job_dir(self.area, spec.key)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / "session.log"
        self._grace, self._chunk, self._poll = kill_grace_s, chunk, poll_s
        self._proc: subprocess.Popen | None = None
        self._sent = 0
        self._escalation: asyncio.Task | None = None

    # ── environment ──

    def _env(self) -> dict[str, str]:
        s = self.spec
        own = {k: self.pathmap.text(v) for k, v in s.env.items() if k not in _SERVER_ONLY}
        own.update(s.identity)
        own["KRAFT_RUN_DIR"] = str(self.area / "run")
        own["KRAFT_HOST"] = "127.0.0.1"
        if self.api_port is not None:
            own["KRAFT_PORT"] = str(self.api_port)
        repo = SimpleNamespace(
            env={k: self.pathmap.text(v) for k, v in s.repo_env.items()},
            env_passthrough=list(s.passthrough),
        )
        env = worker_env(repo, own)
        for name in ("KRAFT_DAEMON_PID", "KRAFT_DAEMON_PORT"):
            env.pop(name, None)
        return env

    @staticmethod
    def _missing(argv0: str, env: dict[str, str]) -> str | None:
        if "/" in argv0:
            found = Path(argv0).exists() and os.access(argv0, os.X_OK)
        else:
            found = shutil.which(argv0, path=env.get("PATH"))
        if found:
            return None
        path = env.get("PATH", "")
        return f"{argv0!r} is not installed on this machine, or not on its PATH ({path})"

    # ── running ──

    async def run(self, emit: Callable[[dict], Awaitable[None]]) -> Outcome:
        s, env = self.spec, self._env()
        argv = [self.pathmap.text(a) for a in s.argv]
        if (why := self._missing(argv[0], env)) is not None:
            return Outcome(rejected=why, outcome="config_error")
        if s.setup_command:
            done = await asyncio.to_thread(
                subprocess.run,
                s.setup_command,
                shell=True,
                cwd=self.root,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
            (self.dir / "setup.log").write_text(done.stdout + done.stderr)
            if done.returncode != 0:
                tail = (done.stderr.strip() or done.stdout.strip())[-300:]
                return Outcome(
                    rejected=(
                        f"the setup command failed on this machine: {s.setup_command!r}: {tail}"
                    ),
                    outcome="failed",
                )
        with open(self.log_path, "ab") as log:
            try:
                self._proc = subprocess.Popen(
                    argv,
                    cwd=self.pathmap.text(s.cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                return Outcome(
                    rejected=f"could not start {argv[0]!r}: {exc}", outcome="config_error"
                )
        await emit({"t": "started", "id": s.key})
        tail = asyncio.create_task(self._tail(emit))
        try:
            while self._proc.poll() is None:
                await asyncio.sleep(self._poll)
        finally:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                # whatever it backgrounded and left running
                os.killpg(self._proc.pid, _signal.SIGTERM)
        await tail
        code = self._proc.returncode
        return Outcome(code=128 - code if code < 0 else code)

    async def _tail(self, emit: Callable[[dict], Awaitable[None]]) -> None:
        """Send the log from `_sent` as it grows, until the command has exited and it is drained."""
        while True:
            # sampled before reading, so the last bytes are not missed
            exited = self._proc is None or self._proc.poll() is not None
            with open(self.log_path, "rb") as fh:
                fh.seek(self._sent)
                data = fh.read(self._chunk)
            if data:
                try:
                    await emit(
                        {
                            "t": "log",
                            "id": self.spec.key,
                            "offset": self._sent,
                            "data": base64.b64encode(data).decode("ascii"),
                        }
                    )
                except protocol.Closed:
                    await asyncio.sleep(self._poll)  # offline: wait; `rewind` says where to go on
                    continue
                self._sent += len(data)
                continue
            if exited:
                return
            await asyncio.sleep(self._poll)

    def rewind(self, offset: int) -> None:
        """The server has this many bytes of the log: resend from there."""
        self._sent = offset

    async def replay(self, emit: Callable[[dict], Awaitable[None]], offset: int) -> None:
        """A finished job's log from `offset`."""
        self._sent = offset
        await self._tail(emit)

    def signal(self, sig: str) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        if sig == "INT":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, _signal.SIGINT)
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, _signal.SIGTERM)

        async def escalate() -> None:
            await asyncio.sleep(self._grace)
            if proc.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, _signal.SIGKILL)

        self._escalation = asyncio.get_running_loop().create_task(escalate())
