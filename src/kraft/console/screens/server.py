"""The Server (spec §7.3): how the server is, from `/health` and the release
feed, with `server.log` followed under it. `l` reloads the config here;
doctor, restart and update run their `kraft admin` verbs in the output
panel, restart and update after the console's own confirm."""

from __future__ import annotations

import collections
from functools import partial
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.content import Content
from textual.widgets import Input, RichLog, Static

from kraft import answers, client, render
from kraft.console import words
from kraft.console.screens import View

#: Lines of `server.log` kept, for the pane and for the filter to search.
MAX_LINES = 2000
#: What the rule over the log says while new lines move it to its end.
FOLLOWING = "following · f filter"
#: And while the reader has scrolled up, which holds it where it is.
HELD = "paused · End follows · f filter"


def updates_waiting(health: dict | None) -> int:
    """How many add-on updates wait for a person: `/health`'s held
    `addon_updates`. The Add-ons tab's count and the Server's line."""
    return sum(
        entry.get("outcome") == "held"
        for entry in ((health or {}).get("addon_updates") or {}).values()
    )


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
        ("Server ", "bold"),
        (f"● {status}", dot),
        f" · {' · '.join(head)}\n",
        ("Config  ", "$kraft-muted"),
        "templates ",
        _mark(health.get("invalid_templates")),
        "  policy ",
        _mark(health.get("invalid_policy")),
        "  intake ",
        _mark(health.get("invalid_intake")),
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
        Binding("d", "doctor", "doctor"),
        Binding("l", "reload", "reload"),
        # Both names: kitty-protocol terminals report Shift+R as `shift+r` (spec §6).
        Binding("R,shift+r", "restart", "restart"),
        Binding("U,shift+u", "update", "update"),
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
        yield Static(id="server-rule")
        yield Input(placeholder="filter server.log · Esc clears", id="server-filter")
        # Focus stays with the view, which scrolls the log by its keys.
        log = RichLog(id="server-log", wrap=True, min_width=1, max_lines=MAX_LINES)
        log.can_focus = False
        yield log

    def on_mount(self) -> None:
        self.query_one("#server-filter").display = False

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
        if self.app.release is None:  # the read at start failed: the feed may answer now
            self.run_worker(self._release, group="release", exclusive=True)

    def on_hide(self) -> None:
        """Another view is shown (this one stays mounted): read nothing."""
        for group in ("tail", "release"):
            self.workers.cancel_group(self, group)

    async def _release(self) -> None:
        await self.app.check_update()
        if self.display:
            self.draw()

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
