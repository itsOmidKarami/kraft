"""Git LFS objects beside a bundle. A bundle carries commits, not the objects their LFS pointers
name, so those travel as their own blobs. Pointers are read here with plain git, never by git-lfs:
what an object is, and whether one that came back is allowed in, is decided on the server without
trusting the machine or needing git-lfs."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from kraft.remote import bundle
from kraft.worker import sandbox as _sandbox

POINTER_MAX = 1024
_VERSION = "version https://git-lfs.github.com/spec/v1"
_OID = re.compile(r"[0-9a-f]{64}")
_OID_LINE = re.compile(r"oid sha256:([0-9a-f]{64})")
_SIZE_LINE = re.compile(r"size ([0-9]+)")


class LfsRefused(Exception):
    """str() is the reason, for the session log."""


def uses_lfs(repo_path: str | Path) -> bool:
    """The root `.gitattributes` routes some files through the lfs filter."""
    attrs = Path(repo_path) / ".gitattributes"
    return attrs.is_file() and "filter=lfs" in attrs.read_text(errors="replace")


def parse_pointer(data: bytes) -> tuple[str, int] | None:
    """`(oid, size)` of an LFS spec v1 pointer, None for any other bytes."""
    if len(data) > POINTER_MAX:
        return None
    try:
        lines = data.decode().splitlines()
    except UnicodeDecodeError:
        return None
    if not lines or lines[0] != _VERSION:
        return None
    oid = size = None
    for line in lines[1:]:
        if m := _OID_LINE.fullmatch(line):
            oid = m.group(1)
        elif m := _SIZE_LINE.fullmatch(line):
            size = int(m.group(1))
    return (oid, size) if oid is not None and size is not None else None


def _env() -> dict[str, str]:
    env = dict(os.environ)
    _sandbox.harden_host_git_env(env)
    return env


def _pointers_among(repo: Path, candidates: Iterable[str]) -> dict[str, int]:
    """The pointers among blob ids `candidates`: small blobs, read with one `cat-file --batch`."""
    ids = list(dict.fromkeys(candidates))
    if not ids:
        return {}
    checked = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        ],
        input="\n".join(ids) + "\n",
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    small = []
    for line in checked.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "blob" and int(parts[2]) <= POINTER_MAX:
            small.append(parts[0])
    if not small:
        return {}
    read = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=("\n".join(small) + "\n").encode(),
        capture_output=True,
        env=_env(),
        check=False,
    ).stdout
    found: dict[str, int] = {}
    at = 0
    while at < len(read):
        eol = read.find(b"\n", at)
        header = read[at:eol].split()
        if eol < 0 or len(header) != 3 or header[1] != b"blob":
            break
        end = eol + 1 + int(header[2])
        if p := parse_pointer(read[eol + 1 : end]):
            found[p[0]] = p[1]
        at = end + 1
    return found


def tree_pointers(repo: Path, rev: str) -> dict[str, int]:
    """The pointers in `rev`'s tree, `{oid: size}`."""
    listing = bundle._git(repo, "ls-tree", "-r", "-l", "-z", rev).stdout
    blobs = []
    for entry in listing.split("\0"):
        meta = entry.partition("\t")[0].split()
        if (
            len(meta) == 4
            and meta[1] == "blob"
            and meta[3].isdigit()
            and int(meta[3]) <= POINTER_MAX
        ):
            blobs.append(meta[2])
    return _pointers_among(repo, blobs)


def new_pointers(repo: Path, tip: str, exclude: Iterable[str]) -> dict[str, int]:
    """The pointers among the blobs `tip` has and `exclude` does not."""
    skip = [f"^{s}" for s in bundle.usable(repo, exclude)]
    listing = bundle._git(repo, "rev-list", "--objects", tip, *skip).stdout
    return _pointers_among(repo, (line.split(" ", 1)[0] for line in listing.splitlines()))


def store(repo: Path) -> Path:
    """`<git common dir>/lfs/objects`, the one every linked worktree of `repo` shares."""
    common = bundle._git(repo, "rev-parse", "--git-common-dir").stdout.strip()
    return (Path(repo) / common).resolve() / "lfs" / "objects"


def object_path(store: Path, oid: str) -> Path:
    return store / oid[0:2] / oid[2:4] / oid


def held(store: Path) -> list[str]:
    """The oids with a file in `store`."""
    if not store.is_dir():
        return []
    return sorted(
        p.name
        for p in store.glob("??/??/*")
        if _OID.fullmatch(p.name) and p.parent.parent.name == p.name[:2] and p.is_file()
    )


def _sha256(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def take(store: Path, objects: Mapping[str, tuple[Path, int | None]]) -> None:
    """Move each `oid: (src, size)` into `store`, all or nothing: every object is checked against
    its oid (and `size` when given) before any is moved, and `src` is removed after."""
    for oid, (src, size) in objects.items():
        if not _OID.fullmatch(oid):
            raise LfsRefused(f"{oid[:80]!r} is not an LFS object id")
        digest, length = _sha256(src)
        if digest != oid:
            raise LfsRefused(f"LFS object {oid} does not hash to its name")
        if size is not None and length != size:
            raise LfsRefused(f"LFS object {oid} is {length} bytes; its pointer says {size}")
    for oid, (src, _) in objects.items():
        dest = object_path(store, oid)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".incoming-")
            os.close(fd)
            try:
                shutil.copyfile(src, tmp)
                os.replace(tmp, dest)
            finally:
                Path(tmp).unlink(missing_ok=True)
        src.unlink(missing_ok=True)
