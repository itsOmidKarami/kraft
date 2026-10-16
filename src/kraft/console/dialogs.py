"""The console's dialogs (spec §4.1): each a ModalScreen, so the keys of the
view underneath never act while one is open."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static


class Dialog[R](ModalScreen[R]):
    """A question someone awaits the answer to. The output panel never pops
    one (`KraftConsole._show_panel`): a pop drops its result callback, and
    the decision with it."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self) -> None:
        super().__init__()
        self._answered = False

    def _answer(self, value: R) -> None:
        # Esc and a button before the pop completes: the first answer stands.
        if not self._answered:
            self._answered = True
            self.dismiss(value)


class Confirm(Dialog[bool]):
    """Go on or not (spec §7.8): `question`, then `lines` saying what going
    on does. Cancel has the focus, so Enter alone never goes on."""

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

    def action_cancel(self) -> None:
        self._answer(False)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self._answer(event.button.id == "yes")


class Note(Dialog[str | None]):
    """A line for the server (spec §7.2): a reject's note, a steer, the answer
    to an agent's question. Enter sends it; an empty one only when it is not
    `required` (sent as ""). Esc answers None: nothing is sent."""

    def __init__(self, question: str, lines: Sequence[str] = (), *, required: bool) -> None:
        super().__init__()
        self.question = question
        self.lines = lines
        self.required = required

    def compose(self) -> ComposeResult:
        with Vertical(id="note"):
            yield Static(Content(self.question), id="note-question")
            if self.lines:
                yield Static(Content("\n".join(self.lines)), id="note-lines")
            hint = "Enter sends it · Esc sends nothing"
            yield Input(placeholder=hint if self.required else "optional · " + hint)
            yield Static(id="note-error")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def action_cancel(self) -> None:
        self._answer(None)

    @on(Input.Submitted)
    def _sent(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text or not self.required:
            self._answer(text)
        else:
            self.query_one("#note-error", Static).update(Content("A note is required."))

    @on(Input.Changed)
    def _typed(self) -> None:
        self.query_one("#note-error", Static).update("")


class Reject(Note):
    """The reject note on the Item (spec §7.2), with where the work goes back
    to: the nodes before the gate, its fix target chosen. Answers
    `(note, node)`, or None for Esc."""

    def __init__(self, question: str, lines: Sequence[str], *, nodes: Sequence[str], to: str):
        super().__init__(question, lines, required=True)
        self.nodes = nodes
        self.to = to

    def on_mount(self) -> None:
        # Textual runs Note's on_mount too, after this one: it focuses the note.
        # Text, not a str: a node id is drawn as written, never as markup.
        options = [(Text(node), node) for node in self.nodes]
        picker = Select(options, allow_blank=False, value=self.to, id="reject-to")
        self.query_one("#note").mount(picker, before=self.query_one(Input))

    def _answer(self, value) -> None:
        super()._answer(None if value is None else (value, self.query_one(Select).value))
