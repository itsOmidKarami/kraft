"""Whether Kraft may write in one review-bot thread: what the thread shows, and for a
triage reply, Kraft's last word and its argument count against the cap."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from support.bot_threads import VIEWER, bot_thread, kraft_note, note
from support.harness import v1_node, v1_task

from kraft import store
from kraft.adapters.forge.models import Posted
from kraft.adapters.forge.replies import facts, record
from kraft.adapters.forge.replies.gate import (
    Latest,
    fix_refusal,
    resolve_refusal,
    thread_refusal,
    triage_refusal,
)
from kraft.adapters.forge.replies.record import Intent, new_id

ITEM = "wi1"
K_ID = f"{ITEM}-0123456789ab"  # the marker kraft_note puts on K
K2_ID = f"{ITEM}-fedcba987654"
BOT = "coderabbitai"

B = note("B", BOT, "rename x")
U = note("U", BOT, "rename x", type_="User")
P = note("P", "alice", "I disagree", type_="User")
K = kraft_note("K", ITEM)
K2 = note("K2", VIEWER, f"Answer:\n\nyes\n\n{facts.reply_marker(K2_ID)}", type_="User")
VIEWER_UNMARKED = note("V", VIEWER, "a word from the viewer", type_="User")


def _thread(*notes, resolved=False, can_reply=True, can_resolve=True, tid="T1"):
    _, thread = bot_thread(tid, *notes, file="a.py")
    return replace(thread, mr=7, resolved=resolved, can_reply=can_reply, can_resolve=can_resolve)


def _kraft(posted=None):
    return facts.Kraft(VIEWER, ITEM, posted or {})


def _intent(state, *, id=None, **payload):
    """A triage reply's intent in T1 unless `payload` says otherwise."""
    payload = {"op": "reply", "thread": "T1", "kind": "declined", **payload}
    return Intent(id or new_id(ITEM), payload, state)


def _by_id(*intents):
    return {i.id: i for i in intents}


@pytest.mark.parametrize(
    "thread, on, posted, expected",
    [
        pytest.param(_thread(B), (BOT,), {}, None, id="bot-started"),
        pytest.param(None, (BOT,), {}, "not_found", id="no-thread"),
        pytest.param(_thread(B, resolved=True), (BOT,), {}, "resolved", id="resolved"),
        pytest.param(_thread(B, can_reply=False), (BOT,), {}, "cannot_reply", id="locked"),
        pytest.param(_thread(P, B), (BOT,), {}, "not_bot_started", id="started-by-a-person"),
        pytest.param(
            _thread(U), (BOT,), {}, "bot posts as a user", id="the-bot-from-a-user-account"
        ),
        pytest.param(_thread(B), (), {}, "not_bot_started", id="started-by-a-bot-with-replies-off"),
        pytest.param(_thread(B, P), (BOT,), {}, "person_written", id="a-person-wrote-in-it"),
        pytest.param(_thread(B, K, B), (BOT,), {}, None, id="kraft-and-the-bot"),
        pytest.param(
            _thread(B, K), (BOT,), {"K": "another hash"}, "person_written", id="a-kraft-note-edited"
        ),
        pytest.param(
            _thread(B, VIEWER_UNMARKED), (BOT,), {}, "person_written", id="the-viewer-unmarked"
        ),
    ],
)
def test_the_gate_reads_the_thread(thread, on, posted, expected):
    assert thread_refusal(thread, bots=(BOT,), on=on, kraft=_kraft(posted)) == expected


@pytest.mark.parametrize(
    "thread, intents, expected, cap",
    [
        pytest.param(_thread(B), {}, None, 2, id="first-word"),
        pytest.param(_thread(B, K), {}, "last_is_kraft", 2, id="after-kraft"),
        pytest.param(
            _thread(B, K, B), _by_id(_intent("posted", id=K_ID)), None, 2, id="bot-answered"
        ),
        pytest.param(
            _thread(B, K, B, K2, B),
            _by_id(_intent("posted", id=K_ID), _intent("posted", id=K2_ID, kind="answered")),
            "cap",
            2,
            id="at-the-cap",
        ),
        pytest.param(
            _thread(B),
            _by_id(_intent("posted", id=K_ID), _intent("unclear", id=K2_ID)),
            "cap",
            2,
            id="an-unclear-send-counts",
        ),
        pytest.param(
            _thread(B),
            _by_id(_intent("posted", id=K_ID), _intent("posted", id=K2_ID)),
            "cap",
            2,
            id="a-deleted-reply-counts",
        ),
        pytest.param(
            _thread(B, K, B),
            _by_id(_intent("posted", id=K_ID), _intent("posted", id=K2_ID, kind="fixed")),
            None,
            2,
            id="a-fix-reply-does-not-count",
        ),
        pytest.param(
            _thread(B),
            _by_id(
                _intent("posted", id=K_ID, thread="T2"), _intent("posted", id=K2_ID, thread="T2")
            ),
            None,
            2,
            id="another-thread-s-replies",
        ),
        pytest.param(_thread(B, K, B, K2, B), {}, "cap", 2, id="marked-notes-the-record-lost"),
        pytest.param(
            _thread(B, K, B),
            _by_id(_intent("posted", id=K_ID), _intent("posted", id=K2_ID)),
            None,
            3,
            id="cap-three-allows-a-third",
        ),
    ],
)
def test_a_triage_reply_waits_its_turn(thread, intents, expected, cap):
    got = triage_refusal(thread, bots=(BOT,), on=(BOT,), kraft=_kraft(), intents=intents, cap=cap)
    assert got == expected


