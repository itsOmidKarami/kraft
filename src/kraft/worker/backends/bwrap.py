"""The bubblewrap command line of a `kind: os` sandbox.

`bwrap_argv` returns the whole thing: the launcher (`os_init.py launch`, on
the host), bwrap with every mount and variable, and the init (`os_init.py
init`) that runs the command inside. Nothing secret is on it: a secret crosses
by name (`--keep`), through the launcher's environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kraft import item_paths
from kraft.paths import RunDirs, kraft_home
from kraft.worker import channel as _channel
from kraft.worker import host_view, os_init, os_seccomp, shim
from kraft.worker import mounts as _mounts
from kraft.worker import refstore as _refstore
from kraft.worker.backends.docker import _ONESHOT_SH, ONESHOT_HOME_RO, OomKill, _run_dirs
from kraft.worker.ca import write_whole
from kraft.worker.mounts import RELAY_PORT, RELAY_PROXY_ENV, Mount
from kraft.worker.refstore import RefStore
from kraft.worker.sandbox import (
    _HARDENED_GIT_CONFIG,
    FORWARDED_ENV,
    SandboxNotReady,
    SandboxRefused,
    _pin,
)

TMP_BYTES = 2147483648
SHM_BYTES = 268435456
#: The worker baseline's names that cross (the CA ones come from `ca_names`).
_BASELINE_NAMES = ("LANG", "LC_ALL", "TZ", "TERM", "USER", "LOGNAME", "SHELL")


_BWRAP_FLAGS = (
    *("--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent"),
    *("--cap-drop", "ALL", "--as-pid-1"),
)


def _once(path: Path, data: bytes, mode: int) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        write_whole(path, data, mode)
    return path


def seccomp_file(run_dirs: RunDirs) -> Path:
    arch = os_seccomp.machine()
    try:
        data = os_seccomp.build(arch)
    except ValueError as exc:
        raise SandboxRefused(str(exc)) from exc
    name = f"seccomp-{arch}-{hashlib.sha256(data).hexdigest()[:12]}.bpf"
    return _once(run_dirs.base / "os-sandbox" / name, data, 0o600)


def system_args() -> list[str]:
    args = [a for p in ("/usr", "/etc") for a in ("--ro-bind", p, p)]
    for p in ("/bin", "/sbin", "/lib", "/lib64"):
        if os.path.islink(p):
            args += ["--symlink", os.readlink(p), p]
        elif os.path.isdir(p):
            args += ["--ro-bind", p, p]
    return [
        *args,
        *("--proc", "/proc", "--dev", "/dev"),
        *("--size", str(TMP_BYTES), "--tmpfs", "/tmp"),
        *("--size", str(SHM_BYTES), "--tmpfs", "/dev/shm"),
        *("--symlink", "/tmp", "/var/tmp"),
    ]


def _bind(src: Path | str, dst: Path | str | None = None, *, ro: bool = True) -> list[str]:
    return ["--ro-bind" if ro else "--bind", str(src), str(dst or src)]


def bwrap_argv(
    cmd: list[str],
    cwd: str | Path,
    sandbox: Mapping,
    session: list[Mount],
    *,
    env: Mapping[str, str],
    passthrough: Iterable[str],
    home: Path | None,
    workdir: Path | None,
    sock_path: Path | None,
    started: Path | None,
    search_path: str,
    run_dirs: RunDirs,
    agent: str | None = None,
    mode: Literal["launch", "oneshot"] = "launch",
) -> list[str]:
    raw_cwd, cwd = Path(cwd), Path(cwd).resolve()
    # The worktree is bound at its resolved path, so the workdir is too.
    rel = Path()
    if workdir is not None:
        base = raw_cwd if Path(workdir).is_relative_to(raw_cwd) else cwd
        rel = Path(workdir).relative_to(base)
    operator_home = Path(os.environ.get("HOME") or Path.home())
    kh = kraft_home()
    if (bwrap := shutil.which("bwrap")) is None:
        raise SandboxRefused("bubblewrap is not installed (apt install bubblewrap)")
    try:
        interp, interp_roots = host_view.init_interpreter()
        exposed = host_view.expose_roots(sandbox.get("expose") or (), operator_home, kh)
        where = (workdir or cwd, cwd, operator_home, kh)
        # The agent CLI first: what it shows is visible to the command too.
        first = (
            host_view.resolve(agent, search_path, *where, visible=exposed)
            if agent
            else host_view.Resolved(cmd[0], (), ())
        )
        resolved = host_view.resolve(cmd[0], search_path, *where, visible=(*first.roots, *exposed))
        ca = host_view.ca_mounts(os.environ, operator_home, kh)
    except (host_view.Refused, host_view.NotFound) as exc:
        raise SandboxRefused(str(exc)) from exc

    keep = dict.fromkeys((*passthrough, *FORWARDED_ENV, *_BASELINE_NAMES, *host_view.ca_names()))
    init = [str(interp), "-I", "-S", os_init.__file__]
    argv = [
        *init,
        mode,
        *("--keep", ",".join(keep)),
        *("--seccomp-file", str(seccomp_file(run_dirs))),
        # os_init refuses a oneshot that records a start.
        *(("--started", str(started)) if started and mode == "launch" else ()),
        "--",
        os.path.abspath(bwrap),
        *_BWRAP_FLAGS,
        *system_args(),
    ]
    # Links first: bwrap makes a real directory of any ancestor it binds into, and a
    # later --symlink on it dies.
    for link, target in dict.fromkeys((*first.links, *resolved.links)):
        argv += ["--symlink", str(target), str(link)]
    for root in (*interp_roots, Path(os_init.__file__)):
        argv += _bind(root)
    roots = list(dict.fromkeys((*first.roots, *resolved.roots, *exposed)))
    for root in roots:
        argv += _bind(root)
    # Not /dev/null: bwrap mounts binds nodev, so it would read as permission denied.
    empty = _once(run_dirs.base / "os-sandbox" / "empty", b"", 0o444)
    for hit in dict.fromkeys(h for root in roots for h in host_view.credential_shadows(root)):
        argv += _bind(empty, hit)
    for ca_path in ca:
        argv += _bind(ca_path)
    for m in sorted(session, key=lambda m: len(m.dst.parts)):
        argv += _bind(m.src, m.dst, ro=m.ro)
    argv += _bind(shim.HOST_DIR, shim.CONTAINER_DIR)
    if sock_path is not None:
        argv += _bind(sock_path.parent)
    sandbox_home = home or Path("/tmp/home")
    if home is not None:
        argv += _bind(home, ro=False)
    pins: dict[str, str] = {}
    _pin(pins, _HARDENED_GIT_CONFIG)
    for name, value in {"HOME": str(sandbox_home), **pins, **env}.items():
        argv += ["--setenv", name, value]
    argv += ["--chdir", str(cwd / rel)]
    argv += [*init, "init"]
    if sock_path is not None:
        argv += ["--sock", str(sock_path), "--port", str(RELAY_PORT)]
    return [*argv, "--", *cmd]


#: What `bwrap --help` must list: the flags Kraft passes beyond bubblewrap 0.4's.
_NEEDED_FLAGS = (
    "--size",
    "--disable-userns",
    "--as-pid-1",
    "--cap-drop",
    "--seccomp",
    "--die-with-parent",
)
#: `(path under /proc/sys, the value that blocks, the fix)`, read only when the trial fails.
_NAMESPACE_KNOBS = (
    ("user/max_user_namespaces", "0", "sysctl user.max_user_namespaces=15000"),
    ("kernel/unprivileged_userns_clone", "0", "sysctl kernel.unprivileged_userns_clone=1"),
    (
        "kernel/apparmor_restrict_unprivileged_userns",
        "1",
        "install bubblewrap's AppArmor profile, or "
        "sysctl kernel.apparmor_restrict_unprivileged_userns=0",
    ),
)
_PROBE_TTL = 60.0
_probed: tuple[float, str | None] | None = None


def _sysctl(path: str) -> str | None:
    try:
        return Path("/proc/sys", path).read_text().strip()
    except OSError:
        return None


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str] | str:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)


def _probe() -> str | None:
    if sys.platform != "linux":
        return "kind: os runs on Linux only in this release"
    if os.geteuid() == 0:
        return "kind: os does not run while Kraft runs as root"
    if (arch := os_seccomp.machine()) not in ("x86_64", "aarch64"):
        return f"kind: os supports x86_64 and aarch64, not {arch}"
    if (bwrap := shutil.which("bwrap")) is None:
        return "bubblewrap is not installed (apt install bubblewrap)"
    if missing := [n for n in ("sh", "curl") if shutil.which(n) is None]:
        return f"{' and '.join(missing)} is not installed on this host (the `kraft` shim needs it)"
    helped = _run([bwrap, "--help"])
    if isinstance(helped, str):
        return f"bwrap --help failed: {helped}"
    text = helped.stdout + helped.stderr
    if lacking := [f for f in _NEEDED_FLAGS if f not in text]:
        return f"this bubblewrap lacks {', '.join(lacking)}: install a newer bubblewrap"
    # shortcut: the trial omits --seccomp, so a filter the kernel rejects shows up at launch.
    trial = _run([os.path.abspath(bwrap), *_BWRAP_FLAGS, *system_args(), "true"])
    if not isinstance(trial, str) and trial.returncode == 0:
        return None
    for path, blocking, fix in _NAMESPACE_KNOBS:
        if _sysctl(path) == blocking:
            return f"bubblewrap cannot create a user namespace here: {fix}"
    err = trial if isinstance(trial, str) else trial.stderr.strip() or f"exit {trial.returncode}"
    return f"bubblewrap cannot start a sandbox here: {err}"


def probe_host(*, refresh: bool = False) -> str | None:
    """Why `kind: os` cannot run on this machine, with its fix, or None.
    Cached for a minute."""
    # shortcut: no lock, so concurrent cold callers may each probe; upgrade if the
    # trial run ever gets slow enough to matter.
    global _probed
    if refresh or _probed is None or time.monotonic() - _probed[0] > _PROBE_TTL:
        _probed = (time.monotonic(), _probe())
    return _probed[1]


@dataclass
class OsOneshot:
    """`worker.backends.docker.Oneshot` for bwrap: nothing outlives a client,
    since `--die-with-parent` ends the sandbox with it."""

    prefix: list[str]

    def argv(self) -> list[str]:
        return list(self.prefix)

    async def close(self) -> None:
        pass


class OsBackend:
    """`kraft.worker.backends.SandboxBackend` over bubblewrap (Linux)."""

    kind = "os"

    def home(self, run_dirs, work_item_id: str) -> Path:
        return item_paths.sandbox_home(run_dirs, work_item_id)

    async def owner_refusal(self, run_dirs, cwd, work_item_id, result_path, **kw) -> str | None:
        return None

    async def probe(self, sandbox: dict, executable: str, env: dict | None) -> str | None:
        if problem := await asyncio.to_thread(probe_host):
            return problem
        home, cwd, kh = Path(os.environ.get("HOME") or Path.home()), Path.cwd(), kraft_home()
        search = os.environ["PATH"]  # the daemon's, as `open_session` builds the session's
        # shortcut: probe cannot see the worktree, so a path-named executable (./gradlew,
        # a worktree path) is checked at `wrap` only; upgrade if probe is given the checkout.
        if "/" in executable:
            return None
        try:
            exposed = host_view.expose_roots(sandbox.get("expose") or (), home, kh)
            host_view.resolve(executable, search, cwd, cwd, home, kh, visible=exposed)
        except (host_view.Refused, host_view.NotFound) as exc:
            return str(exc)
        return None

    async def prepare(self, sandbox: dict, *, kraft_ca: Path | None = None) -> Path | None:
        return None

    def code_in(self, run_base: Path, cwd: Path, branch: str | None, **kw) -> Sequence[RefStore]:
        return _refstore.prepare_stores(run_base, cwd, branch, **kw)

    def wrap(
        self,
        cmd: list[str],
        cwd: str | Path,
        sandbox: dict,
        results: Iterable[str | Path] | None,
        env: dict | None = None,
        *,
        session_id: str | None = None,
        work_item_id: str | None = None,
        result_path: str | Path | None = None,
        rw_files: Iterable[str | Path] = (),
        rw_dirs: Iterable[str | Path] = (),
        cidfile: str | Path | None = None,
        refs: Sequence[RefStore] = (),
        workdir: str | Path | None = None,
        home: str | Path | None = None,
        passthrough: Iterable[str] = (),
        ro_paths: Iterable[str | Path] = (),
        ca_bundle: str | Path | None = None,  # unused: the host's trust store applies
        sentinels: dict[str, str] | None = None,  # unused: `kind: os` refuses credentials
    ) -> list[str]:
        if session_id is not None and work_item_id is None:
            raise TypeError("wrap: a session's sandbox is found by its work item too")
        sock_path = None
        if sandbox.get("network") and session_id is not None:
            if (registry := _channel.current()) is None:
                raise SandboxRefused(
                    "a kind: os sandbox reaches out only through the Kraft server's egress "
                    "channel, and this process has none"
                )
            sock_path = registry.socket_path(session_id, work_item_id=work_item_id)
        env = dict(env or {})
        # shortcut: wrap is not told the harness's CLI, so no agent= and only cmd[0]'s
        # roots are shown; pass it when wrap learns the executable.
        return bwrap_argv(
            cmd,
            cwd,
            sandbox,
            _mounts.session_mounts(
                Path(cwd),
                results,
                result_path=result_path,
                rw_files=rw_files,
                rw_dirs=rw_dirs,
                refstores=refs,
                ro_paths=ro_paths,
            ),
            env=env,
            passthrough=passthrough,
            home=Path(home) if home is not None else None,
            workdir=Path(workdir) if workdir is not None else None,
            sock_path=sock_path,
            started=Path(cidfile) if cidfile is not None else None,
            search_path=env.get("PATH") or os.environ["PATH"],
            run_dirs=_run_dirs(),
        )

    def oneshot(
        self, sandbox: dict, cwd: str | Path, home: str | Path, executables: Iterable[str] = ()
    ) -> OsOneshot:
        if problem := probe_host():
            raise SandboxNotReady(problem)
        cwd, home = Path(cwd).resolve(), Path(home)
        mounts = [Mount(cwd, cwd, True)]
        if home.is_dir():
            mounts.append(Mount(home, Path(ONESHOT_HOME_RO), True))
        search = os.environ["PATH"]
        try:
            prefix = bwrap_argv(
                ["sh", "-c", _ONESHOT_SH, ONESHOT_HOME_RO],
                cwd,
                sandbox,
                mounts,
                env={"PATH": f"{shim.CONTAINER_DIR}:{search}"},
                passthrough=(),
                home=None,  # HOME is then /tmp/home, on the tmpfs
                workdir=None,
                sock_path=None,
                started=None,
                search_path=search,
                run_dirs=_run_dirs(),
                # shortcut: one agent CLI is resolved, the first; callers pass one.
                agent=next(iter(executables), None),
                mode="oneshot",
            )
        except SandboxRefused as exc:
            raise SandboxNotReady(str(exc)) from exc
        return OsOneshot(prefix)

    def client_cwd(self, session_id: str, *, work_item_id: str) -> Path | None:
        return None

    def launch_failed(self, cidfile: Path, returncode: int | None = None) -> bool:
        try:
            return cidfile.read_text().strip() != "started"
        except OSError:
            return True

    def code_out(self, refs: Sequence[RefStore], session_id: str) -> list[str]:
        return _refstore.sync_all(refs, session_id)

    def code_out_item(self, run_base: Path, work_item_id: str) -> list[str]:
        return []  # Docker's already runs for every backend's sessions

    async def collect(self, session_id: str, result_path: Path, *, work_item_id: str) -> None:
        pass

    async def oom_killed(self, session_id: str, *, work_item_id: str) -> OomKill | None:
        return None

    async def close(self, session_id: str, *, work_item_id: str) -> None:
        pass

    async def egress_transport(self) -> str:
        return "unix"

    async def open_session(
        self, session_id: str, sandbox: dict, sock_path: Path | None, *, work_item_id: str
    ) -> dict:
        return {**RELAY_PROXY_ENV, "PATH": f"{shim.CONTAINER_DIR}:{os.environ['PATH']}"}

    async def close_session(self, session_id: str, *, work_item_id: str) -> None:
        pass

    async def sweep(self, keep_sessions: Iterable[tuple[str, str]]) -> list[str]:
        return []

    def release(self, run_dirs, worktree: Path, work_item_id: str) -> None:
        pass  # Docker's already runs for every backend's sessions

    async def health(self, sandbox: dict, *, refresh: bool = True) -> tuple[bool, str]:
        if problem := await asyncio.to_thread(probe_host, refresh=refresh):
            return False, problem
        out = await asyncio.to_thread(_run, [shutil.which("bwrap") or "bwrap", "--version"])
        version = "" if isinstance(out, str) else out.stdout.strip().removeprefix("bubblewrap ")
        return True, f"bubblewrap {version}".rstrip()
