"""Where a work item's files are.

Everything an item produces, except its worktree, is written under
`run/items/<id>/`:

    docs/<kind>.md            the worker's documents (a sandbox mounts it read-write)
    history/<kind>.<n>.md     earlier versions of them: Kraft's, never mounted
    filed/<kind>[-tag].md     intake's attachment copies: Kraft's, never mounted
    sessions/<sid>/           one session's files: Kraft's, never mounted
        out/                  the two files the worker writes, and all it is granted
    sandbox-home/             the item's sandboxes' `HOME`

Nothing is moved at upgrade: an older item's files stay in `run/logs/`,
`run/results/`, `run/attachments/`, `run/sandbox-home/` and its worktree's
`.engineering/`, and are read there. Each kind of file has one rule:

- a session's files: written in its own folder, read where its row says;
- a gate document: written to `docs/`, read from there first and from the
  worktree's `.engineering/` second;
- a sandbox home: the old one while it exists, else the new one.

A document is named by its path relative to where it lives, so the name says
which base it is under (`base_for`): `items/<id>/...` under the run folder,
anything else under the worktree the caller is working in.

`docs/`, a session's `out/` and the worktree are writable by a worker, so
nothing here trusts a name in them. Every read goes through
`worker.worktree_read`, which follows no symlink at any component and does not
block on a FIFO; every write opens its folder through `open_dir_no_symlinks`
and its file with `O_CREAT | O_EXCL | O_NOFOLLOW`, so a name a worker planted
is neither followed nor replaced.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from kraft.worker.worktree_read import open_dir_no_symlinks, open_no_symlinks, read_worktree_file

ITEMS = "items"
#: The most of a document Kraft reads into a prompt or onto the board, as the
#: gate and artifact readers already cap it.
DOC_MAX_BYTES = 1_000_000
#: How many history numbers `keep_version` tries when another writer takes
#: the one it picked between its listing and its create.
_VERSION_TRIES = 5


def _component(value: str, what: str) -> str:
    """`value`, refused unless it is one plain path component: an id or a
    kind joined into a path must not climb out of, or reach across, the
    folder it names."""
    if value in ("", ".", "..") or "/" in value or os.sep in value or "\0" in value:
        raise ValueError(f"not a valid {what}: {value!r}")
    return value


def item_dir(run_dirs, wid: str) -> Path:
    """`run/items/<id>/`: everything of the item's but its worktree."""
    return run_dirs.base / ITEMS / _component(wid, "work item id")


def docs_dir(run_dirs, wid: str) -> Path:
    """The item's documents, and what its worker may write them into
    (`$KRAFT_DOCS_DIR`)."""
    return item_dir(run_dirs, wid) / "docs"


def history_dir(run_dirs, wid: str) -> Path:
    """Earlier versions of the item's documents. Never mounted."""
    return item_dir(run_dirs, wid) / "history"


def filed_dir(run_dirs, wid: str) -> Path:
    """Where intake copies an attachment. Never mounted."""
    return item_dir(run_dirs, wid) / "filed"


def filed_dirs(run_dirs, wid: str) -> tuple[Path, Path]:
    """Every folder an item's stored attachment copy may be in: the new one,
    and `run/attachments/<id>/` for an item filed before the upgrade."""
    return filed_dir(run_dirs, wid), run_dirs.attachments / _component(wid, "work item id")


def document_name(wid: str, kind: str) -> str:
    """Where a task producing `kind` writes, relative to the run folder."""
    return f"{ITEMS}/{_component(wid, 'work item id')}/docs/{_component(kind, 'kind')}.md"


def legacy_document_name(wid: str, kind: str) -> str:
    """Where it wrote before the upgrade, relative to the item's worktree."""
    return f".engineering/{_component(kind, 'kind')}s/{_component(wid, 'work item id')}.md"


def base_for(run_dirs, wid: str, name: str, *, worktree: Path | None = None) -> Path:
    """The folder `name` is relative to: the run folder for a name under this
    item's `items/<id>/`, else the worktree the caller is working in (the
    item's own when it gives none). A name under another item's folder is
    not this item's, so it resolves inside the worktree, where it reaches
    nothing of the run folder."""
    parts = PurePosixPath(name).parts
    if len(parts) > 2 and parts[0] == ITEMS and parts[1] == wid:
        return run_dirs.base
    return Path(worktree) if worktree is not None else run_dirs.worktrees / wid


