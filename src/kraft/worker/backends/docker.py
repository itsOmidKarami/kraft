"""The docker backend (Kraft-rki): a sandboxed command runs in `docker run`,
one container per session, with the worktree bind-mounted at its host path.

The mounts (`docker_argv`) keep the container out of everything it has no
business writing; `kraft.worker.sandbox` holds the other, load-bearing half --
host git never runs what a worker plants -- and the guards every backend
shares. `kraft.worker.refstore` is how this backend keeps a worker's refs out
of the repository: code goes in and comes out through it, not through the
mount.

Read docs/superpowers/specs/2026-09-13-worker-sandbox-docker-design.md for
the design this implements.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kraft.config import ConfigError
from kraft.paths import default_templates_dir, kraft_home
from kraft.worker import refstore as _refstore
from kraft.worker.backends import docker_forward as _forward
from kraft.worker.sandbox import (
    _HARDENED_GIT_CONFIG,
    _RW_SHADOW_DIRS,
    FORWARDED_ENV,
    SHADOW_DIRS,
    SHADOW_FILES,
    SandboxNotReady,
    _gitdir_of,
    _pin,
    linked_gitdirs,
)

if TYPE_CHECKING:
    from kraft.worker.refstore import RefStore


def container_name(session_id: str) -> str:
    """The `--name` a sandboxed session's container runs under.

    A handle a caller can `docker kill`/`docker rm -f` by, independent of
    whatever became of the `docker run` client's own pid -- that client is
    the process Kraft's group-kill signals, but the container itself is
    parented by the docker daemon, not that group, and does not die with it.
    `session_id` is already a Kraft-minted id (safe for docker's
    `[a-zA-Z0-9_.-]` name charset); prefixed so a stray `kraft-*` container is
    obviously this tool's to clean up.
    """
    return f"kraft-{session_id}"


#: The label every Kraft container carries, valued with the resolved
#: `KRAFT_HOME`: what `sweep_orphans` lists by, so one Kraft never reaps
#: another's containers (a dev instance beside an installed one).
HOME_LABEL = "kraft.home"


def home_label() -> str:
    return f"{HOME_LABEL}={kraft_home().resolve()}"


class SandboxRefused(RuntimeError):
    """This host cannot run a sandbox the way it is set up: a stop for a
    person, never a task failure."""


#: Every limit a runtime may enforce: `resources`' three, and `swap`, which
#: makes a memory limit a limit (without `--memory-swap` docker lets a
#: container swap as much again).
LIMIT_ORDER = ("cpu", "memory", "swap", "pids")
LIMITS = frozenset(LIMIT_ORDER)
#: `--pids-limit` when `resources.pids` is unset: bounds a fork bomb, and no
#: build a worker runs comes near it.
DEFAULT_PIDS = 4096


@dataclass(frozen=True)
class Runtime:
    """How this machine runs a container, detected once (`runtime()`): the
    same flags mean different things to rootless docker, rootless podman and
    an SELinux-enforcing host, and each gets them wrong differently."""

    cli: str = "docker"
    #: What `cli` really is: `podman` behind a `docker` name (the
    #: podman-docker shim) behaves as podman in every way that matters here.
    #: None: what `cli` says.
    engine: str | None = None
    #: The daemon (docker) or the CLI (podman) runs as the operator, so
    #: container uids map into the operator's subordinate range.
    rootless: bool = False
    #: None unless SELinux enforces; then `sandbox.yaml`'s `relabel` or
    #: `disable`, or `refuse` when it says neither.
    selinux: str | None = None
    #: Which of `LIMITS` this runtime can enforce: a cgroup controller it
    #: lacks (cgroup v1 rootless, a v2 host that delegates none) makes docker
    #: *drop* the limit with only a warning, so this is asked, not assumed.
    limits: frozenset[str] = LIMITS

    def limit_args(self, resources: dict | None) -> list[str]:
        """`--cpus`, `--memory` and `--pids-limit` for a sandbox's
        `resources`, or `SandboxRefused` for one this runtime cannot enforce
        (`sandbox-limits-never-silently-drop`). The default pids limit is
        left out where pids cannot be limited: refusing it would refuse every
        launch on such a host, and nobody asked for it."""
        resources = resources or {}
        if problem := self.limits_refusal(resources):
            raise SandboxRefused(problem)
        args = []
        if (cpu := resources.get("cpu")) is not None:
            args.append(f"--cpus={cpu:g}")
        if (memory := resources.get("memory")) is not None:
            args.append(f"--memory={memory}")
            if "swap" in self.limits:
                args.append(f"--memory-swap={memory}")
        pids = resources.get("pids")
        if pids is None and "pids" in self.limits:
            pids = DEFAULT_PIDS
        if pids is not None:
            args.append(f"--pids-limit={pids}")
        return args

    def limits_refusal(self, resources: dict | None) -> str | None:
        """Why a launch under `resources` cannot start here, or None."""
        wanted = [n for n in ("cpu", "memory", "pids") if (resources or {}).get(n) is not None]
        missing = [n for n in wanted if n not in self.limits]
        if not missing:
            return None
        return (
            f"{self.describe()} cannot enforce the sandbox's {' and '.join(missing)} "
            f"limit ({'; '.join(f'{n}: {resources[n]}' for n in missing)}): the cgroup "
            "controller is not available to it, and the runtime would run the container "
            "without the limit. Run it on cgroup v2 and, for a rootless runtime, delegate "
            "the controllers to your user (systemd `Delegate=cpu memory pids` for "
            "user@.service); or remove the limit from the sandbox's `resources`"
        )

    def describe_limits(self) -> str:
        """Doctor's account of what `resources` can do on this runtime."""
        if not self.limits & {"cpu", "memory", "pids"}:
            return (
                "no resource limits (no cgroup controllers: needs cgroup v2 with "
                "Delegate= for a rootless runtime)"
            )
        return "limits " + ", ".join(n for n in LIMIT_ORDER if n in self.limits)

    def user_args(self) -> list[str]:
        """Who the container runs as, so what it writes into the worktree is
        the operator's. Rootless docker maps container root to the operator
        (any other uid lands on a subordinate one); rootless podman needs
        `keep-id` for the operator's own uid to exist inside at all."""
        if self.rootless and not self.podman:
            return ["-u", "0:0"]
        user = ["-u", f"{os.getuid()}:{os.getgid()}"]
        if self.rootless and self.podman:
            return ["--userns=keep-id", *user]
        return user

    @property
    def podman(self) -> bool:
        return (self.engine or self.cli) == "podman"

    def refusal(self) -> str | None:
        if self.selinux != "refuse":
            return None
        return (
            "SELinux is enforcing on this host and denies a container's bind mounts, "
            "and sandbox.yaml does not say how to get past it: set `selinux: relabel` "
            "(labels the worktree and repository files for containers, once) or "
            "`selinux: disable` (turns SELinux separation off for Kraft's containers "
            "only). See the sandbox.yaml reference"
        )

    def describe(self) -> str:
        parts = [self.cli, *(["(podman)"] if self.podman and self.cli != "podman" else [])]
        parts += ["rootless"] if self.rootless else []
        if self.selinux in ("relabel", "disable"):
            parts.append(f"SELinux {self.selinux}")
        return " ".join(parts)


