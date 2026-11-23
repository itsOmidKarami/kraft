"""Resolving a bot thread at node completion: only a thread whose fix Kraft confirmed, from a fresh
read, and never a decline or an answer. Walked through the executor against a `FakeForge`."""

# ruff: noqa: F811 -- `rig` is an imported fixture, taken by name.

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from support.bot_threads import bot_thread, note
from support.harness import git
from support.live_bot import bot_says, live_bot
from support.review_rig import (  # noqa: F401
    DECLINE,
    FIXED,
    FLAG,
    bot_on,
    events_of,
    filed,
    fix_walk,
    report_of,
    review_node,
    rig,
    t1,
    walk_to_completion,
)

from kraft import events, store, waits
from kraft.adapters.forge.models import LOST, ForgeError
from kraft.adapters.forge.replies import record
from kraft.automated_review import BotReviewer
from kraft.executor import replies
from kraft.vocab import ChainEvent, ForgeEvent

#: The triage raises T2's finding, and a repair that fixes it.
FLAG_T2 = {**FLAG, "message": "rename y", "threads": ["T2"]}
FIXED_T2 = {"replies": [{"thread": "T2", "body": "Renamed it too."}]}
DECLINE_T1 = DECLINE["replies"][0]


def on():
    """A bot that replies and resolves fixed threads."""
    return bot_on(resolve_fixed=True)


def _resolve_entries(rig) -> list[dict]:
    return [e for e in report_of(rig)["entries"] if e["kind"] == "resolve"]


def _outcome(rig, tid="T1") -> tuple:
    [entry] = [e for e in _resolve_entries(rig) if e["thread"] == tid]
    return entry["outcome"], entry["why"]


async def fixed_thread(rig, monkeypatch, **kw):
    """A confirmed fix of T1 for a bot that resolves: the first triage raises T1's finding, the
    repair commits and writes `FIXED`, the second triage writes nothing for T1, "Fixed in" posts."""
    return await fix_walk(rig, monkeypatch, [], reviewers=(on(),), **kw)


def _live(fake, monkeypatch, rig, bot=None):
    live_bot(fake, bot or on(), rig.moment(1), monkeypatch)


async def _two_threads(rig, monkeypatch, verdicts, repairs, *, first_file="repaired.txt", **kw):
    """Threads T1 and T2 of a live bot on the file the repair changes, the verdicts and repairs
    as given, walked to completion."""
    one = bot_thread("T1", note("N1", "a", "rename x"), file=first_file)
    two = bot_thread("T2", note("N2", "a", "rename y"), file="repaired.txt")
    fake = await filed(rig, monkeypatch, one, two, bot=on(), watch_ci=True, **kw)
    _live(fake, monkeypatch, rig)
    rig.verdicts, rig.repairs = verdicts, repairs
    seen = await walk_to_completion(rig, start=10, step=63, walks=30)
    assert seen[-1] == "completed"
    return fake


async def test_a_confirmed_fix_the_bot_never_answers_is_resolved(rig, monkeypatch):
    fake = await fixed_thread(rig, monkeypatch)

    assert fake.writes == [("reply", "T1"), ("resolve", "T1")]  # "Fixed in", then the resolve
    assert fake.threads["T1"].resolved
    assert _outcome(rig) == ("resolved", None)


async def test_a_decline_the_bot_never_answers_stays_open(rig, monkeypatch):
    fake = await filed(rig, monkeypatch, t1(), bot=on())
    _live(fake, monkeypatch, rig)
    rig.verdicts = [DECLINE]

    assert (await walk_to_completion(rig))[-1] == "completed"

    assert fake.writes == [("reply", "T1")]  # no resolve: a person decides a decline
    assert not fake.threads["T1"].resolved
    assert _outcome(rig) == ("left_open", "not_fixed")


