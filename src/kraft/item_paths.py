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
which base it is under: `items/<id>/...` under the run folder, anything else
under the worktree the caller is working in. `locate` turns a name into the
folder a read starts at and the part of the name walked from there.

`docs/`, a session's `out/` and the worktree are writable by a worker, so
nothing here trusts a name in them. Every read goes through
`worker.worktree_read`, which follows no symlink at any component below where
it starts and does not block on a FIFO; every write opens its folder through
`open_dir_no_symlinks` and its file with `O_CREAT | O_EXCL | O_NOFOLLOW`, so a
name a worker planted is neither followed nor replaced. A walk into an item's
folder starts at `item_dir`, opened as any path is: `run/items` and the item's
own folder are Kraft's, and may be links (a `run/items` moved to another
disk), and only what is below them is a worker's to plant a link in.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from kraft.worker import tree_walk
from kraft.worker.worktree_read import open_dir_no_symlinks, open_no_symlinks, read_worktree_file

logger = logging.getLogger(__name__)

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


def filed_copy(run_dirs, wid: str, source: str | None) -> tuple[Path, str] | None:
    """The folder and file name of the attachment copy intake stored, found
    by the file name of the `source` its row records: in `filed/` first, then
    in `run/attachments/<id>/` for an item filed before the upgrade. Only the
    name is taken from the row, never its folder, so a `KRAFT_RUN_DIR` moved
    or spelled another way since filing still finds the copy, and a row can
    name nothing outside Kraft's own copies. None when neither folder holds
    a file by that name. The caller reads the name inside the folder with no
    symlink followed."""
    if not source:
        return None
    name = PurePosixPath(source).name
    if name in ("", ".", ".."):
        return None
    for folder in filed_dirs(run_dirs, wid):
        if os.path.lexists(folder / name):
            return folder, name
    return None


def stored_attachment(run_dirs, wid: str, attachment: dict, repo: str | Path) -> Path:
    """Where starting an item reads an attachment from. A `source` shaped
    like one of intake's copies (in a `filed/` folder, or in
    `attachments/<id>/`) is looked for first in this item's own copy
    folders, by its name (`filed_copy`): a `KRAFT_RUN_DIR` moved or spelled
    another way since filing leaves the row naming the old place, which may
    still hold an older copy. Otherwise, or when this item has no copy of
    that name, the recorded `source` when a file is there; else the copy of
    that name in the item's copy folders; else the recorded path as it is,
    for the caller to report missing. With no `source`, the path in the
    repo."""
    source = attachment.get("source")
    if not source:
        return Path(repo) / attachment["path"]
    recorded = Path(source)
    if recorded.parent.name == "filed" or recorded.parent.parent.name == "attachments":
        found = filed_copy(run_dirs, wid, source)
        if found:
            return found[0] / found[1]
    if recorded.is_file():
        return recorded
    found = filed_copy(run_dirs, wid, source)
    return found[0] / found[1] if found else recorded


def is_filed_copy(run_dirs, wid: str, source: str | None) -> bool:
    """Whether `source` names a file directly inside one of the item's
    `filed_dirs`, as this Kraft spells them: the test for deleting a copy,
    which, unlike reading one, must not reach a file the row only shares a
    name with."""
    return bool(source) and Path(source).parent in filed_dirs(run_dirs, wid)


def document_name(wid: str, kind: str) -> str:
    """Where a task producing `kind` writes, relative to the run folder."""
    return f"{ITEMS}/{_component(wid, 'work item id')}/docs/{_component(kind, 'kind')}.md"


def legacy_document_name(wid: str, kind: str) -> str:
    """Where it wrote before the upgrade, relative to the item's worktree."""
    return f".engineering/{_component(kind, 'kind')}s/{_component(wid, 'work item id')}.md"


def locate(run_dirs, wid: str, name: str, *, worktree: Path | None = None) -> tuple[Path, str]:
    """`name` as `(root, rel)`: the folder a no-follow read starts at, and the
    part of the name walked from there. A name under this item's
    `items/<id>/` starts at `item_dir`, which is Kraft's and is opened as any
    path is (a link there, such as a `run/items` moved to another disk, is
    followed), and walks the rest; any other name starts at the worktree the
    caller is working in (the item's own when it gives none). A name under
    another item's folder is not this item's, so it resolves inside the
    worktree, where it reaches nothing of the run folder."""
    parts = PurePosixPath(name).parts
    if len(parts) > 2 and parts[0] == ITEMS and parts[1] == wid:
        return item_dir(run_dirs, wid), PurePosixPath(*parts[2:]).as_posix()
    return (Path(worktree) if worktree is not None else run_dirs.worktrees / wid), name


