"""A job on the machine: what the server asks it to run (`JobSpec`) and the process that runs it."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import shutil
import signal as _signal
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace

from kraft import caps
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
    deadline_s: float | None = None  # running seconds left at launch: the step's time cap
    cap: dict | None = None  # the `caps.Hit`, as a dict, that `deadline_s` enforces
    idle_s: float | None = None  # stop the command when its log is silent this long
    # The item's sandbox, which the machine builds itself and never runs the step without. Its
    # credentials are declarations (`InjectRule.to_json`): the values are the machine's own.
    sandbox: dict | None = None
    egress: dict | None = None  # `PhaseLists.to_json()`; None without `network:`
    credentials: list[dict] = field(default_factory=list)
    sentinels: dict[str, str] = field(default_factory=dict)  # env name -> the sentinel it holds
    withheld: list[str] = field(default_factory=list)  # names a sandboxed worker never sees

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
    capped: dict | None = None  # the `caps.Hit` (as a dict) that stopped it: time cap or idle limit


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
        received_at: float | None = None,
    ) -> None:
        self.spec, self.area, self.api_port = spec, Path(area), api_port
        # `deadline_s` counts from when the machine got the job (monotonic), not from the command
        self._received = time.monotonic() if received_at is None else received_at
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
        if self._expired() and s.cap is not None:  # the transfer and setup used up the cap
            return Outcome(code=137, capped=s.cap)
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
        with contextlib.suppress(protocol.Closed):  # offline: the server hears it again on `sync`
            await emit({"t": "started", "id": s.key})
        tail = asyncio.create_task(self._tail(emit))
        capped: dict | None = None
        try:
            capped = await self._until_exit()
        finally:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                # whatever it backgrounded and left running
                os.killpg(self._proc.pid, _signal.SIGTERM)
        await tail
        code = self._proc.returncode
        return Outcome(code=128 - code if code < 0 else code, capped=capped)

    def _expired(self) -> bool:
        s = self.spec
        return s.deadline_s is not None and time.monotonic() >= self._received + s.deadline_s

    async def _until_exit(self) -> dict | None:
        """Wait for the command to exit. The step's time cap and idle limit are enforced here as
        well as on the server, since a sleeping laptop enforces nothing; whichever side fires
        first stops it. Returns the `caps.Hit` (as a dict) that did, if one did."""
        s, began = self.spec, time.monotonic()
        deadline = None if s.deadline_s is None else self._received + s.deadline_s
        last_size, active_at = 0, began
        capped: dict | None = None
        while self._proc.poll() is None:
            await asyncio.sleep(self._poll)
            now = time.monotonic()
            if capped is not None:
                continue  # stopping; wait for it to go
            if deadline is not None and now >= deadline and s.cap is not None:
                capped = s.cap
            elif s.idle_s is not None:
                try:
                    size = os.stat(self.log_path).st_size
                except OSError:
                    size = last_size
                if size > last_size:
                    last_size, active_at = size, now
                elif now - active_at >= s.idle_s:
                    hit = caps.Hit(s.hook_point, "idle_minutes", round(s.idle_s / 60), 0.0)
                    capped = asdict(hit)
            if capped is not None:
                self.signal("KILL")
        return capped

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
