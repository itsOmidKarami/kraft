"""The Board (spec §7.1), driven headless over board rows faked at
`client.board_rows`, and once over the real API."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from support.harness import connected_repo
from textual.widgets import OptionList, RichLog

from kraft import client
from kraft.console import tail
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


def _peek(app) -> list[str]:
    return [line.text.rstrip() for line in app.query_one("#peek-body", RichLog).lines]


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
                "▌m03d77b1  item m03d77b1         failed at verification                     1h",
                "RUNNING 1",
                "▌k7f2e9a0  Docs per major        implement                   docs           4m",
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
                "▌a91c03fe  Board critique fi…  approve spec            r                   12m",
                "▌m03d77b1  item m03d77b1       failed at verification  r                    1h",
                "RUNNING 2",
                "▌e1e1e1e1  item e1e1e1e1       implement               other                2m",
                "▌k7f2e9a0  Docs per major      implement               r · docs             4m",
                "NOT STARTED 1",
                "▌n0n0n0n0  item n0n0n0n0       created paused          r                   30m",
                "DONE 1 ›",
            ],
        ),
        (
            (70, 24),
            "/r",
            [
                "NEEDS YOU 2",
                "▌a91c03fe  Board critique fixes [/]  approve spec",
                "▌m03d77b1  item m03d77b1             failed at verification",
                "RUNNING 1",
                "▌k7f2e9a0  Docs per major            implement",
                "NOT STARTED 1",
                "▌n0n0n0n0  item n0n0n0n0             created paused",
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
    is drawn as typed, on its row and over the peek's detail read; markup
    would read it as a tag and end the console."""
    repo = connected_repo(tmp_path)

    async def scenario(app, pilot):
        created = await client.create_work_item("Fix the [/] parser", repo=str(repo))
        stream.send({"seq": 1_000, "type": "work_item_created", "work_item_id": created["id"]})
        await console.until(pilot, lambda: _peek(app))
        rows = [line for line in _lines(app) if created["id"][:8] in line]
        return created["id"], rows, str(app.query_one("#peek-head").render()), _peek(app)

    wid, [row], head, peek = console.run(scenario, size=(80, 40))
    assert "Fix the [/] parser" in row
    assert head.startswith(wid[:8])
    assert head.endswith(" · default · created paused")
    assert peek[0] == "created paused · just now"


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
    assert style_of(FAILED, "failed at verification") == "$error"

    async def scenario(app, pilot):
        await _settled(board([RUNNING]), app, pilot)
        [option] = [o for o in app.query_one("#rows", OptionList).options if o.disabled]
        [span] = option.prompt.spans
        return option.prompt.plain, str(span.style)

    assert board([RUNNING]).run(scenario) == ("Nothing needs you", "$success")


def test_enter_is_offered_only_where_it_does_something(board):
    """Enter folds Done on its heading and opens a closed peek on an item;
    with the peek open it does nothing on an item, so the footer must not offer it."""

    async def scenario(app, pilot):
        await _settled(board(BOARD), app, pilot)
        on_item = app.view.check_action("open", ())
        await pilot.press("v")
        peek_open = app.view.check_action("open", ())
        await pilot.press("end")
        return on_item, peek_open, app.view.check_action("open", ())

    assert board(BOARD).run(scenario) == (True, False, True)


def test_a_board_longer_than_the_window_keeps_the_cursor_near_a_vanished_item(board):
    """The item under the cursor is archived by a live event: the cursor moves
    to its neighbour, not to the top, and the view stays near."""

    async def scenario(app, pilot):
        await _settled(board(LONG), app, pilot)
        rows = app.query_one("#rows", OptionList)
        await pilot.press(*["j"] * 20)
        await pilot.pause()
        gone, at, scrolled = app.view.selected["id"], rows.highlighted, rows.scroll_y
        app.view.rows_changed(
            [r | {"display_status": "archived"} if r["id"] == gone else r for r in LONG],
            [{"seq": 1}],
        )
        await pilot.pause()
        return gone, app.view.selected["id"], rows.highlighted - at, rows.scroll_y - scrolled

    gone, now, moved, scroll = board(LONG).run(scenario)
    assert now != gone
    assert abs(moved) <= 1
    assert abs(scroll) <= 2


