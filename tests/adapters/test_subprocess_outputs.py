"""A session that declared `outputs` has them checked and kept as it ends
(`subprocess.capture_outputs`), and fails when they cannot be."""

from __future__ import annotations

import pytest
from support.harness import own_files, v1_node, v1_task

from kraft import events, ids, item_paths, outputs
from kraft.adapters import subprocess as sp
from kraft.vocab import SandboxEvent

WRITER = "w.main.t"
#: Leaves a file and a variable in the output folder, then exits with `$1`.
LEAVE = 'echo kept > "$KRAFT_OUTPUT_DIR/a.txt"; echo K=1 > "$KRAFT_OUTPUT_DIR/variables.env"'


class Launch:
    """One writer session of the item `it`, run for real."""

    def __init__(self, it):
        self.it = it

    def files(self, sid: str):
        return own_files(self.it.run_dirs, sid, self.it.id)

    def snapshot(self):
        return item_paths.snapshot_dir(self.it.run_dirs, self.it.id, WRITER)

    def log(self, sid: str) -> str:
        return self.files(sid).log.read_text()

    def declare(self, sid: str, *, cap: int = 1 << 20, declared: bool = True):
        files = self.files(sid)
        if declared:
            decl = outputs.Decl(WRITER, ("a.txt",), ("K",), cap)
            return outputs.declare(files, decl)
        folder = item_paths.writer_dir(files)
        folder.mkdir(parents=True)
        return folder

    async def run(self, sid: str, script: str, **kw) -> str:
        out = self.declare(sid, cap=kw.pop("cap", 1 << 20), declared=kw.pop("declared", True))
        return await sp.run_task(
            self.it.database,
            self.it.run_dirs,
            session_id=sid,
            work_item_id=self.it.id,
            node_id="w",
            hook_point=WRITER,
            cmd=["sh", "-c", script],
            cwd=self.it.repo,
            env={"KRAFT_OUTPUT_DIR": str(out)},
            **kw,
        )

    def status(self, sid: str) -> str:
        row = self.it.database.read(
            lambda c: c.execute(
                "SELECT status FROM worker_sessions WHERE id = ?", (sid,)
            ).fetchone()
        )
        return row["status"]


@pytest.fixture
async def launch(item_on):
    it = await item_on(
        [v1_node("w", tasks=[v1_task("t", outputs={"files": ["a.txt"], "variables": ["K"]})])]
    )
    return Launch(it)


@pytest.mark.parametrize(
    ("exit_code", "want"), [(0, "done"), (3, "failed")], ids=["done", "failed"]
)
async def test_capture_on_exit(launch, exit_code, want):
    assert await launch.run("s1", f"{LEAVE}; exit {exit_code}") == want

    assert (launch.snapshot() / "a.txt").read_text() == "kept\n"
    assert (launch.snapshot() / "variables.env").read_bytes() == b"K=1\n"
    assert not item_paths.writer_dir(launch.files("s1")).exists()
    assert "kept 1 file(s), 0 missing; variables K" in launch.log("s1")


async def test_capture_failure_fails_writer(launch):
    """Ended `done`, but its outputs are over the cap it launched under."""
    status = await launch.run(
        "s1", f'{LEAVE}; head -c 5000 /dev/zero > "$KRAFT_OUTPUT_DIR/big"', cap=1000
    )

    assert status == "failed" == launch.status("s1")
    assert "limit" in launch.log("s1")
    assert not launch.snapshot().exists()


async def test_survivor_fails_writer(launch):
    """The sandbox recorded that processes of the session outlived its sweep, so
    they may still be writing the folder: nothing is kept."""
    it = launch.it
    await it.database.write(
        lambda c: events.append(
            c,
            it.id,
            SandboxEvent.SESSION_OUTLIVED,
            {"session": ids.qualified(it.id, "s1"), "pids": [4242]},
        )
    )

    assert await launch.run("s1", f"{LEAVE}; exit 0") == "failed"

    assert "outlived it" in launch.log("s1")
    assert not launch.snapshot().exists()
    assert not item_paths.writer_dir(launch.files("s1")).exists()


async def test_another_sessions_survivor_does_not_fail_this_writer(launch):
    it = launch.it
    await it.database.write(
        lambda c: events.append(
            c,
            it.id,
            SandboxEvent.SESSION_OUTLIVED,
            {"session": ids.qualified(it.id, "other"), "pids": [4242]},
        )
    )

    assert await launch.run("s1", f"{LEAVE}; exit 0") == "done"


@pytest.mark.parametrize("status", ["unknown", "paused", "needs_context", "rate_limited"])
async def test_a_status_that_is_not_a_finished_run_keeps_its_folder(launch, status):
    out = launch.declare("s1")
    (out / "a.txt").write_text("x")

    got = await sp.capture_outputs(
        launch.it.database, launch.it.run_dirs, launch.it.id, "s1", launch.files("s1"), status
    )

    assert got == status
    assert (out / "a.txt").exists()
