"""`merge_watch` (`mr.post_merge_ci`): after this item merged, watch the
target branch's own pipeline, and when it breaks, file a follow-up bead and
pause the items standing on the broken commit. The re-entry behaviour it
shares with `ci_poll` (session reuse, pipeline pin, infra cap) is in
test_run.py."""

from __future__ import annotations

import subprocess

import pytest
from support.harness import isolated_bd

from kraft import builtins as _builtins
from kraft.adapters import beads, forge

from .conftest import back_half


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

    async def _none(repo):
        return None

    monkeypatch.setattr(_builtins, "upstream_head", _none)

    result = await run_forge(forge.FakeForge(ci_states=["success"]), "merge_watch", repo=repo)

    assert result[0] == "waiting"


async def test_merge_watch_files_a_follow_up_bead_and_pauses_items_on_the_broken_sha(
    run_forge, item_on, database, repo, tmp_path
):
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
    assert run_forge.item.row()["ci_pipeline_ref"] == f"{head_sha}:222"


async def test_merge_watch_gives_up_watching_a_pipeline_that_never_settles(run_forge, repo):
    """Plan-review finding 3, second half: `ci_wait.py`'s shared cap
    (1800s/60 attempts) has no notion of which handler is behind a waiting
    node, so left alone a merely-slow (never infra, never red) target-branch
    pipeline would eventually turn into `needs_human` after this item's own
    work is already merged, and -- since `post_merge_watch` is the chain's
    terminal node -- its tracking bead would never close either.
    `_POST_MERGE_WAIT_CAP` (40) bounds this node's own patience first, and
    reports "done" rather than paging anyone."""
    fake = forge.FakeForge(ci_states=["pending"])

    results = [(await run_forge(fake, "merge_watch", f"s{i}", repo=repo))[0] for i in range(41)]

    assert results == ["waiting"] * 40 + ["done"]


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
