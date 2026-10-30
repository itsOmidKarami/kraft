"""The Server (spec §7.3): how the server is, from `/health` and the release
feed, with `server.log` followed under it. `l` reloads the config here;
doctor, restart and update run their `kraft admin` verbs in the output
panel, restart and update after the console's own confirm."""

from __future__ import annotations

import collections
import re
from functools import partial
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.content import Content
from textual.widgets import Input, RichLog, Static

from kraft import answers, client, render
from kraft.console import tail, words
from kraft.console.panels import Rule
from kraft.console.screens import View

#: Lines of `server.log` kept, for the pane and for the filter to search.
MAX_LINES = 2000
#: Lines of `server.log` read when the view is shown.
BACKLOG = 200
#: What the rule over the log says while new lines move it to its end.
FOLLOWING = "following · f filter"
#: And while the reader has scrolled up, which holds it where it is.
HELD = "paused · End follows · f filter"


#: A `server.log` record's level word: after the `2026-10-16 12:04:58` it is
#: written with, or (a log from before records were one line) after Rich's time
#: column or the blank it leaves. Continuation lines have none.
LEVEL = re.compile(
    r"^(?:\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\s+|\[[^\]]*\]\s+|\s+)(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b"
)
#: The `file.py:123` after the level, or at the end of an older record's first line.
SOURCE = re.compile(r"(?<=\s)\S+\.py:\d+(?=\s)|\S+\.py:\d+\s*$")
#: The level words drawn in a colour; the word stays, so colour is not alone.
LEVEL_COLOUR = {"WARNING": "warning", "ERROR": "error", "CRITICAL": "error"}


def log_line(line: str, colours: dict[str, str]) -> Text:
    """One line of `server.log` as written (never markup), its level word in
    its colour and its source dimmed. Rich wrapped the record when it wrote
    it; the console does not unwrap it."""
    text = Text(line)
    if (level := LEVEL.match(line)) and (colour := LEVEL_COLOUR.get(level[1])):
        text.stylize(colours[colour], level.start(1), level.end(1))
    if source := SOURCE.search(line):
        text.stylize(colours["kraft-faint"], source.start(), source.end())
    return text


def held(health: dict | None) -> set[str]:
    """The add-ons whose update waits for a person: `/health`'s held
    `addon_updates`."""
    return {
        addon_id
        for addon_id, entry in ((health or {}).get("addon_updates") or {}).items()
        if entry.get("outcome") == "held"
    }


def updates_waiting(health: dict | None) -> int:
    """How many: the Add-ons tab's count and the Server's line."""
    return len(held(health))


def _mark(bad: object) -> tuple[str, str]:
    return ("✗", "$error") if bad else ("✓", "$success")