def _selinux_enforcing() -> bool:
    try:
        return Path("/sys/fs/selinux/enforce").read_text().strip() == "1"
    except OSError:
        return False


def _run(*argv: str) -> str | None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except OSError, subprocess.TimeoutExpired:
        return None
    return done.stdout if done.returncode == 0 else None


def _ask(cli: str) -> tuple[str, bool, bool]:
    """`(engine, rootless, labels)`, asked of the runtime itself: what it
    really is, whether it runs as the operator, and whether it applies
    SELinux labels to containers (docker does only when its daemon was
    started with SELinux support). Anything inconclusive (no daemon, an old
    CLI) reads as a rootful, unlabelled docker, which is what every launch
    assumed before."""
    engine = "podman" if "podman" in (_run(cli, "--version") or "").lower() else "docker"
    if engine == "docker":
        options = _run(cli, "info", "--format", "{{json .SecurityOptions}}") or ""
        return engine, "name=rootless" in options, "name=selinux" in options
    security = (
        _run(
            cli, "info", "--format", "{{.Host.Security.Rootless}} {{.Host.Security.SELinuxEnabled}}"
        )
        or ""
    ).split()
    return engine, security[:1] == ["true"], security[1:2] == ["true"]


def _ask_limits(cli: str, engine: str) -> frozenset[str]:
    """Which of `LIMITS` the runtime says it can enforce: docker's `info`
    booleans (rootless docker reports what is delegated to it), podman's
    cgroup controllers (rootless podman on cgroup v1 lists none). An answer
    that does not parse -- no daemon, an old CLI -- reads as every limit,
    what a launch assumed before it asked: the launch itself then fails, or
    not, as it always did."""
    if engine == "docker":
        out = _run(
            cli,
            "info",
            "--format",
            "{{.CPUCfsQuota}} {{.MemoryLimit}} {{.SwapLimit}} {{.PidsLimit}}",
        )
        words = (out or "").split()
        if len(words) != 4 or not set(words) <= {"true", "false"}:
            return LIMITS
        return frozenset(n for n, w in zip(LIMIT_ORDER, words, strict=True) if w == "true")
    try:
        controllers = json.loads(
            _run(cli, "info", "--format", "{{json .Host.CgroupControllers}}") or ""
        )
    except ValueError:
        return LIMITS
    if not isinstance(controllers, list):
        return LIMITS
    # Podman sets a swap limit wherever it can set a memory one.
    enforces = {"cpu": ("cpu",), "memory": ("memory", "swap"), "pids": ("pids",)}
    return frozenset(n for c in controllers if c in enforces for n in enforces[c])


