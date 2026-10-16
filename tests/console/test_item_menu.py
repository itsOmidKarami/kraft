"""The Item's `m` menu (spec §6, §7.2, §7.8): skip, unblock, raise budget,
escalate, complete, cancel and archive, each offered only where the server
opens its door, each through its dialog, each said in the CLI's sentence."""

from __future__ import annotations

import pytest
from support.console_board import FAILED, _detail, _opened, _serve
from textual.widgets import OptionList

from kraft import client
from kraft.console.dialogs import Confirm, Menu, Note
from kraft.console.screens import item as item_view

ITEM = FAILED | {"actions": ["retry", *(door for door, _ in item_view.MENU)]}
PREVIEW = {
    "running": {"node": "verification", "task": "verification.review.code_review", "attempt": 2},
    "kept": {"branch": "kraft/x", "worktree": "/w", "findings": 3, "threads": 0},
    "mr": None,
    "spend": {"spent_usd": 0.42, "cap_usd": None},
    "beads": [],
}


async def _preview(work_item_id=None) -> dict:
    return PREVIEW


def _toasts(app) -> list[str]:
    return [str(toast.render()) for toast in app.screen.query("Toast")]


async def _pick(console, pilot, app, name: str) -> None:
    """`m`, then the entry called `name`."""
    await pilot.press("m")
    await console.until(pilot, lambda: isinstance(app.screen, Menu))
    listed = app.screen.query_one(OptionList)
    listed.highlighted = [option.id for option in listed.options].index(name)
    await pilot.press("enter")


def _record(monkeypatch, name: str, answer) -> list[tuple]:
    calls: list[tuple] = []

    async def call(*args, **kwargs):
        calls.append(args)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(client, name, call)
    return calls


@pytest.mark.parametrize(
    ("actions", "entries"),
    [
        (["retry", "skip", "cancel", "complete", "pause"], ["Skip", "Complete…", "Cancel…"]),
        (["retry", "pause"], None),
    ],
    ids=["open-doors-only", "none-open"],
)
def test_m_lists_only_the_doors_the_server_opens(board, monkeypatch, actions, entries):
    """Spec §6: a key appears only when its action is open; so does an entry."""
    detail = _detail(FAILED, actions=actions)
    _serve(monkeypatch, detail)
    console = board([detail])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, detail["id"])
        if not app.view.check_action("menu", ()):
            return None
        await pilot.press("m")
        await console.until(pilot, lambda: isinstance(app.screen, Menu))
        return [o.prompt.plain for o in app.screen.query_one(OptionList).options]

    assert console.run(scenario) == entries


@pytest.mark.parametrize(
    ("name", "typed", "call", "answer", "sent", "said"),
    [
        (
            "skip",
            ["enter"],
            "skip",
            {"id": "m03d77b1"},
            (None, "m03d77b1"),
            "skipped verification on m03d77b1",
        ),
        (
            "unblock",
            [],
            "unblock",
            {"id": "m03d77b1", "dropped": ["b1b1b1b1"]},
            ("m03d77b1",),
            "m03d77b1 no longer comes after b1b1b1b1",
        ),
        (
            "escalate",
            [*"look", "enter"],
            "escalate",
            {"id": "m03d77b1"},
            ("look", "m03d77b1"),
            "asked an agent about m03d77b1's stop; kraft view show m03d77b1 follows it",
        ),
        (
            "complete",
            [*"done", "enter"],
            "complete",
            {"id": "m03d77b1"},
            ("done", "m03d77b1"),
            "marked m03d77b1 complete",
        ),
        (
            "cancel",
            [*"moot [/]", "enter"],
            "cancel",
            {"id": "m03d77b1"},
            ("moot [/]", "m03d77b1"),
            "cancelled m03d77b1; its worktree and branch stay "
            "(kraft item abandon m03d77b1 --yes deletes them)",
        ),
        ("complete", ["enter", "escape"], "complete", None, None, None),
    ],
    ids=["skip", "unblock", "escalate", "complete", "cancel", "a-reason-is-required"],
)
def test_a_menu_entry_asks_what_it_needs_and_says_what_the_server_did(
    board, monkeypatch, name, typed, call, answer, sent, said
):
    """Each entry's dialog, then its call, then the CLI's own sentence (the
    sentences moved to `kraft.answers`, so the two cannot drift); a required
    reason left empty sends nothing."""
    _serve(monkeypatch, _detail(ITEM))
    calls = _record(monkeypatch, call, answer)
    monkeypatch.setattr(client, "cancel_preview", _preview)
    console = board([ITEM])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, ITEM["id"])
        await _pick(console, pilot, app, name)
        if typed:
            await console.until(pilot, lambda: isinstance(app.screen, Note))
            await pilot.press(*typed)
        if said:
            await console.until(pilot, lambda: _toasts(app))
        else:
            await console.until(pilot, lambda: app.screen is app.default_screen)
        return calls, _toasts(app)

    assert console.run(scenario, notifications=True) == (
        [sent] if sent else [],
        [said] if said else [],
    )


