"""Step outputs: what a task leaves for a later task, and how Kraft keeps it.

A task declaring `outputs` writes into `$KRAFT_OUTPUT_DIR`, the `out/outputs/`
folder of its own session (`declare`). When the session ends `done` or
`failed`, `capture` checks everything the worker left, copies the declared
names into a snapshot nobody mounts writable, and deletes the worker's folder.
A later task naming the writer in `reads` is handed the snapshot read-only.

The worker is untrusted. Every read of `out/outputs/` starts at the session's
own folder, which is Kraft's, and follows no link below it; a file is copied
only when it is a regular file with one link, and no more of it than the size
its descriptor reported; the checks run before anything is copied, so a
failing capture leaves nothing. Variables are `KEY=value` lines in
`variables.env` and are never set in any environment. No message, log line or
prompt here carries a string the worker chose: only the names the chain
declared, and counts.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import stat
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from kraft import item_paths
from kraft.item_paths import SessionFiles
from kraft.worker import tree_walk
from kraft.worker.worktree_read import open_dir_no_symlinks, open_no_symlinks

logger = logging.getLogger(__name__)

VARIABLES_FILE = "variables.env"
SIDECAR = "outputs.json"
#: Beside `SIDECAR`: what `capture` concluded, so a session captured twice (a
#: crash between dropping its folder and recording its exit) keeps the first.
CAPTURED = "outputs.captured"
#: Beside `SIDECAR`: a machine returned none of the writer's outputs, over the limit.
REFUSED = "outputs.refused"
#: The most entries (files, folders, links) a writer's folder may hold.
MAX_ENTRIES = 100
#: The most distinct keys its `variables.env` may hold.
MAX_VARIABLES = 50
MAX_VALUE_BYTES = 4096
#: `variables.env` itself is read to no more than this: fifty values of
#: `MAX_VALUE_BYTES` plus their keys, with room for repeats and comments.
_MAX_ENV_BYTES = 1 << 20
#: How deep `carry_over` follows a worker's folders.
_MAX_DEPTH = 16
#: Any control character but a tab.
_CONTROL = re.compile(rb"[\x00-\x08\x0a-\x1f\x7f]")


@dataclass(frozen=True)
class Decl:
    """What one writer declared, and the size cap it launched under."""

    writer: str
    files: tuple[str, ...]
    variables: tuple[str, ...]
    max_bytes: int


_COULD_NOT_STORE = "kraft: outputs: could not store the declared outputs"


def declare(files: SessionFiles, decl: Decl) -> Path:
    """Make the session's `$KRAFT_OUTPUT_DIR` and record `decl` beside it, in
    the session's own folder (Kraft's, never mounted), so the cap and the
    names are the ones the session launched with whenever it is captured.
    Returns the output folder."""
    folder = item_paths.writer_dir(files)
    folder.mkdir(parents=True, exist_ok=True)
    (files.folder / SIDECAR).write_text(
        json.dumps(
            {
                "writer": decl.writer,
                "files": list(decl.files),
                "variables": list(decl.variables),
                "max_bytes": decl.max_bytes,
            }
        )
    )
    return folder


def read_decl(files: SessionFiles) -> Decl | None:
    """The declaration `declare` recorded, or None for a session that
    declared none (or whose record cannot be read)."""
    try:
        raw = json.loads((files.folder / SIDECAR).read_text())
        return Decl(
            writer=str(raw["writer"]),
            files=tuple(raw["files"]),
            variables=tuple(raw["variables"]),
            max_bytes=int(raw["max_bytes"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def carry_over(old: SessionFiles, new: SessionFiles, decl: Decl) -> None:
    """Give the resumed session `new` a copy of what the paused session `old`
    had written to its output folder, as `copy_in` copies a worker's file:
    no link followed, nothing that is not a regular file with one link, and
    no more than the entry and total size limits a capture holds it to."""
    try:
        top = open_dir_no_symlinks(old.folder, ["out", "outputs"])
    except OSError:
        return
    try:
        names = _regular_files(top)
    finally:
        os.close(top)
    target = item_paths.writer_dir(new)
    left = decl.max_bytes  # the whole folder, as a capture holds it, not each file
    for name in names:
        if left <= 0:
            break
        if item_paths.copy_in(
            old.folder,
            f"out/outputs/{name}",
            target,
            name,
            max_bytes=left,
            single_link=True,
        ):
            left -= (target / name).stat().st_size


def _regular_files(top: int, prefix: str = "", budget: list[int] | None = None) -> list[str]:
    """The regular files below the folder `top`, as relative names, walking
    no link and stopping after `MAX_ENTRIES` entries seen."""
    budget = budget if budget is not None else [MAX_ENTRIES]
    found: list[str] = []
    try:
        with os.scandir(top) as entries:
            listed = [e.name for e in entries]
    except OSError:
        return found
    for name in listed:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        try:
            info = os.stat(name, dir_fd=top, follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            found.append(f"{prefix}{name}")
        elif stat.S_ISDIR(info.st_mode) and prefix.count("/") < _MAX_DEPTH:
            child = tree_walk.open_child(top, name)
            if child is None:
                continue
            try:
                found += _regular_files(child, f"{prefix}{name}/", budget)
            finally:
                os.close(child)
    return found


def log_line(decl: Decl, kept: int, missing: int) -> str:
    """The one line a capture adds to the writer's `session.log`: counts and
    the declared variable names, never a value or a name the worker chose."""
    names = ", ".join(decl.variables)
    return f"kraft: outputs: kept {kept} file(s), {missing} missing; variables {names}"


def capture(run_dirs, wid: str, files: SessionFiles) -> str | None:
    """Keep what the session declared, after it ended `done` or `failed`.

    None when it was kept, or the session declared nothing. Otherwise a
    message, holding no string the worker chose, saying why the writer must
    fail; there is then no snapshot and no temporary folder. Either way the
    worker's folder is deleted. The outcome is also appended to the session's
    log. A session captured before returns what that capture concluded, and
    touches no snapshot."""
    decl = read_decl(files)
    if decl is None:
        return None
    done = _read_captured(files)
    if done is not None:
        _drop_writer_dir(files)
        return done or None
    try:
        problem, counts = _capture(run_dirs, wid, files, decl)
    except Exception as exc:
        logger.warning("could not capture the outputs of a writer of %s: %s", wid, exc)
        problem, counts = _COULD_NOT_STORE, (0, 0)
    if problem is not None and not clear(run_dirs, wid, decl.writer):
        problem = _COULD_NOT_STORE
    _write_captured(files, problem)
    _drop_writer_dir(files)
    _append(files.log, problem or log_line(decl, *counts))
    return problem


def _read_captured(files: SessionFiles) -> str | None:
    """What an earlier capture of this session concluded: its problem, `""`
    for none, or None when it has not been captured."""
    try:
        return str(json.loads((files.folder / CAPTURED).read_text())["problem"] or "")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_captured(files: SessionFiles, problem: str | None) -> None:
    try:
        (files.folder / CAPTURED).write_text(json.dumps({"problem": problem}))
    except OSError as exc:
        logger.warning("could not record a capture in %s: %s", files.folder, exc)


def mark_refused(files: SessionFiles) -> None:
    """Record, beside the session, that a machine dropped its outputs for being
    over the limit, so the refusal outlives the server process that heard it."""
    try:
        (files.folder / REFUSED).write_text("")
    except OSError as exc:
        logger.warning("could not record a refusal in %s: %s", files.folder, exc)


def was_refused(files: SessionFiles) -> bool:
    return (files.folder / REFUSED).exists()


def drop(files: SessionFiles) -> None:
    """Delete the session's output folder, with no link followed."""
    _drop_writer_dir(files)


