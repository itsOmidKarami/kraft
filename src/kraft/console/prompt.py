"""The prompt (spec §7.7): any `kraft` verb, typed, with Tab completing from
the CLI's own parser and ↑ recalling this session's commands."""

from __future__ import annotations

import argparse
import functools
import os
from collections.abc import Iterable
from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.widgets import Input

from kraft import cli


@functools.cache
def _parser() -> argparse.ArgumentParser:
    return cli.build_parser()


def _verbs(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """The verbs (or groups) `parser` takes next; none for a leaf verb."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def complete(line: str, ids: Iterable[str]) -> tuple[str, list[str]]:
    """`line` with its last word completed as far as the CLI's parser makes it
    certain, and what that word could still become when that is more than one
    thing. It completes a group, a verb, a flag, or an item id where the
    verb's positional is `id`."""
    lead = "/" if line.startswith("/") else ""
    *done, word = line.removeprefix("/").split(" ")
    parser = _parser()
    # The words after the last verb: a word that is no verb (`kraft`, an id, a
    # flag's value) leaves the parser where it was.
    after: list[str] = []
    for typed in done:
        verbs = _verbs(parser)
        parser, after = (verbs[typed], []) if typed in verbs else (parser, [*after, typed])
    if word.startswith("-"):
        options = [flag for action in parser._actions for flag in action.option_strings]
    elif verbs := _verbs(parser):
        options = list(verbs)
    elif all(w.startswith("-") for w in after if w) and any(
        a.dest == "id" and not a.option_strings for a in parser._actions
    ):
        options = list(ids)
    else:
        options = []
    found = sorted({option for option in options if option.startswith(word)})
    if len(found) == 1:
        word = found[0] + " "
    elif found:
        word = os.path.commonprefix(found)
    return lead + " ".join([*done, word]), found if len(found) > 1 else []


class Prompt(Input):
    """One line at the bottom of every view. `/` from anywhere comes here."""

    BINDINGS: ClassVar[list[BindingType]] = [
        # Explicit: Textual's suggester alone accepts on Right, not Tab.
        Binding("tab", "complete", "complete", show=False),
        Binding("up", "recall", "previous command", show=False),
        Binding("escape", "leave", "leave the prompt", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(placeholder="/ runs a kraft command", id="prompt", select_on_focus=False)
        self.history: list[str] = []
        self._back = 0

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip(" /"):
            self.history.append(event.value)
        self._back = 0
        self.value = ""

    def action_complete(self) -> None:
        ids = [row["id"][:8] for row in self.app.rows]
        self.value, left = complete(self.value, ids)
        self.cursor_position = len(self.value)
        self.app.clear_notifications()
        if left:
            self.app.notify("  ".join(left), markup=False)

    def action_recall(self) -> None:
        if self._back < len(self.history):
            self._back += 1
            self.value = self.history[-self._back]
            self.cursor_position = len(self.value)

    def action_leave(self) -> None:
        self.value = ""
        self._back = 0
        # No server: the view is hidden and Enter should press Start server (spec §6).
        frame = self.app.default_screen
        start = self.app.query_one_optional("#start")
        if start is not None and frame.has_class("-no-server"):
            frame.set_focus(start)
        else:
            self.app.view.focus()
