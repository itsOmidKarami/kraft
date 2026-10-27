"""The two git guards, against a real `git` and a real remote.

Kraft-8mi8. `assert_clean` and `_assert_pushed` are the last two things
standing between a chain and landing an empty or a stale merge request, and
every other test drives them through a stub `git` on PATH that answers with
whatever the test wrote into it — so they prove the caller reacts to a string,
never that the string is what `git` says. Here `git` is the real binary and
`origin` is a real `git init --bare` inside tmp_path. Only the forge CLI is
stubbed, and nothing here touches a network.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from support.harness import commit_all, git, make_repo, make_repo_with_submodule

from kraft import builtins as kraft_builtins
from kraft.adapters import forge
from kraft.adapters.forge import git as forge_git
from kraft.worker import sandbox

from . import outputs
from .outputs import GLAB_MR_VIEW

BRANCH = "kraft/abc"


def _repo_with_origin(tmp_path: Path) -> Path:
    """A real repo on BRANCH, really pushed to a real bare origin in tmp_path."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", "-b", "main", str(origin))
    repo = make_repo(tmp_path)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "main")
    git(repo, "checkout", "-q", "-b", BRANCH)
    (repo / "work.txt").write_text("the work\n")
    commit_all(repo, "the work")
    git(repo, "push", "-q", "-u", "origin", BRANCH)
    # What `forge.push` records, without its event loop: some callers are async.
    git(repo, "update-ref", f"{forge_git.PUSHED_REFS}/{BRANCH}", "HEAD")
    return repo


def _someone_else_pushes(tmp_path: Path) -> str:
    """A commit on BRANCH pushed from another clone, as a person would; its sha."""
    other_clone = tmp_path / "other-clone"
    git(tmp_path, "clone", "-q", "-b", BRANCH, str(tmp_path / "origin.git"), str(other_clone))
    (other_clone / "elsewhere.txt").write_text("someone else's commit\n")
    head = commit_all(other_clone, "a concurrent writer")
    git(other_clone, "push", "-q", "origin", BRANCH)
    return head


@pytest.fixture
def glab(cli):
    """A stub `glab` on PATH that answers a merge request view. `git` stays the
    real binary: the stub directory holds no `git`."""
    cli.stub("glab", GLAB_MR_VIEW)
    return cli


def test_open_mr_refuses_a_dirty_worktree_against_real_git(tmp_path, glab):
    """`git status --porcelain` from the real binary, over a worktree with a
    file the agent never `git add`ed — work item 5163dd1b's failure."""
    repo = _repo_with_origin(tmp_path)
    (repo / "forgotten.py").write_text("never added\n")

    with pytest.raises(forge.ForgeError, match="forgotten.py"):
        asyncio.run(
            forge.GlabCli().open_mr(repo=repo, branch=BRANCH, base="main", title="t", body="b")
        )

    assert glab.argv("glab") == [], "glab ran over an uncommitted worktree"


@pytest.mark.parametrize(
    "name, cls, view",
    [("glab", forge.GlabCli, outputs.GLAB_MR_VIEW), ("gh", forge.GhCli, outputs.GH_PR_VIEW)],
    ids=["glab", "gh"],
)
def test_open_mr_in_place_ignores_a_dirty_checkout(tmp_path, cli, name, cls, view):
    """The in-place counterpart: the person's own uncommitted edits in their
    checkout are no reason to refuse; only the branch and its commits reach
    the merge request."""
    cli.stub(name, view)
    repo = _repo_with_origin(tmp_path)
    (repo / "their_edit.py").write_text("half-finished\n")

    asyncio.run(
        cls().open_mr(repo=repo, branch=BRANCH, base="main", title="t", body="b", check_clean=False)
    )

    assert cli.argv(name)[1] == "create"


