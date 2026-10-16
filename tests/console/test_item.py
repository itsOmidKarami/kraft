"""The Item (spec §7.2): its header, banner and chain, the gate's document on
the Overview, and the keys that act on it."""

from __future__ import annotations

import asyncio

import pytest
from support.console_board import (
    CHAIN,
    DOC,
    FAILED,
    FILED,
    GATE,
    PAUSED,
    QUESTION,
    RUNNING,
    _detail,
    _opened,
    _overview,
    _row,
    _serve,
    _settled,
    _text,
)
from textual.widgets import RichLog
from textual.widgets._footer import FooterKey

from kraft import client
from kraft.console import words
from kraft.console.dialogs import Note, Reject
from kraft.console.screens import item as item_view

AT = "What is wrong? The agent at {} gets this note and redoes it."
OVER_BUDGET = FAILED | {
    "id": "u1u1u1u1",
    "title": "Cap [/] it",
    "display_status": "needs_you",
    "current_node_id": "implement",
    "stop": {
        "kind": "budget",
        "node": "implement",
        "reason": "spent $5.10 [/] of $5",
        "scope": "daily",
    },
    "actions": ["retry"],
    "budget_cap": {"spent_usd": 5.1, "cap_usd": 5},
}
STUCK = _row(
    "s5s5s5s5",
    "needs_you",
    5,
    current_node_id="verification",
    stop={
        "kind": "stuck",
        "node": "verification",
        "reason": "the fix loop made no progress in 3 rounds",
    },
)


async def _read_done(console, app, pilot) -> None:
    """The Item's read workers have all finished (the app's own stream worker
    never does, so not `workers.wait_for_complete`)."""
    await console.until(pilot, lambda: all(w.is_finished for w in app.workers if w.group == "read"))


@pytest.mark.parametrize(
    ("row", "title", "status", "banner", "tone", "spent", "where", "chain"),
    [
        (
            GATE,
            "Board › a91c03fe  Board critique fixes [/]",
            "● Needs you · approve spec",
            "▌ Waiting for you to approve spec.",
            "$warning",
            "12m",
            "r · default",
            "spec → spec_approval ● → plan → implement",
        ),
        (
            OVER_BUDGET,
            "Board › u1u1u1u1  Cap [/] it",
            "● Needs you · spent $5.10 [/] of $5",
            f"▌ Stopped at implement. Spent $5.10 [/] of $5. {words.NOT_RAISABLE}",
            "$warning",
            "$5.10 of $5.00 · 1h",
            "r · default",
            "spec → spec_approval → plan → implement ●",
        ),
        (
            RUNNING,
            "Board › k7f2e9a0  Docs per major",
            "● Running",  # the working line carries where it is
            None,
            None,
            "4m",
            "r · docs",
            "spec → spec_approval → plan → implement ●",
        ),
        (
            FAILED,
            "Board › m03d77b1  item m03d77b1",
            "● Failed · failed at verification",
            "▌ Failed at verification. Tests failed: 3 of 120.",
            "$error",
            "1h",
            "r · default",
            "spec → spec_approval → plan → implement",
        ),
        (
            STUCK,
            "Board › s5s5s5s5  item s5s5s5s5",
            "● Needs you · the fix loop made no progress in 3 rounds",
            "▌ Stopped at verification. The fix loop made no progress in 3 rounds.",
            "$warning",
            "5m",
            "r · default",
            "spec → spec_approval → plan → implement",
        ),
        (
            # Nothing to say but that it stopped: the frame still says that much.
            STUCK | {"stop": {"kind": "stuck", "node": None, "reason": None}},
            "Board › s5s5s5s5  item s5s5s5s5",
            "● Needs you · waiting for you at verification",
            "▌ Stopped.",
            "$warning",
            "5m",
            "r · default",
            "spec → spec_approval → plan → implement",
        ),
        (
            FILED,
            "Board › n0n0n0n0  item n0n0n0n0",
            "● Not started · created paused",
            None,
            None,
            "30m",
            "r · default",
            "spec → spec_approval → plan → implement",
        ),
    ],
    ids=["gate", "budget-it-cannot-raise", "running", "failed", "stuck", "stopped-saying-nothing"]
    + ["not-started"],
)
def test_the_header_says_where_the_item_stands(
    board, monkeypatch, row, title, status, banner, tone, spent, where, chain
):
    """Spec §7.2: id and title, status in the web's words with the reason, the
    banner with the server's reason and the way on only when stopped (amber
    when it needs you, red when it failed), `[/]` drawn as typed; spend, age,
    repo and chain; and the chain's window, the item's place in the accent."""
    _serve(monkeypatch, _detail(row), calls=[])
    console = board([row])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, row["id"])
        shown = app.query_one("#item-banner")
        line = app.query_one("#item-chain").render()
        place = next((s for s in line.spans if "●" in line.plain[s.start : s.end]), None)
        marker = shown.render().spans[0].style if shown.display else None
        return (
            _text(app, "#item-title"),
            _text(app, "#item-status"),
            _text(app, "#item-banner") if shown.display else None,
            str(marker) if marker else None,
            _text(app, "#item-spent"),
            _text(app, "#item-where"),
            line.plain,
            str(place.style) if place else None,
        )

    assert console.run(scenario) == (
        title,
        status,
        banner,
        tone,
        spent,
        where,
        chain,
        "bold $kraft-accent" if "●" in chain else None,
    )


