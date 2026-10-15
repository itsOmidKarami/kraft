"""The console's frame: the top line, the views and their keys, small
windows, the no-server state, and `kraft console`'s own refusals."""

from __future__ import annotations

import tomllib
from fnmatch import fnmatch
from pathlib import Path

import pytest
from support.harness import connected_repo
from textual.widgets import ContentSwitcher

from kraft import client
from kraft.console import app as console_app
from kraft.console.app import TABS
from kraft.console.live import Link


def _text(app, selector: str) -> str:
    return str(app.query_one(selector).render())


def test_an_event_reads_the_board_again(console, stream, tmp_path):
    """The proof the rest stands on: `run_test()`, the real API in process
    and a faked event stream, in one loop. An item filed after the console
    opened appears once an event says something changed."""
    repo = connected_repo(tmp_path)

    async def scenario(app, pilot):
        assert app.link is Link.LIVE
        assert app.rows == []
        created = await client.create_work_item("seen live", repo=str(repo))
        stream.send({"seq": 1_000, "type": "work_item_created", "work_item_id": created["id"]})
        await console.until(pilot, lambda: app.rows and app.health)
        return created["id"], app.rows, _text(app, "#tabs")

    wid, rows, tabs = console.run(scenario)
    assert [row["id"] for row in rows] == [wid]
    assert tabs.startswith("Kraft ● live")
    assert "Server ok" in tabs


@pytest.mark.parametrize(
    ("refusal", "said"),
    [
        (client.transport._unreachable, "No server at 127.0.0.1:"),
        (
            lambda: client.status_error(401, "a token this server does not know"),
            "The server refused this machine's token (run/mcp-token).",
        ),
    ],
    ids=["no-server", "token-refused"],
)
def test_a_server_the_console_cannot_use_is_said_on_every_view(console, monkeypatch, refusal, said):
    """No view is drawn over a server that is not there, and a refused token
    is never reported as no server: one sends a person to start a server that
    is already running."""

    async def board_rows(**kwargs):
        raise refusal()

    monkeypatch.setattr(client, "board_rows", board_rows)

    async def scenario(app, pilot):
        seen = []
        for name in TABS:
            app.show(name)
            await pilot.pause()
            seen.append((app.query_one("#down").display, app.query_one(ContentSwitcher).display))
        return app.link, _text(app, "#down"), _text(app, "#tabs"), seen

    link, down, tabs, seen = console.run(scenario)
    assert link is Link.DOWN
    assert down.startswith(said)
    assert "reconnecting" in tabs
    assert seen == [(True, False)] * len(TABS)


@pytest.mark.parametrize(
    ("opened_in", "count"),
    [(None, "Board 3"), ("/r", "Board 2")],
    ids=["all-repos", "in-a-repo"],
)
def test_the_board_tab_counts_what_needs_you_where_it_was_opened(
    console, monkeypatch, opened_in, count
):
    """The count a person reads before opening the board: items that need
    them, in the repo the console was opened in (spec §5.4)."""
    rows = [
        {"display_status": status, "repo": repo}
        for status, repo in [
            ("needs_you", "/r"),
            ("needs_you", "/r"),
            ("needs_you", "/other"),
            ("running", "/r"),
        ]
    ]

    async def board_rows(**kwargs):
        return rows, 0

    async def resolve_repo(cwd=None):
        return opened_in

    monkeypatch.setattr(client, "board_rows", board_rows)
    monkeypatch.setattr(client, "resolve_repo", resolve_repo)

    async def scenario(app, pilot):
        await console.until(pilot, lambda: count in _text(app, "#tabs"))
        return _text(app, "#where")

    assert console.run(scenario).startswith(opened_in or "all repos")


def test_a_refusal_is_shown_in_the_servers_own_words(console):
    """A toast reads as the server wrote it, `[/]` and all: with markup on,
    a title like that is a markup error that ends the console."""

    async def scenario(app, pilot):
        refused = "kraft 409: [/] is not a title"
        app.notify_refusal(client.ClientError(refused, kind=client.Failure.REFUSED, status=409))
        await pilot.pause()
        return [str(toast.render()) for toast in app.screen.query("Toast")]

    assert console.run(scenario, notifications=True) == ["409: [/] is not a title"]


def test_a_wheel_carries_every_file_the_console_reads():
    """Spec §4.1: without a package-data entry a wheel ships no `kraft.tcss`,
    and an installed console fails at start where a checkout works."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    shipped = tomllib.loads(pyproject.read_text())["tool"]["setuptools"]["package-data"]["kraft"]
    package = Path(console_app.__file__).parent
    files = [
        path.relative_to(package.parent).as_posix()
        for path in package.rglob("*")
        if path.is_file() and path.suffix not in (".py", ".pyc")
    ]
    assert files
    assert [f for f in files if not any(fnmatch(f, pattern) for pattern in shipped)] == []
