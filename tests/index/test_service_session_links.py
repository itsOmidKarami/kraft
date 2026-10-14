"""The run a session summary came from: its own item's session, though a
session id is unique only within its item (spec "Item files and IDs",
part 3). In its own file because `test_service.py` is near its line budget
(`dev/check_tests.py`)."""

from __future__ import annotations

import pytest
from support.harness import make_repo_with_engineering
from support.index import seed_session, seed_work_item

from kraft.index.service import Indexer
from kraft.paths import RunDirs

#: A session id minted before per-item ids.
OLD = "8cbfe6e27c1044b3e445c0f0d726357d"


async def _summarised(tmp_path, database, conn, sessions, linked_to=()):
    """An indexer over a repo whose `sessions` (`(wid, sid, status)`) each
    wrote and had ingested a summary in their item's folder. A summary's
    front matter also names the items in `linked_to`."""
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
    rd = RunDirs(tmp_path / "run").ensure()
    ix = Indexer(conn, database, repos_env="", run_dirs=rd)
    for wid in sorted({w for w, _, _ in sessions}):
        await seed_work_item(database, str(repo), wid)
    for wid, sid, status in sessions:
        ref = f"items/{wid}/sessions/{sid}/out/summary.md"
        await seed_session(database, sid, wid, ref)
        await database.write(
            lambda c, w=wid, s=sid, st=status: c.execute(
                "UPDATE worker_sessions SET status = ? WHERE work_item_id = ? AND id = ?",
                (st, w, s),
            )
        )
        items = ", ".join(sorted({wid, *linked_to}))
        (rd.base / ref).parent.mkdir(parents=True)
        (rd.base / ref).write_text(f"---\nwork_item_ids: [{items}]\n---\n# {wid}\nbody\n")
        assert await ix.ingest_session_summary(wid, sid)
    return ix


def _summaries(ix, wid):
    return {
        d["path"]: d["session_status"]
        for d in ix.documents_for_work_item(wid)
        if d["source_kind"] == "session_summary"
    }


async def test_a_summary_is_listed_once_with_its_own_item_s_session(tmp_path, database, conn):
    """Two items' sessions `abc123`, each summary naming both items: each
    is listed once on each item, with the run of the session that wrote it."""
    ix = await _summarised(
        tmp_path,
        database,
        conn,
        [("w1", "abc123", "done"), ("w2", "abc123", "failed")],
        linked_to=("w1", "w2"),
    )

    expected = {
        "items/w1/sessions/abc123/out/summary.md": "done",
        "items/w2/sessions/abc123/out/summary.md": "failed",
    }
    assert _summaries(ix, "w1") == _summaries(ix, "w2") == expected
    assert len([d for d in ix.documents_for_work_item("w1") if d["worker_session_id"]]) == 2


@pytest.mark.parametrize(
    ("sessions", "status"),
    [
        ([("w1", OLD, "done")], "done"),
        ([("w1", "abc123", "done"), ("w2", "abc123", "failed")], None),
    ],
    ids=["pre-upgrade-id", "an-id-two-items-share"],
)
async def test_a_summary_linked_before_the_upgrade_keeps_its_run(
    tmp_path, database, conn, sessions, status
):
    """A link written before `session_work_item_id` has none: its session is
    found by id alone, which a pre-upgrade id names uniquely. An id more
    than one item's session has gives no run rather than another item's."""
    ix = await _summarised(tmp_path, database, conn, sessions)
    conn.execute("UPDATE document_links SET session_work_item_id = NULL")
    conn.commit()

    (doc,) = [d for d in ix.documents_for_work_item("w1") if d["source_kind"] == "session_summary"]
    assert doc["session_status"] == status
    assert doc["attempt"] == (1 if status else None)