@pytest.mark.parametrize(
    ("row", "sessions", "first", "p"),
    [
        (
            QUESTION,
            [],
            ["The agent asks:", "keep [/] in titles?", "", "p answers it."],
            "answer",
        ),
        (FAILED, [], ["t retries it."], None),
        (FAILED, [{"id": "s1"}], ["t retries it.", item_view.LOG_POINTER], None),
        (PAUSED, [], ["p resumes it."], "resume"),
        (RUNNING, [], [], "pause"),
        (
            PAUSED | {"actions": []},
            [],
            ["Kraft offers no action for it now; m shows what it allows."],
            None,
        ),
        (
            FAILED | {"actions": []},
            [],
            ["Kraft offers no action for it now; m shows what it allows."],
            None,
        ),
    ],
    ids=[
        "question",
        "failed",
        "failed-with-a-session",
        "paused",
        "running-says-nothing",
        "paused-no-door",
        "failed-no-door",
    ],
)
def test_a_stopped_item_says_the_way_on_and_p_says_what_it_does(
    board, monkeypatch, row, sessions, first, p
):
    """The banner says why a stopped item stopped; the body says what moves it
    on, in the peek's own sentences (`acts.way_on`): an agent's question
    whole and `p` to answer it, `t` and the Log for a failed one, `p` for a
    paused one. The footer's `p` names what it does now."""
    _serve(monkeypatch, _detail(row, worker_sessions=sessions), calls=[])
    console = board([row])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, row["id"])
        await pilot.pause()
        shown = app.screen.active_bindings.get("p")
        return _overview(app)[: len(first)], shown and shown.binding.description

    assert console.run(scenario) == (first, p)


@pytest.mark.parametrize("keys", [["enter"], ["a"]], ids=["enter", "a"])
def test_enter_and_a_on_the_board_open_the_items_overview(board, monkeypatch, keys):
    """Spec §6: Enter opens the item; `a` opens its Overview rather than
    approving blind. Neither sends anything; Esc comes back to the Board, and
    opening it again draws at the window's width, not at a hidden pane's."""
    calls: list[tuple] = []
    _serve(monkeypatch, _detail(GATE), calls=calls)
    console = board([GATE])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        opened = []
        for _ in range(2):
            await pilot.press(*keys)
            await console.until(pilot, lambda: app.view.id == "item" and _overview(app))
            opened.append((app.view.work_item_id, _overview(app)[0]))
            await pilot.press("escape")
        return opened, app.view.id, calls

    assert console.run(scenario) == ([(GATE["id"], "line 0 [/]")] * 2, "board", [])


def test_the_gate_document_is_read_whole_and_scrolls_by_keys(board, monkeypatch):
    """At 80×24 the document gets a dozen lines: every one of its 60 is there,
    the keys scroll it, and a cut document says so at its end."""
    document = DOC | {"truncated": True, "artifact_max_bytes": 1000, "path": "spec.md"}
    _serve(monkeypatch, _detail(GATE), document=document, calls=[])
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        log = app.query_one("#overview", RichLog)
        top = log.scroll_y
        await pilot.press("j", "pagedown")
        moved = log.scroll_y
        await pilot.press("end")
        return top, moved > top, log.scroll_y == log.max_scroll_y, _overview(app)

    top, moved, at_end, lines = console.run(scenario, size=(80, 24))
    assert (top, moved, at_end) == (0, True, True)
    assert lines[:60] == [f"line {n} [/]" for n in range(60)]
    assert lines[61].startswith("WARNING: this document was truncated by the server at 1000 bytes")
    assert lines[-1].endswith("Read the rest at spec.md.")  # no chain list after the warning


