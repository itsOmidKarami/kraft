"""The Board (spec §7.1), driven headless over board rows faked at
`client.board_rows`, and once over the real API."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from support.harness import connected_repo
from textual.widgets import OptionList

from kraft import client
from kraft.console.screens import board as board_view

CHAIN = {
    "template_id": "default",
    "nodes": [
        {"id": "spec", "kind": "exec"},
        {"id": "spec_approval", "kind": "gate"},
        {"id": "plan", "kind": "exec"},
        {"id": "implement", "kind": "exec"},
    ],
}


#: The Board's clock in these tests: ages read the same however late a test runs.
NOW = float(int(time.time()))  # whole seconds: an ISO round trip keeps them exact


def _iso(minutes_ago: float) -> str:
    return datetime.fromtimestamp(NOW - minutes_ago * 60, UTC).isoformat()


def _row(wid: str, status: str, minutes: float = 1, **fields) -> dict:
    """A board row as `GET /work-items` sends it, cut to what the Board reads."""
    return {
        "id": wid,
        "title": f"item {wid}",
        "repo": "/r",
        "display_status": status,
        "current_node_id": "implement",
        "chain_template": None,
        "chain_definition": CHAIN,
        "updated_at": _iso(minutes),
        "actions": [],
        **fields,
    }


GATE = _row(
    "a91c03fe",
    "needs_you",
    12,
    title="Board critique fixes [/]",
    bead_id="Kraft-x1y2",
    current_node_id="spec_approval",
    pending_gate="spec_approval",
    stop={"kind": "gate", "node": "spec_approval", "reason": None},
    actions=["approve", "reject"],
)
FAILED = _row(
    "m03d77b1",
    "failed",
    60,
    current_node_id="verification",
    stop={"kind": "failed", "node": "verification", "reason": "tests failed: 3 of 120"},
    actions=["retry"],
)
RUNNING = _row(
    "k7f2e9a0", "running", 4, title="Docs per major", chain_template="docs", actions=["pause"]
)
ELSEWHERE = _row("e1e1e1e1", "running", 2, repo="/src/other")
FILED = _row("n0n0n0n0", "paused", 30, current_node_id=None, actions=["resume", "steer"])
DONE = _row("d0d0d0d0", "done", 600, mr_ref={"number": 42, "url": "https://example.com/mr/42"})
ARCHIVED = _row("z9z9z9z9", "archived", 900)
BOARD = [RUNNING, DONE, GATE, ARCHIVED, ELSEWHERE, FILED, FAILED]


@pytest.fixture
def board(console, monkeypatch):
    """`board(rows, repo)`: the console over these rows, opened in `repo`."""

    def serve(rows: list[dict], repo: str | None = "/r"):
        async def board_rows(**kwargs):
            return rows, 0

        async def resolve_repo(cwd=None):
            return repo

        monkeypatch.setattr(client, "board_rows", board_rows)
        monkeypatch.setattr(client, "resolve_repo", resolve_repo)
        monkeypatch.setattr(board_view, "time", SimpleNamespace(time=lambda: NOW))
        return console

    return serve


def _lines(app) -> list[str]:
    return [option.prompt.plain for option in app.query_one("#rows", OptionList).options]


def _ids(app) -> list[str]:
    options = app.query_one("#rows", OptionList).options
    return [o.id for o in options if o.id and not o.id.startswith("group:")]


async def _settled(console, app, pilot) -> None:
    """Until the Board is drawn for the repo the console was opened in."""
    await console.until(pilot, lambda: app._scoped and _lines(app))


@pytest.mark.parametrize(
    ("size", "repo", "lines"),
    [
        (
            (80, 24),
            "/r",
            [
                "NEEDS YOU 2",
                "▌a91c03fe  Board critique fixe…  approve spec                              12m",
                "▌m03d77b1  item m03d77b1         failed at verificat…                       1h",
                "RUNNING 1",
                "▌k7f2e9a0  Docs per major        implement             docs                 4m",
                "NOT STARTED 1",
                "▌n0n0n0n0  item n0n0n0n0         created paused                            30m",
                "DONE 1 ›",
            ],
        ),
        (
            (80, 24),
            None,
            [
                "NEEDS YOU 2",
                "▌a91c03fe  Board critique fixe…  approve spec          r                   12m",
                "▌m03d77b1  item m03d77b1         failed at verificat…  r                    1h",
                "RUNNING 2",
                "▌e1e1e1e1  item e1e1e1e1         implement             other                2m",
                "▌k7f2e9a0  Docs per major        implement             r · docs             4m",
                "NOT STARTED 1",
                "▌n0n0n0n0  item n0n0n0n0         created paused        r                   30m",
                "DONE 1 ›",
            ],
        ),
        (
            (70, 24),
            "/r",
            [
                "NEEDS YOU 2",
                "▌a91c03fe  Board critique fixes [/]     approve spec",
                "▌m03d77b1  item m03d77b1                failed at verification",
                "RUNNING 1",
                "▌k7f2e9a0  Docs per major               implement",
                "NOT STARTED 1",
                "▌n0n0n0n0  item n0n0n0n0                created paused",
                "DONE 1 ›",
            ],
        ),
    ],
    ids=["in-a-repo", "all-repos", "narrow"],
)
def test_the_board_draws_the_webs_groups_in_a_fixed_grid(board, size, repo, lines):
    """Needs you first, sorted as the web sorts; one line a row, in columns
    that a long title or tail cuts with "…" rather than pushes; the repo only
    with every repo shown, a chain only when it is not the default; archived
    items never; Done folded to its count. Narrow drops the chain and age."""

    async def scenario(app, pilot):
        await _settled(board(BOARD, repo), app, pilot)
        await pilot.resize_terminal(*size)
        await pilot.pause()
        return [line.rstrip() for line in _lines(app)]

    assert board(BOARD, repo).run(scenario) == lines


@pytest.mark.parametrize(
    ("row", "bar"),
    [
        (GATE, "$warning"),
        (_row("p", "paused"), "$warning"),
        (FAILED, "$error"),
        (RUNNING, "$kraft-info"),
        (_row("q", "queued", current_node_id=None), "$kraft-info"),
        (FILED, "$kraft-faint"),
        (DONE, "$success"),
        (_row("c", "cancelled"), "$kraft-faint"),
    ],
    ids=["gate", "paused-mid-chain", "failed", "running", "queued", "filed", "done", "cancelled"],
)
def test_amber_marks_only_what_needs_you(row, bar):
    """Spec §5.2: each colour one job. A failure is bad, not amber; what
    waits on a person and nothing else is amber."""
    assert board_view.bar_of(row) == bar


@pytest.mark.parametrize(
    ("rows", "keys", "lines"),
    [
        ([], [], ["Nothing filed yet. /item create files one."]),
        (
            [RUNNING],
            [],
            ["NEEDS YOU 0", "Nothing needs you", "RUNNING 1", "k7f2e9a0"]
            + ["NOT STARTED 0", "DONE 0"],
        ),
        ([RUNNING], ["f", *"zzz"], ["nothing here for this filter"]),
    ],
    ids=["nothing-filed", "nothing-needs-you", "nothing-matches"],
)
def test_an_empty_board_says_why(board, rows, keys, lines):
    """Nothing filed says how to file; nothing needing you is said under
    Needs you; a filter that matches nothing says so."""

    async def scenario(app, pilot):
        await _settled(board(rows), app, pilot)
        await pilot.press(*keys)
        await pilot.pause()
        return [line.split("  ")[0].lstrip("▌") for line in _lines(app)]

    assert board(rows).run(scenario) == lines


@pytest.mark.parametrize(
    ("keys", "shown", "where"),
    [
        ([], ["a91c03fe", "m03d77b1", "k7f2e9a0", "n0n0n0n0"], "/r"),
        (["s"], ["a91c03fe", "m03d77b1", "e1e1e1e1", "k7f2e9a0", "n0n0n0n0"], "all repos"),
        (["s", "s"], ["a91c03fe", "m03d77b1", "k7f2e9a0", "n0n0n0n0"], "/r"),
        (["f", *"critique"], ["a91c03fe"], "/r"),
        (["f", *"K7F2", "enter"], ["k7f2e9a0"], "/r"),
        (["f", *"critique", "escape"], ["a91c03fe", "m03d77b1", "k7f2e9a0", "n0n0n0n0"], "/r"),
        (["end", "enter"], ["a91c03fe", "m03d77b1", "k7f2e9a0", "n0n0n0n0", "d0d0d0d0"], "/r"),
    ],
    ids=[
        "this-repo",
        "s-all-repos",
        "s-back",
        "filter",
        "filter-by-id",
        "esc-clears",
        "done-opens",
    ],
)
def test_scope_filter_and_done_change_what_the_board_shows(board, keys, shown, where):
    """`s` widens to every repo and back, and the top line says which; `f`
    filters by title or id, Esc clears it; Enter on Done's heading opens it."""

    async def scenario(app, pilot):
        await _settled(board(BOARD), app, pilot)
        await pilot.press(*keys)
        await pilot.pause()
        return _ids(app), str(app.query_one("#where").render()).split(" · ")[0]

    assert board(BOARD).run(scenario) == (shown, where)


