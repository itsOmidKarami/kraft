"""When each automated reviewer has finished with the latest push
(`settle.decide`): a bot by its quiet after its last activity, a check by
completing, either by the wait's timeout. Pure: the clock and the memory the
last check left are arguments. Last, which bots a run of settled reviews found
silent (`silent_runs`), read off the events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kraft import events
from kraft.adapters.forge import settle
from kraft.adapters.forge.models import ReviewerFeedback
from kraft.automated_review import AutomatedReview
from kraft.vocab import ForgeEvent

SINCE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
GRACE = timedelta(seconds=60)


def at(seconds: float) -> datetime:
    """`seconds` after the push."""
    return SINCE + timedelta(seconds=seconds)


def bot(name="x", *, active=None, summaries=(), threads=()) -> ReviewerFeedback:
    return ReviewerFeedback(
        AutomatedReview(bot=name),
        None if active is None else at(active),
        tuple(summaries),
        tuple({"id": t, "file": "a.py", "line": 1, "body": f"fix {t}"} for t in threads),
    )


def check(finished, conclusion=None, name="scan") -> ReviewerFeedback:
    return ReviewerFeedback(
        AutomatedReview(check=name),
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


def decide(feedback, *, now, memory=None, timed_out=False):
    return settle.decide(
        feedback, since=SINCE, now=at(now), grace=GRACE, memory=memory or {}, timed_out=timed_out
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


def test_a_check_that_succeeded_is_collected_without_its_output():
    assert decide([check(True, "success")], now=5).collection[0]["check"] is None


_ONE_NODE = [
    {"id": "n", "kind": "exec", "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}]}
]


@pytest.mark.parametrize(
    "reviews, named",
    [
        # Oldest first: (what the bot did, the workspace member it reviewed).
        ([("silent", None)] * 3, ["w3", "w2", "w1"]),
        ([("silent", None), ("silent", None), ("heard", None)], None),
        ([("heard", None)] + [("silent", None)] * 3, ["w4", "w3", "w2"]),
        ([("silent", None)] * 2, None),
        # A member's reviews are counted apart from the root's.
        ([("silent", "repos/pkg"), ("silent", None), ("silent", "repos/pkg")], None),
    ],
    ids=["three-silent", "heard-last", "heard-before-three", "only-two", "a-member-apart"],
)
async def test_a_bot_is_named_only_when_its_last_reviews_in_a_repository_were_silent(
    item_on, database, reviews, named
):
    for n, (did, mount) in enumerate(reviews, 1):
        it = await item_on(_ONE_NODE, wid=f"w{n}")
        payload = {"repo": "checkout", "mount": mount, "heard": [], "silent": []}
        payload[did] = ["ccr"]
        await database.write(
            lambda c, w=it.id, p=payload: events.append(
                c, w, ForgeEvent.AUTOMATED_REVIEW_SETTLED, p
            )
        )

    got = database.read(settle.silent_runs)

    assert got == ([] if named is None else [{"repo": str(it.repo), "bot": "ccr", "items": named}])
