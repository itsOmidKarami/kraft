"""A writer that ends across a restart has its outputs kept as one that ends
under a live server does, from the cap and names it launched with."""

from __future__ import annotations

import pytest
from support.harness import own_files, v1_node, v1_task

from kraft import item_paths, outputs
from kraft.worker import reattach

WRITER = "w.main.t"
#: A definitely-dead pid, with a start time.
DEAD = (2_000_000_000, 123.0)


@pytest.mark.parametrize(
    ("launched_cap", "size", "status", "kept"),
    [
        (1 << 20, 5000, "done", True),
        (1000, 5000, "failed", False),
    ],
    ids=["launched-with-room", "launched-with-less-than-it-wrote"],
)
async def test_capture_across_restart(item_on, run_dirs, launched_cap, size, status, kept):
    """What the cap is today (policy since changed, a new default) is not read:
    the session's own record is."""
    it = await item_on(
        [v1_node("w", tasks=[v1_task("t", outputs={"files": ["a.txt"], "variables": ["K"]})])]
    )
    files = own_files(run_dirs, "s1", it.id)
    out = outputs.declare(files, outputs.Decl(WRITER, ("a.txt",), ("K",), launched_cap))
    (out / "a.txt").write_text("x" * size)
    (out / "variables.env").write_text("K=1\n")
    (run_dirs.results / "s1.json").write_text('{"status": "done"}')
    await it.session("s1", WRITER, running=DEAD)

    await reattach.reattach(it.database, run_dirs, grace_retry_delay_s=0)

    [row] = it.sessions("w")
    assert row["status"] == status
    snapshot = item_paths.snapshot_dir(run_dirs, it.id, WRITER)
    assert (snapshot / "a.txt").exists() is kept
    assert not out.exists()
    assert ("kept 1 file(s)" in files.log.read_text()) is kept


async def test_a_machines_refusal_survives_the_restart(item_on, run_dirs):
    """A remote writer's folder was over the limit and the machine dropped it;
    the server that heard so is gone, and a files-only writer must still fail."""
    it = await item_on([v1_node("w", tasks=[v1_task("t", outputs={"files": ["a.txt"]})])])
    files = own_files(run_dirs, "s1", it.id)
    outputs.declare(files, outputs.Decl(WRITER, ("a.txt",), (), 1 << 20))
    outputs.mark_refused(files)
    item_paths.remove_tree(item_paths.writer_dir(files))
    (run_dirs.results / "s1.json").write_text('{"status": "done"}')
    await it.session("s1", WRITER, running=DEAD)

    await reattach.reattach(it.database, run_dirs, grace_retry_delay_s=0)

    assert [r["status"] for r in it.sessions("w")] == ["failed"]
    assert "none of it was kept" in files.log.read_text()


async def test_a_capture_made_before_the_crash_is_not_made_again(item_on, run_dirs):
    """The crash fell after the folder was dropped and before the exit was
    recorded: the good snapshot stays, not replaced by an empty one."""
    it = await item_on([v1_node("w", tasks=[v1_task("t", outputs={"files": ["a.txt"]})])])
    files = own_files(run_dirs, "s1", it.id)
    out = outputs.declare(files, outputs.Decl(WRITER, ("a.txt",), (), 1 << 20))
    (out / "a.txt").write_text("kept")
    assert outputs.capture(run_dirs, it.id, files) is None
    (run_dirs.results / "s1.json").write_text('{"status": "done"}')
    await it.session("s1", WRITER, running=DEAD)

    await reattach.reattach(it.database, run_dirs, grace_retry_delay_s=0)

    assert [r["status"] for r in it.sessions("w")] == ["done"]
    assert (item_paths.snapshot_dir(run_dirs, it.id, WRITER) / "a.txt").read_text() == "kept"