def detect_runtime() -> Runtime:
    """Read `sandbox.yaml` and ask the runtime. Raises `ConfigError` for a
    `sandbox.yaml` that does not parse."""
    from kraft import config

    templates = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    host = config.SandboxHost.load(templates / config.SandboxHost.FILE)
    cli = host.cli or (
        "docker" if shutil.which("docker") else "podman" if shutil.which("podman") else "docker"
    )
    engine, rootless, labels = _ask(cli)
    selinux = None
    # Both: a permissive host denies nothing, and a runtime that applies no
    # labels leaves its containers unconfined, so its mounts work as they are.
    if labels and _selinux_enforcing():
        selinux = "refuse" if host.selinux == "auto" else host.selinux
    return Runtime(
        cli=cli, engine=engine, rootless=rootless, selinux=selinux, limits=_ask_limits(cli, engine)
    )


_RUNTIME: Runtime | None = None


def runtime(*, refresh: bool = False) -> Runtime:
    """This machine's `Runtime`, detected on first use and kept for the
    process: `docker info` is a round trip every launch would otherwise pay.
    Doctor refreshes it, so a changed `sandbox.yaml` is picked up there."""
    global _RUNTIME
    if _RUNTIME is None or refresh:
        _RUNTIME = detect_runtime()
    return _RUNTIME


