"""The Board's keys that act on an item (`r`, `p`, `t`): through their
dialogs, to the item they opened for, and the server's own sentence back."""

from __future__ import annotations

import pytest
from support.console_board import FAILED, GATE, PAUSED, QUESTION, RUNNING, _detail, _settled

from kraft import client
from kraft.console.dialogs import Note
from kraft.console.screens import board as board_view

#: What a lifecycle route answers with the item's whole row (`is_item_row`).
WHOLE = {"status": "active", "chain_definition": {}}
PAUSED_STEER = PAUSED | {"actions": ["resume", "steer"]}


@pytest.mark.parametrize(
    ("row", "keys", "answer", "sent", "said"),
    [
        (
            GATE,
            ["r", *"vague", "enter"],
            GATE | WHOLE,
            ("reject_gate", ("vague", "spec_approval", "a91c03fe")),
            "rejected spec_approval on a91c03fe; the item is now running",
        ),
        (GATE, ["r", "enter"], None, None, None),
        (GATE, ["r", *"vague", "escape"], None, None, None),
        (
            RUNNING,
            ["p"],
            {"id": "k7f2e9a0", "paused_sessions": ["s1"]},
            ("pause", ("k7f2e9a0",)),
            "paused k7f2e9a0, stopping 1 running session; kraft item resume k7f2e9a0 carries on",
        ),
        (
            PAUSED_STEER,
            ["p", *"sqlite", "enter"],
            {"id": "p1p1p1p1", "node_id": "implement", "steered": ["implement.main.author"]},
            ("resume", ("sqlite", "p1p1p1p1")),
            "resumed p1p1p1p1 at implement; steered implement.main.author",
        ),
        (
            PAUSED,
            ["p"],
            {"id": "p1p1p1p1", "status": "queued", "slots": {"busy": 3, "limit": 3}},
            ("resume", (None, "p1p1p1p1")),
            "queued p1p1p1p1: 3 of 3 slots are busy. It starts when one frees; "
            "kraft item pause p1p1p1p1 takes it out of the queue",
        ),
        (
            QUESTION,
            ["p", "enter", *"postgres", "enter"],
            {"id": "q1q1q1q1", "node_id": "implement", "steered": []},
            ("resume", ("postgres", "q1q1q1q1")),
            "resumed q1q1q1q1 at implement",
        ),
        (
            FAILED,
            ["t", "enter"],
            {"id": "m03d77b1", "node_id": "verification", "attempt": 2},
            ("retry", (None, "m03d77b1")),
            "retried m03d77b1 at verification, attempt 2",
        ),
        (
            FAILED,
            ["t", *"sqlite", "enter"],
            {"id": "m03d77b1", "node_id": "verification"},
            ("retry", ("sqlite", "m03d77b1")),
            "retried m03d77b1 at verification",
        ),
        (RUNNING, ["r"], None, None, None),
        (
            FAILED,
            ["t", "enter"],
            client.status_error(409, "the item moved on: [/] it is running"),
            ("retry", (None, "m03d77b1")),
            "409: the item moved on: [/] it is running",
        ),
    ],
    ids=[
        "reject",
        "reject-needs-a-note",
        "esc-sends-nothing",
        "pause",
        "resume-with-a-steer",
        "resume-that-queued",
        "answer-a-question",
        "retry",
        "retry-with-a-steer",
        "not-open",
        "refused",
    ],
)
def test_a_key_acts_through_its_dialog_and_says_what_the_server_did(
    board, monkeypatch, row, keys, answer, sent, said
):
    """`r`, `p` and `t` send what their dialog collected and toast the CLI's
    sentence for the answer: "queued" for a resume the board had no slot for,
    never "resumed". A note is required where the server needs one; Esc sends
    nothing; a key the server does not list does nothing at all; a refusal is
    the server's own words, `[/]` and all."""
    calls: list[tuple] = []

    def fake(name):
        async def call(*args, **kwargs):
            calls.append((name, args))
            if isinstance(answer, Exception):
                raise answer
            return answer

        return call

    for name in ("reject_gate", "pause", "resume", "retry"):
        monkeypatch.setattr(client, name, fake(name))

    async def get_work_item(work_item_id=None, *, full=False):
        return _detail(row, steerable=True)

    monkeypatch.setattr(client, "get_work_item", get_work_item)

    async def scenario(app, pilot):
        await _settled(board([row]), app, pilot)
        for k, key in enumerate(keys):
            await pilot.press(key)
            if key in "rpt" and keys[k + 1 :]:  # what follows is typed into its dialog
                await board([row]).until(pilot, lambda: isinstance(app.screen, Note))

        def toasts() -> list[str]:
            return [str(toast.render()) for toast in app.screen.query("Toast")]

        if said:
            await board([row]).until(pilot, lambda: toasts())
        else:  # nothing to wait for: let the keys be handled, then look
            await pilot.pause()
            await pilot.pause()
        return calls, toasts(), isinstance(app.screen, Note)

    # Only an empty note leaves its dialog open: Enter does not send it.
    waiting = keys == ["r", "enter"]
    assert board([row]).run(scenario, notifications=True) == (
        [sent] if sent else [],
        [said] if said else [],
        waiting,
    )