def test_the_cursor_stays_on_its_item_when_a_refresh_moves_it(board):
    """Spec §5.4: a live update re-sorts the board; the cursor follows its item."""
    rows = [RUNNING, ELSEWHERE | {"repo": "/r"}]

    async def scenario(app, pilot):
        await _settled(board(rows), app, pilot)
        before = app.view.selected["id"]
        app.view.rows_changed([RUNNING | {"updated_at": _iso(0)}, rows[1]], [{"seq": 1}])
        await pilot.pause()
        return before, app.view.selected["id"], _ids(app)

    assert board(rows).run(scenario) == ("e1e1e1e1", "e1e1e1e1", ["k7f2e9a0", "e1e1e1e1"])


def test_a_title_with_markup_in_it_draws_as_typed(console, stream, tmp_path):
    """Spec §9, against the real API: an item filed with `[/]` in its title
    is drawn as typed; markup would read it as a tag and end the console."""
    repo = connected_repo(tmp_path)

    async def scenario(app, pilot):
        created = await client.create_work_item("Fix the [/] parser", repo=str(repo))
        stream.send({"seq": 1_000, "type": "work_item_created", "work_item_id": created["id"]})
        await console.until(pilot, lambda: _ids(app))
        return [line for line in _lines(app) if created["id"][:8] in line]

    [row] = console.run(scenario)
    assert "Fix the [/] parser" in row