@pytest.mark.parametrize(
    "thread, intents, expected",
    [
        pytest.param(_thread(B), {}, None, id="first"),
        pytest.param(_thread(B, K), {}, None, id="after-kraft-s-own-note"),
        pytest.param(
            _thread(B),
            _by_id(_intent("posted", id=K_ID), _intent("posted", id=K2_ID, kind="answered")),
            None,
            id="at-the-cap",
        ),
        pytest.param(
            _thread(B),
            _by_id(_intent("posted", id=K_ID, kind="fixed", finding="fp1")),
            "fixed_before",
            id="posted-before-for-the-finding",
        ),
        pytest.param(
            _thread(B),
            _by_id(_intent("posted", id=K_ID, kind="fixed", finding="fp2")),
            None,
            id="posted-before-for-another-finding",
        ),
    ],
)
def test_a_fix_reply_goes_once_per_finding(thread, intents, expected):
    got = fix_refusal(
        thread, bots=(BOT,), on=(BOT,), kraft=_kraft(), intents=intents, finding="fp1"
    )
    assert got == expected


async def test_the_cap_survives_a_retry(item_on, database):
    """A person's retry forks the run, and the argument count still reads every reply."""
    it = await item_on([v1_node("n", tasks=[v1_task("t")])])
    for n in ("0123456789ab", "fedcba987654"):
        intent = f"{it.id}-{n}"
        payload = {
            "op": "reply",
            "node_id": "n",
            "repo": "/repo",
            "thread": "T1",
            "kind": "declined",
            "body_sha256": "0" * 64,
            "head": "abc123",
            "round": 0,
            "session": "s1",
        }
        await database.write(lambda c, i=intent, p=payload: record.intend(c, it.id, i, p))
        await database.write(
            lambda c, i=intent: record.posted(
                c, it.id, i, Posted(("1", "N1"), "t", "b"), via="send"
            )
        )
    await database.write(lambda c: store.fork_run(c, it.id, None, by_person=True))

    held = database.read(lambda c: record.read(c, it.id))

    assert len(held) == 2
    kraft = facts.Kraft(VIEWER, it.id, {})
    got = triage_refusal(_thread(B), bots=(BOT,), on=(BOT,), kraft=kraft, intents=held, cap=2)
    assert got == "cap"


READ_AT = datetime(2026, 1, 1, 0, 1, 0, tzinfo=UTC)
LATEST = Latest("s1", READ_AT, frozenset())
#: Bot notes before and after the triage's read of the thread (`READ_AT`), and 3 s before it.
LATE = note("B2", BOT, "no, still wrong", at="2026-01-01T00:02:00Z")
WITHIN_SKEW = note("B3", BOT, "no, still wrong", at="2026-01-01T00:00:57Z")
K_LATE = kraft_note("K", ITEM, at="2026-01-01T00:02:00Z")


def _sent(state, n, *, kind="fixed", id=None, why=None, **payload) -> Intent:
    """A reply intent of run 1 of node `n` in T1; `posted` ones hold the ids of note `n`'s."""
    posted = {"ids": [n.id, n.rest_id], "body_sha256": "h"} if state == "posted" else None
    base = {"op": "reply", "thread": "T1", "node_id": "n", "run": 1, "kind": kind, "cycle": 1}
    return Intent(id or new_id(ITEM), {**base, **payload}, state, why=why, posted=posted)


def _resolved_before(state, run=0, why=None) -> Intent:
    payload = {"op": "resolve", "thread": "T1", "node_id": "n", "run": run, "kind": "resolve"}
    return Intent(new_id(ITEM), payload, state, why=why)