async def test_an_answered_thread_stays_open(rig, monkeypatch):
    fake = await filed(rig, monkeypatch, t1(), bot=on())
    _live(fake, monkeypatch, rig)
    rig.verdicts = [
        {"findings": [], "replies": [{**DECLINE_T1, "kind": "answered", "body": "It holds."}]}
    ]

    assert (await walk_to_completion(rig))[-1] == "completed"

    assert fake.writes == [("reply", "T1")]
    assert not fake.threads["T1"].resolved
    assert _outcome(rig) == ("left_open", "not_fixed")


async def test_a_decline_repeated_on_a_second_head_stays_open(rig, monkeypatch):
    """T1 is declined on head 1; T2's fix makes head 2, whose triage declines T1 again. Kraft's
    decline is the thread's last note, so the second is withheld: T1 stays open, T2 is resolved."""
    again = {"findings": [], "replies": [{**DECLINE_T1, "body": "Still the call."}]}
    fake = await _two_threads(
        rig,
        monkeypatch,
        [{"findings": [FLAG_T2], "replies": [DECLINE_T1]}, again],
        [FIXED_T2],
        first_file="calc.py",
    )

    assert not fake.threads["T1"].resolved and fake.threads["T2"].resolved
    assert [w for w in fake.writes if w[0] == "resolve"] == [("resolve", "T2")]
    assert _outcome(rig, "T1") == ("left_open", "not_fixed")
    assert _outcome(rig, "T2") == ("resolved", None)


async def test_a_declined_thread_fixed_in_a_later_cycle_is_resolved(rig, monkeypatch):
    """Declined first, the bot pushes back, the round's triage raises a finding, a cycle fixes it
    and "Fixed in" posts, the bot is silent: Kraft's latest note is the fix."""
    thread = bot_thread("T1", note("N1", "a", "rename x"), file="repaired.txt")
    fake = await filed(rig, monkeypatch, thread, bot=on(), watch_ci=True)
    _live(fake, monkeypatch, rig)
    rig.verdicts = [DECLINE, {"findings": [FLAG]}, []]
    rig.repairs = [FIXED]
    pushed = False
    for n in range(30):
        rig.clock(10 + n * 63)
        status = await rig.walk()
        if status == "completed":
            break
        if events_of(rig, "reply_round_started") and not pushed:
            pushed = True  # the bot answers just after the clock's now, once the round waits
            at = (waits.now() + timedelta(seconds=1)).isoformat()
            bot_says(fake, "T1", note("P1", "a", "Are you sure?", at=at))
    assert status == "completed" and pushed

    assert fake.writes == [("reply", "T1"), ("reply", "T1"), ("resolve", "T1")]
    assert fake.threads["T1"].resolved
    assert _outcome(rig) == ("resolved", None)


async def test_a_fix_the_triage_later_declines_stays_open(rig, monkeypatch):
    """T1 is fixed and "Fixed in" posts; a later triage (T2's fix makes another head) declines T1:
    withheld, Kraft's note is last, and the thread no longer stands as a confirmed fix."""
    again = {"findings": [], "replies": [{**DECLINE_T1, "body": "Still the call."}]}
    fake = await _two_threads(
        rig,
        monkeypatch,
        [{"findings": [FLAG]}, {"findings": [FLAG_T2]}, again],
        [FIXED, FIXED_T2],
    )

    assert not fake.threads["T1"].resolved and fake.threads["T2"].resolved
    assert fake.writes.count(("reply", "T1")) == 1  # "Fixed in", and no decline after it
    assert _outcome(rig, "T1") == ("left_open", "not_fixed")


async def test_a_thread_with_a_below_severity_finding_is_left_open(rig, monkeypatch):
    """T1 is fixed and "Fixed in" posts; the last triage (T2's fix makes another head) raises a
    `minor` finding on T1, below the loop's severities, so no cycle takes it: it is still named."""
    minor = {**FLAG, "severity": "minor", "message": "rename x again"}
    fake = await _two_threads(
        rig,
        monkeypatch,
        [{"findings": [FLAG]}, {"findings": [FLAG_T2]}, {"findings": [minor]}],
        [FIXED, FIXED_T2],
    )

    assert not fake.threads["T1"].resolved and fake.threads["T2"].resolved
    assert _outcome(rig, "T1") == ("left_open", "finding_outstanding")


