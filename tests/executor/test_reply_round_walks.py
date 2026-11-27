"""A reply round, walked: after Kraft replies in a bot thread and before the node completes, the
node waits for the bot's answer, judges what is new and answers or fixes, a bounded number of
times. Through the executor against a `FakeForge` with live bot threads (`support.live_bot`)."""

# ruff: noqa: F811 -- `rig` is an imported fixture, taken by name.

import json
from dataclasses import replace
from datetime import timedelta

import pytest
from support.bot_threads import bot_thread, kraft_note, note
from support.harness import entry_of
from support.live_bot import bot_says, counting, live_bot
from support.review_rig import (  # noqa: F401
    DECLINE,
    FLAG,
    bot_on,
    events_of,
    filed,
    report_of,
    review_node,
    rig,
    t1,
    walk_to_completion,
)

from kraft import executor, store, waits
from kraft.adapters import forge
from kraft.adapters.forge.models import ForgeError
from kraft.adapters.forge.replies import record
from kraft.automated_review import BotReviewer
from kraft.executor import replies, reply_round


async def test_a_silent_bot_gets_one_reply_round_then_the_node_completes(rig, monkeypatch):
    fake = await filed(rig, monkeypatch, t1())
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    rig.verdicts = [DECLINE]

    seen = await walk_to_completion(rig, start=10, step=63, walks=8)

    assert seen[-1] == "completed"
    assert fake.writes == [("reply", "T1")]  # one decline, no second
    [started] = events_of(rig, "reply_round_started")
    assert started["payload"]["threads"] == ["T1"]
    first, second = events_of(rig, "findings_measured")
    assert "reply_round" not in first["payload"] and second["payload"]["reply_round"] is True
    assert "feedback.review_feedback.triage" in second["payload"]["unmeasured_tasks"]
    assert rig.triaged.count("feedback.review_feedback.triage") == 1  # the round's triage skipped


def _round_records(rig):
    """The `findings_measured` records a reply round wrote."""
    return [e for e in events_of(rig, "findings_measured") if e["payload"].get("reply_round")]


def _early(node):
    node["fix_loop"]["early"] = True


def _ci_first(node):
    ci = {"id": "ci", "tasks": [{"id": "await_ci", "kind": "forge", "target": "mr.ci"}]}
    node["steps"].insert(0, ci)


@pytest.mark.parametrize(
    "watch_ci, tweak",
    [(True, _early), (False, None), (False, _ci_first)],
    ids=["shipped", "no-early", "ci-first"],
)
async def test_a_reply_round_runs_on_every_shape_that_takes_replies(
    rig, monkeypatch, watch_ci, tweak
):
    """`a-reply-round-runs-instead-of-completing-up-to-the-limit`: the shipped shape (the review
    wait watching CI, an early fix loop, the CI step), a node with neither, and one that
    waits for CI before the review each complete after one round."""
    fake = await filed(rig, monkeypatch, t1(), watch_ci=watch_ci, tweak=tweak)
    # A push (the CI step's, before the review) is dated by the real clock, the bot's activity
    # by the frozen one: the head's own date is where the frozen clock starts.
    monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    rig.verdicts = [DECLINE]

    seen = await walk_to_completion(rig, start=10, step=63, walks=12)

    assert seen[-1] == "completed"
    assert fake.writes == [("reply", "T1")]
    assert len(events_of(rig, "reply_round_started")) == 1


_RED = (forge.FailedJob("test", "failed", "script_failure"),)


def _red_after_the_triage_until_repaired(fake, rig):
    """The CI step (it runs after the triage) reads a red pipeline until `on_failure`'s repair ran;
    every other read, the review wait's included, is green."""
    read = fake.ci_status

    async def ci_status(**kw):
        triaged = any(t.endswith(".triage") for t in rig.triaged)
        repaired = any(t.endswith("repair_checks") for t in rig.triaged)
        red = triaged and not repaired
        fake.ci_states = ["failed" if red else "success"]
        fake.ci_failed_jobs = [_RED if red else ()]
        return await read(**kw)

    fake.ci_status = ci_status


