"""The Seatbelt profile `kind: os` runs a worker under on macOS.

The profile is `(deny default)` and the last matching rule wins, so the order
of `render_profile` is the policy: broad allows first, then the carve-outs
(`/usr/local`, read-only mounts, credential files) that must override them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Literal, NamedTuple

from kraft.paths import RunDirs
from kraft.worker import host_view, os_init, shim
from kraft.worker.backends import bwrap
from kraft.worker.ca import write_whole
from kraft.worker.mounts import Mount
from kraft.worker.sandbox import _HARDENED_GIT_CONFIG, SandboxRefused, _pin

logger = logging.getLogger(__name__)

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
#: Longest `end_session` waits for a sweep; the sweep itself stops after ten kill rounds.
SWEEP_SECONDS = 30

#: Readable besides `host_view.system_dirs()`; none holds operator data.
SYSTEM_READS = (
    "/private/etc",
    "/private/var/db/timezone",
    "/private/var/db/dyld",
    "/dev/fd",
    # With Xcode.app selected, `/usr/bin/git` is a shim through `xcodebuild`, which refuses
    # to run ("You have not agreed to the Xcode license") unless it reads this: the licence
    # version the machine agreed to, no operator data.
    "/Library/Preferences/com.apple.dt.Xcode.plist",
)
#: By name, never `/dev` whole: it holds the operator's other terminals, and
#: reading one is their keystrokes.
DEV_READS = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/dtracehelper")
DEV_WRITES = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")
#: Never `(allow mach-lookup)` unfiltered: that hands the worker LaunchServices,
#: Apple Events, the pasteboard and the keychain.
MACH_SERVICES: tuple[str, ...] = (
    "com.apple.system.opendirectoryd.libinfo",  # getpwuid: python pwd, node os.userInfo
)

#: Python's `multiprocessing.Lock` (a named POSIX semaphore) and
#: `shared_memory`; both fail without these, and the names are global to the
#: operator's processes, so only the prefixes the runtime itself makes.
IPC_SEM_PREFIX = "/mp-"
IPC_SHM_PREFIX = "/psm_"

_NEVER = (
    "com.apple.SecurityServer",
    "com.apple.securityd",
    "com.apple.coreservices.launchservicesd",
    "com.apple.lsd.",
    "com.apple.coreservices.appleevents",
    "com.apple.pasteboard",
)
assert not [s for s in MACH_SERVICES if s.startswith(_NEVER)]


def quote(path: str | Path) -> str:
    """An SBPL string literal that matches exactly `path`."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _refuse_unpinned_carve_outs(mounts: Sequence[Mount]) -> None:
    """`(allow file-write* (subpath A))` lets a directory between a read-write
    directory mount A and a read-only mount R inside it be renamed, carrying R
    away: only a mount's own directory is pinned or unwritable."""
    dsts = {_real(m.dst) for m in mounts}
    for a in (_real(m.dst) for m in mounts if not m.ro and _real(m.dst).is_dir()):
        for r in (_real(m.dst) for m in mounts if m.ro):
            for between in r.parents:
                if between == a:
                    break
                if between not in dsts and between.is_relative_to(a):
                    raise SandboxRefused(
                        f"kind: os on macOS cannot keep {r} read-only: {between}, between it and "
                        f"the writable {a}, is not mounted, so the worker could rename it away"
                    )