def test_open_mr_ignores_gitignored_paths_against_real_git(tmp_path, glab):
    """The other half: `.pytest_cache/` and `.engineering/sessions/` must not
    fail a node. Real git does that exclusion, so only real git can prove it."""
    repo = _repo_with_origin(tmp_path)
    (repo / ".gitignore").write_text("junk/\n")
    commit_all(repo, "ignore junk")
    git(repo, "push", "-q", "origin", BRANCH)
    (repo / "junk").mkdir()
    (repo / "junk" / "cache.txt").write_text("noise\n")

    mr = asyncio.run(
        forge.GlabCli().open_mr(repo=repo, branch=BRANCH, base="main", title="t", body="b")
    )

    assert mr.number == 54
    assert glab.argv("glab")[:2] == ["mr", "create"]


def test_open_mr_accepts_a_worktree_whose_attachment_was_copied_in(tmp_path, glab):
    """Kraft-8iw6's symptom against the real binary: the document Kraft copied
    in is committed by Kraft's own primitive, so `assert_clean` passes and glab
    is reached instead of the node failing with "1 uncommitted path(s)"."""
    repo = _repo_with_origin(tmp_path)
    doc = repo / ".engineering" / "specs" / "s.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# the attached spec\n")

    kraft_builtins._commit_paths(
        repo, [".engineering/specs/s.md"], "chore: attach spec for w1", "main"
    )

    mr = asyncio.run(
        forge.GlabCli().open_mr(repo=repo, branch=BRANCH, base="main", title="t", body="b")
    )

    assert mr.number == 54
    assert glab.argv("glab")[:2] == ["mr", "create"]


def test_merge_refuses_an_unpushed_head_against_real_git(tmp_path, glab):
    """A commit made in the worktree after the last push — Kraft-bxj8's exact
    shape — and `git rev-list --count origin/BRANCH..HEAD` from real git."""
    repo = _repo_with_origin(tmp_path)
    (repo / "late.txt").write_text("committed after the push\n")
    commit_all(repo, "late")

    with pytest.raises(forge.ForgeError, match="ahead of origin/kraft/abc by 1"):
        asyncio.run(
            forge.GlabCli().merge(repo=repo, branch=BRANCH, mr=forge.MR(0, ""), head="a" * 40)
        )

    assert glab.argv("glab") == [], "glab merged a head the forge has never seen"


@pytest.mark.parametrize("elsewhere", [False, True], ids=["on-the-branch", "checkout-elsewhere"])
def test_merge_accepts_a_pushed_head_against_real_git(tmp_path, glab, elsewhere):
    """The ordinary path: really pushed, so the guard lets the CLI run. It
    reads the branch, not HEAD: an in-place item's checkout may be on another
    branch with commits of its own."""
    repo = _repo_with_origin(tmp_path)
    if elsewhere:
        git(repo, "checkout", "-q", "main")
        git(repo, "commit", "-q", "--allow-empty", "-m", "the person's own")

    asyncio.run(forge.GlabCli().merge(repo=repo, branch=BRANCH, mr=forge.MR(0, ""), head="a" * 40))

    # glab first asks the project whether it merges through a train (it does
    # not: the stub's answer has no such field); the merge itself runs once.
    assert [c for c in glab.calls("glab") if c.startswith("mr merge")] == [
        f"mr merge {BRANCH} --yes --sha {'a' * 40}"
    ]
    assert not any("merge_trains" in c for c in glab.calls("glab"))


