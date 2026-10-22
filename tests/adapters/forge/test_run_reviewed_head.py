"""`mr.merge` pushes only the head the final review approved
(`merge-refuses-a-head-final-review-did-not-see`): the pin `kraft.reviewed_head`
records at the final gate, read right before the forge is asked to merge.
Walked through the executor against a `FakeForge`; git is real."""

from __future__ import annotations

import dataclasses

import pytest
from support.addons import with_origin
from support.harness import commit_all, entry_of, git, write

from kraft import builtins as _builtins
from kraft import events, executor, reviewed_head, store
from kraft.adapters import forge
from kraft.executor.context import LANDED_UNREVIEWED, UNREVIEWED_HEAD

from .nodes import back_half, forge_node
from .test_run_merge_queue import _on_branch, _open

ON_A_FORGE = entry_of({"setup_command": "", "forge": "github"})
NO_SETUP = entry_of({"setup_command": ""})

#: `merge`'s `on_merge_rejected`, rewinding to `open_mr`, before the final gate.
REJECTED = {
    "restart_from": "open_mr",
    "repair": {"tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]},
}


#: A node rebasing onto the base itself, before the merge.
REBASE = {
    "id": "rebase",
    "kind": "exec",
    "tasks": [{"id": "rebase", "kind": "builtin", "ref": "kraft.mr_rebase"}],
}


def _chain(rejects: bool, rebases: bool = False) -> list[dict]:
    """open_mr -> final (the chain's final gate) -> [rebase ->] merge."""
    merge = forge_node("merge", "mr.merge")
    if rejects:
        merge["on_merge_rejected"] = REJECTED
    return [
        forge_node("open_mr", "mr.open_draft"),
        {"id": "final", "kind": "gate", "chain_finalized": True},
        *([REBASE] if rebases else []),
        merge,
    ]


class _ConflictsUntilRebased(forge.FakeForge):
    """Green, but conflicting with main until `clears_at` pushes have been
    made: the push after the merge's own forced rebase clears it, as a real
    forge's next read of the rebased head would."""

    clears_at: int | None = None

    async def push(self, *, repo, branch):
        await super().push(repo=repo, branch=branch)
        if self.clears_at is not None and len(self.pushed) >= self.clears_at:
            self.mergeable = True


@pytest.fixture
def walk(database, run_dirs, monkeypatch):
    """`await walk(fake, item)`: `executor.run` from the item's cursor, with
    `forge.run.resolve` answering `fake`."""

    async def go(fake, it):
        monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
        return await executor.run(
            database, run_dirs, work_item_id=it.id, launch=executor.LaunchContext(ON_A_FORGE)
        )

    return go


@pytest.mark.parametrize(
    ("after", "rejects", "pinned", "status", "stands_at"),
    [
        ("nothing", False, True, "completed", None),
        ("a-commit", False, True, "needs_human", "final"),
        ("a-commit", True, True, "needs_human", "open_mr"),
        ("a-commit", False, False, "completed", None),
        ("a-clean-rebase", False, True, "completed", None),
        ("a-pre-merge-rebase", False, True, "completed", None),
    ],
    ids=[
        "the-approved-head-merges",
        "a-commit-after-the-approval-goes-back-to-the-final-gate",
        "a-merge-node-holds-it-at-restart-from",
        "approved-before-the-pin-is-not-checked",
        "kraft-s-own-clean-rebase-is-repinned-and-merges",
        "kraft-s-own-rebase-before-the-merge-is-repinned",
    ],
)
async def test_the_merge_pushes_only_the_head_the_final_review_approved(
    walk, item_on, database, run_dirs, repo, after, rejects, pinned, status, stands_at
):
    """Approving the final gate pins the branch tip. A commit after it -- an
    agent's, a person's, any route no earlier guard caught -- is never pushed
    for merging nor merged: the item is held where the final review comes round
    again, `on_merge_rejected.restart_from` or the gate itself, with the gate
    reopened, and stops naming the change. Kraft's own conflict-free rebase of
    the approved head merges, repinned. An item approved before the pin
    existed is not checked."""
    it = await item_on(_chain(rejects, rebases=after == "a-pre-merge-rebase"))
    await _builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id=it.id, repo_entry=NO_SETUP
    )
    write(it.worktree, "feature.txt", "the item's change\n")
    reviewed = commit_all(it.worktree, "the item's change")
    fake = _ConflictsUntilRebased(ci_states=["success"])
    assert await walk(fake, it) == "awaiting_gate"
    tips = reviewed_head.pin_for(database, it.row(), "final", it.worktree) if pinned else None
    await database.write(lambda c: store.request_gate(c, it.id, "final", "final"))
    await database.write(lambda c: store.approve_gate(c, it.id, "final", tips=tips))
    pushed = len(fake.pushed)
    if after == "a-commit":
        write(it.worktree, "unseen.txt", "nobody reviewed this\n")
        unseen = commit_all(it.worktree, "after the approval")
    elif after == "a-clean-rebase":
        fake.mergeable, fake.merge_detail = False, "conflict"
        fake.clears_at = pushed + 2
    if after.endswith("rebase"):
        write(repo, "moved.txt", "landed on main meanwhile\n")
        commit_all(repo, "upstream moved")

    assert await walk(fake, it) == status

    refused = [e["payload"] for e in it.events("merge_refused_unreviewed")]
    reopened = [e["payload"] for e in it.events("gate_reopened")]
    if stands_at is None:
        assert fake.merged == [1]
        assert refused == [] and reopened == []
        repinned = [e["payload"] for e in it.events("reviewed_head_repinned")]
        assert [p["cause"] for p in repinned] == (["rebase"] if after.endswith("rebase") else [])
        # The forge is told which head it may merge: the branch's tip, pinned or not.
        tip = reviewed_head.branch_tip(it.worktree, store.branch_for(it.row()))
        assert fake.merge_heads == [tip]
        return
    assert fake.merged == [] and len(fake.pushed) == pushed, "the unseen head went nowhere"
    assert refused == [
        {
            "node_id": "merge",
            "repo": str(it.worktree),
            "reviewed": reviewed,
            "current": unseen,
            "source": "local",
        }
    ]
    assert reopened == [{"gate": "final", "reason": "unreviewed_change"}]
    assert (it.status(), it.row()["current_node_id"]) == ("needs_human", stands_at)
    assert it.row()["stop_kind"] == "failed"
    back = "back through final" + (" from open_mr" if rejects else "")
    assert it.events("work_item_needs_human")[-1]["payload"]["reason"] == (
        f"node merge did not merge: the branch is at {unseen[:12]}, not at {reviewed[:12]}, "
        f"the head final approved; a retry takes it {back}"
    )


