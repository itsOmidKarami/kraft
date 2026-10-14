"""Where a work item's files are: written under `run/items/<id>/`, read there
first and from the old places second, and never through a symlink a worker
planted, whether Kraft is reading or writing."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest
from support.deep_tree import deep_chain
from support.harness import write
from support.unprivileged import run_unprivileged

from kraft import item_paths

OLD_SPEC = ".engineering/specs/w1.md"


def _plant_link(link, target):
    """`link` made a symlink to `target`, its parent folder created."""
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return link


@pytest.mark.parametrize(
    ("name", "worktree", "base", "rel"),
    [
        ("items/w1/docs/spec.md", None, "item", "docs/spec.md"),
        (OLD_SPEC, None, "own-worktree", OLD_SPEC),
        (OLD_SPEC, "member", "member", OLD_SPEC),
        ("items/w2/docs/spec.md", None, "own-worktree", "items/w2/docs/spec.md"),
    ],
    ids=["new-name", "old-name", "old-name-in-a-member", "another-item-s-name"],
)
def test_a_document_is_named_relative_to_where_it_lives(
    run_dirs, tmp_path, name, worktree, base, rel
):
    """A name under this item's `items/<id>/` is relative to the run folder,
    and a read of it starts at the item's own folder; anything else, another
    item's folder included, is relative to the worktree the caller works in."""
    member = tmp_path / "member"
    want = {
        "item": run_dirs.base / "items" / "w1",
        "own-worktree": run_dirs.worktrees / "w1",
        "member": member,
    }[base]

    got = item_paths.locate(run_dirs, "w1", name, worktree=member if worktree else None)

    assert got == (want, rel)
    assert item_paths.path_of(run_dirs, "w1", name, worktree=member if worktree else None) == (
        want / rel
    )
    assert item_paths.document_name("w1", "spec") == "items/w1/docs/spec.md"
    assert item_paths.legacy_document_name("w1", "spec") == OLD_SPEC


@pytest.mark.parametrize(
    ("new", "old", "found"),
    [
        ("file", "file", "items/w1/docs/spec.md"),
        ("file", None, "items/w1/docs/spec.md"),
        (None, "file", OLD_SPEC),
        (None, None, None),
        ("symlink", None, None),
        ("fifo", None, None),
        (None, "folder-symlink", None),
    ],
    ids=[
        "both-new-wins",
        "new-only",
        "old-only",
        "neither",
        "symlink-is-none",
        "fifo-is-none",
        "old-folder-symlink-is-none",
    ],
)
def test_a_document_is_read_from_the_new_place_first(run_dirs, tmp_path, new, old, found):
    secret = write(tmp_path, "secret/w1.md", "host file")
    path = item_paths.docs_dir(run_dirs, "w1") / "spec.md"
    if new == "file":
        write(path.parent, path.name, "new")
    elif new == "symlink":
        _plant_link(path, secret)
    elif new == "fifo":
        path.parent.mkdir(parents=True)
        os.mkfifo(path)
    worktree = run_dirs.worktrees / "w1"
    if old == "file":
        write(worktree, OLD_SPEC, "old")
    elif old == "folder-symlink":
        # A worker owns its worktree, so a folder on the way can be a link.
        _plant_link(worktree / ".engineering/specs", secret.parent)

    name = item_paths.document(run_dirs, "w1", "spec")

    assert name == found
    if name is not None:
        assert item_paths.read_document(run_dirs, "w1", name) == ("new" if new else "old")
    assert item_paths.read_document(run_dirs, "w1", OLD_SPEC) != "host file"
    assert item_paths.read_document(run_dirs, "w1", "items/w1/docs/spec.md") != "host file"