def trim_oversized(files: SessionFiles, max_bytes: int) -> bool:
    """On a machine that ran the session: delete the output folder when it is
    over `max_bytes` or `MAX_ENTRIES`, so it is not sent back at all. True
    when it was. The same two checks a capture makes, here before the folder
    is packed, so an oversized writer fails with the outputs message instead of
    the transfer's refusal of everything the step returned."""
    if _check_folder(files, Decl("", (), (), max_bytes)) is None:
        return False
    _drop_writer_dir(files)
    return True


def refuse(run_dirs, wid: str, files: SessionFiles, why: str) -> str | None:
    """Keep nothing of a session that declared outputs, for `why` (a message
    holding no string the worker chose): its folder and any earlier snapshot of
    the writer are deleted and the reason logged. None, touching nothing, for a
    session that declared none."""
    decl = read_decl(files)
    if decl is None:
        return None
    _drop_writer_dir(files)
    problem = why if clear(run_dirs, wid, decl.writer) else _COULD_NOT_STORE
    _append(files.log, problem)
    return problem


def _capture(
    run_dirs, wid: str, files: SessionFiles, decl: Decl
) -> tuple[str | None, tuple[int, int]]:
    problem = _check_folder(files, decl)
    values: dict[str, bytes] = {}
    if problem is None:
        problem = _check_variables(files, decl, values)
    if problem is not None:
        return problem, (0, 0)
    root = item_paths.outputs_dir(run_dirs, wid)
    snapshot = item_paths.snapshot_dir(run_dirs, wid, decl.writer)
    temp_name = f".{decl.writer}.new"
    temp = root / temp_name
    try:
        root.mkdir(parents=True, exist_ok=True)
        item_paths.remove_tree(temp)
        temp.mkdir()
        kept = sum(
            item_paths.copy_in(
                files.folder,
                f"out/outputs/{name}",
                root,
                f"{temp_name}/{name}",
                max_bytes=decl.max_bytes,
                single_link=True,
            )
            for name in decl.files
        )
        (temp / VARIABLES_FILE).write_bytes(
            b"".join(b"%s=%s\n" % (key.encode(), values[key]) for key in decl.variables)
        )
        item_paths.remove_tree(snapshot)
        os.rename(temp, snapshot)
    except OSError as exc:
        logger.warning("could not store the outputs of a writer of %s: %s", wid, exc)
        item_paths.remove_tree(temp)
        return _COULD_NOT_STORE, (0, 0)
    return None, (kept, len(decl.files) - kept)


