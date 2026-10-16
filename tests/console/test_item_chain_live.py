"""The Chain tab's keys and hints, and what a live read does to it: its
events, its cursor, its scroll and its running session's clock."""

from __future__ import annotations

import pytest
from support.console_board import _detail, _event, _iso, _opened, _row, _serve, _serve_events
from support.console_chain import (
    ID,
    REVIEWED,
    _chain_tab,
    _done,
    _flow,
    _running,
    _session,
    _words,
)
from textual.widgets import RichLog

from kraft.console.screens import item as item_view

LATER = {"updated_at": "2026-10-16T00:00:00+00:00"}


def _live(app, detail, events=None) -> None:
    app.view.rows_changed([detail | LATER], events or [{"seq": 9}])


async def _read_again(console, pilot, app, detail, change) -> None:
    """Change what the server says, let a live read bring it, and wait for it."""
    change(detail)
    detail.update(LATER)
    _live(app, detail)
    await console.until(
        pilot,
        lambda: (
            app.view._key[0] == LATER["updated_at"]
            and not any(w.group == "read" and w.is_running for w in app.view.workers)
        ),
    )
    await pilot.pause()


@pytest.mark.parametrize(
    ("keys", "state"),
    [
        (["enter", "left"], (False, "chain", "item")),
        (["enter", "escape"], (False, "chain", "item")),
        (["escape"], (False, None, "board")),
        (["enter", "right"], (True, "activity", "item")),
        (["enter", "left_square_bracket"], (True, "overview", "item")),
        (["enter", "shift+tab"], (True, "overview", "item")),
        (["left"], (False, "overview", "item")),
    ],
    ids=[
        "left-closes",
        "escape-closes",
        "escape-leaves-with-no-node-open",
        "right-switches",
        "bracket-switches",
        "shift-tab-switches",
        "left-on-the-list-switches",
    ],
)
def test_left_and_escape_close_an_open_node_and_the_other_tab_keys_switch(
    board, monkeypatch, keys, state
):
    """In an open node ← and Esc close it and nothing else does; →, `]`, `[`
    and Shift+Tab switch tabs; with no node open Esc goes back and ← switches."""
    detail = _flow(_session("implement", "author", 1, 20))
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.press(*keys)
        await pilot.pause()
        view = app.view  # the Board, once Esc has gone back to it
        return getattr(view, "chain_open", False), getattr(view, "tab", None), view.id

    assert console.run(scenario) == state


def test_the_hints_name_the_key_that_closes_an_open_node(board, monkeypatch):
    """Footer, `?` and the panel's edge say `esc close` and `← close · → tabs`
    while a node is open, and the tabs' keys otherwise."""
    detail = _flow(_session("implement", "author", 1, 20))
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        head = app.query_one("#item-head")
        closed = (head.border_subtitle, ("← esc", "close") in app.screen_keys())
        await pilot.press("enter")
        await pilot.pause()
        footer = app.screen.active_bindings
        opened = (
            head.border_subtitle,
            ("← esc", "close") in app.screen_keys(),
            footer["escape"].binding.description,
            footer["left"].binding.description,
        )
        await pilot.press("left_square_bracket")
        await pilot.pause()
        return closed, opened, head.border_subtitle

    assert console.run(scenario) == (
        ("← → tabs", False),
        ("← close · → tabs", True, "close", "close"),
        "← → tabs",
    )


def test_a_key_that_opens_nothing_does_nothing_without_a_chain(board, monkeypatch):
    """Enter on a chain with no nodes opens nothing, so Esc then leaves the item."""
    row = _row(ID, "running", current_node_id=None, chain_definition={"nodes": []})
    _serve(monkeypatch, _detail(row), calls=[])
    _serve_events(monkeypatch, [])
    console = board([_detail(row)])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, ID)
        app.view.show_tab("chain")
        await pilot.pause()
        await pilot.press("enter")
        opened = app.view.chain_open
        await pilot.press("escape")
        await pilot.pause()
        return opened, app.view.id

    assert console.run(scenario) == (False, "board")


def test_the_whole_event_log_is_read_once_then_topped_up(board, monkeypatch):
    """All events, not a window: an early gate decision under hundreds of later
    events is still told; a live event reads only what came after the last."""
    history = [_event(1, "gate_approved", 900, gate="spec_approval", by="human")]
    history += [_event(n, "node_started", 800 - n, node="plan") for n in range(2, 603)]
    detail = _done()
    _serve(monkeypatch, detail, calls=[])
    reads = _serve_events(monkeypatch, history)
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.press("home", "down", "enter")
        await console.until(pilot, lambda: any("approved by you" in line for line in _words(app)))
        history.append(_event(603, "node_started", 0, node="plan"))
        _live(app, detail, [history[-1]])
        await console.until(pilot, lambda: len(reads) == 2)
        return reads

    assert console.run(scenario) == [
        {"after_seq": 0, "limit": None, "before_seq": None},
        {"after_seq": 602, "limit": None, "before_seq": None},
    ]


