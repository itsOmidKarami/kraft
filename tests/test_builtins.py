import asyncio
import subprocess
from pathlib import Path

import pytest
from support.harness import (
    _git,
    isolated_bd,
    make_repo,
    make_repo_with_engineering,
    make_repo_with_submodule,
)

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
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            branch = store.branch_for(row)
            assert branch == "kraft/t-w1"
            branches = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert branch in branches
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


def test_an_attachment_from_another_worktree_is_copied_from_its_source(tmp_path):
    """Kraft-85wk's other half. The validator accepted a path that does not
    exist under `repo`, so the copy has to read the absolute `source` it stored.
    Reading `repo / path` here hits the `src.is_file()` guard and skips
    silently — a trimmed spec gate with no spec, which is worse than the 422
    this replaces."""
    repo = make_repo(tmp_path)
    other = tmp_path / "other-wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(other), "-b", "other"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    spec = other / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# from the other worktree\n")

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
            worktree = await kraft_builtins.ensure_worktree(
                database,
                rd,
                repo=str(repo),
                work_item_id="w1",
                attachments=[
                    {
                        "kind": "spec",
                        "path": ".engineering/specs/s.md",
                        "source": str(spec),
                    }
                ],
            )
            copied = worktree / ".engineering" / "specs" / "s.md"
            assert copied.read_text() == "# from the other worktree\n"
        finally:
            await database.close()

    asyncio.run(scenario())


def _porcelain(cwd):
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.splitlines()


def test_commit_paths_stages_and_commits_only_the_named_paths(tmp_path):
    """The primitive on its own (Kraft-xwen). Named path committed, an unrelated
    dirty file untouched, and a second call with nothing left to stage is a
    no-op rather than git's "nothing to commit" failure."""
    repo = make_repo(tmp_path)
    (repo / "wanted.md").write_text("attached\n")
    (repo / "unrelated.py").write_text("an agent is mid-edit\n")

    kraft_builtins._commit_paths(repo, ["wanted.md"], "chore: attach spec for w1")

    assert _porcelain(repo) == ["?? unrelated.py"]
    committed = subprocess.run(
        ["git", "show", "HEAD:wanted.md"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert committed == "attached\n"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout

    kraft_builtins._commit_paths(repo, ["wanted.md"], "chore: attach spec for w1")

    again = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert again == head, "a second call made an empty commit"


def test_an_attached_document_is_committed_in_the_worktree(tmp_path):
    """Kraft-8iw6. An uncommitted attachment lands in the worktree as an
    untracked file that no agent changed and so no agent commits, and
    forge._assert_clean then refuses to open the merge request over it."""
    repo = make_repo(tmp_path)
    spec = repo / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# never committed\n")

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
            worktree = await kraft_builtins.ensure_worktree(
                database,
                rd,
                repo=str(repo),
                work_item_id="w1",
                attachments=[{"kind": "spec", "path": ".engineering/specs/s.md"}],
            )
            assert _porcelain(worktree) == []
            in_head = subprocess.run(
                ["git", "show", "HEAD:.engineering/specs/s.md"],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert in_head == "# never committed\n"
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
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert git_read(again, "rev-parse", "--abbrev-ref", "HEAD") == store.branch_for(row)
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


def test_ensure_worktree_syncs_deps_so_the_first_commit_can_run_pre_commit(tmp_path):
    """A fresh worktree had no `.venv`, so `spec` -- the first node to commit,
    ahead of `env_setup` in `default.yaml` -- hit `Failed to spawn: pre-commit`
    and fell back to `--no-verify`, skipping ruff-format on the doc it had just
    written (Kraft-i047). `uv sync` here, before any node dispatches, closes
    that gap for any worktree that is actually a uv project."""
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add pyproject")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, repo)
            wt = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            assert (wt / ".venv" / "bin" / "python3").exists()
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_skips_sync_when_repo_has_no_pyproject(tmp_path):
    """The common test fixture (`make_repo`) is not a uv project; `uv sync`
    against it would fail loudly for every other test in this file if the
    gate on `pyproject.toml` ever slipped."""
    repo = make_repo(tmp_path)
    assert not (repo / "pyproject.toml").exists()

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, repo)
            wt = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            assert not (wt / ".venv").exists()
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_pins_commit_identity_into_the_worktree(tmp_path):
    """Kraft-cppp. A linked worktree must carry its own explicit identity, not
    rely on inheriting the repo's -- that is what left submodule commits
    authored `Kraft Agent <kraft@local>` on the real occurrence."""
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, repo)
            wt = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            assert git_read(wt, "config", "--local", "user.name") == "t"
            assert git_read(wt, "config", "--local", "user.email") == "t@t"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_fails_when_the_repo_has_no_resolvable_identity(tmp_path, monkeypatch):
    """No `[user]` block anywhere the repo's git config precedence looks --
    worktree creation must refuse rather than let the first commit either die
    mid-node or fall back to a fabricated identity."""
    repo = tmp_path / "no-identity"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, repo)
            with pytest.raises(RuntimeError, match="user.name|user.email"):
                await kraft_builtins.ensure_worktree(
                    database, rd, repo=str(repo), work_item_id="w1"
                )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_checks_out_a_declared_submodule_on_the_items_branch(tmp_path):
    root, _sub = make_repo_with_submodule(tmp_path)

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
                    repo=str(root),
                    chain_template="default",
                    chain_definition="{}",
                    submodules=["repos/pkg"],
                    root_merge_policy="bump_no_mr",
                )
            )
            worktree = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id="w1"
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone()
            )
            branch = store.branch_for(row)
            sub_path = worktree / "repos" / "pkg"
            current = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=sub_path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            repos = database.read(lambda c: store.repos_for(c, "w1"))
            return branch, current, repos
        finally:
            await database.close()

    branch, current, repos = asyncio.run(scenario())

    assert current == branch
    assert [r["role"] for r in repos] == ["submodule", "root"]
    assert repos[0]["path"].endswith("repos/pkg")


