"""The console's dialogs (spec §4.1): each a ModalScreen, so the keys of the
view underneath never act while one is open."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class Confirm(ModalScreen[bool]):
    """Go on or not (spec §7.8): `question`, then `lines` saying what going
    on does. Cancel has the focus, so Enter alone never goes on."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss(False)", "cancel")]

    def __init__(self, question: str, lines: Sequence[str]) -> None:
        super().__init__()
        self.question = question
        self.lines = lines

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            # Content, not markup: a title in the listing may hold `[/]`.
            yield Static(Content(self.question), id="question")
            yield Static(Content("\n".join(self.lines)), id="listing")
            with Horizontal():
                yield Button("Go on", id="yes")
                yield Button("Cancel", id="no")

    def on_mount(self) -> None:
        self.query_one("#no").focus()

    @on(Button.Pressed)
    def _answer(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")