#: Seven nodes, so a narrow line has to leave some out on both sides.
SEVEN = [{"node": name, "gate": False, "state": "todo"} for name in "abcdefg"]


@pytest.mark.parametrize(
    ("at", "states", "width", "line"),
    [
        (3, None, 80, "a → b → c → d ● → e → f → g"),
        (3, None, 20, "+3 … d ● → e … +2"),
        (0, None, 14, "a ● → b … +5"),
        (None, "done", 14, "+4 … e → f → g"),
        (None, "todo", 14, "a → b → c … +4"),
    ],
    ids=["all-fits", "window", "at-the-start", "ended", "not-started"],
)
def test_the_chain_line_is_a_window_around_where_the_item_stands(at, states, width, line):
    """Spec §6: what fits on either side, `+N …` and `… +N` counting what is
    hidden; an ended chain shows its end, one not started its start."""
    marks = [
        {**m, "state": states or ("done" if k < at else "current" if k == at else "todo")}
        for k, m in enumerate(SEVEN)
    ]
    assert board_view.chain_line(marks, width).plain == line


def _detail(row: dict, **fields) -> dict:
    """The detail read (`get_work_item(full=True)`) of `row`."""
    return {**row, "status": "active", "worker_sessions": [], "dependencies": [], **fields}


QUESTION = _row(
    "q1q1q1q1",
    "needs_you",
    stop={"kind": "question", "node": "implement", "reason": "needs_context: keep [/] in titles?"},
    actions=["resume", "steer"],
)
PAUSED = _row("p1p1p1p1", "paused", 12, actions=["resume"])
BLOCKED = _row("b1b1b1b1", "blocked", current_node_id=None)
OVER_BUDGET = _row(
    "u1u1u1u1",
    "needs_you",
    stop={
        "kind": "budget",
        "node": "implement",
        "reason": "Spent $5.10 of a $5 cap. No other task stopped.",
    },
    actions=["retry", "raise_budget"],
)
SESSION = {
    "id": "s1",
    "node_id": "implement",
    "hook_point": "implement.main.author",
    "status": "running",
}
DOC = {"content": "# Spec\nGoal: fix the eleven findings.", "digest": None}


