"""`merge_watch` (`mr.post_merge_ci`): after this item merged, watch the
target branch's own pipeline, and when it breaks, file a follow-up bead and
pause the items standing on the broken commit. The re-entry behaviour it
shares with `ci_poll` (session reuse, pipeline pin, infra cap) is in
test_run.py."""

from __future__ import annotations

import subprocess

import pytest
from support.harness import commit_all, isolated_bd, write

from kraft import builtins as _builtins
from kraft.adapters import beads, forge

from .nodes import back_half


class _NoMrForge(forge.FakeForge):
    """A fake that behaves like a real backend once the merge request is gone.

    `FakeForge` answers the MR-based `ci_status` happily whether or not an MR
    exists, so every `merge_watch` test passed while the handler still read
    the pipeline through `gh pr view` / `glab mr view` on a just-merged branch
    -- where both real backends raise `ForgeError` and fail the terminal node.
    This double raises there, so a test can hold the branch-based read down.

    `branch_ci_status` deliberately calls `FakeForge.ci_status` unbound rather
    than `self.ci_status`: the base class implements it in terms of the method
    this class overrides, and going through `self` would make the branch read
    raise too.
    """

    async def ci_status(self, *, repo, mr, branch, pipeline_id=""):
        raise forge.ForgeError(f"no open merge request for {branch!r}")

    async def branch_ci_status(self, *, repo, branch, head_sha="", pipeline_id=""):
        return await forge.FakeForge.ci_status(
            self,
            repo=repo,
            mr=forge.MR(number=0, url="http://fake.forge/branch"),
            branch=branch,
            pipeline_id=pipeline_id,
        )


def _head(repo) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.mark.parametrize(
    "ci_states, retries",
    [
        (["success"], 0),
        # Judge finding (rounds 0/2/3): the infra kick's own re-read went
        # through the MR-based `ci_status`, which raises on a merged branch.
        # Empty failed_jobs is infra-red: kicked once, then green.
        (["failed", "success"], 1),
    ],
    ids=["green", "infra-red-retried-once"],
)
async def test_merge_watch_settles_without_reading_the_merged_away_mr(
    run_forge, repo, ci_states, retries
):
    """No call in this node may resolve a merge request: the branch's MR is
    already merged, and both real backends raise there."""
    fake = _NoMrForge(ci_states=ci_states, ci_failed_jobs=[(), ()])

    assert (await run_forge(fake, "merge_watch", repo=repo))[0] == "done"

    assert len(fake.retried) == retries


class _Watching(_NoMrForge):
    """Records the branch each post-merge pipeline read was for."""

    def __init__(self):
        super().__init__(ci_states=["success"])
        self.watched: list[str] = []

    async def branch_ci_status(self, *, repo, branch, head_sha="", pipeline_id=""):
        self.watched.append(branch)
        return await super().branch_ci_status(
            repo=repo, branch=branch, head_sha=head_sha, pipeline_id=pipeline_id
        )


async def test_merge_watch_watches_the_items_base_branch(run_forge, item_on, repo):
    """Kraft-v9gbi: the item merged into its base branch, so that branch's
    pipeline is the one its merge can have broken."""
    from kraft.templates.environment import WorkItemTarget

    bare = repo.parent / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD:release"], cwd=repo, check=True)
    target = WorkItemTarget.for_repository("target", base_branch="release")
    run_forge.item = await item_on(back_half(), repo=repo, target=target)
    fake = _Watching()

    assert (await run_forge(fake, "merge_watch", repo=repo))[0] == "done"

    assert fake.watched == ["release"]


async def test_merge_watch_waits_instead_of_crashing_when_upstream_head_is_unknown(
    run_forge, repo, monkeypatch
):
    """`upstream_head` returns `None` whenever `git rev-parse HEAD` fails or
    hits `config.git_read`'s timeout (same as the other two callers,
    `ensure_worktree` and `refresh_worktree_base`). `merge_watch` must treat
    that the same way they do -- "nothing to report yet" -- rather than hand
    `None` on to `branch_ci_status`, where `GhCli` crashes formatting
    `head_sha[:7]` and `GlabCli` silently reports the previous commit's
    pipeline as this merge's result (code-review)."""

    async def _none(repo, branch):
        return None

    monkeypatch.setattr(_builtins, "upstream_head", _none)

    result = await run_forge(forge.FakeForge(ci_states=["success"]), "merge_watch", repo=repo)

    assert result[0] == "waiting"


