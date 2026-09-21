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
    v1_resolved,
)

from kraft import builtins as kraft_builtins
from kraft import store
from kraft.config import git_read

#: A repo that deliberately needs no preparation. Most tests here are about
#: git and attachments, not environments.
NO_SETUP = {"setup_command": ""}


async def _prepare(database, rd, repo, *, repo_entry=None, attachments=None, work_item_id="w1"):
    """What `walk.run_once` does before the first node dispatches.

    `env_setup` was a node task until Template Schema V1 deleted it (there is no
    `BuiltinAction` for it, so no V1 chain can name one): its two halves are
    `ensure_worktree` and `prepare_runtime` now, called in that order, and these
    tests drive the pair the way the walk does. Returns the preparation report.
    """
    entry = NO_SETUP if repo_entry is None else repo_entry
    worktree = await kraft_builtins.ensure_worktree(
        database,
        rd,
        repo=str(repo),
        work_item_id=work_item_id,
        attachments=attachments,
        repo_entry=entry,
    )
    return await kraft_builtins.prepare_runtime(worktree, Path(repo), entry)


def _make_item(database, repo, wid="w1", **kw):
    """Work item `wid` on `repo`: quick-task, empty chain definition, unless `kw` says otherwise."""
    fields = {
        "bead_id": "B",
        "title": "t",
        "chain_template": "quick-task",
        "chain_definition": "{}",
    }
    return database.write(
        lambda c: store.create_work_item(c, id=wid, repo=str(repo), **(fields | kw))
    )


def _base_ref(database, wid="w1"):
    return database.read(
        lambda c: c.execute("SELECT base_ref FROM work_items WHERE id = ?", (wid,)).fetchone()
    )["base_ref"]


def _branch(database, wid="w1"):
    return store.branch_for(
        database.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        )
    )


def _worktree(database, run_dirs, repo, *, repo_entry=NO_SETUP, **kw):
    """`ensure_worktree` for item w1 on `repo`; no setup command unless `repo_entry` says so."""
    return kraft_builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id="w1", repo_entry=repo_entry, **kw
    )


async def test_worktree_preparation_creates_the_worktree_and_branch(database, run_dirs, repo):
    await _make_item(database, repo)
    report = await _prepare(database, run_dirs, repo)
    assert "worktree ready" in report
    worktree = run_dirs.worktrees / "w1"
    assert (worktree / "calc.py").is_file()
    branch = _branch(database)
    assert branch == "kraft/t-w1"
    branches = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert branch in branches
    # No session row stands in for the deleted node: preparation is
    # runtime work the walk does, not a task a chain dispatches.
    assert (
        database.read(lambda c: c.execute("SELECT COUNT(*) FROM worker_sessions").fetchone()[0])
        == 0
    )


async def test_worktree_preparation_copies_an_uncommitted_attachment(database, run_dirs, repo):
    plan = repo / ".engineering" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# the plan\n")  # never committed

    await _make_item(database, repo)
    report = await _prepare(
        database,
        run_dirs,
        repo,
        attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
    )
    assert "worktree ready" in report
    copied = run_dirs.worktrees / "w1" / ".engineering" / "plans" / "p.md"
    assert copied.read_text() == "# the plan\n"


async def test_ensure_worktree_alone_copies_attachments_before_any_node_runs(
    database, run_dirs, repo
):
    """The chain's first node can be an agent task, so `plan`'s attached-spec
    fallback only works if the copy happened at `ensure_worktree` time. Prove it
    without going through the walk's own preparation at all."""
    spec = repo / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# the spec\n")  # never committed

    await _make_item(database, repo, chain_template="default")
    worktree = await _worktree(
        database, run_dirs, repo, attachments=[{"kind": "spec", "path": ".engineering/specs/s.md"}]
    )
    copied = worktree / ".engineering" / "specs" / "s.md"
    assert copied.read_text() == "# the spec\n"


