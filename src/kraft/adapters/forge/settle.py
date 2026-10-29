"""When each automated reviewer has finished with the latest push.

A bot that posts nothing when it finds nothing, or edits an old comment in
place, never announces that it is done. So a bot is finished when it has shown
activity since Kraft pushed the head and then been quiet for the grace period;
a check, when it completes; any reviewer, at the wait's timeout. The decision
is pure: the wait supplies the clock and the memory the last check left, and
keeps what comes back (`waits.observe(extra=)`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from kraft.adapters.forge.models import ReviewerFeedback
from kraft.automated_review import AutomatedReview, Reviewers
from kraft.vocab import ForgeEvent

#: How many reviews in a row a bot must have stayed silent on in one repository
#: before `kraft admin doctor` says so: one silence is a bot with nothing to say.
SILENT_RUN = 3

#: The conclusions of a check that has nothing to say, on either forge.
_PASSED = ("success", "neutral", "skipped")


@dataclass(frozen=True)
class Settled:
    state: Literal["pending", "settled"]
    #: When the next check is due, because a bot's grace period ends then;
    #: None leaves it to the wait's own backoff.
    next_at: datetime | None
    #: What to hand `decide` at the next check.
    memory: dict
    #: What each reviewer said, once all have finished; empty before.
    collection: list[dict]


def reviewer_key(reviewer: AutomatedReview) -> str:
    return f"bot:{reviewer.bot}" if reviewer.bot is not None else f"check:{reviewer.check}"


def decide(
    feedback: list[ReviewerFeedback],
    *,
    since: datetime,
    now: datetime,
    grace: timedelta,
    memory: dict,
    timed_out: bool,
) -> Settled:
    """Whether every reviewer in `feedback` has finished with the head pushed
    at `since`.

    A bot's activity is its newest comment, review or inline update after
    `since` (forge clock against Kraft's, no allowance for skew), or a change
    in its set of unresolved threads since the last check, at `now`. The first
    check of a wait has no set to compare with: it stores the threads as the
    baseline and counts none of them, so last round's declined threads do not
    end the wait early. At the timeout every reviewer counts as finished."""
    kept: dict = {}
    finished: dict[str, str | None] = {}
    wakes: list[datetime] = []
    for item in feedback:
        key = reviewer_key(item.reviewer)
        if item.reviewer.check is not None:
            kept[key] = memory.get(key, {})
            finished[key] = "check" if (item.check or {}).get("finished") else None
        else:
            activity, thread_ids = _activity(item, memory.get(key), since, now)
            kept[key] = {
                "activity_at": None if activity is None else activity.isoformat(),
                "threads": thread_ids,
            }
            quiet = activity is not None and now - activity >= grace
            finished[key] = "activity" if quiet else None
            if activity is not None and not quiet:
                wakes.append(activity + grace)
        if finished[key] is None and timed_out:
            finished[key] = "timeout"
    if any(how is None for how in finished.values()):
        return Settled("pending", min(wakes, default=None), kept, [])
    return Settled(
        "settled",
        None,
        kept,
        [_collected(item, finished[reviewer_key(item.reviewer)]) for item in feedback],
    )


def _activity(
    item: ReviewerFeedback, prior: dict | None, since: datetime, now: datetime
) -> tuple[datetime | None, list[str]]:
    """The bot's latest activity at this check, and its thread ids to remember."""
    thread_ids = sorted(t["id"] for t in item.threads)
    seen = [datetime.fromisoformat(prior["activity_at"])] if prior and prior["activity_at"] else []
    if item.last_activity is not None and item.last_activity > since:
        seen.append(item.last_activity)
    if prior is not None and set(prior["threads"]) != set(thread_ids):
        seen.append(now)
    return max(seen, default=None), thread_ids


def _collected(item: ReviewerFeedback, how: str | None) -> dict:
    """One reviewer's entry in the collection the triage reads: only its newest
    comment and newest review, its unresolved threads, and a check that did not
    succeed with its output."""
    newest: dict[str, dict] = {}
    for summary in item.summaries:
        kept = newest.get(summary["kind"])
        if kept is None or summary["updated_at"] > kept["updated_at"]:
            newest[summary["kind"]] = summary
    failed = item.check is not None and item.check.get("conclusion") not in _PASSED
    return {
        "reviewer": reviewer_key(item.reviewer),
        "finished": how,
        "summaries": sorted(newest.values(), key=lambda s: s["updated_at"]),
        "threads": list(item.threads),
        "check": item.check if failed else None,
    }


def heard_and_silent(reviewers: Reviewers, memory: dict) -> tuple[list[str], list[str]]:
    """The bots active since the push, and the ones that never posted, as
    `decide`'s settled memory has them."""
    bots = [r.bot for r in reviewers if r.bot is not None]
    active = {b for b in bots if (memory.get(f"bot:{b}") or {}).get("activity_at")}
    return [b for b in bots if b in active], [b for b in bots if b not in active]


def silent_runs(conn, at_least: int = SILENT_RUN) -> list[dict]:
    """Each bot whose last `at_least` settled reviews in a repository all
    found it silent: most likely a login no account has, or a bot not
    installed there. Newest item first."""
    seen: dict[tuple[str, str], list[tuple[bool, str]]] = {}
    # Keyed by the item's own repository, and a workspace member's folder in
    # it: the path the wait read is that item's own checkout.
    rows = conn.execute(
        "SELECT e.work_item_id, e.payload, w.repo FROM events e "
        "JOIN work_items w ON w.id = e.work_item_id WHERE e.type = ? ORDER BY e.seq DESC",
        (ForgeEvent.AUTOMATED_REVIEW_SETTLED,),
    )
    for row in rows:
        payload = json.loads(row["payload"])
        repo = "/".join(x for x in (row["repo"], payload.get("mount")) if x)
        for silent, key in ((False, "heard"), (True, "silent")):
            for bot in payload.get(key) or []:
                run = seen.setdefault((repo, bot), [])
                if len(run) < at_least:
                    run.append((silent, row["work_item_id"]))
    return [
        {"repo": repo, "bot": bot, "items": [item for _, item in run]}
        for (repo, bot), run in sorted(seen.items())
        if len(run) == at_least and all(silent for silent, _ in run)
    ]