def path_of(run_dirs, wid: str, name: str, *, worktree: Path | None = None) -> Path:
    """`name` as an absolute path, where `locate` finds it. For handing on
    (to an agent's prompt); a read goes through `read_document`."""
    root, rel = locate(run_dirs, wid, name, worktree=worktree)
    return root / rel


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
        if _regular_size(*locate(run_dirs, wid, name, worktree=worktree)) is not None:
            return name
    return None


def read_document(
    run_dirs, wid: str, name: str, *, worktree: Path | None = None, limit: int = DOC_MAX_BYTES
) -> str | None:
    """A document's text, at most `limit` bytes of it, read with no symlink
    followed; None when it cannot be read that way."""
    result, _reason = read_worktree_file(*locate(run_dirs, wid, name, worktree=worktree), limit)
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


def exit_file(log_path: str | os.PathLike[str], result_path: str | os.PathLike[str]) -> Path:
    """Where a session's launch wrapper wrote its exit code, by the paths its
    row records: its own folder's `result.exit` for a session with a session
    folder, else beside its result file (`results/<sid>.exit`), as before the
    upgrade. Never derived from the result file alone: an escalation turn's
    is its thread's, and the exit code is always the turn's own."""
    if not is_legacy_log(log_path):
        return Path(log_path).parent / "result.exit"
    return Path(result_path).with_suffix(".exit")


def exit_code(raw: str) -> int | None:
    """The code an exit file holds, or None: ASCII digits only, so text a
    worker could plant there (`"²"` is a digit to `str.isdigit` and not to
    `int`) is no code rather than an error."""
    raw = raw.strip()
    return int(raw) if re.fullmatch(r"-?[0-9]+", raw) else None


def sandbox_homes(run_dirs, wid: str) -> tuple[Path, Path]:
    """Both places an item's sandbox home can be: the old
    `run/sandbox-home/<id>/` and `items/<id>/sandbox-home/`. Archiving and
    abandoning remove both; `sandbox_home` picks the one in use."""
    return (
        run_dirs.base / "sandbox-home" / _component(wid, "work item id"),
        item_dir(run_dirs, wid) / "sandbox-home",
    )


def sandbox_home(run_dirs, wid: str) -> Path:
    """The `HOME` an item's sandboxes share: the old `run/sandbox-home/<id>/`
    while it exists (it holds the agent CLI's chats a paused session resumes
    from), else `items/<id>/sandbox-home/`. The old one is never created after
    the upgrade, so the two never both exist. Only a real folder counts as
    the old one: a symlink there is not followed."""
    old, new = sandbox_homes(run_dirs, wid)
    try:
        is_old = stat.S_ISDIR(old.lstat().st_mode)
    except OSError:
        is_old = False
    return old if is_old else new


def thread_folder(thread: int | str) -> str:
    """The session-folder name an escalation thread's turns share."""
    return _component(f"escalation-{thread}", "thread")


def thread_out(run_dirs, wid: str, thread: int | str) -> Path:
    """`items/<id>/sessions/escalation-<thread>/out/`: the result file and
    summary every turn of a thread started after the upgrade writes."""
    return item_dir(run_dirs, wid) / "sessions" / thread_folder(thread) / "out"


def legacy_thread_files(run_dirs, wid: str, thread: int | str) -> tuple[Path, Path]:
    """`(result, summary)` of an escalation thread started before the
    upgrade, where every turn of it keeps writing:
    `results/escalation-<id>-<thread>.json` and the worktree's
    `.engineering/sessions/escalation-<id>-<thread>.md`."""
    name = f"escalation-{_component(wid, 'work item id')}-{thread}"
    return (
        run_dirs.results / f"{name}.json",
        run_dirs.worktrees / wid / ".engineering" / "sessions" / f"{name}.md",
    )