@pytest.mark.parametrize(
    "pushed", ["recorded", "pushed-before-the-record", "record-behind-a-crash"]
)
def test_push_publishes_a_rebased_branch_against_real_git(tmp_path, pushed):
    """Kraft-z6i8. A rebase moves the branch off of what origin last saw --
    here, `main` gaining a commit and the branch rebasing onto it -- and a
    plain `push -u` would die non-fast-forward. `forge.push`'s
    `--force-with-lease` must still publish it. A fix Kraft pushed before
    the rebase is Kraft's own work, not someone else's, so it is no reason
    to refuse. A branch pushed before Kraft kept that record is judged by
    the remote-tracking ref instead, as every branch was. Kraft-mre20: a
    crash between the push and the record leaves the record one push behind,
    and the fix, once the local branch's tip, is still Kraft's own."""
    repo = _repo_with_origin(tmp_path)
    (repo / "fix.txt").write_text("a verify fix\n")
    commit_all(repo, "a verify fix")
    if pushed == "recorded":
        asyncio.run(forge.push(repo, BRANCH))
    else:
        if pushed == "pushed-before-the-record":
            git(repo, "update-ref", "-d", f"{forge_git.PUSHED_REFS}/{BRANCH}")
        git(repo, "push", "-q", "origin", BRANCH)
    origin = tmp_path / "origin.git"
    main_clone = tmp_path / "main-clone"
    git(tmp_path, "clone", "-q", str(origin), str(main_clone))
    (main_clone / "elsewhere.txt").write_text("moved on without you\n")
    commit_all(main_clone, "main moved on")
    git(main_clone, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin", "main")
    git(repo, "rebase", "-q", "origin/main")

    asyncio.run(forge.push(repo, BRANCH))

    remote_head = git(origin, "rev-parse", BRANCH)
    assert remote_head == git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("hooks", "linked", "runs"),
    [
        pytest.param("gitdir", False, True, id="installed-in-the-gitdir-runs"),
        pytest.param("in-tree", False, False, id="in-the-working-tree-does-not"),
        pytest.param(
            "in-tree-symlink", False, False, id="symlinked-from-the-working-tree-does-not"
        ),
        pytest.param("dotdot", False, False, id="dotdot-through-a-symlink-does-not"),
        pytest.param("gitdir", True, True, id="linked-worktree-gitdir-runs"),
        pytest.param("in-tree", True, False, id="linked-worktree-in-tree-does-not"),
    ],
)
def test_push_runs_an_installed_pre_push_under_harden_host_git_env(tmp_path, hooks, linked, runs):
    """Kraft-rki. `harden_host_git_env` pins `core.hooksPath=/dev/null` on
    the process env so a worker's own commits can't fire a planted hook --
    but by push time the commit is already made, and a real pre-push hook
    (git-lfs's) uploading the objects a push's pointers reference must still
    run. A pinned `core.hooksPath` that survives into `forge.push` would
    silently drop those uploads.

    A `core.hooksPath` in the working tree is the worker's to edit, so that
    one stays pinned: a push that ran it would run the worker's code as the
    person, outside any sandbox. A symlink there pointing out of the tree is
    still the worker's file, and so is one under `a/../../x`, which reads as
    outside the tree but lands inside it once the worker makes `a` a symlink
    two levels down. A linked worktree, the layout Kraft pushes from, reads
    the shared config and prints its gitdir paths absolute."""
    repo = _repo_with_origin(tmp_path)
    if linked:
        git(repo, "checkout", "-q", "main")
        git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), BRANCH)
        # `git config` from the worktree writes the shared config, as an operator's would.
        repo = tmp_path / "wt"
    if hooks in ("in-tree", "in-tree-symlink"):
        target = tmp_path / "outside-hooks" if hooks == "in-tree-symlink" else repo / ".hooks"
        target.mkdir()
        if hooks == "in-tree-symlink":
            (repo / ".hooks").symlink_to(target)
        git(repo, "config", "core.hooksPath", ".hooks")
    if hooks == "dotdot":
        (repo / "b" / "c").mkdir(parents=True)
        (repo / "x").mkdir()
        (repo / "a").symlink_to(repo / "b" / "c")
        git(repo, "config", "core.hooksPath", "a/../../x")
    # Unhardened: a suite run from inside a Kraft worker inherits the pinned
    # `core.hooksPath=/dev/null`, and `--git-path hooks` would answer that.
    hooks_dir = git(repo, "rev-parse", "--git-path", "hooks", env=sandbox.unhardened_git_env())
    marker = tmp_path / "pre-push-ran"
    pre_push = Path(repo) / hooks_dir / "pre-push"
    pre_push.write_text(f"#!/bin/sh\ntouch {marker}\n")
    pre_push.chmod(0o755)
    # A push with nothing new to send never invokes pre-push at all.
    (repo / "more-work.txt").write_text("more work\n")
    commit_all(repo, "more work")

    sandbox.harden_host_git_env(os.environ)
    try:
        asyncio.run(forge.push(repo, BRANCH))
    finally:
        os.environ.pop("GIT_CONFIG_COUNT", None)

    assert marker.exists() == runs