def _forge_gains_a_commit(it, *, rewrites: bool = False) -> str:
    """Give the item's checkout an `origin` holding the branch, then a commit
    only the forge has: a bot's autofix pushed to the merge request. `rewrites`
    force-pushes it over the reviewed commit instead, a head that is no
    fast-forward of the branch. Returns the forge's new tip."""
    branch = store.branch_for(it.row())
    bare = with_origin(it.worktree)
    git(it.worktree, "push", "-q", "origin", branch)
    scratch = it.worktree.parent / "forge-side"
    git(it.worktree.parent, "clone", "-q", bare.as_uri(), str(scratch))
    git(scratch, "checkout", "-q", branch)
    if rewrites:
        git(scratch, "reset", "-q", "--hard", "HEAD~1")
    write(scratch, "autofix.txt", "a bot's suggestion, committed on the forge\n")
    commit_all(scratch, "autofix")
    git(scratch, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}")
    return git(scratch, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("refused_by", "rewrites", "setup", "fast_forwards", "said"),
    [
        ("merge", False, None, True, "it is now on the item's branch for you to review"),
        ("push", False, None, True, "it is now on the item's branch for you to review"),
        ("merge", True, None, False, "pull them into the item's worktree"),
        ("merge", False, "head-off-the-branch", False, "pull them into the item's worktree"),
        ("merge", False, "in-place", False, "pull them into the item's worktree"),
    ],
    ids=[
        "the-forge-refuses-the-moved-head-so-it-is-brought-home-and-held",
        "a-forge-commit-before-the-push-holds-instead-of-failing",
        "a-force-pushed-forge-head-is-held-by-hand",
        "head-off-the-branch-is-not-fast-forwarded",
        "an-in-place-item-is-held-by-hand",
    ],
)
async def test_a_commit_that_reaches_the_forge_after_the_final_review_goes_back_for_review(
    walk,
    item_on,
    database,
    run_dirs,
    repo,
    monkeypatch,
    refused_by,
    rewrites,
    setup,
    fast_forwards,
    said,
):
    """A commit pushed to the merge request after the approval -- before the
    merge's push (`ForeignCommits`) or between that push and the merge (the
    forge's own refusal of the head Kraft named, `HeadMoved`) -- is never
    merged nor overwritten. The item is held where the final review comes
    round again, naming the commit, and the commit is brought onto the item's
    branch for that review when it is a fast-forward, the checkout is on the
    branch and it is not the person's own (in place); otherwise the stop tells
    the person to pull or drop it by hand and the branch is left alone."""
    it = await item_on(_chain(False))
    await _builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id=it.id, repo_entry=NO_SETUP
    )
    write(it.worktree, "feature.txt", "the item's change\n")
    reviewed = commit_all(it.worktree, "the item's change")
    fake = forge.FakeForge(ci_states=["success"])
    assert await walk(fake, it) == "awaiting_gate"
    tips = reviewed_head.pin_for(database, it.row(), "final", it.worktree)
    await database.write(lambda c: store.request_gate(c, it.id, "final", "final"))
    await database.write(lambda c: store.approve_gate(c, it.id, "final", tips=tips))
    forged = _forge_gains_a_commit(it, rewrites=rewrites)
    if refused_by == "merge":
        fake.forge_head = forged
    else:
        fake.push_refused = forged
    if setup == "head-off-the-branch":
        git(it.worktree, "checkout", "-q", "--detach")
    if setup == "in-place":
        monkeypatch.setattr(forge.run.inplace, "is_in_place", lambda row: True)
    pushed = list(fake.pushed)

    assert await walk(fake, it) == "needs_human"

    branch = store.branch_for(it.row())
    # A push refused for the forge's commit pushed nothing; a refused merge came after a push.
    assert fake.merged == [] and fake.pushed == pushed + ([branch] if refused_by == "merge" else [])
    assert [e["payload"] for e in it.events("merge_refused_unreviewed")] == [
        {
            "node_id": "merge",
            "repo": str(it.worktree),
            "reviewed": reviewed,
            "current": forged,
            "source": "forge",
            "fast_forwarded": fast_forwards,
        }
    ]
    assert reviewed_head.branch_tip(it.worktree, branch) == (forged if fast_forwards else reviewed)
    if setup == "head-off-the-branch":
        assert git(it.worktree, "rev-parse", "HEAD") == reviewed, "a detached HEAD was not moved"
    assert [e["payload"] for e in it.events("gate_reopened")] == [
        {"gate": "final", "reason": "unreviewed_change"}
    ]
    assert (it.status(), it.row()["current_node_id"], it.row()["stop_kind"]) == (
        "needs_human",
        "final",
        "failed",
    )
    assert said in it.events("work_item_needs_human")[-1]["payload"]["reason"]
    pin = database.read(lambda c: reviewed_head.pinned(c, it.id))
    assert list(pin.values()) == [reviewed], "the pin does not move: the commit is unreviewed"


