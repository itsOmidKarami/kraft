"""The `merge` node against a merge train or merge queue that drops the merge
request after Kraft asked for it: seen, classified, and handed back as
`merge_rejected` with findings and a `merge_queue_dropped` event."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support.harness import commit_all, git, write

from kraft.adapters import forge
from kraft.adapters.forge import ci

BRANCH = "kraft/w1"


URL = "http://fake.forge/pipelines/9"


def _dropped(cause: str, reason: str, *jobs: forge.FailedJob) -> forge.QueueRead:
    failure = forge.CIStatus(
        state="failed",
        url=URL,
        jobs=tuple(f"job {j.name}: failed ({j.failure_reason})" for j in jobs),
        failed_jobs=jobs,
    )
    return forge.QueueRead(
        kind="train",
        state="dropped",
        cause=cause,
        reason=reason,
        failure=failure if jobs else None,
        url=URL,
    )


CODE = _dropped("code", "pipeline failed", forge.FailedJob("unit", "failed", "script_failure"))
INFRA = _dropped(
    "code", "pipeline failed", forge.FailedJob("build", "failed", "runner_system_failure")
)
CONFLICT = _dropped("conflict", "merge conflict with main")
NOT_ADDED = _dropped("not_added", "aborted automatic add to merge train")
BY_HAND = _dropped("other", "removed from the merge train by a person")


class _Unanswered(forge.FakeForge):
    """A merge call that neither lands nor queues anything the forge reports:
    the merge request is open, Kraft asked, and `merge_queued` is false."""

    async def merge(self, *, repo, branch="", mr):
        self.merge_calls.append(1)


def _on_branch(repo: Path, *, upstream_moved: bool = False) -> str:
    """`repo` on the item's branch, one commit ahead of `main`; with
    `upstream_moved`, `main` has moved on since (a clean rebase away).
    Returns the branch head before anything rebases it."""
    git(repo, "checkout", "-qb", BRANCH)
    write(repo, "feature.txt", "the item's change\n")
    head = commit_all(repo, "the item's change")
    if upstream_moved:
        git(repo, "checkout", "-q", "main")
        write(repo, "upstream.txt", "landed on main meanwhile\n")
        commit_all(repo, "upstream moved")
        git(repo, "checkout", "-q", BRANCH)
    return head


async def _open(fake: forge.FakeForge, repo: Path) -> forge.FakeForge:
    await fake.open_mr(repo=repo, branch=BRANCH, base="main", title="t", body="b")
    return fake


def _findings(run_forge) -> list[dict] | None:
    """The findings the last finished merge session handed back, if any."""
    path = Path(run_forge.sessions()[-1]["result_path"])
    return json.loads(path.read_text())["findings"] if path.exists() else None


def _train(*drops: forge.QueueRead, **kw) -> forge.FakeForge:
    return forge.FakeForge(queue_kind="train", queue_drops=list(drops), ci_states=["success"], **kw)


@pytest.mark.parametrize(
    "make, moved, expected, causes, asks, pushes, phrase, messages",
    [
        # No train or queue: exactly today's wait, byte for byte, no event.
        pytest.param(
            lambda: _Unanswered(ci_states=["success"]),
            *(False, ["waiting", "waiting"], [], 1, 1),
            "!1 is still open; waiting for the merge to land\n",
            None,
            id="no-queue-unchanged",
        ),
        # A train still reading the merge request: pending, and the log says
        # what the train said.
        pytest.param(
            lambda: _Unanswered(queue_kind="train", ci_states=["success"]),
            *(False, ["waiting", "waiting"], [], 1, 1),
            "waiting for the merge to land (merge train: absent)\n",
            None,
            id="train-state-logged",
        ),
        # The train's pipeline went red on the code: rebased onto the target
        # and handed back for repair, one finding per failed job.
        pytest.param(
            lambda: _train(CODE),
            *(False, ["waiting", "merge_rejected"], ["code"], 1, 2),
            "dropped from the merge train: pipeline failed\n",
            ["job unit: failed (script_failure)"],
            id="code-red",
        ),
        # A conflict with the target that a rebase takes away cleanly; with no
        # failed job, the forge's reason is the finding.
        pytest.param(
            lambda: _train(CONFLICT),
            *(True, ["waiting", "merge_rejected"], ["conflict"], 1, 2),
            "re-pushed",
            ["merge conflict with main"],
            id="conflict-clean-rebase",
        ),
        # The train's runners broke: the same head is queued again, never
        # rebased or pushed, no rewind.
        pytest.param(
            lambda: _train(INFRA),
            *(False, ["waiting", "waiting"], ["infra"], 2, 1),
            "queue the same head again",
            None,
            id="infra-requeues",
        ),
        # ... until `ci_infra:<node>` is spent, which stops for a person.
        pytest.param(
            lambda: _train(*[INFRA] * (ci._INFRA_RETRY_CAP + 1)),
            False,
            ["waiting"] * (ci._INFRA_RETRY_CAP + 1) + ["infra_stop"],
            ["infra"] * (ci._INFRA_RETRY_CAP + 1),
            *(ci._INFRA_RETRY_CAP + 1, 1),
            "queue the same head again",
            None,
            id="infra-cap-spent",
        ),
        # Taken off by a person, or for a reason Kraft cannot read: never
        # re-queued, never rebased.
        pytest.param(
            lambda: _train(BY_HAND),
            *(False, ["waiting", "failed"], ["other"], 1, 1),
            "Kraft does not re-queue",
            None,
            id="removed-by-person",
        ),
        # It never reached the train: the merge request's own pipeline failed
        # after Kraft asked. Rebased, then repaired, as code red is.
        pytest.param(
            lambda: _train(NOT_ADDED),
            *(True, ["waiting", "merge_rejected"], ["not_added"], 1, 2),
            "re-pushed",
            ["aborted automatic add to merge train"],
            id="never-added",
        ),
        # Queued and dropped between two observations, so Kraft never read it
        # as queued (`merge_queued` was never true): the wait's own record
        # that it asked is what makes the drop a drop.
        pytest.param(
            lambda: _train(CODE, merge_delay=3),
            *(False, ["waiting", "merge_rejected"], ["code"], 1, 2),
            "dropped from the merge train",
            ["job unit: failed (script_failure)"],
            id="drop-before-first-queued-read",
        ),
    ],
)
async def test_merge_handles_a_dropped_queue(
    run_forge, repo, make, moved, expected, causes, asks, pushes, phrase, messages
):
    """One merge node, entered once per scheduler observation: the first asks
    for the merge, every later one reads whether it landed. `asks` is how
    often the forge was asked to merge, `pushes` how often the branch was
    pushed (once before the merge, once more after a rebase)."""
    before = _on_branch(repo, upstream_moved=moved)
    fake = await _open(make(), repo)
    drops = list(fake.queue_drops)

    results = [
        (await run_forge(fake, "merge", f"m{i}", repo=repo))[0] for i in range(len(expected))
    ]

    assert results == expected
    assert fake.merge_calls == [1] * asks
    assert fake.pushed == [BRANCH] * pushes
    assert fake.merged == ([1] if causes == ["infra"] else []), "landed only once re-queued"
    log = run_forge.log("m0")
    assert phrase in log, log
    findings = _findings(run_forge)
    assert ([f["message"] for f in findings] if findings else None) == messages
    assert all(f["source_plugin"] == "on.ci.poll" for f in findings or [])
    dropped = run_forge.events("merge_queue_dropped")
    assert [e["cause"] for e in dropped] == causes
    head = git(repo, "rev-parse", BRANCH)
    assert [(e["head_sha"], e["url"]) for e in dropped] == [(head, URL)] * len(dropped)
    assert [e["failed_jobs"] for e in dropped] == [
        [j.name for j in (d.failure.failed_jobs if d.failure else ())] for d in drops
    ][: len(dropped)]
    if moved:
        assert head != before and dropped[-1]["rebased"] is True, "rebased onto the moved target"
    if "infra_stop" in expected:
        assert run_forge.events("ci_infra_exhausted")


async def test_a_drop_is_seen_after_a_restart_of_the_wait_reader(run_forge, repo):
    """A daemon restart rebuilds the forge object and forgets everything in
    memory. The wait's last observation, in the database, still says Kraft
    asked, so the drop is read rather than the merge asked for again."""
    _on_branch(repo)
    asked = await _open(_Unanswered(queue_kind="train", ci_states=["success"]), repo)
    assert (await run_forge(asked, "merge", "m0", repo=repo))[0] == "waiting"

    restarted = await _open(
        forge.FakeForge(queue_kind="train", queue_drops=[CODE], ci_states=["success"]), repo
    )
    # The forge's own record of the drop, which outlives Kraft's restart.
    await restarted.merge(repo=repo, branch=BRANCH, mr=forge.MR(number=0, url=""))
    restarted.merge_calls.clear()

    assert (await run_forge(restarted, "merge", "m1", repo=repo))[0] == "merge_rejected"
    assert restarted.merge_calls == [], "a restart must not ask for the merge again"
