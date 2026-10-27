"""Fetching a collection and extracting one add-on from it.

A git collection is fetched into a bare mirror that never has a working tree,
and an add-on's files are written from raw blob bytes, never through `git
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

from kraft.addons import manifest
from kraft.config import GIT_READ_ENV
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
#: What git says when the remote answered and has no such ref: not a failure of
#: the network, and no retry will find it.
_MISSING_REF = re.compile(r"couldn't find remote ref|invalid refspec", re.IGNORECASE)
_NO_SHALLOW = re.compile(r"does not support shallow", re.IGNORECASE)


class FetchError(Exception):
    """A collection that could not be fetched or read. `kind` is `network`,
    `auth` or `refused`, so doctor and `update --check` can tell them apart."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class AddonRefused(Exception):
    """An add-on whose files Kraft will not take. The message names the file."""


@dataclass(frozen=True)
class Extracted:
    """One add-on's files in the fixed layout, ready to hash and store."""

    #: Relative path to (git mode `100644` or `100755`, bytes).
    files: dict[str, tuple[str, bytes]]
    #: Files that look like part of the layout and are not (`chains/Ship.yaml`).
    skipped: tuple[str, ...] = ()
    #: The git tree id of the add-on's directory; None for a directory collection.
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


def mirror_path(addons_dir: Path, name: str, url: str) -> Path:
    """Keyed by a hash of the URL as well as the name, so a collection re-added
    from another source never reuses the old mirror."""
    return addons_dir / "mirrors" / f"{name}-{hashlib.sha256(url.encode()).hexdigest()[:12]}.git"


def _scheme(url: str) -> str:
    """The one transport this URL may use; `user@host:path` is ssh."""
    return url.split("://", 1)[0] if "://" in url else "ssh"


def fetch(addons_dir: Path, name: str, url: str, ref: str | None) -> tuple[Path, str]:
    """Fetch `ref` (the remote's default branch when None) of the collection at
    `url` into its mirror; return the mirror and the commit it resolved to.
    Always from `url`, never a remote stored in the mirror, and only that ref."""
    mirror = mirror_path(addons_dir, name, url)
    if not mirror.is_dir():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _git("init", "--quiet", "--bare", str(mirror))

    def run(*depth: str) -> None:
        _git(
            "-c", "transfer.fsckObjects=true",
            "-c", "protocol.allow=never",
            "-c", f"protocol.{_scheme(url)}.allow=always",
            "fetch", "--quiet", "--no-tags", "--no-recurse-submodules", *depth,
            "--end-of-options", url, ref or "HEAD",
            git_dir=mirror,
            timeout=FETCH_TIMEOUT_S,
        )  # fmt: skip

    try:
        try:
            # Only the one commit: extraction reads its tree, never its history.
            run("--depth=1")
        except FetchError as exc:
            if not _NO_SHALLOW.search(str(exc)):
                raise
            run()  # a dumb-HTTP host serves no shallow fetch, only the whole history
    except FetchError as exc:
        if _MISSING_REF.search(str(exc)):
            raise FetchError(
                "refused", f"{redact(url)}: ref {ref or 'HEAD'!r} does not exist: {exc}"
            ) from exc
        kind = "auth" if _AUTH.search(str(exc)) else "network"
        raise FetchError(kind, f"{redact(url)}: {exc}") from exc
    commit = _git(
        "rev-parse", "--verify", "--end-of-options", "FETCH_HEAD^{commit}", git_dir=mirror
    )
    return mirror, commit.decode().strip()


def fetch_commit(addons_dir: Path, name: str, url: str, commit: str) -> Path:
    """The collection's mirror, holding `commit`: fetched by its id from `url`
    only when the mirror does not have it already. Raises `FetchError` when
    the remote no longer serves it (history rewritten, branch deleted)."""
    mirror = mirror_path(addons_dir, name, url)
    if mirror.is_dir():
        try:
            _git("cat-file", "-e", f"{commit}^{{commit}}", git_dir=mirror)
            return mirror
        except FetchError:
            pass
    return fetch(addons_dir, name, url, commit)[0]


def pin(mirror: Path, commit: str) -> None:
    """Keep `commit` in the mirror for good: a ref names it, so `git gc` never
    prunes it even after the author rewrites history."""
    _git("update-ref", f"refs/kraft/pinned/{commit}", commit, git_dir=mirror)


def _cap(rel: str) -> int:
    if rel.endswith(".json"):
        return MAX_JSON
    return MAX_SKILL if rel.endswith("SKILL.md") else MAX_YAML


