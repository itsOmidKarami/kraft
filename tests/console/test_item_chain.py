"""The Item's Chain tab: a row per node, an open node's passes, sessions, plan
tasks and gate decisions, and the keys that move through them."""

from __future__ import annotations

import json

import pytest
from support.console_board import (
    GATE,
    _detail,
    _event,
    _iso,
    _opened,
    _row,
    _serve,
    _serve_events,
)
from textual.widgets import RichLog

from kraft.console import chain

ID = GATE["id"]
#: The chain the items run, as the server freezes it: `implement` holds the
#: one agent task with no skill, so it is where the plan's tasks are.
FROZEN = json.dumps(
    {
        "chain": {
            "nodes": [
                {"id": "spec", "tasks": [{"kind": "agent", "skill": "kraft:spec"}], "steps": None},
                {"id": "spec_approval", "kind": "gate"},
                {"id": "plan", "tasks": [{"kind": "agent", "skill": "kraft:plan"}], "steps": None},
                {"id": "implement", "tasks": [{"kind": "agent", "skill": None}], "steps": None},
            ]
        }
    }
)


def _session(node: str, task: str, n: int, minutes: float, **fields) -> dict:
    return {
        "id": f"s-{node}-{task}-{n}",
        "node_id": node,
        "hook_point": f"{node}.main.{task}",
        "status": "done",
        "attempt": 1,
        "created_at": _iso(minutes),
        "started_at": _iso(minutes),
        "exited_at": _iso(minutes - 1),
        "wall_ms": 60_000,
        "cost_usd": 0.5,
        **fields,
    }


def _done(**fields) -> dict:
    """A finished item: every node done, what each cost, the plan's tasks."""
    row = _row(ID, "done", 600, current_node_id="implement", materialized_chain=FROZEN)
    by_node = [
        {"node": "spec", "sessions": 1, "cost_usd": 0.4, "cost_complete": True, "wall_ms": 240_000},
        {
            "node": "implement",
            "sessions": 2,
            "cost_usd": 2.41,
            "cost_complete": False,
            "wall_ms": 7_200_000,
        },
    ]
    return _detail(row, usage={"by_node": by_node}, **fields)


def _words(app) -> list[str]:
    """The Chain tab's lines, their runs of spaces folded to one."""
    return [" ".join(line.text.split()) for line in app.query_one("#chain", RichLog).lines]


async def _chain_tab(console, app, pilot) -> None:
    await _opened(console, app, pilot, ID)
    app.view.show_tab("chain")
    await console.until(pilot, lambda: _words(app))
    await pilot.pause()


def _run(board, monkeypatch, detail, events=(), keys=(), size=(120, 40)):
    """The Chain tab of `detail` after `keys`: its lines and the view."""
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, list(events))
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        for key in keys:
            await pilot.press(key)
        return _words(app), app.view

    return console.run(scenario, size=size)


@pytest.mark.parametrize(
    ("detail", "events", "rows"),
    [
        (
            _done(),
            [],
            [
                "✓ spec $0.400 · 4m",
                "✓ approve spec (gate)",
                "✓ plan",
                "▸✓ implement ≥$2.41 · 2h",
            ],
        ),
        (
            _detail(_row(ID, "running", current_node_id="plan")),
            [],
            ["✓ spec", "✓ approve spec (gate)", "▸● plan", "· implement"],
        ),
        (
            _detail(
                _row(
                    ID,
                    "failed",
                    current_node_id="plan",
                    stop={"kind": "failed", "node": "plan", "reason": "tests failed"},
                )
            ),
            [],
            ["✓ spec", "✓ approve spec (gate)", "▸✗ plan", "· implement"],
        ),
        (
            _detail(_row(ID, "running", current_node_id="plan")),
            [_event(4, "node_skipped", 30, node="spec_approval")],
            ["✓ spec", "↷ approve spec (gate)", "▸● plan", "· implement"],
        ),
    ],
    ids=["done-with-spend", "running", "failed", "skipped-gate"],
)
def test_a_row_a_node_with_its_mark_gate_and_spend(board, monkeypatch, detail, events, rows):
    """One row per node of the chain, named as the web names it, a gate marked,
    what a node cost and took on the right when the usage says; the cursor on
    the node the item stands on, else the last one done."""
    lines, _ = _run(board, monkeypatch, detail, events)
    assert lines == rows


def test_a_node_id_is_drawn_as_typed(board, monkeypatch):
    """Markup is off for every server string: a node id with brackets in it."""
    row = _row(
        ID, "running", current_node_id="a[/]b", chain_definition={"nodes": [{"id": "a[/]b"}]}
    )
    lines, _ = _run(board, monkeypatch, _detail(row))
    assert lines == ["▸● a[/]b"]


