import asyncio
import sys
from pathlib import Path

from support.harness import make_repo

from kraft import db, store
from kraft.adapters import agent
from kraft.paths import RunDirs

_FAKE = Path(__file__).parent / "support" / "fake_agent.py"


async def _seed(database, repo):
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="make the failing test pass",
            repo=str(repo),
            chain_template="quick-task",
            chain_definition="{}",
        )
    )


def test_agent_fix_mode_patches_repo_and_never_writes_claudemd(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    (repo / "CLAUDE.md").write_text("DO NOT EDIT\n")
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="make the failing test pass",
                task_instruction="make the failing test pass",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "done"
            assert "a + b" in (repo / "calc.py").read_text()
            assert (repo / "CLAUDE.md").read_text() == "DO NOT EDIT\n"  # untouched
        finally:
            await database.close()

    asyncio.run(scenario())


def test_agent_error_envelope_downgrades_to_failed(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "error")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s2",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "failed"
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s2'").fetchone()
            )
            assert row["status"] == "failed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_agent_writes_session_summary_and_ref_lands_in_db(tmp_path, monkeypatch):
    """04 §6: the injected prompt carries the linkage fields, the worker writes
    .engineering/sessions/<session>.md, and its ref lands on worker_sessions."""
    repo = make_repo(tmp_path)
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s3",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "done"
            row = database.read(
                lambda c: c.execute(
                    "SELECT session_summary_ref FROM worker_sessions WHERE id='s3'"
                ).fetchone()
            )
            assert row["session_summary_ref"] == ".engineering/sessions/s3.md"
            summary = (repo / ".engineering" / "sessions" / "s3.md").read_text()
            assert "work_item_ids: [w1]" in summary
            assert "node_id: implementation" in summary
            assert "hook_point: on.implementation.start" in summary
            assert "worker_session_id: s3" in summary
        finally:
            await database.close()

    asyncio.run(scenario())
