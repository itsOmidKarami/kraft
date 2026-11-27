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

from kraft.adapters.forge.models import ForgeNote, ReviewerFeedback
from kraft.adapters.forge.replies import facts, owed
from kraft.automated_review import BotReviewer, CheckReviewer, Reviewers
from kraft.vocab import ForgeEvent

#: How many items a bot must have reviewed in one repository, never once
#: posting, before `kraft admin doctor` says so. A bot that posts only when it
#: finds something is silent on clean code, but not on every item, ever.
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


def reviewer_key(reviewer: BotReviewer | CheckReviewer) -> str:
    return f"bot:{reviewer.bot}" if reviewer.bot is not None else f"check:{reviewer.check}"


def decide(
    feedback: list[ReviewerFeedback],
    *,
    since: datetime,
    now: datetime,
    grace: timedelta,
    memory: dict,
    timed_out: bool,
    kraft: facts.Kraft | None = None,
) -> Settled:
    """Whether every reviewer in `feedback` has finished with the head pushed
    at `since`.

    A bot's activity is its newest comment, review or inline update after
    `since` (forge clock against Kraft's, no allowance for skew), or a change
    in its set of unresolved threads since the last check, at `now`. The first
    check of a wait has no set to compare with: it stores the threads as the
    baseline and counts none of them, so last round's declined threads do not
    end the wait early. At the timeout every reviewer counts as finished.
    `kraft` (a bot with replies on) leaves Kraft's conversation out of it."""
    kept: dict = {}
    finished: dict[str, str | None] = {}
    wakes: list[datetime] = []
    for item in feedback:
        key = reviewer_key(item.reviewer)
        if item.reviewer.check is not None:
            kept[key] = memory.get(key, {})
            finished[key] = "check" if (item.check or {}).get("finished") else None
        else:
            activity, thread_ids = _activity(item, memory.get(key), since, now, kraft)
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
    bots = [f.reviewer.bot for f in feedback if f.reviewer.bot is not None]
    return Settled(
        "settled",
        None,
        kept,
        [_collected(item, finished[reviewer_key(item.reviewer)], bots, kraft) for item in feedback],
    )


def collect(
    feedback: list[ReviewerFeedback], how: str | None, kraft: facts.Kraft | None = None
) -> list[dict]:
    """What each reviewer said, as the triage reads it, every reviewer finished `how`
    (`decide` builds the same when it settles)."""
    bots = [f.reviewer.bot for f in feedback if f.reviewer.bot is not None]
    return [_collected(item, how, bots, kraft) for item in feedback]


def reply_wait(
    feedback: list[ReviewerFeedback], wait: owed.ReplyWait, *, now: datetime, kraft: facts.Kraft
) -> tuple[bool, datetime | None]:
    """Whether every thread the round waits for is done (answered, resolved, written in by a
    person, or past its deadline), and the latest deadline among those that are not, clamped
    to `now + timeout`: a forge clock ahead of Kraft's must not park the wait for the skew."""
    bots = [f.reviewer.bot for f in feedback if f.reviewer.bot is not None]
    on = [f.reviewer.bot for f in feedback if f.reviewer.bot is not None and f.reviewer.replies]
    open_ = {t["id"]: t for f in feedback for t in f.threads}
    pending: list[datetime] = []
    for tid in wait.threads:
        thread = open_.get(tid)  # a resolved thread is not in the collection: done
        s = (
            None
            if thread is None
            else owed.standing(
                thread.get("notes", ()), on=on, bots=bots, kraft=kraft, timeout=wait.timeout
            )
        )
        if s is not None and not owed.is_done(s, now=now):
            pending.append(s.deadline)
    latest = max(pending, default=None)
    return not pending, None if latest is None else min(latest, now + wait.timeout)


def _talks(thread: dict, kraft: facts.Kraft | None) -> bool:
    return kraft is not None and kraft.talks_in(thread.get("notes", ()))


