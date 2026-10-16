"""The output panel (spec §7.7): every command this session ran, typed or
launched by a key, with its output and exit code."""

from __future__ import annotations

import shlex
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, RichLog, Static

#: Lines kept: a chatty command must not grow the panel without bound.
MAX_LINES = 5000


class OutputPanel(ModalScreen[None]):
    """Modal, so it holds the focus while open: its `x` never reaches the
    view underneath. Installed once (`KraftConsole.SCREENS`), so closing it
    keeps what it shows for the next Ctrl+O."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q", "dismiss", "close"),
        Binding("x", "stop", "stop"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._pending: list[Text] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="output"):
            yield RichLog(id="output-log", wrap=True, max_lines=MAX_LINES)
            with Horizontal(id="output-bar"):
                yield Button("Stop", id="stop", disabled=True)
                yield Static("x stops the command · Esc closes", id="output-keys")

    def on_mount(self) -> None:
        self.query_one(RichLog).focus()

    def begin(self, argv: list[str]) -> None:
        self._flush()
        log = self.query_one(RichLog)
        if log.lines:
            log.write("")
        log.write(Text(f"kraft {shlex.join(argv)}", style="bold"))
        self.query_one("#stop", Button).disabled = False

    def write_line(self, line: str) -> None:
        # One write per burst, not per line: 100k lines would stall the loop.
        # from_ansi, not markup: a child's own colours draw, and `[/]` is text.
        # The `\r` of CRLF output would blank the line: from_ansi keeps what follows the last one.
        if not self._pending:
            self.app.call_later(self._flush)
        self._pending.append(Text.from_ansi(line.removesuffix("\r")))

    def _flush(self) -> None:
        lines, self._pending = self._pending, []
        if lines:
            self.query_one(RichLog).write(Text("\n").join(lines))

    def end(self, code: int) -> None:
        self._flush()
        theme = self.app.current_theme
        colour = theme.error if code else self.app.theme_variables["kraft-muted"]
        self.query_one(RichLog).write(Text(f"exit {code}", style=colour))
        self.query_one("#stop", Button).disabled = True

    def action_stop(self) -> None:
        self.app.stop_command()

    @on(Button.Pressed, "#stop")
    def _stop(self) -> None:
        self.app.stop_command()