def path_of(run_dirs, wid: str, name: str, *, worktree: Path | None = None) -> Path:
    """`name` as an absolute path, under the base `base_for` picks. For
    handing on (to an agent's prompt); a read goes through `read_document`."""
    return base_for(run_dirs, wid, name, worktree=worktree) / name


def _regular_size(base: Path, name: str) -> int | None:
    """The size of `base/name` when it is a regular file reached with no
    symlink at any component below `base`, else None. Never blocks on a FIFO
    (`open_no_symlinks` opens non-blocking and refuses anything irregular)."""
    try:
        fd = open_no_symlinks(base, name)
    except (OSError, ValueError, IndexError):
        return None
    try:
        return os.fstat(fd).st_size
    finally:
        os.close(fd)


def document(run_dirs, wid: str, kind: str, *, worktree: Path | None = None) -> str | None:
    """The name of the item's `kind` document that is on disk, the new place
    first: a newer revision is only ever written there. A symlink, a FIFO or
    anything else not a regular file is no document."""
    for name in (document_name(wid, kind), legacy_document_name(wid, kind)):
        if _regular_size(base_for(run_dirs, wid, name, worktree=worktree), name) is not None:
            return name
    return None


def read_document(
    run_dirs, wid: str, name: str, *, worktree: Path | None = None, limit: int = DOC_MAX_BYTES
) -> str | None:
    """A document's text, at most `limit` bytes of it, read with no symlink
    followed; None when it cannot be read that way."""
    result, _reason = read_worktree_file(
        base_for(run_dirs, wid, name, worktree=worktree), name, limit
    )
    return result.text if result is not None else None


@dataclass(frozen=True)
class SessionFiles:
    """One session's files. Only `result` and `summary`, in `writable`, are the
    worker's: each is mounted read-write as a single file, and `writable` is
    the folder a harness is granted. The rest are Kraft's, in a folder never
    mounted or granted: the log Kraft reads usage and rate limits from (its
    times sidecar is `logs.times_path(log)`), the exit code and container id
    the host writes with a plain `>`, and the instruction and review package
    a sandbox mounts read-only. For an escalation turn, `result`, `summary`
    and `writable` are its thread's (`escalate.thread_files`)."""

    folder: Path
    log: Path
    exit: Path
    cid: Path
    instruction: Path
    review: Path
    result: Path
    summary: Path
    writable: Path


def session_files(run_dirs, wid: str, sid: str) -> SessionFiles:
    """Where a session started now keeps its files: `items/<id>/sessions/<sid>/`."""
    own = item_dir(run_dirs, wid) / "sessions" / _component(sid, "session id")
    out = own / "out"
    return SessionFiles(
        folder=own,
        log=own / "session.log",
        exit=own / "result.exit",
        cid=own / "result.cid",
        instruction=own / "instruction.md",
        review=own / "review.md",
        result=out / "result.json",
        summary=out / "summary.md",
        writable=out,
    )


def is_legacy_log(log_path: str | os.PathLike[str]) -> bool:
    """Whether a session's log is from before the upgrade: anything not in a
    session folder (`.../items/<id>/sessions/<sid>/session.log`). Decided by
    shape rather than by `run/logs/`, so a moved `KRAFT_HOME` cannot change
    the answer."""
    parts = Path(log_path).parts
    return not (
        len(parts) >= 5
        and parts[-5] == ITEMS
        and parts[-3] == "sessions"
        and parts[-1] == "session.log"
    )


def sandbox_home(run_dirs, wid: str) -> Path:
    """The `HOME` an item's sandboxes share: the old `run/sandbox-home/<id>/`
    while it exists (it holds the agent CLI's chats a paused session resumes
    from), else `items/<id>/sandbox-home/`. The old one is never created after
    the upgrade, so the two never both exist. Only a real folder counts as
    the old one: a symlink there is not followed."""
    old = run_dirs.base / "sandbox-home" / _component(wid, "work item id")
    try:
        is_old = stat.S_ISDIR(old.lstat().st_mode)
    except OSError:
        is_old = False
    return old if is_old else item_dir(run_dirs, wid) / "sandbox-home"


def recorded_summary(run_dirs, wid: str, files: SessionFiles) -> str | None:
    """The summary Kraft asked this session for, by the name it is recorded
    under, when the worker wrote one: a regular, non-empty file reached with
    no symlink. Kraft creates it empty, to mount it, so empty means none.

    A summary in the item's folder is named relative to the run folder
    (`items/<id>/sessions/<folder>/out/summary.md`); an older escalation
    thread's, in the item's worktree, relative to the worktree, as it always
    was. A summary in neither place is not one Kraft asked for."""
    if files.summary.is_relative_to(item_dir(run_dirs, wid)):
        base = run_dirs.base
    else:
        base = run_dirs.worktrees / wid
    try:
        name = files.summary.relative_to(base).as_posix()
    except ValueError:
        return None
    size = _regular_size(base, name)
    return name if size else None