def test_cancel_says_what_it_stops_and_keeps_above_its_reason(board, monkeypatch):
    """Spec §7.2, §7.8: the cancel's confirm is `cancel_preview`, in the web's
    Cancel card words."""
    _serve(monkeypatch, _detail(ITEM))
    monkeypatch.setattr(client, "cancel_preview", _preview)
    console = board([ITEM])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, ITEM["id"])
        await _pick(console, pilot, app, "cancel")
        await console.until(pilot, lambda: isinstance(app.screen, Note))
        return str(app.screen.query_one("#note-lines").render()).splitlines()

    assert console.run(scenario) == [
        "Stops now: code_review, attempt 2. That attempt's work is lost.",
        "Keeps: branch kraft/x, the worktree until it is archived, 3 findings, threads and "
        "the run log.",
        "Spend: $0.420 stays on the ledger and in today's total.",
        "",
        item_view.REASON,
    ]


SPENT = "the item has already spent $5.1; a cap of $5 would stop it again at once"
PARTLY = "the cap was raised to $9, but the retry was refused: a walk is already running"


@pytest.mark.parametrize(
    ("answers", "typed", "sent", "error", "said"),
    [
        (
            [client.status_error(422, SPENT), {"id": "m03d77b1", "node_id": "implement"}],
            [*"5", "enter", "ctrl+u", *"9", "enter"],
            [(5.0, "m03d77b1"), (9.0, "m03d77b1")],
            client.reason_of(str(client.status_error(422, SPENT))),
            "raised the cap on m03d77b1 to $9; retried at implement",
        ),
        (
            [client.status_error(409, PARTLY)],
            [*"9", "enter"],
            [(9.0, "m03d77b1")],
            None,
            client.reason_of(str(client.status_error(409, PARTLY))),
        ),
        ([], [*"abc", "enter", "escape"], [], "dollars or `none`, not 'abc'", None),
        (
            [{"id": "m03d77b1", "node_id": "implement"}],
            [*"none", "enter"],
            [(None, "m03d77b1")],
            None,
            "raised the cap on m03d77b1 to no cap; retried at implement",
        ),
    ],
    ids=["at-or-below-spend-asks-again", "raised-but-not-retried", "not-dollars", "no-cap"],
)
def test_raise_budget_refuses_in_its_dialog_and_says_a_partial_raise(
    board, monkeypatch, answers, typed, sent, error, said
):
    """Spec §7.2: a cap at or below what was spent is the server's 422, shown
    in the dialog, which asks again; the cap raised but the retry refused is
    the server's sentence, which says both."""
    _serve(monkeypatch, _detail(ITEM))
    calls: list[tuple] = []
    left = list(answers)

    async def raise_budget(usd, work_item_id=None):
        calls.append((usd, work_item_id))
        answer = left.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(client, "raise_budget", raise_budget)
    console = board([ITEM])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, ITEM["id"])
        await _pick(console, pilot, app, "raise_budget")
        await console.until(pilot, lambda: isinstance(app.screen, Note))
        shown = []

        def refused() -> str:
            """What the asked-again dialog says; "" until it is up."""
            note = app.screen
            return str(note.query_one("#note-error").render()) if isinstance(note, Note) else ""

        for key in typed:
            if key in ("ctrl+u", "escape"):  # the dialog asks again, saying why
                await console.until(pilot, refused)
                shown.append(refused())
            await pilot.press(key)
        if said:
            await console.until(pilot, lambda: _toasts(app))
        await console.until(pilot, lambda: app.screen is app.default_screen)
        return calls, shown, _toasts(app)

    assert console.run(scenario, notifications=True) == (
        sent,
        [error] if error else [],
        [said] if said else [],
    )


def test_archive_runs_the_cli_verb_after_the_consoles_confirm(board, monkeypatch, kraft_is):
    """Spec §7.8: archive asks first; then the CLI's own preview, refusals
    and result in the output panel."""
    _serve(monkeypatch, _detail(ITEM))
    kraft_is("import sys; print('ran', *sys.argv[1:])")
    console = board([ITEM])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, ITEM["id"])
        await _pick(console, pilot, app, "archive")
        await console.until(pilot, lambda: isinstance(app.screen, Confirm))
        asked = str(app.screen.query_one("#question").render())
        await pilot.press("shift+tab", "enter")
        await console.until(pilot, lambda: any("ran" in line for line in console.output(app)))
        return asked, [line for line in console.output(app) if line.startswith("ran")]

    assert console.run(scenario) == (
        "kraft item archive m03d77b1 --yes",
        ["ran item archive m03d77b1 --yes"],
    )
