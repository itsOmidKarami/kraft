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
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from kraft.config import GIT_READ_ENV
from kraft.plugins import manifest
from kraft.worker.sandbox import harden_host_git_env

FETCH_TIMEOUT_S = 60

#: Checked from sizes alone, before any byte of a file is read.
MAX_YAML = 1 << 20
MAX_JSON = 256 << 10
MAX_SKILL = 256 << 10
MAX_FILES = 500
MAX_TOTAL = 20 << 20
DIGEST_FILE = ".kraft-digest"

_REGULAR = ("100644", "100755")
_LFS = b"version https://git-lfs.github.com/spec/"

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


class PluginRefused(Exception):
    """A plugin whose files Kraft will not take. The message names the file."""


@dataclass(frozen=True)
class Extracted:
    """One plugin's files in the fixed layout, ready to hash and store."""

    #: Relative path to (git mode `100644` or `100755`, bytes).
    files: dict[str, tuple[str, bytes]]
    #: Files that look like part of the layout and are not (`chains/Ship.yaml`).
    skipped: tuple[str, ...] = ()
    #: The git tree id of the plugin's directory; None for a directory collection.
    tree: str | None = None


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


def fetch_commit(plugins_dir: Path, name: str, url: str, commit: str) -> Path:
    """The collection's mirror, holding `commit`: fetched by its id from `url`
    only when the mirror does not have it already. Raises `FetchError` when
    the remote no longer serves it (history rewritten, branch deleted)."""
    mirror = mirror_path(plugins_dir, name, url)
    if mirror.is_dir():
        try:
            _git("cat-file", "-e", f"{commit}^{{commit}}", git_dir=mirror)
            return mirror
        except FetchError:
            pass
    return fetch(plugins_dir, name, url, commit)[0]


def pin(mirror: Path, commit: str) -> None:
    """Keep `commit` in the mirror for good: a ref names it, so `git gc` never
    prunes it even after the author rewrites history."""
    _git("update-ref", f"refs/kraft/pinned/{commit}", commit, git_dir=mirror)


def _cap(rel: str) -> int:
    if rel.endswith(".json"):
        return MAX_JSON
    return MAX_SKILL if rel.endswith("SKILL.md") else MAX_YAML


