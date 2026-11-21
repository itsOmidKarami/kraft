"""Whether Kraft may write in one review-bot thread: what the thread shows, and for a
triage reply, Kraft's last word and its argument count against the cap."""

from __future__ import annotations

from dataclasses import replace

import pytest
from support.bot_threads import VIEWER, bot_thread, kraft_note, note

from kraft.adapters.forge.replies import facts
from kraft.adapters.forge.replies.gate import thread_refusal, triage_refusal
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


def _thread(*notes, resolved=False, can_reply=True, tid="T1"):
    _, thread = bot_thread(tid, *notes, file="a.py")
    return replace(thread, mr=7, resolved=resolved, can_reply=can_reply)


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
    "thread, intents, expected",
    [
        pytest.param(_thread(B), {}, None, id="first-word"),
        pytest.param(_thread(B, K), {}, "last_is_kraft", id="after-kraft"),
        pytest.param(_thread(B, K, B), _by_id(_intent("posted", id=K_ID)), None, id="bot-answered"),
        pytest.param(
            _thread(B, K, B, K2, B),
            _by_id(_intent("posted", id=K_ID), _intent("posted", id=K2_ID, kind="answered")),
            "cap",
            id="at-the-cap",
        ),
        pytest.param(
            _thread(B),
            _by_id(_intent("posted", id=K_ID), _intent("unclear", id=K2_ID)),
            "cap",
            id="an-unclear-send-counts",
        ),
        pytest.param(
            _thread(B),
            _by_id(_intent("posted", id=K_ID), _intent("posted", id=K2_ID)),
            "cap",
            id="a-deleted-reply-counts",
        ),
        pytest.param(
            _thread(B, K, B),
            _by_id(_intent("posted", id=K_ID), _intent("posted", id=K2_ID, kind="fixed")),
            None,
            id="a-fix-reply-does-not-count",
        ),
        pytest.param(
            _thread(B),
            _by_id(
                _intent("posted", id=K_ID, thread="T2"), _intent("posted", id=K2_ID, thread="T2")
            ),
            None,
            id="another-thread-s-replies",
        ),
        pytest.param(_thread(B, K, B, K2, B), {}, "cap", id="marked-notes-the-record-lost"),
    ],
)
def test_a_triage_reply_waits_its_turn(thread, intents, expected):
    got = triage_refusal(thread, bots=(BOT,), on=(BOT,), kraft=_kraft(), intents=intents)
    assert got == expected