async def test_a_fix_the_bot_pushes_back_on_stays_open(rig, monkeypatch):
    """ "Fixed in" posts and the bot answers after every triage's read: nobody judged the answer."""

    def tweak(fake):
        _live(fake, monkeypatch, rig)
        read = fake.read_thread
        said = []

        async def read_thread(*, repo, branch, thread_id):
            if ("reply", "T1") in fake.writes and not said:
                said.append(1)  # dated an hour on: after any read the clock reaches
                bot_says(fake, "T1", note("L", "a", "No.", at="2026-01-01T01:00:00Z"))
            return await read(repo=repo, branch=branch, thread_id=thread_id)

        monkeypatch.setattr(fake, "read_thread", read_thread)

    fake = await fixed_thread(rig, monkeypatch, tweak=tweak)

    assert not fake.threads["T1"].resolved
    assert ("resolve", "T1") not in fake.writes
    assert _outcome(rig) == ("left_open", "answer_unread")


async def test_resolve_fixed_off_resolves_nothing(rig, monkeypatch):
    fake = await fix_walk(rig, monkeypatch, [], reviewers=(bot_on(),))

    assert fake.writes == [("reply", "T1")]  # "Fixed in", and nothing else
    assert not fake.threads["T1"].resolved
    assert _resolve_entries(rig) == []


async def test_only_the_bots_with_resolve_fixed_are_resolved(rig, monkeypatch):
    """Two bots take replies, one resolves; each has a confirmed fix. The collection files both
    threads under `a`, so the choice is made on who started each thread."""
    keeps = bot_thread("T1", note("N1", "a", "rename x"), file="repaired.txt")
    other = bot_thread("T2", note("N2", "b", "rename y"), file="repaired.txt")
    both = {
        "replies": [
            {"thread": "T1", "body": "Renamed it."},
            {"thread": "T2", "body": "Renamed it too."},
        ]
    }
    fake = await fix_walk(
        rig,
        monkeypatch,
        [],
        threads=[keeps, other],
        flags=[FLAG, FLAG_T2],
        repair=both,
        reviewers=(on(), BotReviewer(bot="b", replies=True)),
    )

    assert sorted(fake.writes) == [("reply", "T1"), ("reply", "T2"), ("resolve", "T1")]
    assert fake.threads["T1"].resolved and not fake.threads["T2"].resolved
    assert [e["thread"] for e in _resolve_entries(rig)] == ["T1"]


async def test_a_bot_answer_nobody_read_is_not_resolved_over(rig, monkeypatch):
    """One round per head: the round's triage reads the bot, which answers again after that read;
    the limit is reached and the node completes with the answer unjudged."""

    def tweak(fake):
        _live(fake, monkeypatch, rig)
        read = fake.read_thread
        said = []

        async def read_thread(*, repo, branch, thread_id):
            if events_of(rig, "reply_round_started") and not said:
                said.append(1)  # the owed check at the end of the round is the first read
                bot_says(fake, "T1", note("L", "a", "Still no.", at="2026-01-01T01:00:00Z"))
            return await read(repo=repo, branch=branch, thread_id=thread_id)

        monkeypatch.setattr(fake, "read_thread", read_thread)

    fake = await fixed_thread(rig, monkeypatch, tweak=tweak, policy={"reply_rounds_per_head": 1})

    assert [e["outcome"] for e in report_of(rig)["entries"] if e["kind"] == "owed"] == [
        "owed_at_limit"
    ]
    assert not fake.threads["T1"].resolved
    assert _outcome(rig) == ("left_open", "answer_unread")