def _check_folder(files: SessionFiles, decl: Decl) -> str | None:
    """The size and entry checks of the worker's folder, by descriptors."""
    try:
        top = open_dir_no_symlinks(files.folder, ["out", "outputs"])
    except OSError:
        return None  # nothing there: every declared file is missing
    entries = size = 0
    stop = threading.Event()

    def enter(_dir_fd: int, _name: str, info: os.stat_result) -> bool:
        nonlocal entries, size
        entries += 1
        if stat.S_ISREG(info.st_mode):
            size += info.st_size
        if entries > MAX_ENTRIES or size > decl.max_bytes:
            stop.set()
        return stat.S_ISDIR(info.st_mode)

    try:
        tree_walk.walk(top, enter, stop=stop)
    finally:
        os.close(top)
    if entries > MAX_ENTRIES:
        return f"kraft: outputs: more than {MAX_ENTRIES} entries in the output folder"
    if size > decl.max_bytes:
        return (
            f"kraft: outputs: the output folder holds more than the {decl.max_bytes}-byte limit "
            "(storage.outputs.max_size)"
        )
    return None


def _check_variables(files: SessionFiles, decl: Decl, values: dict[str, bytes]) -> str | None:
    """Read the declared variables out of `variables.env` into `values`, or
    say which declared one fails."""
    if not decl.variables:
        return None
    try:
        parsed = parse_variables(_read_env(files.folder, "out/outputs"))
    except ValueError:
        return f"kraft: outputs: {VARIABLES_FILE} is over {_MAX_ENV_BYTES} bytes"
    except OSError:
        parsed = {}  # none, or not a plain file: every declared variable is missing
    if len(parsed) > MAX_VARIABLES:
        return f"kraft: outputs: more than {MAX_VARIABLES} variables in {VARIABLES_FILE}"
    for key in decl.variables:
        if key not in parsed:
            return f"kraft: outputs: declared variable {key} is missing from {VARIABLES_FILE}"
        problem = value_problem(parsed[key])
        if problem:
            return f"kraft: outputs: variable {key}: {problem}"
        values[key] = parsed[key]
    return None


