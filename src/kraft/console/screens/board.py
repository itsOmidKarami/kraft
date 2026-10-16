"""The Board (spec §7.1): every item in the web's groups and order, one line
each, in the repo the console was opened in or in all of them, and the peek
under it."""

from __future__ import annotations

import os
import time
from functools import partial
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.content import Content
from textual.timer import Timer
from textual.widgets import Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from kraft import client, logs
from kraft.console import acts, moments, tail, words
from kraft.console.screens import View
from kraft.console.screens.server import log_line

#: A row's columns besides the title and the tail: the repo-and-chain column
#: and the age. The bar, the id and the gaps take 1 + 8 + 2 + 2 more.
META_WIDTH = 14
#: The same column in one repo, where it holds only a chain's name.
META_ONE_REPO = 8
#: Of what is left after the fixed columns, the tail's share: a tail such as
#: "failed at verification" is read whole before a title is.
TAIL_SHARE = 55
AGE_WIDTH = 8
#: Each group's bar colour: amber is for what needs you, and nothing else.
BAR = {
    "needs": "$warning",
    "running": "$kraft-info",
    "not_started": "$kraft-faint",
    "done": "$success",
}
#: The peek reads the selected item this long after the cursor stops, so
#: holding `j` does not read every row it passes (spec §7.1).
PEEK_WAIT_S = 0.15
#: Under this many rows the peek starts closed (spec §7.1).
PEEK_MIN_HEIGHT = 30
#: How much of a gate's document the peek shows: its opening.
PEEK_DOC_LINES = 40
#: The Board with no item in scope (spec §7.1).
EMPTY = "Nothing filed yet. n files one."


def cell(text: str, width: int, style: str = "") -> Content:
    """`text` on one line of exactly `width` cells: cut with "…", or padded.
    Never markup: a title holding `[/]` is drawn as typed."""
    return Content.styled(" ".join(text.split()), style).truncate(width, ellipsis=True, pad=True)