def summary(health: dict, release: dict | None, *, answered: bool = True) -> Content:
    """The Server's head (spec §7.3): how it is and what it runs; whether its
    config loaded, its index, storage and waiting add-on updates; then one
    line for each reason it is degraded, labelled as `kraft admin health`
    labels them. Never markup: every reason is the server's own text."""
    status = str(health.get("status") or "?") if answered else "no answer"
    dot = {"ok": "$success", "degraded": "$warning"}.get(status, "$kraft-muted")
    version = health.get("version") or "-"
    notes = []
    if (installed := health.get("installed")) and installed != version:
        notes.append(f"{installed} installed, R finishes it")
    if release and release.get("behind") and release.get("latest"):
        notes.append(f"{release['latest'].removeprefix('v')} available")
    head = [f"v{version}" + (f" ({'; '.join(notes)})" if notes else "")]
    if health.get("uptime_s") is not None:
        head.append(f"up {words.elapsed(health['uptime_s'])}")
    if health.get("pid") is not None:
        head.append(f"pid {health['pid']}")
    if health.get("bind"):
        head.append(f"{health['bind']}:{health.get('port')}")
    index = health.get("index") or {}
    storage = health.get("storage")
    stuck = bool(storage) and storage.get("state") != "ok"
    used = (
        f"{render.human_size(storage['used_bytes'])} of {render.human_size(storage['limit_bytes'])}"
        + (f" · {storage['state'].replace('_', ' ')}" if stuck else "")
        if storage
        else "not limited"
    )
    waiting = updates_waiting(health)
    problems = [
        *(
            f"invalid template: {name}: {why}"
            for name, why in (health.get("invalid_templates") or {}).items()
        ),
        *(
            f"invalid {name}: {health[f'invalid_{name}']}"
            for name in ("policy", "intake")
            if health.get(f"invalid_{name}")
        ),
        *(f"index error: {error}" for error in index.get("errors") or []),
    ]
    return Content.assemble(
        (f"● {status}", dot),
        f" · {' · '.join(head)}\n",
        ("Config  ", "$kraft-muted"),
        "templates ",
        _mark(health.get("invalid_templates")),
        "  policy ",
        _mark(health.get("invalid_policy")),
        "  intake ",
        _mark(health.get("invalid_intake")),
        "  forges ",
        _mark(health.get("invalid_forges")),
        ("  ·  Index  ", "$kraft-muted"),
        f"{(index.get('embeddings') or {}).get('state') or '-'}\n",
        ("Storage  ", "$kraft-muted"),
        (used, "$warning") if stuck else used,
        ("  ·  Add-ons  ", "$kraft-muted"),
        (f"{waiting} update{'' if waiting == 1 else 's'} waiting", "$warning")
        if waiting
        else ("none waiting", "$kraft-muted"),
        *((f"\n  {line}", "$error") for line in problems),
    )