def test_a_session_s_worker_files_are_apart_from_kraft_s(run_dirs):
    f = item_paths.session_files(run_dirs, "w1", "s1")

    own = run_dirs.base / "items/w1/sessions/s1"
    assert (f.folder, f.log, f.exit, f.cid, f.instruction, f.review) == (
        own,
        own / "session.log",
        own / "result.exit",
        own / "result.cid",
        own / "instruction.md",
        own / "review.md",
    )
    assert (f.result, f.summary, f.writable) == (
        own / "out/result.json",
        own / "out/summary.md",
        own / "out",
    )
    assert own.parent.parent == item_paths.item_dir(run_dirs, "w1")
    assert item_paths.item_dir(run_dirs, "w1").parent == run_dirs.items


@pytest.mark.parametrize(
    "value", ["", ".", "..", "a/b", "w1\0"], ids=["empty", "dot", "dot-dot", "slash", "nul"]
)
def test_an_id_that_is_not_one_path_component_is_refused(run_dirs, value):
    """An id or kind joined into a path must not climb out of its folder."""
    with pytest.raises(ValueError, match="not a valid"):
        item_paths.item_dir(run_dirs, value)
    with pytest.raises(ValueError, match="not a valid"):
        item_paths.session_files(run_dirs, "w1", value)
    with pytest.raises(ValueError, match="not a valid"):
        item_paths.document_name("w1", value)


@pytest.mark.parametrize(
    ("log", "legacy"),
    [
        ("run/logs/s1.log", True),
        ("elsewhere/logs/s1.log", True),
        ("run/items/w1/sessions/s1/session.log", False),
        ("/moved/home/run/items/w1/sessions/s1/session.log", False),
        ("run/items/w1/sessions/s1/other.log", True),
    ],
    ids=[
        "old-run-folder",
        "moved-kraft-home",
        "session-folder",
        "moved-session-folder",
        "not-a-session-log",
    ],
)
def test_a_log_outside_an_item_folder_is_an_older_session_s(log, legacy):
    assert item_paths.is_legacy_log(log) is legacy


@pytest.mark.parametrize(
    ("log", "result", "exit"),
    [
        ("/r/logs/s1.log", "/r/results/s1.json", "/r/results/s1.exit"),
        (
            "/r/items/w1/sessions/s1/session.log",
            "/r/items/w1/sessions/s1/out/result.json",
            "/r/items/w1/sessions/s1/result.exit",
        ),
        (
            "/r/items/w1/sessions/s2/session.log",
            "/r/items/w1/sessions/escalation-1/out/result.json",
            "/r/items/w1/sessions/s2/result.exit",
        ),
    ],
    ids=["older-beside-its-result", "own-folder", "thread-turn-s-own"],
)
def test_a_session_s_exit_code_is_found_by_its_row(log, result, exit):
    """Never derived from the result file alone: in a session folder the
    exit code is Kraft's, beside the log, and an escalation turn's result is
    its thread's while its exit code is its own."""
    assert item_paths.exit_file(log, result) == Path(exit)


@pytest.mark.parametrize(
    "old_home",
    ["folder", None, "symlink"],
    ids=["old-home-exists", "no-old-home", "old-home-symlink"],
)
def test_an_existing_sandbox_home_is_kept(run_dirs, tmp_path, old_home):
    old = run_dirs.base / "sandbox-home" / "w1"
    if old_home == "folder":
        old.mkdir(parents=True)
    elif old_home == "symlink":
        (tmp_path / "elsewhere").mkdir()
        _plant_link(old, tmp_path / "elsewhere")

    assert item_paths.sandbox_home(run_dirs, "w1") == (
        old if old_home == "folder" else run_dirs.base / "items/w1/sandbox-home"
    )


def _older_thread(run_dirs):
    """An escalation thread from before the upgrade: its summary is in the
    worktree, named as it always was."""
    own = item_paths.session_files(run_dirs, "w1", "s1")
    name = ".engineering/sessions/escalation-w1-1.md"
    return dataclasses.replace(own, summary=run_dirs.worktrees / "w1" / name), name


