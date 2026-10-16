"""Ctrl+P's Kraft entries (spec §7.7): a screen, an item, a verb."""

from __future__ import annotations

import pytest
from support.console_board import DONE, FAILED, GATE, _settled
from textual.command import CommandList


@pytest.mark.parametrize(
    ("typed", "first", "view", "opened", "prompt"),
    [
        ("rename", "m03d77b1  Rename the [/] parser", "item", "m03d77b1", ""),
        ("Repos", "Repos", "repos", None, ""),
        ("item retry", "/item retry", "board", None, "/item retry "),
    ],
    ids=["an-item-by-title", "a-screen", "a-verb"],
)
def test_ctrl_p_goes_to_an_item_a_screen_or_a_verb(board, typed, first, view, opened, prompt):
    """Spec §7.7: Ctrl+P finds an item by its title (with `[/]` in it, which
    the palette reads as markup from a str) and opens it, even one a filter
    hid; a screen; or a verb, which it types at the prompt."""
    rows = [GATE, FAILED | {"title": "Rename the [/] parser"}, DONE]

    async def scenario(app, pilot):
        await _settled(board(rows), app, pilot)
        await pilot.press("f", *"zzz", "enter")
        await pilot.press("ctrl+p", *typed)
        listed = app.screen.query_one(CommandList)

        def best() -> str:
            option = listed.highlighted_option
            return option.prompt.plain if option else ""

        # The palette gathers hits in batches: wait for the best one.
        await board(rows).until(pilot, lambda: best().startswith(first))
        await pilot.press("enter")
        await board(rows).until(pilot, lambda: app.screen is app.default_screen)
        await pilot.pause()
        return app.view.id, getattr(app.view, "work_item_id", None), app.query_one("#prompt").value

    assert board(rows).run(scenario) == (view, opened, prompt)


def test_an_item_without_a_title_is_still_listed(console):
    async def scenario(app, pilot):
        app.rows = [{"id": "x1x1x1x1aaaa", "title": None, "display_status": "running"}]
        return [title for title, _, _ in app.places() if title.startswith("x1x1x1x1")]

    assert console.run(scenario) == ["x1x1x1x1  "]
