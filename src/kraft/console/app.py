"""The console's frame (spec §6): the top line, the view switcher, the footer.

One Screen for the whole session: the views swap in its middle, so the top
line (and, from PR 2b, the prompt) stays on every view."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import signal
import sys
from collections.abc import Callable, Iterable, Sequence
from functools import partial
from typing import ClassVar
from urllib.parse import urlsplit

from textual import events, on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding, BindingType
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal
from textual.content import Content
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import Button, ContentSwitcher, Footer, HelpPanel, Input, Static

from kraft import answers, client, config, update
from kraft.console import runner, theme, words
from kraft.console.dialogs import Confirm, Dialog
from kraft.console.live import Link, Live
from kraft.console.output import OutputPanel
from kraft.console.prompt import Prompt, commands
from kraft.console.screens import VIEWS, Board, Item, View
from kraft.console.screens.server import updates_waiting
from kraft.vocab import WorkItemStatus

#: The top views, in tab order: the number keys `1`-`4` follow it.
TABS = ("board", "server", "addons", "repos")
#: Below this the frame shows only "make the window larger" (spec §6).
MIN_SIZE = (60, 16)
#: Below this many columns the rows drop chain and age, and the footer keeps
#: only `? keys`: the console still works as a side pane.
NARROW = 80
#: How long a confirm waits for the server to list its running items before
#: it asks without them, as `kraft admin stop` does (`cli.admin._LIST_TIMEOUT`).
CONFIRM_LIST_S = 2.0
#: From this many columns the top line also names the version; under it the
#: Server screen does (Kraft-zrf14.14: at 80 the tabs ran into the scope).
WIDE = 100
#: Said after `kraft admin update` installed a version the console did not start on.
UPDATED = "Kraft was updated; restart the console to use the new version."


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


def starts_here() -> bool:
    """Whether "Start server" can start the server the console reads: only
    one on this machine (spec §7.3)."""
    return urlsplit(client.base_url()).hostname in config.LOOPBACK


#: A palette entry that goes to a screen: the only ones listed before anything is typed.
SCREEN = "Go to this screen"


class Go(Provider):
    """Ctrl+P's Kraft entries (spec §7.7): a screen, an item by its id or
    title, a verb."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for title, about, run in self.app.places():
            if (score := matcher.match(title)) > 0:
                # Content, not a str: the palette reads a str as markup, and a
                # title may hold `[/]`. shortcut: no match highlighting.
                yield Hit(score, Content(title), run, text=title, help=about)

    async def discover(self) -> Hits:
        for title, about, run in self.app.places():
            if about == SCREEN:
                yield DiscoveryHit(Content(title), run, text=title, help=about)