def render_profile(
    *,
    mounts: Sequence[Mount],
    read_roots: Sequence[Path],
    read_literals: Sequence[Path],
    denied_reads: Sequence[Path],
    tmpdir: Path,
    marker: Path,
    relay: bool,
) -> str:
    for m in mounts:
        if m.src != m.dst:
            raise SandboxRefused(f"kind: os on macOS cannot show {m.src} at {m.dst}")
        # A link the worker can re-point (a lock, a reflog) must not choose what it may write.
        if not m.ro and os.path.islink(m.dst):
            raise SandboxRefused(
                f"kind: os on macOS will not give write access to {m.dst}: it is a symlink"
            )
    _refuse_unpinned_carve_outs(mounts)
    sub = lambda p: f"(subpath {quote(_real(Path(p)))})"  # noqa: E731
    lit = lambda p: f"(literal {quote(_real(Path(p)))})"  # noqa: E731
    # `(allow file-write* (subpath D))` also lets D itself be renamed away (rename
    # is an unlink of the source), dropping the read-only carve-outs inside it.
    pin = lambda p: f"(deny file-write-unlink {lit(p)})"  # noqa: E731
    out = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec*)",
        "(allow process-fork)",
        "(allow signal (target same-sandbox))",
        "(allow process-info* (target same-sandbox))",
        "(allow sysctl-read)",
        "(allow file-read-metadata)",
    ]
    out += [
        f'(allow ipc-posix-sem (ipc-posix-name-prefix "{IPC_SEM_PREFIX}"))',
        f'(allow ipc-posix-shm (ipc-posix-name-prefix "{IPC_SHM_PREFIX}"))',
    ]
    if MACH_SERVICES:
        names = " ".join(f'(global-name "{s}")' for s in MACH_SERVICES)
        out.append(f"(allow mach-lookup {names})")
    # `/` itself: every dynamic loader reads it.
    out.append('(allow file-read* (literal "/"))')
    out += [f"(allow file-read* {sub(p)})" for p in (*host_view.system_dirs(), *SYSTEM_READS)]
    out += [f"(allow file-read* {lit(p)})" for p in DEV_READS]
    out += [f"(deny file-read* {sub(p)})" for p in host_view.system_excluded()]
    # The roots go after the deny: an Intel Homebrew Kraft lives under /usr/local.
    out += [f"(allow file-read* {sub(p)})" for p in read_roots]
    out += [f"(allow file-read* {lit(p)})" for p in read_literals]
    # Shallowest first, by the rendered path: a deeper mount must override the
    # tree it sits in. The worker's own tmpdir and devices follow the mounts so
    # a read-only mount holding them cannot deny them.
    for m in sorted(mounts, key=lambda m: len(_real(m.dst).parts)):
        dst = _real(m.dst)
        where = sub(dst) if dst.is_dir() else lit(dst)
        if m.ro:
            out += [f"(allow file-read* {where})", f"(deny file-write* {where})"]
        else:
            out.append(f"(allow file-read* file-write* {where})")
            if dst.is_dir():
                out.append(pin(dst))
    out += [f"(allow file-write* {lit(p)})" for p in DEV_WRITES]
    out += [
        f"(allow file-read* file-write* {sub(tmpdir)})",
        pin(tmpdir),
        f"(allow network-bind network-inbound network-outbound (local unix-socket {sub(tmpdir)}))",
        f"(allow network-outbound (remote unix-socket {sub(tmpdir)}))",
        f"(allow file-read-data {lit(marker)})",
    ]
    # Writes too: a credential renamed inside a read-write tree could be read as the copy.
    out += [f"(deny file-read* file-write* {lit(p)})" for p in denied_reads]
    if relay:
        out.append(
            '(allow network-outbound (remote ip (string-append "localhost:" (param "RELAY_PORT"))))'
        )
    return "\n".join(out) + "\n"


class SessionFiles(NamedTuple):
    """What a launch makes on the host, named by its key (`ids.qualified` of a
    session, or of a setup's item and setup id; `oneshot-` and a uuid for one that has neither)."""

    tmpdir: Path
    marker: Path
    control: Path
    profile: Path


def markers_dir(run_base: Path) -> Path:
    # Resolved: the profile and `sandbox_check` both match the real path.
    return Path(os.path.realpath(run_base)) / "os-sandbox" / "markers"


def session_files(run_base: Path, key: str) -> SessionFiles:
    markers = markers_dir(run_base)
    base = markers.parent
    return SessionFiles(
        base / "tmp" / key, markers / key, markers / "control", base / "profiles" / f"{key}.sb"
    )


