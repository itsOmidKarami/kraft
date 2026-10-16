"""Life where work runs (`kraft.console.pulse`): the spinner and the running
clock, moved by the app's one clock, which is stepped here by hand."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from support.console_board import GATE, NOW, RUNNING, _detail, _iso, _opened, _serve, _settled

from kraft import client
from kraft.console import panels, pulse
from kraft.console.screens import board as board_view


@pytest.mark.parametrize(
    ("seconds", "took"),
    [(0, "0s"), (48.9, "48s"), (252, "4m 12s"), (3852, "1h 4m"), (-5, "0s")],
    ids=["none", "seconds", "minutes", "hours", "clock-skew"],
)
def test_a_working_line_reads_its_time_to_the_second(seconds, took):
    """`4m 12s`; a server clock ahead of this one reads 0."""
    assert pulse.took(seconds) == took


@pytest.mark.parametrize(
    ("status", "spins"),
    [("running", True), ("escalated", True), ("waiting", False), ("queued", False)],
    ids=["running", "escalated", "waiting", "queued"],
)
def test_only_work_that_is_happening_spins(status, spins):
    """A queued or waiting item is in Running, but nothing works on it now."""
    assert pulse.spins({"display_status": status}) is spins


class Recorded:
    """The pulse's timer, stepped by the test instead of the clock."""

    def __init__(self) -> None:
        self.paused = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False


@pytest.fixture
def stepped(monkeypatch):
    """The app's pulse on a `Recorded` timer, and its clock at `NOW + 5`."""

    def wake(self) -> None:
        if self._timer is None:
            self._timer = Recorded()
        self._timer.resume()

    monkeypatch.setattr(pulse.Pulse, "wake", wake)
    monkeypatch.setattr(pulse, "time", SimpleNamespace(time=lambda: NOW + 5))


def _cells(app, wid: str) -> tuple[str, str]:
    """The bar cell and the age column of `wid`'s row, as drawn."""
    rows = app.query_one("#rows", panels.Rows)
    index = next(k for k, o in enumerate(rows.options) if o.id == wid)
    line = rows.render_line(rows._index_to_line[index] - rows.scroll_offset.y).text
    x = panels.SIDE if rows.framed else 0
    return line[x], line[x + rows.inner - board_view.AGE_WIDTH : x + rows.inner].strip()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)], ids=["compact", "framed"])
def test_a_running_rows_spinner_moves_by_the_pulse_and_its_age_stays_relative(board, stepped, size):
    """Each step draws the next of Claude Code's frames on the bar cell. The
    list gives no session's start, so the age reads as every row's does
    (`4m`), never a clock that could pass for a time of day."""
    console = board([RUNNING, GATE])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.pause()  # laid out: the rows have lines to draw
        app.pulse.tick = 0
        seen = [_cells(app, RUNNING["id"])]
        for _ in range(2):
            app.pulse.step()
            seen.append(_cells(app, RUNNING["id"]))
        return seen, _cells(app, GATE["id"])

    seen, still = console.run(scenario, size=size)
    assert seen == [("·", "4m"), ("✢", "4m"), ("✳", "4m")]
    assert still == ("▌", "12m")


def test_a_beat_repaints_the_spinner_cell_never_the_list(board, stepped):
    """Brief rule: a spinner redraws its own cell, one cell a beat."""
    console = board([RUNNING, GATE])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.pause()
        rows = app.query_one("#rows", panels.Rows)
        painted = []
        rows.refresh = lambda *regions, **kw: painted.append(regions)
        rows.beat(1)
        rows.beat(2)
        return list(painted)

    assert [r.size for (r,) in console.run(scenario)] == [(1, 1), (1, 1)]


@pytest.mark.parametrize(
    ("rows", "where", "ticking"),
    [
        ([RUNNING], "board", True),
        ([GATE], "board", False),
        ([RUNNING], "server", False),
    ],
    ids=["running-shown", "nothing-running", "board-hidden"],
)
def test_the_board_beats_only_while_running_rows_are_on_screen(
    board, stepped, rows, where, ticking
):
    """No work while hidden or idle: the Board's beat says no, so the clock
    can stop."""
    console = board(rows)

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        app.show(where)
        await pilot.pause()
        return app.query_one("#rows", panels.Rows).beat(1)

    assert console.run(scenario) is ticking


def test_the_clock_stops_once_nothing_running_is_on_screen(board, stepped):
    """The one clock pauses on the first step no beat wants, and a row that
    starts running wakes it."""
    console = board([GATE])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        app.pulse.wake()
        app.pulse.step()
        idle = app.pulse._timer.paused
        app._live_event([GATE, RUNNING], [{"seq": 1}])
        await pilot.pause()
        return idle, app.pulse._timer.paused

    assert console.run(scenario) == (True, False)


#: A session at work since 4 minutes before `NOW`, the clock 5 seconds later.
SESSION = {"id": "s1", "status": "running", "started_at": _iso(4), "node_id": "implement"}


@pytest.mark.parametrize(
    ("where", "hide", "beats"),
    [
        ("#item-work", None, True),
        ("#item-work", "board", False),
        ("#status-spin", None, True),
        ("#status-spin", "tiny", False),
    ],
    ids=["item-shown", "item-hidden", "status-shown", "status-hidden"],
)
def test_a_working_line_and_the_status_spinner_beat_only_on_screen(
    board, stepped, monkeypatch, where, hide, beats
):
    """Left for the Board, the Item's working line does no work; a window too
    small to draw hides the status line, whose spinner then stops too."""
    _serve(monkeypatch, _detail(RUNNING, worker_sessions=[SESSION]), calls=[])
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        if hide == "board":
            app.show("board")
        elif hide == "tiny":
            await pilot.resize_terminal(50, 10)
        await pilot.pause()
        return app.query_one(where).beat(1)

    assert console.run(scenario, size=(120, 40)) is beats


@pytest.mark.parametrize(
    ("sessions", "where", "said"),
    [
        ([SESSION], "#item-work", "implement · task 3 of 5 · 4m 5s"),
        ([SESSION], "#peek-work", "implement · task 3 of 5 · 4m 5s · $0.420"),
        ([SESSION | {"status": "done"}], "#item-work", "implement · task 3 of 5"),
        ([], "#peek-work", "implement · task 3 of 5 · $0.420"),
        ([SESSION | {"started_at": _iso(1)}], "#item-work", "implement · task 3 of 5 · 1m 5s"),
    ],
    ids=["item", "peek-names-the-spend", "no-session-running", "no-session", "session-not-row"],
)
def test_a_running_items_working_line_is_timed_by_its_running_session(
    board, stepped, monkeypatch, sessions, where, said
):
    """`✻ implement · task 3 of 5 · 4m 5s`, timed from the running session's
    `started_at` as the web's item page times it, never from `updated_at`;
    no session running, no time. The Item's header already says the spend."""
    running = RUNNING | {"progress": {"current": 3, "total": 5, "tasks": []}, "updated_at": _iso(0)}
    detail = _detail(running, budget_cap={"spent_usd": 0.42}, worker_sessions=sessions)
    _serve(monkeypatch, detail, calls=[])
    monkeypatch.setattr(client, "stream_log", _no_log)
    console = board([running])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        if where == "#item-work":
            await _opened(console, app, pilot, running["id"])
        else:
            await console.until(pilot, lambda: app.query_one(where).display)
        work = app.query_one(where, pulse.Working)
        return work.display, str(work.query_one(".working-words").render())

    assert console.run(scenario, size=(120, 40)) == (True, said)


async def _no_log(*args, **kwargs):
    return
    yield
