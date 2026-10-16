"""The four moments (`kraft.console.moments`): opening says what moved while
the console was closed, a decision's toast leads with its mark and the chain
lights where it went, finished work is said once, and leaving keeps one line."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

import pytest
from support.console_board import (
    DONE,
    FILED,
    GATE,
    NOW,
    RUNNING,
    _detail,
    _iso,
    _opened,
    _serve,
    _settled,
    _text,
)

from kraft import cli, client
from kraft.console import moments
from kraft.console.app import KraftConsole
from kraft.console.screens import item as item_view


def _toasts(app) -> list[str]:
    return [str(toast.render()) for toast in app.screen.query("Toast")]


def _seen(at: float) -> None:
    moments.seen_path().parent.mkdir(parents=True, exist_ok=True)
    moments.seen_path().write_text(json.dumps({"seen_at": at}))


RECENT_DONE = DONE | {"id": "f1f1f1f1", "updated_at": _iso(5)}


@pytest.mark.parametrize(
    ("seen", "rows", "said"),
    [
        (None, [GATE, RUNNING], None),
        (NOW, [GATE, RUNNING], "nothing changed since {at}"),
        (NOW - 20 * 60, [GATE, DONE, RUNNING], "since {at} · nothing finished · 1 needs you"),
        (NOW - 20 * 60, [GATE, RECENT_DONE], "since {at} · 1 finished · 1 needs you"),
        (NOW - 10 * 60, [GATE, RUNNING, DONE], "since {at} · nothing finished · nothing needs you"),
    ],
    ids=["first-run", "nothing-changed", "one-needs-you", "one-finished", "only-moved-on"],
)
def test_opening_says_what_moved_since_the_console_was_last_closed(board, seen, rows, said):
    """D1: counted from the rows whose last change came after the last quit;
    never on a first run, and gone at the first key. Quitting notes when."""
    if seen is not None:
        _seen(seen)
    console = board(rows)

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        shown = app.default_screen.has_class("-since") and _text(app, "#since")
        await pilot.press("j")
        await pilot.pause()
        return shown, app.default_screen.has_class("-since")

    left = time.time()
    shown, after_a_key = console.run(scenario)
    assert shown == (said and said.format(at=moments._clock(seen, time.time())) or False)
    assert after_a_key is False
    assert moments.read_seen() >= left


@pytest.mark.parametrize(
    ("seen", "now", "said"),
    [
        (datetime(2026, 10, 16, 14, 2), datetime(2026, 10, 16, 18, 0), "14:02"),
        (datetime(2026, 10, 14, 9, 5), datetime(2026, 10, 16, 18, 0), "Oct 14 09:05"),
    ],
    ids=["today", "another-day"],
)
def test_the_since_line_names_the_day_only_when_it_is_not_today(seen, now, said):
    assert moments._clock(seen.timestamp(), now.timestamp()) == said


def test_approving_leads_with_the_mark_and_lights_where_the_item_went(board, monkeypatch):
    """D2: the CLI's own sentence after `✓`; the next read that shows the
    item past the gate draws its new node lit, for a moment only."""
    monkeypatch.setattr(item_view, "LIT_S", 0.2)
    detail = _detail(GATE)
    _serve(monkeypatch, detail, calls=[])
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("a")
        await console.until(pilot, lambda: _toasts(app))
        chain = app.query_one("#item-chain")
        before = chain.has_class("-lit")
        detail.update(
            pending_gate=None, current_node_id="plan", stop=None, display_status="running"
        )
        app._live_event([GATE | {"updated_at": _iso(0)}], [])
        await console.until(pilot, lambda: chain.has_class("-lit"))
        lit = {str(span.style) for span in chain.render().spans}
        await console.until(pilot, lambda: not chain.has_class("-lit"))
        after = {str(span.style) for span in chain.render().spans}
        return _toasts(app), before, lit, after

    toasts, before, lit, after = console.run(scenario, notifications=True)
    assert toasts == ["✓ approved spec_approval on a91c03fe"]
    assert before is False
    assert "bold $background on $primary" in lit
    assert "bold $background on $primary" not in after and "bold $primary" in after


MERGED = RUNNING | {"display_status": "done", "mr_ref": {"number": 42, "url": "u"}}
COMPLETED = FILED | {"display_status": "done", "title": "Rename [/] it"}
ARCHIVED = DONE | {"id": "a0a0a0a0", "display_status": "archived"}
START = [RUNNING, FILED, DONE, ARCHIVED]


@pytest.mark.parametrize(
    ("boards", "toasts"),
    [
        ([[MERGED, FILED, DONE, ARCHIVED]], ["✓ merged !42 · Docs per major"]),
        ([[RUNNING, COMPLETED, DONE, ARCHIVED]], ["✓ completed · Rename [/] it"]),
        ([[MERGED, COMPLETED, DONE, ARCHIVED]], ["✓ 2 finished"]),
        ([[RUNNING | {"display_status": "cancelled"}, FILED, DONE, ARCHIVED]], []),
        ([[RUNNING, FILED, DONE, ARCHIVED | {"display_status": "done"}]], []),
        ([START], []),
        (
            [[MERGED, FILED, DONE, ARCHIVED], START, [MERGED, FILED, DONE, ARCHIVED]],
            ["✓ merged !42 · Docs per major"],
        ),
    ],
    ids=[
        "merged",
        "completed",
        "many-collapse",
        "cancelled-is-not-finishing",
        "restored-is-not-finishing",
        "nothing-finished",
        "once-per-item",
    ],
)
def test_work_finishing_while_open_is_said_once(board, boards, toasts):
    """D3: an item a live update moves into done, in the Board's own words
    for its outcome; several at once collapse. Never for the first read,
    whose done items were done before, nor for one restored from the
    archive, and each item once."""
    console = board(START)

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.pause()
        first = _toasts(app)
        for rows in boards:
            app._live_event(rows, [{"seq": 1, "type": "work_item_completed"}])
        await pilot.pause()
        return first, _toasts(app)

    assert console.run(scenario, notifications=True) == ([], toasts)


@pytest.mark.parametrize(
    ("rows", "said"),
    [
        ([GATE, RUNNING, RUNNING | {"id": "r2"}], "kraft console · 1 needs you · 2 running"),
        ([GATE, GATE | {"id": "g2"}], "kraft console · 2 need you"),
        ([RUNNING, FILED], "kraft console · nothing needs you · 1 running on their own"),
        ([FILED, DONE], "kraft console · nothing needs you, and nothing is running"),
    ],
    ids=["needs-and-runs", "needs-only", "runs-on-its-own", "all-quiet"],
)
def test_leaving_keeps_one_line(rows, said):
    """D4 and D5 are one sentence family (`moments.at_rest`)."""
    assert moments.leaving(rows) == said


@pytest.mark.parametrize(
    ("code", "farewell", "printed"),
    [
        (0, "kraft console · 2 need you", "kraft console · 2 need you\n"),
        (1, "kraft console · 2 need you", ""),
        (0, None, ""),
    ],
    ids=["quit", "error", "never-connected"],
)
def test_the_quit_line_is_printed_only_after_a_clean_quit(
    monkeypatch, capsys, code, farewell, printed
):
    """Plain text after the TUI has gone: not on an error, not for a console
    that never read the board."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr(client, "health", lambda: asyncio.sleep(0, {}))

    def run(self):
        self.farewell = farewell
        self._return_code = code

    monkeypatch.setattr(KraftConsole, "run", run)
    with pytest.raises(SystemExit):
        cli.main(["console"])
    assert capsys.readouterr().out == printed


def test_a_console_that_never_read_the_board_notes_nothing(console, monkeypatch):
    async def board_rows(**kwargs):
        raise client.transport._unreachable()

    monkeypatch.setattr(client, "board_rows", board_rows)

    async def scenario(app, pilot):
        return app

    app = console.run(scenario)
    assert (app.farewell, moments.read_seen()) == (None, None)
