"""The console's own confirm (spec §7.8): what it asks, the running items it
lists, and that only a deliberate answer goes on."""

from __future__ import annotations

import asyncio

import pytest

from kraft import client
from kraft.console import app as console_app
from kraft.console.dialogs import Confirm
from kraft.vocab import WorkItemStatus

ACTIVE = [{"id": "a1b2", "title": "Fix the [/] parser", "current_node_id": "implement"}]
LISTED = "\n".join(
    [
        "This stops the server and starts it again.",
        "restarting the server ends the agent of any active item (1):",
        "  a1b2  implement  Fix the [/] parser",
        "  each one stops when Kraft starts again; retry it then. To avoid that, "
        "pause them first (kraft item pause ID) and resume them after.",
    ]
)


@pytest.mark.parametrize(
    ("server", "keys", "answer", "listed"),
    [
        ("lists", ["enter"], False, LISTED),
        ("lists", ["escape"], False, LISTED),
        ("lists", ["shift+tab", "enter"], True, LISTED),
        ("gone", ["enter"], False, "This stops the server and starts it again."),
        ("wedged", ["enter"], False, "This stops the server and starts it again."),
    ],
    ids=["enter-cancels", "escape-cancels", "go-on", "no-server", "server-wedged"],
)
def test_a_confirm_lists_what_a_restart_ends_and_goes_on_only_when_told(
    console, monkeypatch, server, keys, answer, listed
):
    """Cancel has the focus, so a stray Enter never restarts a server. A
    server that does not answer the listing, or never replies, still gets
    the question rather than a console that hangs."""

    async def list_work_items(status=None, **kwargs):
        assert status == WorkItemStatus.ACTIVE
        if server == "gone":
            raise client.transport._unreachable()
        if server == "wedged":
            await asyncio.Event().wait()
        return ACTIVE

    monkeypatch.setattr(client, "list_work_items", list_work_items)
    monkeypatch.setattr(console_app, "CONFIRM_LIST_S", 0.1)

    async def scenario(app, pilot):
        asked = app.run_worker(
            app.confirm(
                "kraft admin restart",
                ["This stops the server and starts it again."],
                running="restarting",
            )
        )
        await console.until(pilot, lambda: isinstance(app.screen, Confirm))
        await pilot.pause()
        shown = str(app.screen.query_one("#listing").render())
        await pilot.press(*keys)
        await console.until(pilot, lambda: asked.is_finished)
        return shown, asked.result

    assert console.run(scenario) == (listed, answer)


def test_a_second_answer_before_the_pop_completes_is_ignored(console):
    async def scenario(app, pilot):
        asked = app.run_worker(app.confirm("kraft admin stop"))
        await console.until(pilot, lambda: isinstance(app.screen, Confirm))
        dialog = app.screen
        dialog.action_cancel()
        dialog._answer(True)
        await console.until(pilot, lambda: asked.is_finished)
        return asked.result

    assert console.run(scenario) is False
