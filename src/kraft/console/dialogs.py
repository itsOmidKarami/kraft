"""The console's dialogs (spec §4.1): each a ModalScreen, so the keys of the
view underneath never act while one is open."""

from __future__ import annotations

import os
from collections.abc import Sequence
from functools import partial
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from kraft import client, render


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

    def __init__(
        self, question: str, lines: Sequence[str] = (), *, required: bool, error: str = ""
    ) -> None:
        super().__init__()
        self.question = question
        self.lines = lines
        self.required = required
        #: Why the last answer was refused, asked again (the server's words).
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="note"):
            yield Static(Content(self.question), id="note-question")
            if self.lines:
                yield Static(Content("\n".join(self.lines)), id="note-lines")
            hint = "Enter sends it · Esc sends nothing"
            yield Input(placeholder=hint if self.required else "optional · " + hint)
            yield Static(Content(self.error), id="note-error")

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


class Menu(Dialog[str | None]):
    """The Item's `m` (spec §6): the actions open for the item, by `(name,
    label)`. Enter picks one; Esc picks nothing."""

    def __init__(self, title: str, entries: Sequence[tuple[str, str]]) -> None:
        super().__init__()
        self.title_text = title
        self.entries = entries

    def compose(self) -> ComposeResult:
        with Vertical(id="menu"):
            yield Static(Content(self.title_text), id="menu-title")
            yield OptionList(*(Option(Content(label), id=name) for name, label in self.entries))

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def action_cancel(self) -> None:
        self._answer(None)

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self._answer(event.option.id)


#: What the connect form says of the repo library the CLI offers after a
#: connect: the console does not ask it (spec §7.5).
LIBRARY = "/repo library reviews the repo's .kraft/ library once it is connected."


class Connect(Dialog[list[str] | None]):
    """`c` on Repos (spec §7.5): a path, and the test and setup commands the
    probe proposes for it, to keep or change; Verify adds `--verify`. Answers
    the words of a `kraft repo connect`, or None for Esc. A command left as
    proposed is not passed, so the proposal stands whole, its test scopes
    too, as the CLI's own picker leaves it (`_choose_interactively`)."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        #: The test and setup commands the probe proposed, as the fields show them.
        self.proposed = ("", "")

    def compose(self) -> ComposeResult:
        with Vertical(id="connect"):
            yield Static(Content("Connect a repo"), id="connect-title")
            for box_id, value, title in (
                ("connect-path", self.path, "Path · Enter reads it again"),
                ("connect-test", "", "Test command"),
                ("connect-setup", "", "Setup command"),
            ):
                box = Input(value, id=box_id)
                box.border_title = title
                yield box
            yield Checkbox("Verify: run them once in a throwaway worktree", id="connect-verify")
            yield Static(id="connect-said")
            yield Static(Content(LIBRARY), id="connect-library")
            with Horizontal():
                yield Button("Connect", id="connect-go")
                yield Button("Cancel", id="connect-no")

    def on_mount(self) -> None:
        self._box("path").focus()
        self._probe()

    def _box(self, name: str) -> Input:
        return self.query_one(f"#connect-{name}", Input)

    def _say(self, text: str, style: str = "$kraft-muted") -> None:
        # Content: a path or a refusal may hold `[/]`.
        self.query_one("#connect-said", Static).update(Content.styled(text, style))

    @on(Input.Submitted, "#connect-path")
    def _probe(self) -> None:
        """Read what connecting the path would propose. The last path's
        commands are no proposal for this one, so the fields start empty."""
        self.proposed = ("", "")
        self._box("test").value = self._box("setup").value = ""
        self._say("Reading the repo…")
        path = self._box("path").value.strip()
        self.run_worker(partial(self._probe_now, path), group="probe", exclusive=True)

    async def _probe_now(self, path: str) -> None:
        try:
            probed = await client.probe_repo(path or None)
        except client.ClientError as exc:
            if path == self._box("path").value.strip():
                # The server echoes the typed path: written out, never obeyed.
                self._say(render.printable(client.reason_of(str(exc))), "$error")
            return
        if path != self._box("path").value.strip():
            # An answer for a path no longer in the box; the box holds a new one.
            self._say("Enter reads the new path")
            return
        # Written out, never obeyed: the proposal comes from the repo's own files.
        proposal = [
            render.printable(probed.get(k) or "") for k in ("test_command", "setup_command")
        ]
        self.proposed = (proposal[0], proposal[1])
        for name, text in zip(("test", "setup"), proposal, strict=True):
            box = self._box(name)
            # Only a field still empty: one typed into while the probe ran is the person's.
            if not box.value:
                box.value = text
        self._say(
            f"Kraft proposes these for {render.printable(probed['path'])}. Keep or change them."
        )

    @on(Input.Submitted, "#connect-test, #connect-setup")
    @on(Button.Pressed, "#connect-go")
    def _go(self) -> None:
        path = self._box("path").value.strip()
        argv = ["repo", "connect", client.absolute_path(path or os.getcwd())]
        for flag, name, was in (
            ("--test-command", "test", self.proposed[0]),
            ("--setup-command", "setup", self.proposed[1]),
        ):
            value = self._box(name).value.strip()
            if value != was:
                # `=`: a command starting with `-` is not read as a flag.
                argv.append(f"{flag}={value}")
        if self.query_one(Checkbox).value:
            argv.append("--verify")
        self._answer(argv)

    def action_cancel(self) -> None:
        self._answer(None)

    @on(Button.Pressed, "#connect-no")
    def _no(self) -> None:
        self._answer(None)