async def test_an_attachment_from_another_worktree_is_copied_from_its_source(
    tmp_path, database, run_dirs, repo
):
    """Kraft-85wk's other half. The validator accepted a path that does not
    exist under `repo`, so the copy has to read the absolute `source` it stored.
    Reading `repo / path` here hits the `src.is_file()` guard and skips
    silently — a trimmed spec gate with no spec, which is worse than the 422
    this replaces."""
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

    await _make_item(database, repo)
    worktree = await _worktree(
        database,
        run_dirs,
        repo,
        attachments=[{"kind": "spec", "path": ".engineering/specs/s.md", "source": str(spec)}],
    )
    copied = worktree / ".engineering" / "specs" / "s.md"
    assert copied.read_text() == "# from the other worktree\n"


async def test_a_missing_attachment_source_fails_loudly(tmp_path, database, run_dirs, repo):
    """After Kraft-eqgn the source is Kraft's own copy, so a missing file here
    means Kraft lost it. The gate it justified is already trimmed and cannot be
    put back, so continuing would run the item with a document it promised and
    does not have — silently, which is the bug this closes."""
    await _make_item(database, repo)
    with pytest.raises(FileNotFoundError, match="missing from Kraft's storage"):
        await _worktree(
            database,
            run_dirs,
            repo,
            attachments=[
                {
                    "kind": "spec",
                    "path": ".engineering/specs/s.md",
                    "source": str(tmp_path / "run" / "attachments" / "w1" / "spec.md"),
                }
            ],
        )


def _porcelain(cwd):
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.splitlines()


def test_commit_paths_stages_and_commits_only_the_named_paths(repo):
    """The primitive on its own (Kraft-xwen). Named path committed, an unrelated
    dirty file untouched, and a second call with nothing left to stage is a
    no-op rather than git's "nothing to commit" failure."""
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


