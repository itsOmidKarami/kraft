"""The reply record: a pair of events per write, an intent before it and an outcome
after, read back over every event of the work item so a retry never resets a count or
forgets a write. A crash, a cancel or a lost answer leaves an intent with no outcome,
which the next posting point reconciles rather than sends again (`send`)."""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from kraft import events
from kraft.adapters.forge.models import Posted
from kraft.adapters.forge.replies.facts import body_hash
from kraft.vocab import ForgeEvent

Op = Literal["reply", "summary", "resolve"]
State = Literal["pending", "unclear", "posted", "refused", "superseded", "given_up"]
Failure = Literal["unclear", "rate_limited", "deferred"]

_OPS = ("reply", "summary", "resolve")
#: A failure that counts toward the three sends: it may have landed, or it was refused
#: for the rate and will be tried again.
_COUNTED = ("unclear", "rate_limited")
_TYPES = (
    ForgeEvent.REPLY_INTENDED,
    ForgeEvent.REPLY_SEND_STARTED,
    ForgeEvent.REPLY_SEND_FAILED,
    ForgeEvent.REPLY_POSTED,
    ForgeEvent.REPLY_NOT_FOUND,
    ForgeEvent.REPLY_ENDED,
    ForgeEvent.REPLY_DUPLICATE_DELETED,
)


@dataclass(frozen=True)
class Intent:
    id: str
    payload: dict  # as `intend` recorded it
    state: State = "pending"
    sends: int = 0  # counted sends: unclear and rate_limited failures
    #: A send was ever unclear and was then reconciled or sent again: what the
    #: duplicate sweep reads. A send that posted cleanly never is.
    ever_unclear: bool = False
    failure: dict | None = None  # the newest reply_send_failed payload, plus "at"
    why: str | None = None  # the `reply_ended` reason, once it ended
    posted: dict | None = None  # the newest reply_posted payload


def new_id(work_item_id: str) -> str:
    return f"{work_item_id}-{secrets.token_hex(6)}"


def intend(c, work_item_id: str, intent: str, payload: dict) -> None:
    """Record that Kraft is about to write: always before the write is made."""
    if payload.get("op") not in _OPS:
        raise ValueError(f"a reply intent's op is one of {_OPS}, not {payload.get('op')!r}")
    if not payload.get("body_sha256"):
        raise ValueError("a reply intent records the hash of the body it will send")
    events.append(c, work_item_id, ForgeEvent.REPLY_INTENDED, {"intent": intent, **payload})


def send_began(c, work_item_id: str, intent: str) -> None:
    """Recorded just before a write: if the process dies before the outcome is, the
    intent reads `unclear` and is reconciled, never sent blind. Not a counted send."""
    events.append(c, work_item_id, ForgeEvent.REPLY_SEND_STARTED, {"intent": intent})


def send_failed(c, work_item_id: str, intent: str, failure: Failure, exc: BaseException) -> None:
    payload = {
        "intent": intent,
        "failure": failure,
        "counted": failure in _COUNTED,
        "status": getattr(exc, "status", None),
        "reason": getattr(exc, "reason", None),
        "retry_after": getattr(exc, "retry_after", None),
        "message": str(exc)[:300],
    }
    events.append(c, work_item_id, ForgeEvent.REPLY_SEND_FAILED, payload)


def posted(c, work_item_id: str, intent: str, note: Posted, *, via: str) -> None:
    payload = {
        "intent": intent,
        "ids": list(note.ids),
        "created_at": note.created_at,
        "body_sha256": body_hash(note.body),
        "via": via,
    }
    events.append(c, work_item_id, ForgeEvent.REPLY_POSTED, payload)


def not_found(c, work_item_id: str, intent: str) -> None:
    events.append(c, work_item_id, ForgeEvent.REPLY_NOT_FOUND, {"intent": intent})


def ended(c, work_item_id: str, intent: str, outcome: str, why: str) -> None:
    payload = {"intent": intent, "outcome": outcome, "why": why}
    events.append(c, work_item_id, ForgeEvent.REPLY_ENDED, payload)


def duplicate_deleted(
    c, work_item_id: str, intent: str, kept: Sequence[str], deleted: Sequence[str]
) -> None:
    payload = {"intent": intent, "kept": list(kept), "deleted": list(deleted)}
    events.append(c, work_item_id, ForgeEvent.REPLY_DUPLICATE_DELETED, payload)


def fold(rows: Iterable[dict]) -> dict[str, Intent]:
    """Each intent's state from its events in order. An event for an intent not seen
    intended is ignored."""
    out: dict[str, Intent] = {}
    for row in rows:
        p = row["payload"]
        kind = row["type"]
        if kind == ForgeEvent.REPLY_INTENDED:
            out[p["intent"]] = Intent(p["intent"], {k: v for k, v in p.items() if k != "intent"})
            continue
        cur = out.get(p.get("intent"))
        if cur is None:
            continue
        if kind == ForgeEvent.REPLY_SEND_STARTED:
            # Unclear until an outcome follows, but only a recorded failure or a
            # reconcile makes it `ever_unclear`: a clean post never does.
            out[cur.id] = replace(cur, state="unclear")
        elif kind == ForgeEvent.REPLY_SEND_FAILED:
            counted = bool(p.get("counted"))
            out[cur.id] = replace(
                cur,
                state="unclear" if p["failure"] == "unclear" else "pending",
                sends=cur.sends + counted,
                ever_unclear=cur.ever_unclear or (counted and p["failure"] == "unclear"),
                failure={**p, "at": row["created_at"]},
            )
        elif kind == ForgeEvent.REPLY_POSTED:
            out[cur.id] = replace(
                cur,
                state="posted",
                posted=p,
                ever_unclear=cur.ever_unclear or p.get("via") == "reconcile",
            )
        elif kind == ForgeEvent.REPLY_NOT_FOUND:
            out[cur.id] = replace(cur, state="pending", ever_unclear=True)
        elif kind == ForgeEvent.REPLY_ENDED:
            out[cur.id] = replace(cur, state=p["outcome"], why=p.get("why"))
    return out


def read(c, work_item_id: str) -> dict[str, Intent]:
    """Every intent of the work item, from every fork's events: a retry keeps what
    earlier runs posted (`store.forks`)."""
    rows = c.execute(
        f"SELECT type, payload, created_at FROM events WHERE work_item_id = ? "
        f"AND type IN ({', '.join('?' * len(_TYPES))}) ORDER BY seq",
        (work_item_id, *_TYPES),
    )
    return fold(
        {"type": r["type"], "payload": json.loads(r["payload"]), "created_at": r["created_at"]}
        for r in rows
    )


def thread_ids(intents: Mapping[str, Intent]) -> frozenset[str]:
    """The threads the record holds a reply in (posted or unclear): Kraft's conversation, whether
    or not a Kraft note still shows there (a person may have deleted it)."""
    return frozenset(
        i.payload["thread"]
        for i in intents.values()
        if i.payload.get("op") == "reply"
        and i.payload.get("thread")
        and i.state in ("posted", "unclear")
    )


def posted_hashes(intents: Mapping[str, Intent]) -> dict[str, str]:
    """Every id of every note the record holds as posted, to the hash of the body the
    forge returned (`facts.is_kraft_note`)."""
    return {
        i: it.posted["body_sha256"]
        for it in intents.values()
        if it.posted is not None
        for i in it.posted["ids"]
    }
