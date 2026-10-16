"""Life where work runs (`kraft.console.pulse`): the spinner and the running
clock, moved by the app's one clock, which is stepped here by hand."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from support.console_board import GATE, NOW, RUNNING, _detail, _opened, _serve, _settled

from kraft.console import panels, pulse


@pytest.mark.parametrize(
    ("seconds", "clock", "took"),
    [
        (0, "0:00", "0s"),
        (48.9, "0:48", "48s"),
        (252, "4:12", "4m 12s"),
        (3852, "1:04:12", "1h 4m"),
        (100 * 3600, "4d 4h", "100h 0m"),
        (-5, "0:00", "0s"),
    ],
    ids=["none", "seconds", "minutes", "hours", "past-the-column", "clock-skew"],
)
def test_a_running_time_reads_to_the_second(seconds, clock, took):
    """The age column's `m:ss` and `h:mm:ss`, days past what fits it; the
    working line's `4m 12s`. A server clock ahead of this one reads 0."""
    assert (pulse.clock(seconds), pulse.took(seconds)) == (clock, took)


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
    """The app's pulse on a `Recorded` timer, and `panels`' clock at `NOW + 5`."""

    def wake(self) -> None:
        if self._timer is None:
            self._timer = Recorded()
        self._timer.resume()

    monkeypatch.setattr(pulse.Pulse, "wake", wake)
    monkeypatch.setattr(panels, "time", SimpleNamespace(time=lambda: NOW + 5))


def _cells(app, wid: str) -> tuple[str, str]:
    """The bar cell and the age column of `wid`'s row, as drawn."""
    rows = app.query_one("#rows", panels.Rows)
    index = next(k for k, o in enumerate(rows.options) if o.id == wid)
    line = rows.render_line(rows._index_to_line[index] - rows.scroll_offset.y).text
    x = panels.SIDE if rows.framed else 0
    return line[x], line[x + rows.inner - rows.age_width : x + rows.inner].strip()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)], ids=["compact", "framed"])
def test_a_running_rows_spinner_and_clock_move_by_the_pulse(board, stepped, size):
    """Each step draws the next of Claude Code's frames on the bar cell, and
    the age column reads the time at the node now, not when it was drawn."""
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
    assert seen == [("·", "4:05"), ("✢", "4:05"), ("✳", "4:05")]
    assert still == ("▌", "12m")


def test_a_beat_repaints_the_spinner_cell_and_the_clock_never_the_list(board, stepped):
    """Brief rule: a spinner redraws its own cells. One cell a beat, and the
    age column once its second has moved."""
    console = board([RUNNING, GATE])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.pause()
        rows = app.query_one("#rows", panels.Rows)
        painted = []
        rows.refresh = lambda *regions, **kw: painted.append(regions)
        rows._second = None
        rows.beat(1)
        first = list(painted)
        painted.clear()
        rows.beat(2)  # the same second
        return first, list(painted), rows.age_width

    first, second, age = console.run(scenario)
    assert [r.size for (r,) in first] == [(1, 1), (age, 1)]
    assert [r.size for (r,) in second] == [(1, 1)]


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


def test_a_running_items_working_line_says_where_how_long_and_what_it_spent(
    board, stepped, monkeypatch
):
    """`✻ implement · task 3 of 5 · 4m 5s · $0.42`, in the peek and on the
    Item; none for an item whose work is not happening."""
    running = RUNNING | {"progress": {"current": 3, "total": 5, "tasks": []}}
    monkeypatch.setattr(pulse, "time", SimpleNamespace(time=lambda: NOW + 5))
    _serve(monkeypatch, _detail(running, budget_cap={"spent_usd": 0.42}), calls=[])
    console = board([running])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, running["id"])
        work = app.query_one("#item-work", pulse.Working)
        return work.display, str(work.query_one(".working-words").render())

    said = "implement · task 3 of 5 · 4m 5s · $0.420"
    assert console.run(scenario, size=(120, 40)) == (True, said)
    assert pulse.working_words(GATE, NOW) == "approve spec · 12m 0s"