@pytest.mark.parametrize(
    ("text", "shape", "recorded"),
    [
        ("# done", "own", "items/w1/sessions/s1/out/summary.md"),
        ("", "own", None),
        (None, "own", None),
        (None, "symlink", None),
        (None, "fifo", None),
        ("# done", "older-thread", None),
        ("# done", "elsewhere", None),
    ],
    ids=["written", "left-empty", "absent", "symlink", "fifo", "older-thread", "elsewhere"],
)
def test_kraft_records_only_a_summary_the_worker_wrote(run_dirs, tmp_path, text, shape, recorded):
    f = item_paths.session_files(run_dirs, "w1", "s1")
    if shape == "older-thread":
        f, _name = _older_thread(run_dirs)
    elif shape == "elsewhere":
        f = dataclasses.replace(f, summary=tmp_path / "elsewhere" / "summary.md")
    f.summary.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        f.summary.write_text(text)
    if shape == "symlink":
        f.summary.symlink_to(write(tmp_path, "secret", "host file"))
    elif shape == "fifo":
        os.mkfifo(f.summary)

    assert item_paths.recorded_summary(run_dirs, "w1", f) == recorded


def test_a_revised_document_keeps_its_earlier_versions(run_dirs):
    current = write(item_paths.docs_dir(run_dirs, "w1"), "spec.md", "v1")

    first = item_paths.keep_version(run_dirs, "w1", "spec")
    current.write_text("v2")
    second = item_paths.keep_version(run_dirs, "w1", "spec")

    history = run_dirs.base / "items/w1/history"
    assert (first, second) == (history / "spec.1.md", history / "spec.2.md")
    assert (first.read_text(), second.read_text(), current.read_text()) == ("v1", "v2", "v2")


@pytest.mark.parametrize("member", [False, True], ids=["own-worktree", "member-worktree"])
def test_an_old_document_is_seeded_into_docs_before_its_revision(run_dirs, tmp_path, member):
    """The old document is the one in the worktree the caller works in."""
    worktree = tmp_path / "member" if member else run_dirs.worktrees / "w1"
    write(worktree, OLD_SPEC, "old")

    kept = item_paths.keep_version(run_dirs, "w1", "spec", worktree=worktree if member else None)

    assert kept == run_dirs.base / "items/w1/history/spec.1.md"
    assert kept.read_text() == "old"
    assert (item_paths.docs_dir(run_dirs, "w1") / "spec.md").read_text() == "old"


def test_a_planted_name_in_docs_is_neither_followed_nor_replaced(run_dirs, tmp_path):
    write(run_dirs.worktrees / "w1", OLD_SPEC, "old")
    target = write(tmp_path, "victim", "untouched")
    planted = _plant_link(item_paths.docs_dir(run_dirs, "w1") / "spec.md", target)

    kept = item_paths.keep_version(run_dirs, "w1", "spec")

    assert kept.read_text() == "old"
    assert target.read_text() == "untouched"
    assert planted.is_symlink()
    assert os.readlink(planted) == str(target)


@pytest.mark.parametrize(
    "planted",
    ["source-symlink", "source-folder-symlink", "dest-symlink", "dest-folder-symlink"],
)
def test_copying_a_worker_s_file_follows_no_link(run_dirs, tmp_path, planted):
    """The one copy helper reads with no link followed and writes a new name
    with no link followed or replaced: a planted link anywhere on either
    path refuses the copy and leaves everything as it was."""
    secret = write(tmp_path, "secret/summary.md", "host file")
    victim = write(tmp_path, "victim/kept.md", "untouched")
    src = item_paths.session_files(run_dirs, "w1", "s1").writable
    write(src, "summary.md", "# done")
    dest = item_paths.session_files(run_dirs, "w1", "s2").writable
    dest.parent.mkdir(parents=True)
    if planted == "source-symlink":
        (src / "summary.md").unlink()
        _plant_link(src / "summary.md", secret)
    elif planted == "source-folder-symlink":
        write(tmp_path, "other/out/summary.md", "host file")
        (src / "summary.md").unlink()
        src.rmdir()
        _plant_link(src, tmp_path / "other/out")
    elif planted == "dest-symlink":
        _plant_link(dest / "summary.md", victim)
    else:
        _plant_link(dest, victim.parent)

    copied = item_paths.copy_in(
        run_dirs.base,
        "items/w1/sessions/s1/out/summary.md",
        run_dirs.base,
        "items/w1/sessions/s2/out/summary.md",
    )

    assert copied is False
    if planted.startswith("source"):
        assert not (dest / "summary.md").exists()
    assert victim.read_text() == "untouched"
    assert sorted(p.name for p in victim.parent.iterdir()) == ["kept.md"]


