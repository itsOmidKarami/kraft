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
import subprocess
from pathlib import Path

import pytest
from support.harness import make_repo, make_repo_with_submodule

from kraft import builtins as kraft_builtins
from kraft.adapters import forge
from kraft.worker import sandbox

from .outputs import GLAB_MR_VIEW

BRANCH = "kraft/abc"


def _git(repo: Path, *args: str) -> str:
    """Runs with `sandbox.unhardened_git_env()`, not the inherited process
    env: a test session started under Kraft is itself a child of a process
    that already called `harden_host_git_env` (Kraft-rki), and this helper
    sets up the real repos and hook paths these tests assert against."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=sandbox.unhardened_git_env(),
    ).stdout


def _repo_with_origin(tmp_path: Path) -> Path:
    """A real repo on BRANCH, really pushed to a real bare origin in tmp_path."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    repo = make_repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "checkout", "-q", "-b", BRANCH)
    (repo / "work.txt").write_text("the work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the work")
    _git(repo, "push", "-q", "-u", "origin", BRANCH)
    return repo


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


def test_open_mr_ignores_gitignored_paths_against_real_git(tmp_path, glab):
    """The other half: `.pytest_cache/` and `.engineering/sessions/` must not
    fail a node. Real git does that exclusion, so only real git can prove it."""
    repo = _repo_with_origin(tmp_path)
    (repo / ".gitignore").write_text("junk/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore junk")
    _git(repo, "push", "-q", "origin", BRANCH)
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
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "late")

    with pytest.raises(forge.ForgeError, match="ahead of origin/kraft/abc by 1"):
        asyncio.run(forge.GlabCli().merge(repo=repo, branch=BRANCH, mr=forge.MR(0, "")))

    assert glab.argv("glab") == [], "glab merged a head the forge has never seen"


def test_merge_accepts_a_pushed_head_against_real_git(tmp_path, glab):
    """The ordinary path: really pushed, so the guard lets the CLI run."""
    repo = _repo_with_origin(tmp_path)

    asyncio.run(forge.GlabCli().merge(repo=repo, branch=BRANCH, mr=forge.MR(0, "")))

    assert glab.argv("glab")[:2] == ["mr", "merge"]


