"""Where a sandboxed task runs: one backend per `sandbox: {kind: ...}`.

What a `sandbox:` value looks like is `policy.SandboxPolicy`'s, and which one
an item runs in is `dispatch.item_sandbox`'s. A backend is how a command
actually runs inside one, from the image check before a session exists to
cleaning up after the item is gone. `kraft.worker.sandbox` holds what every
backend shares: host git hardening and the guards on a sandboxed worktree.

A backend owns how code crosses its boundary. Docker bind-mounts the worktree,
so its `collect` has nothing to fetch; a backend with no shared filesystem
(a VM, a cloud sandbox) would copy code in and results out instead. So a
caller never reads what a sandboxed session left in the worktree before the
session's backend has had its `code_out` and `collect`: that ordering is what
lets such a backend exist without reworking every caller.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from kraft.worker.backends.docker import DockerBackend

if TYPE_CHECKING:
    from kraft.worker.refstore import RefStore


class SandboxBackend(Protocol):
    #: The `sandbox.kind` it runs, and what `worker_sessions.sandbox` records.
    kind: str

    def home(self, run_dirs, work_item_id: str) -> Path:
        """The item's own `HOME`, kept across its sessions."""

    async def probe(self, sandbox: dict, executable: str, env: dict | None) -> str | None:
        """Why a launch of `executable` cannot start, or None to go ahead:
        a session that could never start is `config_error`, not a task
        failure."""

    async def prepare(self, sandbox: dict, *, kraft_ca: Path | None = None) -> Path | None:
        """Ready what a launch in `sandbox` needs on the host side, after a
        successful `probe`: the CA bundle to hand `wrap` as `ca_bundle`, or
        None for none -- with `kraft_ca` (the Kraft CA's certificate, when
        the launch has a proxy-managed credential) trusted too. Raises
        `worker.sandbox.SandboxNotReady` saying why the launch cannot go
        ahead (`config_error`)."""

    def code_in(self, run_base: Path, cwd: Path, branch: str | None, **kw) -> RefStore | None:
        """Put the worktree's code where the session will see it. `branch`
        None is a setup command: it sees the code and publishes nothing."""

    def wrap(self, cmd: list[str], cwd, sandbox: dict, results_dir, env=None, **kw) -> list[str]:
        """The argv that runs `cmd` inside the sandbox; `ca_bundle=` is
        what `prepare` returned, `sentinels=` each proxy-managed
        credential's variable and what it holds in the value's place."""

    def oneshot(self, sandbox: dict, cwd, home):
        """Short commands in the session's image, asked as its session would
        but with no network, stdin attached: `.argv()` per command (the
        argv up to it), `await .close()` after. `worker.sandbox.SandboxNotReady`
        where it cannot."""

    def client_cwd(self, session_id: str) -> Path | None:
        """The directory to start the wrapped command's client in, made for
        the session and gone after `close`; None for the task's own `cwd`."""

    def launch_failed(self, cidfile: Path, returncode: int | None = None) -> bool:
        """True when the sandbox itself never started, so the command never
        ran: `cidfile` is what the launch was given to write, `returncode`
        what it exited with."""

    def code_out(self, refs: RefStore, session_id: str) -> str | None:
        """Publish what the session committed; why not, when it could not."""

    def code_out_item(self, run_base: Path, work_item_id: str) -> list[str]:
        """`code_out` for every session of the item no launch is left to
        publish (adopted after a restart, or found dead)."""

    async def collect(self, session_id: str, result_path: Path) -> None:
        """Bring the session's result file back to `result_path`. A no-op
        for a session this backend never ran."""

    async def oom_killed(self, session_id: str) -> str | None:
        """The memory limit the session's sandbox killed it at, or None: it
        was not, or this backend cannot tell (a session it never ran). Asked
        after the command exits and before `close`, which removes the
        evidence; the runtime's own word only, never an exit code."""

    async def close(self, session_id: str) -> None:
        """Stop and remove the session's sandbox, best-effort and bounded. A
        no-op for a session this backend never ran."""

    async def egress_transport(self) -> str:
        """How this backend's sessions reach the egress channel, asked before
        it opens: "unix", a socket per session, or "tls", the daemon's one
        mTLS listener (a runtime in a VM cannot connect to a host socket).
        Raises `worker.sandbox.SandboxNotReady` when it cannot tell."""

    async def open_session(self, session_id: str, sandbox: dict, sock_path: Path | None) -> dict:
        """Ready the session's egress route under `sandbox['network']`
        before it starts, to the channel listening at `sock_path`
        (`worker.channel`), None under the "tls" transport: docker starts a
        relay the worker joins. Returns
        the environment the launch must add (its proxy, and a PATH with the
        `kraft` shim first); `{}` for a sandbox
        with no `network`. Raises `worker.sandbox.SandboxNotReady` when the
        route cannot be made (`config_error`), never opens egress instead."""

    async def close_session(self, session_id: str) -> None:
        """Undo `open_session`, best-effort and bounded like `close`, and a
        no-op for a session with no route. The channel itself is closed by
        whoever opened it (`run_task`, reattach), not by the backend."""

    async def sweep(self, keep_sessions: Iterable[str]) -> list[str]:
        """Remove what this Kraft home started that no session in
        `keep_sessions` owns, returning what went."""

    def release(self, run_dirs, worktree: Path, work_item_id: str) -> None:
        """Forget what the item kept beside its worktree. A no-op for an
        item this backend never ran."""

    async def health(self, sandbox: dict) -> tuple[bool, str]:
        """Doctor's row: can a sandbox like this run here at all?"""


_BACKENDS: dict[str, SandboxBackend] = {b.kind: b for b in (DockerBackend(),)}


def for_sandbox(sandbox: Mapping) -> SandboxBackend:
    """The backend that runs `sandbox` (a `SandboxPolicy` dumped to a dict,
    the form `dispatch.item_sandbox` hands out)."""
    return _BACKENDS[sandbox["kind"]]


def every() -> tuple[SandboxBackend, ...]:
    return tuple(_BACKENDS.values())


def for_session(kind: str | None) -> tuple[SandboxBackend, ...]:
    """The backends a session row may have run in: the one it recorded, or
    every one for a row that recorded none -- unsandboxed, or written before
    the column existed. Each backend's session calls are no-ops for a
    session it never ran, which is what makes asking all of them safe; so is
    a kind no backend runs any more."""
    backend = _BACKENDS.get(kind) if kind is not None else None
    return (backend,) if backend is not None else every()