def _is_item(option_id: str | None) -> bool:
    return bool(option_id) and not option_id.startswith("group:")


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
    meta_width = META_WIDTH if everywhere else META_ONE_REPO
    rest = width - (1 + 8 + 2 + 2) - (0 if narrow else 2 + meta_width + 1 + AGE_WIDTH)
    title_width = max(rest * (100 - TAIL_SHARE) // 100, 1)
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
        parts += ["  ", cell(meta, meta_width, "$kraft-faint"), " "]
        parts.append((age.rjust(AGE_WIDTH), "$kraft-faint"))
    return Content.assemble(*parts)


def chain_line(marks: list[dict], width: int, *, lit: bool = False) -> Content:
    """The chain as a window around where the item stands (spec §6): what fits
    on either side, `+N …` and `… +N` counting what is hidden. An ended chain
    shows its end, one not started its start. The item's place is `●` in the
    accent (spec §5.2), on it when `lit` (just moved there); done nodes are faint."""
    if not marks:
        return Content("")
    names = [m["node"] + (" ●" if m["state"] in ("current", "hot") else "") for m in marks]
    last = len(marks) - 1
    at = next(
        (k for k, m in enumerate(marks) if m["state"] in ("current", "hot")),
        last if marks[-1]["state"] == "done" else 0,
    )

    def size(lo: int, hi: int) -> int:
        shown = sum(len(name) for name in names[lo : hi + 1]) + 3 * (hi - lo)
        return (
            shown + (len(f"+{lo} … ") if lo else 0) + (len(f" … +{last - hi}") if hi < last else 0)
        )

    lo = hi = at
    grown = True
    while grown:
        grown = False
        if hi < last and size(lo, hi + 1) <= width:
            hi, grown = hi + 1, True
        if lo > 0 and size(lo - 1, hi) <= width:
            lo, grown = lo - 1, True
    here = "bold $background on $primary" if lit else "bold $primary"
    style = {"done": "$kraft-faint", "current": here, "hot": here, "todo": ""}
    parts: list[str | tuple[str, str]] = [(f"+{lo} … ", "$kraft-faint")] if lo else []
    for k in range(lo, hi + 1):
        parts += [(" → ", "$kraft-faint")] if k > lo else []
        parts.append((names[k], style[marks[k]["state"]]))
    if hi < last:
        parts.append((f" … +{last - hi}", "$kraft-faint"))
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
        Binding("a", "approve", "approve"),
        Binding("r", "reject", "reject"),
        *acts.P_KEYS,
        Binding("t", "retry", "retry"),
        Binding("v", "peek", "peek"),
        Binding("s", "scope", "this repo or all"),
        Binding("f", "filter", "filter"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rows: list[dict] = []
        self.filter_text = ""
        self.done_open = False
        #: What the peek shows: `(id, updated_at)`, None for `server.log`, or
        #: `()` for nothing yet, so the next look always reads.
        self._peeked: tuple | None = ()
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter by title or id · Esc clears", id="board-filter")
        rows = OptionList(id="rows", markup=False)
        rows.can_focus = False
        yield rows
        with Vertical(id="peek"):
            yield Static(id="peek-head")
            yield Static(id="peek-chain")
            # Focus stays with the Board and the prompt, never the peek.
            body = RichLog(id="peek-body", wrap=True, max_lines=500, min_width=1)
            body.can_focus = False
            yield body

    def on_mount(self) -> None:
        self.query_one("#board-filter").display = False
        self.query_one("#peek").display = self.app.size.height >= PEEK_MIN_HEIGHT

    @property
    def selected(self) -> dict | None:
        """The item under the cursor; None on a heading or an empty board."""
        option = self.query_one("#rows", OptionList).highlighted_option
        if option is None or option.id is None:
            return None
        return next((row for row in self.rows if row["id"] == option.id), None)

    def rows_changed(self, rows: list[dict], events: list[dict]) -> None:
        self.rows = rows
        if not events:  # reconnected, or read again on purpose: so is the peek
            self._peeked = ()
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    def draw(self, keep: str | None = None) -> None:
        """Lay the rows out again. The cursor stays on its item (spec §5.4),
        or on `keep`; else it starts on the first item."""
        rows = self.query_one("#rows", OptionList)
        old = rows.highlighted
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
        scrolled = rows.scroll_y
        rows.set_options(options)
        ids = [option.id for option in options]
        first = next((k for k, i in enumerate(ids) if i and not i.startswith("group:")), 0)
        if keep and keep in ids:
            rows.highlighted = ids.index(keep)
        elif old is not None and any(_is_item(i) for i in ids):
            # Its item went: the nearest one at or after where it stood, else before.
            after = next((k for k in range(old, len(ids)) if _is_item(ids[k])), None)
            rows.highlighted = (
                after
                if after is not None
                else max(k for k in range(min(old, len(ids))) if _is_item(ids[k]))
            )
        else:
            rows.highlighted = first if options else None
        # set_options resets the scroll: put the reader back where they were,
        # unless that would hide the cursor.
        rows.scroll_to(y=scrolled, animate=False, immediate=True)
        if rows.highlighted is not None:
            rows.scroll_to_highlight()
        self.refresh_bindings()
        self._peek_soon()

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
            # Muted caps and the count in the group's colour: the rows outweigh it.
            heading = Content.assemble(
                (label.upper(), "$kraft-muted"),
                " ",
                (str(len(members)), BAR[key] if members else "$kraft-faint"),
                (" ›" if folded and members else "", "$kraft-muted"),
            )
            options.append(Option(heading, id=f"group:{key}"))
            if key == "needs" and not members:
                # One calm line, the quit line's own sentence (moments.at_rest).
                calm = moments.empty_needs_you(len(grouped["running"]))
                options.append(Option(Content.styled(calm, "$kraft-muted"), disabled=True))
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

    def select(self, work_item_id: str) -> None:
        """Put the cursor on an item wherever it is, out of the filter, Done's
        fold or the repo scope, and open the peek on it."""
        row = next((row for row in self.rows if row["id"] == work_item_id), None)
        if row is None:
            return
        self._clear_filter()
        if self.app.repo not in (None, row.get("repo")):
            self.app.widen(True)
        if words.group_of(row) == "done":
            self.done_open = True
        self.query_one("#peek").display = True
        self.draw(keep=work_item_id)

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
        if action in acts.DOORS:
            return acts.opens(action, self.selected, parameters)
        # `s` only where there is a repo to come back to.
        if action == "open":  # folds Done, or opens the item
            option = self.query_one("#rows", OptionList).highlighted_option
            return self.selected is not None or (option is not None and option.id == "group:done")
        return self.app.scope is not None if action == "scope" else True

    @on(OptionList.OptionHighlighted, "#rows")
    def _moved(self) -> None:
        self.refresh_bindings()
        self._peek_soon()

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
        """Enter on Done's heading folds and unfolds it; on an item it opens
        the Item (spec §6). `v` is the peek's."""
        option = self.query_one("#rows", OptionList).highlighted_option
        if option is not None and option.id == "group:done":
            self.done_open = not self.done_open
            self.draw()
        elif row := self.selected:
            self.app.open_item(row["id"])

    def action_approve(self) -> None:
        """`a` opens the item's Overview rather than approving blind: a chain
        revision's approval needs the digest of the version read (spec §7.1)."""
        if row := self.selected:
            self.app.open_item(row["id"])

    def action_peek(self) -> None:
        peek = self.query_one("#peek")
        peek.display = not peek.display
        self._peeked = ()
        if peek.display:
            self._peek_soon()
        else:
            self.workers.cancel_group(self, "peek")

    def action_scope(self) -> None:
        self.app.widen(self.app.repo is not None)
        self.draw()

    def action_filter(self) -> None:
        box = self.query_one("#board-filter", Input)
        box.display = True
        box.focus()

    def action_reject(self) -> None:
        if row := self.selected:
            self.run_worker(partial(acts.reject, self.app, row), group="act")

    def action_pause_or_resume(self, _word: str = "") -> None:
        if row := self.selected:
            self.run_worker(partial(acts.pause_or_resume, self.app, row), group="act")

    def action_retry(self) -> None:
        if row := self.selected:
            self.run_worker(partial(acts.retry, self.app, row), group="act")

    def on_hide(self) -> None:
        """Another view is shown (this one stays mounted): stop polling
        `server.log` and following a session log until it comes back."""
        if self._timer is not None:
            self._timer.stop()
        self.workers.cancel_group(self, "peek")
        self._peeked = ()

    def on_show(self) -> None:
        self._peek_soon()

    def _peek_soon(self) -> None:
        """Look again once the cursor has stopped for `PEEK_WAIT_S`."""
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_timer(PEEK_WAIT_S, self._peek_now)

    def _peek_now(self) -> None:
        # A hidden Board still gets live events: it reads nothing then.
        if not (self.display and self.query_one("#peek").display):
            return
        row = self.selected
        # `actions` too: resolving a thread opens approve without touching `updated_at`.
        key = (row["id"], row.get("updated_at"), tuple(row.get("actions") or ())) if row else None
        if key == self._peeked:
            return
        self._peeked = key
        # Exclusive: a log still being followed for the last selection stops.
        self.run_worker(partial(self._peek, row), group="peek", exclusive=True)

    async def _peek(self, row: dict | None) -> None:
        head = self.query_one("#peek-head", Static)
        body = self.query_one("#peek-body", RichLog)
        body.clear()
        self.query_one("#peek-chain").display = False
        if row is None:
            head.update(Content.styled("server.log", "$kraft-muted"))
            async for line in tail.follow(tail.server_log()):
                body.write(log_line(line, self.app.theme_variables))
            return
        now = time.time()
        bead = f" · {row['bead_id']}" if row.get("bead_id") else ""
        said = f"{row['id'][:8]}{bead} · {words.chain_of(row)} · {words.reason_tail(row, now)}"
        head.update(Content.styled(said, "$kraft-muted"))
        try:
            await self._peek_item(row["id"], body, now)
        except client.ClientError as exc:
            gone = exc.kind == client.Failure.NOT_FOUND
            body.write("This item no longer exists." if gone else client.reason_of(str(exc)))

    async def _peek_item(self, work_item_id: str, body: RichLog, now: float) -> None:
        """Spec §7.1's table: the first line that matches the item wins."""
        item = await client.get_work_item(work_item_id, full=True)
        status, stop = item.get("display_status"), item.get("stop") or {}
        doors = item.get("actions") or []

        def say(*lines: str) -> None:
            # From the top: a document opens at its start, not its end.
            for line in lines:
                body.write(line, scroll_end=False)

        tail_words = words.reason_tail(item, now)
        way = ["", *found] if (found := acts.way_on(item)) else []
        if status == "needs_you" and stop.get("kind") == "gate":
            chain = self.query_one("#peek-chain", Static)
            chain.update(chain_line(words.chain_marks(item), self.size.width - 2))
            chain.display = True
            say(f"{item.get('pending_gate') or stop.get('node')} is waiting on your decision.")
            if "approve" not in doors and (waits := await acts.approve_waits(work_item_id)):
                say(waits)
            try:
                document = await client.artifact(work_item_id)
            except client.ClientError as exc:
                if exc.kind != client.Failure.NOT_FOUND:
                    raise
                say("", "This gate has no document.")
                return
            say("", *(document.get("content") or "").splitlines()[:PEEK_DOC_LINES])
        elif status == "needs_you" and stop.get("kind") == "question":
            say("The agent asks:", item.get("needs_context_question") or tail_words, *way)
        elif status in ("failed", "needs_you"):
            say(stop.get("reason") or tail_words, *way)
        elif status == "paused":
            say(f"{tail_words} · {words.ago(item.get('updated_at'), now)}", *way)
        elif status in ("blocked", "queued"):
            waits = [d for d in item.get("dependencies") or [] if not d.get("met")]
            say(tail_words, *(["It comes after:"] if waits else []))
            say(*(f"  {d['id'][:8]}  {d.get('status')}  {d.get('title')}" for d in waits))
        elif status in ("running", "waiting", "escalated"):
            await self._follow(item, body)
        else:
            mr = item.get("mr_ref") or {}
            say(tail_words, *([mr["url"]] if mr.get("url") else []))

    async def _follow(self, item: dict, body: RichLog) -> None:
        """The newest session's log, followed: "waiting for output" until it
        writes, "session ended" when it stops, until the next refresh."""
        session, _passed_over = logs.pick_session(item, follow=True)
        body.write("waiting for output")
        if session is None:
            return
        fresh = True
        async for entry in client.stream_log(item["id"], session["id"]):
            if fresh:
                body.clear()
                fresh = False
            # As `kraft view logs` reads a line: its summary, else its text.
            body.write(str(entry.get("summary") or entry.get("text") or ""))
        body.write(Text("session ended", style=self.app.theme_variables["kraft-muted"]))
