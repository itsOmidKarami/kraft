"""The status line under the prompt (`kraft.console.status`): what runs on
its own, today's spend against the cap, and the agents' CLIs; each read from
the server, and left out when its read fails."""

from __future__ import annotations

import contextlib

import pytest
from support.console_board import FILED, GATE, RUNNING, _settled, _text

from kraft import client
from kraft.console import status

BUDGET = {"spent_usd": 4.1, "cap_usd": 50.0}
AGENTS = [
    {"id": "codex", "label": "codex", "executable_found": False},
    {"id": "claude", "label": "claude", "executable_found": True},
]
REFUSED = client.status_error(500, "boom")


def _answers(monkeypatch, budget, agents) -> None:
    async def budget_today():
        if budget is REFUSED:
            raise budget
        return budget

    async def harness_status():
        if agents is REFUSED:
            raise agents
        return agents

    monkeypatch.setattr(client, "budget_today", budget_today)
    monkeypatch.setattr(client, "harness_status", harness_status)


@pytest.mark.parametrize(
    ("rows", "budget", "agents", "size", "line"),
    [
        (
            [RUNNING, GATE],
            BUDGET,
            AGENTS,
            (80, 24),
            "1 running on their own · $4.10 of $50.00 today · claude ● codex ○",
        ),
        (
            [GATE],
            {"spent_usd": 0.5, "cap_usd": None},
            AGENTS,
            (80, 24),
            "nothing running · $0.500 today · claude ● codex ○",
        ),
        ([RUNNING], REFUSED, AGENTS, (80, 24), "1 running on their own · claude ● codex ○"),
        ([RUNNING], BUDGET, REFUSED, (80, 24), "1 running on their own · $4.10 of $50.00 today"),
        ([FILED], REFUSED, REFUSED, (80, 24), "nothing running"),
        (
            [RUNNING],
            BUDGET,
            AGENTS,
            (60, 16),
            "1 running on their own · ? keys",
        ),
        # Bodies of another shape (another server version, a proxy): the part goes, nothing raises.
        (
            [RUNNING],
            BUDGET,
            {"claude": True},
            (80, 24),
            "1 running on their own · $4.10 of $50.00 today",
        ),
        ([RUNNING], [BUDGET], AGENTS, (80, 24), "1 running on their own · claude ● codex ○"),
        (
            [RUNNING],
            {"spent_usd": "1.0"},
            AGENTS,
            (80, 24),
            "1 running on their own · claude ● codex ○",
        ),
    ],
    ids=["all-three", "no-daily-cap", "budget-fails", "agents-fail", "both-fail", "narrow"]
    + ["harness-dict", "budget-list", "budget-str"],
)
def test_the_status_line_says_its_parts_and_leaves_out_what_failed(
    board, monkeypatch, rows, budget, agents, size, line
):
    """Its three parts in the server's answers; a read that fails leaves its
    part out, never a guess; a narrow window lets the agents, then the spend,
    give way, and names `? keys`, its footer hidden."""
    _answers(monkeypatch, budget, agents)
    console = board(rows)

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        # The reads answer after the board: wait for the line, or say what it said.
        with contextlib.suppress(TimeoutError):
            await console.until(pilot, lambda: _text(app, "#status-text") == line, 2.0)
        return _text(app, "#status-text")

    assert console.run(scenario, size=size) == line


@pytest.mark.parametrize(
    ("harnesses", "said"),
    [
        (AGENTS, "claude ● codex ○"),
        ([{"id": f"a{n}", "executable_found": n == 4} for n in range(5)], "a4 ● a0 ○ a1 ○ +2"),
        ([], None),
        (None, None),
        ({"claude": True}, None),
        (7, None),
        ([*AGENTS, "cursor"], None),
        ([{"id": "claude"}], None),
        ([{"id": 3, "executable_found": True}], None),
    ],
    ids=["found-first", "at-most-three", "none-listed", "unread"]
    + ["a-dict", "a-number", "an-entry-not-a-dict", "no-found-flag", "no-name"],
)
def test_the_agents_part_names_found_ones_first_by_mark(harnesses, said):
    """`●` found, `○` missing: the mark's shape carries it, not the colour."""
    part = status.agents_part(harnesses)
    assert (part.plain if part else None) == said


@pytest.mark.parametrize(
    ("budget", "said"),
    [
        (BUDGET, "$4.10 of $50.00 today"),
        ({"spent_usd": 4, "cap_usd": None}, "$4.00 today"),
        ({"spent_usd": 4.1}, "$4.10 today"),
        ({"spent_usd": True, "cap_usd": 50}, None),
        ({"spent_usd": 4.1, "cap_usd": "50"}, None),
        ({"cap_usd": 50}, None),
        ([4.1, 50], None),
        ("$4.10", None),
    ],
    ids=[
        "capped",
        "no-cap",
        "cap-missing",
        "spent-bool",
        "cap-str",
        "spent-missing",
        "a-list",
        "a-str",
    ],
)
def test_the_budget_part_reads_only_numbers_it_can_trust(budget, said):
    assert status.budget_part(budget) == said


def test_the_spinner_turns_only_while_work_is_happening(board, monkeypatch):
    """A queued or waiting item counts as running on its own; only one an
    agent works on now spins."""
    _answers(monkeypatch, REFUSED, REFUSED)
    queued = RUNNING | {"display_status": "queued"}
    console = board([queued])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        spin = app.query_one("#status-spin")
        before = spin.display
        app._live_event([RUNNING], [{"seq": 1}])
        await pilot.pause()
        return before, spin.display, _text(app, "#status-text")

    assert console.run(scenario) == (False, True, "1 running on their own")


def test_a_reconnect_reads_the_spend_again_and_an_ended_session_too(board, monkeypatch):
    """Read on start and on every reconnect; a session's cost lands when it
    ends, so `worker_session_exited` reads the spend again."""
    reads = []

    async def budget_today():
        reads.append("budget")
        return {"spent_usd": len(reads), "cap_usd": None}

    _answers(monkeypatch, None, AGENTS)
    monkeypatch.setattr(client, "budget_today", budget_today)
    console = board([GATE])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await console.until(pilot, lambda: reads)
        first = len(reads)
        app._live_event([GATE], [{"seq": 2, "type": "node_started"}])
        await pilot.pause()
        quiet = len(reads)
        app._live_event([GATE], [{"seq": 3, "type": "worker_session_exited"}])
        await console.until(pilot, lambda: len(reads) > quiet)
        app._live_event([GATE], [])
        await console.until(pilot, lambda: len(reads) > quiet + 1)
        return first, quiet

    first, quiet = console.run(scenario)
    assert quiet == first


def test_with_no_server_the_line_keeps_only_its_key(console, monkeypatch):
    """What the server said last is not said as now while it is gone."""
    _answers(monkeypatch, BUDGET, AGENTS)

    async def gone(**kwargs):
        raise client.transport._unreachable()

    monkeypatch.setattr(client, "board_rows", gone)

    async def scenario(app, pilot):
        await console.until(pilot, lambda: app.default_screen.has_class("-no-server"))
        await pilot.pause()
        return _text(app, "#status-text"), app.query_one("#status-spin").display

    assert console.run(scenario, size=(79, 24)) == ("? keys", False)
