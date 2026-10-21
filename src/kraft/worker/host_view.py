"""What a `kind: os` sandbox may see of the host.

The sandbox mounts what a session needs and nothing else, so this decides which
host directories an executable, an `expose:` entry or a CA variable may add,
and refuses any that would show the operator's secrets or Kraft's control
plane. Nothing here imports `kraft.config` or `kraft.worker.backends` at module
level: `policy` calls it from a validator, and the backends import `policy`.
"""

from __future__ import annotations

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
)
#: The system directories every sandbox mounts, so a file in one adds no root.
_SYSTEM = tuple(Path(p) for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64"))
SYSTEM_PYTHON = Path("/usr/bin/python3")
CREDENTIAL_FILES = ("credentials.toml", ".npmrc", ".netrc", ".pypirc", ".git-credentials")
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


def _protected(home: Path, kraft_home: Path) -> list[Path]:
    return [kraft_home.resolve(), *((home / p).resolve() for p in _PROTECTED_IN_HOME)]


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


def _follow(path: Path) -> tuple[list[tuple[Path, Path]], Path]:
    """The symlink hops from `path` to the file it names, and that file."""
    hops: list[tuple[Path, Path]] = []
    cur = path
    while cur.is_symlink() and len(hops) < 40:
        target = Path(os.path.normpath(cur.parent.resolve() / os.readlink(cur)))
        hops.append((cur, target))
        cur = target
    return hops, cur.resolve()


def _root_of(final: Path, home: Path, kraft_home: Path) -> Path | None:
    """The directory to mount for a file: its own, or when that is a `bin/`
    the install prefix above it (a Node prefix, a versioned install)."""
    if any(_within(final, s) for s in _SYSTEM):
        return None
    root = final.parent
    if root.name == "bin":
        up = root.parent
        if up not in (Path("/"), home, home / ".local") and not protected_refusal(
            up, home, kraft_home
        ):
            root = up
    if why := protected_refusal(root, home, kraft_home):
        raise Refused(f"cannot show {final} to the sandbox: {why}; add its toolchain with expose")
    return root


def _interpreter(final: Path, search_path: str) -> Path | None:
    """The file named by a `#!` line, one level: `env X` is looked up on
    `search_path`."""
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
        words = [w for w in words[1:] if not w.startswith("-")][:1]
        if not words:
            return None
        found = shutil.which(words[0], path=search_path)
        return Path(found) if found else None
    return Path(words[0])


def resolve(
    executable: str,
    search_path: str,
    workdir: Path,
    checkout: Path,
    home: Path,
    kraft_home: Path,
) -> Resolved:
    """The mounts that let `executable` run in the sandbox as named. A path
    with a `/` is looked up from `workdir`, anything else on `search_path`."""
    if "/" in executable:
        path = Path(os.path.abspath(workdir / executable))
    else:
        found = shutil.which(executable, path=search_path)
        if found is None:
            raise NotFound(f"{executable!r} not found on PATH; add its directory with expose")
        path = Path(os.path.abspath(found))
    if not path.exists():
        raise NotFound(f"{executable!r} not found; add its directory with expose")
    checkout, home = checkout.resolve(), home.resolve()
    roots: list[Path] = []
    hops: list[tuple[Path, Path]] = []
    cur: Path | None = path
    while cur is not None:
        cur_hops, final = _follow(cur)
        hops += cur_hops
        root = None if _within(final, checkout) else _root_of(final, home, kraft_home)
        if root is not None and root not in roots:
            roots.append(root)
        # The command's `#!` interpreter is followed once, not the interpreter's own.
        # A script in the checkout is the repo's own: its `#!` line must not
        # choose what the operator's home shows.
        follows = cur is path and root is not None
        cur = _interpreter(final, search_path) if follows else None
    links = tuple(
        (link, target)
        for link, target in hops
        if not any(_within(link, r) for r in roots)
        and not _within(link, checkout)
        and not any(_within(link, s) for s in _SYSTEM)
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


def init_interpreter() -> tuple[Path, tuple[Path, ...]]:
    """The python that runs `os_init.py`, and the roots to mount for it: the
    system's when it is under `/usr` and 3.10+, else Kraft's own."""
    home = Path(os.environ.get("HOME") or Path.home())
    from kraft.paths import kraft_home

    kh = kraft_home()
    for p in _protected(home, kh):
        if any(_within(p, d) for d in (Path("/usr"), Path("/etc"))):
            raise Refused(f"{p} is under /usr or /etc, which every os sandbox mounts")
    if SYSTEM_PYTHON.exists() and _within(SYSTEM_PYTHON.resolve(), Path("/usr")):
        probe = [str(SYSTEM_PYTHON), "-c", "import sys; sys.exit(sys.version_info < (3, 10))"]
        if subprocess.run(probe, capture_output=True).returncode == 0:
            return SYSTEM_PYTHON, ()
    prefix = Path(sys.base_prefix)
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
    """The files and directories the daemon's CA variables name, resolved,
    skipping any that is missing or protected."""
    out: list[Path] = []
    for name in ca_names():
        value = environ.get(name)
        if not value or not Path(value).exists():
            continue
        p = Path(value).resolve()
        if protected_refusal(p, home, kraft_home) is None and p not in out:
            out.append(p)
    return out