async def test_a_round_starts_from_the_exit_after_a_passing_on_failure(rig, monkeypatch):
    """The first measurement's CI is red, `on_failure` repairs it and its re-measure passes, and
    the thread Kraft declined is still owed: a round starts from that exit."""
    fake = await filed(rig, monkeypatch, t1(), watch_ci=True)
    monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    _red_after_the_triage_until_repaired(fake, rig)
    rig.verdicts = [DECLINE, []]

    seen = await walk_to_completion(rig, start=10, step=63, walks=14)

    assert seen[-1] == "completed"
    assert any(t.endswith("repair_checks") for t in rig.triaged)
    [started] = events_of(rig, "reply_round_started")
    assert started["payload"]["threads"] == ["T1"]
    assert _round_records(rig), "the round measured the node again"
    assert fake.writes == [("reply", "T1")]


def _with_a_judge(node):
    node["fix_loop"]["judge"] = {
        "id": "judge",
        "kind": "agent",
        "harness": "fake",
        "prompt": "Judge.",
    }


async def test_a_round_starts_from_the_judge_s_stop_downgrade_exit(rig, monkeypatch):
    """T1 is flagged and fixed, then raised again and the judge downgrades it: the node would
    complete, but T2, which Kraft declined, is still owed: a round starts from that exit."""
    t2 = bot_thread("T2", note("N2", "a", "rename y"))
    fake = await filed(rig, monkeypatch, t1(), t2, watch_ci=True, tweak=_with_a_judge)
    monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    decline_t2 = {"thread": "T2", "kind": "declined", "body": "The call is right."}
    rig.verdicts = [{"findings": [FLAG], "replies": [decline_t2]}, [FLAG]]
    rig.repairs = [{}]
    rig.judges = [{"verdict": "stop_downgrade"}]

    seen = await walk_to_completion(rig, start=10, step=63, walks=14)

    assert seen[-1] == "completed"
    [started] = events_of(rig, "reply_round_started")
    assert started["payload"]["threads"] == ["T2"]
    [verdict] = events_of(rig, "judge_verdict")
    assert verdict["payload"]["verdict"] == "stop_downgrade"
    assert _round_records(rig), "the round measured the node again"
    assert fake.writes == [("reply", "T2")]


async def _walk_answering(rig, fake, answers, *, walks=14, step=63):
    """Walk to completion; once a reply round has started, the bot answers thread T1 with the next
    of `answers` (a body) -- once per round. Returns each walk's status."""
    seen, answered = [], 0
    for n in range(walks):
        rig.clock(10 + n * step)
        seen.append(await rig.walk())
        if seen[-1] == "completed":
            return seen
        if len(events_of(rig, "reply_round_started")) > answered and answered < len(answers):
            # The bot answers just after the clock's now: after every read so far, before the next.
            at = (waits.now() + timedelta(seconds=1)).isoformat()
            bot_says(fake, "T1", note(f"A{answered}", "a", answers[answered], at=at))
            answered += 1
    raise AssertionError(f"not completed after {walks} walks: {seen}")


@pytest.mark.parametrize(
    "second, replies, rounds",
    [
        pytest.param(
            {"findings": [], "replies": [{**DECLINE["replies"][0], "body": "Still the call."}]},
            [("reply", "T1"), ("reply", "T1")],
            2,
            id="answer-then-another-decline",
        ),
        pytest.param([], [("reply", "T1")], 1, id="answer-the-triage-accepts"),
    ],
)
async def test_a_bot_answer_is_judged_in_the_same_run(rig, monkeypatch, second, replies, rounds):
    """The bot answers Kraft's decline while the round waits: the round's triage reads it. A
    second decline is posted (and, the bot being silent after it, a second round waits for it);
    an answer the triage accepts ends the conversation."""
    fake = await filed(rig, monkeypatch, t1())
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    rig.verdicts = [DECLINE, second]

    seen = await _walk_answering(rig, fake, ["Are you sure?"])

    assert seen[-1] == "completed"
    assert fake.writes == replies
    assert len(events_of(rig, "reply_round_started")) == rounds
    assert rig.triaged.count("feedback.review_feedback.triage") == 2


def _report_entries(rig, **match):
    return [e for e in report_of(rig)["entries"] if all(e.get(k) == v for k, v in match.items())]


