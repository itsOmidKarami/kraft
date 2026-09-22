from __future__ import annotations

import asyncio
import subprocess

import pytest
from support.harness import make_repo_with_engineering

from kraft.index.service import Indexer

pytestmark = pytest.mark.slow


async def test_work_item_completed_triggers_repo_rescan(tmp_path, database, conn):
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nseed content\n"})
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "('w1','b1','t',?,'quick-task','{}','active','now','now')",
            (str(repo),),
        )
    )
    ix = Indexer(conn, database, repos_env="")
    await ix.startup_scan()
    assert ix.health()["documents"] == 1

    await ix.start()
    database.set_on_commit(ix.notify)
    try:
        (repo / ".engineering/specs/b.md").write_text("# B\nlate breaking news\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "b"], check=True, capture_output=True
        )

        await database.write(
            lambda c: c.execute(
                "INSERT INTO events (work_item_id, type, payload, created_at) "
                "VALUES ('w1','work_item_completed','{}','now')"
            )
        )
        for _ in range(50):
            if ix.search("late breaking"):
                break
            await asyncio.sleep(0.1)
        assert ix.search("late breaking")
    finally:
        database.set_on_commit(None)
        await ix.stop()


async def test_startup_scan_picks_up_preexisting_file(tmp_path, database, conn):
    repo = make_repo_with_engineering(
        tmp_path, {".engineering/plans/p.md": "# Plan\nstartup drift catches\n"}
    )
    ix = Indexer(conn, database, repos_env=str(repo))
    await ix.startup_scan()
    assert ix.search("startup drift catches")


def _seed_item(state, repo, wid="w1"):
    return state.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "(?,'b1','t',?,'quick-task','{}','active','now','now')",
            (wid, str(repo)),
        )
    )


async def test_session_exit_with_ref_ingests_summary(tmp_path, database, conn):
    from kraft import store
    from kraft.paths import RunDirs

    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
    await _seed_item(database, repo)
    rd = RunDirs(tmp_path / "run").ensure()
    sessions = rd.worktrees / "w1" / ".engineering" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "s1.md").write_text("# Session\nrewired the drain\n")

    ix = Indexer(conn, database, repos_env="", run_dirs=rd)
    await ix.start()
    database.set_on_commit(ix.notify)
    try:
        await database.write(
            lambda c: store.create_session(
                c,
                id="s1",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                log_path="/dev/null",
                result_path="/dev/null",
            )
        )
        await database.write(
            lambda c: store.session_exited(c, "s1", "done", ".engineering/sessions/s1.md")
        )
        for _ in range(50):
            if ix.search("drain"):
                break
            await asyncio.sleep(0.1)
        assert [h["path"] for h in ix.search("drain")] == [".engineering/sessions/s1.md"]
    finally:
        database.set_on_commit(None)
        await ix.stop()


async def test_a_repository_artifact_rescan_still_fires_for_a_v1_chain(tmp_path, database, conn):
    """The 04 §2 piggyback used to ride on the `on.env.prepare` session an
    `env_setup` node started. V1 has no such node (Ruling 4), so the trigger is
    `chain_loaded` -- which `store.load_chain` emits at the top of every walk,
    V1 or not, and which carries the same "a work item is starting on this repo"
    meaning. Without the move the rescan would simply never fire again, with
    nothing failing to say so.
    """
    from kraft import store
    from kraft.paths import RunDirs

    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nseed\n"})
    await _seed_item(database, repo)
    ix = Indexer(conn, database, repos_env="", run_dirs=RunDirs(tmp_path / "run").ensure())
    await ix.startup_scan()
    await ix.start()
    database.set_on_commit(ix.notify)
    try:
        (repo / ".engineering/specs/b.md").write_text("# B\npiggyback content\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "b"], check=True, capture_output=True
        )
        await database.write(lambda c: store.load_chain(c, "w1", "build"))
        for _ in range(50):
            if ix.search("piggyback"):
                break
            await asyncio.sleep(0.1)
        assert ix.search("piggyback")
    finally:
        database.set_on_commit(None)
        await ix.stop()
