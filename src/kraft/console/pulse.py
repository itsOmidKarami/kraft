"""Life where work runs: Claude Code's spinner beside running work, the time
it has been at its node, and the one clock that moves them all.

Motion is only ever for work that is happening: an item whose agent (or an
escalation turn) is at work. Nothing else spins, and nothing is staged."""

from __future__ import annotations

import time
from collections.abc import Callable

from textual.app import App
from textual.containers import Horizontal
from textual.content import Content
from textual.dom import DOMNode
from textual.timer import Timer
from textual.widget import Widget

from kraft.console import words

#: Claude Code's spinner, one frame a beat.
FRAMES = "·✢✳✶✻✽"
#: Beats a second: the spinner's pace, and how often a clock may move.
FPS = 8
#: The display statuses whose work is happening now.
SPINS = frozenset({"running", "escalated"})
#: Past this many hours a clock reads in days and hours (`words.elapsed`):
#: `h:mm:ss` would outgrow the age column.
CLOCK_HOURS = 100

#: `beat(tick)` draws its frame and says whether it still has running work
#: on screen; the clock stops once none does.
Beat = Callable[[int], bool]


def spins(row: dict | None) -> bool:
    """Whether `row`'s work is happening now: it spins and its clock ticks."""
    return (row or {}).get("display_status") in SPINS


def frame(tick: int) -> str:
    return FRAMES[tick % len(FRAMES)]


def since(row: dict) -> float | None:
    """When the row's current run began, as the list gives it: `updated_at`,
    which a node's start, a resume and a retry each set. None for no time."""
    return words._seconds(row.get("updated_at"))


def clock(seconds: float) -> str:
    """A running row's age column: `0:48`, `4:12`, `1:04:12`."""
    s = max(0, int(seconds))
    hours, rest = divmod(s, 3600)
    if hours >= CLOCK_HOURS:
        return words.elapsed(s)
    minutes, s = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{s:02d}" if hours else f"{minutes}:{s:02d}"


def took(seconds: float) -> str:
    """The working line's time: `48s`, `4m 12s`, `1h 4m`."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    minutes, s = divmod(s, 60)
    if minutes < 60:
        return f"{minutes}m {s}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def shown(node: DOMNode) -> bool:
    """Whether `node` is drawn: it and everything it sits in are displayed. A
    view the switcher hides keeps its children's own `display`."""
    return node.is_attached and all(n.display for n in node.ancestors_with_self)


class Pulse:
    """The app's one clock for spinners and running clocks: it ticks at `FPS`
    while some beat has running work on screen, and stops when none has.
    Whatever may have started showing running work calls `wake()`."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.tick = 0
        self.beats: list[Beat] = []
        self._timer: Timer | None = None

    def add(self, beat: Beat) -> None:
        self.beats.append(beat)

    def wake(self) -> None:
        if self._timer is None:
            # Made on the first wake: a timer needs the app's loop running.
            self._timer = self.app.set_interval(1 / FPS, self.step)
        else:
            self._timer.resume()

    def step(self) -> None:
        self.tick += 1
        # Every beat draws, so none is starved by one before it saying yes.
        alive = [beat(self.tick) for beat in self.beats]
        if not any(alive) and self._timer is not None:
            self._timer.pause()


class Spinner(Widget):
    """One cell, in the running colour: a frame a beat while `on`, blank off.
    A beat repaints this cell alone: no layout (a `Static.update` lays the
    screen out again)."""

    DEFAULT_CSS = "Spinner { width: 1; height: 1; color: $kraft-info; }"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.on = False
        self.glyph = ""

    def render(self) -> str:
        return self.glyph

    def update(self, glyph: str) -> None:
        self.glyph = glyph
        self.refresh()

    def on_mount(self) -> None:
        self.app.pulse.add(self.beat)

    def set(self, on: bool) -> None:
        if on != self.on:
            self.on = on
            self.update(frame(self.app.pulse.tick) if on else "")
        if on:
            self.app.pulse.wake()

    def beat(self, tick: int) -> bool:
        if not (self.on and shown(self)):
            return False
        self.update(frame(tick))
        return True


class Words(Widget):
    """One line that changes in place: drawn again, never laid out again."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.said = Content("")

    def render(self) -> Content:
        return self.said

    def update(self, said: Content) -> None:
        self.said = said
        self.refresh()


def working_words(item: dict, now: float) -> str:
    """`implement · task 3 of 5 · 4m 12s · $0.42`: where a running item is,
    how long it has been at it, and what it has spent (`budget_cap`)."""
    parts = [words.reason_tail(item, now)]
    if (start := since(item)) is not None:
        parts.append(took(now - start))
    spent = (item.get("budget_cap") or {}).get("spent_usd")
    if spent is not None:
        parts.append(words.usd(spent))
    return " · ".join(part for part in parts if part)


class Working(Horizontal):
    """A running item's working line, in the peek and on the Item: the
    spinner, then `working_words`, its time moving each second."""

    DEFAULT_CSS = """
    Working { height: 1; width: 1fr; }
    Working > .working-words { width: 1fr; height: 1; padding-left: 1; text-wrap: nowrap;
        text-overflow: ellipsis; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.item: dict | None = None
        self._second: int | None = None

    def compose(self):
        yield Spinner()
        yield Words(classes="working-words")

    def on_mount(self) -> None:
        self.display = False
        self.app.pulse.add(self.beat)

    def show_item(self, item: dict | None) -> None:
        """Follow `item`; hidden for one whose work is not happening."""
        self.item = item if spins(item) else None
        self.display = self.item is not None
        self.query_one(Spinner).set(self.item is not None)
        self._second = None
        self._draw(int(time.time()))

    def beat(self, tick: int) -> bool:
        if self.item is None or not shown(self):
            return False
        if (now := int(time.time())) != self._second:
            self._draw(now)
        return True

    def _draw(self, now: int) -> None:
        self._second = now
        said = working_words(self.item, now) if self.item else ""
        self.query_one(".working-words", Words).update(Content(said))