def docker_argv(
    cmd: list[str],
    cwd: str | Path,
    sandbox: dict,
    results_dir: str | Path | None,
    env: dict | None = None,
    name: str | None = None,
    result_path: str | Path | None = None,
    cidfile: str | Path | None = None,
    *,
    refstore: RefStore | None = None,
    home: str | Path | None = None,
    passthrough: Iterable[str] = (),
    ro_paths: Iterable[str | Path] = (),
    ca_bundle: str | Path | None = None,
) -> list[str]:
    """Wrap `cmd` to run inside `sandbox['image']` instead of directly on the host.

    Mounts `cwd` (the git worktree) at its original host path, so every
    relative path the agent's own prompt already names stays meaningful
    unchanged inside the container -- nothing downstream of this has to know
    the process ran in one. Runs as the invoking host uid:gid so a container
    default of root does not leave root-owned files behind in the worktree
    for a later host-side step to trip over -- `--security-opt=no-new-privileges`
    and `--cap-drop=ALL` are what keep that true: without them, a setuid-root
    binary in the operator's image (`su`, `mount`, `sudo` all ship in most base
    images) lets the worker regain root inside the container and write
    root-owned or setuid-root files into the bind-mounted worktree anyway.

    Everything else the container can reach is mounted read-only with exactly
    the paths this session must write carved back out read-write, rather than
    mounted read-write with the dangerous paths shadowed read-only: a
    shadow list is a list of the escapes someone thought of (`.git/hooks`,
    then `.git/modules/<sub>/hooks`, then `.git/worktrees/<id>/modules/...`),
    and the container only has to find the one nobody listed.

    `results_dir`: read-only, because a hook legitimately *reads* its
    neighbours there (its own `<session>.review.md`, the previous fix
    session's result file), but a session writing another session's
    `<other>.json` would forge that session's status and findings. Only this
    session's own `result_path` is mounted back read-write. `None` for a
    launch that is no session and has no results to read (a repository's
    `setup_command`, `builtins.run_setup_command`): nothing is mounted.

    `<repo>/.git`: the worktree's private ref store (`refstore` below), so no
    ref the worker writes reaches the operator's repository; objects are
    shared read-write. This worktree's own gitdir (`<repo>/.git/worktrees/<id>`)
    is read-write -- what a commit from inside a linked worktree writes
    besides objects and refs. That worktree
    gitdir has to be read-write wholesale: a commit rewrites HEAD, the index
    and per-worktree refs through lockfiles beside them, so the directory
    itself must be writable, and every file git redirects config and hooks
    through (`commondir`, `config.worktree`, `modules/<sm>/config`) lives in
    it. Those are not enumerated here -- `harden_host_git_env` is what makes
    them harmless, by pinning `core.hooksPath` and friends on every git Kraft
    itself runs, so whatever a worker plants in a file nobody listed still
    has nothing to execute it.

    A Kraft worktree's `.git` is a file pointing at that gitdir (`git
    worktree`'s own layout), outside `cwd` -- without the mount every git
    command inside the container fails to find it. That file lives inside
    the read-write `cwd` mount, though, so it is shadowed read-only on top:
    otherwise a sandboxed worker rewrites it to point at a gitdir of its own
    making, complete with a `hooks/` it populates itself, and the next
    host-side git command run in the worktree executes that hook as the
    invoking host user.

    The daemon's proxy variables are forwarded bare, except one on its
    loopback, and an extra CA arrives as one bundle at a fixed container path
    with every CA variable naming it: `docker_forward`. `ca_bundle` is the
    bundle `prepare` built, None for none. A repository's own `env` still
    wins.

    `env` is `run_task`'s own `env=` argument -- e.g. `PYTHONDONTWRITEBYTECODE`
    for a fix-loop re-measure -- which is otherwise silently dropped: only
    `FORWARDED_ENV` crosses into the container by default. Passed through as
    literal `-e NAME=VALUE`, unlike `FORWARDED_ENV`'s bare `-e NAME` (which
    copies from docker's own process env, never written to disk).

    `cidfile`: docker writes the container's id there once it has created
    the container, and leaves no file when it never got that far -- the one
    thing that tells docker's own launch failure from the sandboxed
    command's (`launch_failed`, Kraft-6ltwh).

    `refstore`: the worktree's private ref store (`worker.refstore`), mounted
    over the repository's common gitdir so the worker's ref writes never reach
    the operator's refs. Without one, the common gitdir is read-only whole.

    `home`: a directory mounted read-write and set as `HOME`, so an agent CLI
    has somewhere to keep its state across sessions. `passthrough`: names
    forwarded bare, like `FORWARDED_ENV` -- a repo's `env_passthrough`, whose
    values must never be written onto this argv. `ro_paths`: extra
    host paths mounted read-only at the same path (a rules file).

    `cwd` is resolved first: git records a worktree's real path, and a
    worktree mounted only at a symlinked path looks gone to `git worktree
    prune`, which then deletes its admin directory.

    `sandbox['resources']` becomes the runtime's limit flags
    (`Runtime.limit_args`), or `SandboxRefused` where it cannot enforce one.
    A `name`d container runs without `--rm`: removed on exit, it could not be
    asked whether its memory limit killed it (`oom_killed`), so `teardown`
    removes it by name instead, and `sweep_orphans` whatever a crash left.
    """
    try:
        host = runtime()
    except ConfigError as exc:
        raise SandboxRefused(str(exc)) from exc
    if problem := host.refusal():
        raise SandboxRefused(problem)
    cwd = str(Path(cwd).resolve())
    argv = [
        host.cli,
        "run",
        *(["--rm"] if name is None else []),
        # PID 1 ignores a signal it installed no handler for, so without an
        # init neither a pause's SIGINT nor a cap's SIGTERM reaches the agent.
        "--init",
        "--label",
        home_label(),
        *host.user_args(),
        "--security-opt=no-new-privileges",
        *(["--security-opt=label=disable"] if host.selinux == "disable" else []),
        "--cap-drop=ALL",
        *host.limit_args(sandbox.get("resources")),
        "-v",
        f"{cwd}:{cwd}",
        "-w",
        cwd,
    ]
    if results_dir is not None:
        argv += ["-v", f"{results_dir}:{results_dir}:ro"]
    if result_path is not None:
        argv += ["-v", f"{result_path}:{result_path}"]
    argv += _gitdir_mounts(Path(cwd), refstore)
    for path in ro_paths:
        argv += ["-v", f"{path}:{path}:ro"]
    if home is not None:
        argv += ["-v", f"{home}:{home}", "-e", f"HOME={home}"]
    if name is not None:
        argv += ["--name", name]
    if cidfile is not None:
        argv.append(f"--cidfile={cidfile}")
    ca_mount, ca_env = _forward.ca_args(ca_bundle)
    argv += ca_mount
    for env_name in dict.fromkeys((*FORWARDED_ENV, *_forward.forwarded_proxies(), *passthrough)):
        argv += ["-e", env_name]
    pins: dict[str, str] = {}
    _pin(pins, _HARDENED_GIT_CONFIG)
    for env_name, value in {**pins, **ca_env, **(env or {})}.items():
        argv += ["-e", f"{env_name}={value}"]
    if host.selinux == "relabel":
        argv = _relabelled(argv)
    argv.append(sandbox["image"])
    argv += cmd
    return argv


