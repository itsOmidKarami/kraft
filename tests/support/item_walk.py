"""`walk_once` and `approve`: one `run_once` pass over a `support.harness.Item`
and a gate approved under it, for the restart suites (`test_base_change`,
`test_merge_rejection`, `test_default_chain`); `merge_drops` and
`commits_a_fix` script a merge train/queue drop and the repair that fixes it."""

from __future__ import annotations

from kraft import events, executor, store
from kraft import policy as _policy
from kraft.vocab import ForgeEvent
from support.harness import commit_all, entry_of, git, write

#: The repo declaring it needs no preparation: without one no worktree is cut.
NO_SETUP = entry_of({"setup_command": ""})


def walk_once(it, policy=None):
    """`executor.run_once` from the item's own cursor, under `policy` (three
    attempts over an hour for any loop it does not name)."""
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        policy=policy or _policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )


async def approve(it, gate: str) -> None:
    """`gate` requested and approved, as a person's decision leaves it."""
    await it.database.write(lambda c: store.request_gate(c, it.id, gate, gate))
    await it.database.write(lambda c: store.approve_gate(c, it.id, gate))


def merge_drops(
    it, script, cause="code", *, rebased=True, times=1, folder=None, moves_base=False, repo=None
):
    """`merge` answers `merge_rejected` `times` times, then `done`, recording
    each drop as the forge's merge handler does, at the head it left in
    `folder` -- and, `moves_base`, the `base_ref` its forced rebase persists.
    `repo` is the dropped merge request's repository, the item's own unless
    given. No `cause` records nothing."""
    script.plan["merge"] = ["merge_rejected"] * times + ["done"]
    dropped = [0]

    async def effect(_row):
        if dropped[0] == times or cause is None:
            return
        dropped[0] += 1
        if moves_base:
            await it.database.write(lambda c: store.set_base_ref(c, it.id, f"{dropped[0]:040d}"))
        head = git(folder or it.worktree, "rev-parse", "HEAD")
        await it.database.write(
            lambda c: events.append(
                c,
                it.id,
                ForgeEvent.MERGE_QUEUE_DROPPED,
                {
                    "node_id": "merge",
                    "repo": str(repo or it.repo),
                    "kind": "train",
                    "cause": cause,
                    "reason": "pipeline failed on the train",
                    "url": "https://forge.example/p/1",
                    "failed_jobs": ["unit", "lint"],
                    "head_sha": head,
                    "rebased": rebased,
                },
            )
        )

    script.effects["merge"] = effect


def commits_a_fix(it, folder=None):
    """A repair's effect: commit a file in `folder` (the item's worktree)."""

    async def effect(_row):
        write(folder or it.worktree, "fix.txt", "fixed\n")
        commit_all(folder or it.worktree, "fix the train failure")

    return effect
