"""Saving the carried files back after a round (`dispatch._save_carried`)."""

import pytest
from support import worktree as wtree
from support.harness import entry_of, write

from kraft import carried, executor
from kraft.executor import dispatch
from kraft.templates.models import TestSuite

from .test_scopes import _BUILTIN, _verify

_CARRY = entry_of({"carry": [".testmondata"]})
_BASE = {
    "suite": TestSuite.TARGETED,
    "results": ["done", "done"],
    "sandbox": None,
    "runner": None,
}


def _saved(it) -> bool:
    return carried.current_generation(carried.store_dir(it.run_dirs, it.repo)) is not None


def _baseline(it):
    """The record a worktree whose files the round never touched would have."""
    carried.write_record(
        it.run_dirs.base / "items" / it.id / "carried.json",
        {"files": {k: list(v) for k, v in carried.stats(it.worktree, [".testmondata"]).items()}},
    )


@pytest.mark.parametrize(
    ("overrides", "saved"),
    [
        ({}, True),
        ({"suite": TestSuite.FULL}, False),
        ({"sandbox": {"kind": "docker"}}, False),
        ({"runner": "build-box"}, False),
        ({"results": ["done", "failed"]}, False),
        ({"results": []}, False),
        ({"in_place": True}, False),
        ({"entry": entry_of({})}, False),
        ({"untouched": True}, False),
    ],
    ids=[
        "all-guards-hold",
        "suite-full",
        "sandboxed",
        "remote-runner",
        "a-failed-scope",
        "no-results",
        "an-in-place-or-member-folder",
        "no-carry-declared",
        "files-not-touched-by-the-round",
    ],
)
async def test_a_round_saves_the_carried_files_only_when_every_guard_holds(
    item_on, overrides, saved
):
    it = await item_on(_verify(), worktree=True)
    write(it.worktree, ".testmondata", "db")
    # A checkout with the file too, so an in-place folder would have something to save.
    write(it.repo, ".testmondata", "checkout")
    kwargs = {**_BASE, **overrides}
    entry = kwargs.pop("entry", _CARRY)
    worktree = it.repo if kwargs.pop("in_place", False) else it.worktree
    if kwargs.pop("untouched", False):
        _baseline(it)

    await dispatch._save_carried(it.run_dirs, it.row(), worktree, entry, **kwargs)

    assert _saved(it) is saved


async def test_no_record_after_a_restart_counts_every_present_file_as_changed(item_on):
    it = await item_on(_verify(), worktree=True)
    write(it.worktree, ".testmondata", "db")
    assert not (it.run_dirs.base / "items" / it.id / "carried.json").exists()

    await dispatch._save_carried(it.run_dirs, it.row(), it.worktree, _CARRY, **_BASE)

    assert _saved(it)


async def test_a_failing_save_does_not_fail_the_round(item_on, monkeypatch):
    it = await item_on(_verify(), worktree=True)
    write(it.worktree, ".testmondata", "db")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(carried, "save", boom)

    await dispatch._save_carried(it.run_dirs, it.row(), it.worktree, _CARRY, **_BASE)

    assert not _saved(it)


@pytest.mark.parametrize(
    ("task", "saved"),
    [(_BUILTIN, True), ({**_BUILTIN, "suite": "full"}, False)],
    ids=["targeted", "suite-full"],
)
async def test_dispatching_the_builtin_saves_what_its_command_wrote(item_on, repo, task, saved):
    it = await item_on(_verify(task))
    entry = entry_of(
        {
            "setup_command": "",
            "carry": [".testmondata"],
            "test_scopes": [{"paths": ["**"], "command": "sh -c 'echo db > .testmondata'"}],
        }
    )
    worktree = await wtree.ensure(it.database, it.run_dirs, repo, repo_entry=entry)
    node = it.chain.chain.nodes[0]

    status = await dispatch.dispatch_node(
        it.database,
        it.run_dirs,
        node.steps[0].tasks[0],
        node,
        it.row(),
        worktree,
        launch=executor.LaunchContext(repo_entry=entry),
        round=0,
    )

    assert status == "done"
    assert _saved(it) is saved
    if saved:
        current = carried.current_generation(carried.store_dir(it.run_dirs, repo))
        assert (current / ".testmondata").read_text() == "db\n"
