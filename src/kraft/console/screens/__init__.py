"""The views the frame switches between. Each later pull request replaces its
placeholder in `VIEWS` with its own module (`board.py`, `item.py`, ...)."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.widgets import Static


class View(Vertical, can_focus=True):
    """One view in the frame. While it is shown, its `BINDINGS` are the
    footer's keys, and the app's live updates reach it through the hooks."""

    BINDINGS: ClassVar[list[BindingType]] = []
    #: Its name on the top line's tab.
    label: ClassVar[str] = ""

    def shown(self) -> None:
        """The frame switched to this view. Focus goes where single letters
        act rather than type (spec §6): the view itself until it has a list."""
        self.focus()

    def back(self) -> bool:
        """Esc or `q` on this view. Undo something here (a filter, say) and
        return True, or return False to leave the view."""
        return False

    def rows_changed(self, rows: list[dict], events: list[dict]) -> None:
        """The board was read again after `events`; empty `events` means the
        stream reconnected or a read was asked for (`Live.reread`), so read
        again whatever this view shows."""

    def health_changed(self, health: dict | None) -> None:
        """`/health` answered (None: it did not)."""


class _Placeholder(View):
    def compose(self) -> ComposeResult:
        yield Static(f"{self.label}: not built yet.", markup=False)


class Addons(_Placeholder):
    label = "Add-ons"


# After `View`, which each view's module builds on.
from kraft.console.screens.board import Board  # noqa: E402
from kraft.console.screens.item import Item  # noqa: E402
from kraft.console.screens.repos import Repos  # noqa: E402
from kraft.console.screens.server import Server  # noqa: E402

#: Every view, by the name `KraftConsole.show` takes.
VIEWS: dict[str, type[View]] = {
    "board": Board,
    "server": Server,
    "addons": Addons,
    "repos": Repos,
    "item": Item,
}
