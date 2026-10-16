"""The Item (spec §7.2): its header, banner and chain, the gate's document on
the Overview, and the keys that act on it."""

from __future__ import annotations

import pytest
from support.console_board import CHAIN, FAILED, GATE, RUNNING, _detail, _settled, _text
from textual.widgets import RichLog

from kraft import client
from kraft.console import words
from kraft.console.dialogs import Note, Reject
from kraft.console.screens import item as item_view

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
DOC = {"content": "\n".join(f"line {n} [/]" for n in range(60)), "digest": "d1g3st"}


def _overview(app) -> list[str]:
    return [line.text.rstrip() for line in app.query_one("#overview", RichLog).lines]


def _serve(monkeypatch, detail, *, document=DOC, threads=(), calls=None):
    """The item's reads, and every action recorded into `calls`."""

    async def get_work_item(work_item_id=None, *, full=False):
        if isinstance(detail, Exception):
            raise detail
        return detail

    async def artifact(work_item_id=None):
        if document is None:
            raise client.status_error(404, "no document")
        return document

    async def list_threads(work_item_id=None, open_only=False):
        return list(threads)

    monkeypatch.setattr(client, "get_work_item", get_work_item)
    monkeypatch.setattr(client, "artifact", artifact)
    monkeypatch.setattr(client, "threads", list_threads)
    for name in ("approve_gate", "reject_gate", "retry", "pause", "resume"):

        async def call(*args, _name=name, **kwargs):
            calls.append((_name, args, kwargs))
            return {"id": detail["id"], "node_id": "implement"}

        monkeypatch.setattr(client, name, call)


async def _opened(console, app, pilot, work_item_id: str) -> None:
    await _settled(console, app, pilot)
    app.open_item(work_item_id)
    await console.until(pilot, lambda: app.view.item is not None and _overview(app))


@pytest.mark.parametrize(
    ("row", "title", "status", "banner"),
    [
        (
            GATE,
            "Board › a91c03fe  Board critique fixes [/]",
            "● Needs you · approve spec",
            "▌ Waiting for your approval at spec_approval.",
        ),
        (
            OVER_BUDGET,
            "Board › u1u1u1u1  Cap [/] it",
            "● Needs you · spent $5.10 [/] of $5",
            f"▌ Stopped at implement. Spent $5.10 [/] of $5. {words.NOT_RAISABLE}",
        ),
        (RUNNING, "Board › k7f2e9a0  Docs per major", "● Running · implement", None),
    ],
    ids=["gate", "budget-it-cannot-raise", "running"],
)
def test_the_header_says_where_the_item_stands(board, monkeypatch, row, title, status, banner):
    """Spec §7.2: id and title, status in the web's words with the reason, the
    banner with the server's reason and the way on only when stopped, `[/]`
    drawn as typed; and the chain's window, the item's place in the accent."""
    _serve(monkeypatch, _detail(row), calls=[])
    console = board([row])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, row["id"])
        shown = app.query_one("#item-banner")
        chain = app.query_one("#item-chain").render()
        place = next(span for span in chain.spans if "●" in chain.plain[span.start : span.end])
        return (
            _text(app, "#item-title"),
            _text(app, "#item-status"),
            _text(app, "#item-banner") if shown.display else None,
            chain.plain,
            str(place.style),
        )

    assert console.run(scenario) == (
        title,
        status,
        banner,
        "spec → spec_approval ● → plan → implement"
        if row is GATE
        else "spec → spec_approval → plan → implement ●",
        "bold $primary",
    )


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
    assert lines[-4:] == [" ✓ spec", " ● spec_approval (gate)", "   plan", "   implement"]


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
    ],
    ids=["approve-with-the-digest-read", "must-fix-keeps-approve-shut", "retry"],
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
    ("keys", "sent"),
    [
        (
            ["w", "h", "y", "enter"],
            [("reject_gate", ("why", "spec_approval", "a91c03fe"), {"node": "spec"})],
        ),
        (
            ["shift+tab", "down", "down", "enter", "tab", "w", "h", "y", "enter"],
            [("reject_gate", ("why", "spec_approval", "a91c03fe"), {"node": "plan"})],
        ),
        (["w", "h", "y", "escape"], []),
    ],
    ids=["to-the-fix-target", "to-a-node-picked", "esc-sends-nothing"],
)
def test_reject_on_the_item_sends_the_work_back_where_it_was_picked(board, monkeypatch, keys, sent):
    """Spec §7.2: the reject note's node starts on the gate's fix target and
    can be any node before the gate."""
    calls: list[tuple] = []
    chain = CHAIN | {
        "nodes": [*CHAIN["nodes"][:1], {"id": "plan", "kind": "exec"}, *CHAIN["nodes"][1:2]]
    }
    detail = _detail(GATE, chain_definition=chain, fix_target={"node": "spec"})
    _serve(monkeypatch, detail, calls=calls)
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("r")
        await console.until(pilot, lambda: isinstance(app.screen, Reject))
        for key in keys:
            await pilot.press(key)
        if sent:
            await console.until(pilot, lambda: calls)
        await console.until(pilot, lambda: not isinstance(app.screen, Note))
        return calls

    assert console.run(scenario) == sent


def test_an_item_that_goes_away_while_open_says_so(board, monkeypatch):
    """Archived or abandoned while open (spec §7.2): the live read finds it
    gone, the view says so and offers no key that acts; Esc goes back."""
    detail = _detail(FAILED)
    _serve(monkeypatch, detail, calls=[])
    console = board([FAILED])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, FAILED["id"])

        async def gone(work_item_id=None, *, full=False):
            raise client.status_error(404, "no such work item")

        monkeypatch.setattr(client, "get_work_item", gone)
        app.view.rows_changed([], [{"seq": 1}])
        await console.until(pilot, lambda: app.query_one("#item-gone").display)
        keys = app.view.check_action("retry", ())
        await pilot.press("escape")
        return _text(app, "#item-gone"), keys, app.view.id

    assert console.run(scenario) == (item_view.GONE, False, "board")


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
        view.rows_changed([RUNNING | {"updated_at": "2026-10-16T00:00:00+00:00"}], [{"seq": 3}])
        await console.until(pilot, lambda: len(reads) == 2)
        return quiet, hidden

    assert console.run(scenario) == (1, 1)
