"""The Board's rows and helpers, shared by the Board's test files."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from textual.widgets import OptionList, RichLog

from kraft import client

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


def _toasts(app) -> list[str]:
    """Every toast on screen, as plain text."""
    return [str(toast.render()) for toast in app.screen.query("Toast")]


def _text(app, selector: str) -> str:
    """What a widget draws, as plain text."""
    return str(app.query_one(selector).render())


def _lines(app) -> list[str]:
    """The Board's options, each its first line: the cursor's row opens a
    second, and the panel's edges are no rows of their own."""
    options = app.query_one("#rows", OptionList).options
    edges = ("group:end", "group:top")
    return [o.prompt.plain.split("\n")[0] for o in options if o.id not in edges]


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
    # The read has answered: an item with nothing to say on its Overview draws no line.
    await console.until(
        pilot,
        lambda: (
            app.view.item is not None
            and not any(w.group == "read" and w.is_running for w in app.view.workers)
        ),
    )


def _event(seq: int, kind: str, minutes_ago: float, **payload) -> dict:
    return {
        "seq": seq,
        "work_item_id": GATE["id"],
        "type": kind,
        "node_id": payload.pop("node", None),
        "payload": payload,
        "created_at": _iso(minutes_ago),
    }


def _serve_events(monkeypatch, history: list[dict], hold=None) -> list[dict]:
    """`client.events` over `history`, as the server pages it; returns the reads.
    `hold` maps a `before_seq` to an Event its read waits for."""
    reads: list[dict] = []

    async def events(work_item_id=None, after_seq=0, *, limit=None, before_seq=None):
        if before_seq in (hold or {}):
            await hold[before_seq].wait()
        reads.append({"after_seq": after_seq, "limit": limit, "before_seq": before_seq})
        if before_seq is not None:
            return [e for e in history if e["seq"] < before_seq][-limit:]
        return [e for e in history if e["seq"] > after_seq][:limit]

    monkeypatch.setattr(client, "events", events)
    return reads