async def test_a_thread_the_bot_resolved_is_left_alone(rig, monkeypatch):
    def tweak(fake):
        read = fake.read_thread

        async def read_thread(*, repo, branch, thread_id):
            if ("reply", "T1") in fake.writes:  # the bot settles it once "Fixed in" is up
                fake.threads["T1"] = replace(fake.threads["T1"], resolved=True)
            return await read(repo=repo, branch=branch, thread_id=thread_id)

        monkeypatch.setattr(fake, "read_thread", read_thread)

    fake = await fixed_thread(rig, monkeypatch, tweak=tweak)

    assert fake.writes == [("reply", "T1")]
    assert _resolve_entries(rig) == []
    assert [e["payload"]["op"] for e in events_of(rig, ForgeEvent.REPLY_INTENDED)] == ["reply"]


def _repeating(monkeypatch, times=1) -> list:
    """A completion's posting point that ends the node (no round) is followed by `times` more of
    the same run, as when the walk is interrupted before it records the node complete. Returns
    the arguments of every posting point, to make one more by hand."""
    inner = replies.posting_point
    calls: list = []

    async def posting_point(*args, **kw):
        calls.append((args, kw))
        started = await inner(*args, **kw)
        if kw.get("completing") and started is None:
            for _ in range(times):
                await inner(*args, **kw)
        return started

    monkeypatch.setattr(replies, "posting_point", posting_point)
    return calls


def _resolve_intents(rig) -> list[record.Intent]:
    held = rig.item.database.read(lambda c: record.read(c, rig.item.id))
    return [i for i in held.values() if i.payload["op"] == "resolve"]


async def test_a_resolve_whose_answer_is_lost_is_not_sent_twice(rig, monkeypatch):
    """The resolve lands and its answer is lost: the intent is unclear. The next posting point
    reconciles it (no marker: not found) and the gate finds the thread resolved: no second write."""
    _repeating(monkeypatch)
    fake = await fixed_thread(rig, monkeypatch, tweak=lambda f: f.write_script.extend(["ok", LOST]))

    assert fake.writes.count(("resolve", "T1")) == 1
    [intent] = _resolve_intents(rig)
    assert (intent.state, intent.why) == ("refused", "resolved")


async def test_a_pending_resolve_is_sent_at_the_next_posting_point(rig, monkeypatch):
    """The forge refuses the first resolve for the rate: the intent stays pending, the thread
    open, and the next posting point sends it again, without a second intent."""
    _repeating(monkeypatch)
    slow = ForgeError("slow down", reason="rate_limited", retry_after=0)
    fake = await fixed_thread(rig, monkeypatch, tweak=lambda f: f.write_script.extend(["ok", slow]))

    assert fake.threads["T1"].resolved
    assert [i.state for i in _resolve_intents(rig)] == ["posted"]
    assert fake.writes.count(("resolve", "T1")) == 2  # the refused attempt and the one that landed


async def test_a_resolve_intent_is_never_swept_for_a_marker(rig, monkeypatch):
    """A resolve sent again after an unclear send is posted with `ever_unclear`; it has no marker,
    so a later posting point never reads the thread to look for a copy of it."""
    _repeating(monkeypatch, times=2)
    lost = ForgeError("fake: lost the answer", status=502)
    reads = []

    def tweak(fake):
        fake.write_script.extend(["ok", lost])
        inner = fake.read_thread

        async def read_thread(*, repo, branch, thread_id):
            reads.append(len(fake.writes))
            return await inner(repo=repo, branch=branch, thread_id=thread_id)

        monkeypatch.setattr(fake, "read_thread", read_thread)

    fake = await fixed_thread(rig, monkeypatch, tweak=tweak)

    [intent] = _resolve_intents(rig)
    assert (intent.state, intent.ever_unclear) == ("posted", True)
    # Reads after the resolve was posted: the third posting point's, which holds only the round's
    # owed check and the candidate read it skips. A sweep of the resolve would add one.
    assert reads[-1] == len(fake.writes) and reads.count(len(fake.writes)) == 1
    assert not [w for w in fake.writes if w[0] == "delete"]