async def test_the_per_head_limit_ends_the_conversation(rig, monkeypatch):
    """With one round per head and a bot that answers every time, the round runs once and the node
    then completes: the thread still owed is listed as `owed_at_limit`, with one round event."""
    fake = await filed(rig, monkeypatch, t1(), policy={"reply_rounds_per_head": 1})
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    again = {"findings": [], "replies": [{**DECLINE["replies"][0], "body": "Still the call."}]}
    rig.verdicts = [DECLINE, again]

    seen = await _walk_answering(rig, fake, ["Are you sure?", "Really?"])

    assert seen[-1] == "completed"
    assert len(events_of(rig, "reply_round_started")) == 1
    [entry] = _report_entries(rig, kind="owed")
    assert (entry["thread"], entry["outcome"]) == ("T1", "owed_at_limit")
    assert "limit 1" in entry["why"]


async def test_an_owed_at_limit_entry_carries_the_unread_answer(rig, monkeypatch):
    """The bot answers after the round's triage read the thread, before the node completes: at the
    limit, the entry's last word is that answer, which no triage's copy of the thread holds."""
    fake = await filed(rig, monkeypatch, t1(), policy={"reply_rounds_per_head": 1})
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    rig.verdicts = [DECLINE]
    read = fake.read_thread
    late = {"said": False}

    async def read_thread(*, repo, branch, thread_id):
        if events_of(rig, "reply_round_started") and not late["said"]:
            late["said"] = True  # the owed check at the end of the round is the first read
            bot_says(fake, "T1", note("L", "a", "One more thing.", at="2026-01-01T01:00:00Z"))
        return await read(repo=repo, branch=branch, thread_id=thread_id)

    monkeypatch.setattr(fake, "read_thread", read_thread)

    seen = await walk_to_completion(rig, start=10, step=63, walks=14)

    assert seen[-1] == "completed"
    [entry] = _report_entries(rig, kind="owed")
    assert (entry["outcome"], entry["bot_last_word"]) == ("owed_at_limit", "One more thing.")


async def test_a_reply_round_report_keeps_what_an_earlier_measurement_found(rig, monkeypatch):
    """T1 is flagged below the loop's severities (never fixed), T2 is declined and owed a look; the
    round's triage is skipped, so the last record holds no finding: the report still names T1."""
    t2 = bot_thread("T2", note("N2", "a", "rename y"))
    fake = await filed(rig, monkeypatch, t1(), t2)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    decline_t2 = {"thread": "T2", "kind": "declined", "body": "The call is right."}
    rig.verdicts = [
        {"findings": [{**FLAG, "file": "calc.py", "severity": "minor"}], "replies": [decline_t2]}
    ]

    seen = await walk_to_completion(rig, start=10, step=63, walks=14)

    assert seen[-1] == "completed"
    assert len(events_of(rig, "reply_round_started")) == 1
    [entry] = _report_entries(rig, kind="finding")
    assert (entry["thread"], entry["outcome"], entry["why"]) == (
        "T1",
        "not_fixed",
        "never reached a fix",
    )


async def test_a_waiting_fix_reply_survives_a_round(rig, monkeypatch):
    """T1 is fixed but its commit is never pushed (no CI step), so its "Fixed in" waits; T2,
    declined, is owed a look and starts a round at the completion exit. The fix reply is neither
    ended nor posted there, and is ended only when the node finally completes."""
    t2 = bot_thread("T2", note("N2", "a", "rename y"))
    on_repaired = bot_thread("T1", note("N1", "a", "rename x"), file="repaired.txt")
    fake = await filed(rig, monkeypatch, on_repaired, t2)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    decline_t2 = {"thread": "T2", "kind": "declined", "body": "The call is right."}
    rig.verdicts = [{"findings": [FLAG], "replies": [decline_t2]}, []]
    rig.repairs = [{"replies": [{"thread": "T1", "body": "Renamed it."}]}]
    at_the_round: list = []
    seen = []
    for n in range(14):
        rig.clock(10 + n * 63)
        seen.append(await rig.walk())
        if len(events_of(rig, "reply_round_started")) and not at_the_round:
            ended = [e["payload"]["why"] for e in events_of(rig, "reply_ended")]
            at_the_round.append((list(fake.writes), ended))
        if seen[-1] == "completed":
            break

    assert seen[-1] == "completed"
    assert at_the_round == [([("reply", "T2")], [])]  # nothing about T1 at the round's exit
    assert [e["payload"]["why"] for e in events_of(rig, "reply_ended")] == ["not_pushed"]
    assert fake.writes == [("reply", "T2")]