@pytest.mark.parametrize(
    ("base", "branch", "pushes"),
    [
        pytest.param(None, "untouched", True, id="none-anywhere-pushes"),
        pytest.param("main", "untouched", True, id="the-bases-own-pushes"),
        pytest.param("release", "untouched", True, id="a-non-default-bases-own-pushes"),
        pytest.param("main", "committed", False, id="a-committed-change-is-refused"),
        pytest.param(None, "on-disk", False, id="an-uncommitted-one-is-refused"),
        pytest.param(None, "staged", False, id="a-staged-one-is-refused"),
        pytest.param("main", "deleted", False, id="deleting-the-bases-is-refused"),
        pytest.param(None, "head-only", False, id="committed-then-gone-from-disk-is-refused"),
        pytest.param(None, "pushed-by-a-person", True, id="once-a-person-pushes-it-kraft-does"),
    ],
)
def test_push_refuses_a_branch_that_moves_lfsconfig(tmp_path, base, branch, pushes):
    """git-lfs's pre-push sends a push's objects, and asks about locks, at
    the server `.lfsconfig` names. The worker writes that file, and git-lfs
    reads it from disk, then the index, then HEAD, so a branch whose own
    work changed it would have the host talk to a URL the branch chose,
    outside the sandbox. Kraft stops the item for a person instead. What is
    already on a remote branch is not the item's work: the base's own file,
    whichever branch the base is, and one a person reviewed and pushed."""
    repo = _repo_with_origin(tmp_path)
    lfsconfig = repo / ".lfsconfig"
    if base is not None:
        git(repo, "checkout", "-q", "-B", base, "main")
        lfsconfig.write_text("[lfs]\n\turl = https://lfs.example/repo\n")
        commit_all(repo, "the base's lfs server")
        git(repo, "push", "-q", "origin", base)
        git(repo, "checkout", "-q", BRANCH)
        git(repo, "rebase", "-q", base)
    if branch in ("committed", "on-disk", "staged", "head-only", "pushed-by-a-person"):
        lfsconfig.write_text("[lfs]\n\turl = http://127.0.0.1:1/elsewhere\n")
    if branch == "deleted":
        lfsconfig.unlink()
    if branch in ("committed", "deleted", "head-only", "pushed-by-a-person"):
        commit_all(repo, "the branch's lfs server")
    if branch == "staged":
        git(repo, "add", ".lfsconfig")
    if branch in ("head-only", "staged"):
        # git-lfs falls back to the index, then HEAD, when the file is gone.
        lfsconfig.unlink()
    if branch == "pushed-by-a-person":
        git(repo, "push", "-q", "origin", BRANCH)
        (repo / "more-work.txt").write_text("more work\n")
        commit_all(repo, "more work")

    if pushes:
        asyncio.run(forge.push(repo, BRANCH))
        assert git(tmp_path / "origin.git", "rev-parse", BRANCH) == git(repo, "rev-parse", "HEAD")
    else:
        with pytest.raises(forge_git.UnsafeWorktree, match=r"changes \.lfsconfig"):
            asyncio.run(forge.push(repo, BRANCH))


