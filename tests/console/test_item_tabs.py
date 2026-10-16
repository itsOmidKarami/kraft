"""The Item's tabs (spec §6, §7.2): `[` and `]`, and the Activity."""

from __future__ import annotations

import asyncio

import pytest
from support.console_board import GATE, RUNNING, _detail, _iso, _opened, _serve
from textual.widgets import RichLog

from kraft import client
from kraft.console.screens import item as item_view


def _event(seq: int, kind: str, minutes_ago: float, **payload) -> dict:
    return {
        "seq": seq,
        "work_item_id": GATE["id"],
        "type": kind,
        "node_id": payload.pop("node", None),
        "payload": payload,
        "created_at": _iso(minutes_ago),
    }


HISTORY = [
    _event(1, "work_item_created", 90),
    _event(2, "plan_progress", 80),
    _event(3, "node_started", 70, node="spec"),
    _event(4, "gate_requested", 65, gate="spec_approval"),
    _event(5, "gate_rejected", 12, gate="spec_approval", note="vague [/]"),
]


def _serve_events(monkeypatch, history: list[dict]) -> list[dict]:
    """`client.events` over `history`, as the server pages it; returns the reads."""
    reads: list[dict] = []

    async def events(work_item_id=None, after_seq=0, *, limit=None, before_seq=None):
        reads.append({"after_seq": after_seq, "limit": limit, "before_seq": before_seq})
        if before_seq is not None:
            return [e for e in history if e["seq"] < before_seq][-limit:]
        return [e for e in history if e["seq"] > after_seq][:limit]

    monkeypatch.setattr(client, "events", events)
    return reads


def _activity(app) -> list[str]:
    return [line.text.rstrip() for line in app.query_one("#activity", RichLog).lines]


def test_brackets_step_through_the_tabs_and_a_shows_the_overview_first(board, monkeypatch):
    """Spec §6: `[` and `]` step through the tabs, round the end; `a` on
    another tab shows the Overview and approves nothing until pressed there."""
    calls: list[tuple] = []
    _serve(monkeypatch, _detail(GATE), calls=calls)
    _serve_events(monkeypatch, HISTORY)
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        seen = []
        for key in ("right_square_bracket", "left_square_bracket", "left_square_bracket"):
            await pilot.press(key)
            seen.append(app.view.tab)
        await pilot.press("a")
        seen.append((app.view.tab, list(calls)))
        await pilot.press("a")
        await console.until(pilot, lambda: calls)
        return seen, calls

    seen, calls = console.run(scenario)
    # Round the start to the last tab.
    assert seen == ["activity", "overview", item_view.TABS[-1][0], ("overview", [])]
    assert calls == [("approve_gate", ("spec_approval", "a91c03fe", "d1g3st"), {})]


def test_the_activity_reads_newest_first_and_end_reads_earlier(board, monkeypatch):
    """Spec §7.2: the item's events in the web's words, newest first and
    paged: bookkeeping left out, `[/]` as typed, End at the bottom reads the
    page before."""
    _serve(monkeypatch, _detail(GATE), calls=[])
    reads = _serve_events(monkeypatch, HISTORY)
    monkeypatch.setattr(item_view, "PAGE", 3)
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("right_square_bracket")
        await console.until(pilot, lambda: _activity(app))
        first = _activity(app)
        await pilot.press("end")
        await console.until(pilot, lambda: len(reads) == 2)
        await console.until(pilot, lambda: "filed" in " ".join(_activity(app)))
        return first, _activity(app), reads

    first, then, reads = console.run(scenario)
    assert first == [
        "    12m  spec_approval · rejected: vague [/]",
        " 1h 05m  spec_approval is waiting for you",
        " 1h 10m  spec started",
        "End loads earlier events.",
    ]
    assert then == [*first[:3], " 1h 30m  filed"]
    assert reads == [
        {"after_seq": 0, "limit": 3, "before_seq": item_view.NEWEST},
        {"after_seq": 0, "limit": 3, "before_seq": 3},
    ]