def _activity(
    item: ReviewerFeedback,
    prior: dict | None,
    since: datetime,
    now: datetime,
    kraft: facts.Kraft | None = None,
) -> tuple[datetime | None, list[str]]:
    """The bot's latest activity at this check, and its thread ids to remember. A
    thread Kraft has posted in is left out of those ids, so the bot resolving it
    is not activity, and neither is a thread the record holds a reply in that has since been
    resolved or has left the collection: the ids remembered from the last check are compared
    less the ones excluded now."""
    excluded = {t["id"] for t in item.threads if _talks(t, kraft)} | (
        kraft.threads if kraft else set()
    )
    thread_ids = sorted(t["id"] for t in item.threads if t["id"] not in excluded)
    seen = [datetime.fromisoformat(prior["activity_at"])] if prior and prior["activity_at"] else []
    if item.last_activity is not None and item.last_activity > since:
        seen.append(item.last_activity)
    if prior is not None and set(prior["threads"]) - excluded != set(thread_ids):
        seen.append(now)
    return max(seen, default=None), thread_ids


def _note_json(note: ForgeNote, bots: list[str], kraft: facts.Kraft | None = None) -> dict:
    """One note as the triage reads it: `kraft` when Kraft wrote it, which only a
    wait with a replies-on bot can tell (it alone reads the viewer and the record)."""
    return {
        "id": note.id,
        "author": note.author,
        "by": "kraft"
        if kraft is not None and kraft.wrote(note)
        else facts.note_by(note, bots=bots),
        "body": note.body,
        "created_at": note.created_at,
        "reply_to": note.reply_to,
        "review_id": note.review_id,
    }


def _collected(
    item: ReviewerFeedback, how: str | None, bots: list[str], kraft: facts.Kraft | None = None
) -> dict:
    """One reviewer's entry in the collection the triage reads: only its newest
    comment and newest review, its unresolved threads (each with its notes and
    whose each is, `bots` being every configured bot), and a check that did not
    succeed with its output."""
    newest: dict[str, dict] = {}
    for summary in item.summaries:
        kept = newest.get(summary["kind"])
        if kept is None or summary["updated_at"] > kept["updated_at"]:
            newest[summary["kind"]] = summary
    failed = item.check is not None and item.check.get("conclusion") not in _PASSED
    return {
        "reviewer": reviewer_key(item.reviewer),
        "replies": item.reviewer.replies,
        "finished": how,
        "summaries": sorted(newest.values(), key=lambda s: s["updated_at"]),
        "threads": [
            {
                **{k: v for k, v in t.items() if k != "notes"},
                "notes": [_note_json(n, bots, kraft) for n in t.get("notes", ())],
            }
            for t in item.threads
        ],
        "check": item.check if failed else None,
    }


def heard_and_silent(reviewers: Reviewers, memory: dict) -> tuple[list[str], list[str]]:
    """The bots active since the push, and the ones that never posted, as
    `decide`'s settled memory has them."""
    bots = [r.bot for r in reviewers if r.bot is not None]
    active = {b for b in bots if (memory.get(f"bot:{b}") or {}).get("activity_at")}
    return [b for b in bots if b in active], [b for b in bots if b not in active]


def silent_runs(conn, at_least: int = SILENT_RUN) -> list[dict]:
    """Each bot that has never posted in a repository, across reviews of at
    least `at_least` items: most likely a login no account has, or a bot not
    installed there. Its last `at_least` items, newest first. Only a bot the
    repository's newest review still waited for: a login since corrected, or a
    bot since removed, no longer reviews there."""
    heard: set[tuple[str, str]] = set()
    current: dict[str, set[str]] = {}
    items: dict[tuple[str, str], list[str]] = {}
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
        current.setdefault(repo, {*(payload.get("heard") or []), *(payload.get("silent") or [])})
        heard |= {(repo, bot) for bot in payload.get("heard") or []}
        for bot in payload.get("silent") or []:
            seen = items.setdefault((repo, bot), [])
            if row["work_item_id"] not in seen:
                seen.append(row["work_item_id"])
    return [
        {"repo": repo, "bot": bot, "items": seen[:at_least]}
        for (repo, bot), seen in sorted(items.items())
        if bot in current[repo] and (repo, bot) not in heard and len(seen) >= at_least
    ]