def _read_env(base: Path, folder: str) -> bytes:
    """`<folder>/variables.env` below `base`, opened with no link followed,
    a regular file with one link, read to no more than `_MAX_ENV_BYTES`."""
    fd = open_no_symlinks(base, f"{folder}/{VARIABLES_FILE}")
    try:
        info = os.fstat(fd)
        if info.st_nlink != 1:
            raise OSError(errno.EMLINK, "linked")
        if info.st_size > _MAX_ENV_BYTES:
            raise ValueError("too large")
        with os.fdopen(os.dup(fd), "rb") as handle:
            return handle.read(info.st_size)
    finally:
        os.close(fd)


def parse_variables(data: bytes) -> dict[str, bytes]:
    """`KEY=value` lines: blank lines and `#` lines skipped, one trailing
    carriage return dropped per line, a value running to the end of the line
    (an `=` in it kept, no quote stripped), a line with no `=` ignored, and
    a repeated key keeping its last value."""
    out: dict[str, bytes] = {}
    for line in data.split(b"\n"):
        line = line.removesuffix(b"\r")
        if not line or line.startswith(b"#"):
            continue
        key, sep, value = line.partition(b"=")
        if sep:
            out[key.decode("latin-1")] = value
    return out


def value_problem(value: bytes) -> str | None:
    if len(value) > MAX_VALUE_BYTES:
        return f"value is over {MAX_VALUE_BYTES} bytes"
    if _CONTROL.search(value):
        return "value holds a control character"
    return None


def _drop_writer_dir(files: SessionFiles) -> None:
    """Delete the worker's output folder, opening the session's `out/` with no
    link followed first, since the worker may have swapped it for one."""
    try:
        out = open_dir_no_symlinks(files.folder, ["out"])
    except OSError:
        return
    try:
        item_paths.remove_in(out, "outputs")
    finally:
        os.close(out)


def _append(log: Path, line: str) -> None:
    try:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
    except OSError as exc:
        logger.warning("could not log a capture to %s: %s", log, exc)


def clear(run_dirs, wid: str, writer: str) -> bool:
    """Delete the writer's snapshot, so a later reader sees nothing of a run
    that has since re-run, been skipped or failed. False when it is still
    there: `remove_tree` swallows errors, so the removal is checked."""
    snapshot = item_paths.snapshot_dir(run_dirs, wid, writer)
    item_paths.remove_tree(snapshot)
    return not os.path.lexists(snapshot)


def writers_in(chain, node_id: str, under: str | None = None) -> list[str]:
    """The paths of the tasks of node `node_id`'s own steps that declare
    outputs, of those at or below the path `under` when it is given: what a
    skip of that node, step or task supersedes. `chain` is a
    `MaterializedChain`."""
    return [
        task.path
        for node in chain.chain.nodes
        if node.id == node_id
        for step in node.steps
        for task in step.tasks
        if getattr(task.task, "outputs", None) is not None
        and (under is None or task.path == under or task.path.startswith(f"{under}."))
    ]


def lookup_variable(
    run_dirs,
    wid: str,
    writers: Sequence[str],
    name: str,
    declared: Mapping[str, Sequence[str]],
) -> str | None:
    """The value of `name` in the snapshots of `writers`, a later writer
    winning. A key counts only when the writer's current declaration lists
    it, so a snapshot an older chain left with another key is not read for
    it. None when no snapshot holds it, or the value no longer passes the
    checks capture made."""
    found: str | None = None
    for writer in writers:
        if name not in declared.get(writer, ()):
            continue
        try:
            folder = item_paths.snapshot_dir(run_dirs, wid, writer)
            parsed = parse_variables(_read_env(folder.parent, f"{folder.name}"))
        except (OSError, ValueError):
            continue
        value = parsed.get(name)
        if value is not None and value_problem(value) is None:
            found = value.decode("utf-8", errors="replace")
    return found