@pytest.mark.parametrize(
    ("event", "reads", "top"),
    [
        (_event(6, "node_completed", 0, node="spec"), 2, "    now  spec finished"),
        (_event(6, "node_started", 0, node="x") | {"work_item_id": "elsewhere"}, 1, None),
    ],
    ids=["its-own", "another-items"],
)
def test_a_live_event_adds_its_line_to_the_activity(board, monkeypatch, event, reads, top):
    """A live event about this item reads only what came after the newest
    line; another item's reads nothing."""
    _serve(monkeypatch, _detail(GATE), calls=[])
    history = list(HISTORY)
    got = _serve_events(monkeypatch, history)
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("right_square_bracket")
        await console.until(pilot, lambda: _activity(app))
        history.append(event)
        app.view.rows_changed([GATE], [event])
        await console.until(pilot, lambda: len(got) == reads)
        await pilot.pause()
        return [r["after_seq"] for r in got], _activity(app)[0]

    after, first = console.run(scenario)
    assert after == [0, 5][:reads]
    assert first == (top or "    12m  spec_approval · rejected: vague [/]")


SESSIONS = [
    {"id": "s1", "node_id": "implement", "hook_point": "implement.main.author", "status": "done"},
    {
        "id": "s2",
        "node_id": "verification",
        "hook_point": "verification.review.code_review",
        "status": "running",
    },
]
LONG = "reviewing [/] " + "word " * 20


def _log(app) -> list[str]:
    return [line.text.rstrip() for line in app.query_one("#log", RichLog).lines]


def test_the_log_follows_the_newest_session_and_s_steps_to_older_ones(board, monkeypatch):
    """Spec §7.2: the newest session, followed to its end; `s` the next older
    one, offered only with more than one. A long line wraps at the window."""
    _serve(monkeypatch, _detail(RUNNING, worker_sessions=SESSIONS), calls=[])
    _serve_events(monkeypatch, [])

    async def stream_log(work_item_id, session_id, after_line=0):
        yield {"n": 0, "text": LONG.strip() if session_id == "s2" else "wrote code"}

    monkeypatch.setattr(client, "stream_log", stream_log)
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        offered = app.view.check_action("session", ())
        await pilot.press("right_square_bracket", "right_square_bracket")
        await console.until(pilot, lambda: _log(app)[-1:] == ["session ended"])
        newest = _log(app)
        await pilot.press("s")
        await console.until(pilot, lambda: "wrote code" in _log(app))
        await console.until(pilot, lambda: _log(app)[-1:] == ["session ended"])
        return offered, app.view.check_action("session", ()), newest, _log(app)

    offered, on_log, newest, older = console.run(scenario, size=(60, 30))
    assert (offered, on_log) == (False, True)
    head = "verification.review.code_review · running · session 1 of 2"
    assert " ".join(" ".join(newest[:-1]).split()) == f"{head} {LONG.strip()}"
    assert max(len(line) for line in newest) <= 60
    assert older == ["implement.main.author · done · session 2 of 2", "wrote code", "session ended"]


def test_a_log_nobody_sees_is_not_followed(board, monkeypatch):
    """Leaving the Log tab, or the Item, stops following the session."""
    _serve(monkeypatch, _detail(RUNNING, worker_sessions=SESSIONS[1:]), calls=[])
    _serve_events(monkeypatch, [])

    async def stream_log(work_item_id, session_id, after_line=0):
        yield {"n": 0, "text": "first"}
        await asyncio.Event().wait()  # a session that never ends
        yield {}

    monkeypatch.setattr(client, "stream_log", stream_log)
    console = board([RUNNING])

    def live(app) -> list:
        return [w for w in app.view.workers if w.group == "log" and w.is_running]

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        seen = []
        for leave in (["left_square_bracket"], ["1"]):  # another tab; the Board
            app.view.show_tab("log")
            await console.until(pilot, lambda: live(app) and "first" in _log(app))
            await pilot.press(*leave)
            await pilot.pause()
            seen.append(live(app))
            app.show("item")
            await pilot.pause()
        return seen

    assert console.run(scenario) == [[], []]