@pytest.mark.parametrize(
    ("keys", "at"),
    [
        ([], "plan"),
        (["j"], "implement"),
        (["down", "down"], "implement"),
        (["k"], "approve spec (gate)"),
        (["up", "up", "up", "up"], "spec"),
        (["home"], "spec"),
        (["end"], "implement"),
    ],
    ids=[
        "starts-on-current",
        "j",
        "down-stops-at-the-end",
        "k",
        "up-stops-at-the-top",
        "home",
        "end",
    ],
)
def test_the_cursor_moves_by_keys(board, monkeypatch, keys, at):
    """↑ ↓ and `j` `k` move the cursor a node; Home and End go to the ends."""
    detail = _detail(_row(ID, "running", current_node_id="plan"))
    lines, _ = _run(board, monkeypatch, detail, keys=keys)
    assert [line for line in lines if "▸" in line][0].endswith(at)


def _flow(*sessions, **fields) -> dict:
    return _done(worker_sessions=list(sessions), **fields)


def test_enter_opens_a_node_and_left_or_escape_close_it_before_the_tabs_move(board, monkeypatch):
    """Enter opens the node; ← or Esc closes it. On the list ← and → still
    switch tabs, so a closed node's ← leaves the tab and an open one's does not."""
    detail = _flow(_session("implement", "author", 1, 20))
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        seen = []
        for key in ("enter", "left", "enter", "escape", "enter", "right", "left"):
            await pilot.press(key)
            seen.append((key, app.view.chain_open, app.view.tab, app.view.id))
        return seen

    assert console.run(scenario) == [
        ("enter", True, "chain", "item"),
        ("left", False, "chain", "item"),
        ("enter", True, "chain", "item"),
        ("escape", False, "chain", "item"),
        ("enter", True, "chain", "item"),
        ("right", True, "activity", "item"),  # → is a tab key on the list and in a node
        ("left", True, "chain", "item"),
    ]


@pytest.mark.parametrize(
    ("detail", "events", "shown"),
    [
        (
            _flow(
                _session("implement", "author", 1, 40, **{"pass": 1}),
                _session(
                    "implement", "author", 2, 20, **{"pass": 1, "attempt": 2, "cost_usd": None}
                ),
                _session(
                    "implement",
                    "author",
                    3,
                    10,
                    **{
                        "pass": 2,
                        "status": "running",
                        "wall_ms": None,
                        "exited_at": None,
                        "cost_usd": None,
                    },
                ),
                _session("implement", "reviewer", 4, 5),
                node_passes={
                    "implement": [
                        {"pass": 1},
                        {"pass": 2, "reason": "reject", "gate": "spec_approval"},
                    ]
                },
            ),
            [_event(5, "gate_rejected", 15, gate="spec_approval", note="too [/] vague")],
            [
                "done",
                "pass 2 of 2 after changes were requested at approve spec: too [/] vague",
                "▸ author · attempt 1 · running · 10m",
                "pass 1 of 2",
                "author · attempt 2 · done · 1m",
                "author · attempt 1 · done · 1m · $0.500",
                "not tied to a pass",
                "reviewer · attempt 1 · done · 1m · $0.500",
            ],
        ),
        (
            _flow(_session("implement", "author", 1, 40), _session("implement", "author", 2, 20)),
            [],
            [
                "done",
                "▸ author · attempt 1 · done · 1m · $0.500",
                "author · attempt 1 · done · 1m · $0.500",
            ],
        ),
        (_done(), [], ["done"]),
    ],
    ids=["two-passes", "one-pass", "no-session"],
)
def test_an_open_node_lists_its_passes_and_sessions_newest_first(
    board, monkeypatch, detail, events, shown
):
    """A node that ran more than once is told by pass, newest first, each with
    what sent it back and the rejection's note whole; its sessions are
    `task · attempt · status · time · cost`; a field the server left out is
    left out."""
    lines, _ = _run(board, monkeypatch, detail, events, keys=["enter"])
    after = lines[lines.index(next(line for line in lines if "implement" in line)) + 1 :]
    assert [line for line in after if line] == shown


def test_enter_on_a_session_opens_the_log_on_exactly_that_session(board, monkeypatch):
    """↓ moves among the open node's sessions and Enter shows the Log of the
    one under the cursor, not the newest."""
    detail = _flow(
        _session("implement", "author", 1, 40),
        _session("implement", "author", 2, 20),
        _session("implement", "author", 3, 10),
    )
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.press("enter", "down", "enter")
        return app.view.tab, app.view.session_id

    assert console.run(scenario) == ("log", "s-implement-author-2")


def test_an_implement_node_shows_the_plans_tasks_and_those_not_started(board, monkeypatch):
    """The plan's tasks from `progress`: done, the current one, and the rest
    as not started; a task's title is drawn as typed."""
    tasks = [
        {"n": 1, "title": "Parse [/] it", "state": "done", "sha": "abc1234"},
        {"n": 2, "title": "Draw it", "state": "current"},
        {"n": 3, "title": "Ship it", "state": "pending"},
    ]
    detail = _done(progress={"current": 2, "total": 3, "title": "Draw it", "tasks": tasks})
    lines, _ = _run(board, monkeypatch, detail, keys=["enter"])
    assert lines[lines.index("✓ implement ≥$2.41 · 2h") + 1 :] == [
        "done",
        "",
        "Plan tasks · 1 of 3 done",
        "✓ 1. Parse [/] it abc1234",
        "● 2. Draw it",
        "· 3. Ship it not started",
    ]


