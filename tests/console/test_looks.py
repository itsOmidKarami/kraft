"""What the console's surfaces look like where nothing says it in words: a
dialog keeps the screen behind it in sight, the accidents (scrollbars, focus
frames, diff headers, group headings) stay quieter than the work, and a
toggle's state is a mark, not only a colour."""

from __future__ import annotations

from functools import partial

import pytest
from support.console_board import GATE, _settled
from textual.color import Color
from textual.widgets import Checkbox, Input, OptionList, RichLog

from kraft.console import theme
from kraft.console.dialogs import Confirm, Note
from kraft.console.screens.new_item import NewItem

DIALOGS = {
    "confirm": lambda: Confirm("kraft admin stop", ["This stops the server."]),
    "note": lambda: Note("Reject spec_approval", required=True),
    "new-item": lambda: NewItem(None),
}


@pytest.mark.parametrize("dialog", list(DIALOGS))
@pytest.mark.parametrize("look", ["kraft-dark", "kraft-light"])
def test_a_dialog_keeps_the_screen_behind_it_in_sight(console, dialog, look):
    """`Screen`'s opaque background must not reach a ModalScreen: the spec a
    person rejects stays readable, dimmed, while they write why."""

    async def scenario(app, pilot):
        app.theme = look
        app.run_worker(partial(app.push_screen_wait, DIALOGS[dialog]()))
        await console.until(pilot, lambda: app.screen is not app.default_screen)
        await pilot.pause()
        return app.screen.styles.background.a, app.default_screen.styles.background.a

    behind, frame = console.run(scenario)
    assert 0 < behind < 1
    assert frame == 1


@pytest.mark.parametrize("look", ["kraft-dark", "kraft-light"])
def test_scrollbars_and_focus_frames_stay_quieter_than_the_work(console, look):
    """One cell wide, the thumb in the faint colour, not the text's; a focused
    field's frame is the neutral focus colour at half strength, never the
    accent, and an unfocused one is not the surface it sits on (it would read
    as disabled)."""
    tokens = theme.DARK if look == "kraft-dark" else theme.LIGHT

    async def scenario(app, pilot):
        app.theme = look
        await pilot.pause()
        log = app.query_one("#peek-body", RichLog)
        app.run_worker(partial(app.push_screen_wait, Note("Why", required=True)))
        await console.until(pilot, lambda: isinstance(app.screen, Note))
        await pilot.pause()
        field = app.screen.query_one(Input)
        focused = field.styles.border_top[1].hex
        app.screen.set_focus(None)
        await pilot.pause()
        return (
            log.styles.scrollbar_size_vertical,
            log.styles.scrollbar_color.hex,
            focused,
            field.styles.border_top[1].hex,
        )

    width, thumb, focused, blurred = console.run(scenario)
    assert width == 1
    assert thumb.lower() == tokens["text-faint"]
    assert focused.lower() not in (tokens["accent"], tokens["surface"])
    half_focus = Color.parse(tokens["surface"]).blend(Color.parse(tokens["focus"]), 0.5)
    assert focused.lower() == half_focus.hex.lower()
    assert blurred.lower() == tokens["border"]


@pytest.mark.parametrize(
    ("line", "style"),
    [
        ("diff --git a/x.py b/x.py", "kraft-muted"),
        ("index 1a2b3c..4d5e6f 100644", "kraft-muted"),
        ("--- a/x.py", "kraft-muted"),
        ("+++ b/x.py", "kraft-muted"),
        ("+added", "success"),
        ("-removed", "error"),
        ("@@ -1 +1 @@", "kraft-info"),
        (" context", ""),
    ],
    ids=["diff-git", "index", "old-file", "new-file", "add", "delete", "hunk", "context"],
)
def test_a_diffs_file_headers_are_muted_and_its_changes_lead(console, line, style):
    async def scenario(app, pilot):
        return str(app.query_one("#item")._diff_line(line).style), app.theme_variables

    drawn, colours = console.run(scenario)
    assert drawn == colours.get(style, style)


def test_a_group_heading_is_a_title_in_the_accent_with_its_count_in_the_groups_colour(board):
    """A heading is the panel's titled separator: no bold, the label in the
    accent, the count the colour of what it counts, faint at 0. Empty groups
    share one line, joined in the frame's colour."""
    console = board([GATE])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        options = app.query_one("#rows", OptionList).options
        return {
            option.id: [
                (span.style, option.prompt.plain[span.start : span.end])
                for span in option.prompt.spans
                if span.end > span.start
            ]
            for option in options
            if option.id in ("group:needs", "group:done")
        }

    empty = [("$kraft-faint", "0")]
    assert console.run(scenario) == {
        "group:needs": [("$kraft-accent", "Needs you"), ("$warning", "1")],
        "group:done": [
            ("$kraft-accent", "Running"),
            *empty,
            ("$kraft-frame", " ─ "),
            ("$kraft-accent", "Not started"),
            *empty,
            ("$kraft-frame", " ─ "),
            ("$kraft-accent", "Done"),
            *empty,
        ],
    }


def test_a_toggle_says_on_and_off_by_its_mark_not_only_its_colour(console):
    """Colour never carries meaning alone (PRODUCT.md): `✓` on, `·` off."""

    async def scenario(app, pilot):
        app.run_worker(partial(app.push_screen_wait, NewItem(None)))
        await console.until(pilot, lambda: isinstance(app.screen, NewItem))
        await pilot.pause()

        def marks() -> list[str]:
            boxes = app.screen.query(Checkbox).results(Checkbox)
            return [str(box.render()).split(" ")[0] for box in boxes]

        before = marks()
        for box in app.screen.query(Checkbox).results(Checkbox):
            box.toggle()
        await pilot.pause()
        return before, marks()

    assert console.run(scenario) == (["▐✓▌", "▐·▌"], ["▐·▌", "▐✓▌"])


def test_a_focused_button_is_underlined_not_inverted(console):
    """Textual's focused label is reversed: an inverted pill inside the button
    that reads as a drawing glitch."""

    async def scenario(app, pilot):
        app.run_worker(partial(app.push_screen_wait, DIALOGS["confirm"]()))
        await console.until(pilot, lambda: isinstance(app.screen, Confirm))
        await pilot.pause()
        return str(app.screen.query_one("#no").styles.text_style)

    style = console.run(scenario)
    assert "underline" in style and "reverse" not in style


@pytest.mark.parametrize("tokens", [theme.DARK, theme.LIGHT], ids=["dark", "light"])
def test_selection_and_focus_stay_neutral_when_the_look_has_an_accent(tokens):
    """The default look's accent is violet, but a menu's cursor, a focused
    frame and a primary button stay the text colour, as on the web; the
    accent is `$kraft-accent`, for the console's own marks."""
    from kraft.console.app import _theme

    look = _theme("kraft-test", tokens, dark=tokens is theme.DARK)
    assert tokens["accent"] != tokens["selection"]
    assert look.primary.lower() == tokens["selection"]
    assert look.variables["kraft-accent"] == tokens["brand"]
