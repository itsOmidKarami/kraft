"""A repo's carried files: the state a test-selection tool keeps (`.testmondata`).

`carry` in repos.yaml names them. Kraft seeds each new worktree with the set and
saves it back after a passing local targeted round, so the tool starts warm.

The saved copy is one folder per repo under `run/carried/`, shared by the
daemon (`ensure_worktree`, the builtin) and the CLI (`kraft repo connect
--verify`), so a lock in one process is not enough. Instead each save writes the
whole set into a new **generation** directory and points a `current` symlink at
it with one atomic rename:

    run/carried/<repo>-<digest>/
        current -> gen-<time_ns>-<pid>
        gen-<time_ns>-<pid>/<carried files>, and `superseded` once not current

A reader resolves `current` once and copies the whole set from that directory,
so it never pairs one save's database with another's WAL, and a crash
mid-save leaves the old generation current. A generation is deleted an hour
after it stopped being current, not an hour after it was made, so a reader that
resolved `current` just before a save still has its directory.

Each item keeps a baseline (`item_paths.carried_record`): what was copied into
its worktree and those files' sizes and mtimes then. A save happens only when a
listed file differs from the baseline, so a round that never touched the
files cannot overwrite a newer copy with an old one.

Standard library only: `builtins` imports this at the top.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: (size, mtime_ns), what `lstat` says about a regular file.
Stat = tuple[int, int]

#: How long a generation outlives being replaced as `current`.
PRUNE_AFTER_S = 3600.0

_SUPERSEDED = "superseded"


@dataclass(frozen=True)
class CarryIn:
    """What copying the carried files into a worktree did."""

    #: "saved" (the repo's saved copy), "checkout" or "none".
    source: str
    carried: list[str]
    refused: list[str]
    #: A source file changed while it was being copied, so nothing was kept.
    torn: bool = False

    def as_record(self, files: dict[str, Stat]) -> dict:
        return {
            "source": self.source,
            "carried": list(self.carried),
            "refused": list(self.refused),
            "torn": self.torn,
            "files": {rel: list(st) for rel, st in files.items()},
        }


def stats(root: Path, rels: Iterable[str]) -> dict[str, Stat]:
    """`(size, mtime_ns)` of each of `rels` under `root` that is a regular file.
    A symlink or a missing file is absent."""
    found: dict[str, Stat] = {}
    for rel in rels:
        try:
            st = os.lstat(root / rel)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            found[rel] = (st.st_size, st.st_mtime_ns)
    return found


def store_dir(run_dirs, repo: str | Path) -> Path:
    """`run/carried/<name>-<digest>/`, one per repo checkout: the digest is of
    the resolved path, so two repos of one name do not share a copy and one
    spelled two ways does."""
    resolved = Path(repo).resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:12]
    return run_dirs.base / "carried" / f"{resolved.name}-{digest}"


def current_generation(store: Path) -> Path | None:
    """The directory `current` names, resolved once. None when there is no
    `current`, it dangles, or it points anywhere but a plain directory beside it."""
    try:
        name = os.readlink(store / "current")
    except OSError:
        return None
    if not name or "/" in name or name in (".", ".."):
        return None
    generation = store / name
    if generation.is_symlink() or not generation.is_dir():
        return None
    return generation


def saved_set(store: Path, rels: Iterable[str]) -> Path | None:
    """The current generation, if it holds at least one of `rels`."""
    generation = current_generation(store)
    if generation is None:
        return None
    return generation if stats(generation, rels) else None


def read_record(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def write_record(path: Path, record: dict) -> None:
    """Replace `path` in one rename, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def changed(record: dict | None, files: dict[str, Stat]) -> bool:
    """Whether a file in `files` is absent from, or differs from, the baseline.
    A vanished file is not a change. No record means nothing was copied in."""
    baseline = (record or {}).get("files") or {}
    return any(list(baseline.get(rel) or ()) != list(st) for rel, st in files.items())


def save(
    run_dirs,
    repo: str | Path,
    worktree: Path,
    rels: list[str],
    record_path: Path,
    *,
    now: float | None = None,
) -> bool:
    """Save the worktree's carried files as the repo's newest copy. False, and
    nothing written, when none of them changed since the baseline. A listed
    file the worktree lacks is not copied, so an old WAL never sits beside a
    newer database. On `OSError` the half-built generation is removed and
    `current` is untouched."""
    now = time.time() if now is None else now
    record = read_record(record_path)
    files = stats(worktree, rels)
    if not changed(record, files):
        return False
    store = store_dir(run_dirs, repo)
    store.mkdir(parents=True, exist_ok=True)
    generation = store / f"gen-{time.time_ns()}-{os.getpid()}"
    generation.mkdir()
    link = store / f".current-{generation.name}"
    try:
        for rel in files:
            target = generation / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(worktree / rel, target)
        previous = current_generation(store)
        os.symlink(generation.name, link)
        os.replace(link, store / "current")
    except OSError:
        shutil.rmtree(generation, ignore_errors=True)
        with contextlib.suppress(OSError):
            link.unlink()
        raise
    if previous is not None:
        with contextlib.suppress(OSError):
            (previous / _SUPERSEDED).write_text(str(now))
    prune(store, now=now)
    baseline = dict(record or CarryIn("none", [], []).as_record({}))
    baseline["files"] = {rel: list(st) for rel, st in files.items()}
    write_record(record_path, baseline)
    return True


def prune(store: Path, *, now: float) -> None:
    """Delete generations an hour after they stopped being current. One with no
    readable marker (a crash leftover, or another saver's build in progress) is
    stamped now and kept."""
    current = current_generation(store)
    for generation in store.glob("gen-*"):
        if not generation.is_dir() or generation == current:
            continue
        marker = generation / _SUPERSEDED
        try:
            since = float(marker.read_text())
        except (OSError, ValueError):
            with contextlib.suppress(OSError):
                marker.write_text(str(now))
            continue
        if now - since > PRUNE_AFTER_S:
            shutil.rmtree(generation, ignore_errors=True)


def report_lines(record: dict | None) -> list[str]:
    """What the carry did, for the `worktree_prepared` report and the log."""
    if record is None:
        return []
    if record.get("torn"):
        lines = [
            "carried files in the checkout changed while they were being copied, "
            "so none were carried and the tool starts cold"
        ]
    elif record.get("carried"):
        where = "saved copy" if record.get("source") == "saved" else "checkout"
        lines = [f"carried from the {where}: {', '.join(record['carried'])}"]
    else:
        lines = ["nothing to carry yet (no saved copy, none in the checkout): the tool starts cold"]
    if record.get("refused"):
        lines.append(
            "carry entries NOT carried into this worktree -- only a file the worktree's "
            ".gitignore covers is copied, so ignore these or drop them:"
        )
        lines.extend(f"  {rel}" for rel in record["refused"])
    return lines
