"""The status line under the prompt: how much runs on its own, today's
spend against the daily cap, and which agents' CLIs the server can find.

Each part is the server's own answer, read on start and on every reconnect;
a part whose read fails is left out rather than guessed."""

from __future__ import annotations

from textual.containers import Horizontal
from textual.content import Content
from textual.widgets import Static

from kraft import client
from kraft.console import pulse, words
from kraft.console.live import Link

#: Agents named before the rest are counted as `+N`.
AGENTS_SHOWN = 3
#: The status line's own key, where the footer that names it is hidden.
KEYS = "? keys"


def running_part(rows: list[dict]) -> str:
    """How many items run on their own, in the Board's own words."""
    n = sum(words.group_of(row) == "running" for row in rows)
    return f"{n} running on their own" if n else "nothing running"


def budget_part(budget: dict | None) -> str | None:
    """`$4.10 of $50.00 today`, or `$4.10 today` with no daily cap."""
    if not budget or budget.get("spent_usd") is None:
        return None
    cap = budget.get("cap_usd")
    of = f" of {words.usd(cap)}" if cap is not None else ""
    return f"{words.usd(budget['spent_usd'])}{of} today"


def agents_part(harnesses: list[dict] | None) -> Content | None:
    """`claude ● codex ○ +5`: each provider's CLI found (`●`) or missing
    (`○`), the found ones first; the mark's shape says it, the colour repeats it."""
    if not harnesses:
        return None
    ordered = sorted(harnesses, key=lambda h: not h.get("executable_found"))
    parts: list[str | tuple[str, str]] = []
    for h in ordered[:AGENTS_SHOWN]:
        found = bool(h.get("executable_found"))
        parts += [f"{h.get('label') or h.get('id')} ", ("●", "$success") if found else "○", " "]
    if (more := len(ordered) - AGENTS_SHOWN) > 0:
        parts.append(f"+{more}")
    return Content.assemble(*parts).rstrip()


class StatusLine(Horizontal):
    """One line: the spinner while anything runs, then the parts that fit,
    the agents giving way first, then the spend."""

    def __init__(self, **kwargs) -> None:
        super().__init__(id="status", **kwargs)
        self.rows: list[dict] = []
        self.budget: dict | None = None
        self.harnesses: list[dict] | None = None

    def compose(self):
        yield pulse.Spinner(id="status-spin")
        yield Static(id="status-text")

    def show_rows(self, rows: list[dict]) -> None:
        """The board in scope, read again."""
        self.rows = rows
        self.draw()

    async def read(self) -> None:
        """Both of the server's parts, each on its own: one failing leaves
        only itself out."""
        await self.read_budget()
        try:
            self.harnesses = await client.harness_status()
        except client.ClientError:
            self.harnesses = None
        self.draw()

    async def read_budget(self) -> None:
        try:
            self.budget = await client.budget_today()
        except client.ClientError:
            self.budget = None
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    def draw(self) -> None:
        # With no server, what it last said is not said as now.
        up = getattr(self.app, "link", None) is not Link.DOWN
        spin = self.query_one("#status-spin", pulse.Spinner)
        # Only for work that is happening: a queued item counts, but does not spin.
        spin.display = up and any(pulse.spins(row) for row in self.rows)
        spin.set(spin.display)
        narrow = self.screen.has_class("-narrow")
        parts: list[str | Content] = [running_part(self.rows)] if up else []
        for part in (budget_part(self.budget), agents_part(self.harnesses)) if up else ():
            if part:
                parts.append(part)
        tail = [KEYS] if narrow else []
        # The line's padding, the spinner and its margins, the text's own padding.
        room = self.size.width - 2 - (3 if spin.display else 0) - 1
        while len(parts) > 1 and self._joined([*parts, *tail]).cell_length > room:
            parts.pop()
        line = self._joined([*parts, *tail])
        self.query_one("#status-text", Static).update(line)

    @staticmethod
    def _joined(parts: list[str | Content]) -> Content:
        out: list[str | Content | tuple[str, str]] = []
        for part in parts:
            out += [(" · ", "$kraft-faint"), part] if out else [part]
        return Content.assemble(*out).stylize_before("$kraft-muted")