@pytest.mark.parametrize(
    ("detail", "threads", "keys", "sent", "said"),
    [
        (
            _detail(GATE),
            (),
            ["a"],
            [("approve_gate", ("spec_approval", "a91c03fe", "d1g3st"), {})],
            None,
        ),
        (
            _detail(GATE, actions=["reject"]),
            [{"label": "must_fix"}, {"label": "must_fix"}],
            ["a"],
            [],
            "Approve waits on 2 open must-fix threads.",
        ),
        (
            _detail(FAILED, steerable=False),
            (),
            ["t"],
            [("retry", (None, "m03d77b1"), {})],
            None,
        ),
        (
            _detail(GATE),
            (),
            ["a", "a"],
            [("approve_gate", ("spec_approval", "a91c03fe", "d1g3st"), {})],
            None,
        ),
    ],
    ids=[
        "approve-with-the-digest-read",
        "must-fix-keeps-approve-shut",
        "retry",
        "a-second-a-sends-nothing",
    ],
)
def test_a_key_on_the_item_acts_through_the_shared_flow(
    board, monkeypatch, detail, threads, keys, sent, said
):
    """`a` approves with the digest of the document on screen (a chain
    revision's approval is bound to it); open must-fix threads keep it shut
    and the Overview says why; `t` is the Board's own retry flow."""
    calls: list[tuple] = []
    _serve(monkeypatch, detail, threads=threads, calls=calls)
    console = board([detail])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, detail["id"])
        await pilot.press(*keys)
        if sent:
            await console.until(pilot, lambda: calls)
        else:
            await pilot.pause()
        return calls, _overview(app)[0]

    got, first = console.run(scenario)
    assert got == sent
    if said:
        assert first == said


@pytest.mark.parametrize(
    ("fix", "keys", "sent", "picker", "said"),
    [
        (
            {"node": "plan"},
            ["w", "h", "y", "enter"],
            [("reject_gate", ("why", "spec_approval", "a91c03fe"), {"node": "plan"})],
            True,
            AT.format("plan"),
        ),
        (
            {"node": "plan"},
            ["shift+tab", "down", "up", "enter", "tab", "w", "h", "y", "enter"],
            [("reject_gate", ("why", "spec_approval", "a91c03fe"), {"node": "spec"})],
            True,
            AT.format("spec"),
        ),
        ({"node": "plan"}, ["w", "h", "y", "escape"], [], True, AT.format("plan")),
        (
            None,
            ["w", "h", "y", "enter"],
            [("reject_gate", ("why", "spec_approval", "a91c03fe"), {})],
            False,
            "What is wrong? The agent that redoes it gets this note.",
        ),
    ],
    ids=["to-the-fix-target", "to-a-node-picked", "esc-sends-nothing", "no-fix-target"],
)
def test_reject_on_the_item_sends_the_work_back_where_it_was_picked(
    board, monkeypatch, fix, keys, sent, picker, said
):
    """Spec §7.2: the reject note's node starts on the gate's fix target (not
    the first node) and can be any node before the gate; with no fix target
    the plain note is asked and the server's own target applies. The note
    names the node whose agent gets it, and follows the pick."""
    calls: list[tuple] = []
    chain = CHAIN | {
        "nodes": [*CHAIN["nodes"][:1], {"id": "plan", "kind": "exec"}, *CHAIN["nodes"][1:2]]
    }
    detail = _detail(GATE, chain_definition=chain, fix_target=fix)
    _serve(monkeypatch, detail, calls=calls)
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("r")
        await console.until(pilot, lambda: isinstance(app.screen, Note))
        asked, question = isinstance(app.screen, Reject), app.screen.question
        for key in keys[:-1]:
            await pilot.press(key)
        asks = str(app.screen.query_one("#note-lines").render())
        await pilot.press(keys[-1])
        if sent:
            await console.until(pilot, lambda: calls)
        await console.until(pilot, lambda: not isinstance(app.screen, Note))
        return calls, asked, asks, question

    # Either dialog names the gate as the web does.
    assert console.run(scenario) == (sent, picker, said, "Request changes: approve spec · a91c03fe")