class KraftConsole(App):
    CSS_PATH = "kraft.tcss"
    TITLE = "Kraft"
    COMMANDS = App.COMMANDS | {Go}
    SCREENS: ClassVar[dict] = {"output": OutputPanel}
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "show('board')", "Board", show=False),
        Binding("2", "show('server')", "Server", show=False),
        Binding("3", "show('addons')", "Add-ons", show=False),
        Binding("4", "show('repos')", "Repos", show=False),
        Binding("escape", "back", "back", show=False),
        Binding("q", "back(True)", "back or quit", show=False),
        Binding("question_mark", "keys", "keys"),
        Binding("slash", "prompt", "command", show=False),
        Binding("ctrl+o", "output", "output", show=False),
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
        #: `s` on the Board: every repo, though the console was opened in one.
        self.everywhere = False
        self.link = Link.RECONNECTING
        self.update_waiting = False
        #: `update_available()`'s last answer: the Server names the release.
        self.release: dict | None = None
        #: An update installed another version than this console runs (spec §3, §7.3).
        self.updated = False
        self._scoped = False
        self._last_top = "board"
        #: The command running in the output panel; one at a time.
        self.command: runner.Run | None = None
        #: The command a first stop was sent to: a second one kills it.
        self._stopping: runner.Run | None = None
        self.live = Live(
            on_event=self._live_event, on_health=self._live_health, on_link=self._live_link
        )

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static(id="tabs")
            yield Static(id="where")
        yield Static(Content(UPDATED), id="updated")
        yield Static(id="down")
        if starts_here():
            yield Button("Start server", id="start")
        yield Static("make the window larger", id="tiny")
        with ContentSwitcher(initial="board"):
            for name, view in VIEWS.items():
                yield view(id=name)
        yield Prompt()
        yield Footer(show_command_palette=False)
        # Textual's Footer cannot hide one key, so a narrow frame swaps it for this.
        yield Static("? keys", id="narrow-keys")

    def on_mount(self) -> None:
        self.show("board")
        self.run_worker(self.live.run, name="live")
        # The child is in its own session, so a closed terminal or a `kill` never
        # reaches it; leaving through `exit()` runs `on_unmount`, which ends it.
        # Not the main thread, or no signals here: nothing to hand over.
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGHUP, signal.SIGTERM):
                loop.add_signal_handler(sig, self.exit)
        # After the first paint: a cold cache reads the release feed over the network.
        self.call_after_refresh(self.run_worker, self.check_update, name="update")

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

    @property
    def repo(self) -> str | None:
        """The repo the Board and its count show: the console's own, or None
        for every repo."""
        return None if self.everywhere else self.scope

    def widen(self, everywhere: bool) -> None:
        """Show every repo, or only the console's own (`s` on the Board)."""
        self.everywhere = everywhere
        self._draw_top()

    def places(self) -> list[tuple[str, str, Callable[[], object]]]:
        """What Ctrl+P can go to, as `(title, help, callback)`: the screens,
        each item on the board, and every verb, typed at the prompt."""
        screens = [(VIEWS[name].label, SCREEN, partial(self.show, name)) for name in TABS]
        items = [
            (
                f"{row['id'][:8]}  {' '.join((row.get('title') or '').split())}",
                "Open it",
                partial(self.open_item, row["id"]),
            )
            for row in self.rows
            if row.get("display_status") != "archived"
        ]
        verbs = [
            (f"/{line}", "Type it at the prompt", partial(self.type_command, f"/{line} "))
            for line in commands()
        ]
        return screens + items + verbs

    def open_item(self, work_item_id: str) -> None:
        """The Item view on `work_item_id` (spec §7.2): Enter and `a` on the
        Board, and Ctrl+P's items. Esc comes back to the last top view."""
        self.show("item")
        self.query_one("#item", Item).open(work_item_id)

    def type_command(self, line: str) -> None:
        self.action_prompt()
        prompt = self.query_one(Prompt)
        prompt.value = line
        prompt.cursor_position = len(line)

    def notify_refusal(self, error: client.ClientError) -> None:
        """A call that failed, said in the server's words (spec §8). A token
        refusal is a state, not a toast: every view says so until it clears."""
        if error.kind == client.Failure.UNAUTHORIZED:
            self._live_link(Link.DOWN, error)
            return
        # markup=False: the server's words may hold `[/]`, which markup reads as a tag.
        self.notify(client.reason_of(str(error)), severity="error", markup=False)

    async def run_command(self, argv: list[str]) -> int | None:
        """Run `kraft <argv>` in the output panel, opening it (spec §4.7), and
        return its exit code; None, and a toast, while another is running.
        Await it from a worker."""
        if self.command is not None:
            self.notify("A command is still running: Ctrl+O shows it, x stops it.", markup=False)
            return None
        panel = self.get_screen("output", OutputPanel)
        run = self.command = runner.Run(argv, panel.write_line)
        # An app worker owns the bookkeeping: the caller's worker dies with the
        # screen it ran on, and must then only stop waiting. Quitting cancels
        # this one too, and `on_unmount` ends the command then.
        done = asyncio.Future[int]()
        self.run_worker(partial(self._run, run, panel, done), group="command")
        return await asyncio.shield(done)

    async def _run(self, run: runner.Run, panel: OutputPanel, done: asyncio.Future[int]) -> None:
        await self._show_panel()
        panel.begin(run.argv)
        code = await run.finish()
        self.command = None
        panel.end(code)
        done.set_result(code)
        if run.argv[:2] == ["admin", "update"]:  # a key's or a typed one
            self._after_update()

    async def _show_panel(self) -> None:
        """The panel on top, once: Ctrl+P can open over it. A dialog is never
        popped (that drops its result callback, and the pending answer with
        it): the panel opens above it instead, since a command needs a mounted
        panel to write to, and Esc then shows the dialog again."""
        panel = self.get_screen("output", OutputPanel)
        if panel not in self.screen_stack:
            await self.push_screen(panel)
        while self.screen is not panel and not isinstance(self.screen, Dialog):
            await self.pop_screen()

    def stop_command(self) -> None:
        """SIGINT; pressed again while the same command runs, the kill."""
        run = self.command
        if run is None:
            return
        if run is self._stopping:
            # `end()` waits up to QUIT_GRACE_S: say so, or the press seems to do nothing.
            self.get_screen("output", OutputPanel).note("stopping…")
            self.run_worker(run.end)
        else:
            self._stopping = run
            run.stop()

    async def confirm(
        self, question: str, lines: Sequence[str] = (), *, running: str | None = None
    ) -> bool:
        """Ask before what cannot be undone (spec §7.8). With `running`, the
        word for what ends the agents ("restarting"), the running items are
        listed too. Await it from a worker: it waits for the answer."""
        lines = list(lines)
        if running is not None:
            try:
                async with asyncio.timeout(CONFIRM_LIST_S):
                    items = await client.list_work_items(WorkItemStatus.ACTIVE)
            except (client.ClientError, TimeoutError):
                items = []  # a server that does not answer has none to list
            lines += answers.running_agents_warning(running, items)
        return await self.push_screen_wait(Confirm(question, lines))

    async def on_unmount(self) -> None:
        # Quitting with a command running (spec §8): SIGINT, a bounded wait, SIGKILL.
        if self.command is not None:
            await self.command.end()

    def action_prompt(self) -> None:
        prompt = self.query_one(Prompt)
        prompt.value = "/"
        prompt.cursor_position = 1
        prompt.focus()

    def action_output(self) -> None:
        self.run_worker(self._show_panel)

    @on(Input.Submitted, "#prompt")
    def _typed(self, event: Input.Submitted) -> None:
        if event.value.strip(" /"):
            self.run_worker(partial(self._run_typed, event.value), group="typed")

    async def _run_typed(self, line: str) -> None:
        kind, what = runner.route(line)
        if kind == "show":
            self.show(what)
        elif kind == "say":
            self.notify(what, markup=False)
        else:
            await self.run_asking(what)

    async def run_asking(self, argv: list[str]) -> None:
        """Run `kraft <argv>` in the output panel, after the console's own
        confirm where the verb cannot be undone (spec §7.8). Await it from a
        worker."""
        if self.command is not None:
            # Before the question: its answer would be thrown away.
            self.notify("A command is still running: Ctrl+O shows it, x stops it.", markup=False)
            return
        asked = runner.ask(argv)
        if asked is not None:
            sentence, doing = asked
            if not await self.confirm(f"kraft {shlex.join(argv)}", [sentence], running=doing):
                return
        await self.run_command(argv)

    @on(Button.Pressed, "#start")
    def _start(self) -> None:
        self.run_worker(self._start_server, group="typed")

    async def _start_server(self) -> None:
        if await self.run_command(["admin", "start", "--detach"]) == 0:
            # It returns once the server answers: follow it now, not after the backoff.
            self.live.retry()

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
        self._draw_top(width)

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
        was_down = self.link is Link.DOWN
        self.link = link
        frame = self.default_screen
        frame.set_class(link is Link.DOWN, "-down")
        no_server = link is Link.DOWN and (
            error is None or error.kind == client.Failure.UNREACHABLE
        )
        frame.set_class(no_server, "-no-server")
        start = self.query_one_optional("#start", Button)
        if link is Link.DOWN:
            self.query_one("#down", Static).update(Content(down_sentence(error)))
            # Focused once, on the way down: Enter presses it (spec §6).
            # But never from where a person is typing: only off the view or nothing.
            if no_server and not was_down and start is not None:
                if (
                    frame.focused is None
                    or frame.focused is start
                    or self.view in frame.focused.ancestors_with_self
                ):
                    frame.set_focus(start)
        elif was_down and frame.focused in (None, start):
            # The frame's own focus, which the output panel may be covering.
            frame.set_focus(self.view)
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
        self.query_one("#board", Board).draw()

    async def check_update(self) -> None:
        """Read the release feed through the server, keeping the answer for
        the Server view; a failure leaves the last one (the Server reads
        again when shown)."""
        try:
            self.release = await client.update_available()
        except client.ClientError:
            return
        self.update_waiting = bool(self.release.get("behind"))
        self._draw_top()

    def _after_update(self) -> None:
        """After `kraft admin update` (spec §7.3): a new install is said until
        the console restarts, since it runs the modules it started on, and
        `updated` closes native add-on install and update (spec §3). The feed
        is read again: "update available" may be over."""
        if update.installed() != self.version:
            self.updated = True
            self.default_screen.add_class("-updated")
        self.run_worker(self.check_update, name="update")

    def _draw_top(self, width: int | None = None) -> None:
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
        where = home_relative(self.repo) if self.repo else "all repos"
        # `width` from a resize: inside one, `self.size` is still the old size.
        wide = (width or self.size.width) >= WIDE
        version = f" · v{self.version}" if wide else ""
        update_note = " · update available" if self.update_waiting else ""
        self.query_one("#where", Static).update(Content(f"{where}{version}{update_note}"))

    def _tabs(self) -> list[tuple[str, str | tuple[str, str]]]:
        """Each top view's tab with its count (spec §5.4): how many need you
        in scope, the server's health word, how many add-on updates wait."""
        needs_you = sum(
            words.group_of(row) == "needs" and self.repo in (None, row.get("repo"))
            for row in self.rows
        )
        held = updates_waiting(self.health)
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