def test_a_worker_s_file_is_copied_to_a_new_name(run_dirs):
    """The counterpart of the refusals above: with nothing planted, the copy
    is made, folders and all, and a second copy to the same name is refused."""
    write(item_paths.session_files(run_dirs, "w1", "s1").writable, "summary.md", "# done")
    args = (run_dirs.base, "items/w1/sessions/s1/out/summary.md", run_dirs.base)

    assert item_paths.copy_in(*args, "items/w1/sessions/s2/out/summary.md") is True
    assert item_paths.copy_in(*args, "items/w1/sessions/s2/out/summary.md") is False
    copy = item_paths.session_files(run_dirs, "w1", "s2").summary
    assert copy.read_text() == "# done"


def test_keeping_a_version_of_nothing_writes_nothing(run_dirs):
    assert item_paths.keep_version(run_dirs, "w1", "spec") is None
    assert not (run_dirs.base / "items/w1/history").exists()
    assert not (run_dirs.base / "items/w1/docs").exists()


def test_a_run_items_folder_that_is_a_link_is_followed(run_dirs, tmp_path):
    """`run/items` is Kraft's, and may be moved to another disk and linked:
    every read and write of an item's files starts at the item's folder, so
    the link is followed there, and only the worker's part below it is
    walked without following one."""
    run_dirs.items.rmdir()
    (tmp_path / "elsewhere").mkdir()
    run_dirs.items.symlink_to(tmp_path / "elsewhere")
    write(item_paths.docs_dir(run_dirs, "w1"), "spec.md", "v1")
    f = item_paths.session_files(run_dirs, "w1", "s1")
    write(f.writable, "summary.md", "# done")

    name = item_paths.document(run_dirs, "w1", "spec")
    kept = item_paths.keep_version(run_dirs, "w1", "spec")
    copied = item_paths.copy_in(
        item_paths.item_dir(run_dirs, "w1"),
        "sessions/s1/out/summary.md",
        item_paths.item_dir(run_dirs, "w1"),
        "sessions/s2/out/summary.md",
    )

    assert name == "items/w1/docs/spec.md"
    assert item_paths.read_document(run_dirs, "w1", name) == "v1"
    assert kept is not None and kept.read_text() == "v1"
    assert item_paths.recorded_summary(run_dirs, "w1", f) == "items/w1/sessions/s1/out/summary.md"
    assert copied is True
    assert (tmp_path / "elsewhere/w1/sessions/s2/out/summary.md").read_text() == "# done"


@pytest.mark.parametrize("trouble", ["oversized", "history-not-a-folder", "item-not-writable"])
def test_keeping_a_version_is_never_a_reason_to_stop(run_dirs, monkeypatch, trouble):
    """A convenience: a worker can write a document of any size, and it would
    be copied again on every retry, and a failure to keep one must not fail
    the dispatch. Each leaves no version and raises nothing."""
    write(item_paths.docs_dir(run_dirs, "w1"), "spec.md", "x" * 64)
    history = item_paths.history_dir(run_dirs, "w1")
    if trouble == "oversized":
        monkeypatch.setattr(item_paths, "DOC_MAX_BYTES", 32)
    elif trouble == "history-not-a-folder":
        history.write_text("not a folder")
    else:

        def refuse(*a, **kw):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(item_paths, "_copy_fd", refuse)

    assert item_paths.keep_version(run_dirs, "w1", "spec") is None
    assert not history.is_dir()


