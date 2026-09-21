import subprocess
from pathlib import Path

import pytest
from support import worktree as wtree
from support.harness import (
    _git,
    isolated_bd,
    make_repo_with_engineering,
    make_repo_with_submodule,
    v1_chain,
    v1_resolved,
    workspace_target,
)

from kraft import builtins as kraft_builtins
from kraft import store
from kraft.config import git_read


def _commit(cwd, name, text, message):
    """Write `name` under `cwd` and commit everything; returns the new HEAD."""
    (cwd / name).write_text(text)
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-m", message)
    return git_read(cwd, "rev-parse", "HEAD")


async def test_worktree_preparation_creates_the_worktree_and_branch(database, run_dirs, repo):
    await wtree.make_item(database, repo)
    report = await wtree.prepare(database, run_dirs, repo)
    assert "worktree ready" in report
    worktree = run_dirs.worktrees / "w1"
    assert (worktree / "calc.py").is_file()
    branch = wtree.branch(database)
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

    await wtree.make_item(database, repo)
    report = await wtree.prepare(
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

    await wtree.make_item(database, repo, chain_template="default")
    worktree = await wtree.ensure(
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

    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(
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
    await wtree.make_item(database, repo)
    with pytest.raises(FileNotFoundError, match="missing from Kraft's storage"):
        await wtree.ensure(
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


async def test_an_attached_document_is_committed_in_the_worktree(database, run_dirs, repo):
    """Kraft-8iw6. An uncommitted attachment lands in the worktree as an
    untracked file that no agent changed and so no agent commits, and
    forge._assert_clean then refuses to open the merge request over it."""
    spec = repo / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# never committed\n")

    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(
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

    await wtree.make_item(database, repo)
    await wtree.prepare(
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

    await wtree.make_item(database, repo)
    report = await wtree.prepare(
        database,
        run_dirs,
        repo,
        attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
    )
    assert "worktree ready" in report
    assert not outside.exists()


async def test_worktree_preparation_stamps_base_ref(database, run_dirs, repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    await wtree.make_item(database, repo)
    await wtree.prepare(database, run_dirs, repo)
    row = database.read(
        lambda c: c.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()
    )
    assert row["base_ref"] == head


async def test_worktree_preparation_does_not_restamp_on_reentry(database, run_dirs, repo):
    await wtree.make_item(database, repo)
    await wtree.prepare(database, run_dirs, repo)
    await database.write(lambda c: store.set_base_ref(c, "w1", "PINNED"))
    # second call: `ensure_worktree` returns early (the worktree already
    # exists) and re-pins nothing; preparation still re-runs and reports.
    report = await wtree.prepare(database, run_dirs, repo)
    assert "worktree ready" in report
    row = database.read(
        lambda c: c.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()
    )
    assert row["base_ref"] == "PINNED"


async def test_ensure_worktree_is_idempotent_and_pins_base_ref_once(database, run_dirs, repo):
    await wtree.make_item(database, repo)
    first = await wtree.ensure(database, run_dirs, repo)
    assert first.is_dir()
    pinned = wtree.base_ref(database)
    assert pinned

    # A second commit lands, then a second call: the pin must not move,
    # and the existing worktree must be reused rather than re-added
    # (git refuses to add a worktree at a path that already exists).
    _commit(repo, "second.txt", "x", "second")
    again = await wtree.ensure(database, run_dirs, repo)
    assert again == first
    assert wtree.base_ref(database) == pinned


async def test_ensure_worktree_reattaches_an_existing_branch(database, run_dirs, repo):
    """A rejected gate can leave the item's branch behind with no worktree.
    The next run must check that branch out, not fail on `-b` for a name in
    use."""
    await wtree.make_item(database, repo)
    wt = await wtree.ensure(database, run_dirs, repo)
    _git(repo, "worktree", "remove", "--force", str(wt))

    again = await wtree.ensure(database, run_dirs, repo)
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
        repo_entry=wtree.NO_SETUP,
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
    await wtree.make_item(database, repo)
    await database.write(lambda c: c.execute("UPDATE work_items SET branch = NULL WHERE id='w1'"))
    wt = await wtree.ensure(database, run_dirs, repo)
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
    await wtree.make_item(database, repo)
    entry = {} if setup_command is None else {"setup_command": setup_command}
    if raises:
        with pytest.raises(RuntimeError, match=raises):
            await wtree.ensure(database, run_dirs, repo, repo_entry=entry)
        return
    wt = await wtree.ensure(database, run_dirs, repo, repo_entry=entry)
    assert wt.is_dir()
    assert (wt / "prepared.txt").exists() == ("touch" in setup_command)


async def test_a_failed_setup_leaves_no_worktree_so_retry_reruns_it(database, run_dirs, repo):
    """The regression that makes the whole fix real. ensure_worktree returns
    early when the directory exists, so a worktree left behind by a failed
    setup would make `kraft item retry` -- the only door back onto a stopped
    item -- skip setup entirely and dispatch into the broken environment."""
    await wtree.make_item(database, repo)
    with pytest.raises(RuntimeError):
        await wtree.ensure(database, run_dirs, repo, repo_entry={"setup_command": "exit 1"})
    assert not (run_dirs.worktrees / "w1").exists()

    wt = await wtree.ensure(
        database, run_dirs, repo, repo_entry={"setup_command": "touch recovered.txt"}
    )
    assert (wt / "recovered.txt").exists()


async def test_ensure_worktree_pins_commit_identity_into_the_worktree(database, run_dirs, repo):
    """Kraft-cppp. A linked worktree must carry its own explicit identity, not
    rely on inheriting the repo's -- that is what left submodule commits
    authored `Kraft Agent <kraft@local>` on the real occurrence."""
    await wtree.make_item(database, repo)
    wt = await wtree.ensure(database, run_dirs, repo)
    assert git_read(wt, "config", "--local", "user.name") == "t"
    assert git_read(wt, "config", "--local", "user.email") == "t@t"


def _workspace_item(database, root, mounts):
    """Item w1 filed against `root` as a workspace target selecting `mounts`
    (member -> mount path): the frozen target is what `ensure_worktree` reads."""
    chain = v1_chain(_ONE_NODE, repo=root, target=workspace_target(mounts))
    return wtree.make_item(database, root, materialized_chain=chain.to_json())


_ONE_NODE = [
    {"id": "n", "kind": "exec", "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}]}
]


async def test_a_workspace_item_assembles_its_selected_members_each_on_the_items_branch(
    tmp_path, database, run_dirs
):
    """`workspace-tasks-have-an-assembled-checkout` and `selected-repositories-
    get-corresponding-branches`: the item's checkout is the root with each
    selected member at its frozen mount path, and every selected repository
    -- members and the root -- is on the item's branch, one row each so the
    forge nodes know what to publish and in what order."""
    root, _sub = make_repo_with_submodule(tmp_path)
    await _workspace_item(database, root, {"pkg": "repos/pkg"})

    worktree = await wtree.ensure(database, run_dirs, root)

    branch = wtree.branch(database)
    for checkout in (worktree, worktree / "repos" / "pkg"):
        assert git_read(checkout, "branch", "--show-current") == branch
    assert (worktree / "repos" / "pkg" / ".git").exists(), "the member is initialized"
    repos = database.read(lambda c: store.repos_for(c, "w1"))
    assert [(r["role"], r["path"]) for r in repos] == [
        ("submodule", str(worktree / "repos" / "pkg")),
        ("root", str(worktree)),
    ]


async def test_a_single_repository_item_initializes_no_submodule(tmp_path, database, run_dirs):
    """Membership is the target's, never `.gitmodules`': a root filed as a
    plain repository gets no member checked out and no repository rows."""
    root, _sub = make_repo_with_submodule(tmp_path)
    await wtree.make_item(
        database, root, materialized_chain=v1_chain(_ONE_NODE, repo=root).to_json()
    )

    worktree = await wtree.ensure(database, run_dirs, root)

    assert not (worktree / "repos" / "pkg" / ".git").exists()
    assert database.read(lambda c: store.repos_for(c, "w1")) == []


async def test_ensure_worktree_never_runs_a_blanket_submodule_init(
    tmp_path, monkeypatch, database, run_dirs
):
    """Global constraint: only the selected members' mount paths, never every
    submodule in .gitmodules (design §3 step 2)."""
    root, _sub = make_repo_with_submodule(tmp_path)
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(args, **kw):
        if "submodule" in args and "update" in args:
            calls.append(args)
        return real_run(args, **kw)

    monkeypatch.setattr(kraft_builtins.subprocess, "run", spy)

    await _workspace_item(database, root, {"pkg": "repos/pkg"})
    await wtree.ensure(database, run_dirs, root)
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


async def test_ensure_worktree_raises_when_git_fails(tmp_path, database, run_dirs):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    await wtree.make_item(database, not_a_repo)
    with pytest.raises(RuntimeError, match="w1"):
        await wtree.ensure(database, run_dirs, not_a_repo)


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
    launch = executor.LaunchContext(repo_entry=wtree.NO_SETUP, steering_dir=None)
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
    await wtree.make_item(database, repo)
    with pytest.raises(RuntimeError, match=unset):
        await wtree.ensure(database, run_dirs, repo)


async def test_worktree_preparation_reruns_the_setup_command_on_every_entry(
    tmp_path, database, run_dirs, repo
):
    """A rebase can land a new lockfile (Kraft-zlsuk), so preparation re-runs on
    every entry into the walk rather than only at worktree creation."""
    marker = tmp_path / "setup-runs"
    entry = {**wtree.NO_SETUP, "setup_command": f"echo run >> {marker}"}

    await wtree.make_item(database, repo)
    await kraft_builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id="w1", repo_entry=entry
    )
    before = marker.read_text().count("run")
    for n in (1, 2):
        await kraft_builtins.prepare_runtime(run_dirs.worktrees / "w1", Path(repo), entry)
        assert marker.read_text().count("run") == before + n
