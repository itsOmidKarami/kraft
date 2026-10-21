"""The bubblewrap command line of a `kind: os` sandbox.

`bwrap_argv` returns the whole thing: the launcher (`os_init.py launch`, on
the host), bwrap with every mount and variable, and the init (`os_init.py
init`) that runs the command inside. Nothing secret is on it: a secret crosses
by name (`--keep`), through the launcher's environment.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal

from kraft.paths import kraft_home
from kraft.worker import host_view, os_init, os_seccomp, shim
from kraft.worker.mounts import RELAY_PORT, Mount
from kraft.worker.sandbox import _HARDENED_GIT_CONFIG, FORWARDED_ENV, SandboxRefused, _pin

TMP_BYTES = 2147483648
SHM_BYTES = 268435456
#: The worker baseline's names that cross (the CA ones come from `ca_names`).
_BASELINE_NAMES = ("LANG", "LC_ALL", "TZ", "TERM", "USER", "LOGNAME", "SHELL")


def _private_file(path: Path, data: bytes, mode: int) -> Path:
    """`path` holding `data`, written once; other launches may race to it."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.write_bytes(data)
        tmp.chmod(mode)
        os.replace(tmp, path)
    return path


def seccomp_file(run_dirs) -> Path:
    arch = os_seccomp.machine()
    data = os_seccomp.build(arch)
    name = f"seccomp-{arch}-{hashlib.sha256(data).hexdigest()[:12]}.bpf"
    return _private_file(run_dirs.base / "os-sandbox" / name, data, 0o600)


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
    run_dirs,
    mode: Literal["launch", "oneshot"] = "launch",
) -> list[str]:
    cwd = Path(cwd).resolve()
    operator_home = Path(os.environ.get("HOME") or Path.home())
    kh = kraft_home()
    if (path := shutil.which("bwrap")) is None:
        raise SandboxRefused("bubblewrap is not installed (apt install bubblewrap)")
    try:
        interp, interp_roots = host_view.init_interpreter()
        exposed = host_view.expose_roots(sandbox.get("expose") or (), operator_home, kh)
        resolved = host_view.resolve(
            cmd[0],
            search_path,
            workdir or cwd,
            cwd,
            operator_home,
            kh,
            visible=exposed,
        )
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
        *(("--started", str(started)) if started else ()),
        "--",
        os.path.abspath(path),
        *("--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent"),
        *("--cap-drop", "ALL", "--as-pid-1"),
        *system_args(),
    ]
    for root in (*interp_roots, Path(os_init.__file__)):
        argv += _bind(root)
    roots = list(dict.fromkeys((*resolved.roots, *exposed)))
    for root in roots:
        argv += _bind(root)
    # Not /dev/null: bwrap mounts binds nodev, so it would read as permission denied.
    empty = _private_file(run_dirs.base / "os-sandbox" / "empty", b"", 0o444)
    for root in roots:
        for hit in host_view.credential_shadows(root):
            argv += _bind(empty, hit)
    for link, target in resolved.links:
        argv += ["--symlink", str(target), str(link)]
    for path_ in ca:
        argv += _bind(path_)
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
    argv += ["--chdir", str(workdir or cwd)]
    argv += [*init, "init"]
    if sock_path is not None:
        argv += ["--sock", str(sock_path), "--port", str(RELAY_PORT)]
    return [*argv, "--", *cmd]
