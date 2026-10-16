"""The workbench's frames (`kraft.console.panels`): the Board as one rounded
panel whose groups are titled separators, the cursor's row opening a second
line, each view's panel and title, and a short window giving the frames up."""

from __future__ import annotations

import pytest
from support.console_board import BOARD, GATE, RUNNING, _lines, _settled
from textual.content import Content

from kraft.console import panels


def _drawn(app) -> list[str]:
    """The Board's list as the terminal shows it, line by line."""
    rows = app.query_one("#rows", panels.Rows)
    return [rows.render_line(y).text.rstrip() for y in range(rows.size.height)]


@pytest.mark.parametrize(
    ("size", "first", "rule", "last"),
    [
        ((120, 40), "╭─ Needs you 2 ─", "├─ Running 1 ─", "╰─"),
        ((80, 24), "── Needs you 2 ─", "── Running 1 ─", None),
    ],
    ids=["framed", "compact"],
)
def test_the_board_is_one_panel_and_its_groups_are_titled_separators(
    board, size, first, rule, last
):
    """Needs you's title is the panel's top edge, each later group's a joint
    in its sides, and the end its bottom edge; under 30 rows the edges go and
    a title is a rule, so every row is kept for the work."""
    console = board(BOARD)

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.pause()
        return _drawn(app)

    drawn = [line for line in console.run(scenario, size=size) if line]
    assert drawn[0].startswith(first)
    assert any(line.startswith(rule) for line in drawn)
    item = next(line for line in drawn if "m03d77b1" in line)
    if last:
        assert drawn[-1].startswith(last) and drawn[-1].endswith("╯")
        assert drawn[0].endswith("╮") and item.startswith("│ ") and item.endswith(" │")
    else:
        assert item.startswith("▌ ") and not any(line[0] in "╭╰│" for line in drawn)


def test_only_the_cursors_row_opens_its_second_line(board):
    """Under its title: the repo, the chain (the default named too) and where
    the item stands on it. Moving the cursor closes the last one."""
    console = board([GATE, RUNNING])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        rows = app.query_one("#rows")

        def opened():
            return [o.id for o in rows.options if "\n" in o.prompt.plain]

        first = opened()
        detail = next(o for o in rows.options if o.id == GATE["id"]).prompt.plain.split("\n")[1]
        await pilot.press("j", "j")
        await pilot.pause()
        return first, detail, opened()

    first, detail, moved = console.run(scenario)
    assert first == [GATE["id"]]
    assert detail == "            r · default · spec → spec_approval ● → plan → implement"
    assert moved == [RUNNING["id"]]


@pytest.mark.parametrize(
    ("key", "selector", "title", "subtitle"),
    [
        ("2", "#server", "Server", ""),
        ("3", "#addons", "Add-ons", ""),
        ("4", "#repos", "Connected repos", "c connects one"),
    ],
    ids=["server", "addons", "repos"],
)
def test_each_view_is_a_panel_titled_in_its_border(board, key, selector, title, subtitle):
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.press(key)
        view = app.query_one(selector)
        return view.has_class("panel"), view.border_title, view.border_subtitle or ""

    assert console.run(scenario) == (True, title, subtitle)


@pytest.mark.parametrize(
    ("size", "prompt", "peek", "item"),
    [
        ((120, 40), 3, ("round", "round"), ("round", "round")),
        ((80, 24), 1, ("solid", ""), ("", "")),
    ],
    ids=["framed", "compact"],
)
def test_a_short_window_gives_up_the_frames_edges(board, size, prompt, peek, item):
    """Under 30 rows the prompt box is one line, the peek's title sits on a
    rule, and the Item's header loses its frame: rows go to the work."""
    console = board([GATE])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        styles = app.query_one("#peek").styles
        head = app.query_one("#item-head").styles
        return (
            app.query_one("#prompt-box").region.height,
            (styles.border_top[0], styles.border_bottom[0]),
            (head.border_top[0], head.border_bottom[0]),
        )

    assert console.run(scenario, size=size) == (prompt, peek, item)


def test_an_80x24_board_keeps_at_least_eight_rows_of_items(board):
    """Brief: the frames cost rows, so a small window must still list work."""
    many = [GATE | {"id": f"{n:08x}"} for n in range(12)]
    console = board(many)

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.pause()
        return sum(1 for line in _drawn(app) if line.lstrip("▌ ")[:8] in {r["id"] for r in many})

    assert console.run(scenario) >= 8


def test_an_empty_board_is_still_one_closed_panel(board):
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.pause()
        return [line for line in _drawn(app) if line], _lines(app)

    drawn, lines = console.run(scenario, size=(120, 40))
    assert [drawn[0][0], drawn[0][-1], drawn[-1][0], drawn[-1][-1]] == ["╭", "╮", "╰", "╯"]
    assert lines == ["Nothing filed yet. n files one."]


@pytest.mark.parametrize(
    ("title", "width", "left", "right", "drawn"),
    [
        ("server.log", 20, "─", "─", "── server.log ──────"),
        ("Running 2", 16, "├", "┤", "├─ Running 2 ──┤"),
        ("", 6, "╰", "╯", "╰────╯"),
    ],
    ids=["rule", "joint", "edge"],
)
def test_a_rule_puts_its_title_on_a_line_its_width(title, width, left, right, drawn):
    line = panels.rule(Content(title), width, left=left, right=right)
    assert (line.plain, line.cell_length) == (drawn, width)