def test_an_item_that_goes_away_while_open_says_so(board, monkeypatch):
    """Archived or abandoned while open (spec §7.2): the live read finds it
    gone, the view says so and offers no key that acts; Esc goes back."""
    detail = _detail(FAILED)
    _serve(monkeypatch, detail, calls=[])
    console = board([FAILED])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, FAILED["id"])

        reads: list[str] = []

        async def gone(work_item_id=None, *, full=False):
            reads.append(work_item_id)
            raise client.status_error(404, "no such work item")

        monkeypatch.setattr(client, "get_work_item", gone)
        app.view.rows_changed([], [{"seq": 1}])
        await console.until(pilot, lambda: app.query_one("#item-gone").display)
        keys = app.view.check_action("retry", ())
        app.view.rows_changed([], [{"seq": 2}])  # an item that is gone is not read again
        await pilot.pause()
        await pilot.press("escape")
        return _text(app, "#item-gone"), keys, app.view.id, reads

    assert console.run(scenario) == (item_view.GONE, False, "board", [FAILED["id"]])


def test_the_item_reads_again_only_when_shown_and_its_row_changed(board, monkeypatch):
    """A live event about another item, or one while the Item is hidden,
    reads nothing; its own row's change reads it once more."""
    reads: list[str] = []
    detail = _detail(RUNNING)

    async def get_work_item(work_item_id=None, *, full=False):
        reads.append(work_item_id)
        return detail

    _serve(monkeypatch, detail, calls=[])
    monkeypatch.setattr(client, "get_work_item", get_work_item)
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        view = app.view
        view.rows_changed([RUNNING], [{"seq": 1}])
        await pilot.pause()
        quiet = len(reads)
        app.show("server")
        view.rows_changed([RUNNING | {"updated_at": "2026-10-16T00:00:00+00:00"}], [{"seq": 2}])
        await pilot.pause()
        hidden = len(reads)
        app.show("item")
        await pilot.pause()  # the show's own read settles first
        shown = len(reads)
        view.rows_changed([RUNNING | {"updated_at": "2026-10-16T00:00:00+00:00"}], [{"seq": 3}])
        await console.until(pilot, lambda: len(reads) > shown)
        return quiet, hidden

    assert console.run(scenario) == (1, 1)


@pytest.mark.parametrize("again", [False, True], ids=["first-read", "same-gate-pends-again"])
def test_approve_waits_for_the_document_it_would_approve(board, monkeypatch, again):
    """Review Focus 1: `a` is shut, and sends nothing, while the document is
    being read, including when the same gate pends again with a new version
    (the last version's digest is never offered); once read it sends the digest
    of what is on screen."""
    calls: list[tuple] = []
    _serve(monkeypatch, _detail(GATE), calls=calls)
    console = board([GATE])

    async def scenario(app, pilot):
        asked, release = asyncio.Event(), asyncio.Event()
        if again:
            await _opened(console, app, pilot, GATE["id"])

        async def artifact(work_item_id=None):
            asked.set()
            await release.wait()
            return DOC | {"digest": "new"}

        monkeypatch.setattr(client, "artifact", artifact)
        if again:
            app.view.rows_changed([], [])
        else:
            app.open_item(GATE["id"])
        await console.until(pilot, asked.is_set)
        held = app.view.check_action("approve", ())
        await pilot.press("a")
        await pilot.pause()
        sent_while_held = list(calls)
        release.set()
        await console.until(pilot, lambda: app.view.digest == "new")
        opened = app.view.check_action("approve", ())
        await pilot.press("a")
        await console.until(pilot, lambda: calls)
        return held, sent_while_held, opened, calls

    held, early, opened, sent = console.run(scenario)
    assert (held, early, opened) == (False, [], True)
    assert sent == [("approve_gate", ("spec_approval", "a91c03fe", "new"), {})]


def test_a_failed_live_read_leaves_the_error_once(board, monkeypatch):
    """A read that fails after the item answered replaces what the Overview
    shows with the error, again and again, never under the last one."""
    _serve(monkeypatch, _detail(GATE), calls=[])
    console = board([GATE])

    async def scenario(app, pilot):
        async def artifact(work_item_id=None):
            raise client.status_error(500, "boom [/]")

        monkeypatch.setattr(client, "artifact", artifact)
        app.open_item(GATE["id"])
        await console.until(pilot, lambda: app.view.item is not None and _overview(app))
        for _ in range(2):
            await _read_done(console, app, pilot)
            app.view.rows_changed([], [])
        await _read_done(console, app, pilot)
        return _overview(app)

    assert console.run(scenario) == [client.reason_of(str(client.status_error(500, "boom [/]")))]


