"""Fetching a collection and extracting one plugin from it.

A git collection is fetched into a bare mirror that never has a working tree,
and a plugin's files are written from raw blob bytes, never through `git
archive` or a checkout: the same commit then gives the same bytes and the same
digest on every machine, whatever its git config, and no repository code (a
hook, a filter, a `.gitattributes` conversion) ever runs.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from kraft.config import GIT_READ_ENV
from kraft.worker.sandbox import harden_host_git_env

FETCH_TIMEOUT_S = 60

_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@")
_AUTH = re.compile(
    r"Authentication failed|Permission denied|could not read Username|terminal prompts disabled",
    re.IGNORECASE,
)


class FetchError(Exception):
    """A collection that could not be fetched or read. `kind` is `network`,
    `auth` or `refused`, so doctor and `update --check` can tell them apart."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def redact(text: str) -> str:
    """`text` with the userinfo of every URL in it removed: git's own error
    text repeats the URL it was given."""
    return _USERINFO.sub(r"\1", text)


def _git(
    *args: str, git_dir: Path | None = None, input: bytes | None = None, timeout: int | None = None
) -> bytes:
    """One git command that never prompts and never runs a hook."""
    env = {
        **os.environ,
        **GIT_READ_ENV,
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
    }
    harden_host_git_env(env)
    where = ["--git-dir", str(git_dir)] if git_dir is not None else []
    command = ["git", *where, "-c", "core.hooksPath=/dev/null", *args]
    try:
        done = subprocess.run(
            command, input=input, capture_output=True, env=env, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise FetchError("network", f"git timed out after {timeout}s") from exc
    if done.returncode != 0:
        raise FetchError("refused", redact(done.stderr.decode(errors="replace").strip()))
    return done.stdout


def mirror_path(plugins_dir: Path, name: str, url: str) -> Path:
    """Keyed by a hash of the URL as well as the name, so a collection re-added
    from another source never reuses the old mirror."""
    return plugins_dir / "mirrors" / f"{name}-{hashlib.sha256(url.encode()).hexdigest()[:12]}.git"


def _scheme(url: str) -> str:
    """The one transport this URL may use; `user@host:path` is ssh."""
    return url.split("://", 1)[0] if "://" in url else "ssh"


def fetch(plugins_dir: Path, name: str, url: str, ref: str | None) -> tuple[Path, str]:
    """Fetch `ref` (the remote's default branch when None) of the collection at
    `url` into its mirror; return the mirror and the commit it resolved to.
    Always from `url`, never a remote stored in the mirror, and only that ref."""
    mirror = mirror_path(plugins_dir, name, url)
    if not mirror.is_dir():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _git("init", "--quiet", "--bare", str(mirror))
    try:
        _git(
            "-c", "transfer.fsckObjects=true",
            "-c", "protocol.allow=never",
            "-c", f"protocol.{_scheme(url)}.allow=always",
            "fetch", "--quiet", "--no-tags", "--no-recurse-submodules",
            "--end-of-options", url, ref or "HEAD",
            git_dir=mirror,
            timeout=FETCH_TIMEOUT_S,
        )  # fmt: skip
    except FetchError as exc:
        kind = "auth" if _AUTH.search(str(exc)) else "network"
        raise FetchError(kind, f"{redact(url)}: {exc}") from exc
    commit = _git(
        "rev-parse", "--verify", "--end-of-options", "FETCH_HEAD^{commit}", git_dir=mirror
    )
    return mirror, commit.decode().strip()


def pin(mirror: Path, commit: str) -> None:
    """Keep `commit` in the mirror for good: a ref names it, so `git gc` never
    prunes it even after the author rewrites history."""
    _git("update-ref", f"refs/kraft/pinned/{commit}", commit, git_dir=mirror)