class _ReportsNoHead(forge.FakeForge):
    """A forge that does not say the head of a merge request, as GitLab does
    for one it has merged (the head it names then may be a rebase's)."""

    async def find_mr(self, *, repo, branch):
        found = await super().find_mr(repo=repo, branch=branch)
        return dataclasses.replace(found, head_sha="") if found else None


@pytest.mark.parametrize(
    ("fake", "asked", "verdicts", "cancelled", "landed"),
    [
        (
            lambda forged: forge.FakeForge(merge_delay=3, head_after_merge=forged),
            True,
            ["waiting", UNREVIEWED_HEAD],
            [1],
            None,
        ),
        (
            lambda forged: forge.FakeForge(merge_delay=1, head_after_merge=forged),
            True,
            ["waiting", LANDED_UNREVIEWED, LANDED_UNREVIEWED],
            [],
            1,
        ),
        (
            lambda forged: _ReportsNoHead(merge_delay=1, head_after_merge=forged),
            True,
            ["waiting", "done"],
            [],
            None,
        ),
        (
            lambda forged: forge.FakeForge(forge_head=forged),
            False,
            ["done"],
            [],
            None,
        ),
    ],
    ids=[
        "an-open-merge-whose-forge-head-moved-is-cancelled-and-held",
        "a-merge-that-landed-a-moved-head-stops-and-a-retry-stops-again",
        "a-merged-mr-with-no-head-reported-is-done",
        "a-merge-kraft-never-asked-for-is-not-checked",
    ],
)
async def test_a_head_that_moves_on_the_forge_after_the_merge_was_asked_is_caught(
    run_forge, item_on, repo, fake, asked, verdicts, cancelled, landed
):
    """The forge enforces the head named in the merge call, but it may merge
    later (auto-merge, a queue) and a commit can reach the branch meanwhile.
    The next observation of a merge Kraft asked for reads the forge's head
    against the approved one: still open, the merge is cancelled and the item
    held for review; already merged, nothing can be undone, so the item stops
    for a person (`merge_landed_unreviewed`) and a plain retry stops again. A
    forge that names no head for a merged request (GitLab's rebase), and a
    merge a person made by hand, are not checked."""
    reviewed = _on_branch(repo)
    run_forge.item = await item_on(back_half(), repo=repo)
    pin = {"gate": "final", "by": "human", "tips": {str(repo): reviewed}}
    await run_forge.database.write(lambda c: events.append(c, "w1", "gate_approved", pin))
    forged = "f" * 40
    fake = await _open(fake(forged), repo)
    if not asked:
        fake.merged.append(1)

    got = [(await run_forge(fake, "merge", f"m{i}", repo=repo))[0] for i in range(len(verdicts))]

    assert got == verdicts
    assert fake.cancelled == cancelled
    recorded = run_forge.events("merge_landed_unreviewed")
    assert recorded == (
        [
            {
                "node_id": "mr_checks",
                "repo": str(repo),
                "reviewed": reviewed,
                "merged_head": forged,
                "number": landed,
            }
        ]
        if landed
        else []
    ), "once, however often it is retried"
    refused = run_forge.events("merge_refused_unreviewed")
    assert [(r["source"], r["current"]) for r in refused] == (
        [("forge", forged)] if cancelled else []
    )


