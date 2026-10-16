"""The Chain tab's fixtures, shared by its test files."""

from __future__ import annotations

import json
import re

from textual.widgets import RichLog

from kraft.console import pulse
from support.console_board import (
    CHAIN,
    GATE,
    _detail,
    _event,
    _iso,
    _opened,
    _row,
    _serve,
    _serve_events,
)

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
#: A chain whose last gate sits after the nodes it can send back, as the
#: server's passes need.
REVIEWED = CHAIN | {"nodes": [*CHAIN["nodes"], {"id": "final_review", "kind": "gate"}]}


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


def _running(node: str = "implement", minutes: float = 5, **fields) -> dict:
    return _session(
        node,
        "author",
        1,
        minutes,
        status="running",
        exited_at=None,
        wall_ms=None,
        cost_usd=None,
        **fields,
    )


def _rejected(seq: int, minutes: float, to: str, note: str, gate: str = "final_review") -> dict:
    """A gate's rejection as the server records it: the node it re-enters at
    is in the payload, which `_event` would take for the event's own node."""
    event = _event(seq, "gate_rejected", minutes, gate=gate, note=note)
    event["payload"]["node"] = to
    return event


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


def _flow(*sessions, **fields) -> dict:
    return _done(worker_sessions=list(sessions), **fields)


def _words(app) -> list[str]:
    """The Chain tab's lines, their runs of spaces folded to one."""
    # The spinner's frame moves with the clock: it reads as `*` here.
    lines = [" ".join(line.text.split()) for line in app.query_one("#chain", RichLog).lines]
    return [re.sub(f"[{pulse.FRAMES}] running", "* running", line) for line in lines]


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