def _copy_fd(src: int, dest_base: Path, dest_name: str) -> None:
    """Copy the open regular file `src` to a new file `dest_base/dest_name`,
    its folders made as needed. Every folder below `dest_base` is opened
    without following a link (`open_dir_no_symlinks`), and the file itself
    with `O_CREAT | O_EXCL | O_NOFOLLOW` relative to its folder: a name
    already there, a planted symlink included, raises `FileExistsError` and
    is left as it is. A copy that fails partway is removed, through the same
    folder handle, so no half version is left behind."""
    parts = PurePosixPath(dest_name).parts
    if not parts:
        raise OSError(errno.EINVAL, f"refusing destination {dest_name!r}")
    leaf = _component(parts[-1], "file name")
    dir_fd = open_dir_no_symlinks(dest_base, parts[:-1], create=True)
    try:
        out = os.open(
            leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dir_fd
        )
        try:
            with os.fdopen(out, "wb") as w, os.fdopen(os.dup(src), "rb") as r:
                while chunk := r.read(65536):
                    w.write(chunk)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(leaf, dir_fd=dir_fd)
            raise
    finally:
        os.close(dir_fd)


def copy_in(source_base: Path, source_name: str, dest_base: Path, dest_name: str) -> bool:
    """Copy a file a worker could have written to a new name: the source read
    with no symlink followed at any component below `source_base`
    (`open_no_symlinks`, which takes only a regular file), the destination
    created as `_copy_fd` does. False, leaving the destination as it was,
    when the source is not a regular file reached that way, the destination
    name is taken (a name a worker planted, or an earlier copy), a folder on
    the way to it is a link, or the write fails."""
    try:
        src = open_no_symlinks(source_base, source_name)
    except (OSError, ValueError, IndexError):
        return False
    try:
        _copy_fd(src, dest_base, dest_name)
    except (OSError, ValueError):
        return False
    finally:
        os.close(src)
    return True


def _next_version(history: Path, kind: str) -> int:
    """One past the highest `<kind>.<n>.md` in `history`, 1 when there is none."""
    pattern = re.compile(rf"{re.escape(kind)}\.([0-9]+)\.md")
    try:
        names = os.listdir(history)
    except FileNotFoundError:
        names = []
    taken = [int(m[1]) for n in names if (m := pattern.fullmatch(n))]
    return max(taken, default=0) + 1


def keep_version(run_dirs, wid: str, kind: str, *, worktree: Path | None = None) -> Path | None:
    """Before a task producing `kind` is dispatched again, copy the item's
    current `kind` document to `history/<kind>.<n>.md`, `n` one past the
    highest there, and return that path. The document stays where it is, so
    the agent revises it in place and a failed re-run still leaves one.

    An old document, in the worktree's `.engineering/`, is also seeded into
    `docs/`, so the revise note and the "write to" path name the same file;
    a name a worker already planted there is left alone. None, writing
    nothing, when there is no document."""
    name = document(run_dirs, wid, kind, worktree=worktree)
    if name is None:
        return None
    base = base_for(run_dirs, wid, name, worktree=worktree)
    try:
        src = open_no_symlinks(base, name)
    except (OSError, ValueError, IndexError):
        return None  # gone, or swapped for a link, since `document` looked
    try:
        history = history_dir(run_dirs, wid)
        rel_history = history.relative_to(run_dirs.base).as_posix()
        kept = None
        n = _next_version(history, kind)
        for candidate in range(n, n + _VERSION_TRIES):
            try:
                os.lseek(src, 0, os.SEEK_SET)
                _copy_fd(src, run_dirs.base, f"{rel_history}/{kind}.{candidate}.md")
            except FileExistsError:
                continue  # another dispatch took this number first
            kept = history / f"{kind}.{candidate}.md"
            break
        if kept is not None and name != document_name(wid, kind):
            os.lseek(src, 0, os.SEEK_SET)
            # A convenience: whatever already holds the name, a planted link
            # included, is left as it is and the revise note names it anyway.
            with contextlib.suppress(OSError):
                _copy_fd(src, run_dirs.base, document_name(wid, kind))
        return kept
    finally:
        os.close(src)