async def test_merge_watch_files_a_follow_up_bead_and_pauses_items_on_the_broken_sha(
    run_forge, item_on, database, repo, tmp_path, monkeypatch
):
    from kraft.worker import reattach

    signalled = []
    monkeypatch.setattr(reattach, "interrupt_group", lambda pid, **_: signalled.append(pid))
    tracker = isolated_bd(tmp_path, name="tracker")
    head_sha = _head(repo)
    run_forge.item = await item_on(back_half(), repo=repo, bead_cwd=str(tracker))
    others = {
        # rebased onto exactly the commit that just broke -- must pause.
        "w2": ("active", head_sha),
        # active but on a different base -- must not be touched.
        "w3": ("active", "some-other-sha"),
        # parked on its own pipeline (`waiting`) on the broken commit: the item
        # most likely to re-discover this break in its own fix loop, so it
        # must be warned too (gate review).
        "w4": ("waiting", head_sha),
    }
    items = {}
    for wid, (status, base_ref) in others.items():
        items[wid] = await item_on(back_half(), repo=repo, wid=wid, status=status)
        await database.write(
            lambda c, w=wid, b=base_ref: c.execute(
                "UPDATE work_items SET base_ref = ? WHERE id = ?", (b, w)
            )
        )
    # w2's tests are mid-run: the pause stops them, as a person's would, so
    # nothing more of its node (a review, a fix round) runs under "paused".
    await database.write(
        lambda c: c.execute("UPDATE work_items SET current_node_id = 'mr_checks' WHERE id = 'w2'")
    )
    await items["w2"].session("s2", "mr_checks.main.ci", running=(4242, 1.0))
    fake = forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(forge.FailedJob("build", "failed", "script_failure"),)],
    )

    assert (await run_forge(fake, "merge_watch", repo=repo))[0] == "done"

    assert {w: it.status() for w, it in items.items()} == {
        "w2": "paused",
        "w3": "active",
        "w4": "paused",
    }
    assert items["w2"].events("paused_by_broken_base")
    assert [r["status"] for r in items["w2"].sessions()] == ["paused"]
    assert signalled == [4242]
    filed = await beads.search("post-merge", cwd=str(tracker))
    assert any("post-merge pipeline broke" in b["title"] for b in filed), filed


async def test_merge_watch_does_not_pin_a_pipeline_read_for_a_different_commit(run_forge, repo):
    """Plan-review finding 2: right after a merge, the target branch's
    "latest pipeline" is usually still a previous, unrelated commit's, until
    this head's own pipeline exists. Pinning that wrong id to head_sha would
    have every later re-entry poll it forever -- it never moves, and
    `render_ci` never gets a chance to see the real one."""
    head_sha = _head(repo)
    fake = forge.FakeForge(
        ci_states=["success", "success"],
        # First read: settled green, but for a different commit entirely.
        # Second read: this head's own, matching pipeline.
        ci_shas=["deadbeefdeadbeef", head_sha],
        ci_pipeline_refs=["111", "222"],
    )

    results = [(await run_forge(fake, "merge_watch", sid, repo=repo))[0] for sid in ("s1", "s2")]

    # `render_ci`'s own sha guard keeps the wrong-sha read from a false
    # "done"; only that read is wrong-sha, and it must never be pinned.
    assert results == ["waiting", "done"]
    assert fake.pipeline_ids_requested == ["", ""], "the second read polled the wrong-sha pin"
    assert run_forge.item.row()["ci_pipeline_ref"] == f"{head_sha}:222"


@pytest.mark.parametrize(
    "no_checks, minutes, outcome, says",
    [
        (False, 90, "capped_out", "timed out"),
        # Kraft-rbjo3: a merge commit with no run at all is a branch that runs
        # no CI on a push. Within the grace it may still be registering...
        (True, 4, "waiting", "pipeline pending"),
        # ...past it, nothing is coming: pass, saying nothing was checked.
        (True, 5, "done", "no post-merge CI on "),
    ],
    ids=["a-pending-run-times-out", "no-run-within-the-grace", "no-run-past-the-grace"],
)
async def test_a_post_merge_pipeline_that_never_settles_times_out_for_a_human(
    run_forge, repo, wait_clock, no_checks, minutes, outcome, says
):
    """`external-wait-timeout-needs-human`, for this wait too. It used to give
    up after its own 40-entry cap and report `done` -- a pipeline nobody saw
    finish, read as a finished chain. Now it runs out like every wait: the
    session is `capped_out`, which the walk stops on for a person."""
    fake = forge.FakeForge(ci_states=["pending"], ci_no_checks=no_checks)

    first = await run_forge(fake, "merge_watch", "s1", repo=repo)
    wait_clock.advance(minutes * 60)
    last = await run_forge(fake, "merge_watch", "s2", repo=repo)

    assert (first[0], last[0]) == ("waiting", outcome)
    assert says in run_forge.log("s1")
    absent = run_forge.events("post_merge_ci_absent")
    assert len(absent) == (outcome == "done"), absent