def test_an_oversized_worker_file_is_not_copied(run_dirs, monkeypatch):
    """A summary a worker wrote is copied into each turn's own folder: not at
    any size."""
    write(item_paths.session_files(run_dirs, "w1", "s1").writable, "summary.md", "x" * 64)
    monkeypatch.setattr(item_paths, "DOC_MAX_BYTES", 32)
    root = item_paths.item_dir(run_dirs, "w1")

    copied = item_paths.copy_in(
        root, "sessions/s1/out/summary.md", root, "sessions/s2/out/summary.md"
    )

    assert copied is False
    assert not (root / "sessions/s2").exists()


_REMOVE = """
import sys
from pathlib import Path
from kraft import item_paths
from kraft.paths import RunDirs

which, run_dirs = sys.argv[1], RunDirs(Path(sys.argv[2]))
if which == "planted-summary":
    item_paths.remove_entry(item_paths.session_files(run_dirs, "w1", "s1").summary)
elif which == "worker-files":
    from kraft.adapters.subprocess import _create_worker_files
    _create_worker_files(item_paths.session_files(run_dirs, "w1", "s1"))
elif which == "archived-home":
    from kraft.worker.backends.docker import DockerBackend
    DockerBackend().release(run_dirs, run_dirs.worktrees / "w1", "w1")
else:
    from kraft.api.routes.lifecycle import _remove_item_files
    _remove_item_files(run_dirs, "w1")
"""


#: Whether Kraft can unlock a mode-0 folder here: only through `O_PATH` and
#: `/proc/self/fd` (`item_paths._unlock`); elsewhere it leaves one.
_UNLOCKS_MODE_0 = hasattr(os, "O_PATH") and os.path.isdir("/proc/self/fd")
_MODE_0 = pytest.mark.skipif(not _UNLOCKS_MODE_0, reason="Kraft leaves a mode-0 folder here")
_LOCKED = {"locked": 0o555, "hidden": 0o000}


@pytest.mark.parametrize(
    ("which", "tree", "modes"),
    [
        ("planted-summary", "sessions/s1/out/summary.md", _LOCKED),
        ("worker-files", "sessions/s1/out/summary.md", _LOCKED),
        ("archived-home", "sandbox-home/go/pkg/mod", _LOCKED),
        ("abandoned-item", "docs/cache", _LOCKED),
        ("abandoned-item", "docs/cache", {"readable": 0o600}),
        ("abandoned-item", "docs/cache", {"read-only": 0o400}),
    ],
    ids=[
        "planted-summary",
        "worker-files",
        "archived-home",
        "abandoned-item",
        "mode-600",
        "mode-400",
    ],
)
def test_a_folder_its_owner_cannot_write_is_still_removed(run_dirs, tmp_path, which, tree, modes):
    """A worker, or a tool it ran (a Go module cache is read-only), can leave
    a folder its owner cannot write, search or list. A daemon that is not
    root cannot empty it as it is: Kraft sets it to mode 0700 and removes
    it, and never follows a link while doing so. (A mode-0 folder only where
    Kraft can unlock one, `_UNLOCKS_MODE_0`.)"""
    root = item_paths.item_dir(run_dirs, "w1") / tree
    modes = {n: m for n, m in modes.items() if m or _UNLOCKS_MODE_0}
    victim = write(tmp_path, "victim/keep.md", "untouched")
    for name in modes:
        write(root, f"{name}/f", "x")
    (root / next(iter(modes)) / "link").symlink_to(victim.parent)
    for name, mode in modes.items():
        (root / name).chmod(mode)
    try:
        done = run_unprivileged(
            _REMOVE, which, str(run_dirs.base), env={"KRAFT_HOME": str(tmp_path / "home")}
        )

        assert done.returncode == 0, done.stderr
        if which == "worker-files":
            assert root.is_file() and not root.is_symlink()
        elif which == "abandoned-item":
            assert not item_paths.item_dir(run_dirs, "w1").exists()
        else:
            assert not os.path.lexists(root)
        assert victim.read_text() == "untouched"
    finally:
        for name in modes:
            if (root / name).is_dir() and not (root / name).is_symlink():
                (root / name).chmod(0o755)