@pytest.mark.parametrize(
    ("rows", "detail", "chain", "body"),
    [
        (
            [GATE],
            _detail(GATE, actions=["reject"]),
            "spec → spec_approval ● → plan → implement",
            [
                "spec_approval is waiting on your decision.",
                "Approve waits on 2 open must-fix threads.",
                "",
                "# Spec",
                "Goal: fix the eleven findings.",
            ],
        ),
        (
            [QUESTION],
            _detail(QUESTION, needs_context_question="keep [/] in titles?"),
            None,
            ["The agent asks:", "keep [/] in titles?", "", "p answers it."],
        ),
        ([FAILED], _detail(FAILED), None, ["tests failed: 3 of 120", "", "t retries it."]),
        (
            [OVER_BUDGET],
            _detail(OVER_BUDGET),
            None,
            [
                "Spent $5.10 of a $5 cap. No other task stopped.",
                "",
                "t retries it.",
                "/item raise-budget u1u1u1u1 --usd N raises its budget.",
            ],
        ),
        ([PAUSED], _detail(PAUSED), None, ["paused at implement · 12m ago", "", "p resumes it."]),
        (
            [BLOCKED],
            _detail(
                BLOCKED,
                dependencies=[
                    {"id": "f1f1f1f1aaaa", "title": "first", "status": "active", "met": False},
                    {"id": "g1g1g1g1", "title": "done", "status": "completed", "met": True},
                ],
            ),
            None,
            ["waiting on another item", "It comes after:", "  f1f1f1f1  active  first"],
        ),
        (
            [RUNNING],
            _detail(RUNNING, worker_sessions=[SESSION]),
            None,
            ["reading the plan", "writing code", "session ended"],
        ),
        ([RUNNING], _detail(RUNNING), None, ["waiting for output"]),
        ([DONE], _detail(DONE), None, ["merged !42", "https://example.com/mr/42"]),
        (
            [GATE],
            client.status_error(404, "no such work item"),
            None,
            ["This item no longer exists."],
        ),
        (
            [GATE],
            client.status_error(500, "the store is unavailable"),
            None,
            [client.reason_of(str(client.status_error(500, "the store is unavailable")))],
        ),
        ([], None, None, ["server: started", "server: listening"]),
    ],
    ids=[
        "gate",
        "question",
        "failed",
        "over-budget",
        "paused",
        "blocked",
        "running",
        "running-before-output",
        "done",
        "gone",
        "server-error",
        "nothing-selected",
    ],
)
def test_the_peek_shows_what_the_selection_needs(board, monkeypatch, rows, detail, chain, body):
    """Spec §7.1's table, first match wins: a gate's chain, decision and
    document, with the must-fix threads that keep approve closed; a question;
    a stop's reason and way on; who waits on what; a running item's log, to
    its end; the outcome; and `server.log` with nothing selected."""
    monkeypatch.setattr(tail, "POLL_S", 0.01)
    log = tail.server_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("server: started\nserver: listening\n")

    async def get_work_item(work_item_id=None, *, full=False):
        assert full
        if isinstance(detail, Exception):
            raise detail
        return detail

    async def threads(work_item_id=None, open_only=False):
        return [{"label": "must_fix"}, {"label": "must_fix"}, {"label": "nit"}]

    async def artifact(work_item_id=None):
        return DOC

    async def stream_log(work_item_id, session_id, after_line=0):
        for text in ("reading the plan", "writing code"):
            yield {"n": 0, "text": text}

    for name, fake in [
        ("get_work_item", get_work_item),
        ("threads", threads),
        ("artifact", artifact),
        ("stream_log", stream_log),
    ]:
        monkeypatch.setattr(client, name, fake)

    async def scenario(app, pilot):
        await _settled(board(rows), app, pilot)
        if rows:  # Done is folded: `select` opens what holds the item
            app.view.select(rows[0]["id"])
        await board(rows).until(pilot, lambda: len(_peek(app)) >= len(body))
        await pilot.pause(0.05)  # nothing more arrives
        shown = app.query_one("#peek-chain")
        return (str(shown.render()) if shown.display else None), _peek(app)

    assert board(rows).run(scenario, size=(80, 40)) == (chain, body)


@pytest.mark.parametrize(
    ("height", "keys", "shown"),
    [(29, [], False), (30, [], True), (29, ["v"], True), (30, ["v"], False), (29, ["enter"], True)],
    ids=["closed-under-30", "open-at-30", "v-opens", "v-closes", "enter-opens"],
)
def test_the_peek_starts_closed_on_a_short_window_and_v_toggles_it(board, height, keys, shown):
    """Spec §7.1: closed under 30 rows, `v` at any size; Enter on an item
    opens it too, until the Item screen takes Enter over."""

    async def scenario(app, pilot):
        await _settled(board([RUNNING]), app, pilot)
        await pilot.press(*keys)
        return app.query_one("#peek").display

    assert board([RUNNING]).run(scenario, size=(80, height)) is shown


def test_holding_j_reads_only_where_the_cursor_stops(board, monkeypatch):
    """The peek reads after the cursor stops, so running down the board reads
    one item, not every row passed: each move stops the timer before it."""
    rows = [_row(f"r{n}r{n}r{n}r{n}", "running", n) for n in range(5)]
    read: list[str] = []
    timers: list[SimpleNamespace] = []

    def set_timer(self, delay, callback=None, **kwargs):
        timer = SimpleNamespace(stopped=False, callback=callback)
        timer.stop = lambda: setattr(timer, "stopped", True)
        timers.append(timer)
        return timer

    async def get_work_item(work_item_id=None, *, full=False):
        read.append(work_item_id)
        return _detail(next(row for row in rows if row["id"] == work_item_id))

    monkeypatch.setattr(client, "get_work_item", get_work_item)
    monkeypatch.setattr(board_view.Board, "set_timer", set_timer)

    async def scenario(app, pilot):
        await _settled(board(rows), app, pilot)
        before = len(timers)
        await pilot.press("j", "j", "j", "j")
        held = timers[before:]
        waiting = [timer for timer in held if not timer.stopped]
        quiet = list(read)
        for timer in waiting:
            timer.callback()
        await board(rows).until(pilot, lambda: read)
        return len(waiting), quiet, read

    assert board(rows).run(scenario, size=(80, 40)) == (1, [], [rows[4]["id"]])