def _moved_under_a_dialog(app, row) -> None:
    """A live read while the dialog is open: this row is gone from the board."""
    app.view.rows_changed([r for r in app.view.rows if r["id"] != row["id"]], [{"seq": 9}])


@pytest.mark.parametrize(
    ("row", "open_keys", "name", "args"),
    [
        (GATE, ["r"], "reject_gate", ("why", "spec_approval", "a91c03fe")),
        (FAILED, ["t"], "retry", ("why", "m03d77b1")),
    ],
    ids=["reject", "retry"],
)
def test_an_action_goes_to_the_item_its_dialog_opened_for(
    board, monkeypatch, row, open_keys, name, args
):
    """A live redraw may move or drop rows while a dialog is open: what is sent
    is for the item the key was pressed on, not the one under the cursor now."""
    calls: list[tuple] = []

    async def call(*sent):
        calls.append((name, sent))
        return {"id": row["id"]}

    async def get_work_item(work_item_id=None, *, full=False):
        return _detail(row, steerable=True)

    monkeypatch.setattr(client, name, call)
    monkeypatch.setattr(client, "get_work_item", get_work_item)

    async def scenario(app, pilot):
        await _settled(board([GATE, FAILED, RUNNING]), app, pilot)
        while app.view.selected["id"] != row["id"]:
            await pilot.press("j")
        await pilot.press(*open_keys)
        await board([row]).until(pilot, lambda: isinstance(app.screen, Note))
        _moved_under_a_dialog(app, row)
        await pilot.press(*"why", "enter")
        await board([row]).until(pilot, lambda: calls)
        return calls

    assert board([GATE, FAILED, RUNNING]).run(scenario) == [(name, args)]


@pytest.mark.parametrize(
    ("height", "reads"), [(40, True), (24, False)], ids=["peek-open", "peek-closed"]
)
def test_a_refused_action_reads_the_board_and_the_item_again(board, monkeypatch, height, reads):
    """The server's refusal may mean the row is stale: the board is read once
    more (no event announces it) and, if the peek is open, the item too."""
    detail_reads: list[str] = []
    board_reads: list[int] = []
    rows = [FAILED]

    async def get_work_item(work_item_id=None, *, full=False):
        detail_reads.append(work_item_id)
        return _detail(FAILED)

    async def refused(*args):
        raise client.status_error(409, "the item moved on")

    async def board_rows(**kwargs):
        board_reads.append(1)
        return rows, 0

    monkeypatch.setattr(client, "get_work_item", get_work_item)
    monkeypatch.setattr(client, "retry", refused)
    monkeypatch.setattr(board_view, "PEEK_WAIT_S", 0.01)

    console = board(rows)  # patches the board read; ours goes on top of it

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        monkeypatch.setattr(client, "board_rows", board_rows)
        if reads:
            await console.until(pilot, lambda: detail_reads)
        before = len(detail_reads)
        await pilot.press("t", "enter")
        await console.until(pilot, lambda: board_reads)
        if reads:
            await console.until(pilot, lambda: len(detail_reads) >= before + 2)
        return len(detail_reads) - before

    # The steer check reads the item once; an open peek reads it once more.
    assert console.run(scenario, size=(80, height)) == (2 if reads else 1)


@pytest.mark.parametrize(
    ("row", "key", "name", "args"),
    [
        (FAILED, "t", "retry", (None, "m03d77b1")),
        (PAUSED | {"actions": ["resume", "steer"]}, "p", "resume", (None, "p1p1p1p1")),
    ],
    ids=["retry", "resume"],
)
def test_a_steer_the_server_cannot_place_is_not_asked_for(board, monkeypatch, row, key, name, args):
    """The server answers a steer with no agent task to reach with a 409 and
    says so on the detail's `steerable`: no dialog, and none is sent."""
    calls: list[tuple] = []

    async def get_work_item(work_item_id=None, *, full=False):
        return _detail(row, steerable=False)

    async def call(*sent):
        calls.append((name, sent))
        return {"id": row["id"], "node_id": "implement"}

    monkeypatch.setattr(client, "get_work_item", get_work_item)
    monkeypatch.setattr(client, name, call)

    async def scenario(app, pilot):
        await _settled(board([row]), app, pilot)
        await pilot.press(key)
        await board([row]).until(pilot, lambda: calls)
        return calls, isinstance(app.screen, Note)

    assert board([row]).run(scenario) == ([(name, args)], False)
