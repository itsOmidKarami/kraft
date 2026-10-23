"""The console's tests: the app driven headless with `App.run_test()`
against the real API in process (`wired`), the event stream faked at
`client.stream_events` as `tests/cli/test_watching.py` fakes it."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest
from support.api import run_with_app
from support.console_board import NOW
from textual.widgets import RichLog

from kraft import client
from kraft.console import live, runner
from kraft.console.app import KraftConsole
from kraft.console.screens import board as board_view
from kraft.console.screens import item as item_view


class FakeStream:
    """`client.stream_events`, fed by hand: `send(event)` hands one event to
    the console; until then it waits, as a quiet server's stream does."""

    def __init__(self) -> None:
        self.after: list[int] = []
        self.queue: asyncio.Queue[dict] = asyncio.Queue()

    def send(self, event: dict) -> None:
        self.queue.put_nowait(event)

    async def __call__(self, after_seq: int = 0):
        self.after.append(after_seq)
        while True:
            yield await self.queue.get()


@pytest.fixture
def stream(monkeypatch) -> FakeStream:
    fake = FakeStream()
    monkeypatch.setattr(client, "stream_events", fake)
    return fake


class Console:
    """Runs `scenario(app, pilot)` against a `KraftConsole` in the same loop
    as the API's lifespan, once the first live read has answered."""

    def __init__(self, wired) -> None:
        self.wired = wired

    def run(self, scenario, size: tuple[int, int] = (80, 24), notifications: bool = False):
        async def inside():
            app = KraftConsole()
            async with app.run_test(size=size, notifications=notifications) as pilot:
                await self.until(pilot, lambda: app.link is not live.Link.RECONNECTING)
                try:
                    return await scenario(app, pilot)
                finally:
                    # `app.run()` cancels the workers before it takes the
                    # screens down; `run_test()` does it after, so a live
                    # callback could land on a frame with no widgets left.
                    app.workers.cancel_all()
                    await pilot.pause()

        return run_with_app(self.wired, inside)

    @staticmethod
    def output(app) -> list[str]:
        """The output panel's lines, as drawn; none before it first opens."""
        return [line.text for log in app.get_screen("output").query(RichLog) for line in log.lines]

    @staticmethod
    async def until(pilot, condition, timeout: float = 30.0) -> None:
        """Let the app run until `condition()` holds."""
        async with asyncio.timeout(timeout):
            while not condition():
                await pilot.pause(0.01)


@pytest.fixture
def console(wired, stream, monkeypatch) -> Console:
    monkeypatch.setattr(live, "BATCH_S", 0.01)
    return Console(wired)


@pytest.fixture
def kraft_is(monkeypatch):
    """`kraft_is(script)`: the console's `kraft` child becomes the Python
    `script`, given the command's words as its arguments."""

    def use(script: str) -> None:
        monkeypatch.setattr(runner, "COMMAND", (sys.executable, "-c", script))

    return use


@pytest.fixture
def board(console, monkeypatch):
    """`board(rows, repo)`: the console over these rows, opened in `repo`."""

    def serve(rows: list[dict], repo: str | None = "/r"):
        async def board_rows(**kwargs):
            return rows, 0

        async def resolve_repo(cwd=None):
            return repo

        monkeypatch.setattr(client, "board_rows", board_rows)
        monkeypatch.setattr(client, "resolve_repo", resolve_repo)
        monkeypatch.setattr(board_view, "time", SimpleNamespace(time=lambda: NOW))
        monkeypatch.setattr(item_view, "time", SimpleNamespace(time=lambda: NOW))
        return console

    return serve
