"""`dispatch.measure_node` and what a measuring pass reads back: its verdict,
its step cursor, a pending `needs_context` question, a regressed finding, and
the unresolved findings a retry is seeded with."""

import asyncio
import json

import pytest

from kraft import events
from kraft.executor import context, dispatch
from kraft.executor.context import BASE_MOVED
from kraft.findings import Finding


def _agent(task_id, **fields):
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


def _builtin(task_id):
    return {"id": task_id, "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}


def _steps(*groups):
    """One node whose steps are `groups`, each a list of subprocess task ids."""
    return {
        "id": "n",
        "kind": "exec",
        "steps": [
            {
                "id": f"s{i}",
                "tasks": [{"id": t, "kind": "subprocess", "command": "true"} for t in group],
            }
            for i, group in enumerate(groups)
        ],
    }


@pytest.fixture
def fake_dispatch(monkeypatch):
    """`dispatch_node` stood in for: `answer(task_id)` is each task's status (or
    a coroutine). Records the task ids in dispatch order."""

    class Fake:
        calls: list = []
        answer = staticmethod(lambda task_id: "done")

    async def fake(db_, run_dirs_, task, *a, **kw):
        Fake.calls.append(task.task.id)
        result = Fake.answer(task.task.id)
        return await result if asyncio.iscoroutine(result) else result

    Fake.calls = []
    monkeypatch.setattr(dispatch, "dispatch_node", fake)
    return Fake


async def _measure(it, **kwargs):
    """`measure_node` over the item's only node, in a real worktree (`_head()`
    reads it) -- `dispatch_node` is faked, so the repo itself stands in."""
    return await dispatch.measure_node(
        it.database, it.run_dirs, it.id, it.chain.chain.nodes[0], it.row(), it.repo, **kwargs
    )


# -- the verdict and the step order (`exec-node-orders-concurrent-task-groups`) --


@pytest.mark.parametrize(
    ("status_of_a", "verdict", "failed"),
    [
        ("failed", "failed", ["a"]),
        # Kraft-tfnjt: a status outside every known outcome stopped the step
        # loop and then fell through the verdict ladder to "ok" -- a pass for a
        # task that reported nothing Kraft understands.
        ("infra", "failed", ["a"]),
        ("unknown", "failed", ["a"]),
        ("capped_out", "failed", ["a"]),
        ("no-such-status", "failed", ["a"]),
        # Not a failure: the later steps must not run against a base the first
        # step just moved, and nothing reaches `failed` to open a fix loop.
        (BASE_MOVED, BASE_MOVED, []),
    ],
    ids=["failed", "infra", "unknown", "capped_out", "no-such-status", "base-moved"],
)
async def test_a_step_that_does_not_pass_stops_the_node(
    item_on, fake_dispatch, status_of_a, verdict, failed
):
    """Anything but an advancing status in step `a` stops the node before step
    `b`, and the verdict fails closed -- naming the task that actually failed."""
    it = await item_on([_steps(["a"], ["b"])])
    fake_dispatch.answer = lambda task_id: status_of_a if task_id == "a" else "done"

    got_verdict, got_failed, _excs = await _measure(it)

    assert got_verdict == verdict
    assert [t.task.id for t in got_failed] == failed
    assert fake_dispatch.calls == ["a"], "a later step must not run after an earlier one stopped"


def _timed(order, slow="a"):
    async def run(task_id):
        order.append(f"start:{task_id}")
        await asyncio.sleep(0.01 if task_id == slow else 0)
        order.append(f"end:{task_id}")
        return "done"

    return run


async def test_a_later_group_runs_only_after_the_earlier_one_finishes(item_on, fake_dispatch):
    it = await item_on([_steps(["a"], ["b"])])
    order = []
    fake_dispatch.answer = _timed(order)

    await _measure(it)

    assert order.index("end:a") < order.index("start:b")


async def test_one_group_still_runs_concurrently(item_on, fake_dispatch):
    """The no-regression case: the tasks in one step overlap."""
    it = await item_on([_steps(["a", "b"])])
    order = []
    fake_dispatch.answer = _timed(order)

    await _measure(it)

    assert order.index("start:b") < order.index("end:a"), "overlapped, not serialized"


async def test_measure_node_records_the_group_it_reached(item_on, fake_dispatch):
    """current_node_id alone cannot say 'step 3 of 4', so every re-entry
    restarted the node."""
    it = await item_on([_steps(["a"], ["b"], ["c"])])
    seen = {}

    def answer(task_id):
        seen[task_id] = it.row()["current_step"]
        return "failed" if task_id == "c" else "done"

    fake_dispatch.answer = answer

    await _measure(it)

    assert seen == {"a": 0, "b": 1, "c": 2}


async def test_measure_node_reads_head_once_per_task_not_once_per_node(
    item_on, fake_dispatch, monkeypatch
):
    """Kraft-37myi: the node-entry snapshot is wrong the moment anything
    dispatched inside the node moves HEAD -- a repair's commit or an ordered
    step that rebases."""
    it = await item_on([_steps(["a", "b"])])
    reads = []
    real_git_read = dispatch._config.git_read

    def counting_git_read(path, *args, **kwargs):
        if args[:1] == ("rev-parse",):
            reads.append(args)
        return real_git_read(path, *args, **kwargs)

    monkeypatch.setattr(dispatch._config, "git_read", counting_git_read)

    await _measure(it)

    assert len(reads) == 2, f"expected one HEAD read per task, got {len(reads)}"


async def test_measure_node_reuses_a_done_session_at_the_current_head(item_on, fake_dispatch):
    """Kraft-gl9d: a task whose session for this (node, round) already exited
    'done' against the worktree's current HEAD is not redispatched; a task
    with no matching 'done' session is. (`test_resuming`'s
    `test_reconcile_reuses_a_done_measuring_session_after_a_crash` is the same
    rule through `executor.resume`.)"""
    it = await item_on(
        [{"id": "verify", "kind": "exec", "tasks": [_builtin("suite"), _agent("review")]}]
    )
    head = dispatch._config.git_read(it.repo, "rev-parse", "HEAD")
    await it.session("s-done", "verify.main.review", "done", head_sha=head)
    fake_dispatch.answer = lambda task_id: "failed"

    verdict, failed, _excs = await _measure(it, round=0)

    # the reused task never dispatches; the other one does
    assert fake_dispatch.calls == ["suite"]
    assert verdict == "failed"
    assert [t.task.id for t in failed] == ["suite"]


def test_every_status_declares_the_tier_that_handles_it():
    """One table says which tier handles each status, so a new status cannot be
    added without saying where it is handled."""
    statuses = {
        v
        for k, v in vars(context).items()
        if k.isupper() and isinstance(v, str) and not k.startswith("_")
    }
    missing = sorted(statuses - set(context.SCOPE))
    assert not missing, f"statuses with no declared scope: {missing}"
    assert set(context.SCOPE.values()) <= {"advance", "task", "node", "chain", "stop"}


# -- a pending `needs_context` question --------------------------------------


_NEEDS_CONTEXT_NODE = {
    "id": "verify",
    "kind": "exec",
    "tasks": [_builtin("suite")],
    "fix_loop": {"tasks": [_agent("fix")], "judge": _agent("judge")},
}


async def _asked(it, sid, path, question, *, round=0):
    """A session at `path` that exited `needs_context` asking `question`."""
    (it.run_dirs.results / f"{sid}.json").write_text(
        json.dumps({"status": "needs_context", "question": question})
    )
    await it.session(sid, path, "needs_context", node="verify", round=round)


async def test_resolved_escalation_does_not_restop_the_node(item_on):
    """An escalation that exited needs_context must not stop the node again.

    Kraft-7itv follow-up: the judge is already excluded for this reason;
    escalation is a conversation with a human, not a measurement, and its
    question re-fires forever once answered.
    """
    it = await item_on([_NEEDS_CONTEXT_NODE], repo="/r")
    await _asked(it, "esc", dispatch.ESCALATION_HOOK, "which base image?")

    assert dispatch.needs_context_question(it.database, it.id, it.chain.chain.nodes[0], 0) is None


async def test_measurement_needs_context_still_stops_the_node(item_on):
    """The negative case: a real measurement question must still stop."""
    it = await item_on([_NEEDS_CONTEXT_NODE], repo="/r")
    await _asked(it, "meas", "verify.main.suite", "which python?")

    node = it.chain.chain.nodes[0]
    assert dispatch.needs_context_question(it.database, it.id, node, 0) == "which python?"


async def test_reentry_is_not_stopped_by_the_previous_passs_fix_question(item_on):
    """A gate rejection or ci_wait poll re-entering a fix_loop node must not
    inherit the last pass's fix-task needs_context (review finding 5).

    walk_node now seeds `round` from the persisted counter, and the counter
    survives a non-resume re-entry -- so the previous pass's round-N fix row is
    still the latest for its path when the new pass takes its first
    measurement, and nothing this pass writes can displace it until it bumps to
    N+1. The unflagged call must still return the question: surfacing a fix
    task's own needs_context one iteration later is deliberate.
    """
    it = await item_on([_NEEDS_CONTEXT_NODE], repo="/r")
    node = it.chain.chain.nodes[0]
    await _asked(it, "fix2", dispatch.fix_task_paths(node)[0], "which migration?", round=2)

    ask = dispatch.needs_context_question
    assert ask(it.database, it.id, node, 2, first_iteration=True) is None
    assert ask(it.database, it.id, node, 2) == "which migration?"


# -- regressed findings (Kraft-s7c04.7) ---------------------------------------


def _f(message):
    return Finding("critical", message, "a.py", 1, "p")


def _round(n, messages, *, fixed=True):
    return {
        "round": n,
        "findings": [_f(m) for m in messages],
        "fix_result_path": f"/r/{n}.json" if fixed else None,
    }


@pytest.mark.parametrize(
    ("history", "regressed"),
    [
        # e983d85c, the dataset's one true X<->Y oscillation: f37b90d3 pinned
        # core.hooksPath=/dev/null to close a container escape; verify flagged
        # that it broke git-lfs pre-push; 7381755f unpinned it; the next round
        # found the CRITICAL escape reopened.
        ([_round(0, ["escape"]), _round(1, ["lfs"]), _round(2, ["escape"])], ["escape"]),
        # Never fixed rather than fixed and broken again: `stuck_fingerprint`'s job.
        ([_round(n, ["escape"]) for n in range(3)], []),
        # Only the latest round's findings can be one: that is what the fixer is
        # about to work on.
        ([_round(0, ["escape"]), _round(1, []), _round(2, ["other"])], []),
        # Gone because a round crashed or a resume re-measured, not because a
        # fix removed it -- so nothing was reverted.
        ([_round(0, ["escape"]), _round(1, [], fixed=False), _round(2, ["escape"])], []),
        ([_round(0, ["escape"]), _round(1, ["escape"])], []),
    ],
    ids=[
        "a-finding-that-came-back",
        "one-that-merely-persisted",
        "one-that-is-gone-now",
        "a-gap-with-no-fix-in-it",
        "two-rounds-cannot-hold-one",
    ],
)
def test_regressed_fingerprints_finds_a_finding_a_fix_undid(history, regressed):
    """A finding fixed and then broken again by a later fix in the same loop --
    the loop had everything it needed to see an oscillation and could only ever
    look one round back."""
    assert dispatch.regressed_fingerprints(history) == [_f(m).fingerprint for m in regressed]


# -- the unresolved findings a retry is seeded with (Kraft-7sec) -------------


def _measured(cycle, *findings):
    return {
        "node_id": "verify",
        "cycle": cycle,
        "findings": [
            {
                "severity": "important",
                "message": message,
                "file": file,
                "line": line,
                "source_plugin": "reviewer",
            }
            for message, file, line in findings
        ],
        "fingerprints": [m for m, _f, _l in findings],
        "noop_hooks": [],
    }


@pytest.mark.parametrize(
    ("measurements", "present", "absent"),
    [
        ([_measured(0, ("missing null check", "a.py", 10))], ["missing null check", "a.py:10"], []),
        # Only the most recent measurement counts: a finding resolved in a later
        # cycle must not be re-seeded.
        (
            [_measured(0, ("old bug", "a.py", 1)), _measured(1, ("new bug", "b.py", 2))],
            ["new bug"],
            ["old bug"],
        ),
        ([], None, None),
        # A blind task failure with nothing structured reported falls through
        # to the caller's own `last_rejection`, rather than seeding an empty note.
        ([_measured(0)], None, None),
    ],
    ids=[
        "the-latest-measurements-findings",
        "not-one-resolved-in-a-later-cycle",
        "no-measurement-yet",
        "no-eligible-finding",
    ],
)
async def test_unresolved_findings_steer_seeds_the_latest_measurements_findings(
    item_on, measurements, present, absent
):
    it = await item_on([_NEEDS_CONTEXT_NODE], repo="/r")
    for payload in measurements:
        await it.database.write(
            lambda c, p=payload: events.append(c, it.id, "findings_measured", p)
        )

    note = dispatch.unresolved_findings_steer(it.database, it.id, "verify", None)

    if present is None:
        assert note is None
        return
    assert all(text in note for text in present), note
    assert not any(text in note for text in absent), note
