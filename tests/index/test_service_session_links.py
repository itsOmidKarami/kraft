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


_SHARED = [("w1", "abc123", "done"), ("w2", "abc123", "failed")]


@pytest.mark.parametrize(
    ("sessions", "linked_to", "status"),
    [
        ([("w1", OLD, "done")], (), "done"),
        (_SHARED, ("w1", "w2"), None),
        (_SHARED, (), "done"),
    ],
    ids=["pre-upgrade-id", "an-id-two-items-share", "an-id-another-item-has-too"],
)
async def test_a_summary_linked_before_the_upgrade_keeps_its_run(
    tmp_path, database, conn, sessions, linked_to, status
):
    """A link written before `session_work_item_id` has none: its session is
    found by id among the sessions of the summary's own items, which a
    pre-upgrade id names uniquely. An id more than one of those items'
    sessions has gives no run rather than a guess, and another item's
    session of that id is not looked at."""
    ix = await _summarised(tmp_path, database, conn, sessions, linked_to)
    conn.execute("UPDATE document_links SET session_work_item_id = NULL")
    conn.commit()

    docs = [d for d in ix.documents_for_work_item("w1") if d["source_kind"] == "session_summary"]
    (doc,) = [d for d in docs if d["path"].startswith("items/w1/")]
    assert doc["session_status"] == status
    assert doc["attempt"] == (1 if status else None)


@pytest.mark.parametrize(
    ("items", "holders", "beside", "status"),
    [
        (["w1", "w3"], ["w2"], None, None),
        (["w1"], ["w2"], None, None),
        (["w1", "w3"], ["w1", "w2"], None, "done"),
        (["w1", "w3"], ["w2"], ["w1", "w2"], None),
    ],
    ids=[
        "another-item-s-session",
        "its-one-item-has-none",
        "its-own-item-s-session",
        "a-sibling-summary-s-item-s-session",
    ],
)
async def test_a_scanned_summary_finds_only_its_own_items_session(
    tmp_path, database, conn, items, holders, beside, status
):
    """A summary git scanned names its session by id alone, and its items in
    `work_item_ids`. Only those items' sessions are its: a 6-character id
    one other item's session holds gives it no run, not that item's, even
    when another summary scanned with it names that item."""
    summary = f"---\nwork_item_ids: [{', '.join(items)}]\nworker_session_id: abc123\n---\n# S\nx\n"
    files = {".engineering/sessions/s.md": summary}
    if beside:
        files[".engineering/sessions/t.md"] = (
            f"---\nwork_item_ids: [{', '.join(beside)}]\nworker_session_id: def456\n---\n# T\ny\n"
        )
    repo = make_repo_with_engineering(tmp_path, files)
    for wid in ("w1", "w2", "w3"):
        await seed_work_item(database, str(repo), wid)
    for wid in holders:
        await seed_session(database, "abc123", wid, ".engineering/sessions/s.md")
        await database.write(
            lambda c, w=wid: c.execute(
                "UPDATE worker_sessions SET status = ? WHERE work_item_id = ?",
                ("done" if w == "w1" else "failed", w),
            )
        )
    ix = Indexer(conn, database, repos_env=str(repo))
    await ix.rescan_repo(str(repo))

    (doc,) = [d for d in ix.documents_for_work_item("w1") if d["path"].endswith("/s.md")]
    assert (doc["worker_session_id"], doc["session_status"]) == ("abc123", status)