def test_push_publishes_a_rebased_branch_against_real_git(tmp_path, monkeypatch):
    """Kraft-z6i8. A rebase moves the branch off of what origin last saw --
    here, `main` gaining a commit and the branch rebasing onto it -- and a
    plain `push -u` would die non-fast-forward. `forge.push`'s
    `--force-with-lease` must still publish it."""
    repo = _repo_with_origin(tmp_path)
    origin = tmp_path / "origin.git"
    main_clone = tmp_path / "main-clone"
    _git(tmp_path, "clone", "-q", str(origin), str(main_clone))
    _git(main_clone, "config", "user.email", "t@t")
    _git(main_clone, "config", "user.name", "t")
    (main_clone / "elsewhere.txt").write_text("moved on without you\n")
    _git(main_clone, "add", "-A")
    _git(main_clone, "commit", "-q", "-m", "main moved on")
    _git(main_clone, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin", "main")
    _git(repo, "rebase", "-q", "origin/main")

    asyncio.run(forge.push(repo, BRANCH))

    remote_head = _git(origin, "rev-parse", BRANCH).strip()
    assert remote_head == _git(repo, "rev-parse", "HEAD").strip()


def test_push_still_runs_pre_push_under_harden_host_git_env(tmp_path, monkeypatch):
    """Kraft-rki. `harden_host_git_env` pins `core.hooksPath=/dev/null` on
    the process env so a worker's own commits can't fire a planted hook --
    but by push time the commit is already made, and a real pre-push hook
    (git-lfs's) uploading the objects a push's pointers reference must still
    run. A pinned `core.hooksPath` that survives into `forge.push` would
    silently drop those uploads."""
    repo = _repo_with_origin(tmp_path)
    hooks_dir = _git(repo, "rev-parse", "--git-path", "hooks").strip()
    marker = tmp_path / "pre-push-ran"
    pre_push = Path(repo) / hooks_dir / "pre-push"
    pre_push.write_text(f"#!/bin/sh\ntouch {marker}\n")
    pre_push.chmod(0o755)
    # A push with nothing new to send never invokes pre-push at all.
    (repo / "more-work.txt").write_text("more work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "more work")

    sandbox.harden_host_git_env(os.environ)
    try:
        asyncio.run(forge.push(repo, BRANCH))
    finally:
        os.environ.pop("GIT_CONFIG_COUNT", None)

    assert marker.exists()


def test_push_refuses_when_the_remote_moved_under_the_lease(tmp_path):
    """The other half of Kraft-z6i8's fix: a genuine concurrent writer --
    someone else pushing to the same branch between this worktree's last
    observation and its own push -- must still be rejected, not silently
    clobbered."""
    repo = _repo_with_origin(tmp_path)
    origin = tmp_path / "origin.git"
    other_clone = tmp_path / "other-clone"
    _git(tmp_path, "clone", "-q", "-b", BRANCH, str(origin), str(other_clone))
    _git(other_clone, "config", "user.email", "t@t")
    _git(other_clone, "config", "user.name", "t")
    (other_clone / "elsewhere.txt").write_text("someone else's commit\n")
    _git(other_clone, "add", "-A")
    _git(other_clone, "commit", "-q", "-m", "a concurrent writer")
    _git(other_clone, "push", "-q", "origin", BRANCH)
    # This worktree never saw that push -- its own remote-tracking ref is
    # still the stale sha from `_repo_with_origin`'s own push.
    (repo / "work.txt").write_text("rewritten locally\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "-m", "rewritten")

    with pytest.raises(forge.ForgeError, match="stale info|rejected"):
        asyncio.run(forge.push(repo, BRANCH))

    assert _git(origin, "rev-parse", BRANCH).strip() != _git(repo, "rev-parse", "HEAD").strip()


def test_commit_stragglers_commits_everything_the_agent_left(tmp_path):
    """Kraft-7fip. A worker that edits a tracked file, adds a new one and exits
    without committing leaves work that `assert_clean` refuses two nodes later
    and a worktree prune destroys. Kraft owns the worktree, so it commits."""
    repo = _repo_with_origin(tmp_path)
    (repo / "work.txt").write_text("edited, never committed\n")
    (repo / "forgotten.py").write_text("never added\n")

    committed = asyncio.run(
        forge.commit_stragglers(repo, base="main", message="wip: implementation")
    )

    assert committed is True
    assert _git(repo, "status", "--porcelain") == ""
    assert "wip: implementation" in _git(repo, "log", "-1", "--format=%s")
    assert set(_git(repo, "show", "--name-only", "--format=", "HEAD").split()) == {
        "work.txt",
        "forgotten.py",
    }


def test_commit_stragglers_leaves_a_clean_worktree_alone(tmp_path):
    """No empty commit, and no lie in the log: a worker that committed its own
    work must not gain a second, empty commit on top of it."""
    repo = _repo_with_origin(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")

    committed = asyncio.run(
        forge.commit_stragglers(repo, base="main", message="wip: implementation")
    )

    assert committed is False
    assert _git(repo, "rev-parse", "HEAD") == before


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
    before = _git(repo, "rev-parse", "HEAD")
    (repo / ".engineering" / "sessions").mkdir(parents=True)
    (repo / ".engineering" / "sessions" / "abc.md").write_text("what I did today\n")

    assert (
        asyncio.run(forge.commit_stragglers(repo, base="main", message="wip: implementation"))
        is False
    )
    assert _git(repo, "rev-parse", "HEAD") == before
    # ... and open_mr is not blocked by it either
    asyncio.run(forge.assert_clean(repo, "main"))

    (repo / ".engineering" / "specs").mkdir()
    (repo / ".engineering" / "specs" / "abc.md").write_text("the design\n")

    assert (
        asyncio.run(forge.commit_stragglers(repo, base="main", message="wip: implementation"))
        is False
    )
    assert _git(repo, "rev-parse", "HEAD") == before
    asyncio.run(forge.assert_clean(repo, "main"))
    assert _git(repo, "ls-files", ".engineering").split() == []


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
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "repo's own doc")
    _git(repo, "push", "-q", "origin", BRANCH)

    doc.write_text("the agent's edit to it\n")
    (repo / ".engineering" / "sessions").mkdir(parents=True)
    (repo / ".engineering" / "sessions" / "abc.md").write_text("what I did today\n")

    assert (
        asyncio.run(forge.commit_stragglers(repo, base="main", message="wip: implementation"))
        is True
    )
    assert "preexisting.md" in _git(repo, "show", "--name-only", "--format=", "HEAD")
    # Kraft's own session note still stays out. A brand-new directory
    # collapses to one line in `git status --porcelain` rather than one line
    # per file inside it -- still the correct exclusion, just not per-path.
    assert "sessions/abc.md" not in _git(repo, "show", "--name-only", "--format=", "HEAD")
    assert _git(repo, "status", "--porcelain").strip() == "?? .engineering/sessions/"


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
    _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(main_clone))
    # A plain clone inherits no identity -- CI's container has no global
    # `user.name`/`user.email` at all, unlike `make_repo`, which sets both on
    # the repo it creates directly.
    _git(main_clone, "config", "user.email", "t@t")
    _git(main_clone, "config", "user.name", "t")
    (main_clone / ".gitignore").write_text("docs/superpowers/\n")
    _git(main_clone, "add", "-A")
    _git(main_clone, "commit", "-q", "-m", "widen gitignore")
    _git(main_clone, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin", "main")
    assert not (repo / ".gitignore").exists(), "repo's own checkout must stay unaware of the rule"
    doc = repo / "docs" / "superpowers" / "specs" / "s.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# the attached spec\n")

    committed = asyncio.run(
        forge.commit_stragglers(repo, base="main", message="wip: implementation")
    )

    assert committed is False
    asyncio.run(forge.assert_clean(repo, "main"))
    # Collapses to the first new directory level, `docs/` -- `docs` itself
    # did not exist on the branch before, same collapse `git status` does for
    # any new untracked directory.
    assert _git(repo, "status", "--porcelain").strip() == "?? docs/"


def test_commit_stragglers_ignores_gitignored_paths(tmp_path):
    """Same exclusion `assert_clean` relies on: a `.pytest_cache/` left behind
    is not work, and committing it would put junk in the merge request."""
    repo = _repo_with_origin(tmp_path)
    (repo / ".gitignore").write_text("junk/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore junk")
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "junk").mkdir()
    (repo / "junk" / "cache.txt").write_text("noise\n")

    committed = asyncio.run(
        forge.commit_stragglers(repo, base="main", message="wip: implementation")
    )

    assert committed is False
    assert _git(repo, "rev-parse", "HEAD") == before


def test_assert_clean_sees_a_submodule_with_ignore_all(tmp_path):
    """`submodule.<path>.ignore = all` is a legitimate thing for a human to
    set on a six-submodule workspace -- it must not blind Kraft's own guard
    to a submodule commit that never left the worktree (the real failure on
    work item 9d0ab38ff3c9439b90506df0f6966660)."""
    root, _ = make_repo_with_submodule(tmp_path, submodule_path="pkg")
    _git(root, "config", "submodule.pkg.ignore", "all")
    # The submodule checkout's gitdir lives under root/.git/modules and has no
    # identity of its own; a runner with no global git config (CI) needs one.
    _git(root / "pkg", "config", "user.email", "t@t")
    _git(root / "pkg", "config", "user.name", "t")
    # A new commit inside the submodule, root pointer left untouched -- what
    # "do not bump the workspace submodule pointer" produces.
    (root / "pkg" / "f.txt").write_text("2\n")
    _git(root / "pkg", "add", "-A")
    _git(root / "pkg", "commit", "-q", "-m", "metric change")

    with pytest.raises(forge.ForgeError, match="pkg"):
        asyncio.run(forge.assert_clean(root, "main"))


def test_commits_on_a_branch_without_origin_main_is_empty_not_an_error(tmp_path):
    """A description is not worth failing a node over."""
    repo = make_repo(tmp_path)

    assert asyncio.run(forge.commits_on(repo, "kraft/nope", "main")) == ()


def test_a_hanging_cli_call_is_killed_and_raises(tmp_path):
    """A wait's deadline never reached the subprocess inside it: a stalled gh
    blocked forever and a 300s cap ran for 16 minutes."""
    from kraft.adapters.forge import git as forge_git

    with pytest.raises(forge.ForgeError, match="timed out"):
        asyncio.run(forge_git.run_git(tmp_path, ["sleep", "30"], timeout=0.5))