def _relabelled(argv: list[str]) -> list[str]:
    """Every `-v` shared-labelled (`z`) for SELinux. Never `Z`: that label is
    private to one container, and would lock the operator out of their own
    repository and every other session out of it."""
    out = list(argv)
    for i, arg in enumerate(out[:-1]):
        if arg == "-v":
            spec = out[i + 1]
            out[i + 1] = f"{spec},z" if spec.endswith(":ro") else f"{spec}:z"
    return out


def _alternates(objects: Path) -> list[Path]:
    """Object directories `objects/info/alternates` borrows from (`clone
    --shared`, `--reference`): outside every other mount, so without their own
    the container cannot read those objects at all."""
    try:
        lines = (objects / "info" / "alternates").read_text().splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            path = Path(line) if Path(line).is_absolute() else objects / line
            if path.resolve().is_dir():
                found.append(path.resolve())
    return found


def _gitdir_mounts(cwd: Path, refstore: RefStore | None) -> list[str]:
    """`-v` arguments for the gitdirs a linked worktree's `.git` file points at.

    Empty when `cwd` is not a linked worktree: an ordinary `.git` directory is
    inside `cwd`, already mounted with it.

    The worktree's own gitdir `W` is read-write (a commit writes its index,
    HEAD and lockfiles there), with the files git redirects config and the
    common dir through shadowed read-only, created first so a worker cannot
    dodge a shadow by deleting its file. The common gitdir is the private ref
    store `refstore` when given, with the real `objects/` and `lfs/` inside
    it read-write and `HEAD`, `config`, `info/` and `shallow` read-only; the
    worker's refs, reflogs, `packed-refs` and `FETCH_HEAD` all land in the
    store. Without a store it is read-only whole, and a commit cannot move a
    ref at all.
    """
    dirs = linked_gitdirs(cwd)
    if dirs is None:
        # A `.git` file naming some other gitdir (a submodule's checkout, say):
        # nothing to commit through a ref store, so both stay read-only.
        other = _gitdir_of(cwd)
        if other is None:
            return []
        return ["-v", f"{cwd / '.git'}:{cwd / '.git'}:ro", "-v", f"{other}:{other}:ro"]
    common, worktree_gitdir = dirs
    # `.git` itself lives inside the read-write `cwd` mount. Left alone, a
    # sandboxed worker repoints it at a gitdir it builds inside the
    # worktree -- HEAD, objects/, refs/, a config with hooks -- and the next
    # host-side git command run there executes the worker's hook as the
    # invoking host user. It never legitimately changes for the life of the
    # worktree, so shadowing it read-only costs nothing.
    git_file = cwd / ".git"
    mounts = ["-v", f"{git_file}:{git_file}:ro"]
    if refstore is None:
        mounts += ["-v", f"{common}:{common}:ro"]
    else:
        mounts += ["-v", f"{refstore.shadow}:{common}"]
        for name in (*SHADOW_FILES, *SHADOW_DIRS):
            path = common / name
            if path.exists():
                mode = "" if name in _RW_SHADOW_DIRS else ":ro"
                mounts += ["-v", f"{path}:{path}{mode}"]
        # `objects/info` is where `alternates` lives: read-write, a worker
        # names any host directory there and every later sandboxed launch on
        # this repository mounts it (and host git reads objects from it).
        info = common / "objects" / "info"
        if info.is_dir():
            mounts += ["-v", f"{info}:{info}:ro"]
        for alternate in _alternates(common / "objects"):
            mounts += ["-v", f"{alternate}:{alternate}:ro"]
    mounts += ["-v", f"{worktree_gitdir}:{worktree_gitdir}"]
    # `commondir` names where `hooks/` and `config` resolve to, and
    # `config.worktree` is read as part of the config stack whenever
    # `extensions.worktreeConfig` is on: either one, rewritten, hands the next
    # host-side git in this worktree a config of the worker's choosing.
    # `gitdir` is the backlink `git worktree prune` checks: rewritten to a
    # path that does not exist, the next host prune deletes this worktree's
    # admin directory.
    for name in ("commondir", "config.worktree", "gitdir"):
        path = worktree_gitdir / name
        if name != "gitdir":
            try:
                path.touch(exist_ok=True)
            except OSError:
                pass
        if path.exists():
            mounts += ["-v", f"{path}:{path}:ro"]
    return mounts