@pytest.mark.parametrize("top", ["link", "file"])
def test_a_link_or_a_file_where_the_folder_should_be_is_left(run_dirs, tmp_path, top):
    """`remove_tree` removes a folder: a link at its path is not followed nor
    removed, and a file there is left too, as `shutil.rmtree` refuses both."""
    victim = write(tmp_path, "victim/keep.md", "untouched")
    home = item_paths.sandbox_homes(run_dirs, "w1")[1]
    home.parent.mkdir(parents=True)
    if top == "link":
        home.symlink_to(victim.parent)
    else:
        home.write_text("x")

    item_paths.remove_tree(home)

    assert os.path.lexists(home)
    assert victim.read_text() == "untouched"


_SWAP = """
import os, sys
from pathlib import Path
from kraft import item_paths

item, when, swap, target, moment = Path(sys.argv[1]), *sys.argv[2:]
real_open, swapped = os.open, []

def swap_now():
    # The worker's move: the folder `swap` (in `docs/`) is renamed beside
    # itself and a link into the daemon's home put in its place.
    swapped.append(when)
    try:
        os.rename(item / "docs" / swap, item / "docs" / (swap + ".moved"))
        os.symlink(target, item / "docs" / swap)
    except OSError as exc:
        swapped.append(exc)

def swap_around_open(name, flags, *a, dir_fd=None, **kw):
    # Just as Kraft opens `when`, or just after it opened it with `O_PATH`
    # to unlock it.
    if moment == "before" and not swapped and dir_fd is not None and name == when:
        swap_now()
    fd = real_open(name, flags, *a, dir_fd=dir_fd, **kw)
    if moment == "after-o-path" and not swapped and name == when:
        if flags & getattr(os, "O_PATH", 0):
            swap_now()
    return fd

os.open = swap_around_open
item_paths.remove_tree(item)
print(swapped[1:] or "swapped" if swapped else "never swapped")
"""


@pytest.mark.parametrize(
    ("folder", "mode", "swap", "moment"),
    [
        ("run", 0o555, "a", "before"),
        pytest.param("hidden", 0o000, "a", "before", marks=_MODE_0),
        pytest.param("hidden", 0o000, "a/hidden", "before", marks=_MODE_0),
        pytest.param("hidden", 0o077, "a/hidden", "after-o-path", marks=_MODE_0),
    ],
    ids=["read-only", "mode-0", "mode-0-folder-itself", "mode-0-folder-itself-while-unlocked"],
)
def test_a_folder_swapped_for_a_link_while_it_is_removed_takes_nothing_outside(
    run_dirs, tmp_path, folder, mode, swap, moment
):
    """A worker plants `docs/a/<folder>/kraft.db` in a folder its owner cannot
    write or list, and as Kraft goes into it swaps `docs/a`, or the folder
    itself, for a link into the daemon's home: before Kraft opens it, or
    after Kraft opened it to unlock it (mode 0, or 077, with no access for
    its owner) and before the chmod. Kraft removes
    what it found through the descriptors it opened, and unlocks a folder
    through one too, so the daemon's own `~/.kraft/<folder>/` keeps its
    `kraft.db` and its mode."""
    item = item_paths.item_dir(run_dirs, "w1")
    planted = write(item, f"docs/a/{folder}/kraft.db", "worker's")
    home = tmp_path / "home"
    victim = write(home, f"{folder}/kraft.db", "the daemon's")
    target = home if swap == "a" else victim.parent
    planted.parent.chmod(mode)
    victim.parent.chmod(0o500)
    try:
        done = run_unprivileged(_SWAP, str(item), folder, swap, str(target), moment)

        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == "swapped"
        assert victim.read_text() == "the daemon's"
        assert victim.parent.stat().st_mode & 0o777 == 0o500
        if swap == "a":
            # Removed where it went, through the descriptors Kraft holds.
            assert not os.path.lexists(item / "docs" / "a.moved" / folder)
        if moment == "after-o-path":
            # Unlocked where it went, to 0700 whatever its own bits: a
            # chmod that ever landed elsewhere could not widen access.
            assert (item / "docs" / "a" / "hidden.moved").stat().st_mode & 0o777 == 0o700
    finally:
        victim.parent.chmod(0o755)
        for left in (item / "docs" / "a.moved" / folder, item / "docs" / "a" / "hidden.moved"):
            if left.is_dir() and not left.is_symlink():
                left.chmod(0o755)