def test_a_live_read_leaves_the_reader_where_they_scrolled(board, monkeypatch):
    """The Overview is drawn again by every live read; a reader halfway down
    the document stays there."""
    _serve(monkeypatch, _detail(GATE), calls=[])
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        log = app.query_one("#overview", RichLog)
        await pilot.press("pagedown")
        before = log.scroll_y
        app.view.rows_changed([], [])
        await _read_done(console, app, pilot)
        await pilot.pause()
        return before > 0, log.scroll_y == before

    assert console.run(scenario) == (True, True)


def test_the_title_names_the_view_the_item_was_opened_from(board, monkeypatch):
    """Ctrl+P opens an item from whichever view shows; the crumb says which."""
    _serve(monkeypatch, _detail(RUNNING), calls=[])
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        app.show("server")
        app.open_item(RUNNING["id"])
        await console.until(pilot, lambda: app.view.item is not None)
        return _text(app, "#item-title")

    assert console.run(scenario) == "Server › k7f2e9a0  Docs per major"


def test_a_read_for_the_item_shown_before_is_dropped(board, monkeypatch):
    """Ctrl+P from an open item lands while its live read is in flight: the
    answer for the old item must not draw its header or open `a` for its gate,
    even when the new item's own read fails."""
    other = GATE | {"id": "b2b2b2b2", "title": "Second", "pending_gate": "plan_review"}
    calls: list[tuple] = []
    _serve(monkeypatch, _detail(other), calls=calls)
    console = board([GATE, other])

    async def scenario(app, pilot):
        asked, release, broken = asyncio.Event(), asyncio.Event(), [True]

        async def get_work_item(work_item_id=None, *, full=False):
            if work_item_id == GATE["id"]:
                asked.set()
                await release.wait()
                return _detail(GATE)
            if broken:
                raise client.status_error(500, "down")
            return _detail(other)

        monkeypatch.setattr(client, "get_work_item", get_work_item)
        await _settled(console, app, pilot)
        app.open_item(GATE["id"])
        await console.until(pilot, asked.is_set)
        app.open_item(other["id"])
        release.set()
        await _read_done(console, app, pilot)
        await pilot.pause()
        stale = (_text(app, "#item-title"), app.view.check_action("approve", ()))
        broken.clear()
        app.view.rows_changed([], [])
        await console.until(pilot, lambda: app.view.item is not None and _overview(app))
        await pilot.press("a")
        await console.until(pilot, lambda: calls)
        return stale, _text(app, "#item-title"), calls

    stale, title, sent = console.run(scenario)
    assert stale == ("Board › b2b2b2b2", False)
    assert title == "Board › b2b2b2b2  Second"
    assert sent == [("approve_gate", ("plan_review", "b2b2b2b2", "d1g3st"), {})]


@pytest.mark.parametrize(
    ("keys", "tab"),
    [
        (["right"], "chain"),
        (["left"], "diff"),
        (["tab"], "chain"),
        (["shift+tab"], "diff"),
        (["right", "right", "left"], "chain"),
    ],
    ids=["right", "left-wraps", "tab", "shift-tab-wraps", "back-and-forth"],
)
def test_arrows_and_tab_switch_the_items_tabs(board, monkeypatch, keys, tab):
    """What a first-time user tries: ← →, Tab and Shift+Tab step the tabs, as
    `[` and `]` do (the tests/console/test_item_tabs.py pin)."""
    _serve(monkeypatch, _detail(GATE), calls=[])
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press(*keys)
        return app.view.tab

    assert console.run(scenario) == tab


def test_clicking_a_tabs_name_opens_it(board, monkeypatch):
    _serve(monkeypatch, _detail(GATE), calls=[])
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        shown = []
        # " Overview  Chain  Activity  Log  Diff": Log's name starts at column 30.
        for x in (33, 22, 14):
            await pilot.click("#item-tabs", offset=(x, 0))
            shown.append(app.view.tab)
        return shown

    assert console.run(scenario) == ["log", "activity", "chain"]


def test_the_footer_and_the_keys_dialog_name_the_tab_keys(board, monkeypatch):
    """One footer entry, `←→ tabs`; `?` lists every key that switches them."""
    _serve(monkeypatch, _detail(GATE), calls=[])
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.pause()
        footer = [(k.key_display, k.description) for k in app.query(FooterKey)]
        return footer, dict((label, keys) for keys, label in app.screen_keys())

    footer, keys = console.run(scenario)
    assert ("←→", "tabs") in footer
    assert not any(label in ("tab", "tabs") and key != "←→" for key, label in footer)
    assert keys["tabs"] == "←→ [ ] tab"
