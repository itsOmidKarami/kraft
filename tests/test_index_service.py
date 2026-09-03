from __future__ import annotations

import asyncio
import os

import pytest
from support.harness import make_repo_with_engineering

from kraft.db import Database
from kraft.index import db as index_db
from kraft.index.service import Indexer


def _seed_work_item(db: Database, repo: str, wid: str = "w1"):
    return db.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "(?, 'b1', 't', ?, 'quick-task', '{}', 'active', 'now', 'now')",
            (wid, repo),
        )
    )


def test_repos_union_of_work_items_and_env(tmp_path):
    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo_a = make_repo_with_engineering(
                tmp_path, {".engineering/specs/a.md": "# A\nx\n"}, "a"
            )
            repo_b = make_repo_with_engineering(
                tmp_path, {".engineering/specs/b.md": "# B\ny\n"}, "b"
            )
            await _seed_work_item(state, str(repo_a))
            ix = Indexer(conn, state, repos_env=f"{repo_b}{os.pathsep}/no/such/dir")
            assert set(ix.repos()) == {str(repo_a), str(repo_b)}
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())


def test_startup_scan_then_search(tmp_path):
    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(
                tmp_path,
                {
                    ".engineering/specs/ws.md": (
                        "---\ntitle: WS transport\nowner: omid\n---\n"
                        "reconnect backoff schedule caps\n"
                    ),
                    ".engineering/plans/ui.md": "# UI plan\nboard and detail view\n",
                },
            )
            await _seed_work_item(state, str(repo))
            ix = Indexer(conn, state, repos_env="")
            await ix.startup_scan()

            hits = ix.search("reconnect backoff")
            assert [h["path"] for h in hits] == [".engineering/specs/ws.md"]
            assert hits[0]["title"] == "WS transport"
            assert hits[0]["kind"] == "specs"
            assert "[reconnect]" in hits[0]["snippet"]
            assert hits[0]["links"] == []

            assert ix.search("board", kind="plans")
            assert ix.search("board", kind="specs") == []
            assert ix.search("backoff", repo="/other/repo") == []
            assert ix.search("backoff", source_kind="session_summary") == []

            doc = ix.get_document(hits[0]["id"])
            assert doc["content"] == "reconnect backoff schedule caps\n"
            assert doc["metadata"] == {"owner": "omid"}
            assert ix.get_document("nope") is None

            h = ix.health()
            assert h["documents"] == 2
            assert h["repos_scanned"] == 1
            assert h["errors"] == []
            assert h["last_scan_at"]
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())


def test_search_malformed_query_raises(tmp_path):
    import sqlite3

    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            ix = Indexer(conn, state, repos_env="")
            with pytest.raises(sqlite3.OperationalError):
                ix.search('"unterminated')
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())