def test_a_new_session_does_not_move_the_cursor_off_the_one_it_is_on(board, monkeypatch):
    """The session under the cursor is kept by what it is, not where it is: a
    newer one starting on top leaves the cursor on the older, and Enter opens it."""
    older = _session("implement", "author", 1, 20)
    detail = _flow(older, chain_definition=REVIEWED)
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.press("up", "enter")
        newer = _session("implement", "author", 2, 2, attempt=2)
        await _read_again(
            console, pilot, app, detail, lambda d: d.update(worker_sessions=[older, newer])
        )
        lit = [line for line in _words(app) if line.startswith("▸ author")]
        await pilot.press("enter")
        return lit, app.view.session_id

    assert console.run(scenario) == (
        ["▸ author · attempt 1 · done · 1m · $0.500"],
        "s-implement-author-1",
    )


def test_a_live_read_leaves_a_reader_who_paged_down_where_they_are(board, monkeypatch):
    """Paging down an open node's long plan is not undone by the next event:
    only a cursor key or an opened node brings the view back."""
    tasks = [{"n": n, "title": f"Task {n}", "state": "pending"} for n in range(1, 41)]
    detail = _done(progress={"current": 1, "total": 40, "title": "x", "tasks": tasks})
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.press("enter", "pagedown", "pagedown")
        log = app.query_one("#chain", RichLog)
        paged = log.scroll_y
        await _read_again(console, pilot, app, detail, lambda d: d.update(title="again"))
        return paged > 0, log.scroll_y == paged

    assert console.run(scenario, size=(80, 24)) == (True, True)


def test_a_running_session_in_an_open_node_ticks(board, monkeypatch):
    """The open node's running session shows the working line's time, moving
    with the clock; an ended one stays as it was drawn."""
    detail = _flow(_running(), _session("implement", "reviewer", 2, 30))
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.press("enter")
        before = [line for line in _words(app) if "author" in line]
        now = item_view.time.time()
        app.view._now = lambda: now + 65
        await console.until(pilot, lambda: any("6m 5s" in line for line in _words(app)))
        return before, [line for line in _words(app) if "reviewer" in line]

    assert console.run(scenario) == (
        ["▸ author · attempt 1 · * running · 5m 0s"],
        ["reviewer · attempt 1 · done · 1m · $0.500"],
    )


def test_a_hidden_chain_pane_is_not_drawn_into_by_a_live_read(board, monkeypatch):
    """After the Chain was shown and left for another tab, a live read changes
    nothing in its pane: the next visit draws it as it then stands."""
    running = _running()
    detail = _flow(running)
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.press("enter")
        before = _words(app)
        app.view.show_tab("overview")
        done = {**running, "status": "done", "exited_at": _iso(1)}
        await _read_again(console, pilot, app, detail, lambda d: d.update(worker_sessions=[done]))
        hidden = _words(app)
        app.view.show_tab("chain")
        await console.until(pilot, lambda: _words(app) != before)
        return hidden == before, any("done" in line for line in _words(app) if "author" in line)

    assert console.run(scenario) == (True, True)


@pytest.mark.parametrize("name", ["x" * 75], ids=["long-ids"])
def test_a_long_node_name_is_cut_so_the_cursor_stays_in_view(board, monkeypatch, name):
    """A row never wraps, so the cursor's line is the line it is drawn on:
    twelve long names at 80×24 and End leave the last row on screen."""
    nodes = [{"id": f"{name}{n:02d}"} for n in range(12)]
    row = _row(ID, "running", current_node_id=nodes[0]["id"], chain_definition={"nodes": nodes})
    rows, shown = _run_end(board, monkeypatch, _detail(row))
    assert (len(rows), all("…" in line for line in rows), shown) == (12, True, True)


def _run_end(board, monkeypatch, detail):
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.press("end")
        log = app.query_one("#chain", RichLog)
        height = log.scrollable_content_region.height
        last = len(log.lines) - 1
        return _words(app), log.scroll_y <= last < log.scroll_y + height

    return console.run(scenario, size=(80, 24))


def test_the_cursor_has_a_column_of_its_own(board, monkeypatch):
    """The cursor's mark does not touch the node's: marked and unmarked rows
    put the node's mark in the same column."""
    detail = _done()
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        raw = [line.text for line in app.query_one("#chain", RichLog).lines]
        return {line.index("✓") for line in raw}

    assert len(console.run(scenario)) == 1


@pytest.mark.parametrize("keys", [[], ["enter"]], ids=["list", "open-node"])
def test_one_of_a_keys_two_actions_is_live_at_a_time(board, monkeypatch, keys):
    """← and Esc each mean close or the other thing, never both: exactly one of
    `left` and `close_node` acts, and only an open node's close does."""
    detail = _flow(_session("implement", "author", 1, 20))
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.press(*keys)
        view = app.view
        return view.check_action("left", ()), view.check_action("close_node", ())

    assert console.run(scenario) == ((True, False) if not keys else (False, True))


def test_the_cursor_starts_in_view_on_a_chain_longer_than_the_pane(board, monkeypatch):
    """A done item of thirty nodes at 80×24 opens with the cursor, on the last
    node, on screen: nobody scrolls to find where they are."""
    nodes = [{"id": f"node{n:02d}"} for n in range(30)]
    row = _row(ID, "done", current_node_id="node29", chain_definition={"nodes": nodes})
    _serve(monkeypatch, _detail(row), calls=[])
    _serve_events(monkeypatch, [])
    console = board([_detail(row)])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.pause(0.2)
        log = app.query_one("#chain", RichLog)
        return log.scroll_y <= 29 < log.scroll_y + log.scrollable_content_region.height

    assert console.run(scenario, size=(80, 24)) is True