def _select(where: str, entries: Iterable[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """From every `(relative path, git mode)` under a plugin directory: the
    layout's files, and the look-alikes left out. A symlink or submodule where
    the layout would be read is refused, not skipped."""
    kept: list[str] = []
    skipped: list[str] = []
    for rel, mode in entries:
        if mode not in _REGULAR:
            if manifest.shadows_layout(rel):
                what = {"120000": "a symlink", "160000": "a submodule"}.get(mode, f"mode {mode}")
                raise PluginRefused(f"{where}/{rel}: is {what}; a plugin's files are regular files")
            continue
        if manifest.in_layout(rel):
            kept.append(rel)
        elif manifest.near_layout(rel):
            skipped.append(rel)
    if len(kept) > MAX_FILES:
        raise PluginRefused(f"{where}: {len(kept)} files; a plugin holds at most {MAX_FILES}")
    return sorted(kept), sorted(skipped)


def _check_sizes(where: str, sizes: dict[str, int]) -> None:
    for rel, size in sizes.items():
        if size > _cap(rel):
            raise PluginRefused(f"{where}/{rel}: {size} bytes; at most {_cap(rel)}")
    total = sum(sizes.values())
    if total > MAX_TOTAL:
        raise PluginRefused(f"{where}: {total} bytes in all; a plugin holds at most {MAX_TOTAL}")


def _check_bytes(where: str, rel: str, data: bytes) -> None:
    if data.startswith(_LFS):
        raise PluginRefused(
            f"{where}/{rel}: is a Git LFS pointer, not the file; commit the file itself"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginRefused(f"{where}/{rel}: is not UTF-8 text") from exc
    if (bad := manifest.hidden_character(text)) is not None:
        raise PluginRefused(f"{where}/{rel}: carries {bad}, which a reader cannot see")


def _ls(mirror: Path, commit: str, prefix: str) -> list[tuple[str, str, str]]:
    """`(mode, object id, full path)` of every entry under `prefix`."""
    out = _git("ls-tree", "-r", "-z", "--full-tree", commit, "--", prefix, git_dir=mirror)
    entries = []
    for record in out.split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            mode, _type, oid = meta.decode().split()
            entries.append((mode, oid, path.decode("utf-8", "replace")))
    return entries


def _sizes(mirror: Path, oids: list[str]) -> dict[str, int]:
    out = _git(
        "cat-file", "--batch-check", git_dir=mirror, input="".join(f"{o}\n" for o in oids).encode()
    )
    return {line.split()[0]: int(line.split()[2]) for line in out.decode().splitlines()}


def _blobs(mirror: Path, oids: list[str]) -> dict[str, bytes]:
    out = _git(
        "cat-file", "--batch", git_dir=mirror, input="".join(f"{o}\n" for o in oids).encode()
    )
    blobs: dict[str, bytes] = {}
    at = 0
    for _ in oids:
        end = out.index(b"\n", at)
        oid, _type, size = out[at:end].decode().split()
        blobs[oid] = out[end + 1 : end + 1 + int(size)]
        at = end + 1 + int(size) + 1
    return blobs


def read_file(mirror: Path, commit: str, path: str, cap: int) -> bytes:
    """One regular file of `commit`, at most `cap` bytes: `collection.json`."""
    found = [(mode, oid) for mode, oid, full in _ls(mirror, commit, path) if full == path]
    if not found or found[0][0] not in _REGULAR:
        raise PluginRefused(f"{commit[:12]}: no regular file {path}")
    oid = found[0][1]
    size = _sizes(mirror, [oid])[oid]
    if size > cap:
        raise PluginRefused(f"{commit[:12]}:{path}: {size} bytes; at most {cap}")
    return _blobs(mirror, [oid])[oid]


def extract_git(mirror: Path, commit: str, source: str) -> Extracted:
    """The plugin at `source` (as `collection.json` writes it) in `commit`."""
    base = source.removeprefix("./")
    where = f"{commit[:12]}:{base}"
    under = {
        full[len(base) + 1 :]: (mode, oid)
        for mode, oid, full in _ls(mirror, commit, base)
        if full.startswith(base + "/")
    }
    kept, skipped = _select(where, ((rel, mode) for rel, (mode, _oid) in under.items()))
    oids = list(dict.fromkeys(under[rel][1] for rel in kept))  # two files may share one blob
    sizes = _sizes(mirror, oids) if oids else {}
    _check_sizes(where, {rel: sizes[under[rel][1]] for rel in kept})
    blobs = _blobs(mirror, oids) if oids else {}
    files: dict[str, tuple[str, bytes]] = {}
    for rel in kept:
        mode, oid = under[rel]
        _check_bytes(where, rel, blobs[oid])
        files[rel] = (mode, blobs[oid])
    tree = _git("rev-parse", "--verify", "--end-of-options", f"{commit}:{base}", git_dir=mirror)
    return Extracted(files, tuple(skipped), tree.decode().strip())


def extract_dir(root: Path) -> Extracted:
    """The plugin in the directory `root`, as it stands: a directory
    collection. Nothing is followed: a symlink is reported as one, a file is
    opened with `O_NOFOLLOW`, and a layout file with a second hard link is
    refused, so the bytes reviewed are the bytes in this directory."""
    where = str(root)
    found: dict[str, tuple[str, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root:
            # Only the layout's own folders are entered: a plugin directory may
            # share a repo with anything.
            entered = [d for d in dirnames if d in ("chains", "skills", ".kraft")]
        else:
            entered = list(dirnames)
        for name in (*entered, *filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode):
                found[rel] = ("120000", 0)
            elif stat.S_ISREG(st.st_mode):
                if st.st_nlink > 1 and manifest.in_layout(rel):
                    raise PluginRefused(f"{where}/{rel}: has a second hard link")
                found[rel] = ("100755" if st.st_mode & 0o100 else "100644", st.st_size)
        dirnames[:] = [d for d in entered if not (Path(dirpath) / d).is_symlink()]
    kept, skipped = _select(where, ((rel, mode) for rel, (mode, _size) in found.items()))
    _check_sizes(where, {rel: found[rel][1] for rel in kept})
    files: dict[str, tuple[str, bytes]] = {}
    for rel in kept:
        fd = os.open(root / rel, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            data = fh.read(_cap(rel) + 1)
        _check_sizes(where, {rel: len(data)})
        _check_bytes(where, rel, data)
        files[rel] = (found[rel][0], data)
    return Extracted(files, tuple(skipped))


def digest_manifest(files: Mapping[str, tuple[str, bytes]]) -> bytes:
    """One line per file, sorted by the path's bytes: path, mode and the
    SHA-256 of the content, NUL-separated. Paths are lowercase ASCII, so the
    order and the bytes are the same on every machine."""
    return b"".join(
        rel.encode()
        + b"\0"
        + mode.encode()
        + b"\0"
        + hashlib.sha256(data).hexdigest().encode()
        + b"\n"
        for rel, (mode, data) in sorted(files.items(), key=lambda item: item[0].encode())
    )


def digest(files: Mapping[str, tuple[str, bytes]]) -> str:
    return "sha256:" + hashlib.sha256(digest_manifest(files)).hexdigest()


def write_store(plugins_dir: Path, extracted: Extracted) -> Path:
    """`extracted` as `store/<digest>/`, addressed by its content alone, so two
    installs of the same content share it. Written in `staging/` and renamed
    into place (one filesystem, so the rename is atomic), then never changed:
    files and directories are read-only. The digest's manifest sits beside the
    files as `.kraft-digest`, outside the hashed set."""
    final = plugins_dir / "store" / digest(extracted.files).removeprefix("sha256:")
    if final.is_dir():
        return final
    staging = plugins_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=staging))
    for rel, (mode, data) in extracted.files.items():
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o555 if mode == "100755" else 0o444)
    (tmp / DIGEST_FILE).write_bytes(digest_manifest(extracted.files))
    (tmp / DIGEST_FILE).chmod(0o444)
    folders = [p for p in tmp.rglob("*") if p.is_dir()]
    try:
        # Renamed while still writable: macOS refuses to move a read-only
        # directory to another parent.
        os.rename(tmp, final)
    except OSError:
        # Another writer renamed the same content into place first.
        if not final.is_dir():
            raise
        shutil.rmtree(tmp)
        return final
    for folder in (final, *(final / p.relative_to(tmp) for p in folders)):
        folder.chmod(0o555)
    return final
