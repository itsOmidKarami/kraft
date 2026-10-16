"""The prompt: `/` into it, Tab completing from the CLI's own parser, ↑
recalling, Esc out, and what a typed line does once Enter sends it."""

from __future__ import annotations

import pytest
from textual.widgets._footer import FooterKey

from kraft import client
from kraft.console import runner
from kraft.console.dialogs import Confirm
from kraft.console.prompt import Prompt, complete

ECHO = "import sys; print(' '.join(sys.argv[1:]))"


@pytest.mark.parametrize(
    ("line", "ids", "completed"),
    [
        ("/it", [], ("/item ", [])),
        ("/item ap", [], ("/item approve ", [])),
        ("kraft ad", [], ("kraft admin ", [])),
        (
            "/item set",
            [],
            (
                "/item set-",
                [
                    "set-attachments",
                    "set-chain",
                    "set-node-override",
                    "set-overrides",
                    "set-policy",
                ],
            ),
        ),
        ("/item reject a91c --not", [], ("/item reject a91c --note ", [])),
        ("/item approve a9", ["a91c03fe", "b2c4d6e8"], ("/item approve a91c03fe ", [])),
        ("/item approve a91c03fe a9", ["a91c03fe"], ("/item approve a91c03fe a9", [])),
        ("/item progress 3 a9", ["a91c03fe"], ("/item progress 3 a91c03fe ", [])),
        ("/item reject --note foo a9", ["a91c03fe"], ("/item reject --note foo a91c03fe ", [])),
        (
            '/item reject --note "x y" a9',
            ["a91c03fe"],
            ('/item reject --note "x y" a91c03fe ', []),
        ),
        ("/view search a9", ["a91c03fe"], ("/view search a9", [])),
        ("/admin addon un", [], ("/admin addon uninstall ", [])),
    ],
    ids=[
        "group",
        "verb",
        "after-kraft",
        "several",
        "flag",
        "item-id",
        "id-already-given",
        "progress-task-then-id",
        "after-a-flag-value",
        "after-a-quoted-flag-value",
        "not-an-id",
        "nested-group",
    ],
)
def test_tab_completes_what_the_clis_parser_takes(line, ids, completed):
    """From `cli.build_parser()`, so a verb the CLI gains completes with no
    console change; item ids only where the verb's positional is an id."""
    assert complete(line, ids) == completed


def test_slash_tab_up_and_esc_at_the_prompt(console):
    """`/` from a view lands in the prompt with the `/` typed; Tab is the
    completion, not Textual's focus change; ↑ brings back what ran; Esc
    hands the keys back to the view."""

    async def scenario(app, pilot):
        await pilot.press("slash")
        prompt = app.query_one(Prompt)
        seen = [(app.focused is prompt, prompt.value)]
        await pilot.press("v", "i", "tab")
        seen.append((app.focused is prompt, prompt.value))
        prompt.value = "/repo"
        await pilot.press("enter")
        await console.until(pilot, lambda: app.view.id == "repos")
        await pilot.press("slash", "up")
        seen.append((app.focused is prompt, prompt.value))
        await pilot.press("escape")
        seen.append((app.focused is app.view, prompt.value))
        return seen

    assert console.run(scenario) == [
        (True, "/"),
        (True, "/view "),
        (True, "/repo"),
        (True, ""),
    ]


@pytest.mark.parametrize("server", [True, False], ids=["live", "no-server"])
def test_the_prompt_stays_just_above_the_footer(console, monkeypatch, server):
    """With no server the views give way to a short message, and the prompt
    must not ride up under it."""
    if not server:

        async def gone(**kwargs):
            raise client.transport._unreachable()

        monkeypatch.setattr(client, "board_rows", gone)

    async def scenario(app, pilot):
        await pilot.pause()
        return app.query_one(Prompt).region.y

    assert console.run(scenario) == 22


@pytest.mark.parametrize(
    ("line", "keys", "view", "toast", "ran"),
    [
        ("/admin", [], "server", [], []),
        ("/admin mcp", [], "board", [runner.REFUSED[("admin", "mcp")]], []),
        ("kraft view show a91c", [], "board", [], ["kraft view show a91c", "view show a91c"]),
        ("/admin stop", ["shift+tab", "enter"], "board", [], ["kraft admin stop", "admin stop"]),
        ("/admin stop", ["escape"], "board", [], []),
    ],
    ids=["bare-group", "refused", "runs", "confirmed", "declined"],
)
def test_enter_runs_the_line_opens_its_view_or_says_why_not(
    console, kraft_is, monkeypatch, line, keys, view, toast, ran
):
    kraft_is(ECHO)

    async def no_one_running(status=None, **kwargs):
        return []

    monkeypatch.setattr(client, "list_work_items", no_one_running)

    async def scenario(app, pilot):
        await pilot.press("slash")
        app.query_one(Prompt).value = line
        await pilot.press("enter")
        if keys:
            await console.until(pilot, lambda: isinstance(app.screen, Confirm))
            await pilot.press(*keys)
        await pilot.pause(0.1)
        await console.until(pilot, lambda: app.command is None)
        toasts = [str(t.render()) for t in app.screen.query("Toast")]
        return app.view.id, toasts, console.output(app)[: len(ran)]

    assert console.run(scenario, notifications=True) == (view, toast, ran)


def test_esc_with_no_server_lands_on_start_server(console, monkeypatch):
    """The view is hidden then: Enter must press the button (spec §6)."""

    async def gone(**kwargs):
        raise client.transport._unreachable()

    monkeypatch.setattr(client, "board_rows", gone)

    async def scenario(app, pilot):
        await console.until(pilot, lambda: app.default_screen.has_class("-no-server"))
        await pilot.press("slash", "escape")
        return app.focused is app.query_one("#start")

    assert console.run(scenario) is True


def test_a_busy_console_does_not_ask_first(console, kraft_is):
    """A confirm whose answer would be thrown away is no confirm."""
    kraft_is("import time; time.sleep(30)")

    async def scenario(app, pilot):
        app.run_worker(app.run_command(["view", "list"]))
        await console.until(pilot, lambda: app.command is not None)
        await pilot.press("escape")
        await pilot.press("slash")
        app.query_one(Prompt).value = "/admin stop"
        await pilot.press("enter")
        await pilot.pause(0.2)
        asked = isinstance(app.screen, Confirm)
        toasts = [str(t.render()) for t in app.screen.query("Toast")]
        await app.command.end()
        return asked, toasts

    assert console.run(scenario, notifications=True) == (
        False,
        ["A command is still running: Ctrl+O shows it, x stops it."],
    )


def test_the_footer_names_the_prompts_own_keys(console):
    """With the prompt focused the footer is not blank: it says how to leave,
    complete and recall."""

    async def scenario(app, pilot):
        await pilot.press("slash")
        await pilot.pause()
        return [(k.key_display, k.description) for k in app.query(FooterKey)]

    footer = console.run(scenario)
    assert {("esc", "leave"), ("tab", "complete"), ("↑", "previous")} <= set(footer)
