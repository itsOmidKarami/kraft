"""When each automated reviewer has finished with the latest push
(`settle.decide`): a bot by its quiet after its last activity, a check by
completing, either by the wait's timeout. Pure: the clock and the memory the
last check left are arguments. Last, which bots a run of settled reviews found
silent (`silent_runs`), read off the events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from support.bot_threads import VIEWER, kraft_note, note

from kraft import events
from kraft.adapters.forge import settle
from kraft.adapters.forge.models import ForgeNote, ReviewerFeedback
from kraft.adapters.forge.replies import facts, owed
from kraft.automated_review import BotReviewer, CheckReviewer
from kraft.vocab import ForgeEvent

SINCE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
GRACE = timedelta(seconds=60)


def at(seconds: float) -> datetime:
    """`seconds` after the push."""
    return SINCE + timedelta(seconds=seconds)


def bot(name="x", *, active=None, summaries=(), threads=(), notes=()) -> ReviewerFeedback:
    return ReviewerFeedback(
        BotReviewer(bot=name),
        None if active is None else at(active),
        tuple(summaries),
        tuple(
            {"id": t, "file": "a.py", "line": 1, "body": f"fix {t}"}
            | ({"notes": notes} if notes else {})
            for t in threads
        ),
    )


def check(finished, conclusion=None, name="scan") -> ReviewerFeedback:
    return ReviewerFeedback(
        CheckReviewer(check=name),
        None,
        check={"name": name, "finished": finished, "conclusion": conclusion, "output": "boom"},
    )


def comment(body, seconds) -> dict:
    return {"kind": "comment", "state": None, "body": body, "updated_at": at(seconds).isoformat()}


def mem(activity=None, threads=()) -> dict:
    return {
        "activity_at": None if activity is None else at(activity).isoformat(),
        "threads": list(threads),
    }


def decide(feedback, *, now, memory=None, timed_out=False, kraft=None):
    return settle.decide(
        feedback,
        since=SINCE,
        now=at(now),
        grace=GRACE,
        memory=memory or {},
        timed_out=timed_out,
        kraft=kraft,
    )


@pytest.mark.parametrize(
    "feedback, now, memory, timed_out, state, next_at, finished",
    [
        ([bot()], 30, None, False, "pending", None, None),
        ([bot(active=10)], 40, None, False, "pending", at(70), None),
        ([bot(active=10)], 71, None, False, "settled", None, ["activity"]),
        # The bot edited its summary for the old head just after the push.
        ([bot(active=0)], 100, None, False, "pending", None, None),
        # Last round's declined threads, seen on the first check, are a baseline.
        ([bot(threads=["a", "b"])], 100, None, False, "pending", None, None),
        # A later check that finds one resolved is activity, at that check.
        (
            [bot(threads=["a"])],
            40,
            {"bot:x": mem(threads=["a", "b"])},
            False,
            "pending",
            at(100),
            None,
        ),
        # The activity a past check saw is remembered when this read shows nothing newer.
        ([bot()], 71, {"bot:x": mem(activity=10)}, False, "settled", None, ["activity"]),
        ([bot()], 300, None, True, "settled", None, ["timeout"]),
        # One bot still in its grace holds the other, already settled, back.
        (
            [bot("x", active=10), bot("y", active=90)],
            100,
            None,
            False,
            "pending",
            at(150),
            None,
        ),
        ([check(True, "success")], 5, None, False, "settled", None, ["check"]),
        ([check(False)], 5, None, False, "pending", None, None),
        ([check(False)], 300, None, True, "settled", None, ["timeout"]),
    ],
    ids=[
        "no-activity-yet",
        "activity-inside-grace",
        "activity-then-quiet",
        "activity-at-the-push-ignored",
        "first-check-thread-set-is-baseline",
        "thread-resolved-between-checks-is-activity",
        "activity-is-remembered",
        "silent-at-timeout-settles",
        "one-bot-still-in-grace-holds-all",
        "check-finished",
        "check-cancelled-is-not-finished",
        "check-unfinished-at-timeout-settles",
    ],
)
def test_a_reviewer_settles(feedback, now, memory, timed_out, state, next_at, finished):
    result = decide(feedback, now=now, memory=memory, timed_out=timed_out)

    assert (result.state, result.next_at) == (state, next_at)
    assert [e["finished"] for e in result.collection] == (finished or [])


@pytest.mark.parametrize(
    "feedback, now, memory, key, expected",
    [
        ([bot(threads=["a", "b"])], 100, None, "bot:x", mem(threads=["a", "b"])),
        (
            [bot(threads=["a"])],
            40,
            {"bot:x": mem(threads=["a", "b"])},
            "bot:x",
            mem(activity=40, threads=["a"]),
        ),
        ([bot(active=10)], 20, None, "bot:x", mem(activity=10)),
    ],
    ids=["baseline-stored-without-activity", "resolved-thread-is-activity-now", "activity-is-kept"],
)
def test_what_a_check_remembers_for_the_next(feedback, now, memory, key, expected):
    assert decide(feedback, now=now, memory=memory).memory[key] == expected


_PLAIN_NOTE = (note("N1", "x", "fix"),)


@pytest.mark.parametrize(
    "notes, first, second, activity",
    [
        ((note("N1", "x", "fix"), kraft_note("N2", "w1")), (), (), None),
        (_PLAIN_NOTE, (), (), at(40)),
        # Kraft first posts in T1 between the two checks, and then the bot resolves it.
        (_PLAIN_NOTE, (), ("T1",), None),
    ],
    ids=[
        "bot-resolved-a-kraft-thread",
        "bot-resolved-another-thread",
        "posted-between-checks-then-resolved",
    ],
)
def test_a_thread_kraft_posted_in_leaves_the_compared_set(notes, first, second, activity):
    """The first check stores only the threads Kraft has not posted in, so the
    bot resolving one Kraft answered is not activity, whether the thread shows a Kraft
    note or only the record knows Kraft posted in it."""
    before = decide(
        [bot(threads=["T1"], notes=notes)],
        now=10,
        kraft=facts.Kraft("kraft-user", "w1", {}, frozenset(first)),
    )

    after = decide(
        [bot()],
        now=40,
        memory=before.memory,
        kraft=facts.Kraft("kraft-user", "w1", {}, frozenset(second)),
    )

    assert after.memory["bot:x"]["activity_at"] == (activity and activity.isoformat())


def test_only_the_latest_summary_of_a_kind_is_collected():
    older, newer = comment("first pass", 10), comment("second pass", 20)

    result = decide([bot(active=20, summaries=[older, newer])], now=100)

    assert [s["body"] for s in result.collection[0]["summaries"]] == ["second pass"]


def test_a_collection_names_each_reviewer_with_its_threads_and_failed_check():
    result = decide([bot(active=1, threads=["t1"]), check(True, "failure")], now=100)

    first, second = result.collection
    assert (first["reviewer"], [t["id"] for t in first["threads"]], first["check"]) == (
        "bot:x",
        ["t1"],
        None,
    )
    assert (second["reviewer"], second["check"]["conclusion"]) == ("check:scan", "failure")
    assert (first["replies"], second["replies"]) == (False, False)


def test_a_collection_says_which_bots_take_replies():
    feedback = ReviewerFeedback(BotReviewer(bot="a", replies=True), at(1), (), ())

    assert decide([feedback], now=100).collection[0]["replies"] is True


def test_a_check_that_succeeded_is_collected_without_its_output():
    assert decide([check(True, "success")], now=5).collection[0]["check"] is None


_ONE_NODE = [
    {"id": "n", "kind": "exec", "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}]}
]


@pytest.mark.parametrize(
    "reviews, named",
    [
        # Oldest first: (item, what the bot did, the workspace member it reviewed).
        (
            [("w1", "silent", None), ("w2", "silent", None), ("w3", "silent", None)],
            ["w3", "w2", "w1"],
        ),
        ([(f"w{n}", "silent", None) for n in range(1, 5)], ["w4", "w3", "w2"]),
        # A bot that has posted there even once is one that posts only on findings.
        ([("w1", "heard", None)] + [(f"w{n}", "silent", None) for n in range(2, 5)], None),
        ([("w1", "silent", None), ("w2", "silent", None), ("w3", "heard", None)], None),
        ([("w1", "silent", None), ("w2", "silent", None)], None),
        # Three heads of one item, through its fix loop, are one item.
        ([("w1", "silent", None)] * 3, None),
        # A member's reviews are counted apart from the root's.
        (
            [("w1", "silent", "repos/pkg"), ("w2", "silent", None), ("w3", "silent", "repos/pkg")],
            None,
        ),
        # The login was corrected: the newest review waits for another bot.
        (
            [(f"w{n}", "silent", None) for n in range(1, 4)] + [("w4", "heard", None, "ccr-bot")],
            None,
        ),
    ],
    ids=[
        "three-items",
        "more-than-three",
        "heard-once-before",
        "heard-last",
        "only-two-items",
        "one-item-three-heads",
        "a-member-apart",
        "since-corrected",
    ],
)
async def test_a_bot_is_named_only_when_it_never_posted_in_a_repository_across_items(
    item_on, database, reviews, named
):
    filed = {}
    for wid, did, mount, *login in reviews:
        if wid not in filed:
            filed[wid] = await item_on(_ONE_NODE, wid=wid)
        payload = {"repo": "checkout", "mount": mount, "heard": [], "silent": []}
        payload[did] = login or ["ccr"]
        await database.write(
            lambda c, w=wid, p=payload: events.append(c, w, ForgeEvent.AUTOMATED_REVIEW_SETTLED, p)
        )

    got = database.read(settle.silent_runs)

    repo = str(filed["w1"].repo)
    assert got == ([] if named is None else [{"repo": repo, "bot": "ccr", "items": named}])


def test_each_collected_note_says_whose_it_is():
    notes = (
        ForgeNote("N1", "1", "x", "Bot", "fix it", "2026-01-01T00:00:01Z", "u", None, "51"),
        ForgeNote("N2", "2", "x", "User", "no", "2026-01-01T00:00:02Z", "u", "N1", "52"),
    )
    [entry] = decide([bot(active=1, threads=["t1"], notes=notes)], now=100).collection

    assert entry["threads"][0]["notes"] == [
        {
            "id": "N1",
            "author": "x",
            "by": "bot",
            "body": "fix it",
            "created_at": "2026-01-01T00:00:01Z",
            "reply_to": None,
            "review_id": "51",
        },
        {
            "id": "N2",
            "author": "x",
            "by": "person",
            "body": "no",
            "created_at": "2026-01-01T00:00:02Z",
            "reply_to": "N1",
            "review_id": "52",
        },
    ]
    assert entry["threads"][0]["body"] == "fix t1"  # unchanged


def _said(nid, seconds, author="x"):
    return note(nid, author, "text", at=at(seconds).isoformat())


def _kraft_said(nid, seconds):
    return kraft_note(nid, "w1", at=at(seconds).isoformat())


def _replying(*threads, replies=True):
    """One bot's feedback whose threads are `(id, notes)`, built in the test."""
    return lambda: ReviewerFeedback(
        BotReviewer(bot="x", replies=replies),
        None,
        (),
        tuple(
            {"id": tid, "file": "a.py", "line": 1, "body": "fix", "notes": notes}
            for tid, notes in threads
        ),
    )