def test_restore_branch_recovers_from_a_stranded_mid_merge_diagnostic_branch(repo):
    """Kraft-v5qd: an agent diagnosing a conflict checked out a scratch
    branch, ran a test merge that hit real conflicts, and never checked back
    out -- the worktree sat on the scratch branch, mid-merge, with the
    item's own branch untouched underneath. `restore_branch` is the net for
    exactly that, whatever left the worktree there."""
    readme = repo / "README.md"

    _git(repo, "checkout", "-b", "kraft/w1")
    readme.write_text("item's own change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "item work")
    item_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout

    _git(repo, "checkout", "main")
    readme.write_text("diverging main change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "main diverges")

    # An agent's ad-hoc diagnosis: branch off the item's own branch, try a
    # merge, hit a conflict, and stop mid-merge without cleaning up.
    _git(repo, "checkout", "kraft/w1")
    _git(repo, "checkout", "-b", "_conflict_test")
    subprocess.run(["git", "merge", "main"], cwd=repo, capture_output=True, text=True)
    assert any("README.md" in line for line in _porcelain(repo)), (
        "the scenario did not actually conflict"
    )

    kraft_builtins.restore_branch(repo, "kraft/w1")

    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current == "kraft/w1"
    assert _porcelain(repo) == [], "the aborted merge left the tree dirty"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert head == item_head, "the item's own commit must be untouched"


def test_restore_branch_is_a_no_op_when_already_on_the_right_branch(repo):
    _git(repo, "checkout", "-b", "kraft/w1")

    kraft_builtins.restore_branch(repo, "kraft/w1")

    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current == "kraft/w1"


async def test_an_attached_document_is_committed_in_the_worktree(database, run_dirs, repo):
    """Kraft-8iw6. An uncommitted attachment lands in the worktree as an
    untracked file that no agent changed and so no agent commits, and
    forge._assert_clean then refuses to open the merge request over it."""
    spec = repo / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# never committed\n")

    await _make_item(database, repo)
    worktree = await _worktree(
        database, run_dirs, repo, attachments=[{"kind": "spec", "path": ".engineering/specs/s.md"}]
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


async def test_worktree_preparation_leaves_a_committed_attachment_alone(
    tmp_path, database, run_dirs
):
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# committed\n"})
    (repo / ".engineering" / "plans" / "p.md").write_text("# dirty working tree\n")

    await _make_item(database, repo)
    await _prepare(
        database,
        run_dirs,
        repo,
        attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
    )
    copied = run_dirs.worktrees / "w1" / ".engineering" / "plans" / "p.md"
    # git brought the committed version; the copy must not clobber it
    assert copied.read_text() == "# committed\n"


async def test_worktree_preparation_does_not_write_through_a_symlinked_attachment(
    tmp_path, database, run_dirs, repo
):
    """HEAD (what the fresh worktree is checked out from) can hold a symlink
    at the attachment path that the browser-supplied path never showed:
    validation only ever looked at the repo's working tree. A dangling
    symlink there must not become a write to wherever it points."""
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

    await _make_item(database, repo)
    report = await _prepare(
        database,
        run_dirs,
        repo,
        attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
    )
    assert "worktree ready" in report
    assert not outside.exists()


# `builtins.noop` lived here. V1 has no name indirection for a hook to be bound
# to, so there is nothing for an inert placeholder task to be: a test that needs
# one uses a `subprocess` task running `true`.


async def test_worktree_preparation_stamps_base_ref(database, run_dirs, repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    await _make_item(database, repo)
    await _prepare(database, run_dirs, repo)
    row = database.read(
        lambda c: c.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()
    )
    assert row["base_ref"] == head


async def test_worktree_preparation_does_not_restamp_on_reentry(database, run_dirs, repo):
    await _make_item(database, repo)
    await _prepare(database, run_dirs, repo)
    await database.write(lambda c: store.set_base_ref(c, "w1", "PINNED"))
    # second call: `ensure_worktree` returns early (the worktree already
    # exists) and re-pins nothing; preparation still re-runs and reports.
    report = await _prepare(database, run_dirs, repo)
    assert "worktree ready" in report
    row = database.read(
        lambda c: c.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()
    )
    assert row["base_ref"] == "PINNED"


async def test_ensure_worktree_is_idempotent_and_pins_base_ref_once(database, run_dirs, repo):
    await _make_item(database, repo)
    first = await _worktree(database, run_dirs, repo)
    assert first.is_dir()
    pinned = _base_ref(database)
    assert pinned

    # A second commit lands, then a second call: the pin must not move,
    # and the existing worktree must be reused rather than re-added
    # (git refuses to add a worktree at a path that already exists).
    (repo / "second.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "second")
    again = await _worktree(database, run_dirs, repo)
    assert again == first
    assert _base_ref(database) == pinned


async def test_ensure_worktree_reattaches_an_existing_branch(database, run_dirs, repo):
    """A rejected gate can leave the item's branch behind with no worktree.
    The next run must check that branch out, not fail on `-b` for a name in
    use."""
    await _make_item(database, repo)
    wt = await _worktree(database, run_dirs, repo)
    _git(repo, "worktree", "remove", "--force", str(wt))

    again = await _worktree(database, run_dirs, repo)
    assert again.is_dir()
    row = database.read(lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone())
    assert git_read(again, "rev-parse", "--abbrev-ref", "HEAD") == store.branch_for(row)


async def test_ensure_worktree_branch_is_a_title_slug(database, run_dirs, repo):
    """The branch on `main` after the merge has to say what was merged."""
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
        run_dirs,
        repo=str(repo),
        work_item_id="b63d95be41884b6e83a423c114a97ce3",
        repo_entry=NO_SETUP,
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


async def test_ensure_worktree_keeps_the_legacy_uuid_branch(database, run_dirs, repo):
    """No stranding: an item whose row predates the `branch` column keeps
    `kraft/<id>`, which is the branch its worktree and open MR already use."""
    await _make_item(database, repo)
    await database.write(lambda c: c.execute("UPDATE work_items SET branch = NULL WHERE id='w1'"))
    wt = await _worktree(database, run_dirs, repo)
    assert git_read(wt, "rev-parse", "--abbrev-ref", "HEAD") == "kraft/w1"


@pytest.mark.parametrize(
    ("setup_command", "raises"),
    [
        ("touch prepared.txt", None),
        ("", None),
        # No default and no fallback: Python is not a special case (Kraft-kji8w).
        (None, "setup_command"),
        # Kraft-s0w2l: the chain used to dispatch into a known-broken worktree.
        # `tr` so the stderr the message must carry is not in the command text.
        ("echo deliberate failure | tr a-z A-Z >&2; exit 3", "DELIBERATE FAILURE"),
    ],
    ids=[
        "declared-runs-in-the-worktree",
        "empty-runs-nothing",
        "undeclared-stops",
        "failing-raises-its-stderr",
    ],
)
async def test_ensure_worktree_runs_the_declared_setup_command(
    database, run_dirs, repo, setup_command, raises
):
    """The repo's `setup_command` runs in the new worktree; an undeclared one
    and a failing one both raise, naming why."""
    await _make_item(database, repo)
    entry = {} if setup_command is None else {"setup_command": setup_command}
    if raises:
        with pytest.raises(RuntimeError, match=raises):
            await _worktree(database, run_dirs, repo, repo_entry=entry)
        return
    wt = await _worktree(database, run_dirs, repo, repo_entry=entry)
    assert wt.is_dir()
    assert (wt / "prepared.txt").exists() == ("touch" in setup_command)


async def test_a_failed_setup_leaves_no_worktree_so_retry_reruns_it(database, run_dirs, repo):
    """The regression that makes the whole fix real. ensure_worktree returns
    early when the directory exists, so a worktree left behind by a failed
    setup would make `kraft item retry` -- the only door back onto a stopped
    item -- skip setup entirely and dispatch into the broken environment."""
    await _make_item(database, repo)
    with pytest.raises(RuntimeError):
        await _worktree(database, run_dirs, repo, repo_entry={"setup_command": "exit 1"})
    assert not (run_dirs.worktrees / "w1").exists()

    wt = await _worktree(
        database, run_dirs, repo, repo_entry={"setup_command": "touch recovered.txt"}
    )
    assert (wt / "recovered.txt").exists()


async def test_setup_runs_after_local_files_are_carried(database, run_dirs, repo):
    """Kraft-gxcmy: uv picks an interpreter when it runs, so a pin that lands
    after the toolchain is a pin that changed nothing."""
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")

    await _make_item(database, repo)
    wt = await kraft_builtins.ensure_worktree(
        database,
        run_dirs,
        repo=str(repo),
        work_item_id="w1",
        repo_entry={
            "setup_command": "cp .python-version seen-by-setup.txt",
            "local_files": [".python-version"],
        },
    )
    assert (wt / "seen-by-setup.txt").read_text().strip() == "3.11"


async def test_ensure_worktree_pins_commit_identity_into_the_worktree(database, run_dirs, repo):
    """Kraft-cppp. A linked worktree must carry its own explicit identity, not
    rely on inheriting the repo's -- that is what left submodule commits
    authored `Kraft Agent <kraft@local>` on the real occurrence."""
    await _make_item(database, repo)
    wt = await _worktree(database, run_dirs, repo)
    assert git_read(wt, "config", "--local", "user.name") == "t"
    assert git_read(wt, "config", "--local", "user.email") == "t@t"


async def test_ensure_worktree_checks_out_a_declared_submodule_on_the_items_branch(
    tmp_path, database, run_dirs
):
    root, _sub = make_repo_with_submodule(tmp_path)

    await _make_item(
        database,
        root,
        chain_template="default",
        submodules=["repos/pkg"],
        root_merge_policy="bump_no_mr",
    )
    worktree = await _worktree(database, run_dirs, root)
    row = database.read(lambda c: c.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone())
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
    branch, current, repos = branch, current, repos

    assert current == branch
    assert [r["role"] for r in repos] == ["submodule", "root"]
    assert repos[0]["path"].endswith("repos/pkg")


async def test_ensure_worktree_never_runs_a_blanket_submodule_init(
    tmp_path, monkeypatch, database, run_dirs
):
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

    await _make_item(
        database,
        root,
        chain_template="default",
        submodules=["repos/pkg"],
        root_merge_policy="bump_no_mr",
    )
    await _worktree(database, run_dirs, root)
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


async def test_scan_submodules_finds_a_submodule_the_agent_touched_but_nobody_declared(
    tmp_path, database, run_dirs
):
    root, _sub = make_repo_with_submodule(tmp_path)

    await _make_item(
        database, root, chain_template="default"
    )  # no submodules declared -- exactly the real item's shape
    worktree = await _worktree(database, run_dirs, root)
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
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="implementation",
        hook_point="on.repos.scan",
        round=0,
        repo=str(root),
        worktree=str(worktree),
    )
    repos = database.read(lambda c: store.repos_for(c, "w1"))
    assert len(repos) == 1
    assert repos[0]["role"] == "submodule"
    assert repos[0]["path"].endswith("repos/pkg")


async def test_ensure_worktree_raises_when_git_fails(tmp_path, database, run_dirs):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    await _make_item(database, not_a_repo)
    with pytest.raises(RuntimeError, match="w1"):
        await _worktree(database, run_dirs, not_a_repo)


async def test_the_worktree_and_the_forge_agree_on_the_branch(
    tmp_path, monkeypatch, repo, database, run_dirs
):
    """Kraft-nhps. Three places used to build `kraft/<uuid>` and none of them
    shared a line. They now read the one value written at intake, which is the
    only way two derivations of one string cannot drift."""
    from kraft import executor
    from kraft.api.routes import lifecycle
    from kraft.executor import dispatch

    tracker = isolated_bd(tmp_path)
    captured: dict = {}

    async def fake_forge_run_task(db_, run_dirs_, **kw):
        captured.update(kw)
        return "done"

    monkeypatch.setattr(dispatch._forge, "run_task", fake_forge_run_task)

    wid = await executor.intake(
        database,
        run_dirs,
        title="Teach probe_repo about worktrees",
        repo=str(repo),
        chain=v1_resolved(
            [
                {
                    "id": "open_mr",
                    "kind": "exec",
                    "tasks": [{"id": "open", "kind": "forge", "target": "mr.open_draft"}],
                }
            ]
        ),
        bd_cwd=str(tracker),
    )
    launch = executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None)
    # one node, no gate: the chain completes and closes its own bead in
    # `tracker`, which is why intake and run are handed the same one
    await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        launch=launch,
    )
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    branch = store.branch_for(row)
    assert branch == f"kraft/teach-probe-repo-about-worktrees-{wid[:8]}"
    # what git created
    assert git_read(run_dirs.worktrees / wid, "rev-parse", "--abbrev-ref", "HEAD") == branch
    # what the forge adapter was handed
    assert captured["branch"] == branch
    # and what abandon reclaims
    assert await lifecycle._remove_worktree(repo, run_dirs.worktrees / wid, branch)
    listed = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert listed.strip() == ""


@pytest.mark.parametrize("unset", ["user.name", "user.email"], ids=["no-name", "no-email"])
async def test_ensure_worktree_raises_when_the_repo_has_no_commit_identity(
    tmp_path, monkeypatch, database, run_dirs, repo, unset
):
    """Kraft-mxdx. A repo with no `user.email` (or `user.name`) anywhere in its
    config chain used to hit `fatal: unable to auto-detect email address` on
    the first commit a node attempted, well after `ensure_worktree` had already
    returned success — the agent then invented an identity to get unblocked.
    Fail here instead, at worktree creation, naming the missing key."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git(repo, "config", "--unset", unset)
    await _make_item(database, repo)
    with pytest.raises(RuntimeError, match=unset):
        await _worktree(database, run_dirs, repo)


def test_refresh_worktree_base_returns_none_when_no_worktree(tmp_path, repo):
    worktree = tmp_path / "nope"
    result = asyncio.run(kraft_builtins.refresh_worktree_base(worktree, repo, "kraft/w1"))
    assert result is None


async def test_refresh_worktree_base_returns_none_when_already_up_to_date(database, run_dirs, repo):
    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    branch = _branch(database)
    # no origin remote exists here at all -- the already-pushed probe's
    # failure must be swallowed (expected_failure), not raised
    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch)
    assert result is None


async def test_refresh_worktree_base_skips_when_branch_already_pushed(
    tmp_path, database, run_dirs, repo
):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")

    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    branch = _branch(database)
    _git(worktree, "push", "-q", "-u", "origin", branch)
    before = git_read(worktree, "rev-parse", "HEAD")

    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on")

    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch)
    assert result is None
    assert git_read(worktree, "rev-parse", "HEAD") == before


async def test_refresh_worktree_base_rebases_and_returns_new_head(database, run_dirs, repo):
    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    branch = _branch(database)

    (worktree / "worktree_work.txt").write_text("done in the worktree\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "worktree work")

    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on")
    new_head = git_read(repo, "rev-parse", "HEAD")

    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch)
    assert result == new_head
    assert (worktree / "worktree_work.txt").is_file()
    assert (worktree / "moved.txt").is_file()
    assert git_read(worktree, "merge-base", "--is-ancestor", new_head, "HEAD") == ""


def _repo_with_origin(tmp_path):
    """`make_repo` pushed to a bare `origin`, plus a second clone standing in for
    everyone else -- what lands through it reaches origin but not `repo`."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    repo = make_repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git(other, "config", "user.email", "o@o")
    _git(other, "config", "user.name", "o")
    return repo, other


def _land_upstream(other, name):
    (other / name).write_text("landed upstream\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", f"upstream {name}")
    _git(other, "push", "-q", "origin", "main")
    return git_read(other, "rev-parse", "HEAD")


async def test_ensure_worktree_forks_from_origin_when_the_local_checkout_is_behind(
    tmp_path, database, run_dirs
):
    # Kraft-k647: the connected repo's checkout is only as fresh as its owner's
    # last pull, and Kraft merges on the forge -- forking from it started every
    # item behind the branch its MR targets.
    repo, other = _repo_with_origin(tmp_path)
    upstream = _land_upstream(other, "upstream.txt")

    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    assert (worktree / "upstream.txt").is_file()
    assert _base_ref(database) == upstream


async def test_refresh_worktree_base_rebases_onto_origin_when_the_local_checkout_is_behind(
    tmp_path, database, run_dirs
):
    # Kraft-k647: against the stale local HEAD this answered "nothing to
    # rebase" -- the branch already contained it -- on every pre_mr_rebase.
    repo, other = _repo_with_origin(tmp_path)

    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    branch = _branch(database)
    (worktree / "worktree_work.txt").write_text("done in the worktree\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "worktree work")
    upstream = _land_upstream(other, "upstream.txt")

    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch)
    assert result == upstream
    assert (worktree / "upstream.txt").is_file()
    assert (worktree / "worktree_work.txt").is_file()


async def test_refresh_worktree_base_falls_back_to_local_head_when_origin_is_unreachable(
    tmp_path, database, run_dirs, repo
):
    # Offline, or credentials the server process cannot reach: the rebase still
    # happens against what the checkout has, rather than failing the resume.
    _git(repo, "remote", "add", "origin", str(tmp_path / "gone.git"))

    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    branch = _branch(database)
    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on")
    local = git_read(repo, "rev-parse", "HEAD")

    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch)
    assert result == local
    assert (worktree / "moved.txt").is_file()


def test_upstream_head_falls_back_to_the_last_fetched_origin_ref_before_local_head(tmp_path):
    # A failed fetch still leaves the last good view of origin, and that -- not
    # whatever the checkout has on it, unpushed commits or another branch -- is
    # what the MR targets.
    repo, _ = _repo_with_origin(tmp_path)
    fetched = git_read(repo, "rev-parse", "origin/main")
    (repo / "local_only.txt").write_text("never pushed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "local only")
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    assert asyncio.run(kraft_builtins.upstream_head(repo)) == fetched


def test_upstream_head_sees_origin_even_when_the_fetch_refspec_skips_the_default(tmp_path):
    # A --single-branch clone of some other branch: a bare `fetch origin main`
    # would not update refs/remotes/origin/main, and the tip read back is stale.
    repo, other = _repo_with_origin(tmp_path)
    _git(repo, "config", "remote.origin.fetch", "+refs/heads/other:refs/remotes/origin/other")
    upstream = _land_upstream(other, "upstream.txt")

    assert asyncio.run(kraft_builtins.upstream_head(repo)) == upstream


async def test_refresh_worktree_base_raises_and_aborts_on_conflict(database, run_dirs, repo):
    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    branch = _branch(database)

    (worktree / "calc.py").write_text("def add(a, b):\n    return a - b - 1  # bug: should be +\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "worktree edit")
    worktree_head = git_read(worktree, "rev-parse", "HEAD")

    (repo / "calc.py").write_text("def add(a, b):\n    return a - b - 2  # bug: should be +\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "conflicting edit")

    with pytest.raises(RuntimeError, match="git rebase failed"):
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch)

    assert git_read(worktree, "status", "--porcelain") == ""
    assert git_read(worktree, "rev-parse", "HEAD") == worktree_head


async def test_mr_rebase_moves_the_base_and_records_a_done_session(database, run_dirs, repo):
    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    branch = _branch(database)

    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on")
    new_head = git_read(repo, "rev-parse", "HEAD")

    status = await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="pre_mr_rebase",
        hook_point="on.mr.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=branch,
    )
    assert status == "done"
    assert _base_ref(database) == new_head
    assert (worktree / "moved.txt").is_file()
    session = database.read(
        lambda c: c.execute(
            "SELECT hook_point, status FROM worker_sessions WHERE id='s1'"
        ).fetchone()
    )
    assert session["hook_point"] == "on.mr.rebase"
    assert session["status"] == "done"


async def test_mr_rebase_leaves_base_ref_alone_when_nothing_to_rebase(database, run_dirs, repo):
    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    branch = _branch(database)
    before = _base_ref(database)

    status = await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="pre_mr_rebase",
        hook_point="on.mr.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=branch,
    )
    assert status == "done"
    assert _base_ref(database) == before


async def test_mr_rebase_raises_on_conflict(database, run_dirs, repo):
    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    branch = _branch(database)

    (worktree / "calc.py").write_text("def add(a, b):\n    return a - b - 1  # bug: should be +\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "worktree edit")

    (repo / "calc.py").write_text("def add(a, b):\n    return a - b - 2  # bug: should be +\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "conflicting edit")

    with pytest.raises(RuntimeError, match="git rebase failed"):
        await kraft_builtins.mr_rebase(
            database,
            run_dirs,
            session_id="s1",
            work_item_id="w1",
            node_id="pre_mr_rebase",
            hook_point="on.mr.rebase",
            round=0,
            repo=str(repo),
            worktree=str(worktree),
            branch=branch,
        )
    assert _base_ref(database) != git_read(repo, "rev-parse", "HEAD")


async def test_refresh_worktree_base_raises_rebase_conflict_a_runtimeerror_subclass(
    database, run_dirs, repo
):
    """.23: the three callers that can now *act* on a conflict need to tell
    one apart from any other git failure without matching on message text --
    and every existing `except RuntimeError` around this call must keep
    working unchanged, since `RebaseConflict` is a subclass."""
    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    branch = _branch(database)

    (worktree / "calc.py").write_text("def add(a, b):\n    return a - b - 1  # bug: should be +\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "worktree edit")

    (repo / "calc.py").write_text("def add(a, b):\n    return a - b - 2  # bug: should be +\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "conflicting edit")

    with pytest.raises(kraft_builtins.RebaseConflict):
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch)

    # The compatibility claim itself: a bare `except RuntimeError` --
    # every call site that predates this bead -- still catches it.
    try:
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch)
        raised = False
    except RuntimeError:
        raised = True
    assert raised


def _linked_worktree(repo, tmp_path, name="wt"):
    wt = tmp_path / name
    subprocess.run(
        ["git", "worktree", "add", "-q", str(wt), "-b", name],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return wt


def test_carry_local_files_copies_an_ignored_untracked_file(tmp_path, repo):
    """The whole point of Kraft-gxcmy: the pin exists in the developer's
    checkout and nowhere in the worktree, because it was never committed."""
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")
    wt = _linked_worktree(repo, tmp_path)

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert carried == [".python-version"]
    assert refused == []
    assert (wt / ".python-version").read_text() == "3.11\n"
    # and invisible to git, so `open_mr`'s dirty-worktree guard never sees it
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True
    )
    assert status.stdout == ""


def test_carry_local_files_refuses_a_file_the_worktree_would_not_ignore(tmp_path, repo):
    """Kraft cannot hold an unignored file out of a commit, so it declines to
    create one. A refusal is reported, never raised."""
    (repo / ".python-version").write_text("3.11\n")
    wt = _linked_worktree(repo, tmp_path)

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert carried == []
    assert refused == [".python-version"]
    assert not (wt / ".python-version").exists()


def test_carry_local_files_skips_a_symlinked_destination(tmp_path, repo):
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")
    wt = _linked_worktree(repo, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched\n")
    (wt / ".python-version").symlink_to(outside)

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert carried == []
    assert refused == []
    assert outside.read_text() == "untouched\n"


def test_carry_local_files_leaves_an_existing_destination_alone(tmp_path, repo):
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")
    wt = _linked_worktree(repo, tmp_path)
    (wt / ".python-version").write_text("3.12\n")

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert carried == []
    assert refused == []
    assert (wt / ".python-version").read_text() == "3.12\n"


def test_carry_local_files_ignores_a_source_that_is_not_there(tmp_path, repo):
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    wt = _linked_worktree(repo, tmp_path)

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert carried == []
    assert refused == []


def test_carry_local_files_refuses_a_directory_entry_missing_its_trailing_slash(tmp_path, repo):
    """`config.py` only rejects a directory entry that ends in `/`, so a plain
    `.venv` in `local_files` passes validation and reaches here. It is not a
    file, so it always fails `src.is_file()` -- the same branch a genuinely
    absent source takes. Without this, a typo like this is carried nowhere,
    refused nowhere, and never shows up in the preparation report either
    (`_uncarried_local_files`'s `"/" not in n` filter drops the `--directory`
    listing's `.venv/` entry) -- zero feedback for a plausible mistake."""
    (repo / ".venv").mkdir()
    (repo / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    wt = _linked_worktree(repo, tmp_path)

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".venv"])

    assert carried == []
    assert refused == [".venv"]


async def test_ensure_worktree_without_local_files_is_unchanged(database, run_dirs, repo):
    """The feature is opt-in: an unconfigured repo must behave exactly as before."""
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")

    await _make_item(database, repo, chain_template="default")
    worktree = await _worktree(database, run_dirs, repo)
    assert not (worktree / ".python-version").exists()


def test_uncarried_local_files_names_root_files_missing_from_the_worktree(tmp_path, repo):
    """An unconfigured repo is the default, so the gap has to be visible
    without anyone having configured anything (Kraft-gxcmy)."""
    (repo / ".gitignore").write_text(".venv/\n.env\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore local state")
    (repo / ".python-version").write_text("3.11\n")  # untracked, not ignored
    (repo / ".env").write_text("TOKEN=x\n")  # untracked and ignored
    (repo / ".venv").mkdir()  # a directory: never reported
    (repo / ".venv" / "marker").write_text("x\n")
    wt = _linked_worktree(repo, tmp_path)

    missing = kraft_builtins._uncarried_local_files(repo, wt)

    assert missing == [".env", ".python-version"]


def test_uncarried_local_files_omits_what_was_carried(tmp_path, repo):
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")
    wt = _linked_worktree(repo, tmp_path)
    kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert kraft_builtins._uncarried_local_files(repo, wt) == []


async def test_mr_rebase_reports_a_moved_base_when_the_node_bounces(database, run_dirs, repo):
    """A node that declares `rebase_bounce_to` must not run its later steps
    against a base the rebase just moved. A node with no bounce target wants the
    opposite, which is why the flag decides."""
    from kraft.executor.context import BASE_MOVED

    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    row = database.read(lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone())
    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on")
    status = await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="open_mr",
        hook_point="on.mr.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=store.branch_for(row),
        has_rebase_bounce=True,
    )
    assert status == BASE_MOVED
    session = database.read(
        lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
    )
    assert session["status"] == "done"


async def test_mr_rebase_reports_done_when_it_moved_nothing(database, run_dirs, repo):
    """No movement, no stop: otherwise every `open_mr` would bounce once."""
    await _make_item(database, repo)
    worktree = await _worktree(database, run_dirs, repo)
    row = database.read(lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone())
    status = await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="open_mr",
        hook_point="on.mr.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=store.branch_for(row),
        has_rebase_bounce=True,
    )
    assert status == "done"


async def test_worktree_preparation_reruns_the_setup_command_on_every_entry(
    tmp_path, database, run_dirs, repo
):
    """A rebase can land a new lockfile (Kraft-zlsuk), so preparation re-runs on
    every entry into the walk rather than only at worktree creation."""
    marker = tmp_path / "setup-runs"
    entry = {**NO_SETUP, "setup_command": f"echo run >> {marker}"}

    await _make_item(database, repo)
    await kraft_builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id="w1", repo_entry=entry
    )
    before = marker.read_text().count("run")
    for n in (1, 2):
        await kraft_builtins.prepare_runtime(run_dirs.worktrees / "w1", Path(repo), entry)
        assert marker.read_text().count("run") == before + n
