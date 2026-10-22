"""The Seatbelt profile `kind: os` runs a worker under on macOS.

The profile is `(deny default)` and the last matching rule wins, so the order
of `render_profile` is the policy: broad allows first, then the carve-outs
(`/usr/local`, read-only mounts, credential files) that must override them.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from kraft.worker import host_view
from kraft.worker.mounts import Mount
from kraft.worker.sandbox import SandboxRefused

#: Readable besides `host_view.system_dirs()`; none holds operator data.
SYSTEM_READS = ("/private/etc", "/dev", "/private/var/db/timezone", "/private/var/db/dyld")
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
    sub = lambda p: f"(subpath {quote(_real(Path(p)))})"  # noqa: E731
    lit = lambda p: f"(literal {quote(_real(Path(p)))})"  # noqa: E731
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
    # The roots go after the deny: an Intel Homebrew Kraft lives under /usr/local.
    out += [f"(deny file-read* {sub(p)})" for p in host_view.system_excluded()]
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
    out += [f"(allow file-write* {lit(p)})" for p in DEV_WRITES]
    out += [
        f"(allow file-read* file-write* {sub(tmpdir)})",
        f"(allow network-bind network-inbound network-outbound (local unix-socket {sub(tmpdir)}))",
        f"(allow network-outbound (remote unix-socket {sub(tmpdir)}))",
        f"(allow file-read-data {lit(marker)})",
    ]
    out += [f"(deny file-read* {lit(p)})" for p in denied_reads]
    if relay:
        out.append(
            '(allow network-outbound (remote ip (string-append "localhost:" (param "RELAY_PORT"))))'
        )
    return "\n".join(out) + "\n"
