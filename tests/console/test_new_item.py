"""New item (spec §7.6): `n` on any view opens a form that files a work item
as `kraft item create` does, keeps a refusal in the form, and on success
opens the item and says what filing said. The create is faked at
`kraft.client`: no item is filed and nothing reaches a real server."""

from __future__ import annotations

import asyncio
from functools import partial

import pytest
from support.console_board import _settled
from textual.widgets import Button, Checkbox, Input, TextArea

from kraft import client
from kraft.console.dialogs import Confirm
from kraft.console.prompt import Prompt
from kraft.console.screens import new_item
from kraft.console.screens.new_item import NewItem

FILED = {"id": "n1n1n1n1", "status": "paused", "title": "t"}
WARNED = {**FILED, "duplicate_warning": "looks like open work item a1 (paused, same title [/])"}
#: What a form holding only a title sends, the console opened in `/r`.
SENT = {
    "title": "Fix the [/] parser",
    "repo": "/r",
    "chain": None,
    "description": None,
    "attachments": None,
    "auto_gate": True,
    "depends_on": None,
    "autostart": False,
}


def _recording(monkeypatch, answer) -> list[dict]:
    """`client.create_work_item`, faked: each call's fields recorded, and
    `answer` raised when it is an error, waited on when it is an Event, else
    returned."""
    sent: list[dict] = []

    async def create_work_item(title, **fields):
        sent.append({"title": title, **fields})
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, asyncio.Event):
            await answer.wait()
            return FILED
        return answer

    monkeypatch.setattr(client, "create_work_item", create_work_item)
    return sent


async def _opened(console, app, pilot) -> NewItem:
    await _settled(console, app, pilot)
    await pilot.press("n")
    await console.until(pilot, lambda: isinstance(app.screen, NewItem))
    return app.screen


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (FILED, ("filed n1n1n1n1; the item is now paused", "information")),
        (
            {**WARNED, "bead_warning": "no beads database"},
            (
                "filed n1n1n1n1; the item is now paused\n"
                "looks like open work item a1 (paused, same title [/])\nno beads database",
                "warning",
            ),
        ),
        (
            {**FILED, "status": "queued", "slots": {"busy": 3, "limit": 3}},
            (
                "filed n1n1n1n1; the item is now queued for a free slot\n"
                "queued n1n1n1n1: 3 of 3 slots are busy. It starts when one frees; "
                "kraft item pause n1n1n1n1 takes it out of the queue",
                "information",
            ),
        ),
        (
            {**FILED, "slots": {"busy": 3, "limit": 3}},
            (
                "filed n1n1n1n1; the item is now paused\n"
                "filed paused: 3 of 3 slots are busy. Start it when one frees: "
                "kraft item resume n1n1n1n1",
                "information",
            ),
        ),
        (
            {**FILED, "status": "blocked", "waiting_on": [{"id": "a1", "status": "active"}]},
            (
                "filed n1n1n1n1; the item is now blocked until the items it comes after complete",
                "information",
            ),
        ),
        (
            {**FILED, "repo_warning": "the repo has no test command"},
            ("filed n1n1n1n1; the item is now paused\nthe repo has no test command", "warning"),
        ),
    ],
    ids=["paused", "warned", "queued", "paused-for-a-slot", "blocked", "repo-cannot-run"],
)
def test_what_filing_says(answer, said):
    """The item and where it stands; for a start with no free slot, the
    CLI's sentence (`kraft.answers`); then each warning in the server's
    words, said as a warning."""
    assert new_item.said(answer) == said


@pytest.mark.parametrize(
    ("fields", "sent"),
    [
        ({}, {}),
        (
            {
                "description": "Why.\nHow [/].",
                "repo": "/src/web",
                "chain": "docs",
                "spec": "s.md",
                "plan": "plans/p.md",
                "after": "a1, b2",
                "auto-gate": False,
                "start": True,
            },
            {
                "description": "Why.\nHow [/].",
                "repo": "/src/web",
                "chain": "docs",
                "attachments": [("spec", "s.md"), ("plan", "plans/p.md")],
                "auto_gate": False,
                "depends_on": ["a1", "b2"],
                "autostart": True,
            },
        ),
        ({"repo": ""}, {"repo": None}),
    ],
    ids=["title-only", "every-field", "repo-emptied"],
)
def test_the_form_files_what_it_holds(board, monkeypatch, tmp_path, fields, sent):
    """As `kraft item create` sends it: the repo the console was opened in
    unless changed (emptied, the client finds the one its directory is in),
    the spec and plan made absolute against its directory, the ids it comes
    after split. Filed, the item opens and a toast says what filing said,
    `[/]` and all."""
    monkeypatch.chdir(tmp_path)
    calls = _recording(monkeypatch, WARNED)
    console = board([], repo="/r")
    want = {**SENT, **sent}
    if want["attachments"]:
        want["attachments"] = [
            {"kind": kind, "path": str((tmp_path / path).resolve())}
            for kind, path in want["attachments"]
        ]

    async def scenario(app, pilot):
        form = await _opened(console, app, pilot)
        form.query_one("#new-title", Input).value = SENT["title"]
        for name, value in fields.items():
            if name == "description":
                form.query_one(TextArea).text = value
            elif isinstance(value, bool):
                form.query_one(f"#new-{name}", Checkbox).value = value
            else:
                form.query_one(f"#new-{name}", Input).value = value
        form.query_one("#new-go", Button).press()
        await console.until(pilot, lambda: app.view.id == "item")
        await console.until(pilot, lambda: app.screen.query("Toast"))
        toasts = [str(toast.render()) for toast in app.screen.query("Toast")]
        return calls, app.view.work_item_id, new_item.said(WARNED)[0] in toasts

    assert console.run(scenario, notifications=True) == ([want], "n1n1n1n1", True)