@pytest.mark.parametrize("case", ["unseen", "fetched-by-the-checkout", "record-behind-a-crash"])
def test_push_refuses_to_overwrite_someone_elses_commits(tmp_path, case):
    """Kraft-z6i8's other half: a person's commits on the MR branch are
    never overwritten by a rebased push. `fetched` is Kraft-m7ppj: a plain
    `git fetch` in the connected checkout moves the remote-tracking ref the
    worktree shares onto those commits, and a lease on that ref let the push
    clobber them. Kraft-mre20: a record left behind by a crash excuses only
    tips the local branch had, never a person's commit on top of them."""
    repo = _repo_with_origin(tmp_path)
    if case == "record-behind-a-crash":
        (repo / "fix.txt").write_text("pushed, never recorded\n")
        commit_all(repo, "a fix")
        git(repo, "push", "-q", "origin", BRANCH)
    theirs = _someone_else_pushes(tmp_path)
    if case == "fetched-by-the-checkout":
        git(repo, "fetch", "-q", "origin")
    (repo / "work.txt").write_text("rewritten locally\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--amend", "-m", "rewritten")

    with pytest.raises(forge.ForeignCommits, match="Kraft did not push") as refused:
        asyncio.run(forge.push(repo, BRANCH))

    assert refused.value.remote == theirs, "the typed refusal names the forge's tip"
    assert git(tmp_path / "origin.git", "rev-parse", BRANCH) == theirs


def test_push_refuses_to_recreate_a_branch_origin_deleted(tmp_path):
    """Kraft-7itv: origin dropping a branch Kraft pushed means it was merged
    or closed; a push must not quietly bring it back."""
    repo = _repo_with_origin(tmp_path)
    git(tmp_path / "origin.git", "branch", "-q", "-D", BRANCH)

    with pytest.raises(forge.ForgeError, match="origin no longer has"):
        asyncio.run(forge.push(repo, BRANCH))

    assert git(tmp_path / "origin.git", "branch", "--list", BRANCH) == ""


def test_push_reads_the_tip_where_it_pushes(tmp_path):
    """A `pushurl` sends pushes somewhere other than where `origin` fetches
    from; the tip judged is the one the push would replace."""
    repo = _repo_with_origin(tmp_path)
    mirror = tmp_path / "mirror.git"
    git(tmp_path, "init", "--bare", "-q", str(mirror))
    git(repo, "remote", "set-url", "origin", str(mirror))
    git(repo, "remote", "set-url", "--push", "origin", str(tmp_path / "origin.git"))
    (repo / "fix.txt").write_text("a fix\n")
    commit_all(repo, "a fix")

    asyncio.run(forge.push(repo, BRANCH))

    head = git(repo, "rev-parse", "HEAD")
    assert git(tmp_path / "origin.git", "rev-parse", BRANCH) == head


def test_push_publishes_once_someone_elses_commits_are_pulled_in(tmp_path):
    """The way out the refusal names: pull their commits into the worktree
    and retry. Their tip is now in the branch, so the push fast-forwards."""
    repo = _repo_with_origin(tmp_path)
    theirs = _someone_else_pushes(tmp_path)
    git(repo, "pull", "-q", "--rebase", "origin", BRANCH)
    (repo / "fix.txt").write_text("a review fix\n")
    commit_all(repo, "a review fix")

    asyncio.run(forge.push(repo, BRANCH))

    head = git(repo, "rev-parse", "HEAD")
    assert git(tmp_path / "origin.git", "rev-parse", BRANCH) == head
    assert git(repo, "merge-base", "--is-ancestor", theirs, head) == ""


def test_commit_stragglers_commits_everything_the_agent_left(tmp_path):
    """Kraft-7fip. A worker that edits a tracked file, adds a new one and exits
    without committing leaves work that `assert_clean` refuses two nodes later
    and a worktree prune destroys. Kraft owns the worktree, so it commits."""
    repo = _repo_with_origin(tmp_path)
    (repo / "work.txt").write_text("edited, never committed\n")
    (repo / "forgotten.py").write_text("never added\n")

    committed = asyncio.run(
        forge.commit_stragglers(repo, branch=BRANCH, base="main", message="wip: implementation")
    )

    assert committed is True
    assert git(repo, "status", "--porcelain") == ""
    assert "wip: implementation" in git(repo, "log", "-1", "--format=%s")
    assert set(git(repo, "show", "--name-only", "--format=", "HEAD").split()) == {
        "work.txt",
        "forgotten.py",
    }