def _select(where: str, entries: Iterable[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """From every `(relative path, git mode)` under an add-on directory: the
    layout's files, and the look-alikes left out. A symlink or submodule where
    the layout would be read is refused, not skipped."""
    kept: list[str] = []
    skipped: list[str] = []
    for rel, mode in entries:
        if mode not in _REGULAR:
            if manifest.shadows_layout(rel):
                what = {"120000": "a symlink", "160000": "a submodule"}.get(mode, f"mode {mode}")
                raise AddonRefused(f"{where}/{rel}: is {what}; an add-on's files are regular files")
            continue
        if manifest.in_layout(rel):
            kept.append(rel)
        elif manifest.near_layout(rel):
            skipped.append(rel)
    if len(kept) > MAX_FILES:
        raise AddonRefused(f"{where}: {len(kept)} files; an add-on holds at most {MAX_FILES}")
    return sorted(kept), sorted(skipped)


def _check_sizes(where: str, sizes: dict[str, int]) -> None:
    for rel, size in sizes.items():
        if size > _cap(rel):
            raise AddonRefused(f"{where}/{rel}: {size} bytes; at most {_cap(rel)}")
    total = sum(sizes.values())
    if total > MAX_TOTAL:
        raise AddonRefused(f"{where}: {total} bytes in all; an add-on holds at most {MAX_TOTAL}")


def _check_bytes(where: str, rel: str, data: bytes) -> None:
    if data.startswith(_LFS):
        raise AddonRefused(
            f"{where}/{rel}: is a Git LFS pointer, not the file; commit the file itself"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AddonRefused(f"{where}/{rel}: is not UTF-8 text") from exc
    if (bad := manifest.hidden_character(text)) is not None:
        raise AddonRefused(f"{where}/{rel}: carries {bad}, which a reader cannot see")


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
        raise AddonRefused(f"{commit[:12]}: no regular file {path}")
    oid = found[0][1]
    size = _sizes(mirror, [oid])[oid]
    if size > cap:
        raise AddonRefused(f"{commit[:12]}:{path}: {size} bytes; at most {cap}")
    return _blobs(mirror, [oid])[oid]


def read_regular(path: Path, cap: int) -> bytes:
    """`path`'s bytes, if it is a regular file (not a symlink, a FIFO or a
    device) of at most `cap` bytes; else `FetchError("refused")`. Opened
    `O_NOFOLLOW | O_NONBLOCK`, so a FIFO cannot block the read."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise FetchError("refused", f"{path}: {exc}") from exc
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise FetchError("refused", f"{path}: not a regular file")
        data = fh.read(cap + 1)
    if len(data) > cap:
        raise FetchError("refused", f"{path}: over {cap} bytes")
    return data


def extract_git(mirror: Path, commit: str, source: str) -> Extracted:
    """The add-on at `source` (as `collection.json` writes it) in `commit`."""
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
    """The add-on in the directory `root`, as it stands: a directory
    collection. Nothing is followed: a symlink is reported as one, a file is
    opened with `O_NOFOLLOW`, and a layout file with a second hard link is
    refused, so the bytes reviewed are the bytes in this directory."""
    where = str(root)
    found: dict[str, tuple[str, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root:
            # Only the layout's own folders are entered: an add-on directory may
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
                    raise AddonRefused(f"{where}/{rel}: has a second hard link")
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


#: How every refusal of a store file ends: the store was written once, read-only.
CHANGED = "the store was changed after it was installed"


class StoreMismatch(AddonRefused):
    """A file in a store that does not match the digest the store was written
    with. The message names the store-relative file and ends with `CHANGED`."""


@dataclass(frozen=True)
class StoreFiles:
    """A store's `.kraft-digest`, checked against the store's name: every read
    of a store file after the store is written goes through `read`."""

    root: Path
    #: Relative path to (git mode, SHA-256 hex), as the manifest lists it.
    listed: Mapping[str, tuple[str, str]]
    #: The manifest's bytes, as read.
    raw: bytes

    def read(self, rel: str, *, optional: bool = False) -> bytes | None:
        """`rel`'s bytes, only if they hash to its manifest line; the bytes
        returned are the bytes hashed. A listed file that is gone, a file at a
        path the manifest does not list, and a symlink or other non-regular
        file are `StoreMismatch`. With `optional`, a path neither listed nor
        present is None."""
        path = self.root / rel
        if rel not in self.listed:
            if optional and not os.path.lexists(path):
                return None
            raise StoreMismatch(f"{rel} is not in its digest; {CHANGED}")
        if not os.path.lexists(path):
            raise StoreMismatch(f"{rel} is missing; {CHANGED}")
        try:
            data = read_regular(path, _cap(rel))
        except FetchError as exc:
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise StoreMismatch(f"{rel} is not a regular file; {CHANGED}") from exc
            raise StoreMismatch(f"{rel} does not match its digest; {CHANGED}") from exc
        if hashlib.sha256(data).hexdigest() != self.listed[rel][1]:
            raise StoreMismatch(f"{rel} does not match its digest; {CHANGED}")
        return data

    def paths(self, prefix: str) -> list[str]:
        """The listed paths that start with `prefix`, sorted."""
        return sorted(rel for rel in self.listed if rel.startswith(prefix))


def open_store(root: Path) -> StoreFiles:
    """The store at `root`, its `.kraft-digest` read (bounded, no symlink) and
    checked to hash to the store's name: the digest the lock or an item's pin
    recorded. `StoreMismatch` when it does not, or cannot be read."""
    try:
        raw = read_regular(root / DIGEST_FILE, MAX_FILES * 512)
    except FetchError as exc:
        raise StoreMismatch(f"{DIGEST_FILE} cannot be read; {CHANGED}") from exc
    if hashlib.sha256(raw).hexdigest() != root.name:
        raise StoreMismatch(f"{DIGEST_FILE} does not hash to the store's name; {CHANGED}")
    listed: dict[str, tuple[str, str]] = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        fields = line.split("\0")
        if len(fields) != 3 or not fields[0] or len(fields[2]) != 64:
            raise StoreMismatch(f"{DIGEST_FILE} is malformed; {CHANGED}")
        listed[fields[0]] = (fields[1], fields[2])
    return StoreFiles(root, listed, raw)


def verify_store(root: Path) -> Extracted:
    """The whole store at `root`, checked: its manifest hashes to its name, and
    its files, as they stand, give that manifest. Returns them."""
    store = open_store(root)
    extracted = extract_dir(root)
    if digest_manifest(extracted.files) != store.raw:
        raise StoreMismatch(f"its files do not match its digest; {CHANGED}")
    return extracted


def _lay_out(root: Path, files: Mapping[str, tuple[str, bytes]]) -> None:
    for rel, (_mode, data) in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / DIGEST_FILE).write_bytes(digest_manifest(files))


def write_scratch(parent: Path, extracted: Extracted) -> Path:
    """`extracted` laid out as a store would be, at `parent/<digest>/`, with
    its `.kraft-digest`, but writable: a candidate not yet installed, read
    through the same `open_store` as an installed one. Returns the root."""
    root = parent / digest(extracted.files).removeprefix("sha256:")
    root.mkdir(parents=True, exist_ok=True)
    _lay_out(root, extracted.files)
    return root


def remove_tree(path: Path) -> None:
    """Delete a store, or a copy of one: its folders are read-only. A symlink
    is removed itself, and nothing is followed through one."""
    if path.is_symlink():
        path.unlink()
        return
    path.chmod(0o755)
    for dirpath, dirnames, _files in os.walk(path):  # top-down: made writable before entered
        for name in dirnames:
            if not (Path(dirpath) / name).is_symlink():
                (Path(dirpath) / name).chmod(0o755)
    shutil.rmtree(path)


def _discard(path: Path) -> None:
    """`remove_tree`, best effort: what is left stays in `staging/` for GC."""
    try:
        if os.path.lexists(path):
            remove_tree(path)
    except OSError:
        pass


def write_store(addons_dir: Path, extracted: Extracted) -> Path:
    """`extracted` as `store/<digest>/`, addressed by its content alone, so two
    installs of the same content share it. Written in `staging/` and renamed
    into place (one filesystem, so the rename is atomic), then never changed:
    files and directories are read-only. The digest's manifest sits beside the
    files as `.kraft-digest`, outside the hashed set. A store already there is
    reused only if it is a directory and `verify_store` passes; one edited
    since it was written, or a symlink, is replaced by these files, which hash
    to its name by construction."""
    final = addons_dir / "store" / digest(extracted.files).removeprefix("sha256:")
    # A link, or a plain file where the store goes, is never reused or entered.
    stale = final.is_symlink() or (os.path.lexists(final) and not final.is_dir())
    if not stale and final.is_dir():
        try:
            verify_store(final)
            return final
        except (AddonRefused, OSError):
            stale = True
    staging = addons_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=staging))
    _lay_out(tmp, extracted.files)
    for rel, (mode, _data) in extracted.files.items():
        (tmp / rel).chmod(0o555 if mode == "100755" else 0o444)
    (tmp / DIGEST_FILE).chmod(0o444)
    folders = [p for p in tmp.rglob("*") if p.is_dir()]
    aside = staging / f"{tmp.name}.stale"
    if stale:
        try:
            if final.is_symlink() or not final.is_dir():
                final.unlink()  # never chmod or move what a link points at
            else:
                # macOS will not move a read-only directory to another parent.
                final.chmod(0o755)
                os.rename(final, aside)
        except FileNotFoundError:
            pass  # another writer moved it first
    try:
        # Renamed while still writable: macOS refuses to move a read-only
        # directory to another parent.
        os.rename(tmp, final)
    except OSError:
        # Another writer renamed the same content into place first.
        if not final.is_dir():
            _discard(aside)
            raise
        shutil.rmtree(tmp)
    else:
        for folder in (final, *(final / p.relative_to(tmp) for p in folders)):
            folder.chmod(0o555)
    _discard(aside)
    return final
