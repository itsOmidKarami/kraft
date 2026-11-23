"""A reply round across a retry, a resume, a red pipeline and a new head: the sequences the design
requires, walked against a `FakeForge` with live bot threads (`support.live_bot`)."""

# ruff: noqa: F811 -- `rig` is an imported fixture, taken by name.

import asyncio

import pytest
from support.bot_threads import bot_thread, note
from support.harness import git
from support.live_bot import live_bot
from support.review_rig import (  # noqa: F401
    DECLINE,
    FLAG,
    bot_on,
    events_of,
    filed,
    rig,
    t1,
    walk_to_completion,
)

from kraft import waits
from kraft.executor import dispatch

from .test_reply_round_walks import (
    _RED,
    _ci_in_the_round,
    _early,
    _report_entries,
    _round_records,
    _walk_answering,
)


def _declined(body):
    return {"findings": [], "replies": [{**DECLINE["replies"][0], "body": body}]}


async def test_a_cap_of_three_allows_a_third(rig, monkeypatch):
    """The item's own cap of three replies per thread lets the bot's second answer, read in the
    second round, be met with a third decline."""
    fake = await filed(rig, monkeypatch, t1(), policy={"reply_cap_per_thread": 3})
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    rig.verdicts = [DECLINE, _declined("Second."), _declined("Third.")]

    seen = await _walk_answering(rig, fake, ["Are you sure?", "Really?"], walks=20)

    assert seen[-1] == "completed"
    assert fake.writes == [("reply", "T1")] * 3
    assert len(events_of(rig, "reply_round_started")) == 2


async def test_a_crash_inside_a_reply_round_resumes_it(rig, monkeypatch):
    """The process dies the moment the round starts, before its first step: the cursor is at the
    round's first step (not where the node's last measurement ended), so a fresh `executor.run`
    resumes the round itself: no second round event, the wait runs, and the triage was launched
    once in all."""
    fake = await filed(rig, monkeypatch, t1(), watch_ci=True, tweak=_early)
    monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    rig.verdicts = [DECLINE]
    measure, crashed = dispatch.measure_node, []

    async def dying(*args, **kw):
        if events_of(rig, "reply_round_started") and not crashed:
            crashed.append(rig.item.row()["current_step"])
            raise asyncio.CancelledError  # the process is gone
        return await measure(*args, **kw)

    monkeypatch.setattr(dispatch, "measure_node", dying)
    seen = []
    with pytest.raises(asyncio.CancelledError):
        for n in range(14):
            rig.clock(10 + n * 63)
            seen.append(await rig.walk())

    assert crashed == [0], "begin left the cursor at the round's first step"
    seen += await walk_to_completion(rig, start=10 + 63 * len(seen), step=63, walks=10)
    assert seen[-1] == "completed"
    assert len(events_of(rig, "reply_round_started")) == 1
    assert rig.triaged.count("feedback.review_feedback.triage") == 1
    assert len(_round_records(rig)) == 1


async def test_a_red_pipeline_in_a_reply_round_takes_the_node_s_recovery(rig, monkeypatch):
    """The round's CI goes red: `on_failure` runs once, its re-measure passes on the same head and
    the node completes. The one round counts toward the limit, and its event's round is the loop
    counter's count, never the repair's."""
    fake = await filed(rig, monkeypatch, t1(), watch_ci=True, tweak=_early)
    monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    _ci_in_the_round(fake, rig, red_at=150)
    rig.verdicts = [DECLINE, []]

    seen = await walk_to_completion(rig, start=10, step=63, walks=16)

    assert seen[-1] == "completed"
    assert [t.endswith("repair_checks") for t in rig.triaged].count(True) == 1
    [started] = events_of(rig, "reply_round_started")
    assert started["payload"]["round"] == 0
    # The re-measure inside the round is the round's record, at the repair's round.
    assert [e["payload"]["cycle"] for e in _round_records(rig)] == [-1]


def _red_in_the_round_until_fixed(fake, rig, fixes):
    """Green except in a reply round: red until the fix loop's repair has run `fixes` times."""
    read = fake.ci_status

    async def ci_status(**kw):
        repairs = [t for t in rig.triaged if t.endswith("fix.repair")]
        red = bool(events_of(rig, "reply_round_started")) and len(repairs) < fixes
        fake.ci_states = ["failed" if red else "success"]
        fake.ci_failed_jobs = [_RED if red else ()]
        return await read(**kw)

    fake.ci_status = ci_status


async def test_after_a_fix_cycle_a_red_round_goes_straight_to_the_fix_loop(rig, monkeypatch):
    """The recovery is for the first measurement of an entry: a round that follows a fix cycle and
    goes red does not run `on_failure` again, it enters the fix loop."""
    t2 = bot_thread("T2", note("N2", "a", "rename y"))
    on_repaired = bot_thread("T1", note("N1", "a", "rename x"), file="repaired.txt")
    fake = await filed(rig, monkeypatch, on_repaired, t2, watch_ci=True)
    monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    decline_t2 = {"thread": "T2", "kind": "declined", "body": "The call is right."}
    rig.verdicts = [{"findings": [FLAG], "replies": [decline_t2]}, [], [], []]
    rig.repairs = [{}, {}]
    _red_in_the_round_until_fixed(fake, rig, fixes=2)

    seen = await walk_to_completion(rig, start=10, step=63, walks=20)

    assert seen[-1] == "completed"
    assert not any(t.endswith("repair_checks") for t in rig.triaged)
    assert len([t for t in rig.triaged if t.endswith("fix.repair")]) == 2
    # The fix moved the head, so the thread still owed was given a round on the new head too.
    heads = [e["payload"]["head"] for e in events_of(rig, "reply_round_started")]
    assert len(heads) == 2 and len(set(heads)) == 2


async def test_a_new_head_is_never_in_a_reply_round(rig, monkeypatch):
    """The head moves while a round waits: the next measurement is an ordinary one of the new head,
    with Part 1's full head wait and skip rule and a CI look bounded by the CI wait's own time."""
    fake = await filed(rig, monkeypatch, t1(), watch_ci=True, tweak=_early)
    monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    read = fake.ci_status
    first = {}

    async def ci_status(**kw):
        head = git(rig.item.worktree, "rev-parse", "HEAD")
        first.setdefault("head", head)
        pending = head != first["head"] and waits.now() < rig.moment(400)
        fake.ci_states = ["pending" if pending else "success"]
        return await read(**kw)

    fake.ci_status = ci_status
    rig.verdicts = [DECLINE, []]
    seen = []
    for n in range(16):
        rig.clock(10 + n * 63)
        seen.append(await rig.walk())
        if events_of(rig, "reply_round_started") and seen[-1] == "waiting" and len(first) == 1:
            (rig.item.worktree / "moved.txt").write_text("x")
            git(rig.item.worktree, "add", "moved.txt")
            git(rig.item.worktree, "commit", "-qm", "a person pushed")
            first["moved"] = True
        if seen[-1] == "completed":
            break

    assert seen[-1] == "completed" and first.get("moved")
    [started] = events_of(rig, "reply_round_started")
    records = events_of(rig, "findings_measured")
    assert [bool(e["payload"].get("reply_round")) for e in records] == [False, False]
    assert rig.triaged.count("feedback.review_feedback.triage") == 2  # Part 1's skip did not apply
    ci_waits = [
        e["payload"]
        for e in events_of(rig, "external_wait_started")
        if e["payload"]["task"].endswith(".await_ci")
    ]
    assert started["payload"]["deadline"] not in [w["deadline"] for w in ci_waits[1:]]
    assert _report_entries(rig, kind="ci") == []