async def test_merge_watch_runs_once_for_a_multi_repo_item(run_forge, item_on, database, repo):
    """`merge_watch` ignores the per-target repo -- it reads `orig_repo`'s own
    default branch. Without collapsing `targets`, a multi-repo item would poll
    that one branch once per submodule and file that many identical follow-up
    beads for a single break."""
    fake = forge.FakeForge(ci_states=["success"])
    reads: list[str] = []
    original = fake.branch_ci_status

    async def counting(**kwargs):
        reads.append(kwargs["branch"])
        return await original(**kwargs)

    fake.branch_ci_status = counting
    run_forge.item = await item_on(back_half(), repo=repo)
    for path, role, rank in (
        (repo / "a", "submodule", 1),
        (repo / "b", "submodule", 2),
        (repo, "root", 3),
    ):
        await database.write(
            lambda c, p=path, r=role, k=rank: c.execute(
                "INSERT INTO work_item_repos (work_item_id, repo_path, role, "
                "submodule_path, merge_rank, created_at, updated_at) VALUES "
                "('w1', ?, ?, ?, ?, 'now', 'now')",
                (str(p), r, p.name if r == "submodule" else None, k),
            )
        )

    assert (await run_forge(fake, "merge_watch", repo=repo))[0] == "done"

    assert len(reads) == 1, reads


class _Merged(_NoMrForge):
    """`find_mr` reports the item's merge request as merged at `merged_sha`
    (`finds` is what each call answers instead, one at a time), and every
    `head_sha` a read was asked about is kept."""

    def __init__(self, merged_sha: str | None, *, finds=(), red_sha: str | None = None, **kw):
        super().__init__(**kw)
        self.merged_sha, self.finds, self.red_sha = merged_sha, list(finds), red_sha
        self.asked: list[str] = []

    async def find_mr(self, *, repo, branch):
        if self.finds:
            found = self.finds.pop(0)
            if isinstance(found, Exception):
                raise found
            return found
        if self.merged_sha is None:
            return None
        return forge.MRRef(
            number=7, url="http://fake.forge/7", state="merged", merged_sha=self.merged_sha
        )

    async def branch_ci_status(self, *, repo, branch, head_sha="", pipeline_id=""):
        self.asked.append(head_sha)
        if head_sha == self.red_sha:
            return forge.CIStatus(
                state="failed",
                url="http://fake.forge/red",
                jobs=("build: failed",),
                sha=head_sha,
                failed_jobs=(forge.FailedJob("build", "failed", "script_failure"),),
            )
        return await super().branch_ci_status(
            repo=repo, branch=branch, head_sha=head_sha, pipeline_id=pipeline_id
        )


def _advance_base(repo, name: str) -> str:
    write(repo, name, name)
    return commit_all(repo, name)


async def test_a_skipped_root_is_still_the_repo_the_merge_commit_is_found_in(
    run_forge, item_on, database, repo
):
    """Kraft-h6wi2: with the root dropped as pointer-only the first target is a
    member, whose merge request landed on the member's own base -- its merged
    commit is not on the branch whose CI is read."""
    asked: list = []

    class _Recording(_Merged):
        async def find_mr(self, *, repo, branch):
            asked.append(repo)
            return await super().find_mr(repo=repo, branch=branch)

    fake = _Recording(None, ci_states=["success"])
    run_forge.item = await item_on(back_half(), repo=repo)
    for path, role, rank in ((repo / "a", "submodule", 1), (repo, "root", 2)):
        await database.write(
            lambda c, p=path, r=role, k=rank: c.execute(
                "INSERT INTO work_item_repos (work_item_id, repo_path, role, "
                "submodule_path, merge_rank, created_at, updated_at) VALUES "
                "('w1', ?, ?, ?, ?, 'now', 'now')",
                (str(p), r, p.name if r == "submodule" else None, k),
            )
        )

    await run_forge(fake, "merge_watch", repo=repo)

    assert asked == [repo]


async def test_merge_watch_stays_on_the_merge_commit_when_the_base_moves(
    run_forge, repo, wait_clock
):
    """Kraft-h6wi2: a busy base branch kept the wait on whatever was newest,
    each merge starting its pipeline over."""
    merged = _head(repo)
    fake = _Merged(merged, ci_states=["pending", "success"])

    first = await run_forge(fake, "merge_watch", "s1", repo=repo)
    _advance_base(repo, "later.txt")
    wait_clock.advance(60)
    second = await run_forge(fake, "merge_watch", "s2", repo=repo)

    assert (first[0], second[0]) == ("waiting", "done")
    assert fake.asked == [merged, merged]


