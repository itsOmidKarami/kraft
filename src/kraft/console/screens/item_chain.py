"""The Item's Chain tab: its state, its pane and its keys, for the Item view
(`MenuActions`' pattern). What the lines say is `kraft.console.chain`."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import RichLog

from kraft.console import chain, words

#: The panel edge's hint: the tabs' keys, and while a node is open its own.
EDGE = "← → tabs"
EDGE_OPEN = "← close · → tabs"


class ChainPane(RichLog):
    """The Chain tab's pane: it draws again when its width changes, because a
    row is padded to the width it was written at and a RichLog keeps its lines."""

    def on_resize(self) -> None:
        for above in self.ancestors:
            if isinstance(above, ChainTab):
                above._write_chain()
                return


class ChainTab:
    """The Chain tab for the Item view (`self.item`, `self.tab`, `self.app`)."""

    def _init_chain(self) -> None:
        #: The item's events, oldest first: its gate decisions, skips and notes.
        self.chain_events: list[dict] = []
        #: The node the cursor is on (None until the first draw), whether it is
        #: open, and the session the cursor is on inside it, by id and index.
        self.chain_at: int | None = None
        self.chain_open = False
        self.chain_session_id: str | None = None
        self.chain_session = 0
        #: What the next draw brings into view, "cursor" or an "open" node; any
        #: other draw leaves the reader where they were.
        self._reveal: str | None = "cursor"

    def _reset_chain(self) -> None:
        self._init_chain()

    def _chain_beat(self, tick: int) -> bool:
        """The clock's beat: an open node's running session ticks."""
        if not (self._shown("chain") and self._running_in_node()):
            return False
        self._write_chain()
        return True

    def _running_in_node(self) -> bool:
        return any(s.get("status") == "running" for s in self._node_sessions())

    async def _chain_first(self) -> None:
        """Every event of the item, once: the tab shows (or the item changed)."""
        page = await self._events(after_seq=0)
        if page is not None:
            self.chain_events = page
            self._write_chain()

    async def _chain_newer(self) -> None:
        """What came after the newest event read, as the Activity tops up."""
        if not self.chain_events:
            await self._chain_first()
            return
        page = await self._events(after_seq=self.chain_events[-1]["seq"])
        if page:
            self.chain_events += page
            self._write_chain()

    def _write_chain(self) -> None:
        """Draw the Chain tab, once its pane is on screen: kept until then,
        and drawn again when the tab or the view is shown."""
        item = self.item
        if not self._shown("chain") or item is None:
            return
        nodes = words.nodes_of(item) or []
        log = self.query_one("#chain", RichLog)
        if not log.size.width:  # not laid out yet: its resize draws it
            return
        scrolled = log.scroll_y
        log.clear()
        if not nodes:
            muted = self.app.theme_variables["kraft-muted"]
            log.write(Text("This item's chain has no nodes.", style=muted))
            return
        if self.chain_at is None:
            self.chain_at = chain.first_cursor(chain.node_states(item, self.chain_events))
        self.chain_at = min(self.chain_at, len(nodes) - 1)
        sessions = self._node_sessions()
        ids = [s["id"] for s in sessions]
        if self.chain_session_id in ids:  # the session itself, not its place
            self.chain_session = ids.index(self.chain_session_id)
        self.chain_session = min(self.chain_session, max(len(sessions) - 1, 0))
        self.chain_session_id = ids[self.chain_session] if ids else None
        width = log.scrollable_content_region.width
        lines, on, top = chain.chain_lines(
            item,
            self.chain_events,
            (self.chain_at, self.chain_open, self.chain_session),
            width,
            (self._now(), self.app.pulse.tick),
            self.app.theme_variables,
        )
        for line in lines:  # at the pane's width: a row is padded to it
            log.write(line, width=width, scroll_end=False)
        high = scrolled
        height = max(log.scrollable_content_region.height, 1)
        reveal, self._reveal = self._reveal, None
        if reveal == "open":  # an opened node shows from its row
            high = top
        elif reveal == "cursor":
            high = min(max(scrolled, on - height + 1), on)
        if reveal:  # after layout: a pane just sized has not its scroll range yet
            log.call_after_refresh(log.scroll_to, y=high, animate=False)
        else:
            log.scroll_to(y=high, animate=False, immediate=True)
        if self._running_in_node():
            self.app.pulse.wake()

    def _node_sessions(self) -> list[dict]:
        """The open node's sessions, as the tab lists them; none when closed."""
        nodes = words.nodes_of(self.item or {}) or []
        if not (self.chain_open and self.chain_at is not None and self.chain_at < len(nodes)):
            return []
        return chain.sessions_of(self.item, nodes[self.chain_at]["id"])

    def _sync_edge(self) -> None:
        """The panel edge names the keys that are live: ← closes an open node."""
        opened = self.tab == "chain" and self.chain_open
        self.query_one("#item-head").border_subtitle = EDGE_OPEN if opened else EDGE

    def _can_open(self) -> bool:
        return bool(self.item and words.nodes_of(self.item))

    def check_chain_action(self, action: str) -> bool | None:
        """Whether the Chain's own keys act now; None for any other action."""
        if action == "open":
            # A node opens from the list; an open node's session opens its log.
            if self.tab != "chain" or not self._can_open():
                return False
            return not self.chain_open or bool(self._node_sessions())
        if action == "close_node":
            return self.tab == "chain" and self.chain_open
        if action == "left":  # ← is a tab key until a node is open
            return not (self.tab == "chain" and self.chain_open)
        return None

    def _close_node(self) -> None:
        self.chain_open = False
        self._reveal = "cursor"
        self._write_chain()
        self._sync_edge()
        self.refresh_bindings()

    def action_close_node(self) -> None:
        if self.tab == "chain" and self.chain_open:
            self._close_node()

    def action_left(self) -> None:
        self.action_tab(-1)

    def action_open(self) -> None:
        """Enter on the Chain tab: open the node under the cursor; in an open
        node, the Log on the session under the cursor."""
        if not self._can_open():
            return
        if not self.chain_open:
            self.chain_open, self.chain_session, self.chain_session_id = True, 0, None
            self._reveal = "open"
            self._write_chain()
            self._sync_edge()
            self.refresh_bindings()
        elif sessions := self._node_sessions():
            self.session_id = sessions[self.chain_session]["id"]
            self.show_tab("log")

    def _move_cursor(self, how: str) -> None:
        """↑ and ↓ on the Chain tab: the node under the cursor, or in an open
        node its session; Home and End go to the first and last."""
        count = (
            len(self._node_sessions())
            if self.chain_open
            else len(words.nodes_of(self.item or {}) or [])
        )
        if not count:
            return
        at = self.chain_session if self.chain_open else self.chain_at or 0
        at = {"up": at - 1, "down": at + 1, "home": 0, "end": count - 1}[how]
        at = max(0, min(at, count - 1))
        if self.chain_open:
            self.chain_session = at
            self.chain_session_id = self._node_sessions()[at]["id"]
        else:
            self.chain_at = at
        self._reveal = "cursor"
        self._write_chain()