async def test_a_merge_that_landed_an_unreviewed_head_stops_the_walk_for_a_person(
    walk, item_on, database, run_dirs, repo
):
    """Nothing is held or rewound for code that is already on the base branch:
    no reopened gate, no fix loop, no escalation -- a `needs_human` stop naming
    both heads and the two doors that end it."""
    it = await item_on(_chain(False))
    await _builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id=it.id, repo_entry=NO_SETUP
    )
    write(it.worktree, "feature.txt", "the item's change\n")
    reviewed = commit_all(it.worktree, "the item's change")
    forged = "f" * 40
    fake = forge.FakeForge(ci_states=["success"], merge_delay=1, head_after_merge=forged)
    assert await walk(fake, it) == "awaiting_gate"
    tips = reviewed_head.pin_for(database, it.row(), "final", it.worktree)
    await database.write(lambda c: store.request_gate(c, it.id, "final", "final"))
    await database.write(lambda c: store.approve_gate(c, it.id, "final", tips=tips))
    statuses = []
    for _ in range(3):
        # As the wait's poller re-enters the walk at its due time.
        await database.write(lambda c: store.mark_reentered(c, it.id))
        statuses.append(await walk(fake, it))
        if statuses[-1] != "waiting":
            break

    assert statuses[-1] == "needs_human"
    assert fake.merged == [1]
    assert [e["payload"]["merged_head"] for e in it.events("merge_landed_unreviewed")] == [forged]
    assert it.events("gate_reopened") == [] and it.events("merge_refused_unreviewed") == []
    assert (it.status(), it.row()["current_node_id"], it.row()["stop_kind"]) == (
        "needs_human",
        "merge",
        "failed",
    )
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert f"merged {forged[:12]}, not {reviewed[:12]}" in reason
    assert "kraft item complete" in reason