async def teardown(session_id: str) -> None:
    """`docker rm -f` this session's container, best-effort.

    The one place a session's container is torn down, called from every path
    a session can *end* on -- `run_task`'s `finally` (normal exit, pause,
    skip, timeout) and `reattach`'s (a session adopted or resolved after a
    Kraft restart). The pid Kraft signals is `docker run`'s own client
    process, not the container: the container is parented by the docker
    daemon, so a killed -- or restarted-away-from -- client leaves it running
    with the worktree still mounted (Kraft-rki).

    Safe to call for a session that never had a sandbox at all: the name
    (`container_name`) simply matches nothing, and `docker rm -f` on an
    unknown name is a no-op this swallows. That is deliberate -- the callers
    are "a session ended" points, and making them each first work out whether
    this one was sandboxed is how a path gets missed. Errors (already gone,
    docker not installed) are not this function's to raise: a container that
    is already down is the success case.

    Bounded: a wedged daemon answers `docker rm` never, and this is awaited on
    every session's way out and for every row at startup. A container that
    outlives a timed-out call is `sweep_orphans`'s at the next start.
    """
    await docker_call("rm", "-f", container_name(session_id))


def _size(n: int) -> str:
    """A byte count in docker's own units, as `resources.memory` spells it."""
    for unit, size in (("g", 1024**3), ("m", 1024**2), ("k", 1024)):
        if n % size == 0:
            return f"{n // size}{unit}"
    return str(n)


async def oom_killed(session_id: str) -> str | None:
    """The memory limit this session's container was killed at, read off
    `State.OOMKilled` before `teardown` removes the container; None when it
    was not, or cannot be told (already gone, no daemon).

    Only the runtime's own OOM flag counts, never exit 137 alone: that is
    also Kraft's own SIGKILL (a cap, a teardown). A container with no memory
    limit that the host's OOM killer hit is None too: that is the host
    running short, not a limit a retry would hit again."""
    got = await docker_call(
        "inspect",
        "--format",
        "{{.State.OOMKilled}} {{.HostConfig.Memory}}",
        container_name(session_id),
    )
    if got is None or got[0] != 0:
        return None
    killed, _, memory = got[1].strip().partition(" ")
    if killed != "true" or not memory.isdigit() or int(memory) <= 0:
        return None
    return _size(int(memory))


#: How long any one best-effort `docker` call Kraft makes on its own account
#: (teardown, the orphan sweep, the image check) may take.
DOCKER_CALL_TIMEOUT_S = 30.0


async def docker_call(*args: str, timeout: float | None = None) -> tuple[int, str] | None:
    """Run `docker args...`; `(returncode, stdout)`, or None when docker is
    missing or did not answer in time (its client is then killed)."""
    try:
        cli = (await asyncio.to_thread(runtime)).cli
    except ConfigError, OSError:
        # A bad sandbox.yaml: best-effort calls stay quiet, and the launch
        # that needs the runtime says why.
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            cli,
            *args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout or DOCKER_CALL_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    return proc.returncode, out.decode(errors="replace")


