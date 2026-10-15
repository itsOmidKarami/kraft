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
        return answer

    async def stream_events(after_seq=0):
        after.append(after_seq)
        if not streams:
            await asyncio.Event().wait()
        for event in streams.pop(0):
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


async def test_a_burst_of_events_reads_the_board_once(monkeypatch):
    """Three events in a burst are one redraw, and the stream starts at the
    board's cursor rather than replaying every event the server ever wrote."""
    burst = [{"seq": 8}, {"seq": 9}, {"seq": 10}, OPEN]
    after = _script(monkeypatch, [([], 7), ([], 10), ([], 10), ([], 10)], [burst])
    watched, seen = _recording()
    await _run_until(watched, lambda: len(seen["events"]) >= 2)
    assert seen["events"] == [[], [8, 9, 10]]
    assert after == [7]


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
    ],
    ids=["token-refused", "never-up", "restarting", "gone-past-grace"],
)
async def test_what_the_dot_says_after_a_failure(monkeypatch, boards, streams, grace, links):
    """A refused token is down at once, never "reconnecting" to a server that
    will refuse again. A server that was live and stops answering reads as
    reconnecting for the grace a restart needs, then as down."""
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