async def test_a_thread_kraft_resolved_is_not_resolved_again_after_a_retry(rig, monkeypatch):
    """Kraft resolved T1 in run 1; the run is forked (a retry), someone reopened the thread, and a
    triage of the new run read it. The check reads every run: the thread stays open, `reopened`."""
    calls = _repeating(monkeypatch, times=0)
    fake = await fixed_thread(rig, monkeypatch)
    assert fake.threads["T1"].resolved
    fake.threads["T1"] = replace(fake.threads["T1"], resolved=False)
    it = rig.item
    await it.database.write(lambda c: events.append(c, it.id, "run_forked", {}))
    head = git(it.worktree, "rev-parse", "HEAD")
    _, _, result = await it.session("s2", "feedback.review_feedback.triage", "done", head_sha=head)
    Path(result).write_text(json.dumps({"status": "done", "findings": []}))
    collection = {
        "head": head,
        "read_at": waits.now().isoformat(),
        "reviewers": [{"reviewer": "bot:a", "replies": True, "threads": []}],
    }
    copy = it.files("s2").bot_feedback
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(json.dumps(collection))
    rig.clock(3600)
    args, kw = [c for c in calls if c[1].get("completing")][-1]

    await replies.posting_point(*args, **kw)

    assert fake.writes.count(("resolve", "T1")) == 1
    assert _outcome(rig) == ("left_open", "reopened")


def _at_the_resolve(monkeypatch, act):
    """`act(fake)` as `_Point.resolve` begins: after the summary, before any candidate read."""
    inner = replies._Point.resolve

    async def resolve(self):
        act(self.forge)
        return await inner(self)

    monkeypatch.setattr(replies._Point, "resolve", resolve)


def _after_the_intent(monkeypatch, act) -> None:
    """`act()` once the resolve intent is made: between the candidate check and the gate."""
    inner = replies._Point.intend

    async def intend(self, key, payload, rendered_for):
        made = await inner(self, key, payload, rendered_for)
        if payload["op"] == "resolve":
            act()
        return made

    monkeypatch.setattr(replies._Point, "intend", intend)


def _changed(fake, **change) -> None:
    """From now on the merge request reads with `change` (another head, or closed)."""
    inner = fake.mr_details

    async def mr_details(**kw):
        return replace(await inner(**kw), **change)

    fake.mr_details = mr_details


async def test_the_resolve_gate_checks_the_head_again(rig, monkeypatch):
    """A push lands between the candidate check and the send: the gate reads the merge request
    afresh, sees another head and refuses; nothing is written."""

    def tweak(fake):
        _after_the_intent(monkeypatch, lambda: _changed(fake, head_sha="f" * 40))

    fake = await fixed_thread(rig, monkeypatch, tweak=tweak)

    assert ("resolve", "T1") not in fake.writes
    assert _outcome(rig) == ("left_open", "head_moved")


async def test_the_resolve_gate_refuses_a_closed_merge_request(rig, monkeypatch):

    def tweak(fake):
        _after_the_intent(monkeypatch, lambda: _changed(fake, state="closed"))

    fake = await fixed_thread(rig, monkeypatch, tweak=tweak)

    assert ("resolve", "T1") not in fake.writes
    assert _outcome(rig) == ("left_open", "mr_not_open")


async def test_resolve_reads_the_thread_afresh(rig, monkeypatch):
    """The candidate read shows a settled thread; the gate's read shows a bot answer created
    after the triage read it: refused, no write."""

    def tweak(fake):
        _after_the_intent(
            monkeypatch,
            lambda: bot_says(fake, "T1", note("L", "a", "No.", at="2026-01-01T01:00:00Z")),
        )

    fake = await fixed_thread(rig, monkeypatch, tweak=tweak)

    assert ("resolve", "T1") not in fake.writes
    assert _outcome(rig) == ("left_open", "answer_unread")


async def test_resolve_with_a_stale_head_leaves_threads_open(rig, monkeypatch):
    """The forge's head is not the triage's once "Fixed in" is up: nothing is resolved on a head
    no triage judged."""
    box = {}

    def tweak(fake):
        box["fake"] = fake

    def head():
        moved = ("reply", "T1") in box["fake"].writes
        return "f" * 40 if moved else git(rig.item.worktree, "rev-parse", "HEAD")

    fake = await fix_walk(rig, monkeypatch, [], reviewers=(on(),), tweak=tweak, head=head)

    assert ("resolve", "T1") not in fake.writes
    assert _outcome(rig) == ("left_open", "no_latest_triage")