@pytest.mark.parametrize(
    ("title", "answer", "presses", "said", "sent"),
    [
        ("", FILED, 1, "A title is required.", 0),
        (
            "t",
            client.status_error(422, "spec /x/\x1b[2Ks.md [/] is not inside the repo"),
            1,
            "422: spec /x/\\x1b[2Ks.md [/] is not inside the repo",
            1,
        ),
        (
            "t",
            client.ClientError("no repo: name one explicitly", kind=client.Failure.REFUSED),
            1,
            "no repo: name one explicitly",
            1,
        ),
        ("t", "held", 2, "Filing…", 1),
    ],
    ids=["no-title", "spec-outside-the-repo", "no-repo", "pressed-twice"],
)
def test_a_refusal_shows_in_the_form_and_files_nothing_more(
    board, monkeypatch, title, answer, presses, said, sent
):
    """The form stays open with the reason under its fields, in the server's
    words (spec §7.6); a title is asked for before anything is sent, and a
    create on its way is not sent twice."""
    calls = _recording(monkeypatch, asyncio.Event() if answer == "held" else answer)
    console = board([])

    async def scenario(app, pilot):
        form = await _opened(console, app, pilot)
        form.query_one("#new-title", Input).value = title
        for _ in range(presses):
            form.query_one("#new-go", Button).press()
        await console.until(pilot, lambda: str(form.query_one("#new-said").render()) == said)
        await pilot.pause()
        return isinstance(app.screen, NewItem), len(calls)

    assert console.run(scenario) == (True, sent)


def test_the_form_fits_80x24(board, monkeypatch):
    """Every field and both buttons on an 80x24 screen, the title focused."""
    _recording(monkeypatch, FILED)
    console = board([])

    async def scenario(app, pilot):
        form = await _opened(console, app, pilot)
        await pilot.pause()
        inside = [
            form.region.contains_region(form.query_one(f"#new-{b}", Button).region)
            for b in ("go", "no")
        ]
        rows = [row.size.height for row in form.query(".new-field")]
        return inside, app.focused.id, form.query_one(TextArea).size.height, rows

    assert console.run(scenario) == ([True, True], "new-title", 3, [1, 3, 1, 1, 1, 1, 1])


def test_nothing_clips_at_the_minimum_size(board, monkeypatch):
    """At 60x16 (`MIN_SIZE`) no checkbox label is cut short."""
    _recording(monkeypatch, FILED)
    console = board([])

    async def scenario(app, pilot):
        form = await _opened(console, app, pilot)
        await pilot.pause()
        return [
            box.get_content_width(box.size, box.size)
            <= form.query_one("#new-item").content_region.right - box.region.x
            for box in form.query(Checkbox)
        ]

    assert console.run(scenario, size=(60, 16)) == [True, True]


@pytest.mark.parametrize(
    "then", ["file-again", "esc"], ids=["refused-then-filed-again", "esc-while-filing"]
)
def test_a_refused_form_files_again_and_esc_closes_it_mid_filing(board, monkeypatch, then):
    """After a refusal the same form files again (the 422's whole point);
    Esc closes it while a create is on its way, and nothing more draws."""
    held = asyncio.Event()
    answers = [client.status_error(422, "spec is not inside the repo"), FILED]
    calls: list[str] = []

    async def create_work_item(title, **fields):
        calls.append(title)
        if then == "esc":
            await held.wait()
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(client, "create_work_item", create_work_item)
    console = board([])

    async def scenario(app, pilot):
        form = await _opened(console, app, pilot)
        form.query_one("#new-title", Input).value = "t"
        form.query_one("#new-go", Button).press()
        if then == "esc":
            await console.until(pilot, lambda: calls)
            await pilot.press("escape")
            await console.until(pilot, lambda: app.screen is app.default_screen)
            held.set()
            await pilot.pause()
            return len(calls), app.view.id, len(app.screen.query("Toast"))
        await console.until(pilot, lambda: "422" in str(form.query_one("#new-said").render()))
        form.query_one("#new-go", Button).press()
        await console.until(pilot, lambda: app.view.id == "item")
        return len(calls), app.view.id, 0

    want = (1, "board", 0) if then == "esc" else (2, "item", 0)
    assert console.run(scenario, notifications=True) == want


@pytest.mark.parametrize(
    ("before", "opens", "typed"),
    [
        (None, True, ""),
        ("4", True, ""),
        ("confirm", False, ""),
        ("confirm-action", False, ""),
        ("slash", False, "/n"),
    ],
    ids=[
        "from-the-board",
        "from-repos",
        "not-over-a-confirm",
        "action-not-over-a-confirm",
        "typed-in-the-prompt",
    ],
)
def test_n_opens_the_form_from_any_view_but_never_over_a_dialog(
    board, monkeypatch, before, opens, typed
):
    """Spec §6: `n` everywhere, but a dialog's keys are its own, and in the
    prompt `n` is a letter."""
    _recording(monkeypatch, FILED)
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        if before in ("confirm", "confirm-action"):
            app.run_worker(partial(app.confirm, "kraft admin stop"))
            await console.until(pilot, lambda: isinstance(app.screen, Confirm))
        elif before:
            await pilot.press(before)
        if before == "confirm-action":
            # a direct call, as a palette entry or another caller would make:
            # only the guard stops it
            app.action_new_item()
        else:
            await pilot.press("n")
        if opens:
            await console.until(pilot, lambda: isinstance(app.screen, NewItem))
        else:
            await pilot.pause()
        return isinstance(app.screen, NewItem), app.default_screen.query_one(Prompt).value

    assert console.run(scenario) == (opens, typed)
