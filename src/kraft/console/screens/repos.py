"""Repos (spec §7.5): every repo Kraft has, with its test and setup commands
and `*` on the one the console was opened in. `c` connects one through a form
the probe fills; `d` disconnects one. Both run their `kraft repo` verb in the
output panel, so the CLI says what it saved."""

from __future__ import annotations

import os
from functools import partial
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.content import Content
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from kraft import client, render
from kraft.console.dialogs import Connect
from kraft.console.screens import View

#: The panel's title, what it lists; its subtitle, the key that adds to it.
TITLE = "Connected repos"
ADDS = "c connects one"
#: The list with nothing connected.
EMPTY = "No repo is connected yet."


def command_words(command: str | None) -> str:
    """A saved command as the list says it: None is not decided yet, "" a
    declared none. Control characters are written out, never obeyed."""
    if command is None:
        return "not set"
    return render.printable(command) if command else "none"


def repo_line(repo: dict, here: str | None) -> Content:
    """Two lines: `*` on the repo the console was opened in, its name and
    path, and whether it is disabled or only detected; then its test and
    setup commands."""
    scopes = repo.get("test_scopes") or []
    test = f"{len(scopes)} scopes" if scopes else command_words(repo.get("test_command"))
    notes = [
        *(["disabled"] if not repo.get("enabled", True) else []),
        *(["detected, not managed"] if not repo.get("managed", True) else []),
    ]
    return Content.assemble(
        ("* " if repo["path"] == here else "  ", "bold"),
        (render.printable(repo.get("name") or ""), "bold"),
        f"  {render.printable(repo['path'])}",
        *([(f" · {', '.join(notes)}", "$kraft-muted")] if notes else []),
        (f"\n    test: {test} · setup: {command_words(repo.get('setup_command'))}", "$kraft-muted"),
    )


class Repos(View):
    label = "Repos"
    BINDINGS: ClassVar[list[BindingType]] = [
        # The view keeps the focus, so its letters act; the list only draws.
        Binding("down,j", "rows('cursor_down')", "down", show=False),
        Binding("up,k", "rows('cursor_up')", "up", show=False),
        Binding("c", "connect", "connect"),
        Binding("d", "disconnect", "disconnect"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.repos: list[dict] = []

    def compose(self) -> ComposeResult:
        rows = OptionList(id="repo-rows", markup=False)
        rows.can_focus = False
        yield rows

    def on_mount(self) -> None:
        self.add_class("panel")
        self.border_title, self.border_subtitle = TITLE, ADDS

    @property
    def selected(self) -> dict | None:
        """The repo under the cursor; None on an empty list."""
        option = self.query_one("#repo-rows", OptionList).highlighted_option
        return next((r for r in self.repos if option is not None and r["path"] == option.id), None)

    def on_show(self) -> None:
        self._read()

    def on_hide(self) -> None:
        """Another view is shown (this one stays mounted): read nothing."""
        self.workers.cancel_group(self, "read")

    def rows_changed(self, rows: list[dict], events: list[dict]) -> None:
        if not events:  # reconnected, or read again on purpose: so is the list
            self._read()

    def _read(self) -> None:
        # A hidden view reads nothing: it reads again when shown.
        if self.display:
            self.run_worker(self._read_now, group="read", exclusive=True)

    async def _read_now(self) -> None:
        try:
            self.repos = await client.repos()
        except client.ClientError as exc:
            self.app.notify_refusal(exc)
            return
        self.draw()

    def draw(self) -> None:
        """The repos again; the cursor stays on its repo, else the first."""
        rows = self.query_one("#repo-rows", OptionList)
        keep = rows.highlighted_option.id if rows.highlighted_option is not None else None
        here = self.app.scope
        rows.set_options(
            [Option(repo_line(repo, here), id=repo["path"]) for repo in self.repos]
            or [Option(Content.styled(EMPTY, "$kraft-muted"), disabled=True)]
        )
        paths = [repo["path"] for repo in self.repos]
        rows.highlighted = paths.index(keep) if keep in paths else (0 if paths else None)
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return self.selected is not None if action == "disconnect" else True

    def action_rows(self, name: str) -> None:
        getattr(self.query_one("#repo-rows", OptionList), f"action_{name}")()

    def action_connect(self) -> None:
        # Before the form: a connect refused after it would throw away what was typed.
        if not self.app.busy():
            self.run_worker(self._connect, group="act")

    async def _connect(self) -> None:
        argv = await self.app.push_screen_wait(Connect(os.getcwd()))
        if argv is not None:
            await self.app.run_command(argv)
            self._read()

    def action_disconnect(self) -> None:
        if repo := self.selected:
            self.run_worker(partial(self._disconnect, repo["path"]), group="act")

    async def _disconnect(self, path: str) -> None:
        # `--`: a path is never read as a flag.
        await self.app.run_asking(["repo", "disconnect", "--", path])
        self._read()
