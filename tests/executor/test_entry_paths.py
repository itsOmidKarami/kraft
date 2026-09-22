"""Every way back into the walk goes through one function that honours the
item's cursor and its status (Kraft-c3dab, Kraft-z0hah).

The doors that *resume* -- `/resume`, crash resume, the rate-limit poller, the
CI-wait poller -- hand the walk no position of their own: `walk.run_once`
reads `current_node_id` and `current_step` off the row. The doors that *move*
the item (retry, skip, a gate decision) say where they move it to.
"""

from __future__ import annotations

import pytest
from support.harness import entry_of

from kraft import executor, policy, store
from kraft.executor import dispatch
from kraft.executor.context import RATE_LIMITED, WAITING

#: One node of two ordered steps, then a second node. `first` has already run.
TWO_STEPS = """
- id: n
  kind: exec
  steps:
    - id: first
      tasks: [{id: a, kind: subprocess, command: "true"}]
    - id: second
      tasks: [{id: b, kind: subprocess, command: "true"}]
- {id: after, kind: exec, tasks: [{id: c, kind: subprocess, command: "true"}]}
"""

#: The same node, able to remediate itself -- crash resume used to re-enter
#: such a node from its first step.
WITH_FIX_LOOP = TWO_STEPS.replace(
    "- id: n\n  kind: exec\n",
    "- id: n\n  kind: exec\n  fix_loop:\n"
    '    tasks: [{id: fix, kind: subprocess, command: "true"}]\n',
)

#: Three single-step nodes: crash resume reads the first one's outcome off its
#: session and walks on from the second.
THREE_NODES = """
- {id: n, kind: exec, tasks: [{id: a, kind: subprocess, command: "true"}]}
- {id: after, kind: exec, tasks: [{id: c, kind: subprocess, command: "true"}]}
- {id: last, kind: exec, tasks: [{id: d, kind: subprocess, command: "true"}]}
"""

LAUNCH = executor.LaunchContext(repo_entry=entry_of({"setup_command": ""}), steering_dir=None)


@pytest.fixture
def dispatched(monkeypatch):
    """Every task path dispatched, in order; `verdicts[path]` overrides the
    `done` a task otherwise reports."""
    ran = _Dispatched()
    verdicts = ran.verdicts

    async def fake(db, run_dirs, task, node, row, worktree, **kwargs):
        ran.append(task.path)
        return verdicts.get(task.path, "done")

    monkeypatch.setattr(dispatch, "dispatch_node", fake)
    return ran


class _Dispatched(list):
    def __init__(self):
        super().__init__()
        self.verdicts: dict[str, str] = {}


async def _at_second_step(item_on, chain):
    it = await item_on(chain, "n", worktree=True)
    await it.database.write(lambda c: store.set_current_step(c, it.id, 1))
    return it


@pytest.mark.parametrize("chain", [TWO_STEPS, WITH_FIX_LOOP], ids=["plain", "fix-loop"])
async def test_a_walk_given_no_position_starts_at_the_items_cursor(
    item_on, database, run_dirs, dispatched, chain
):
    """The one entry function. `/resume`, the rate-limit and CI-wait pollers
    call it this way; `first` completed before the stop and does not run again
    (`resume-preserves-completed-work`)."""
    it = await _at_second_step(item_on, chain)

    result = await executor.run_once(
        database, run_dirs, work_item_id=it.id, policy=_policy(), launch=LAUNCH
    )

    assert result == "completed"
    assert dispatched == ["n.second.b", "after.main.c"]


@pytest.mark.parametrize("chain", [TWO_STEPS, WITH_FIX_LOOP], ids=["plain", "fix-loop"])
async def test_crash_resume_keeps_the_steps_that_completed(
    item_on, database, run_dirs, dispatched, chain
):
    """Kraft-c3dab: crash resume re-entered a multi-step or self-remediating
    node from its first step, rerunning paid work that had completed."""
    it = await _at_second_step(item_on, chain)

    result = await _crash_resume(database, run_dirs, it)

    assert result == "completed"
    assert dispatched == ["n.second.b", "after.main.c"]


@pytest.mark.parametrize(
    ("verdict", "status"), [(WAITING, "waiting"), (RATE_LIMITED, "rate_limited")]
)
async def test_crash_resume_leaves_a_waiting_item_waiting(
    item_on, database, run_dirs, dispatched, verdict, status
):
    """Kraft-z0hah, first half: crash resume mapped every non-`ok` result to
    `needs_human`, so a node that went back to waiting on CI read as stuck and
    auto-escalation dispatched an agent onto it."""
    it = await _at_second_step(item_on, WITH_FIX_LOOP)
    dispatched.verdicts["n.second.b"] = verdict

    result = await _crash_resume(database, run_dirs, it)

    assert (result, it.status()) == (verdict, status)
    assert it.events("work_item_needs_human") == []
    assert it.events("escalation_message") == []


async def test_crash_resume_does_not_walk_past_a_pause(item_on, database, run_dirs, dispatched):
    """Kraft-z0hah, second half: the resumed tail ignored a `paused` result,
    walked on into the next node and marked the item completed."""
    it = await item_on(THREE_NODES, "n", worktree=True)
    await it.session("s-a", "n.main.a", "done")
    dispatched.verdicts["after.main.c"] = "paused"

    result = await _crash_resume(database, run_dirs, it)

    assert result == "paused"
    assert dispatched == ["after.main.c"]
    assert it.events("work_item_completed") == []


