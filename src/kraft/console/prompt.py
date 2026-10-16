"""The prompt (spec §7.7): any `kraft` verb, typed, with Tab completing from
the CLI's own parser and ↑ recalling this session's commands."""

from __future__ import annotations

import argparse
import contextlib
import functools
import io
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


def _split(text: str) -> list[str]:
    """`text` cut at the spaces outside quotes, quotes kept, so `--note "a b"`
    is one word and the line joins back as typed."""
    out, quote, start = [], "", 0
    for k, char in enumerate(text):
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == " ":
            out.append(text[start:k])
            start = k + 1
    return [*out, text[start:]]


def _wants_id(parser: argparse.ArgumentParser, typed: list[str]) -> bool:
    """Whether the verb's next positional, after `typed`, is the item `id`:
    a flag and its value (by the flag's `nargs`) are not positionals."""
    positionals = [a for a in parser._actions if not a.option_strings]
    given, k = 0, 0
    while k < len(typed):
        word = typed[k]
        k += 1
        if not word:
            continue
        if word.startswith("-"):
            action = parser._option_string_actions.get(word.split("=")[0])
            if action is not None and "=" not in word:
                k += 1 if action.nargs in (None, "?", "*", "+") else action.nargs
            continue
        if given < len(positionals) and positionals[given].nargs not in ("*", "+"):
            given += 1
    return given < len(positionals) and positionals[given].dest == "id"


def complete(line: str, ids: Iterable[str]) -> tuple[str, list[str]]:
    """`line` with its last word completed as far as the CLI's parser makes it
    certain, and what that word could still become when that is more than one
    thing. It completes a group, a verb, a flag, or an item id where the
    verb's positional is `id`."""
    lead = "/" if line.startswith("/") else ""
    *done, word = _split(line.removeprefix("/"))
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
    elif _wants_id(parser, after):
        options = list(ids)
    else:
        options = []
    found = sorted({option for option in options if option.startswith(word)})
    if len(found) == 1:
        word = found[0] + " "
    elif found:
        word = os.path.commonprefix(found)
    return lead + " ".join([*done, word]), found if len(found) > 1 else []


#: `item` verbs that never act on a work item: they file one, or answer a thread.
NO_ITEM = frozenset({"create", "reply", "resolve", "reopen"})
#: An `item` verb on an item's page that could not be given its id.
NEEDS_ID = (
    "Type the item's id: without one, item {verb} acts on the worktree the console was opened in."
)


def _parsed(argv: list[str]) -> dict | None:
    """`argv` as the CLI parses it, or None where the CLI would refuse it (its
    usage error is its own to print, not here over the screen)."""
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            return vars(_parser().parse_args(argv))
        except (SystemExit, argparse.ArgumentError):
            return None


def for_item(argv: list[str], work_item_id: str) -> tuple[list[str], str | None]:
    """`argv` acting on `work_item_id`, where an `item` verb takes an item and
    none was typed: as its `id` positional, its `--id`, or its `ids`. Else the
    sentence that refuses it: a verb left without an id would act on the item
    of the console's directory, never the one on screen."""
    if argv[:1] != ["item"] or len(argv) < 2 or argv[1] in NO_ITEM:
        return argv, None
    verb = _verbs(_verbs(_parser())["item"]).get(argv[1])
    if verb is None:
        return argv, None  # no such verb: the CLI says so
    refused = NEEDS_ID.format(verb=argv[1])
    takes = {a.dest: a for a in verb._actions if a.dest in ("id", "ids")}
    if not takes:
        return argv, refused
    parsed = _parsed(argv)
    if parsed is None:
        return argv, None  # malformed: the CLI's own usage error, which acts on nothing
    if parsed.get("id") is not None or parsed.get("ids"):
        return argv, None
    head, rest = argv[:2], argv[2:]
    flag = takes["id"].option_strings if "id" in takes else []
    tries = [[*head, flag[0], work_item_id, *rest]] if flag else []
    tries += [[*head, work_item_id, *rest], [*argv, work_item_id]]
    for tried in tries:
        # The first place the CLI reads it as the item, argparse filling positionals in order.
        got = _parsed(tried)
        if got and work_item_id in (got.get("id"), *(got.get("ids") or [])):
            return tried, None
    return argv, refused


def commands() -> list[str]:
    """Every verb the CLI takes, as typed after `kraft`: `item approve`,
    `admin addon install`."""

    def walk(parser: argparse.ArgumentParser, words: list[str]) -> list[str]:
        verbs = _verbs(parser)
        if not verbs:
            return [" ".join(words)]
        return [line for name, sub in verbs.items() for line in walk(sub, [*words, name])]

    return walk(_parser(), [])


class Prompt(Input):
    """One line at the bottom of every view. `/` from anywhere comes here."""

    BINDINGS: ClassVar[list[BindingType]] = [
        # Explicit: Textual's suggester alone accepts on Right, not Tab.
        Binding("tab", "complete", "complete"),
        Binding("up", "recall", "previous"),
        Binding("escape", "leave", "leave"),
    ]

    def __init__(self) -> None:
        super().__init__(placeholder="/command…", id="prompt", select_on_focus=False)
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
