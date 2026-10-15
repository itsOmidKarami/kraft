"""`kraft.console.live`: the stream, its batching and reconnect, without a
screen. The client is faked at `board_rows`, `stream_events` and `health`."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from kraft import client
from kraft.console import live
from kraft.console.live import Link

GONE = client.ClientError("no Kraft server at http://127.0.0.1:1", kind=client.Failure.UNREACHABLE)
DROPPED = client.ClientError("closed the event stream", kind=client.Failure.INTERRUPTED)
TOKEN = client.ClientError("kraft 403: refused", kind=client.Failure.UNAUTHORIZED, status=403)
BROKEN = client.status_error(500, "database disk image is malformed")
#: In a scripted stream: it stays open, quiet, from here on.
OPEN = object()


def _script(monkeypatch, boards: list, streams: list) -> list[int]:
    """Fake the client: each `board_rows()` call takes the next entry of
    `boards`, each `stream_events()` the next of `streams`: a list of events
    to yield, where an exception is raised, OPEN waits, and the end is a clean
    close. Once a script runs out the call waits forever. Returns the
    `after_seq` each stream was opened from."""
    after: list[int] = []

    async def board_rows():
        if not boards:
            await asyncio.Event().wait()
        answer = boards.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if len(answer) == 3:  # (rows, cursor, seconds the read takes)
            await asyncio.sleep(answer[2])
            return answer[:2]
        return answer

    async def stream_events(after_seq=0):
        after.append(after_seq)
        if not streams:
            await asyncio.Event().wait()
        for event in streams.pop(0):
            if isinstance(event, float):  # a pause before the next event
                await asyncio.sleep(event)
                continue
            if event is OPEN:
                await asyncio.Event().wait()
            if isinstance(event, Exception):
                raise event
            yield event

    async def health():
        return {"status": "ok"}

    monkeypatch.setattr(client, "board_rows", board_rows)
    monkeypatch.setattr(client, "stream_events", stream_events)
    monkeypatch.setattr(client, "health", health)
    monkeypatch.setattr(live, "BATCH_S", 0.01)
    monkeypatch.setattr(live, "BACKOFF_S", (0.01,))
    return after


async def _run_until(watched: live.Live, done) -> None:
    """Run `watched` until `done()`, then a few batches longer, so a call that
    should not happen has the time to."""
    task = asyncio.create_task(watched.run())
    try:
        async with asyncio.timeout(5):
            while not done():
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _recording():
    seen = {"events": [], "links": [], "health": []}
    watched = live.Live(
        on_event=lambda rows, events: seen["events"].append([e["seq"] for e in events]),
        on_health=seen["health"].append,
        on_link=lambda link, error: seen["links"].append(link),
    )
    return watched, seen


@pytest.mark.parametrize(
    ("boards", "stream", "delivered", "after"),
    [
        ([([], 7), ([], 10)], [{"seq": 8}, {"seq": 9}, {"seq": 10}, OPEN], [[], [8, 9, 10]], [7]),
        (
            [([], 7), ([], 8, 0.1), ([], 9)],
            [{"seq": 8}, 0.05, {"seq": 9}, OPEN],
            [[], [8], [9]],
            [7],
        ),
        (
            [([], 7), GONE, ([], 9)],
            [{"seq": 8}, 0.05, {"seq": 9}, OPEN],
            [[], [8, 9]],
            [7],
        ),
    ],
    ids=["burst", "event-during-the-read", "read-fails-keeps-the-batch"],
)
async def test_a_burst_of_events_reads_the_board_once(
    monkeypatch, boards, stream, delivered, after
):
    """Three events in a burst are one redraw, and the stream starts at the
    board's cursor rather than replaying every event the server ever wrote.
    An event that arrives while the board is being read is not left waiting
    for the next one."""
    opened = _script(monkeypatch, boards + [([], 10)] * 3, [stream])
    watched, seen = _recording()
    await _run_until(watched, lambda: len(seen["events"]) >= len(delivered))
    assert seen["events"] == delivered
    assert opened == after


@pytest.mark.parametrize(
    "ending",
    [[{"seq": 8}], [{"seq": 8}, DROPPED]],
    ids=["clean-close", "dropped"],
)
async def test_a_stream_that_ends_reconnects_from_the_last_event_seen(monkeypatch, ending):
    """A clean close ends the iterator with no error; it is a drop all the
    same. The board is read again and the stream resumes after event 8, not
    from the new cursor, so nothing in between is missed."""
    after = _script(monkeypatch, [([], 7), ([], 12)], [ending])
    watched, seen = _recording()
    await _run_until(watched, lambda: len(after) == 2)
    assert after == [7, 8]
    assert seen["links"] == [Link.LIVE, Link.RECONNECTING, Link.LIVE]


@pytest.mark.parametrize(
    ("boards", "streams", "grace", "links"),
    [
        ([([], 7)], [[TOKEN]], 15.0, [Link.LIVE, Link.DOWN]),
        ([GONE], [], 15.0, [Link.DOWN]),
        ([([], 7), GONE], [[]], 15.0, [Link.LIVE, Link.RECONNECTING, Link.RECONNECTING]),
        ([([], 7), GONE], [[]], 0.0, [Link.LIVE, Link.RECONNECTING, Link.DOWN]),
        ([BROKEN] * 3, [], 15.0, [Link.RECONNECTING, Link.RECONNECTING, Link.DOWN]),
        (
            [BROKEN, BROKEN, ([], 7), BROKEN],
            [[]],
            15.0,
            [Link.RECONNECTING] * 2 + [Link.LIVE] + [Link.RECONNECTING] * 2,
        ),
    ],
    ids=[
        "token-refused",
        "never-up",
        "restarting",
        "gone-past-grace",
        "server-error-stuck",
        "server-error-passes",
    ],
)
async def test_what_the_dot_says_after_a_failure(monkeypatch, boards, streams, grace, links):
    """A refused token is down at once, never "reconnecting" to a server that
    will refuse again. A server that was live and stops answering reads as
    reconnecting for the grace a restart needs, then as down. A server that
    answers every board read with an error is down after a few, so its words
    are shown; one that recovers between errors starts the count over."""
    _script(monkeypatch, boards, streams)
    monkeypatch.setattr(live, "GRACE_S", grace)
    watched, seen = _recording()
    await _run_until(watched, lambda: len(seen["links"]) >= len(links))
    assert seen["links"][: len(links)] == links


async def test_the_health_poll_reports_no_answer_as_none(monkeypatch):
    _script(monkeypatch, [], [])

    async def health():
        raise GONE

    monkeypatch.setattr(client, "health", health)
    watched, seen = _recording()
    await _run_until(watched, lambda: seen["health"])
    assert seen["health"][0] is None


@pytest.mark.parametrize(
    ("stream", "waits"),
    [
        ([], [0.011, 0.022, 0.033, 0.033]),
        ([{"seq": 8}], [0.011, 0.011, 0.011, 0.011]),
    ],
    ids=["closes-at-once", "delivered-an-event"],
)
async def test_the_backoff_grows_until_a_stream_has_worked(monkeypatch, stream, waits):
    """The board answering is not the stream working: a stream that closes the
    moment it opens must walk the backoff, and one that delivered an event starts over."""
    _script(monkeypatch, [([], 7)] * 12, [stream] * 6)
    monkeypatch.setattr(live, "BACKOFF_S", (0.011, 0.022, 0.033))
    seen_waits: list[float] = []
    rest = live.Live._rest

    async def spy(self, seconds):
        seen_waits.append(seconds)
        await rest(self, seconds)

    monkeypatch.setattr(live.Live, "_rest", spy)
    watched, seen = _recording()
    await _run_until(watched, lambda: len(seen["links"]) >= 8)
    assert seen_waits[:4] == waits


async def test_a_server_that_answers_again_is_followed_at_once(monkeypatch):
    """The health poll hearing the server back cuts the backoff short, so "No
    server" clears when the server is up, not up to a backoff later."""
    _script(monkeypatch, [GONE, ([], 7)], [])
    monkeypatch.setattr(live, "BACKOFF_S", (60.0,))
    monkeypatch.setattr(live, "HEALTH_EVERY_S", 0.05)
    answers = iter([GONE])

    async def health():
        if next(answers, None) is not None:
            raise GONE
        return {"status": "ok"}

    monkeypatch.setattr(client, "health", health)
    watched, seen = _recording()
    await _run_until(watched, lambda: Link.LIVE in seen["links"])
    assert seen["links"][:2] == [Link.DOWN, Link.LIVE]


async def test_a_view_that_fails_ends_the_run_not_just_a_task(monkeypatch):
    """Only a ClientError is expected: anything else a callback raises must
    reach whoever runs `run()`, not die unseen in a background task."""
    _script(monkeypatch, [([], 7), ([], 8)], [[{"seq": 8}, OPEN]])

    def on_event(rows, events):
        if events:
            raise RuntimeError("a view broke")

    watched = live.Live(on_event=on_event, on_health=lambda h: None, on_link=lambda *a: None)
    with pytest.raises(ExceptionGroup) as raised:
        async with asyncio.timeout(5):
            await watched.run()
    assert raised.value.exceptions[0].args == ("a view broke",)
