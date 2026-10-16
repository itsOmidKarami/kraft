"""The Item (spec §7.2): one work item's header, its stop banner, where it
stands on its chain, and its tabs. A pane of the frame's switcher, not a
pushed screen, so the prompt and the top line stay; Esc goes back."""

from __future__ import annotations

import os
import time
from functools import partial
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.content import Content
from textual.widgets import ContentSwitcher, RichLog, Static

from kraft import answers, client, logs, render
from kraft.console import acts, words
from kraft.console.dialogs import Menu, Note, Reject
from kraft.console.screens import View
from kraft.console.screens.board import bar_of, chain_line

#: The Item's tabs, in `[` and `]` order, with their names.
TABS = (("overview", "Overview"), ("activity", "Activity"), ("log", "Log"), ("diff", "Diff"))
#: How many events the Activity reads at a time, newest first.
PAGE = 50
#: A `before_seq` past every event: SQLite's largest integer.
NEWEST = 2**63 - 1
#: What the view says once its item is gone (spec §7.2).
GONE = "This item no longer exists. Esc goes back."
#: A diff line's colour by how it starts, the first match winning: the web's
#: add and delete colours, git's bold headers and hunk colour.
DIFF_STYLE = (
    ("+++", "bold"),
    ("---", "bold"),
    ("diff --git", "bold"),
    ("+", "success"),
    ("-", "error"),
    ("@@", "kraft-info"),
)
#: The `m` menu (spec §6): each entry's door (`actions`), in the web's order
#: of the header's menu, and its label. Only open doors are listed.
MENU = (
    ("skip", "Skip"),
    ("unblock", "Unblock"),
    ("raise_budget", "Raise budget…"),
    ("escalate", "Escalate…"),
    ("complete", "Complete…"),
    ("cancel", "Cancel…"),
    ("archive", "Archive…"),
)
#: What each of the menu's dialogs asks.
SKIP = "A note for the record, or Enter for none."
ESCALATE = "What the agent should look into: it gets the item's stop and this."
REASON = "Why: the reason goes in the run log."
RAISE = "The new cap in dollars, or none for no cap. It must be above what the item has spent."
#: A chain node's mark in the Overview's list, by its state.
MARK = {"done": "✓", "current": "●", "hot": "●", "todo": " "}


def usd(amount: float) -> str:
    """`format.ts`'s `usd`: cents from a dollar up, a tenth of a cent below."""
    return f"${amount:.2f}" if amount >= 1 else f"${amount:.3f}"


def status_words(item: dict) -> str:
    """The header's status, as the web's badge says it (`headerState`), in
    sentence case: an item filed paused and never started is "Not started"."""
    if item.get("display_status") == "paused" and not item.get("current_node_id"):
        return "Not started"
    return (item.get("display_status") or "running").replace("_", " ", 1).capitalize()


def cancel_lines(preview: dict) -> list[str]:
    """What cancelling does, in the web's Cancel card words (`CancelCard`,
    `stopsNow`), from `client.cancel_preview`."""
    run = preview.get("running")
    if run:
        name = words.task_name(run["task"]) if run.get("task") else run.get("node")
        attempt = f"attempt {run['attempt']}" if run.get("attempt") else ""
        stops = f"{', '.join(p for p in (name, attempt) if p)}. That attempt's work is lost."
    else:
        stops = "Nothing is running."
    kept = preview.get("kept") or {}
    findings = f"{kept['findings']} findings" if kept.get("findings") else "findings"
    threads = f"{kept['threads']} threads" if kept.get("threads") else "threads"
    spent = (preview.get("spend") or {}).get("spent_usd") or 0
    return [
        f"Stops now: {stops}",
        f"Keeps: branch {kept.get('branch')}, the worktree until it is archived, "
        f"{findings}, {threads} and the run log.",
        f"Spend: {usd(spent)} stays on the ledger and in today's total.",
    ]


def banner_line(item: dict) -> str | None:
    """The stop banner (spec §7.2): the server's reason and the way on, in
    `words.banner_of`'s words and each kind's frame, as the web draws it."""
    banner = words.banner_of(item)
    if banner is None:
        return None
    node, text, way = banner["node"], banner["text"], banner["way"]
    at = f"Stopped at {node}. " if node else ""
    said = {
        "gate": f"Waiting for your approval at {node}.",
        "question": f"The agent asks: {text}",
        "failed": f"Failed at {node}. {text}" if node else f"Failed. {text}",
        "limit": at + text,
        "stop": (at + text) if text else (f"Stopped at {node}." if node else "Stopped."),
    }[banner["kind"]]
    return f"{said} {way}".strip()