@pytest.mark.parametrize(
    "act, why",
    [
        pytest.param(lambda fake: fake.threads.pop("T1"), "not_found", id="thread-gone"),
        pytest.param(
            lambda fake: setattr(fake, "read_thread", _fail), "not_read", id="read-failed"
        ),
        pytest.param(
            lambda fake: _changed(fake, state="closed"), "mr_not_open", id="merge-request-closed"
        ),
    ],
)
async def test_every_refusal_is_listed_left_open(rig, monkeypatch, act, why):
    _at_the_resolve(monkeypatch, act)

    fake = await fixed_thread(rig, monkeypatch)

    assert ("resolve", "T1") not in fake.writes
    assert _outcome(rig) == ("left_open", why)


async def _fail(**_):
    raise ForgeError("fake: the read failed")


async def test_resolve_only_runs_when_no_round_starts(rig, monkeypatch):
    """ "Fixed in" waits for the bot's answer in a round first: no resolve is written at the
    completion that starts it, only at the one that does not."""
    at_a_round, box = [], {}
    inner = replies.posting_point

    async def posting_point(*args, **kw):
        started = await inner(*args, **kw)
        if kw.get("completing") and started is not None:
            at_a_round.append(list(box["fake"].writes))
        return started

    monkeypatch.setattr(replies, "posting_point", posting_point)

    fake = await fixed_thread(rig, monkeypatch, tweak=lambda f: box.update(fake=f))

    assert at_a_round and all(("resolve", "T1") not in w for w in at_a_round)
    assert fake.writes[-1] == ("resolve", "T1")


_RAISED = {**FLAG, "threads": ["T1"]}
_MEASURED, _JUDGED = ChainEvent.FINDINGS_MEASURED, ChainEvent.JUDGE_VERDICT


@pytest.mark.parametrize(
    "seed, expected",
    [
        pytest.param(
            {"record": {"findings": [_RAISED]}}, ({"T1"}, False), id="last-record-findings"
        ),
        pytest.param(
            {"record": {"findings": [], "unmeasured": [{**_RAISED, "severity": "minor"}]}},
            ({"T1"}, False),
            id="last-record-unmeasured",
        ),
        pytest.param(
            {"judge": {"verdict": "stop_downgrade", "findings": [_RAISED]}},
            ({"T1"}, False),
            id="stop-downgrade-verdict",
        ),
        pytest.param({"result": [_RAISED]}, ({"T1"}, False), id="latest-triage-result-file"),
        pytest.param(
            {"record": {"findings": [], "dropped": 1}}, (set(), True), id="dropped-record"
        ),
        pytest.param({"record": {"findings": []}}, (set(), False), id="nothing-outstanding"),
    ],
)
async def test_outstanding_reads_every_source(item_on, database, run_dirs, seed, expected):
    """Each source that can name a thread, alone: no other source names T1 in a row."""
    it = await item_on([review_node(30, reviewers=(bot_on(),), loop=True)])
    node = store.materialized_chain_of(it.row()).chain.nodes[0]
    point = replies._Point(
        database, run_dirs, it.id, node, it.row(), it.worktree, None, ("a",), ("a",), 0, ""
    )

    def seeded(type_, payload):
        return database.write(
            lambda c: events.append(c, it.id, type_, {"node_id": node.id, **payload})
        )

    if "record" in seed:
        await seeded(_MEASURED, seed["record"])
    if "judge" in seed:
        await seeded(_JUDGED, seed["judge"])
    if "result" in seed:
        _, _, result = await it.session(
            "s1", "feedback.review_feedback.triage", "done", head_sha="h"
        )
        Path(result).write_text(json.dumps({"status": "done", "findings": seed["result"]}))

    assert point.outstanding() == expected
