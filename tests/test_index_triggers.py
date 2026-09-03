from __future__ import annotations

import asyncio
import subprocess

import pytest
from support.harness import make_repo_with_engineering

from kraft.db import Database
from kraft.index import db as index_db
from kraft.index.service import Indexer

pytestmark = pytest.mark.slow


def test_work_item_completed_triggers_repo_rescan(tmp_path):
    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(
                tmp_path, {".engineering/specs/a.md": "# A\nseed content\n"}
            )
            await state.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','b1','t',?,'quick-task','{}','active','now','now')",
                    (str(repo),),
                )
            )
            ix = Indexer(conn, state, repos_env="")
            await ix.startup_scan()
            assert ix.health()["documents"] == 1

            await ix.start()
            state.set_on_commit(ix.notify)
            try:
                (repo / ".engineering/specs/b.md").write_text("# B\nlate breaking news\n")
                subprocess.run(
                    ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
                )
                subprocess.run(
                    ["git", "-C", str(repo), "commit", "-m", "b"], check=True, capture_output=True
                )

                await state.write(
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
                state.set_on_commit(None)
                await ix.stop()
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())


def test_startup_scan_picks_up_preexisting_file(tmp_path):
    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(
                tmp_path, {".engineering/plans/p.md": "# Plan\nstartup drift catches\n"}
            )
            ix = Indexer(conn, state, repos_env=str(repo))
            await ix.startup_scan()
            assert ix.search("startup drift catches")
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())