async def test_a_node_with_no_fix_loop_never_starts_a_round(rig, monkeypatch):
    """Nothing here can act on an answer: replies are inert on a node with no fix loop, and no round
    starts."""
    fake = await filed(rig, monkeypatch, t1(), loop=False)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    rig.verdicts = [DECLINE]

    seen = await walk_to_completion(rig, start=10, step=63, walks=8)

    assert seen[-1] == "completed"
    assert events_of(rig, "reply_round_started") == []
    assert [e["payload"]["reason"] for e in events_of(rig, "replies_inert")] == ["no_fix_loop"]
    assert fake.writes == []


async def test_replies_off_starts_no_round_and_reads_nothing(rig, monkeypatch):
    """With replies off nothing is asked of the forge for a round, and no head is read for one."""
    fake = await filed(rig, monkeypatch, t1(), bot=BotReviewer(bot="a"))
    reads = counting(fake, monkeypatch, "read_thread")
    rig.verdicts = [DECLINE]

    def never(*_):
        raise AssertionError("the head was asked for")

    monkeypatch.setattr(reply_round, "item_head", never)

    seen = await walk_to_completion(rig, start=10, step=63, walks=8)

    assert seen[-1] == "completed"
    assert events_of(rig, "reply_round_started") == [] and reads == []


async def test_the_owed_check_makes_no_forge_call_without_a_posted_reply(rig, monkeypatch):
    """Replies are on, but the triage writes none: at completion nothing is read for a round."""
    fake = await filed(rig, monkeypatch, t1())
    reads = counting(fake, monkeypatch, "read_thread")
    mrs = counting(fake, monkeypatch, "mr_details")
    rig.verdicts = [[]]

    seen = await walk_to_completion(rig, start=10, step=63, walks=8)

    assert seen[-1] == "completed"
    assert (reads, mrs) == ([], [])
    assert events_of(rig, "reply_round_started") == []


def _viewer_unreadable_after_the_reply(fake):
    read = fake.viewer

    async def viewer(*, repo):
        if fake.writes:
            raise ForgeError("fake: unreadable")
        return await read(repo=repo)

    fake.viewer = viewer


def _merge_request_closed_after_the_reply(fake):
    read = fake.mr_details

    async def mr_details(*, repo, branch):
        mr = await read(repo=repo, branch=branch)
        return replace(mr, state="closed") if fake.writes else mr

    fake.mr_details = mr_details


def _read_thread_raises_after_the_reply(fake):
    read = fake.read_thread

    async def read_thread(*, repo, branch, thread_id):
        if fake.writes:
            raise ForgeError("fake: unreadable")
        return await read(repo=repo, branch=branch, thread_id=thread_id)

    fake.read_thread = read_thread


@pytest.mark.parametrize(
    "cannot, listed",
    [
        (_viewer_unreadable_after_the_reply, True),
        (_merge_request_closed_after_the_reply, False),
        (_read_thread_raises_after_the_reply, True),
    ],
    ids=["viewer-unreadable", "merge-request-closed", "read-thread-raises"],
)
async def test_an_unreadable_viewer_closed_request_or_failed_read_is_not_owed(
    rig, monkeypatch, cannot, listed
):
    """What the owed check cannot read at the end of the node is not owed: the node completes with
    no round and nothing parked, and the report lists the thread it could not read (a closed merge
    request lists nothing: there is nothing to read)."""
    fake = await filed(rig, monkeypatch, t1())
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    rig.verdicts = [DECLINE]
    cannot(fake)

    seen = await walk_to_completion(rig, start=10, step=63, walks=8)

    assert seen[-1] == "completed"
    assert events_of(rig, "reply_round_started") == []
    entries = _report_entries(rig, kind="owed")
    assert [(e["thread"], e["outcome"]) for e in entries] == (
        [("T1", "not_read")] if listed else []
    )


