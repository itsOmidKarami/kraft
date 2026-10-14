"""Which session summary the index reads: a ref under `items/` only when it
is the session's own `out/summary.md` (or its escalation thread's) in its
item's folder, anything else inside the item's worktree as before. In its own
file because `test_service.py` is near its line budget (`dev/check_tests.py`)."""

from __future__ import annotations

import pytest
from support.harness import make_repo_with_engineering
from support.index import seed_session, seed_work_item

from kraft.index.service import Indexer
from kraft.paths import RunDirs


@pytest.mark.parametrize(
    ("ref", "where", "ingested"),
    [
        ("items/w1/sessions/s1/out/summary.md", "run", True),
        ("items/w1/sessions/escalation-1/out/summary.md", "run", True),
        (".engineering/sessions/s1.md", "worktree", True),
        ("items/w1/sessions/s9/out/summary.md", "run", False),
        ("items/w2/sessions/s1/out/summary.md", "run", False),
        ("items/w1/docs/spec.md", "run", False),
        ("items/w1/sessions/s1/out/summary.md", "worktree", False),
        ("items/w1/sessions/escalation-2/out/summary.md", "run", False),
        ("items/w1/sessions/s1/out/summary.md", "linked-items", True),
    ],
    ids=[
        "own-folder",
        "own-thread",
        "older-ref",
        "another-session-s",
        "another-item-s",
        "not-a-summary",
        "items-ref-in-the-worktree",
        "another-thread-s",
        "run-items-is-a-link",
    ],
)
async def test_a_summary_is_read_only_from_its_own_session_s_folder(
    tmp_path, database, conn, ref, where, ingested
):
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
    await seed_work_item(database, str(repo))
    await seed_session(database, "s1", "w1", ref, hook="escalation", thread=1)
    rd = RunDirs(tmp_path / "run").ensure()
    if where == "linked-items":
        # `run/items` moved to another disk: Kraft's own folder, followed.
        rd.items.rmdir()
        (tmp_path / "elsewhere").mkdir()
        rd.items.symlink_to(tmp_path / "elsewhere")
    path = (rd.worktrees / "w1" if where == "worktree" else rd.base) / ref
    path.parent.mkdir(parents=True)
    path.write_text("---\nkind: sessions\n---\n# done\nthe summary body\n")
    ix = Indexer(conn, database, repos_env="", run_dirs=rd)

    assert await ix.ingest_session_summary("w1", "s1") is ingested
    hits = ix.search("summary body")
    assert [(h["path"], h["source_kind"]) for h in hits] == (
        [(ref, "session_summary")] if ingested else []
    )


@pytest.mark.parametrize("planted", ["symlink", "folder-symlink"])
async def test_a_summary_in_an_item_folder_is_never_read_through_a_link(
    tmp_path, database, conn, planted
):
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
    await seed_work_item(database, str(repo))
    ref = "items/w1/sessions/s1/out/summary.md"
    await seed_session(database, "s1", "w1", ref)
    rd = RunDirs(tmp_path / "run").ensure()
    secret = tmp_path / "secret" / "summary.md"
    secret.parent.mkdir()
    secret.write_text("# secret\nhost file\n")
    out = rd.base / "items/w1/sessions/s1/out"
    if planted == "symlink":
        out.mkdir(parents=True)
        (out / "summary.md").symlink_to(secret)
    else:
        out.parent.mkdir(parents=True)
        out.symlink_to(secret.parent)

    assert (
        await Indexer(conn, database, repos_env="", run_dirs=rd).ingest_session_summary("w1", "s1")
        is False
    )