def test_a_node_that_is_not_the_implementing_one_has_no_tasks(board, monkeypatch):
    """The plan's tasks belong to the node the frozen chain names; the cursor
    on another node shows none."""
    tasks = [{"n": 1, "title": "Parse it", "state": "done"}]
    detail = _done(progress={"current": 1, "total": 1, "title": "Parse it", "tasks": tasks})
    lines, _ = _run(board, monkeypatch, detail, keys=["home", "enter"])
    assert not any("Plan tasks" in line for line in lines)


@pytest.mark.parametrize(
    ("detail", "events", "keys", "shown"),
    [
        (
            _detail(GATE),
            [],
            ["enter"],
            ["waiting for you · a approves it, r requests changes"],
        ),
        (
            _done(),
            [
                _event(3, "gate_approved", 50, gate="spec_approval", by="human"),
                _event(4, "gate_rejected", 20, gate="spec_approval", note="vague [/]"),
                _event(5, "gate_approved", 5, gate="spec_approval", by="agent"),
            ],
            ["home", "down", "enter"],
            [
                "done",
                "",
                "5m passed on its own",
                "20m changes requested: vague [/]",
                "50m approved by you",
            ],
        ),
    ],
    ids=["pending", "decided"],
)
def test_a_gate_node_says_waiting_or_every_decision_in_the_servers_words(
    board, monkeypatch, detail, events, keys, shown
):
    """A pending gate says it waits for you; a decided one lists its decisions
    newest first, a request for changes with its note whole."""
    lines, _ = _run(board, monkeypatch, detail, events, keys=keys)
    after = lines[next(k for k, line in enumerate(lines) if "approve spec" in line) + 1 :]
    assert after[: len(shown)] == shown


def test_nothing_is_read_or_drawn_while_the_tab_is_not_shown(board, monkeypatch):
    """A live event on another tab reads no events for the Chain tab and draws
    nothing into it; showing the tab reads and draws."""
    detail = _done()
    _serve(monkeypatch, detail, calls=[])
    reads = _serve_events(monkeypatch, [_event(1, "node_started", 5, node="spec")])
    console = board([detail])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, ID)
        app.view.rows_changed([detail | {"updated_at": "2026-10-16T00:00:00+00:00"}], [{"seq": 9}])
        await pilot.pause()
        hidden = (list(reads), _words(app))
        app.view.show_tab("chain")
        await console.until(pilot, lambda: _words(app) and reads)
        return hidden, len(reads)

    assert console.run(scenario) == (([], []), 1)


def test_a_live_read_redraws_the_open_node(board, monkeypatch):
    """The tab is read again with the item: a session that ends while a node is
    open shows its new status."""
    running = _session(
        "implement", "author", 1, 5, status="running", exited_at=None, wall_ms=None, cost_usd=None
    )
    detail = _flow(running)
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _chain_tab(console, app, pilot)
        await pilot.press("enter")
        first = [line for line in _words(app) if "author" in line]
        detail["worker_sessions"] = [{**running, "status": "done", "exited_at": _iso(1)}]
        app.view.rows_changed([detail | {"updated_at": "2026-10-16T00:00:00+00:00"}], [{"seq": 9}])
        await console.until(
            pilot, lambda: any("done" in line for line in _words(app) if "author" in line)
        )
        return first

    assert console.run(scenario) == ["▸ author · attempt 1 · running · 5m"]


def test_the_keys_dialog_lists_the_chain_tabs_keys_only_there(board, monkeypatch):
    """`?` lists ⏎ as `open` on the Chain tab, and not on the Overview."""
    detail = _done()
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    console = board([detail])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, ID)
        before = ("⏎", "open") in app.screen_keys()
        app.view.show_tab("chain")
        await pilot.pause()
        return before, ("⏎", "open") in app.screen_keys()

    assert console.run(scenario) == (False, True)


@pytest.mark.parametrize(
    ("item", "expect"),
    [
        ({}, None),
        ({"materialized_chain": None}, None),
        ({"materialized_chain": FROZEN}, "implement"),
        (
            {
                "materialized_chain": json.dumps(
                    {
                        "chain": {
                            "nodes": [
                                {
                                    "id": "v",
                                    "tasks": None,
                                    "steps": [{"tasks": [{"kind": "agent"}]}],
                                }
                            ]
                        }
                    }
                )
            },
            "v",
        ),
    ],
    ids=["no-field", "null", "skill-less-task", "in-a-step"],
)
def test_the_implementing_node_is_the_first_with_an_agent_task_of_no_skill(item, expect):
    assert chain.implementing_node(item) == expect
