"""The Board (spec §7.1): every item in the web's groups and order, one line
each, in the repo the console was opened in or in all of them."""

from __future__ import annotations

import os
import time
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.content import Content
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from kraft.console import words
from kraft.console.screens import View

#: A row's columns besides the title and the tail: the repo-and-chain column
#: and the age. The bar, the id and the gaps take 1 + 8 + 2 + 2 more.
META_WIDTH = 14
AGE_WIDTH = 8
#: Each group's bar colour: amber is for what needs you, and nothing else.
BAR = {
    "needs": "$warning",
    "running": "$kraft-info",
    "not_started": "$kraft-faint",
    "done": "$success",
}
#: The Board with no item in scope. `n` files one once New item exists.
EMPTY = "Nothing filed yet. /item create files one."


def cell(text: str, width: int, style: str = "") -> Content:
    """`text` on one line of exactly `width` cells: cut with "…", or padded.
    Never markup: a title holding `[/]` is drawn as typed."""
    return Content.styled(" ".join(text.split()), style).truncate(width, ellipsis=True, pad=True)


def bar_of(row: dict) -> str:
    """The row's state colour (spec §5.2): failed is bad, cancelled faint,
    the rest their group's."""
    status = row.get("display_status")
    if status == "failed":
        return "$error"
    if status == "cancelled":
        return "$kraft-faint"
    return BAR[words.group_of(row)]


def row_line(row: dict, width: int, now: float, *, everywhere: bool, narrow: bool) -> Content:
    """One row as a fixed grid (spec §5.4, §6): bar, id, title, tail; then
    the repo (all repos shown) and a chain that is not the default, and the
    age. A narrow window drops the last two."""
    colour = bar_of(row)
    rest = width - (1 + 8 + 2 + 2) - (0 if narrow else 2 + META_WIDTH + 1 + AGE_WIDTH)
    title_width = max(rest // 2, 1)
    chain = words.chain_of(row)
    repo = os.path.basename(row.get("repo", "").rstrip("/")) if everywhere else ""
    meta = " · ".join(part for part in (repo, "" if chain == "default" else chain) if part)
    age = words.ago(row.get("updated_at"), now).removesuffix(" ago")
    tail_style = colour if words.group_of(row) == "needs" else "$kraft-muted"
    parts: list[str | Content | tuple[str, str]] = [
        ("▌", colour),
        (row["id"][:8], "$kraft-muted"),
        "  ",
        cell(row.get("title") or "", title_width),
        "  ",
        cell(words.reason_tail(row, now), max(rest - title_width, 1), tail_style),
    ]
    if not narrow:
        parts += ["  ", cell(meta, META_WIDTH, "$kraft-faint"), " "]
        parts.append((age.rjust(AGE_WIDTH), "$kraft-faint"))
    return Content.assemble(*parts)


class Board(View):
    label = "Board"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "open", "open"),
        # The view keeps the focus, so its letters act; the list only draws.
        Binding("down,j", "rows('cursor_down')", "down", show=False),
        Binding("up,k", "rows('cursor_up')", "up", show=False),
        Binding("home", "rows('first')", "first", show=False),
        Binding("end", "rows('last')", "last", show=False),
        Binding("pageup", "rows('page_up')", "page up", show=False),
        Binding("pagedown", "rows('page_down')", "page down", show=False),
        Binding("s", "scope", "this repo or all"),
        Binding("f", "filter", "filter"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rows: list[dict] = []
        self.filter_text = ""
        self.done_open = False

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter by title or id · Esc clears", id="board-filter")
        rows = OptionList(id="rows", markup=False)
        rows.can_focus = False
        yield rows

    def on_mount(self) -> None:
        self.query_one("#board-filter").display = False

    @property
    def selected(self) -> dict | None:
        """The item under the cursor; None on a heading or an empty board."""
        option = self.query_one("#rows", OptionList).highlighted_option
        if option is None or option.id is None:
            return None
        return next((row for row in self.rows if row["id"] == option.id), None)

    def rows_changed(self, rows: list[dict], events: list[dict]) -> None:
        self.rows = rows
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    def draw(self, keep: str | None = None) -> None:
        """Lay the rows out again. The cursor stays on its item (spec §5.4),
        or on `keep`; else it starts on the first item."""
        rows = self.query_one("#rows", OptionList)
        if keep is None and rows.highlighted_option is not None:
            keep = rows.highlighted_option.id
        shown = [
            row
            for row in self.rows
            if row.get("display_status") != "archived"
            and self.app.repo in (None, row.get("repo"))
            and self._matches(row)
        ]
        options = self._options(shown, rows.scrollable_content_region.width)
        rows.set_options(options)
        ids = [option.id for option in options]
        first = next((k for k, i in enumerate(ids) if i and not i.startswith("group:")), 0)
        rows.highlighted = ids.index(keep) if keep and keep in ids else first if options else None
        self.refresh_bindings()

    def _matches(self, row: dict) -> bool:
        text = self.filter_text.strip().lower()
        return not text or text in (row.get("title") or "").lower() or text in row["id"].lower()

    def _options(self, shown: list[dict], width: int) -> list[Option]:
        if not shown:
            said = "nothing here for this filter" if self.filter_text.strip() else EMPTY
            return [Option(Content.styled(said, "$kraft-muted"), disabled=True)]
        now, narrow = time.time(), self.screen.has_class("-narrow")
        grouped: dict[str, list[dict]] = {key: [] for key, _ in words.GROUPS}
        for row in words.sort_rows(shown):
            grouped[words.group_of(row)].append(row)
        options: list[Option] = []
        for key, label in words.GROUPS:
            members = grouped[key]
            folded = key == "done" and not self.done_open
            heading = f"{label.upper()} {len(members)}" + (" ›" if folded and members else "")
            options.append(Option(Content.styled(heading, "bold $kraft-muted"), id=f"group:{key}"))
            if key == "needs" and not members:
                options.append(
                    Option(Content.styled("Nothing needs you", "$success"), disabled=True)
                )
            if not folded:
                everywhere = self.app.repo is None
                options += [
                    Option(
                        row_line(row, width, now, everywhere=everywhere, narrow=narrow),
                        id=row["id"],
                    )
                    for row in members
                ]
        return options

    def back(self) -> bool:
        if self.filter_text or self.query_one("#board-filter").display:
            self._clear_filter()
            self.draw()
            return True
        return False

    def _clear_filter(self) -> None:
        box = self.query_one("#board-filter", Input)
        self.filter_text, box.value, box.display = "", "", False
        self.focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # `s` only where there is a repo to come back to.
        return self.app.scope is not None if action == "scope" else True

    @on(Input.Changed, "#board-filter")
    def _filtered(self, event: Input.Changed) -> None:
        self.filter_text = event.value
        self.draw()

    @on(Input.Submitted, "#board-filter")
    def _filter_done(self) -> None:
        self.focus()

    def action_rows(self, name: str) -> None:
        getattr(self.query_one("#rows", OptionList), f"action_{name}")()

    def action_open(self) -> None:
        """Enter on Done's heading folds and unfolds it."""
        option = self.query_one("#rows", OptionList).highlighted_option
        if option is not None and option.id == "group:done":
            self.done_open = not self.done_open
            self.draw()

    def action_scope(self) -> None:
        self.app.widen(self.app.repo is not None)
        self.draw()

    def action_filter(self) -> None:
        box = self.query_one("#board-filter", Input)
        box.display = True
        box.focus()