async def sweep_orphans(keep: Iterable[str] = ()) -> list[str]:
    """`docker rm -f` every container this Kraft home started that no live
    session owns, returning their names: a teardown that failed or timed out
    (the daemon was briefly down) leaves one writing a worktree forever, and
    only a scan finds it. Listed by `home_label`, never by name, so another
    Kraft's containers on the same daemon are left alone."""
    listed = await docker_call(
        "ps", "-a", "--filter", f"label={home_label()}", "--format", "{{.Names}}"
    )
    if listed is None or listed[0] != 0:
        return []
    keep = set(keep)
    orphans = [n for n in listed[1].split() if n not in keep]
    for name in orphans:
        await docker_call("rm", "-f", name)
    return orphans


#: `(image, executable)` pairs an image was seen to hold. Only a hit is
#: remembered: an operator who fixes the image need not restart Kraft.
_HAS_EXECUTABLE: set[tuple[str, str]] = set()


async def missing_executable(
    image: str, executable: str, env: dict[str, str] | None = None
) -> bool:
    """True only when `image` demonstrably lacks `executable`. A launch then
    stops as `config_error` naming both, instead of exiting 127 inside a
    container that exists -- which reads as the agent failing and opens a fix
    loop no agent can win. Asked the way the launch will run: through the
    image's own entrypoint (a version manager's shim sets `PATH` there) and
    with the repository's literal `env`. Anything inconclusive -- no daemon,
    no `sh`, an entrypoint that is the agent itself, a pull that failed -- is
    False: the launch goes ahead and fails, or not, as it always did."""
    key = (image, executable)
    if key in _HAS_EXECUTABLE:
        return False
    env_args = [a for name, value in (env or {}).items() for a in ("-e", f"{name}={value}")]
    probed = await docker_call(
        "run",
        "--rm",
        "--label",
        home_label(),
        "--network=none",
        "--cap-drop=ALL",
        *env_args,
        image,
        "sh",
        "-c",
        'command -v "$0" >/dev/null && echo kraft-probe-yes || echo kraft-probe-no',
        executable,
        timeout=300,
    )
    if probed is None or probed[0] != 0:
        return False
    answer = probed[1].strip().splitlines()[-1:]
    if answer == ["kraft-probe-yes"]:
        _HAS_EXECUTABLE.add(key)
        return False
    return answer == ["kraft-probe-no"]


def sandbox_home(run_dirs, work_item_id: str) -> Path:
    """The `HOME` a sandboxed item's containers share, kept across its
    sessions so an agent CLI can resume one: the same directory for every
    session of the item, never shared with another item."""
    return run_dirs.base / "sandbox-home" / work_item_id


def launch_failed(cidfile: Path, returncode: int | None = None) -> bool:
    """True if `docker run` itself never created the container -- the daemon
    is down, or an image could not be pulled -- so the sandboxed command
    never ran: an infra problem no agent edit can fix (Kraft-nc9gm).

    Read off `--cidfile`, not the log (Kraft-6ltwh): the log holds the
    sandboxed command's own output too, so a task that prints docker's
    wording is not docker failing. Nor the exit code: daemon-down is plain 1
    (docker-cli 29.7.2, probed 2026-09-22), the same code a failing command
    returns. docker writes the id once the container exists and removes an
    unwritten cidfile when create fails (probed the same day: daemon
    unreachable exits 1, a refused create exits 125, and neither leaves the
    file; a create that succeeds writes it before start). A container docker
    created but could not start is not caught here; it fails as the task's
    own, as it did before this check existed.

    Podman removes the cidfile when a `--rm` container exits, so it says
    nothing afterwards; podman instead reserves exit 125 for a failure of
    its own, before or instead of the command (probed with 4.9.3). A session
    no longer runs `--rm`, but the exit code stays the rule: it holds
    whichever way a container was run."""
    if runtime().podman:
        return returncode == 125
    try:
        return not cidfile.read_text().strip()
    except OSError:
        return True


