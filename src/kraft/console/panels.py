"""The workbench's frames: a titled separator inside a panel, and the
Board's list drawn as one rounded panel whose groups are such separators.

A window under `COMPACT_ROWS` rows keeps every row for content: frames drop
their top and bottom edges, so a title becomes a rule."""

from __future__ import annotations

from rich.segment import Segment
from rich.style import Style
from textual.content import Content
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import OptionList

from kraft.console import pulse

#: Below this many rows the panels give up their top and bottom edges.
COMPACT_ROWS = 30
#: An option that is the panel's frame, not an item: a group's heading, and
#: (`END`) the panel's bottom edge.
GROUP = "group:"
END = "group:end"
#: The cells the frame takes on each side of a row: `│ ` and ` │`.
SIDE = 2


def rule(title: Content, width: int, *, left: str = "─", right: str = "─") -> Content:
    """`├─ Running 2 ─────┤`: `title` on a line `width` cells long, in the
    frame's colour; `left` and `right` are its ends."""
    lead = f"{left}─ " if title.cell_length else left
    fill = max(width - len(lead) - title.cell_length - 1 - len(right), 0)
    tail = (" " if title.cell_length else "─") + "─" * fill + right
    return Content.assemble((lead, "$kraft-frame"), title, (tail, "$kraft-frame"))


class Rule(Widget):
    """A titled separator between a panel's sections, as wide as it is laid out."""

    DEFAULT_CSS = "Rule { height: 1; }"

    def __init__(self, title: Content | str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.title = Content(title) if isinstance(title, str) else title

    def show(self, title: Content | str) -> None:
        self.title = Content(title) if isinstance(title, str) else title
        self.refresh()

    def render(self) -> Content:
        return rule(self.title, self.size.width)


def _put(strip: Strip, x: int, text: str) -> Strip:
    """`strip` with `text` over its cells from `x`, in the style of the cell at `x`."""
    width = len(text)
    if x < 0 or x + width > strip.cell_length:
        return strip
    style = next((seg.style for seg in strip.crop(x, x + 1)), None)
    return Strip.join(
        [strip.crop(0, x), Strip([Segment(text, style)], width), strip.crop(x + width)]
    )


class Rows(OptionList):
    """The Board's list in its panel. Each line sits between the panel's
    sides, a heading is a titled separator, `END` is the bottom edge, and the
    right side carries the scroll position. Rows whose work is happening have
    their bar cell drawn over a beat at a time (`live`), so a spinner redraws
    its own cell, never the list."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: Drawn in a panel; off in a compact window, where titles are rules.
        self.framed = True
        #: The option index of each row that spins.
        self.live: set[int] = set()

    def on_mount(self) -> None:
        self.app.pulse.add(self.beat)

    @property
    def inner(self) -> int:
        """The width a row's own text is laid out in."""
        width = self.scrollable_content_region.width
        return width - 2 * SIDE if self.framed else width

    def beat(self, tick: int) -> bool:
        if not self.live or not pulse.shown(self):
            return False
        top, height = self.scroll_offset.y, self.size.height
        x = SIDE if self.framed else 0
        for index in self.live:
            y = self._index_to_line.get(index, -1) - top
            if 0 <= y < height:
                self.refresh(Region(x, y, 1, 1))
        return True

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        line = self.scroll_offset.y + y
        lines = self._lines
        if line >= len(lines):
            return strip
        index, offset = lines[line]
        option = self.options[index]
        if offset == 0 and index in self.live:
            # The spinner's frame on the bar cell.
            strip = _put(strip, 0, pulse.frame(self.app.pulse.tick))
        if not self.framed:
            if option.id and option.id.startswith(GROUP):
                return self._heading(strip, option.prompt, "─", "─")
            return strip
        if option.id == END:
            return self._edge("╰", "╯")
        if option.id and option.id.startswith(GROUP) and not option.prompt.cell_length:
            return self._edge("╭", "╮")
        if option.id and option.id.startswith(GROUP):
            return self._heading(strip, option.prompt, *(("╭", "╮") if line == 0 else ("├", "┤")))
        frame = self._frame_style()
        right = "┃" if self._thumb(y) else "│"
        return Strip.join(
            [
                Strip([Segment("│ ", frame)], SIDE),
                strip.crop(0, self.inner),
                Strip([Segment(" " + right, frame)], SIDE),
            ]
        )

    def _frame_style(self) -> Style:
        return self.rich_style + Style(color=self.app.theme_variables["kraft-frame"])

    def _heading(self, strip: Strip, title: Content, left: str, right: str) -> Strip:
        width = self.scrollable_content_region.width
        size = title.cell_length
        lead = f"{left}─ "
        fill = max(width - len(lead) - size - 1 - len(right), 0)
        frame = self._frame_style()
        return Strip.join(
            [
                Strip([Segment(lead, frame)], len(lead)),
                strip.crop(0, size),
                Strip([Segment(" " + "─" * fill + right, frame)], fill + 1 + len(right)),
            ]
        )

    def _edge(self, left: str, right: str) -> Strip:
        width = self.scrollable_content_region.width
        return Strip([Segment(left + "─" * (width - 2) + right, self._frame_style())], width)

    def _thumb(self, y: int) -> bool:
        """Whether the right side at viewport row `y` is the scroll thumb."""
        total, view = self.virtual_size.height, self.size.height
        if total <= view or view <= 0:
            return False
        size = max(1, view * view // total)
        start = round(self.scroll_offset.y * (view - size) / (total - view))
        return start <= y < start + size
