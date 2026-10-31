"""What a `kind: os` sandbox may see of the host.

The sandbox mounts what a session needs and nothing else, so this decides which
host directories an executable, an `expose:` entry or a CA variable may add,
and refuses any that would show the operator's secrets or Kraft's control
plane. Nothing here imports `kraft.config` or `kraft.worker.backends` at module
level: `policy` calls it from a validator, and the backends import `policy`.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

#: Under the operator's home, a path that is or holds any of these is refused.
_PROTECTED_IN_HOME = (
    ".ssh",
    ".gnupg",
    ".config/gh",
    ".config/glab-cli",
    ".aws",
    ".kube",
    ".docker",
    ".claude",
    ".codex",
    ".local/share/keyrings",
    "Library/Keychains",
    "Library/Cookies",
)
SYSTEM_PYTHON = Path("/usr/bin/python3")
#: `credentials` is cargo's legacy name for `credentials.toml`, still read.
CREDENTIAL_FILES = (
    "credentials.toml",
    "credentials",
    ".npmrc",
    ".netrc",
    ".pypirc",
    ".git-credentials",
)
_SHADOW_DEPTH = 3


class NotFound(Exception):
    """Something the sandbox was asked to show does not exist."""


class Refused(Exception):
    """Something the sandbox was asked to show is protected."""


@dataclass(frozen=True)
class Resolved:
    #: The command's own first word, unchanged.
    argv0: str
    #: Directories mounted read-only for the command.
    roots: tuple[Path, ...]
    #: `(link path, its target)` for each symlink hop to recreate inside.
    links: tuple[tuple[Path, Path], ...]


def _within(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


@functools.cache
def system_dirs() -> tuple[Path, ...]:
    """The system directories every sandbox mounts, so a file in one adds no root."""
    if sys.platform != "darwin":
        return tuple(Path(p) for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64"))
    dirs = [
        Path(p) for p in ("/usr", "/bin", "/sbin", "/System", "/Library/Developer/CommandLineTools")
    ]
    try:
        out = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, timeout=5)
        picked = out.stdout.strip() if out.returncode == 0 else ""
        selected = Path(picked).resolve() if picked else None
    except (OSError, subprocess.SubprocessError):
        selected = None
    # `xcode-select -p` inside Xcode.app is `Xcode.app/Contents/Developer`, and the `/usr/bin/git`
    # shim runs `xcodebuild`, which loads frameworks from `Contents/SharedFrameworks`: show the
    # app's `Contents`, which holds no operator data, not only `Developer`.
    if (
        selected
        and selected.name == "Developer"
        and selected.parent.name == "Contents"
        and selected.parent.parent.suffix == ".app"
    ):
        selected = selected.parent
    if selected:
        from kraft.paths import kraft_home

        # `xcode-select -p` follows DEVELOPER_DIR, which the operator's shell may point anywhere:
        # checked on what is shown, after the widening.
        home = Path(os.environ.get("HOME") or Path.home())
        if protected_refusal(selected, home, kraft_home()):
            selected = None
    if selected and not any(_within(selected, d) for d in dirs):
        dirs.append(selected)
    return tuple(dirs)


def system_excluded() -> tuple[Path, ...]:
    """Inside `system_dirs()` yet not system: `/usr/local` holds operator data."""
    return (Path("/usr/local"),) if sys.platform == "darwin" else ()


def _is_system(path: Path) -> bool:
    return any(_within(path, d) for d in system_dirs()) and not any(
        _within(path, x) for x in system_excluded()
    )


def _protected(home: Path, kraft_home: Path) -> list[Path]:
    # Inside: credentials imports host_view.
    from kraft.adapters.forge import credentials
    from kraft.paths import config_dir, default_run_dir

    # The run and config directories as well: `KRAFT_RUN_DIR` and
    # `KRAFT_CONFIG_DIR` may put either outside Kraft's home.
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    return [
        kraft_home.resolve(),
        run_dir.resolve(),
        config_dir().resolve(),
        *((home / p).resolve() for p in _PROTECTED_IN_HOME),
        *credentials.secret_files(),
    ]


def protected_refusal(path: Path, home: Path, kraft_home: Path) -> str | None:
    """Why `path`, resolved, may not be shown, or None. It is refused when it
    is or holds `home`, or is, holds or is inside Kraft's home or a secrets
    directory. Inside `home` alone is fine: home-installed CLIs live there."""
    p, h = path.resolve(), home.resolve()
    if p.name in CREDENTIAL_FILES:
        return f"{p} is a credential file"
    if _within(h, p):
        return f"{p} is or holds your home directory"
    for q in _protected(home, kraft_home):
        if _within(p, q) or _within(q, p):
            return f"{p} is or holds {q}, which the sandbox never shows"
    return None


def _expand(path: Path) -> tuple[list[tuple[Path, Path]], Path]:
    """Every symlink on the way to `path`, directories included, as
    `(link, target)` in the order met, and the real file it names."""
    links: list[tuple[Path, Path]] = []
    real = Path("/")
    pending = list(reversed(path.parts[1:]))
    while pending:
        name = pending.pop()
        if name in ("", "."):
            continue
        if name == "..":
            real = real.parent
            continue
        nxt = real / name
        if not nxt.is_symlink():
            real = nxt
            continue
        if len(links) >= 40:
            raise NotFound(f"too many symlinks resolving {path}")
        target = Path(os.readlink(nxt))
        links.append((nxt, Path(os.path.normpath(real / target))))
        if target.is_absolute():
            real = Path("/")
        pending.extend(reversed(target.parts[1:] if target.is_absolute() else target.parts))
    return links, real


def _root_of(final: Path, home: Path, kraft_home: Path) -> Path | None:
    """The directory to mount for a file: its own, or when that is a `bin/`
    the install prefix above it (a Node prefix, a versioned install)."""
    if _is_system(final):
        return None
    root = final.parent
    if root.name == "bin":
        up = root.parent
        if up not in (Path("/"), home) and not protected_refusal(up, home, kraft_home):
            root = up
    if why := protected_refusal(root, home, kraft_home):
        raise Refused(f"cannot show {final} to the sandbox: {why}; add its toolchain with expose")
    return root


def _interpreter(final: Path, search_path: str) -> Path | None:
    """The file named by a `#!` line, one level: `env X` is looked up on
    `search_path`. NotFound when it does not exist."""
    try:
        with final.open("rb") as f:
            head = f.readline(512)
    except OSError:
        return None
    if not head.startswith(b"#!"):
        return None
    words = head[2:].decode(errors="replace").split()
    if not words:
        return None
    if Path(words[0]).name == "env":
        words = [w for w in words[1:] if not w.startswith("-") and "=" not in w][:1]
        if not words:
            return None
        found = shutil.which(words[0], path=search_path)
        if found is None:
            raise NotFound(
                f"{final} needs {words[0]!r}, which is not on the Kraft server's PATH; "
                "add its directory to the PATH the server starts with"
            )
        return Path(found)
    if not Path(words[0]).exists():
        raise NotFound(f"{final} needs {words[0]}, which does not exist")
    return Path(words[0])


def resolve(
    executable: str,
    search_path: str,
    workdir: Path,
    checkout: Path,
    home: Path,
    kraft_home: Path,
    visible: Iterable[Path] = (),
) -> Resolved:
    """The mounts that let `executable` run in the sandbox as named. A path
    with a `/` is looked up from `workdir`, anything else on `search_path`.

    An executable inside the checkout is the repo's own, so it adds no root
    and its `#!` is not followed; its symlinks may lead only into what the
    sandbox shows anyway (the system, the checkout, `visible`). A file
    already inside `visible` adds no root either."""
    if "/" in executable:
        path = Path(os.path.abspath(workdir / executable))
    else:
        found = shutil.which(executable, path=search_path)
        if found is None:
            # `search_path` is the shim's directory, then the daemon's PATH:
            # `expose` mounts a path but never joins it.
            raise NotFound(
                f"{executable!r} not found on the Kraft server's PATH; add its directory "
                "to the PATH the server starts with (expose does not add to PATH)"
            )
        path = Path(os.path.abspath(found))
    if not path.exists():
        raise NotFound(f"{executable!r} not found: {path} does not exist")
    checkout, home = checkout.resolve(), home.resolve()
    shown = [checkout, *(v.resolve() for v in visible)]
    roots: list[Path] = []
    hops: list[tuple[Path, Path]] = []
    cur_hops, final = _expand(path)
    hops += cur_hops
    # By its real spelling: the checkout is resolved, and a path may reach it
    # through a symlinked directory.
    named = Path(os.path.realpath(path.parent)) / path.name
    if _within(named, checkout):
        if not (_is_system(final) or any(_within(final, d) for d in shown)):
            raise Refused(
                f"{executable} leads to {final}, outside the sandbox's view; "
                "add its directory to the sandbox's expose list"
            )
    elif not _within(final, checkout):
        targets = [final]
        if interp := _interpreter(final, search_path):
            more, interp_final = _expand(interp)
            hops += more
            targets.append(interp_final)
        for target in targets:
            # Already shown (an `expose` path, say): no wider root, so exposing
            # `~/.cargo/bin` never mounts `~/.cargo` for a cargo in it.
            shown_already = _is_system(target) or any(_within(target, d) for d in shown)
            root = None if shown_already else _root_of(target, home, kraft_home)
            if root is not None and root not in roots:
                roots.append(root)
    links = tuple(
        (link, target)
        for link, target in hops
        if not (_is_system(link) or any(_within(link, d) for d in (*roots, *shown)))
    )
    return Resolved(executable, tuple(roots), links)


def expose_roots(expose: Iterable[str], home: Path, kraft_home: Path) -> list[Path]:
    """The resolved `expose:` paths, each checked against the protected-path
    rule and for existence."""
    out = []
    for entry in expose:
        p = home / entry[2:] if entry.startswith("~/") else Path(entry)
        if why := protected_refusal(p, home, kraft_home):
            raise Refused(f"expose path {entry}: {why}")
        if not p.exists():
            raise NotFound(f"expose path {p} does not exist")
        out.append(p.resolve())
    return out


def credential_shadows(tree: Path) -> list[Path]:
    """Credential files inside `tree`, at most three directories down: the
    backstop covers them with an empty file. Never the boundary."""
    hits = []
    base = len(tree.parts)
    for dirpath, dirs, files in os.walk(tree):
        if len(Path(dirpath).parts) - base >= _SHADOW_DEPTH:
            dirs.clear()
        hits += [Path(dirpath) / f for f in files if f in CREDENTIAL_FILES]
    return sorted(hits)


@functools.cache
def init_interpreter() -> tuple[Path, tuple[Path, ...]]:
    """The python that runs `os_init.py`, and the roots to mount for it: the
    system's when it is under `/usr` and 3.10+, else Kraft's own."""
    home = Path(os.environ.get("HOME") or Path.home())
    from kraft.paths import kraft_home

    kh = kraft_home()
    for p in _protected(home, kh):
        if any(_within(p, d) for d in (Path("/usr"), Path("/etc"))):
            raise Refused(f"{p} is under /usr or /etc, which every os sandbox mounts")
    # On a Mac /usr/bin/python3 is an xcrun shim to 3.9, never the init's python.
    if (
        sys.platform != "darwin"
        and SYSTEM_PYTHON.exists()
        and _within(SYSTEM_PYTHON.resolve(), Path("/usr"))
    ):
        probe = [str(SYSTEM_PYTHON), "-c", "import sys; sys.exit(sys.version_info < (3, 10))"]
        if subprocess.run(probe, capture_output=True).returncode == 0:
            return SYSTEM_PYTHON, ()
    prefix = Path(sys.base_prefix).resolve()
    if why := protected_refusal(prefix, home, kh):
        raise Refused(f"Kraft's python prefix cannot be shown to the sandbox: {why}")
    return Path(os.path.realpath(sys.executable)), (prefix,)


def ca_names() -> tuple[str, ...]:
    """Every variable a CLI reads its trust roots from."""
    from kraft.worker.backends.docker_forward import CA_VARS

    base = (
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "GIT_SSL_CAINFO",
        "NODE_EXTRA_CA_CERTS",
    )
    return base + tuple(v for v in CA_VARS if v not in base)


def ca_mounts(environ: Mapping[str, str], home: Path, kraft_home: Path) -> list[Path]:
    """The files and directories the daemon's CA variables name, absolute but
    as written (bwrap follows a source symlink, so the path the variable
    names exists inside), skipping any that is missing or unreadable, or whose resolved
    path is protected."""
    out: list[Path] = []
    for name in ca_names():
        value = environ.get(name)
        if not value:
            continue
        try:
            if not Path(value).exists():
                continue
        except OSError:  # EACCES on a parent: not there for this user
            continue
        p = Path(os.path.abspath(value))
        if protected_refusal(p, home, kraft_home) is None and p not in out:
            out.append(p)
    return out