async def test_an_error_in_the_round_decision_still_completes_the_node(rig, monkeypatch):
    """A failure while deciding the round completes the node as if none were owed: what waits is
    ended and the report is written."""
    t2 = bot_thread("T2", note("N2", "a", "rename y"))
    on_repaired = bot_thread("T1", note("N1", "a", "rename x"), file="repaired.txt")
    fake = await filed(rig, monkeypatch, on_repaired, t2)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    decline_t2 = {"thread": "T2", "kind": "declined", "body": "The call is right."}
    rig.verdicts = [{"findings": [FLAG], "replies": [decline_t2]}, []]
    rig.repairs = [{"replies": [{"thread": "T1", "body": "Renamed it."}]}]

    async def broken(self, candidates):
        raise RuntimeError("the owed check broke")

    monkeypatch.setattr(replies._Point, "owed", broken)

    seen = await walk_to_completion(rig, start=10, step=63, walks=8)

    assert seen[-1] == "completed"
    assert events_of(rig, "reply_round_started") == []
    assert [e["payload"]["why"] for e in events_of(rig, "reply_ended")] == ["not_pushed"]
    assert {e["thread"] for e in report_of(rig)["entries"]} >= {"T1", "T2"}


async def test_the_owed_check_reads_the_review_tasks_timeout(rig, monkeypatch):
    """The review task allows the bot a minute: Kraft's reply, a minute old and unanswered, is not
    owed (the node's own five minutes would call it owed), so no round starts."""

    def a_minute(node):
        node["steps"][0]["tasks"][0]["policy"]["reply_timeout_minutes"] = 1

    fake = await filed(rig, monkeypatch, t1(), tweak=a_minute)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    rig.verdicts = [DECLINE]

    seen = await walk_to_completion(rig, start=10, step=63, walks=8)

    assert seen[-1] == "completed"
    assert events_of(rig, "reply_round_started") == []
    assert fake.writes == [("reply", "T1")]


async def test_an_answer_at_the_cap_is_read_and_judged_not_answered(rig, monkeypatch):
    """With one reply allowed per thread, the bot answers Kraft's one: a round still runs, the
    triage reads the answer and writes another decline, and the gate refuses it at the cap."""
    fake = await filed(rig, monkeypatch, t1(), policy={"reply_cap_per_thread": 1})
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    again = {"findings": [], "replies": [{**DECLINE["replies"][0], "body": "Still the call."}]}
    rig.verdicts = [DECLINE, again]

    seen = await _walk_answering(rig, fake, ["Are you sure?"])

    assert seen[-1] == "completed"
    assert len(events_of(rig, "reply_round_started")) == 1
    assert fake.writes == [("reply", "T1")]
    assert [e["payload"]["why"] for e in events_of(rig, "reply_ended")] == ["cap"]


def _ci_in_the_round(fake, rig, *, red_at=None):
    """Green until a reply round starts (the first measurement's CI), then pending; red from
    `red_at` seconds on while `on_failure`'s repair has not run, green after it."""
    read = fake.ci_status

    async def ci_status(**kw):
        in_round = bool(events_of(rig, "reply_round_started"))
        repaired = any(t.endswith("repair_checks") for t in rig.triaged)
        red = in_round and not repaired and red_at is not None and waits.now() >= rig.moment(red_at)
        fake.ci_states = [
            "failed" if red else "pending" if in_round and not repaired else "success"
        ]
        fake.ci_failed_jobs = [_RED if red else ()]
        return await read(**kw)

    fake.ci_status = ci_status


def _ci_task_waits(rig):
    return [
        e
        for e in events_of(rig, "external_wait_ended")
        if e["payload"]["task"].endswith(".await_ci")
    ]


