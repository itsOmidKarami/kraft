"""The files that travel with a step: read-only ones one way, read-write ones both ways. Member
names are absolute server paths without the leading slash, so each side maps them with its own
PathMap."""

from __future__ import annotations

import hashlib
import os
import tarfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILES, MAX_BYTES = 5000, 256 << 20


class FilesRefused(Exception):
    """str() is the reason, for the session log."""


@dataclass(frozen=True)
class Entry:
    path: Path  # absolute, on the server
    rw: bool  # comes back after the step


@dataclass
class Applied:
    written: list[Path] = field(default_factory=list)
    conflicts: list[Path] = field(
        default_factory=list
    )  # the server's file that kept its version ...
    kept: list[Path] = field(default_factory=list)  # ... and, in the same order, the step's copy


def entries(
    *,
    results: Iterable[Path],
    ro: Iterable[Path],
    rw_dirs: Iterable[Path],
    rw_files: Iterable[Path],
) -> list[Entry]:
    seen: set[Path] = set()
    out: list[Entry] = []
    for rw, group in ((False, [*results, *ro]), (True, [*rw_dirs, *rw_files])):
        for p in map(Path, group):
            if p not in seen:
                seen.add(p)
                out.append(Entry(p, rw))
    return out


def _walk(root: Path) -> Iterator[Path]:
    """Regular files under `root` (or `root` itself): never a link, device or FIFO."""
    if root.is_symlink() or not root.exists():
        return
    if root.is_file():
        yield root
        return
    for here, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(here) / d).is_symlink()]
        for n in names:
            p = Path(here) / n
            if p.is_file() and not p.is_symlink():
                yield p


def _name(p: Path) -> str:
    return str(p).lstrip("/")


def _sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _too_many(what: str) -> FilesRefused:
    return FilesRefused(
        f"{what} is more than {MAX_FILES} files or {MAX_BYTES >> 20} MiB; "
        "trim the item's docs/ folder"
    )


def pack(items: Sequence[Entry], dest: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    total = 0
    with tarfile.open(dest, "w:gz") as tar:
        for e in items:
            for p in _walk(e.path):
                total += p.stat().st_size
                if len(manifest) >= MAX_FILES or total > MAX_BYTES:
                    raise _too_many("the files this step needs")
                manifest[_name(p)] = _sha_file(p)
                tar.add(p, arcname=_name(p), recursive=False)
    return manifest


def unpack(
    archive: Path, mapper: Callable[[str], Path], *, keep_existing: bool = False
) -> dict[str, str]:
    """Write the archive's files where `mapper` says; `keep_existing` leaves a file that is already
    there (a co-task of the same step has been working in it). Returns {member name: sha256 of what
    the server sent}."""
    written: dict[str, str] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar:
            if not m.isreg() or ".." in Path(m.name).parts or m.name.startswith("/"):
                continue
            target = Path(mapper(m.name))
            target.parent.mkdir(parents=True, exist_ok=True)
            data = tar.extractfile(m).read()
            written[m.name] = _sha(data)
            if keep_existing and target.exists():
                continue
            tmp = target.with_name(target.name + ".kraft-tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
    return written


def changed(
    items: Sequence[Entry], mapper: Callable[[str], Path], pushed: Mapping[str, str], dest: Path
) -> bool:
    """On the machine: a tar of the read-write files that are new or differ from what was pushed.
    False, with nothing written, when there are none."""
    count = 0
    with tarfile.open(dest, "w:gz") as tar:
        for e in items:
            if not e.rw:
                continue
            here = Path(mapper(_name(e.path)))
            for p in _walk(here):
                rel = "" if p == here else f"/{p.relative_to(here).as_posix()}"
                name = f"{_name(e.path)}{rel}"
                if pushed.get(name) == _sha_file(p):
                    continue
                tar.add(p, arcname=name, recursive=False)
                count += 1
    if count == 0:
        dest.unlink(missing_ok=True)
    return count > 0


def _owner(name: str, items: Sequence[Entry]) -> Entry | None:
    path = Path("/" + name)
    for e in items:
        if e.rw and (path == e.path or e.path in path.parents):
            return e
    return None


def _no_links(dest: Path, entry: Entry) -> bool:
    """Neither the file nor any directory down from the read-write entry is a link."""
    stop = entry.path.parent
    for p in (dest, *dest.parents):
        if p == stop:
            return True
        if p.is_symlink():
            return False
    return True


def apply_returned(
    archive: Path, items: Sequence[Entry], pushed: Mapping[str, str], keep_dir: Path
) -> Applied:
    """On the server: write back what the step changed, but only inside the read-write entries.
    Under a `docs` folder, a file the server's copy changed since the package left keeps the
    server's version; the step's is saved under `keep_dir`. The archive is untrusted: it is counted
    before anything is written, so a refusal applies nothing."""
    applied = Applied()
    with tarfile.open(archive, "r:gz") as tar:
        members = [m for m in tar if m.isreg()]
        if len(members) > MAX_FILES or sum(m.size for m in members) > MAX_BYTES:
            raise _too_many("what the step returned")
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar:
            if not m.isreg() or ".." in Path(m.name).parts or m.name.startswith("/"):
                continue
            entry = _owner(m.name, items)
            dest = Path("/" + m.name)
            if entry is None or not _no_links(dest, entry):
                continue
            data = tar.extractfile(m).read()
            if (
                entry.path.name == "docs"
                and dest.exists()
                and pushed.get(m.name) != _sha_file(dest)
                and _sha(data) != _sha_file(dest)
            ):
                keep = keep_dir / (
                    dest.name if dest == entry.path else dest.relative_to(entry.path)
                )
                keep.parent.mkdir(parents=True, exist_ok=True)
                keep.write_bytes(data)
                applied.conflicts.append(dest)
                applied.kept.append(keep)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".kraft-tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            applied.written.append(dest)
    return applied
