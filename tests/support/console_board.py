"""The Board's rows and helpers, shared by the Board's test files."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from textual.widgets import OptionList, RichLog

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


QUESTION = _row(
    "q1q1q1q1",
    "needs_you",
    stop={"kind": "question", "node": "implement", "reason": "needs_context: keep [/] in titles?"},
    actions=["resume", "steer"],
)
PAUSED = _row("p1p1p1p1", "paused", 12, actions=["resume"])


def _detail(row: dict, **fields) -> dict:
    """The detail read (`get_work_item(full=True)`) of `row`."""
    return {**row, "status": "active", "worker_sessions": [], "dependencies": [], **fields}