def recorded_summary(run_dirs, wid: str, files: SessionFiles) -> str | None:
    """The summary Kraft asked this session for, by the name it is recorded
    under (`items/<id>/sessions/<folder>/out/summary.md`, relative to the run
    folder), when the worker wrote one: a regular, non-empty file in this
    item's folder reached with no symlink below it. Kraft creates it empty,
    to mount it, so empty means none.

    Only a summary in this item's folder is Kraft's to record. An older
    escalation thread's sits in the worktree, where every turn shares it and
    one turn's is still there when the next turn writes none, so the
    worker's own ref decides there, as it did before the upgrade
    (`subprocess.read_result_fields`)."""
    root = item_dir(run_dirs, wid)
    try:
        rel = files.summary.relative_to(root).as_posix()
    except ValueError:
        return None
    return f"{ITEMS}/{wid}/{rel}" if _regular_size(root, rel) else None


def remove_entry(path: Path) -> None:
    """Remove whatever is at `path`, if anything, never following a link: a
    file or a link is unlinked, and a folder a worker planted where a file
    belongs is removed with everything under it (`remove_tree`)."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError:
        if not _is_real_dir(path):
            raise
        remove_tree(path)


def remove_tree(path: str | os.PathLike[str]) -> None:
    """Remove the folder `path` and everything under it, best-effort, never
    following a link, never raising.

    Only `path`'s own folders are resolved as a path, and they are Kraft's;
    below them the walk is `tree_walk.walk`'s, by descriptors: each name is
    opened, unlinked or removed relative to the descriptor of the folder it
    was listed in, with no link followed, so a link is removed rather than
    what it points at, and a worker swapping a folder for a link (to the
    daemon's home) while Kraft removes it redirects nothing. A link or a
    file at `path` itself is left, as `shutil.rmtree` refuses it.

    A worker, or a tool it ran, can leave a folder its owner cannot write or
    list (`chmod 555`, `chmod 000`, a Go module cache), which a daemon that
    is not root cannot empty. Such a folder is given back its owner's read,
    write and search permission through a descriptor of the folder itself
    (`_unlock`), never a path, and the removal retried; a folder Kraft
    cannot fix that way (another user's) is left, as is everything above
    it."""
    path = os.path.abspath(os.fspath(path))
    parent, name = os.path.split(path)
    if name in ("", ".", ".."):
        return
    try:
        top = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return
    try:
        tree_walk.walk(
            top,
            lambda dir_fd, entry, st: _remove_entry_at(dir_fd, entry, st, at_top=dir_fd == top),
            open_dir=_open_to_remove,
            leave=lambda dir_fd, entry: _as_owner(dir_fd, lambda: os.rmdir(entry, dir_fd=dir_fd)),
            names=[name],
        )
    except OSError:
        pass
    finally:
        os.close(top)


def _remove_entry_at(dir_fd: int, name: str, st: os.stat_result, *, at_top: bool) -> bool:
    """`remove_tree`'s step for one entry of the folder `dir_fd`: a folder is
    walked (True); anything else, a link included, is unlinked by its name
    there, never followed. At the top only a folder is removed."""
    if stat.S_ISDIR(st.st_mode):
        return True
    if at_top:
        return False
    if _as_owner(dir_fd, lambda: os.unlink(name, dir_fd=dir_fd)):
        return False
    # Swapped for a folder since its `lstat`? Then walk that.
    try:
        return stat.S_ISDIR(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def _as_owner(dir_fd: int, op) -> bool:
    """Run `op`, an unlink or rmdir in the folder `dir_fd`, and when it is
    refused, give that folder its owner's read, write and search permission
    through `dir_fd` itself (`fchmod`) and run it once more. True when the
    name is gone."""
    try:
        op()
        return True
    except FileNotFoundError:
        return True
    except PermissionError:
        pass
    except OSError:
        return False
    try:
        os.fchmod(dir_fd, stat.S_IMODE(os.fstat(dir_fd).st_mode) | stat.S_IRWXU)
        op()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _open_to_remove(dir_fd: int, name: str) -> int | None:
    """The folder `name` in `dir_fd`, opened with no link followed and given
    its owner's read, write and search permission, so what is in it can be
    listed and removed: a mode-0 folder through `_unlock` first. None, and
    the folder left, when Kraft cannot open it that way."""
    fd = tree_walk.open_child(dir_fd, name)
    if fd is None:
        if not _unlock(dir_fd, name):
            return None
        fd = tree_walk.open_child(dir_fd, name)
        if fd is None:
            return None
    try:
        mode = stat.S_IMODE(os.fstat(fd).st_mode)
        if mode & stat.S_IRWXU != stat.S_IRWXU:
            os.fchmod(fd, mode | stat.S_IRWXU)
    except OSError:
        pass  # another user's: what can be removed in it still is
    return fd


def _unlock(dir_fd: int, name: str) -> bool:
    """Give the folder `name` in `dir_fd`, one Kraft cannot open (mode 0),
    its owner's read, write and search permission without following a link
    and without resolving any path a worker could swap a component of. True
    when it did.

    On Linux, the folder is opened with `O_PATH | O_NOFOLLOW`, which needs no
    permission on it, checked to be a folder, and changed through
    `/proc/self/fd/<n>`, which names the object that descriptor holds rather
    than any path (glibc's `lchmod` does the same). Where the platform has
    `fchmodat(AT_SYMLINK_NOFOLLOW)` (macOS), that, relative to `dir_fd`.
    With neither, the folder is left: never a chmod by path."""
    o_path = getattr(os, "O_PATH", 0)
    if o_path:
        try:
            pfd = os.open(
                name, o_path | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=dir_fd
            )
        except OSError:
            pfd = None
        if pfd is not None:
            try:
                info = os.fstat(pfd)
                if stat.S_ISDIR(info.st_mode):
                    os.chmod(f"/proc/self/fd/{pfd}", stat.S_IMODE(info.st_mode) | stat.S_IRWXU)
                    return True
            except OSError:
                pass
            finally:
                os.close(pfd)
    if os.chmod in os.supports_dir_fd and os.chmod in os.supports_follow_symlinks:
        try:
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                os.chmod(
                    name,
                    stat.S_IMODE(info.st_mode) | stat.S_IRWXU,
                    dir_fd=dir_fd,
                    follow_symlinks=False,
                )
                return True
        except (OSError, NotImplementedError):
            pass
    return False


def _is_real_dir(path: str | os.PathLike[str]) -> bool:
    """A folder, not a link to one."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


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
    when the source is not a regular file reached that way, is larger than
    `DOC_MAX_BYTES` (a worker's file is not copied at any size), the
    destination name is taken (a name a worker planted, or an earlier copy),
    a folder on the way to it is a link, or the write fails."""
    try:
        src = open_no_symlinks(source_base, source_name)
    except (OSError, ValueError, IndexError):
        return False
    try:
        if os.fstat(src).st_size > DOC_MAX_BYTES:
            logger.warning(
                "not copying %s: larger than %d bytes", source_base / source_name, DOC_MAX_BYTES
            )
            return False
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
    nothing, when there is no document.

    A convenience, never a reason to stop a dispatch: a document larger than
    `DOC_MAX_BYTES` (a worker's to write at any size, and copied again on
    every retry) is not kept, and any failure to read or write is logged and
    leaves None."""
    try:
        return _keep_version(run_dirs, wid, kind, worktree)
    except (OSError, ValueError) as exc:
        logger.warning("could not keep a version of %s's %s: %s", wid, kind, exc)
        return None


def _keep_version(run_dirs, wid: str, kind: str, worktree: Path | None) -> Path | None:
    name = document(run_dirs, wid, kind, worktree=worktree)
    if name is None:
        return None
    try:
        src = open_no_symlinks(*locate(run_dirs, wid, name, worktree=worktree))
    except (OSError, ValueError, IndexError):
        return None  # gone, or swapped for a link, since `document` looked
    try:
        size = os.fstat(src).st_size
        if size > DOC_MAX_BYTES:
            logger.warning(
                "not keeping a version of %s's %s: %d bytes, over %d",
                wid,
                kind,
                size,
                DOC_MAX_BYTES,
            )
            return None
        root = item_dir(run_dirs, wid)
        root.mkdir(parents=True, exist_ok=True)  # Kraft's own; may be reached by a link
        history = history_dir(run_dirs, wid)
        kept = None
        n = _next_version(history, kind)
        for candidate in range(n, n + _VERSION_TRIES):
            try:
                os.lseek(src, 0, os.SEEK_SET)
                _copy_fd(src, root, f"history/{kind}.{candidate}.md")
            except FileExistsError:
                continue  # another dispatch took this number first
            kept = history / f"{kind}.{candidate}.md"
            break
        if kept is not None and name != document_name(wid, kind):
            os.lseek(src, 0, os.SEEK_SET)
            # A convenience: whatever already holds the name, a planted link
            # included, is left as it is and the revise note names it anyway.
            with contextlib.suppress(OSError):
                _copy_fd(src, root, f"docs/{_component(kind, 'kind')}.md")
        return kept
    finally:
        os.close(src)