class Server(View):
    label = "Server"
    BINDINGS: ClassVar[list[BindingType]] = [
        # The view keeps the focus, so its letters act; the log scrolls by keys.
        Binding("down,j", "scroll('down')", "down", show=False),
        Binding("up,k", "scroll('up')", "up", show=False),
        Binding("pagedown", "scroll('page_down')", "page down", show=False),
        Binding("pageup", "scroll('page_up')", "page up", show=False),
        Binding("home", "scroll('home')", "top", show=False),
        Binding("end", "scroll('end')", "follow", show=False),
        Binding("d", "doctor", "doctor"),
        Binding("l", "reload", "reload"),
        # Both names: kitty-protocol terminals report Shift+R as `shift+r` (spec §6).
        Binding("R,shift+r", "restart", "restart"),
        Binding("U,shift+u", "update", "update"),
        Binding("f", "filter", "filter"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: `/health`'s last answer, kept on screen while the server does not
        #: answer (spec §8); `answered` says whether the last poll did.
        self.health: dict | None = None
        self.answered = False
        #: `server.log`'s lines since the view was last shown, for the filter.
        self.lines: collections.deque[str] = collections.deque(maxlen=MAX_LINES)
        self.filter_text = ""

    def compose(self) -> ComposeResult:
        yield Static(id="server-summary")
        yield Rule(id="server-rule")
        yield Input(placeholder="filter server.log · Esc clears", id="server-filter")
        # Focus stays with the view, which scrolls the log by its keys.
        log = RichLog(id="server-log", wrap=True, min_width=1, max_lines=MAX_LINES)
        log.can_focus = False
        yield log

    def on_mount(self) -> None:
        self.add_class("panel")
        self.border_title = "Server"
        self.query_one("#server-filter").display = False
        # The rule says whether the log follows: whenever it scrolls, by key, wheel or write.
        self.watch(self.query_one("#server-log", RichLog), "scroll_y", self._draw_rule, init=False)

    def health_changed(self, health: dict | None) -> None:
        self.answered = health is not None
        self.health = health or self.health
        if self.display:
            self.draw()

    def draw(self) -> None:
        said = (
            summary(self.health, self.app.release, answered=self.answered)
            if self.health is not None
            else Content.styled("Waiting for the server to say how it is.", "$kraft-muted")
        )
        self.query_one("#server-summary", Static).update(said)

    def on_show(self) -> None:
        self.draw()
        self._draw_rule()
        if self.app.release is None:  # the read at start failed: the feed may answer now
            self.run_worker(self._release, group="release", exclusive=True)
        # After the refresh: a log just shown has no width until it is laid out,
        # and would wrap what it is given then at one cell.
        self.call_after_refresh(self.run_worker, self._follow, group="tail", exclusive=True)

    def on_hide(self) -> None:
        """Another view is shown (this one stays mounted): read nothing."""
        for group in ("tail", "release"):
            self.workers.cancel_group(self, group)

    async def _release(self) -> None:
        await self.app.check_update()

    async def _follow(self) -> None:
        """`server.log`'s last lines, then each one as the server writes it.
        Shown again, it starts over: the last lines hold what came meanwhile."""
        if not self.display:  # hidden again before the refresh came
            return
        log = self.query_one("#server-log", RichLog)
        log.clear()
        self.lines.clear()
        async for line in tail.follow(tail.server_log(), backlog=BACKLOG):
            self.lines.append(line)
            if self._shows(line):
                # To the end only from the end: a reader scrolled up stays there.
                drawn = log_line(line, self.app.theme_variables)
                log.write(drawn, scroll_end=log.is_vertical_scroll_end)

    def _shows(self, line: str) -> bool:
        return self.filter_text.strip().lower() in line.lower()

    def _draw_rule(self) -> None:
        log = self.query_one("#server-log", RichLog)
        state = FOLLOWING if log.is_vertical_scroll_end else HELD
        text = self.filter_text.strip()
        shown = f" · filter: {text}" if text else ""
        self.query_one("#server-rule", Rule).show(
            Content.styled(f"server.log{shown} · {state}", "$kraft-muted")
        )

    def _redraw(self) -> None:
        """The kept lines again, through the filter: followed from the end, or
        held where a reader scrolled up to."""
        log = self.query_one("#server-log", RichLog)
        held = not log.is_vertical_scroll_end
        log.clear()
        for line in self.lines:
            if self._shows(line):
                log.write(log_line(line, self.app.theme_variables), scroll_end=not held)
        self._draw_rule()

    def back(self) -> bool:
        box = self.query_one("#server-filter", Input)
        if not (self.filter_text or box.display):
            return False
        self.filter_text, box.value, box.display = "", "", False
        self.focus()  # clearing the box redraws, through _filtered
        return True

    def action_filter(self) -> None:
        box = self.query_one("#server-filter", Input)
        log = self.query_one("#server-log", RichLog)
        following = log.is_vertical_scroll_end
        box.display = True
        box.focus()
        if following:  # the box takes a row from the log: keep a following reader at its end
            self.call_after_refresh(log.scroll_end, animate=False)

    @on(Input.Changed, "#server-filter")
    def _filtered(self, event: Input.Changed) -> None:
        self.filter_text = event.value
        self._redraw()

    @on(Input.Submitted, "#server-filter")
    def _filter_done(self) -> None:
        self.focus()

    def action_scroll(self, how: str) -> None:
        getattr(self.query_one("#server-log", RichLog), f"scroll_{how}")(animate=False)

    def action_doctor(self) -> None:
        self.run_worker(partial(self.app.run_command, ["admin", "doctor"]), group="act")

    def action_reload(self) -> None:
        self.run_worker(self._reload, group="act")

    async def _reload(self) -> None:
        try:
            result = await client.reload_templates()
        except client.ClientError as exc:
            self.app.notify_refusal(exc)
            return
        severity = "error" if answers.reload_refused(result) else "information"
        # markup=False: a refused file's reason is the server's words.
        self.app.notify(answers.reload_summary(result), severity=severity, markup=False)

    def action_restart(self) -> None:
        self.run_worker(partial(self.app.run_asking, ["admin", "restart"]), group="act")

    def action_update(self) -> None:
        # --restart, so the new version serves; the confirm lists the running items
        # that ends. Never -y: its own refusals still show (spec §4.7).
        argv = ["admin", "update", "--restart"]
        self.run_worker(partial(self.app.run_asking, argv), group="act")