FIX = _sent("posted", K, id=K_ID)
#: Kraft's note is a posted "Fixed in", unless a row says otherwise.
RESOLVE_ROWS = [
    pytest.param(_thread(B, K), [FIX], {}, None, id="confirmed-fix"),
    pytest.param(
        _thread(B, K, B, K2),
        [_sent("posted", K, kind="declined"), _sent("posted", K2, cycle=2)],
        {},
        None,
        id="fix-after-a-decline",
    ),
    pytest.param(_thread(B, K, can_reply=False), [FIX], {}, None, id="locked-but-resolvable"),
    pytest.param(
        _thread(B, K_LATE), [_sent("posted", K_LATE)], {}, None, id="kraft-note-after-the-read"
    ),
    pytest.param(
        _thread(B, K),
        [
            _sent("superseded", K, why="later_cycle", cycle=2),
            _sent("posted", K, cycle=3),
        ],
        {},
        None,
        id="later-cycle-superseded-with-a-later-fix-posted",
    ),
    pytest.param(
        _thread(B, K), [_sent("posted", K, kind="declined")], {}, "not_fixed", id="decline-only"
    ),
    pytest.param(
        _thread(B, K), [_sent("posted", K, kind="answered")], {}, "not_fixed", id="answer-only"
    ),
    pytest.param(
        _thread(B, K, K2),
        [FIX, _sent("posted", K2, kind="declined")],
        {},
        "not_fixed",
        id="fix-then-a-decline",
    ),
    pytest.param(
        _thread(B, K),
        [FIX],
        {"latest": Latest("s1", READ_AT, frozenset({"T1"}))},
        "not_fixed",
        id="triage-replied-after-the-fix",
    ),
    pytest.param(_thread(B, K), [], {}, "not_fixed", id="note-the-record-lacks"),
    pytest.param(_thread(B, K, resolved=True), [FIX], {}, "resolved", id="already-resolved"),
    pytest.param(
        _thread(B, K, resolved=True, can_resolve=False),
        [FIX],
        {},
        "resolved",
        id="already-resolved-and-not-resolvable",
    ),
    pytest.param(
        _thread(B, K, can_resolve=False), [FIX], {}, "cannot_resolve", id="cannot-resolve"
    ),
    pytest.param(_thread(B, K, P), [FIX], {}, "person_written", id="person-written"),
    pytest.param(
        _thread(B, K),
        [FIX],
        {"kraft": _kraft({"K": "another hash"})},
        "person_written",
        id="an-edited-kraft-note",
    ),
    pytest.param(_thread(B, K), [FIX], {"on": ()}, "not_bot_started", id="not-bot-started"),
    pytest.param(_thread(U, K), [FIX], {}, "bot posts as a user", id="bot-posts-as-a-user"),
    pytest.param(_thread(B), [FIX], {}, "kraft_note_gone", id="kraft-note-deleted"),
    pytest.param(
        _thread(B, K),
        [FIX, _resolved_before("posted")],
        {},
        "reopened",
        id="reopened-after-kraft-resolved",
    ),
    pytest.param(
        _thread(B, K), [FIX, _resolved_before("unclear")], {}, "reopened", id="reopened-unclear"
    ),
    pytest.param(
        _thread(B, K),
        [FIX, _resolved_before("refused", why="resolved")],
        {},
        "reopened",
        id="reopened-after-an-unclear-send-that-landed",
    ),
    pytest.param(_thread(B, K), [FIX], {"latest": None}, "no_latest_triage", id="no-latest-triage"),
    pytest.param(_thread(B, K, LATE), [FIX], {}, "answer_unread", id="bot-answered-after-the-read"),
    pytest.param(_thread(B, K, WITHIN_SKEW), [FIX], {}, "answer_unread", id="skew-margin"),
    pytest.param(
        _thread(B, K), [FIX], {"dropped": True}, "findings_dropped", id="findings-dropped"
    ),
    pytest.param(
        _thread(B, K),
        [FIX, _sent("refused", K, kind="declined", why="cap")],
        {},
        "reply_held_back",
        id="cap-refused",
    ),
    pytest.param(
        _thread(B, K),
        [FIX, _sent("pending", K, kind="declined")],
        {},
        "reply_held_back",
        id="deferred",
    ),
    pytest.param(
        _thread(B, K),
        [FIX, _sent("unclear", K, kind="declined")],
        {},
        "reply_held_back",
        id="unclear",
    ),
    pytest.param(
        _thread(B, K),
        [FIX, _sent("given_up", K, kind="declined")],
        {},
        "reply_held_back",
        id="given-up",
    ),
    pytest.param(
        _thread(B, K),
        [FIX, _sent("superseded", K, why="raised_again", cycle=2)],
        {},
        "reply_held_back",
        id="superseded-fix",
    ),
    pytest.param(
        _thread(B, K),
        [FIX, _sent("superseded", K, why="later_cycle", cycle=2)],
        {},
        "reply_held_back",
        id="later-cycle-superseded-alone",
    ),
    pytest.param(
        _thread(B, K),
        [FIX, _sent("refused", K, kind="declined", why="cap", run=0)],
        {},
        None,
        id="another-run",
    ),
    pytest.param(
        _thread(B, K),
        [FIX],
        {"outstanding": {"T1"}},
        "finding_outstanding",
        id="finding-outstanding",
    ),
]


@pytest.mark.parametrize("thread, intents, given, expected", RESOLVE_ROWS)
def test_resolve_refusal(thread, intents, given, expected):
    """Only a confirmed fix is resolved: Kraft's latest note in the thread is a posted "Fixed in",
    the latest triage saw every note, and nothing is held back or outstanding."""
    args = {
        "bots": (BOT,),
        "on": (BOT,),
        "kraft": _kraft(),
        "intents": _by_id(*intents),
        "run": 1,
        "node_id": "n",
        "latest": LATEST,
        "outstanding": (),
        "dropped": False,
        **given,
    }
    assert resolve_refusal(thread, **args) == expected