_NO_O_PATH = """
import os, sys
from pathlib import Path

# A platform without `O_PATH`, with `fchmodat(AT_SYMLINK_NOFOLLOW)`: macOS.
del os.O_PATH
os.supports_follow_symlinks.add(os.chmod)
from kraft import item_paths

item, key = Path(sys.argv[1]), sys.argv[2]
real_stat, seen = os.stat, []

def stat_then_swap(name, *a, dir_fd=None, follow_symlinks=True, **kw):
    st = real_stat(name, *a, dir_fd=dir_fd, follow_symlinks=follow_symlinks, **kw)
    if name == "hidden":
        seen.append(name)
        if len(seen) == 2:
            # The worker, between a check of the name and a change by it:
            # the folder out, a hard link to the daemon's key in.
            os.rename(item / "docs/a/hidden", item / "docs/a/hidden.moved")
            os.link(key, item / "docs/a/hidden")
    return st

os.stat = stat_then_swap
item_paths.remove_tree(item)
"""


def test_a_mode_0_folder_is_left_where_no_descriptor_can_unlock_it(run_dirs, tmp_path):
    """Without `O_PATH`, the only way to change a folder Kraft cannot open is
    by its name, and a worker can put a hard link to the daemon's private
    key at that name between Kraft's look and its chmod. Kraft leaves the
    folder rather than chmod anything by name."""
    item = item_paths.item_dir(run_dirs, "w1")
    hidden = write(item, "docs/a/hidden/f", "x").parent
    key = write(tmp_path, "home/.ssh/id_ed25519", "KEY")
    key.chmod(0o600)
    hidden.chmod(0o077)
    try:
        done = run_unprivileged(_NO_O_PATH, str(item), str(key))

        assert done.returncode == 0, done.stderr
        assert key.stat().st_mode & 0o777 == 0o600
        assert hidden.is_dir() and hidden.stat().st_mode & 0o777 == 0o077
    finally:
        for left in (hidden, hidden.with_name("hidden.moved")):
            if left.is_dir() and not left.is_symlink():
                left.chmod(0o755)


_DEEP_REMOVE = """
import os, resource, sys
from kraft import item_paths

real_open, opens = os.open, []

def counting_open(*a, **kw):
    opens.append(a[0])
    return real_open(*a, **kw)

os.open = counting_open
resource.setrlimit(resource.RLIMIT_NOFILE, (64, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
item_paths.remove_tree(sys.argv[1])
print(len(opens))
"""


def test_a_deep_tree_is_removed_within_a_low_open_file_limit(run_dirs):
    """A worker can nest folders in its `docs/` far deeper than the daemon's
    open-file limit. Removing them holds a bounded number of descriptors,
    goes back up by `..` rather than reopening from the top (about two opens
    a folder, where reopening would take some 12.5 million for 5,000), and
    removes the item's folder whole. Counted, not timed, so a loaded machine
    cannot fail it."""
    item = item_paths.item_dir(run_dirs, "w1")
    deep_chain(item / "docs", 5000, leaf=b"x")

    done = subprocess.run(
        [sys.executable, "-c", _DEEP_REMOVE, str(item)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert done.returncode == 0, done.stderr
    assert not os.path.lexists(item)
    assert int(done.stdout) < 3 * 5000