def test_commit_stragglers_leaves_a_clean_worktree_alone(tmp_path):
    """No empty commit, and no lie in the log: a worker that committed its own
    work must not gain a second, empty commit on top of it."""
    repo = _repo_with_origin(tmp_path)
    before = git(repo, "rev-parse", "HEAD")

    committed = asyncio.run(
        forge.commit_stragglers(repo, branch=BRANCH, base="main", message="wip: implementation")
    )

    assert committed is False
    assert git(repo, "rev-parse", "HEAD") == before


def test_kraft_session_notes_are_not_the_agents_work_product(tmp_path):
    """Kraft-z8gj, widened. The agent is told to write its summary to
    `.engineering/sessions/`, and spec/plan/chain_review/review_brief to their
    own `.engineering/` subdirectories — and Kraft's own repo gitignores the
    whole tree (a repo Kraft was pointed at five minutes ago does not).
    Sweeping any of it up puts Kraft's bookkeeping in that repo's first merge
    request; refusing to open one over it is the same bug wearing the other
    hat. None of `.engineering/` is work product anymore: it is ingested into
    the index at gate approval (`Indexer.ingest_gate_artifact`) instead of
    being committed."""
    repo = _repo_with_origin(tmp_path)
    before = git(repo, "rev-parse", "HEAD")
    (repo / ".engineering" / "sessions").mkdir(parents=True)
    (repo / ".engineering" / "sessions" / "abc.md").write_text("what I did today\n")

    assert (
        asyncio.run(
            forge.commit_stragglers(repo, branch=BRANCH, base="main", message="wip: implementation")
        )
        is False
    )
    assert git(repo, "rev-parse", "HEAD") == before
    # ... and open_mr is not blocked by it either
    asyncio.run(forge.assert_clean(repo, "main"))

    (repo / ".engineering" / "specs").mkdir()
    (repo / ".engineering" / "specs" / "abc.md").write_text("the design\n")

    assert (
        asyncio.run(
            forge.commit_stragglers(repo, branch=BRANCH, base="main", message="wip: implementation")
        )
        is False
    )
    assert git(repo, "rev-parse", "HEAD") == before
    asyncio.run(forge.assert_clean(repo, "main"))
    assert git(repo, "ls-files", ".engineering").split() == []


def test_a_repos_own_preexisting_engineering_doc_still_commits(tmp_path):
    """The exclusion is per path, not per directory. A repo that already
    tracked something under `.engineering/` before Kraft ever touched this
    worktree -- unrelated to Kraft, possibly even colliding with one of
    Kraft's own artifact subdirectories, like `.engineering/specs/` -- is that
    repo's own content. An edit to it is real work product and must reach the
    merge request like any other tracked file, not vanish because it happens
    to share a path prefix with Kraft's bookkeeping."""
    repo = _repo_with_origin(tmp_path)
    doc = repo / ".engineering" / "specs" / "preexisting.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("not Kraft's, already tracked\n")
    commit_all(repo, "repo's own doc")
    git(repo, "push", "-q", "origin", BRANCH)

    doc.write_text("the agent's edit to it\n")
    (repo / ".engineering" / "sessions").mkdir(parents=True)
    (repo / ".engineering" / "sessions" / "abc.md").write_text("what I did today\n")

    assert (
        asyncio.run(
            forge.commit_stragglers(repo, branch=BRANCH, base="main", message="wip: implementation")
        )
        is True
    )
    assert "preexisting.md" in git(repo, "show", "--name-only", "--format=", "HEAD")
    # Kraft's own session note still stays out. A brand-new directory
    # collapses to one line in `git status --porcelain` rather than one line
    # per file inside it -- still the correct exclusion, just not per-path.
    assert "sessions/abc.md" not in git(repo, "show", "--name-only", "--format=", "HEAD")
    assert git(repo, "status", "--porcelain") == "?? .engineering/sessions/"


