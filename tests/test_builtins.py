import asyncio
import subprocess
from pathlib import Path

from support.harness import make_repo, make_repo_with_engineering

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


def test_env_setup_copies_an_uncommitted_attachment_into_the_worktree(tmp_path):
    repo = make_repo(tmp_path)
    plan = repo / ".engineering" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# the plan\n")  # never committed

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
                attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
            )
            assert status == "done"
            copied = rd.worktrees / "w1" / ".engineering" / "plans" / "p.md"
            assert copied.read_text() == "# the plan\n"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_env_setup_leaves_a_committed_attachment_alone(tmp_path):
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# committed\n"})
    (repo / ".engineering" / "plans" / "p.md").write_text("# dirty working tree\n")

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
            await kraft_builtins.env_setup(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="env_setup",
                repo=str(repo),
                attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
            )
            copied = rd.worktrees / "w1" / ".engineering" / "plans" / "p.md"
            # git brought the committed version; the copy must not clobber it
            assert copied.read_text() == "# committed\n"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_env_setup_does_not_write_through_a_symlinked_attachment(tmp_path):
    """HEAD (what the fresh worktree is checked out from) can hold a symlink
    at the attachment path that the browser-supplied path never showed:
    validation only ever looked at the repo's working tree. A dangling
    symlink there must not become a write to wherever it points."""
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside.txt"  # outside the repo entirely
    link = repo / ".engineering" / "plans" / "p.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)  # dangling: outside does not exist yet
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add symlinked plan"], cwd=repo, check=True)
    # the working tree now diverges from HEAD: a real, validated file sits
    # where HEAD has the symlink
    link.unlink()
    link.write_text("# real plan\n")

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
                attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
            )
            assert status == "done"
            assert not outside.exists()
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
