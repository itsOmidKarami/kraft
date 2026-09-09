import asyncio
import subprocess
from pathlib import Path

import pytest
from support.harness import _git, make_repo, make_repo_with_engineering

from kraft import builtins as kraft_builtins
from kraft import db, store
from kraft.config import git_read
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
                ["git", "branch", "--list", "kraft/t-w1"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert "kraft/t-w1" in branches
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


def test_ensure_worktree_alone_copies_attachments_before_any_node_runs(tmp_path):
    """`default.yaml` runs `spec` and `plan` before `env_setup`, so `plan`'s
    attached-spec fallback only works if the copy happened at
    `ensure_worktree` time, not `env_setup` time. Prove it without going
    through `env_setup` at all."""
    repo = make_repo(tmp_path)
    spec = repo / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# the spec\n")  # never committed

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
                    chain_template="default",
                    chain_definition="{}",
                )
            )
            worktree = await kraft_builtins.ensure_worktree(
                database,
                rd,
                repo=str(repo),
                work_item_id="w1",
                attachments=[{"kind": "spec", "path": ".engineering/specs/s.md"}],
            )
            copied = worktree / ".engineering" / "specs" / "s.md"
            assert copied.read_text() == "# the spec\n"
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


def test_env_setup_stamps_base_ref(tmp_path):
    repo = make_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

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
            )
            row = database.read(
                lambda c: c.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["base_ref"] == head
        finally:
            await database.close()

    asyncio.run(scenario())


def test_env_setup_does_not_restamp_on_reentry(tmp_path):
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
            await kraft_builtins.env_setup(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="env_setup",
                repo=str(repo),
            )
            await database.write(lambda c: store.set_base_ref(c, "w1", "PINNED"))
            # second call: `ensure_worktree` returns early (the worktree already
            # exists) and re-pins nothing; `env_setup` still writes its own
            # session row and reports done.
            status = await kraft_builtins.env_setup(
                database,
                rd,
                session_id="s2",
                work_item_id="w1",
                node_id="env_setup",
                repo=str(repo),
            )
            assert status == "done"
            row = database.read(
                lambda c: c.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["base_ref"] == "PINNED"
        finally:
            await database.close()

    asyncio.run(scenario())


def _make_item(database, repo, wid="w1"):
    return database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id="B",
            title="t",
            repo=str(repo),
            chain_template="quick-task",
            chain_definition="{}",
        )
    )


def _base_ref(database, wid="w1"):
    return database.read(
        lambda c: c.execute("SELECT base_ref FROM work_items WHERE id = ?", (wid,)).fetchone()
    )["base_ref"]


def test_ensure_worktree_is_idempotent_and_pins_base_ref_once(tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, repo)
            first = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            assert first.is_dir()
            pinned = _base_ref(database)
            assert pinned

            # A second commit lands, then a second call: the pin must not move,
            # and the existing worktree must be reused rather than re-added
            # (git refuses to add a worktree at a path that already exists).
            (repo / "second.txt").write_text("x")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "second")
            again = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            assert again == first
            assert _base_ref(database) == pinned
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_reattaches_an_existing_branch(tmp_path):
    """A rejected gate can leave the item's branch behind with no worktree.
    The next run must check that branch out, not fail on `-b` for a name in
    use."""
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, repo)
            wt = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            _git(repo, "worktree", "remove", "--force", str(wt))

            again = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            assert again.is_dir()
            assert git_read(again, "rev-parse", "--abbrev-ref", "HEAD") == "kraft/t-w1"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_branch_is_a_title_slug(tmp_path):
    """The branch on `main` after the merge has to say what was merged."""
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="b63d95be41884b6e83a423c114a97ce3",
                    bead_id="B",
                    title="Readable merge records: branch slugs & GFM tables!",
                    repo=str(repo),
                    chain_template="quick-task",
                    chain_definition="{}",
                )
            )
            wt = await kraft_builtins.ensure_worktree(
                database,
                rd,
                repo=str(repo),
                work_item_id="b63d95be41884b6e83a423c114a97ce3",
            )
            expected = "kraft/readable-merge-records-branch-slugs-gfm-tables-b63d95be"
            assert git_read(wt, "rev-parse", "--abbrev-ref", "HEAD") == expected
            branches = subprocess.run(
                ["git", "branch", "--list", expected],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert expected in branches
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_keeps_the_legacy_uuid_branch(tmp_path):
    """No stranding: an item whose row predates the `branch` column keeps
    `kraft/<id>`, which is the branch its worktree and open MR already use."""
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, repo)
            await database.write(
                lambda c: c.execute("UPDATE work_items SET branch = NULL WHERE id='w1'")
            )
            wt = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            assert git_read(wt, "rev-parse", "--abbrev-ref", "HEAD") == "kraft/w1"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_raises_when_git_fails(tmp_path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, not_a_repo)
            with pytest.raises(RuntimeError, match="w1"):
                await kraft_builtins.ensure_worktree(
                    database, rd, repo=str(not_a_repo), work_item_id="w1"
                )
        finally:
            await database.close()

    asyncio.run(scenario())