def seatbelt_argv(
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
    key: str,
    agent: str | None = None,
    mode: Literal["launch", "oneshot"] = "launch",
) -> list[str]:
    """The whole launch of `cmd` on macOS, as `bwrap.bwrap_argv` builds it on
    Linux: the launcher (`os_init.py launch`, on the host, which is also the
    relay), `sandbox-exec` with a profile written for this launch, and the init
    that runs the command inside. Nothing secret is on it: a secret crosses by
    name (`--keep`), through the launcher's environment."""
    shown = bwrap.show_host(
        cmd,
        cwd,
        sandbox,
        passthrough=passthrough,
        workdir=workdir,
        search_path=search_path,
        agent=agent,
    )
    files = session_files(run_dirs.base, key)
    roots = shown.roots
    mounts = [*session, *([Mount(home, home, False)] if home is not None else [])]
    text = render_profile(
        mounts=mounts,
        read_roots=[*shown.interp_roots, Path(os_init.__file__), *roots, *shown.ca, shim.HOST_DIR],
        # A symlink hop's own path: the real paths exist here, so nothing is recreated.
        read_literals=list(
            dict.fromkeys(link for link, _ in (*shown.first.links, *shown.resolved.links))
        ),
        denied_reads=list(
            dict.fromkeys(h for root in roots for h in host_view.credential_shadows(root))
        ),
        tmpdir=files.tmpdir,
        marker=files.marker,
        relay=sock_path is not None,
    )
    for d in (files.profile.parent.parent, files.profile.parent):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        d.chmod(0o700)
    write_whole(files.profile, text.encode(), 0o600)
    pins: dict[str, str] = {}
    _pin(pins, _HARDENED_GIT_CONFIG)
    literals = {"HOME": str(home or files.tmpdir / "home"), **pins, **env}
    init = [str(shown.interp), "-I", "-S", os_init.__file__]
    return [
        *init,
        mode,
        *("--keep", ",".join(shown.keep)),
        *(a for name, value in literals.items() for a in ("--setenv", f"{name}={value}")),
        # os_init refuses a oneshot that records a start.
        *(("--started", str(started)) if started and mode == "launch" else ()),
        *("--marker", str(files.marker), "--control", str(files.control)),
        *("--tmpdir", str(files.tmpdir)),
        *(("--relay-sock", str(sock_path)) if sock_path is not None else ()),
        "--",
        SANDBOX_EXEC,
        *("-f", str(files.profile)),
        *init,
        "init",
        *("--chdir", str(shown.chdir)),
        "--",
        *cmd,
    ]


def probe_darwin() -> str | None:
    """Why `kind: os` cannot run on this Mac, with its fix, or None."""
    if (machine := platform.machine()) not in ("arm64", "x86_64"):
        return f"kind: os supports arm64 and x86_64 Macs, not {machine}"
    if os.geteuid() == 0:
        return "kind: os does not run while Kraft runs as root"
    if not os.access(SANDBOX_EXEC, os.X_OK):
        return f"{SANDBOX_EXEC} is missing (it ships with macOS): use kind: docker on this Mac"
    if problem := bwrap.missing_tools():
        return problem
    # The real profile builder's output for an empty session: catches a profile
    # that does not parse as well as a Kraft that is sandboxed itself.
    with tempfile.TemporaryDirectory(prefix="kraft-probe-") as tmp:
        root = Path(os.path.realpath(tmp))
        profile = root / "probe.sb"
        profile.write_text(
            render_profile(
                mounts=[],
                read_roots=[],
                read_literals=[],
                denied_reads=[],
                tmpdir=root / "tmp",
                marker=root / "marker",
                relay=True,
            )
        )
        trial = bwrap._run(
            [SANDBOX_EXEC, "-f", str(profile), "-D", "RELAY_PORT=1", "/usr/bin/true"]
        )
    if not isinstance(trial, str) and trial.returncode == 0:
        return None
    # 65 is also how a profile that fails to parse exits; only this text means nesting.
    if (
        not isinstance(trial, str)
        and trial.returncode == 65
        and "Operation not permitted" in trial.stderr
    ):
        return (
            "Kraft is itself running inside a sandbox, so it cannot start one: "
            "run the Kraft server outside it"
        )
    err = trial if isinstance(trial, str) else trial.stderr.strip() or f"exit {trial.returncode}"
    return f"sandbox-exec cannot start a sandbox here: {err}"


async def end_session(files: SessionFiles, name: str) -> bool:
    """End every process a session left (`os_init.sweep`, in a thread, bounded),
    then remove its temporary directory and profile. False when processes
    remain or the sweep failed: the marker stays, so a later sweep still finds
    them, and so does the temporary directory. `name` is for the log."""
    if not files.marker.exists() and not files.tmpdir.exists():
        files.profile.unlink(missing_ok=True)
        return True
    try:
        left = await asyncio.wait_for(
            asyncio.to_thread(os_init.sweep, str(files.marker), str(files.control)), SWEEP_SECONDS
        )
    except (OSError, TimeoutError) as exc:
        logger.warning("session %s: its processes could not be swept: %s", name, exc or "timed out")
        return False
    if left:
        logger.warning("session %s: processes survived the sweep, pids %s", name, left)
        return False
    shutil.rmtree(files.tmpdir, ignore_errors=True)
    files.profile.unlink(missing_ok=True)
    return True
