"""A bot thread's conversation (`owed.standing`, `is_owed`, `is_done`): is it waiting on the
bot, has the bot answered, is its deadline past. Pure, and every time is a `created_at`."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from support.bot_threads import VIEWER, kraft_note, note

from kraft.adapters.forge.replies import facts, owed

BOT, OTHER_BOT = "coderabbitai", "sonarbot"
TIMEOUT = timedelta(minutes=5)
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> str:
    return (BASE + timedelta(seconds=seconds)).isoformat()


def when(seconds: int) -> datetime:
    return BASE + timedelta(seconds=seconds)


def b(nid, seconds, author=BOT):
    return note(nid, author, "text", at=_at(seconds))


def k(nid, seconds):
    return kraft_note(nid, "w1", at=_at(seconds))


def person(nid, seconds):
    return note(nid, "alice", "I disagree", at=_at(seconds), type_="User")


def _standing(notes, *, on=(BOT,), kraft=None, resolved=False):
    return owed.standing(
        notes,
        on=on,
        bots=(BOT, OTHER_BOT),
        kraft=kraft or facts.Kraft(VIEWER, "w1", {}),
        timeout=TIMEOUT,
        resolved=resolved,
    )


_EDITED = replace(kraft_note("K", "w1", at=_at(10)), body="Declined:\n\nedited by a person")
_MOVED = replace(b("B2", 20), updated_at=_at(999))


@pytest.mark.parametrize(
    "notes, kwargs, expected",
    [
        pytest.param([b("B", 0), k("K", 10), b("B2", 20)], {}, (10, 20), id="answered"),
        pytest.param(
            [b("B", 0), b("B1", 5), k("K", 10)], {}, (10, None), id="bot-spoke-before-kraft"
        ),
        pytest.param(
            [b("B", 0), k("K", 10), b("B2", 20, OTHER_BOT)],
            {},
            (10, None),
            id="answered-by-someone-else",
        ),
        pytest.param([b("B", 0), k("K", 10)], {}, (10, None), id="unanswered"),
        pytest.param([b("B", 0), person("P", 5), k("K", 10)], {}, None, id="person-wrote"),
        pytest.param([b("B", 0)], {}, None, id="no-kraft-note"),
        pytest.param([b("B", 0), k("K", 10)], {"resolved": True}, None, id="resolved"),
        pytest.param(
            [b("B", 0), k("K", 10)], {"on": ()}, None, id="first-note-by-a-bot-with-replies-off"
        ),
        pytest.param(
            [b("B", 0), _EDITED],
            {"kraft": facts.Kraft(VIEWER, "w1", {"K": "0" * 64})},
            None,
            id="an-edited-kraft-note",
        ),
        pytest.param(
            [b("B", 0), k("K", 10), b("B2", 20), k("K2", 30)],
            {},
            (30, None),
            id="a-second-kraft-note-moves-the-deadline",
        ),
        pytest.param(
            [b("B", 0), k("K", 100)],
            {},
            (100, None),
            id="deadline-is-kraft-s-created-at-plus-the-timeout",
        ),
        pytest.param([b("B", 0), k("K", 10), _MOVED], {}, (10, 20), id="updated-at-moved"),
    ],
)
def test_a_thread_in_the_conversation(notes, kwargs, expected):
    got = _standing(notes, **kwargs)

    if expected is None:
        assert got is None
    else:
        kraft_at, answered = expected
        assert got == owed.Standing(
            when(kraft_at), when(kraft_at) + TIMEOUT, None if answered is None else when(answered)
        )


def _s(answered=None, kraft_at=0):
    return owed.Standing(
        when(kraft_at), when(kraft_at) + TIMEOUT, None if answered is None else when(answered)
    )


_DEADLINE = int(TIMEOUT.total_seconds())


@pytest.mark.parametrize(
    "standing, read_at, now, expected",
    [
        pytest.param(_s(answered=100), when(50), when(110), True, id="answered-after-read-at"),
        pytest.param(_s(answered=100), when(150), when(160), False, id="answered-before-read-at"),
        pytest.param(_s(answered=100), None, when(110), True, id="no-latest-triage-any-answer"),
        pytest.param(_s(), None, when(_DEADLINE - 1), True, id="unanswered-before-the-deadline"),
        pytest.param(_s(), None, when(_DEADLINE), False, id="unanswered-at-the-deadline"),
        pytest.param(_s(), None, when(_DEADLINE + 1), False, id="unanswered-after-the-deadline"),
        pytest.param(_s(answered=100), when(100), when(110), False, id="answered-at-read-at"),
    ],
)
def test_owed_a_look(standing, read_at, now, expected):
    assert owed.is_owed(standing, read_at=read_at, now=now) is expected


@pytest.mark.parametrize(
    "standing, now, expected",
    [
        pytest.param(None, when(0), True, id="no-standing"),
        pytest.param(_s(answered=100), when(110), True, id="answered"),
        pytest.param(_s(), when(_DEADLINE - 1), False, id="before-deadline"),
        pytest.param(_s(), when(_DEADLINE), True, id="after-deadline"),
    ],
)
def test_a_thread_is_done_for_the_reply_wait(standing, now, expected):
    assert owed.is_done(standing, now=now) is expected