LONG = [_row(f"{n:08x}", "running", n + 1) for n in range(40)]


def test_a_board_taller_than_the_window_still_fits_its_columns(board, monkeypatch):
    """The scrollbar's room is kept from the first draw, so rows sized before
    it appeared are not clipped by it (the age would read `12` for `12m`)."""
    sized = []
    real = board_view.row_line

    def spy(row, width, *args, **kwargs):
        sized.append(width)
        return real(row, width, *args, **kwargs)

    monkeypatch.setattr(board_view, "row_line", spy)

    async def scenario(app, pilot):
        await _settled(board(LONG), app, pilot)
        return sized[0], app.query_one("#rows", OptionList).scrollable_content_region.width

    first, final = board(LONG).run(scenario)
    assert first == final


def test_a_refresh_leaves_the_reader_where_they_scrolled(board):
    """A live event redraws the list; the viewport stays where it was."""

    async def scenario(app, pilot):
        await _settled(board(LONG), app, pilot)
        await pilot.press("end", "pageup", "pageup")
        await pilot.pause()
        before = app.query_one("#rows", OptionList).scroll_y
        app.view.rows_changed(list(LONG), [{"seq": 1}])
        await pilot.pause()
        return before, app.query_one("#rows", OptionList).scroll_y

    before, after = board(LONG).run(scenario)
    assert before > 0
    assert after == before


def test_the_tail_is_amber_only_where_it_needs_you(board):
    """Spec §5.2: a Needs-you tail takes its bar's colour, the rest are muted;
    "Nothing needs you" is the ok colour."""
    now = NOW

    def style_of(row, text):
        line = board_view.row_line(row, 80, now, everywhere=False, narrow=False)
        [span] = [s for s in line.spans if line.plain[s.start : s.end].strip() == text]
        return str(span.style)

    assert style_of(GATE, "approve spec") == "$warning"
    assert style_of(RUNNING, "implement") == "$kraft-muted"
    assert style_of(FAILED, "failed at verificati…") == "$error"

    async def scenario(app, pilot):
        await _settled(board([RUNNING]), app, pilot)
        [option] = [o for o in app.query_one("#rows", OptionList).options if o.disabled]
        [span] = option.prompt.spans
        return option.prompt.plain, str(span.style)

    assert board([RUNNING]).run(scenario) == ("Nothing needs you", "$success")


def test_enter_is_offered_only_on_done_s_heading(board):
    """Enter does nothing on an item in this PR, so the footer must not offer it."""

    async def scenario(app, pilot):
        await _settled(board(BOARD), app, pilot)
        on_item = app.view.check_action("open", ())
        await pilot.press("end")
        return on_item, app.view.check_action("open", ())

    assert board(BOARD).run(scenario) == (False, True)