_TIMEOUT = timedelta(minutes=5)
_ANSWERED = ("T1", (_said("B", 0), _kraft_said("K", 10), _said("B2", 20)))
_SILENT = ("T2", (_said("B3", 0), _kraft_said("K2", 10)))
_LATER = ("T3", (_said("B4", 0), _kraft_said("K3", 70)))
_AHEAD = ("T4", (_said("B5", 0), _kraft_said("K4", 3600)))
_PERSON = (
    "T5",
    (_said("B6", 0), note("P", "alice", "I disagree", type_="User"), _kraft_said("K5", 10)),
)


@pytest.mark.parametrize(
    "feedback, threads, now, expected",
    [
        pytest.param(
            [
                _replying(
                    _ANSWERED, ("T2", (_said("B3", 0), _kraft_said("K2", 10), _said("B7", 30)))
                )
            ],
            ("T1", "T2"),
            60,
            (True, None),
            id="every-thread-answered",
        ),
        pytest.param(
            [_replying(_ANSWERED, _SILENT)],
            ("T1", "T2"),
            60,
            (False, at(10) + _TIMEOUT),
            id="one-thread-silent",
        ),
        pytest.param(
            [_replying(_ANSWERED)], ("T1", "T2"), 60, (True, None), id="resolved-thread-is-done"
        ),
        pytest.param([_replying(_PERSON)], ("T5",), 60, (True, None), id="person-wrote-is-done"),
        pytest.param([_replying(_SILENT)], ("T2",), 400, (True, None), id="past-the-deadline"),
        pytest.param(
            [_replying(_SILENT, _LATER)],
            ("T2", "T3"),
            100,
            (False, at(70) + _TIMEOUT),
            id="latest-deadline-among-the-pending",
        ),
        pytest.param(
            [_replying(_SILENT, replies=False)],
            ("T2",),
            60,
            (True, None),
            id="a-reviewer-with-replies-off-is-ignored",
        ),
        pytest.param(
            [_replying(_ANSWERED, _SILENT)],
            ("T1",),
            60,
            (True, None),
            id="an-unanswered-thread-outside-the-round-is-ignored",
        ),
        pytest.param(
            [_replying(_AHEAD)],
            ("T4",),
            60,
            (False, at(60) + _TIMEOUT),
            id="forge-clock-ahead-clamps-the-deadline",
        ),
    ],
)
def test_the_reply_wait_is_done_when(feedback, threads, now, expected):
    got = settle.reply_wait(
        [build() for build in feedback],
        owed.ReplyWait(threads, _TIMEOUT),
        now=at(now),
        kraft=facts.Kraft(VIEWER, "w1", {}),
    )

    assert got == expected
