"""The console's frame (spec §6): the top line, the view switcher, the footer.

One Screen for the whole session: the views swap in its middle, so the top
line (and, from PR 2b, the prompt) stays on every view."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterable
from typing import ClassVar

from textual import events
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.content import Content
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import ContentSwitcher, Footer, HelpPanel, Static

from kraft import client, update
from kraft.console import theme
from kraft.console.live import Link, Live
from kraft.console.screens import VIEWS, View

#: The top views, in tab order: the number keys `1`-`4` follow it.
TABS = ("board", "server", "addons", "repos")
#: Below this the frame shows only "make the window larger" (spec §6).
MIN_SIZE = (60, 16)
#: Below this many columns the rows drop chain and age, and the footer keeps
#: only `? keys`: the console still works as a side pane.
NARROW = 80


def _theme(name: str, tokens: dict[str, str], *, dark: bool) -> Theme:
    """A Textual theme from the web's tokens (spec §5.3): Textual's own slots,
    and the rest as `$kraft-*`, so none collides with Textual's `$text-muted`
    or `$surface`."""
    return Theme(
        name=name,
        primary=tokens["accent"],
        warning=tokens["warn"],
        error=tokens["bad"],
        success=tokens["ok"],
        background=tokens["bg"],
        surface=tokens["surface"],
        foreground=tokens["text"],
        dark=dark,
        variables={
            "kraft-side": tokens["side"],
            "kraft-line": tokens["line"],
            "kraft-info": tokens["info"],
            "kraft-muted": tokens["text-muted"],
            "kraft-faint": tokens["text-faint"],
        },
    )


THEMES = (
    _theme("kraft-dark", theme.DARK, dark=True),
    _theme("kraft-light", theme.LIGHT, dark=False),
)


def home_relative(path: str) -> str:
    """`path` with the home directory written `~`, only at a path boundary."""
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


def down_sentence(error: client.ClientError | None) -> str:
    """What every view says while the link is down (spec §8)."""
    if error is not None and error.kind == client.Failure.UNAUTHORIZED:
        return "The server refused this machine's token (run/mcp-token)."
    if error is not None and error.kind != client.Failure.UNREACHABLE:
        # A server that answers, but not with the board: its own words.
        return f"The server could not show the board: {client.reason_of(str(error))}"
    return f"No server at {client.base_url().removeprefix('http://')}."


class KraftConsole(App):
    CSS_PATH = "kraft.tcss"
    TITLE = "Kraft"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "show('board')", "Board", show=False),
        Binding("2", "show('server')", "Server", show=False),
        Binding("3", "show('addons')", "Add-ons", show=False),
        Binding("4", "show('repos')", "Repos", show=False),
        Binding("escape", "back", "back", show=False),
        Binding("q", "back(True)", "back or quit", show=False),
        Binding("question_mark", "keys", "keys"),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Before the stylesheet is read: kraft.tcss names `$kraft-*`, which
        # only a registered theme defines.
        for kraft_theme in THEMES:
            self.register_theme(kraft_theme)
        self.theme = "kraft-dark"
        self.version = update.installed()
        self.rows: list[dict] = []
        self.health: dict | None = None
        self._health_answered = False
        #: The repo the console was opened in, or None for all repos.
        self.scope: str | None = None
        self.link = Link.RECONNECTING
        self.update_waiting = False
        self._scoped = False
        self._last_top = "board"
        self.live = Live(
            on_event=self._live_event, on_health=self._live_health, on_link=self._live_link
        )

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static(id="tabs")
            yield Static(id="where")
        yield Static(id="down")
        yield Static("make the window larger", id="tiny")
        with ContentSwitcher(initial="board"):
            for name, view in VIEWS.items():
                yield view(id=name)
        yield Footer(show_command_palette=False)
        # Textual's Footer cannot hide one key, so a narrow frame swaps it for this.
        yield Static("? keys", id="narrow-keys")

    def on_mount(self) -> None:
        self.show("board")
        self.run_worker(self.live.run, name="live")
        # After the first paint: a cold cache reads the release feed over the network.
        self.call_after_refresh(self.run_worker, self._check_update, name="update")

    @property
    def view(self) -> View:
        """The view the middle shows."""
        return self.query_one(ContentSwitcher).visible_content

    def show(self, name: str) -> View:
        """Switch the middle to the view called `name` (a key of `VIEWS`) and
        hand it the focus; return it, so a caller can tell it what to show."""
        switcher = self.query_one(ContentSwitcher)
        switcher.current = name
        view = switcher.query_one(f"#{name}", View)
        if name in TABS:
            self._last_top = name
        view.shown()
        self._draw_top()
        return view

    def notify_refusal(self, error: client.ClientError) -> None:
        """A call that failed, said in the server's words (spec §8). A token
        refusal is a state, not a toast: every view says so until it clears."""
        if error.kind == client.Failure.UNAUTHORIZED:
            self._live_link(Link.DOWN, error)
            return
        # markup=False: the server's words may hold `[/]`, which markup reads as a tag.
        self.notify(client.reason_of(str(error)), severity="error", markup=False)

    def action_show(self, name: str) -> None:
        self.show(name)

    def action_back(self, quit: bool = False) -> None:
        if self.view.back():
            return
        if self.view.id not in TABS:
            self.show(self._last_top)
        elif quit:
            self.exit()

    def action_keys(self) -> None:
        if self.screen.query(HelpPanel):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        # Textual's own theme picker lists its themes; Kraft has two.
        for command in super().get_system_commands(screen):
            if command.title != "Theme":
                yield command
        yield SystemCommand("Dark", "Kraft's dark look", lambda: self._use("kraft-dark"))
        yield SystemCommand("Light", "Kraft's light look", lambda: self._use("kraft-light"))

    def _use(self, name: str) -> None:
        self.theme = name

    def on_resize(self, event: events.Resize) -> None:
        width, height = event.size
        # The frame, not whatever is on top: Ctrl+P's palette is a pushed screen.
        self.default_screen.set_class(width < MIN_SIZE[0] or height < MIN_SIZE[1], "-tiny")
        self.default_screen.set_class(width < NARROW, "-narrow")

    def _live_event(self, rows: list[dict], events: list[dict]) -> None:
        self.rows = rows
        self._draw_top()
        for view in self.query(View):
            view.rows_changed(rows, events)

    def _live_health(self, health: dict | None) -> None:
        self.health = health
        self._health_answered = True
        self._draw_top()
        for view in self.query(View):
            view.health_changed(health)

    def _live_link(self, link: Link, error: client.ClientError | None) -> None:
        self.link = link
        self.default_screen.set_class(link is Link.DOWN, "-down")
        if link is Link.DOWN:
            self.query_one("#down", Static).update(Content(down_sentence(error)))
        if link is Link.LIVE and not self._scoped:
            self.run_worker(self._resolve_scope, name="scope", group="scope", exclusive=True)
        self._draw_top()

    async def _resolve_scope(self) -> None:
        try:
            self.scope = await client.resolve_repo()
        except client.ClientError:
            return  # read again on the next reconnect
        self._scoped = True
        self._draw_top()

    async def _check_update(self) -> None:
        try:
            found = await client.update_available()
        except client.ClientError:
            return  # the top line just does not say; the Server view reads it again
        self.update_waiting = bool(found.get("behind"))
        self._draw_top()

    def _draw_top(self) -> None:
        live = self.link is Link.LIVE
        tabs: list[str | tuple[str, str]] = []
        for name, count in self._tabs():
            tabs.append((VIEWS[name].label, "bold" if name == self._last_top else "$kraft-muted"))
            if count:
                tabs += [" ", count]
            tabs.append(" · ")
        self.query_one("#tabs", Static).update(
            Content.assemble(
                ("Kraft ", "bold"),
                ("● live", "$success") if live else ("● reconnecting", "$kraft-muted"),
                "   ",
                *tabs[:-1],
            )
        )
        where = home_relative(self.scope) if self.scope else "all repos"
        update_note = " · update available" if self.update_waiting else ""
        self.query_one("#where", Static).update(Content(f"{where} · v{self.version}{update_note}"))

    def _tabs(self) -> list[tuple[str, str | tuple[str, str]]]:
        """Each top view's tab with its count (spec §5.4): how many need you
        in scope, the server's health word, how many add-on updates wait."""
        needs_you = sum(
            row.get("display_status") == "needs_you" and self.scope in (None, row.get("repo"))
            for row in self.rows
        )
        held = sum(
            entry.get("outcome") == "held"
            for entry in ((self.health or {}).get("addon_updates") or {}).values()
        )
        status = (self.health or {}).get("status", "down")
        counts = {
            "board": (str(needs_you), "$warning") if needs_you else "",
            "server": status if self._health_answered else "…",
            "addons": str(held) if held else "",
            "repos": "",
        }
        return [(name, counts[name]) for name in TABS]


def main() -> None:
    """`kraft console`: refuse where a terminal UI cannot work (spec §3), else
    run. A refusal is a ValueError, which `kraft` prints as `kraft: ...`."""
    if os.environ.get("KRAFT_WORK_ITEM_ID"):
        raise ValueError("a worker does not open the console")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ValueError("console needs a terminal to draw in — try `kraft view list` in a pipe")
    try:
        asyncio.run(client.health())
    except client.ClientError as exc:
        # Another instance on this port would be shown as this machine's; no
        # server at all still opens, and says so.
        if exc.kind == client.Failure.OTHER_INSTANCE:
            raise
    app = KraftConsole()
    app.run()
    raise SystemExit(app.return_code or 0)
