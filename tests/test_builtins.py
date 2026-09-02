import asyncio
import subprocess
from pathlib import Path

from support.harness import make_repo

from kraft import builtins as kraft_builtins
from kraft import db, store
from kraft.paths import RunDirs


def test_env_setup_creates_worktree_and_branch(tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B",
                    title="t",
                    repo=str(repo),
                    chain_template="quick-task",
                    chain_definition="{}",
                )
            )
            status = await kraft_builtins.env_setup(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="env_setup",
                repo=str(repo),
            )
            assert status == "done"
            worktree = rd.worktrees / "w1"
            assert (worktree / "calc.py").is_file()
            branches = subprocess.run(
                ["git", "branch", "--list", "kraft/w1"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert "kraft/w1" in branches
            row = database.read(
                lambda c: c.execute(
                    "SELECT hook_point, status FROM worker_sessions WHERE id='s1'"
                ).fetchone()
            )
            assert row["hook_point"] == "on.env.prepare"
            assert row["status"] == "done"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_noop_creates_done_session_with_log(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B",
                    title="t",
                    repo="/r",
                    chain_template="default",
                    chain_definition="{}",
                )
            )
            status = await kraft_builtins.noop(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="spec",
                hook_point="on.spec.requested",
            )
            assert status == "done"
            row = database.read(
                lambda c: c.execute(
                    "SELECT hook_point, status, log_path FROM worker_sessions WHERE id='s1'"
                ).fetchone()
            )
            assert row["hook_point"] == "on.spec.requested"
            assert row["status"] == "done"
            assert Path(row["log_path"]).is_file()
        finally:
            await database.close()

    asyncio.run(scenario())
