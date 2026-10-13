"""Where a work item's files are: written under `run/items/<id>/`, read there
first and from the old places second, and never through a symlink a worker
planted, whether Kraft is reading or writing."""

from __future__ import annotations

import dataclasses
import os

import pytest
from support.harness import write

from kraft import item_paths

OLD_SPEC = ".engineering/specs/w1.md"


def _plant_link(link, target):
    """`link` made a symlink to `target`, its parent folder created."""
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return link


@pytest.mark.parametrize(
    ("name", "worktree", "base"),
    [
        ("items/w1/docs/spec.md", None, "run"),
        (OLD_SPEC, None, "own-worktree"),
        (OLD_SPEC, "member", "member"),
        ("items/w2/docs/spec.md", None, "own-worktree"),
    ],
    ids=["new-name", "old-name", "old-name-in-a-member", "another-item-s-name"],
)
def test_a_document_is_named_relative_to_where_it_lives(run_dirs, tmp_path, name, worktree, base):
    """A name under this item's `items/<id>/` is relative to the run folder;
    anything else, another item's folder included, to the worktree the caller
    works in."""
    member = tmp_path / "member"
    want = {"run": run_dirs.base, "own-worktree": run_dirs.worktrees / "w1", "member": member}[base]

    got = item_paths.base_for(run_dirs, "w1", name, worktree=member if worktree else None)

    assert got == want
    assert item_paths.path_of(run_dirs, "w1", name, worktree=member if worktree else None) == (
        want / name
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
        ("# done", "older-thread", ".engineering/sessions/escalation-w1-1.md"),
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