def test_the_peek_stops_when_its_view_is_hidden_and_starts_when_shown(board):
    """The Board stays mounted behind other tabs: its `server.log` follower
    must not keep polling there."""

    async def scenario(app, pilot):
        await board([]).until(pilot, lambda: app._scoped)
        running = lambda: [w for w in app.workers if w.group == "peek" and w.is_running]  # noqa: E731
        await board([]).until(pilot, running)
        app.show("server")
        await board([]).until(pilot, lambda: not running())
        app.show("board")
        await board([]).until(pilot, running)
        return True

    assert board([]).run(scenario, size=(80, 40))


def test_a_later_selection_is_not_painted_over_by_the_earlier_log(board, monkeypatch):
    """Follow A's log, move to B: A's lines, written late, must not appear."""
    other = _row("o0o0o0o0", "running", 10)
    rows = [RUNNING, other]
    late = asyncio.Event()

    async def get_work_item(work_item_id=None, *, full=False):
        row = next(row for row in rows if row["id"] == work_item_id)
        return _detail(row, worker_sessions=[SESSION])

    async def stream_log(work_item_id, session_id, after_line=0):
        yield {"n": 0, "text": f"first from {work_item_id}"}
        if work_item_id == RUNNING["id"]:
            await late.wait()
            yield {"n": 1, "text": "late from the first"}

    monkeypatch.setattr(client, "get_work_item", get_work_item)
    monkeypatch.setattr(client, "stream_log", stream_log)
    monkeypatch.setattr(board_view, "PEEK_WAIT_S", 0.01)

    async def scenario(app, pilot):
        await _settled(board(rows), app, pilot)
        await board(rows).until(pilot, lambda: "first from k7f2e9a0" in _peek(app))
        await pilot.press("j")
        await board(rows).until(pilot, lambda: "first from o0o0o0o0" in _peek(app))
        late.set()
        await pilot.pause(0.1)
        return _peek(app)

    assert "late from the first" not in board(rows).run(scenario, size=(80, 40))


def test_a_long_line_wraps_inside_a_narrow_window(board, monkeypatch):
    """The body cannot take focus, so a line past the window's edge could never
    be read: it wraps at the window's width, not at the log's usual minimum."""
    long = "keep " * 24
    detail = _detail(QUESTION, needs_context_question=long.strip())

    async def get_work_item(work_item_id=None, *, full=False):
        return detail

    monkeypatch.setattr(client, "get_work_item", get_work_item)

    async def scenario(app, pilot):
        await _settled(board([QUESTION]), app, pilot)
        await board([QUESTION]).until(pilot, lambda: len(_peek(app)) >= 5)
        await pilot.pause(0.05)
        return _peek(app)

    lines = board([QUESTION]).run(scenario, size=(60, 40))
    assert max(len(line) for line in lines) <= 60
    assert " ".join(" ".join(lines).split()).count("keep") == 24


def test_a_live_refresh_rereads_the_peek_only_when_its_item_changed(board, monkeypatch):
    """A redraw on every live event must not read the same item again, nor
    take the focus from the Board; the item's own change reads it once more."""
    read: list[str] = []

    async def get_work_item(work_item_id=None, *, full=False):
        read.append(work_item_id)
        return _detail(RUNNING)

    monkeypatch.setattr(client, "get_work_item", get_work_item)
    monkeypatch.setattr(board_view, "PEEK_WAIT_S", 0.01)

    async def scenario(app, pilot):
        await _settled(board([RUNNING]), app, pilot)
        await board([RUNNING]).until(pilot, lambda: read)
        app.view.rows_changed([RUNNING], [{"seq": 1}])
        await pilot.pause(0.1)
        quiet = list(read)
        app.view.rows_changed([RUNNING | {"updated_at": _iso(0)}], [{"seq": 2}])
        await board([RUNNING]).until(pilot, lambda: len(read) == 2)
        return quiet, app.focused is app.view

    assert board([RUNNING]).run(scenario, size=(80, 40)) == ([RUNNING["id"]], True)