async def test_crash_resume_stops_on_an_item_no_longer_active(
    item_on, database, run_dirs, dispatched
):
    """The item's status is the other half of the invariant: a walk entered on
    an item a human already paused does nothing."""
    it = await item_on(TWO_STEPS, "n", worktree=True, status="active")
    await database.write(lambda c: store.pause_work_item(c, it.id, []))

    assert await _crash_resume(database, run_dirs, it) == "paused"
    assert dispatched == []


ENDED = pytest.mark.parametrize(
    ("verb", "ended"), [("complete", "completed"), ("cancel", "abandoned")]
)


async def _ended(item_on, verb):
    """An item on `THREE_NODES` whose first node's session finished, ended
    by an operator's `verb` -- which crash resume must not settle or walk on."""
    it = await item_on(THREE_NODES, "n", worktree=True, status="active")
    await it.session("s-a", "n.main.a", "done")
    await it.database.write(lambda c: store.end_work_item(c, it.id, verb, "by hand"))
    return it


@ENDED
@pytest.mark.parametrize("entry", ["walk", "crash-resume"])
async def test_no_entry_into_the_walk_runs_an_ended_item(
    item_on, database, run_dirs, dispatched, verb, ended, entry
):
    """Kraft-dncfg: the executor's own half of "no action on an ended item
    runs its chain again" -- whatever door reached it, and before the walk
    touches the worktree, the bead tracker or the item's cursor."""
    it = await _ended(item_on, verb)
    before = len(it.events())

    if entry == "walk":
        result = await executor.run_once(
            database, run_dirs, work_item_id=it.id, policy=_policy(), launch=LAUNCH
        )
    else:
        result = await _crash_resume(database, run_dirs, it)

    assert (result, it.status()) == (ended, ended)
    assert dispatched == []
    assert len(it.events()) == before


#: Every store write that moves an item to a status other than an ending one.
_NOW = "2000-01-01T00:00:00+00:00"
STATUS_WRITES = {
    "mark_needs_human": lambda c, wid: store.mark_needs_human(c, wid, "n", "stop"),
    "mark_rate_limited": lambda c, wid: store.mark_rate_limited(c, wid, "n", _NOW),
    "mark_waiting": lambda c, wid: store.mark_waiting(c, wid, "n", _NOW),
    "mark_blocked_by_dependency": lambda c, wid: store.mark_blocked_by_dependency(
        c, wid, "n", ["b-1"]
    ),
    "mark_reentered": lambda c, wid: store.mark_reentered(c, wid),
    "pause_work_item": lambda c, wid: store.pause_work_item(c, wid, []),
    "pause_for_broken_base": lambda c, wid: store.pause_for_broken_base(
        c, wid, broken_by="abc", follow_up_bead=None
    ),
    "claim_for_run": lambda c, wid: store.claim_for_run(c, wid, from_statuses=list(store.ENDED)),
    "request_gate": lambda c, wid: store.request_gate(c, wid, "n", "g"),
    "approve_gate": lambda c, wid: store.approve_gate(c, wid, "n"),
    "reject_gate": lambda c, wid: store.reject_gate(c, wid, "n", "no", reopen=True),
    "skip_node": lambda c, wid: store.skip_node(c, wid, "n", None, None),
}


@ENDED
@pytest.mark.parametrize("write", list(STATUS_WRITES))
async def test_no_status_write_moves_an_ended_item(item_on, database, verb, ended, write):
    """Kraft-y6f08: whichever door or poller reaches it, and however late, a
    status write leaves an ended item ended -- and a write that did not
    happen records nothing. The backstop behind every door's own 409."""
    it = await _ended(item_on, verb)
    before = len(it.events())

    await database.write(lambda c: STATUS_WRITES[write](c, it.id))

    assert it.status() == ended
    assert len(it.events()) == before


def _policy() -> policy.Policy:
    return policy.Policy(
        loops={}, default=policy.Cap(attempts=2, wall_clock_s=3600), auto_escalate_stuck=True
    )


async def _crash_resume(database, run_dirs, it):
    """`executor.resume` the way `api/startup.py` calls it after a restart,
    with auto-escalation armed so a wrong `needs_human` would dispatch one."""
    return await executor.resume(
        database,
        run_dirs,
        work_item_id=it.id,
        adopted={},
        policy=_policy(),
        launch=LAUNCH,
    )


#: One step of two tasks: a crash can land between their starts.
PAIR = """
- id: n
  kind: exec
  tasks:
    - {id: a, kind: subprocess, command: "true"}
    - {id: b, kind: subprocess, command: "true"}
- {id: after, kind: exec, tasks: [{id: c, kind: subprocess, command: "true"}]}
"""


async def test_crash_resume_dispatches_a_sibling_the_crash_never_started(
    item_on, database, run_dirs, dispatched
):
    """Kraft-fvmym: `a` finished and `b` has no session yet -- the crash came
    between their starts. That is not a failure for a human: the walk runs
    the node again and `b` gets dispatched."""
    it = await item_on(PAIR, "n", worktree=True)
    await it.session("s-a", "n.main.a", "done")

    assert await _crash_resume(database, run_dirs, it) == "completed"
    assert "n.main.b" in dispatched
    assert it.events("work_item_needs_human") == []