async def test_a_pending_pipeline_in_a_reply_round_completes_at_the_reply_deadline(
    rig, monkeypatch
):
    """`a-reply-round-never-stops-the-item-on-pending-ci`: a check a reply's workflow added keeps
    the head's CI pending through the round. The CI look parks while the round has time, then
    completes the node on the earlier green measurement: its wait ends `timed_out` but the session
    is `done`, never `capped_out`, and the report says CI was still pending."""
    fake = await filed(rig, monkeypatch, t1(), watch_ci=True, tweak=_early)
    monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    _ci_in_the_round(fake, rig)
    rig.verdicts = [DECLINE]

    seen = await walk_to_completion(rig, start=10, step=63, walks=14)

    assert seen[-1] == "completed"
    [started] = events_of(rig, "reply_round_started")
    assert [e["payload"]["outcome"] for e in _ci_task_waits(rig)] == ["settled", "timed_out"]
    looks = [s for s in rig.item.sessions("feedback") if s["hook_point"].endswith(".await_ci")]
    assert [s["status"] for s in looks][-1] == "done"
    assert "capped_out" not in {s["status"] for s in looks}
    [entry] = _report_entries(rig, kind="ci")
    assert entry["outcome"] == "ci_pending"
    assert started["payload"]["deadline"] and fake.writes == [("reply", "T1")]


async def test_a_pipeline_that_goes_red_in_a_reply_round_still_fails(rig, monkeypatch):
    """The same pending check goes red before the round's time is up: the round's measurement fails
    and takes the node's `on_failure`, as any measurement's red pipeline does."""
    fake = await filed(rig, monkeypatch, t1(), watch_ci=True, tweak=_early)
    monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    live_bot(fake, bot_on(), rig.moment(1), monkeypatch)
    _ci_in_the_round(fake, rig, red_at=200)
    rig.verdicts = [DECLINE, []]

    seen = await walk_to_completion(rig, start=10, step=63, walks=14)

    assert seen[-1] == "completed"
    assert any(t.endswith("repair_checks") for t in rig.triaged)
    assert _report_entries(rig, kind="ci") == []
    assert [e["payload"]["outcome"] for e in _ci_task_waits(rig)].count("timed_out") == 0


async def test_a_reply_posted_on_one_head_is_read_on_the_next(
    item_on, database, run_dirs, monkeypatch
):
    """The candidates are the threads Kraft ever posted a reply of this node in, whatever head the
    reply was made on: the bot's answer, created after the latest triage's read of the thread on
    the current head, owes it a look."""
    it = await item_on([review_node(30, reviewers=(bot_on(),), loop=True)])
    node = store.materialized_chain_of(it.row()).chain.nodes[0]
    head = reply_round.item_head(it.row(), it.repo)
    # The latest triage, on the current head, read the bots at 00:02.
    await it.session("s1", "feedback.review_feedback.triage", "done", head_sha=head)
    copy = it.files("s1").bot_feedback
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(json.dumps({"head": head, "read_at": "2026-01-01T00:02:00Z", "reviewers": []}))
    # Kraft's decline, made on an earlier commit, and the bot's answer after the read.
    iid = record.new_id(it.id)
    intent = {
        "op": "reply",
        "node_id": "feedback",
        "thread": "T1",
        "kind": "declined",
        "head": "0" * 40,
        "round": 0,
        "session": "s0",
        "run": 0,
        "body_sha256": "x" * 64,
    }
    posted = forge.Posted(("1",), "2026-01-01T00:00:20Z", "Declined:\n\nno")

    def seed(c):
        record.intend(c, it.id, iid, intent)
        record.posted(c, it.id, iid, posted, via="send")

    await database.write(seed)
    _, thread = bot_thread(
        "T1",
        note("N1", "a", "fix"),
        kraft_note("N2", it.id),
        note("N3", "a", "are you sure?", at="2026-01-01T00:03:00Z"),
    )
    fake = forge.FakeForge(threads={"T1": thread})
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    async def details(*, repo, branch):
        return forge.MRDetails(1, "u", "open", "t", "", True, (), branch, "main", head)

    monkeypatch.setattr(fake, "mr_details", details)
    launch = executor.LaunchContext(repo_entry=entry_of({"setup_command": "", "forge": "github"}))

    started = await replies.posting_point(
        database, run_dirs, it.id, node, it.row(), it.repo, launch, completing=True
    )

    assert (started.head, started.threads) == (head, ("T1",))
    [event] = it.events("reply_round_started")
    assert event["payload"]["threads"] == ["T1"]