class Item(View):
    label = "Item"
    BINDINGS: ClassVar[list[BindingType]] = [
        # Long content scrolls by keys: the view keeps the focus (spec §6).
        Binding("down,j", "scroll('down')", "down", show=False),
        Binding("up,k", "scroll('up')", "up", show=False),
        Binding("pagedown", "scroll('page_down')", "page down", show=False),
        Binding("pageup", "scroll('page_up')", "page up", show=False),
        Binding("home", "scroll('home')", "top", show=False),
        Binding("end", "scroll('end')", "end", show=False),
        Binding("left_square_bracket", "tab(-1)", "tab"),
        Binding("right_square_bracket", "tab(1)", "tab", show=False),
        Binding("a", "approve", "approve"),
        Binding("r", "reject", "reject"),
        Binding("p", "pause_or_resume", "pause/resume"),
        Binding("t", "retry", "retry"),
        Binding("s", "session", "session"),
        Binding("m", "menu", "menu"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.work_item_id: str | None = None
        #: The detail read (`get_work_item(full=True)`); None until it answers.
        self.item: dict | None = None
        self.tab = TABS[0][0]
        self.crumb = "Board"
        #: The gate whose document the Overview shows, and that document's
        #: digest: approve sends the digest of what was read (spec §7.2).
        self.read_gate: str | None = None
        self.digest: str | None = None
        #: `(updated_at, actions)` of the last read: a live event that leaves
        #: them alone reads nothing.
        self._key: tuple | None = None
        #: The item answered 404: nothing is read until `open` shows another.
        self._dead = False
        #: What the Overview draws, kept for when its pane is shown.
        self._lines: list[str | Text] = []
        #: The Activity's events, newest first, and whether older ones remain.
        self.events: list[dict] = []
        self.more = False
        #: The session the Log shows; None picks as `kraft view logs -f` does.
        self.session_id: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="item-top"):
            yield Static(id="item-title")
            yield Static(id="item-where")
        with Horizontal(id="item-state"):
            yield Static(id="item-status")
            yield Static(id="item-spent")
        yield Static(id="item-banner")
        yield Static(id="item-chain")
        yield Static(id="item-tabs")
        with ContentSwitcher(initial=self.tab, id="item-panes"):
            # Focus stays with the view, which scrolls these by its keys.
            overview = RichLog(id="overview", wrap=True, min_width=1)
            overview.can_focus = False
            yield overview
            activity = RichLog(id="activity", wrap=True, min_width=1)
            activity.can_focus = False
            yield activity
            log = RichLog(id="log", wrap=True, min_width=1, max_lines=2000)
            log.can_focus = False
            yield log
            diff = RichLog(id="diff", wrap=True, min_width=1)
            diff.can_focus = False
            yield diff
        yield Static(Content(GONE), id="item-gone")

    def on_mount(self) -> None:
        self.query_one("#item-gone").display = False

    def open(self, work_item_id: str) -> None:
        """Show `work_item_id`, on the Overview, read afresh."""
        # The read still answering for the item shown before must not draw it.
        for group in ("read", "tab", "older", "log"):
            self.workers.cancel_group(self, group)
        self.work_item_id, self.item, self._key = work_item_id, None, None
        self._dead = False
        # Where it was opened from: the top view showing, never a guess.
        from kraft.console.screens import VIEWS  # defined after this module loads

        self.crumb = VIEWS[self.app._last_top].label
        self.read_gate = self.digest = None
        self.events, self.more = [], False
        self._lines = []
        self.session_id = None
        self.query_one("#item-gone").display = False
        self.query_one("#item-title", Static).update(Content(f"{self.crumb} › {work_item_id[:8]}"))
        for part in ("#item-where", "#item-status", "#item-spent", "#item-chain"):
            self.query_one(part, Static).update("")
        self.query_one("#item-banner").display = False
        for pane in self.query("#item-panes > RichLog").results(RichLog):
            pane.clear()
        self.show_tab(TABS[0][0])
        # After the next refresh: a pane just shown has no width until it is
        # laid out, and a RichLog wraps what it is given then at one cell.
        self.call_after_refresh(self._read)

    def show_tab(self, tab: str) -> None:
        """Switch to `tab`, reading what it shows afresh."""
        self.tab = tab
        self.query_one("#item-panes", ContentSwitcher).current = tab
        self._draw_tabs()
        self.refresh_bindings()
        # An older page asked for before the tab was left must not land on the
        # list this visit rereads: its boundary is gone.
        self.workers.cancel_group(self, "older")
        if self.work_item_id is not None and not self._dead:
            work = {
                "activity": (self._activity_first, "tab"),
                "diff": (self._diff, "tab"),
                "log": (self._follow, "log"),
            }.get(tab)
            if work:
                self.call_after_refresh(self._start, tab, *work)
            else:  # a pane written while hidden wraps at one cell: draw it shown
                self.call_after_refresh(self._write_overview)
        if tab != "log" or self._dead:  # a log nobody sees is not followed
            self.workers.cancel_group(self, "log")

    def _shown(self, tab: str) -> bool:
        """Whether `tab`'s pane is on screen: a RichLog written while it is not
        has no width and wraps what it is given at one cell (ruling 15)."""
        return self.display and self.tab == tab

    def _start(self, tab: str, work, group: str) -> None:
        # After the refresh, a key or a view change may have moved on.
        if self._shown(tab) and not self._dead:
            self.run_worker(work, group=group, exclusive=True)

    def on_show(self) -> None:
        # Hidden (another view, or a terminal too small), the panes were not
        # drawn and the Log was let go: draw and follow again.
        if self.work_item_id is not None and not self._dead:
            self.show_tab(self.tab)

    def on_hide(self) -> None:
        self.workers.cancel_group(self, "log")

    def _read(self) -> None:
        # Exclusive: a read still answering for an older state is dropped.
        self.run_worker(self._read_now, group="read", exclusive=True)

    def rows_changed(self, rows: list[dict], events: list[dict]) -> None:
        # A hidden Item still hears every event: it reads nothing then.
        if not self.display or self.work_item_id is None or self._dead:
            return
        if self.tab == "activity":
            mine = [e for e in events if e.get("work_item_id") == self.work_item_id]
            # A reread (no events) or a line of this item's: read what is newer.
            if mine or not events:
                self.run_worker(self._activity_newer, group="tab", exclusive=True)
        row = next((r for r in rows if r["id"] == self.work_item_id), None)
        key = (row.get("updated_at"), tuple(row.get("actions") or ())) if row else None
        if events and key == self._key:
            return
        self._read()
        if self.tab == "diff":  # a commit landed, or the worktree went
            self.run_worker(self._diff, group="tab", exclusive=True)

    async def _read_now(self) -> None:
        try:
            item = await client.get_work_item(self.work_item_id, full=True)
        except client.ClientError as exc:
            if exc.kind == client.Failure.NOT_FOUND:
                self._gone()
            else:
                self.app.notify_refusal(exc)
            return
        if item.get("id") != self.work_item_id:  # an answer for another item
            return
        self.item = item
        self._key = (item.get("updated_at"), tuple(item.get("actions") or ()))
        self._draw_head(item)
        try:
            await self._overview(item)
        except client.ClientError as exc:
            # One copy of the error, not one per failed read.
            self._lines = [client.reason_of(str(exc))]
            self._write_overview()
        self.refresh_bindings()
        # A Log opened before this read had no session to pick; now it has.
        if self._shown("log") and self.session_id is None and not self._dead:
            self.run_worker(self._follow, group="log", exclusive=True)

    def _gone(self) -> None:
        """Archived or abandoned while open (spec §7.2): say so; nothing acts."""
        # Further events read nothing for an item that is not there.
        self.item, self._dead = None, True
        for group in ("tab", "older", "log"):
            self.workers.cancel_group(self, group)
        self.query_one("#item-gone").display = True
        self.query_one("#item-panes").display = False
        self.refresh_bindings()

    def _draw_head(self, item: dict) -> None:
        self.query_one("#item-panes").display = True
        title = " ".join((item.get("title") or "").split())
        self.query_one("#item-title", Static).update(
            Content.assemble((f"{self.crumb} › ", "$kraft-muted"), f"{item['id'][:8]}  ", title)
        )
        repo = os.path.basename((item.get("repo") or "").rstrip("/"))
        self.query_one("#item-where", Static).update(
            Content.styled(f"{repo} · {words.chain_of(item)}", "$kraft-muted")
        )
        now = time.time()
        colour = bar_of(item)
        self.query_one("#item-status", Static).update(
            Content.assemble(
                ("● ", colour),
                (status_words(item), colour),
                (f" · {words.reason_tail(item, now)}", "$kraft-muted"),
            )
        )
        cap = item.get("budget_cap") or {}
        money = ""
        if cap.get("spent_usd") is not None:
            of = f" of {usd(cap['cap_usd'])}" if cap.get("cap_usd") is not None else ""
            money = f"{usd(cap['spent_usd'])}{of} · "
        age = words.ago(item.get("updated_at"), now).removesuffix(" ago")
        self.query_one("#item-spent", Static).update(Content.styled(money + age, "$kraft-muted"))
        said = banner_line(item)
        banner = self.query_one("#item-banner", Static)
        banner.display = said is not None
        if said is not None:
            tone = "$error" if item.get("display_status") == "failed" else "$warning"
            banner.update(Content.assemble(("▌ ", tone), said))
        self._draw_chain()

    def _draw_chain(self) -> None:
        # The frame's width: this view fills it, and may not be laid out yet.
        if self.item is not None:
            marks = words.chain_marks(self.item)
            self.query_one("#item-chain", Static).update(chain_line(marks, self.app.size.width - 2))

    def on_resize(self) -> None:
        self._draw_chain()

    def _draw_tabs(self) -> None:
        parts: list[str | tuple[str, str]] = []
        for key, name in TABS:
            parts += [(f" {name} ", "bold $primary" if key == self.tab else "$kraft-muted"), " "]
        self.query_one("#item-tabs", Static).update(Content.assemble(*parts))

    async def _overview(self, item: dict) -> None:
        """With a gate pending, its document, whole and scrollable, and why
        approve waits; else where the item stands. Then every node."""
        # Nothing is read until the document answers: approve stays shut for
        # the length of the reads, and never offers the last version's digest.
        self.read_gate = self.digest = None
        self.refresh_bindings()
        lines: list[str | Text] = []
        stop = item.get("stop") or {}
        gate = item.get("pending_gate")
        muted = self.app.theme_variables["kraft-muted"]
        if item.get("display_status") == "needs_you" and stop.get("kind") == "gate" and gate:
            doors = item.get("actions") or []
            if "approve" not in doors and (waits := await acts.approve_waits(item["id"])):
                lines.append(Text(waits, style=self.app.theme_variables["warning"]))
            try:
                document = await client.artifact(item["id"])
            except client.ClientError as exc:
                if exc.kind != client.Failure.NOT_FOUND:
                    raise
                document = None
            if document is None:
                lines.append(Text("This gate has no document.", style=muted))
                self.digest = None
            else:
                lines += (document.get("content") or "").splitlines()
                if warning := render.artifact_truncated(document):
                    lines += ["", Text(warning, style=self.app.theme_variables["warning"])]
                self.digest = document.get("digest")
            self.read_gate = gate
        else:
            lines.append(words.reason_tail(item, time.time()))
        lines += ["", Text("Chain", style=f"bold {muted}")]
        styles = {
            "done": muted,
            "current": f"bold {self.app.theme_variables['primary']}",
            "hot": f"bold {self.app.theme_variables['primary']}",
            "todo": "",
        }
        for mark in words.chain_marks(item):
            gate_word = " (gate)" if mark["gate"] else ""
            lines.append(
                Text(
                    f" {MARK[mark['state']]} {mark['node']}{gate_word}", style=styles[mark["state"]]
                )
            )
        self._lines = lines
        self._write_overview()

    def _write_overview(self) -> None:
        """Draw the Overview's lines, once its pane is on screen: kept until
        then, and drawn again when the tab or the view is shown."""
        if not self._shown("overview"):
            return
        log = self.query_one("#overview", RichLog)
        scrolled = log.scroll_y
        log.clear()
        for line in self._lines:
            # From the top: a document opens at its start, not its end.
            log.write(line, scroll_end=False)
        # A live read redraws it: the reader stays where they were.
        log.scroll_to(y=scrolled, animate=False, immediate=True)

    async def _activity_first(self) -> None:
        """The newest page of the item's events (spec §7.2)."""
        page = await self._events(limit=PAGE, before_seq=NEWEST)
        if page is not None:
            self.events, self.more = page[::-1], len(page) == PAGE
            self._draw_activity()

    async def _activity_older(self) -> None:
        """The page before the oldest shown: End at the bottom asks for it."""
        if not self.events:
            return
        since = self.events[-1]["seq"]
        page = await self._events(limit=PAGE, before_seq=since)
        # The first page may have been reread meanwhile: its boundary moved.
        if page is not None and self.events and self.events[-1]["seq"] == since:
            self.events += page[::-1]
            self.more = len(page) == PAGE
            self._draw_activity()
            self.query_one("#activity", RichLog).scroll_end(animate=False)

    async def _activity_newer(self) -> None:
        if not self.events:
            await self._activity_first()
            return
        page = await self._events(after_seq=self.events[0]["seq"])
        if page:
            self.events = page[::-1] + self.events
            self._draw_activity()

    async def _events(self, **since) -> list[dict] | None:
        asked = self.work_item_id
        try:
            page = await client.events(asked, **since)
            # An answer for an item no longer open is dropped.
            return page if asked == self.work_item_id else None
        except client.ClientError as exc:
            if exc.kind == client.Failure.NOT_FOUND:
                self._gone()
            else:
                self.app.notify_refusal(exc)
            return None

    def _draw_activity(self) -> None:
        """One line an event, newest first, as the web's Activity words them;
        the quiet set (bookkeeping) left out."""
        if not self._shown("activity"):  # drawn again by the next visit
            return
        log = self.query_one("#activity", RichLog)
        scrolled = log.scroll_y
        muted = self.app.theme_variables["kraft-muted"]
        now = time.time()
        log.clear()
        for event in self.events:
            if (line := words.event_line(event)) is not None:
                age = words.event_age(event.get("created_at"), now).rjust(7)
                log.write(Text.assemble((age, muted), "  ", line), scroll_end=False)
        if self.more:
            log.write(Text("End loads earlier events.", style=muted), scroll_end=False)
        log.scroll_to(y=scrolled, animate=False, immediate=True)

    async def _diff(self) -> None:
        """Spec §7.2: what has landed on the branch first, under its head, then
        uncommitted work, then the trailer every diff view ends with (4.3), in
        the web's add and delete colours. A gone worktree is the server's own
        words."""
        log = self.query_one("#diff", RichLog)
        try:
            payload = await client.diff(self.work_item_id)
        except client.ClientError as exc:
            if self._shown("diff"):
                log.clear()
                log.write(client.reason_of(str(exc)))
            return
        colour = self.app.theme_variables
        muted, warn = colour["kraft-muted"], colour["warning"]
        lines: list[Text] = []
        if payload.get("base_ref") is None:
            lines.append(Text(render.NO_BASELINE, style=muted))
        else:
            landed = (payload.get("landed") or {}).get("diff") or ""
            if landed:
                lines.append(Text(render.landed_head(payload), style=muted))
                lines += [self._diff_line(line) for line in landed.splitlines()]
                lines += [Text(""), Text(render.UNCOMMITTED_HEAD, style=muted)]
            body = [self._diff_line(line) for line in (payload.get("diff") or "").splitlines()]
            empty = render.NOTHING_UNCOMMITTED if landed else render.NO_CHANGES
            lines += body or [Text(empty, style=muted)]
        how = {"": "", "dim": muted, "warn": warn}
        lines += [Text(line, style=how[kind]) for line, kind in render.diff_trailer(payload)]
        if not self._shown("diff"):  # drawn again by the next visit
            return
        scrolled = log.scroll_y
        log.clear()
        for line in lines:
            log.write(line, scroll_end=False)
        log.scroll_to(y=scrolled, animate=False, immediate=True)

    def _diff_line(self, line: str) -> Text:
        style = next((style for start, style in DIFF_STYLE if line.startswith(start)), "")
        return Text(line, style=self.app.theme_variables.get(style, style))

    def _sessions(self) -> list[dict]:
        """The item's sessions, newest first."""
        return list(reversed((self.item or {}).get("worker_sessions") or []))

    async def _follow(self) -> None:
        """The Log (spec §7.2): the session picked, else the newest, followed
        to its end; while the reader is scrolled up, writes leave them there."""
        log = self.query_one("#log", RichLog)
        log.clear()
        sessions = self._sessions()
        session = next((s for s in sessions if s["id"] == self.session_id), None)
        passed_over = None
        if session is None and self.item is not None:
            session, passed_over = logs.pick_session(self.item, follow=True)
        muted = self.app.theme_variables["kraft-muted"]
        if session is None:
            log.write(Text("No session has run yet.", style=muted))
            return
        self.session_id = session["id"]
        k = next(n for n, s in enumerate(sessions, 1) if s["id"] == session["id"])
        head = f"{session['hook_point']} · {session['status']} · session {k} of {len(sessions)}"
        heads = [Text(head, style=f"bold {muted}")]
        heads += [Text(passed_over, style=muted)] if passed_over else []
        for line in [*heads, Text("waiting for output", style=muted)]:
            log.write(line)
        fresh = True
        try:
            async for entry in client.stream_log(self.work_item_id, session["id"]):
                if fresh:  # the placeholder goes once output comes
                    log.clear()
                    for line in heads:
                        log.write(line)
                    fresh = False
                # As `kraft view logs` reads a line: its summary, else its text.
                text = str(entry.get("summary") or entry.get("text") or "")
                log.write(text, scroll_end=log.is_vertical_scroll_end)
        except client.ClientError as exc:
            log.write(client.reason_of(str(exc)))
            return
        log.write(Text("session ended", style=muted), scroll_end=log.is_vertical_scroll_end)

    def action_session(self) -> None:
        """`s` on the Log: the next older session, round to the newest."""
        ids = [s["id"] for s in self._sessions()]
        at = ids.index(self.session_id) if self.session_id in ids else -1
        self.session_id = ids[(at + 1) % len(ids)]
        self.run_worker(self._follow, group="log", exclusive=True)

    def _pane(self) -> RichLog:
        return self.query_one(f"#{self.tab}", RichLog)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "approve":
            # Elsewhere it shows the Overview; there, only once the document
            # it approves has been read (its digest).
            item = self.item
            return acts.opens(action, item) and (
                self.tab != "overview" or self.read_gate == item.get("pending_gate")
            )
        if action in acts.DOORS:
            return acts.opens(action, self.item)
        if action == "menu":
            return bool(self._menu_entries())
        if action == "session":
            return self.tab == "log" and len(self._sessions()) > 1
        return True

    def action_scroll(self, how: str) -> None:
        if how == "end" and self.tab == "activity" and self.more:
            # Its own group: a live line arriving meanwhile must not cancel it.
            self.run_worker(self._activity_older, group="older", exclusive=True)
        getattr(self._pane(), f"scroll_{how}")(animate=False)

    def action_tab(self, step: int) -> None:
        keys = [key for key, _ in TABS]
        self.show_tab(keys[(keys.index(self.tab) + step) % len(keys)])

    def action_approve(self) -> None:
        """Spec §6: from another tab, `a` shows the Overview first."""
        if self.tab != "overview":
            self.show_tab("overview")
        elif item := self.item:
            digest = self.digest
            # Shut until the next read answers: a second `a` would approve twice.
            self.read_gate = self.digest = None
            self.refresh_bindings()
            self.run_worker(partial(self._approve, item, digest), group="act")

    async def _approve(self, item: dict, digest: str | None) -> None:
        gate = item["pending_gate"]
        await acts.act(
            self.app,
            client.approve_gate(gate, item["id"], digest),
            answers.item_action(f"approved {gate} on {{id}}"),
        )

    def action_reject(self) -> None:
        if item := self.item:
            self.run_worker(
                partial(acts.reject, self.app, item, self._reject_dialog(item)), group="act"
            )

    def _reject_dialog(self, item: dict) -> Reject | None:
        """The note with where the work goes back to: the exec nodes before
        the gate, its fix target chosen. None (the plain note, the server's
        own target) when the target is not one of them."""
        nodes = (item.get("chain_definition") or {}).get("nodes") or []
        gate = item.get("pending_gate")
        at = next((k for k, node in enumerate(nodes) if node.get("id") == gate), 0)
        before = [node["id"] for node in nodes[:at] if node.get("kind") != "gate"]
        to = (item.get("fix_target") or {}).get("node")
        if to not in before:
            return None
        return Reject(
            f"Reject {gate} on {item['id'][:8]}",
            ["Say what is wrong: the note goes to the agent that redoes it, at the node below."],
            nodes=before,
            to=to,
        )

    def _menu_entries(self) -> list[tuple[str, str]]:
        doors = (self.item or {}).get("actions") or []
        return [(door, label) for door, label in MENU if door in doors]

    def action_menu(self) -> None:
        if item := self.item:
            self.run_worker(partial(self._menu, item), group="act")

    async def _menu(self, item: dict) -> None:
        """Pick an entry, then ask what it needs and send it, each in the
        CLI's own sentence: the same `acts.act` the keys use."""
        title = " ".join((item.get("title") or "").split())
        name = await self.app.push_screen_wait(
            Menu(f"{item['id'][:8]}  {title}", self._menu_entries())
        )
        # The menu was modal: the Item may have moved on or gone meanwhile.
        if name is not None and self.work_item_id == item["id"] and not self._dead:
            await getattr(self, f"_{name}")(item)

    async def _ask(self, question: str, lines: list[str], *, required: bool) -> str | None:
        return await self.app.push_screen_wait(Note(question, lines, required=required))

    async def _skip(self, item: dict) -> None:
        what = item.get("pending_gate") or item.get("current_node_id") or "the current node"
        note = await self._ask(f"Skip {what} on {item['id'][:8]}", [SKIP], required=False)
        if note is not None:
            said = answers.item_action(answers.skipped(what))
            await acts.act(self.app, client.skip(note or None, item["id"]), said)

    async def _unblock(self, item: dict) -> None:
        await acts.act(self.app, client.unblock(item["id"]), answers.unblocked)

    async def _escalate(self, item: dict) -> None:
        message = await self._ask(f"Escalate {item['id'][:8]}", [ESCALATE], required=True)
        if message:
            said = answers.item_action(answers.ESCALATED, small=answers.escalated)
            await acts.act(self.app, client.escalate(message, item["id"]), said)

    async def _complete(self, item: dict) -> None:
        reason = await self._ask(f"Complete {item['id'][:8]}", [REASON], required=True)
        if reason:
            said = answers.item_action(answers.COMPLETED, status=False)
            await acts.act(self.app, client.complete(reason, item["id"]), said)

    async def _cancel(self, item: dict) -> None:
        """The confirm is the reason's dialog (spec §7.8): what cancelling stops
        and keeps, from `cancel_preview`, above the required reason."""
        try:
            preview = await client.cancel_preview(item["id"])
        except client.ClientError as exc:
            self.app.notify_refusal(exc)
            self.app.live.reread()
            return
        lines = [*cancel_lines(preview), "", REASON]
        reason = await self._ask(f"Cancel {item['id'][:8]}?", lines, required=True)
        if reason:
            said = answers.item_action(answers.CANCELLED, status=False)
            await acts.act(self.app, client.cancel(reason, item["id"]), said)

    async def _archive(self, item: dict) -> None:
        """Through the CLI (spec §4.2): its preview, its refusals and its
        result in the output panel, after the console's confirm (spec §7.8)."""
        await self.app.run_asking(["item", "archive", item["id"], "--yes"])

    async def _raise_budget(self, item: dict) -> None:
        """Spec §7.2: a cap at or below what was spent is the server's 422,
        said in the dialog, which asks again; a cap raised whose retry was
        refused is said in the server's words, which say both."""
        error = ""
        while True:
            typed = await self.app.push_screen_wait(
                Note(f"Raise the budget of {item['id'][:8]}", [RAISE], required=True, error=error)
            )
            if typed is None:
                return
            try:
                usd_ = answers.dollars(typed)
            except ValueError as exc:
                error = str(exc)
                continue
            try:
                result = await client.raise_budget(usd_, item["id"])
            except client.ClientError as exc:
                if exc.status == 422:
                    error = client.reason_of(str(exc))
                    continue
                self.app.notify_refusal(exc)
                self.app.live.reread()
                return
            self.app.notify(answers.raised_budget(usd_)(result), markup=False)
            return

    def action_pause_or_resume(self) -> None:
        if item := self.item:
            self.run_worker(partial(acts.pause_or_resume, self.app, item), group="act")

    def action_retry(self) -> None:
        if item := self.item:
            self.run_worker(partial(acts.retry, self.app, item), group="act")
