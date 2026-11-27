"""The CI look of a reply round (`run_task(reply=)` on `ci_poll`): a pipeline still pending is
bounded by the round's deadline, never by the CI wait's own timeout, and a red one still fails.
Through `forge.run_task` against a `FakeForge`."""

# ruff: noqa: F811 -- `on_branch` is an imported fixture, taken by name.

from __future__ import annotations

from datetime import timedelta

import pytest

from kraft.adapters import forge
from kraft.adapters.forge import run as forge_run
from kraft.adapters.forge.replies import owed

from .test_run_push import on_branch  # noqa: F401 -- the fixture
from .test_run_reply_wait import _ended

_RED = (forge.FailedJob("test", "failed", "script_failure"),)
_FIVE = timedelta(minutes=5)


@pytest.mark.parametrize(
    "state, jobs, at, with_reply, returned, recorded, ended",
    [
        ("pending", (), 0, True, "waiting", "waiting", []),
        ("pending", (), 300, True, "done", "done", ["timed_out"]),
        ("failed", _RED, 300, True, "failed", "failed", ["settled"]),
        ("success", (), 300, True, "done", "done", ["settled"]),
        ("pending", (), 300, False, "waiting", "waiting", []),
    ],
    ids=[
        "pending-before-the-deadline-waits",
        "pending-at-the-deadline-is-done",
        "red-still-fails",
        "green-is-done",
        "no-reply-keeps-the-ci-waits-own-timeout",
    ],
)
async def test_the_ci_look_in_a_reply_round(
    run_forge, on_branch, wait_clock, state, jobs, at, with_reply, returned, recorded, ended
):
    """`a-reply-round-never-stops-the-item-on-pending-ci`: pending before the round's deadline
    parks; at it, the look ends as a settle (`done`, its wait `timed_out`, never `capped_out`); red
    fails as ever; and without a round the wait keeps its own 90 minutes."""
    fake = forge.FakeForge(ci_states=[state], ci_failed_jobs=[jobs])
    await fake.open_mr(repo=on_branch, branch="kraft/w1", base="main", title="t", body="b")
    wait_clock.advance(at)
    reply = owed.ReplyWait(("T1",), _FIVE, wait_clock.start + timedelta(seconds=300))

    got = await run_forge(
        fake,
        "ci_poll",
        "s1",
        repo=on_branch,
        orig_repo=on_branch,
        **({"reply": reply} if with_reply else {}),
    )

    assert got == (returned, recorded)
    assert _ended(run_forge) == ended
    if with_reply and state == "pending" and at == 0:
        [wait] = [e for e in run_forge.events("external_wait_started")]
        assert wait["deadline"] == wait_clock.at(300), "the round's time, not the wait's 90 minutes"


async def test_a_later_head_s_ci_look_keeps_the_ci_waits_own_timeout(
    run_forge, on_branch, wait_clock
):
    """The head moves while a round's CI look is parked: the next look has no round, and its
    pending pipeline must not time out at the round's deadline (a stop for a person) but start the
    CI wait's own."""
    fake = forge.FakeForge(ci_states=["pending"])
    await fake.open_mr(repo=on_branch, branch="kraft/w1", base="main", title="t", body="b")
    reply = owed.ReplyWait(("T1",), _FIVE, wait_clock.start + timedelta(seconds=300))
    ask = {"repo": on_branch, "orig_repo": on_branch}
    assert await run_forge(fake, "ci_poll", "s1", reply=reply, **ask) == ("waiting", "waiting")
    wait_clock.advance(301)

    assert (await run_forge(fake, "ci_poll", "s2", **ask))[0] == "waiting"

    assert _ended(run_forge) == ["superseded"]
    starts = run_forge.events("external_wait_started")
    assert starts[1]["deadline"] == wait_clock.at(301 + 90 * 60)


async def test_a_rebase_in_a_reply_rounds_ci_look_waits_for_the_rebased_heads_ci(
    run_forge, on_branch, wait_clock, monkeypatch
):
    """A node with no base-change restart rebases a conflict away past the round's deadline: the
    rebased head has no green to complete on, so it waits on the CI wait's own timeout."""

    async def rebased(*_a, **_k):
        return "rebased\n", "done"

    monkeypatch.setattr(forge_run, "_rebase_conflict_away", rebased)
    fake = forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict")
    await fake.open_mr(repo=on_branch, branch="kraft/w1", base="main", title="t", body="b")
    wait_clock.advance(301)
    reply = owed.ReplyWait(("T1",), _FIVE, wait_clock.start + timedelta(seconds=300))

    got = await run_forge(fake, "ci_poll", "s1", reply=reply, repo=on_branch, orig_repo=on_branch)

    assert got == ("waiting", "waiting")
    [wait] = run_forge.events("external_wait_started")
    assert wait["deadline"] == wait_clock.at(301 + 90 * 60)