def test_commit_stragglers_ignores_a_root_main_gitignored_after_the_branch_forked(tmp_path):
    """Kraft-vu26. `ensure_worktree` checks out whatever `.gitignore` `main`
    had at intake, and nothing refreshes it short of a rebase most nodes never
    trigger. If `main` starts ignoring a root afterward -- `docs/superpowers/`
    the day this bug was filed -- this worktree's own checked-out `.gitignore`
    still does not know it, so a plain `git add -A` would stage a new file
    under it same as any other work. `main_ignore_args` reads `main`'s current
    `.gitignore` off the remote-tracking ref instead, so this stays out
    exactly like a root the branch always ignored -- caught by
    `test_kraft_session_notes_are_not_the_agents_work_product` above."""
    repo = _repo_with_origin(tmp_path)
    main_clone = tmp_path / "main-clone"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(main_clone))
    (main_clone / ".gitignore").write_text("docs/superpowers/\n")
    commit_all(main_clone, "widen gitignore")
    git(main_clone, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin", "main")
    assert not (repo / ".gitignore").exists(), "repo's own checkout must stay unaware of the rule"
    doc = repo / "docs" / "superpowers" / "specs" / "s.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# the attached spec\n")

    committed = asyncio.run(
        forge.commit_stragglers(repo, branch=BRANCH, base="main", message="wip: implementation")
    )

    assert committed is False
    asyncio.run(forge.assert_clean(repo, "main"))
    # Collapses to the first new directory level, `docs/` -- `docs` itself
    # did not exist on the branch before, same collapse `git status` does for
    # any new untracked directory.
    assert git(repo, "status", "--porcelain") == "?? docs/"


def test_commit_stragglers_ignores_gitignored_paths(tmp_path):
    """Same exclusion `assert_clean` relies on: a `.pytest_cache/` left behind
    is not work, and committing it would put junk in the merge request."""
    repo = _repo_with_origin(tmp_path)
    (repo / ".gitignore").write_text("junk/\n")
    before = commit_all(repo, "ignore junk")
    (repo / "junk").mkdir()
    (repo / "junk" / "cache.txt").write_text("noise\n")

    committed = asyncio.run(
        forge.commit_stragglers(repo, branch=BRANCH, base="main", message="wip: implementation")
    )

    assert committed is False
    assert git(repo, "rev-parse", "HEAD") == before


@pytest.mark.parametrize(
    ("made", "markers"),
    [
        (".venv", ("pyvenv.cfg", "bin/python")),
        ("web/node_modules", (".package-lock.json", "left-pad/index.js")),
    ],
    ids=["virtualenv", "node_modules"],
)
def test_commit_stragglers_leaves_an_unignored_install_out(tmp_path, made, markers):
    """A setup's `.venv/` or `node_modules/` in a repo that never ignored it
    is an install, not work: swept, it put 1,023 virtualenv files into one
    merge request. The agent's own file beside it still commits, and the
    install does not stop the merge request either."""
    repo = _repo_with_origin(tmp_path)
    (repo / "web").mkdir()
    (repo / "web" / "app.js").write_text("tracked\n")
    commit_all(repo, "a web dir")
    for marker in markers:
        (repo / made / marker).parent.mkdir(parents=True, exist_ok=True)
        (repo / made / marker).write_text("installed\n")
    (repo / "forgotten.py").write_text("never added\n")

    committed = asyncio.run(
        forge.commit_stragglers(repo, branch=BRANCH, base="main", message="wip: implementation")
    )

    assert committed is True
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["forgotten.py"]
    asyncio.run(forge.assert_clean(repo, "main"))