class DockerBackend:
    """`kraft.worker.backends.SandboxBackend` over `docker run`. Every method
    reads this module's functions at call time, so a test patching one of
    them patches the backend too."""

    kind = "docker"

    def home(self, run_dirs, work_item_id: str) -> Path:
        return sandbox_home(run_dirs, work_item_id)

    async def probe(self, sandbox: dict, executable: str, env: dict | None) -> str | None:
        try:
            host = await asyncio.to_thread(runtime)
        except ConfigError as exc:
            return str(exc)
        if problem := host.refusal() or host.limits_refusal(sandbox.get("resources")):
            return problem
        if await missing_executable(sandbox["image"], executable, env):
            return (
                f"image {sandbox['image']!r} has no {executable!r} on its PATH, so this "
                "sandboxed task cannot start; install it in the image (see the sandbox "
                "section of the repos.yaml reference)"
            )
        return None

    async def prepare(self, sandbox: dict) -> Path | None:
        """The image's combined CA bundle, built when there is an extra CA
        (`docker_forward.prepare`), for `wrap`'s `ca_bundle`."""
        try:
            return await _forward.prepare(sandbox["image"])
        except ConfigError as exc:
            raise SandboxNotReady(str(exc)) from exc
        except OSError as exc:
            raise SandboxNotReady(f"could not write the sandbox's CA bundle: {exc}") from exc

    def code_in(self, run_base: Path, cwd: Path, branch: str | None, **kw) -> RefStore | None:
        return _refstore.prepare(run_base, cwd, branch, **kw)

    def wrap(
        self,
        cmd: list[str],
        cwd: str | Path,
        sandbox: dict,
        results_dir: str | Path | None,
        env: dict | None = None,
        *,
        session_id: str | None = None,
        result_path: str | Path | None = None,
        cidfile: str | Path | None = None,
        refs: RefStore | None = None,
        home: str | Path | None = None,
        passthrough: Iterable[str] = (),
        ro_paths: Iterable[str | Path] = (),
        ca_bundle: str | Path | None = None,
    ) -> list[str]:
        return docker_argv(
            cmd,
            cwd,
            sandbox,
            results_dir,
            env=env,
            name=container_name(session_id) if session_id is not None else None,
            result_path=result_path,
            cidfile=cidfile,
            refstore=refs,
            home=home,
            passthrough=passthrough,
            ro_paths=ro_paths,
            ca_bundle=ca_bundle,
        )

    def launch_failed(self, cidfile: Path, returncode: int | None = None) -> bool:
        return launch_failed(cidfile, returncode)

    def code_out(self, refs: RefStore, session_id: str) -> str | None:
        return _refstore.sync(refs, session_id)

    def code_out_item(self, run_base: Path, work_item_id: str) -> list[str]:
        return _refstore.sync_item(run_base, work_item_id)

    async def collect(self, session_id: str, result_path: Path) -> None:
        """Nothing to fetch: the result file is bind-mounted, so the worker
        wrote it where Kraft reads it."""

    async def oom_killed(self, session_id: str) -> str | None:
        return await oom_killed(session_id)

    async def close(self, session_id: str) -> None:
        await teardown(session_id)

    async def sweep(self, keep_sessions: Iterable[str]) -> list[str]:
        return await sweep_orphans({container_name(sid) for sid in keep_sessions})

    def release(self, run_dirs, worktree: Path, work_item_id: str) -> None:
        # The ref store is found through the worktree's `.git`, so this runs
        # before the worktree goes.
        _refstore.discard(run_dirs.base, worktree)
        shutil.rmtree(sandbox_home(run_dirs, work_item_id), ignore_errors=True)

    async def health(self, sandbox: dict) -> tuple[bool, str]:
        """A sandboxed repository's work items stop for a human without a
        runtime to run them in, and its first launch pulls an image inside
        that session's time cap: both are better learned from doctor.
        Re-detects, so an edited `sandbox.yaml` shows here first."""
        try:
            host = await asyncio.to_thread(runtime, refresh=True)
        except ConfigError as exc:
            return False, str(exc)
        if problem := host.refusal() or host.limits_refusal(sandbox.get("resources")):
            return False, problem
        try:
            extra = await asyncio.to_thread(_forward.extra_ca)
        except ConfigError as exc:
            return False, str(exc)
        image = sandbox["image"]
        daemon = await docker_call("version", "--format", "{{.Server.Version}}")
        if daemon is None or daemon[0] != 0:
            return False, f"{host.cli} is not installed or its daemon is not reachable"
        pulled = await docker_call("image", "inspect", "--format", "{{.Id}}", image)
        if pulled is None or pulled[0] != 0:
            return False, f"image {image!r} is not pulled -- run: {host.cli} pull {image}"
        trusts = f", extra CA from {extra[0]}" if extra is not None else ""
        return (
            True,
            f"{host.describe()} {daemon[1].strip()}, image {image}{trusts}, "
            f"{host.describe_limits()}",
        )
