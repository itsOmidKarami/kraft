"""Stopping a work item for a human: `stops.claimed_or_stopped`, the invariant
that no path may leave an item claimed and unowned, and the cause a
config-error stop carries on its card."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kraft import store
from kraft.executor import stops, walk

_CHAIN = """
- id: implementation
  kind: exec
  tasks: [{id: build, kind: subprocess, command: "true"}]
"""


@pytest.fixture
async def claimed(item_on):
    """An item at `implementation`, claimed for a run: `active`, a walk
    supposedly behind it."""
    it = await item_on(_CHAIN, "implementation", repo="/r")
    await it.database.write(
        lambda c: store.claim_for_run(c, it.id, from_statuses=["active", "paused"])
    )
    assert it.status() == "active", "the fixture did not claim the item"
    return it


def _bracket(it, reason="gave up", **kwargs):
    return stops.claimed_or_stopped(it.database, it.id, "implementation", reason=reason, **kwargs)


async def test_an_exit_that_neither_hands_off_nor_stops_leaves_the_item_stopped(claimed):
    """The whole point. A claimed item reads `active`, which means "a walk is
    behind this"; an early return that spawns nothing would leave it looking
    running with nothing coming for it, and no selector filters on `active`."""
    async with _bracket(claimed):
        pass

    assert claimed.status() == "needs_human"


async def test_a_raising_exit_stops_the_item_and_still_raises(claimed):
    """An exception is an exit too, and the ones that matter most here are the
    ones no static sweep of `return`/`raise` statements can see -- a `LookupError`
    from `walk.chain_of`, a `StopIteration` from a `next(...)`. The bracket must
    not swallow it: the 409 a refused hand-off raises has to reach the caller.
    Its stop names the exception (Task 6b review round 1): the bracket's own
    reason says what happened, not why, and the exception that escaped reached
    only the server log."""
    with pytest.raises(LookupError, match="boom"):
        async with _bracket(claimed, "could not start a walk"):
            raise LookupError("no-such-node boom")

    assert claimed.status() == "needs_human"
    reason = claimed.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert reason.startswith("could not start a walk")
    assert "no-such-node" in reason


async def test_a_handed_off_item_is_left_alone(claimed):
    """`handed_off` is "something else owns this now" -- true both when this call
    spawned the walk and when `spawn` refused because another walk already holds
    it. Stopping either would kill a live run."""
    async with _bracket(claimed, handed_off=lambda: True):
        pass

    assert claimed.status() == "active"


async def test_an_item_the_body_already_stopped_is_not_stopped_twice(claimed):
    """Idempotent, so a site that already had its own per-branch stop keeps
    exactly one event rather than gaining a second, contradictory one."""
    async with _bracket(claimed):
        await claimed.database.write(
            lambda c: store.mark_needs_human(c, claimed.id, "implementation", "the real reason")
        )

    assert claimed.status() == "needs_human"
    assert [e["payload"]["reason"] for e in claimed.events("work_item_needs_human")] == [
        "the real reason"
    ]


async def test_a_walk_that_reached_a_terminal_status_needs_no_callback(claimed):
    """A site that awaits its walk inline (`gates.resume_after_escalation`)
    registers no task, so it passes no `handed_off`. It does not need one: a
    finished walk has already written its own status, and the bracket's `active`
    test is false."""
    async with _bracket(claimed):
        await claimed.database.write(lambda c: store.mark_completed(c, claimed.id))

    assert claimed.status() == "completed"


async def test_a_gate_approval_that_cannot_start_a_walk_stops_the_item(claimed):
    """The live instance `dev/check_claim_handoff.py` could not see until its
    `CLAIMS` set stopped being hand-written.

    `store.approve_gate` is an unconditional `UPDATE work_items SET status =
    'active'` -- a claim like any other, and `api/deps.py`'s own `task_is_live`
    docstring already named the hazard: "calling `store.approve_gate`/
    `apply_rejection` and only then discovering `spawn` refuses would leave the
    gate cleared and the item `active` with no walk behind it". Both approval
    doors then computed their start index with a **defaultless** `next(...)`
    (`gate_node_index`, which its own docstring says "Raises `StopIteration` for
    a gate this chain does not have"), after the claim.

    Asserted here against the bracket rather than over HTTP, because the failure
    is the *stored status*, not the response: the gate is cleared either way.
    """
    await claimed.database.write(lambda c: store.approve_gate(c, claimed.id, "spec_approval"))
    assert claimed.status() == "active", "approve_gate is a claim"

    # `next(...)` inside a coroutine raises `StopIteration`, which Python
    # re-wraps as `RuntimeError` on the way out -- so the assertion is on the
    # *stored status*, which is the thing that was wrong.
    with pytest.raises((StopIteration, RuntimeError)):
        async with _bracket(claimed, "could not start a walk"):
            raise StopIteration("gate_node_index on a gate this chain does not have")

    assert claimed.status() == "needs_human"


# -- the cause a config-error stop carries on its card (Task 6b review, finding 3) --
#
# `walk._stop_for_config_error` reads the task's own session log. Its edges must
# never leave a stop *less* legible than the generic "see the session log" text:
# a missing or undecodable log falls back to it, and only the newest session in
# the status being explained is read.


_NODE = SimpleNamespace(id="verify")
_TASK = SimpleNamespace(path="verify.main.check", task=SimpleNamespace(id="check"))


async def _config_error_reason(item_on, sessions) -> str:
    """The reason `_stop_for_config_error` writes for `check` in `verify`, given
    that task's sessions as `(sid, log_path, status, created_at)`."""
    it = await item_on(_CHAIN, repo="/r")
    for sid, log_path, status, created_at in sessions:
        await it.database.write(
            lambda c, sid=sid, log=log_path, st=status, at=created_at: c.execute(
                "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid,"
                " log_path, result_path, status, created_at)"
                " VALUES (?, ?, 'verify', ?, NULL, ?, 'x', ?, ?)",
                (sid, it.id, _TASK.path, str(log), st, at),
            )
        )
    await walk._stop_for_config_error(it.database, it.id, _NODE, [_TASK])
    return it.events("work_item_needs_human")[-1]["payload"]["reason"]


@pytest.mark.parametrize(
    "log_bytes", [None, b"\xff\xfe\xfa not utf-8\n"], ids=["missing", "undecodable"]
)
async def test_an_unreadable_log_falls_back_to_the_generic_pointer(item_on, tmp_path, log_bytes):
    """A missing log, and one that does not decode, fall back rather than
    escaping the walk."""
    log = tmp_path / "s1.log"
    if log_bytes is not None:
        log.write_bytes(log_bytes)

    reason = await _config_error_reason(item_on, [("s1", log, "config_error", "2026-01-01")])

    assert reason == "could not start check in node verify: see the session log"


async def test_the_newest_session_is_the_one_explained(item_on, tmp_path):
    """A retry that hits a config_error again for a new reason must not put the
    stale first cause on the card."""
    old, new = tmp_path / "old.log", tmp_path / "new.log"
    old.write_text("stale cause\n")
    new.write_text("current cause\n")

    reason = await _config_error_reason(
        item_on,
        [("s1", old, "config_error", "2026-01-01"), ("s2", new, "config_error", "2026-01-02")],
    )

    assert reason.endswith(": current cause")


async def test_only_a_session_in_the_explained_status_is_read(item_on, tmp_path):
    """A newer session of the same task in another status (a retry that got
    further, say) is not this stop's cause."""
    cause, other = tmp_path / "cause.log", tmp_path / "other.log"
    cause.write_text("the real cause\n")
    other.write_text("some agent output\n")

    reason = await _config_error_reason(
        item_on,
        [("s1", cause, "config_error", "2026-01-01"), ("s2", other, "done", "2026-01-02")],
    )

    assert reason.endswith(": the real cause")
