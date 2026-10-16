"""Ctrl+P's Kraft entries (spec §7.7): a screen, an item, a verb."""

from __future__ import annotations

import pytest
from support.console_board import DONE, FAILED, GATE, _settled
from textual.command import CommandList


@pytest.mark.parametrize(
    ("typed", "first", "view", "selected", "prompt"),
    [
        ("rename", "m03d77b1  Rename the [/] parser", "board", "m03d77b1", ""),
        ("Repos", "Repos", "repos", None, ""),
        ("item retry", "/item retry", "board", None, "/item retry "),
    ],
    ids=["an-item-by-title", "a-screen", "a-verb"],
)
def test_ctrl_p_goes_to_an_item_a_screen_or_a_verb(board, typed, first, view, selected, prompt):
    """Spec §7.7: Ctrl+P finds an item by its title (with `[/]` in it, which
    the palette reads as markup from a str), a screen, or a verb, which it
    types at the prompt. The item is selected on the Board even where a
    filter hid it."""
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
        chosen = app.query_one("#board").selected
        return app.view.id, chosen and chosen["id"], app.query_one("#prompt").value

    assert board(rows).run(scenario) == (view, selected, prompt)