def test_ensure_worktree_never_runs_a_blanket_submodule_init(tmp_path, monkeypatch):
    """Global constraint: only declared paths, never every submodule in
    .gitmodules (design §3 step 2)."""
    root, _sub = make_repo_with_submodule(tmp_path)
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(args, **kw):
        if "submodule" in args and "update" in args:
            calls.append(args)
        return real_run(args, **kw)

    monkeypatch.setattr(kraft_builtins.subprocess, "run", spy)

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
                    repo=str(root),
                    chain_template="default",
                    chain_definition="{}",
                    submodules=["repos/pkg"],
                    root_merge_policy="bump_no_mr",
                )
            )
            await kraft_builtins.ensure_worktree(database, rd, repo=str(root), work_item_id="w1")
        finally:
            await database.close()

    asyncio.run(scenario())
    assert calls == [
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "update",
            "--init",
            "--",
            "repos/pkg",
        ]
    ]


def test_scan_submodules_finds_a_submodule_the_agent_touched_but_nobody_declared(tmp_path):
    root, _sub = make_repo_with_submodule(tmp_path)

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
                    repo=str(root),
                    chain_template="default",
                    chain_definition="{}",
                )  # no submodules declared -- exactly the real item's shape
            )
            worktree = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id="w1"
            )
            # the agent's own half, done correctly: init + branch + commit
            # inside the submodule, root pointer never touched
            subprocess.run(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "submodule",
                    "update",
                    "--init",
                    "--",
                    "repos/pkg",
                ],
                cwd=worktree,
                check=True,
            )
            sub = worktree / "repos" / "pkg"
            subprocess.run(["git", "checkout", "-b", "agent-work"], cwd=sub, check=True)
            (sub / "new.txt").write_text("metric\n")
            subprocess.run(["git", "add", "-A"], cwd=sub, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "m"],
                cwd=sub,
                check=True,
            )

            await kraft_builtins.scan_submodules(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.repos.scan",
                round=0,
                repo=str(root),
                worktree=str(worktree),
            )
            return database.read(lambda c: store.repos_for(c, "w1"))
        finally:
            await database.close()

    repos = asyncio.run(scenario())
    assert len(repos) == 1
    assert repos[0]["role"] == "submodule"
    assert repos[0]["path"].endswith("repos/pkg")


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


def test_the_worktree_and_the_forge_agree_on_the_branch(tmp_path, monkeypatch):
    """Kraft-nhps. Three places used to build `kraft/<uuid>` and none of them
    shared a line. They now read the one value written at intake, which is the
    only way two derivations of one string cannot drift."""
    from kraft import api as kraft_api
    from kraft import executor
    from kraft.templates import Registry, Template

    repo = make_repo(tmp_path)
    tracker = isolated_bd(tmp_path)
    captured: dict = {}

    async def fake_forge_run_task(db_, run_dirs_, **kw):
        captured.update(kw)
        return "done"

    monkeypatch.setattr(executor._forge, "run_task", fake_forge_run_task)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="Teach probe_repo about worktrees",
                repo=str(repo),
                template=Template(
                    id="one-forge-node",
                    nodes=[{"id": "open_mr", "tasks": ["on.mr.open"], "gate_after": None}],
                ),
                bd_cwd=str(tracker),
            )
            registry = Registry(
                hooks={"on.mr.open": {"kind": "forge", "handler": "open_mr", "backend": "fake"}}
            )
            # one node, no gate: the chain completes and closes its own bead in
            # `tracker`, which is why intake and run are handed the same one
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            branch = store.branch_for(row)
            assert branch == f"kraft/teach-probe-repo-about-worktrees-{wid[:8]}"
            # what git created
            assert git_read(rd.worktrees / wid, "rev-parse", "--abbrev-ref", "HEAD") == branch
            # what the forge adapter was handed
            assert captured["branch"] == branch
            # and what abandon reclaims
            assert await kraft_api._remove_worktree(repo, rd.worktrees / wid, branch)
            listed = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert listed.strip() == ""
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_raises_when_the_repo_has_no_commit_identity(tmp_path):
    """Kraft-mxdx. A repo with no `user.email` anywhere in its config chain
    used to hit `fatal: unable to auto-detect email address` on the first
    commit a node attempted, well after `ensure_worktree` had already
    returned success — the agent then invented an identity to get unblocked.
    Fail here instead, at worktree creation, naming the missing key."""
    repo = make_repo(tmp_path)
    _git(repo, "config", "--unset", "user.email")

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
            with pytest.raises(RuntimeError, match="user.email"):
                await kraft_builtins.ensure_worktree(
                    database, rd, repo=str(repo), work_item_id="w1"
                )
        finally:
            await database.close()

    asyncio.run(scenario())