def test_an_unignored_install_stays_out_when_git_lists_every_untracked_file(tmp_path):
    """With `status.showUntrackedFiles=all` git lists `.venv`'s files one by
    one, never `.venv/` whole, so no entry read as an install and the sweep
    committed the virtualenv again."""
    repo = _repo_with_origin(tmp_path)
    git(repo, "config", "status.showUntrackedFiles", "all")
    for d in ("lib", "bin"):
        (repo / ".venv" / d).mkdir(parents=True)
    (repo / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (repo / ".venv" / "bin" / "python").symlink_to("/nonexistent/python3")
    (repo / ".venv" / "lib" / "site.py").write_text("installed\n")
    (repo / "forgotten.py").write_text("never added\n")

    asyncio.run(
        forge.commit_stragglers(repo, branch=BRANCH, base="main", message="wip: implementation")
    )

    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["forgotten.py"]


def test_commit_stragglers_leaves_out_only_the_lockfile_the_setup_wrote(tmp_path):
    """An entry connected before the probe stopped proposing `uv sync` for a
    pyproject with no lockfile still runs it, and every work item committed
    the `uv.lock` it wrote. That one stays out; a `uv.lock` the agent made,
    or an edit to one the repo tracks, is work."""
    repo = _repo_with_origin(tmp_path)
    for d in ("web", "api"):
        (repo / d).mkdir()
        (repo / d / "pyproject.toml").write_text("[project]\n")
    (repo / "web" / "uv.lock").write_text("version = 1\n")
    commit_all(repo, "two projects, web's locked")
    before = asyncio.run(forge.lockfile_digests(repo))
    (repo / "uv.lock").write_text("by the setup\n")
    asyncio.run(forge.record_setup_writes(repo, before))
    (repo / "api" / "uv.lock").write_text("by the agent\n")
    (repo / "web" / "uv.lock").write_text("version = 2\n")

    asyncio.run(
        forge.commit_stragglers(repo, branch=BRANCH, base="main", message="wip: implementation")
    )

    swept = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(swept) == ["api/uv.lock", "web/uv.lock"]
    asyncio.run(forge.assert_clean(repo, "main"))


def test_assert_clean_sees_a_submodule_with_ignore_all(tmp_path):
    """`submodule.<path>.ignore = all` is a legitimate thing for a human to
    set on a six-submodule workspace -- it must not blind Kraft's own guard
    to a submodule commit that never left the worktree (the real failure on
    work item 9d0ab38ff3c9439b90506df0f6966660)."""
    root, _ = make_repo_with_submodule(tmp_path, submodule_path="pkg")
    git(root, "config", "submodule.pkg.ignore", "all")
    # A new commit inside the submodule, root pointer left untouched -- what
    # "do not bump the workspace submodule pointer" produces.
    (root / "pkg" / "f.txt").write_text("2\n")
    commit_all(root / "pkg", "metric change")

    with pytest.raises(forge.ForgeError, match="pkg"):
        asyncio.run(forge.assert_clean(root, "main"))


def test_commits_on_a_branch_without_origin_main_is_empty_not_an_error(tmp_path):
    """A description is not worth failing a node over."""
    repo = make_repo(tmp_path)

    assert asyncio.run(forge.commits_on(repo, "kraft/nope", "main")) == ()


def test_a_hanging_cli_call_is_killed_and_raises(tmp_path):
    """A wait's deadline never reached the subprocess inside it: a stalled gh
    blocked forever and a 300s cap ran for 16 minutes."""
    with pytest.raises(forge.ForgeError, match="timed out"):
        asyncio.run(forge_git.run_git(tmp_path, ["sleep", "30"], timeout=0.5))


@pytest.mark.parametrize(
    "site",
    [
        pytest.param(
            lambda repo, db: forge.commit_stragglers(repo, branch=BRANCH, base="main", message="w"),
            id="commit_stragglers",
        ),
        pytest.param(
            lambda repo, db: forge.run._point_at_merged_members(None, db, repo, BRANCH, "w1"),
            id="pointer_bump",
        ),
    ],
)
async def test_a_host_commit_never_lands_on_a_planted_head(tmp_path, database, site):
    """Kraft-xngty: HEAD sits in a gitdir the worker writes. Pointed at the
    operator's `main`, the straggler sweep's commit, or the root's pointer
    bump (and the push of `HEAD` after it), moved `main`."""
    repo = _repo_with_origin(tmp_path)
    main = git(repo, "rev-parse", "main")
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / "left.txt").write_text("left behind\n")
    git(repo, "add", "left.txt")

    try:
        await site(repo, database)
        stopped = ""
    except forge.ForgeError as exc:
        stopped = str(exc)

    assert git(repo, "rev-parse", "main") == main
    assert f"is not on {BRANCH}" in stopped