async def test_a_later_red_commit_is_not_this_items_break(
    run_forge, repo, item_on, database, tmp_path
):
    merged = _head(repo)
    tracker = isolated_bd(tmp_path, name="tracker")
    run_forge.item = await item_on(back_half(), repo=repo, bead_cwd=str(tracker))
    tip = _advance_base(repo, "later.txt")
    other = await item_on(back_half(), repo=repo, wid="w2", status="active")
    await database.write(
        lambda c: c.execute("UPDATE work_items SET base_ref = ? WHERE id = 'w2'", (tip,))
    )
    fake = _Merged(merged, ci_states=["success"], red_sha=tip)

    assert (await run_forge(fake, "merge_watch", repo=repo))[0] == "done"

    assert other.status() == "active"
    assert not await beads.search("post-merge", cwd=str(tracker))


@pytest.mark.parametrize(
    "merged_sha", [None, ""], ids=["no-merge-request", "merged-without-a-commit"]
)
async def test_with_no_merge_commit_the_first_tip_is_kept(run_forge, repo, wait_clock, merged_sha):
    first_tip = _head(repo)
    fake = _Merged(merged_sha, ci_states=["pending"])

    await run_forge(fake, "merge_watch", "s1", repo=repo)
    _advance_base(repo, "later.txt")
    wait_clock.advance(60)
    await run_forge(fake, "merge_watch", "s2", repo=repo)

    assert fake.asked == [first_tip, first_tip]


@pytest.mark.parametrize(
    "unknown",
    [
        forge.ForgeError("gh is down"),
        forge.MRRef(number=7, url="u", state="open"),
    ],
    ids=["forge-error", "not-merged-yet"],
)
async def test_an_unresolved_merge_commit_pins_nothing(run_forge, repo, wait_clock, unknown):
    merged = _head(repo)
    fake = _Merged(merged, finds=[unknown], ci_states=["success"])

    first = await run_forge(fake, "merge_watch", "s1", repo=repo)
    _advance_base(repo, "later.txt")
    wait_clock.advance(60)
    second = await run_forge(fake, "merge_watch", "s2", repo=repo)

    assert (first[0], second[0]) == ("waiting", "done")
    assert fake.asked == [merged]


def _an_hour_ago() -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(hours=1)).isoformat()


async def test_a_superseded_run_moves_the_watch_to_the_new_tip(run_forge, repo, wait_clock):
    """The forge keeps one pending run per concurrency group, so a merge
    commit's run is cancelled for the next push before it starts. The tip
    holds this merge, so its CI answers instead."""
    merged = _head(repo)
    tip = _advance_base(repo, "later.txt")
    fake = _Merged(merged, ci_states=["pending", "success"], ci_cancelled_at=[_an_hour_ago(), ""])

    first = await run_forge(fake, "merge_watch", "s1", repo=repo)
    wait_clock.advance(60)
    second = await run_forge(fake, "merge_watch", "s2", repo=repo)

    assert (first[0], second[0]) == ("waiting", "done")
    assert fake.asked == [merged, tip]
    [moved] = run_forge.events("post_merge_ci_moved")
    assert (moved["from"], moved["to"]) == (merged, tip)
    assert "cancelled" in moved["why"]


async def test_a_cancel_on_the_tip_still_stops_for_a_person(run_forge, repo):
    fake = _Merged(_head(repo), ci_states=["pending"], ci_cancelled_at=[_an_hour_ago()])

    assert await run_forge(fake, "merge_watch", repo=repo) == ("infra_stop", "infra_stop")

    assert run_forge.events("ci_run_abandoned")
    assert not run_forge.events("post_merge_ci_moved")


async def test_a_move_restarts_the_no_ci_grace(run_forge, repo, wait_clock):
    """The new tip may have no run registered yet. Measured from the wait's
    start, that would pass it as "no CI" at once."""
    merged = _head(repo)
    tip = _advance_base(repo, "later.txt")
    fake = _Merged(merged, ci_states=["pending"], ci_cancelled_at=["", _an_hour_ago(), ""])

    await run_forge(fake, "merge_watch", "s1", repo=repo)
    wait_clock.advance(6 * 60)
    await run_forge(fake, "merge_watch", "s2", repo=repo)  # moves onto the tip
    fake.ci_no_checks = True
    third = await run_forge(fake, "merge_watch", "s3", repo=repo)

    assert third[0] == "waiting"
    assert fake.asked[-1] == tip
    assert not run_forge.events("post_merge_ci_absent")
