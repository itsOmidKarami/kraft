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

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, question: str, lines: Sequence[str]) -> None:
        super().__init__()
        self.question = question
        self.lines = lines
        self._answered = False

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

    def action_cancel(self) -> None:
        self._answer(False)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self._answer(event.button.id == "yes")

    def _answer(self, go_on: bool) -> None:
        # Esc and a button before the pop completes: the first answer stands.
        if not self._answered:
            self._answered = True
            self.dismiss(go_on)
