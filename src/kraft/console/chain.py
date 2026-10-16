"""The Item's Chain tab: every node of the item's chain, one row each, and the
open node's passes, sessions, plan tasks and gate decisions. Pure: the item's
detail read and its events in, styled lines out. Nothing is drawn that the
server did not give; a missing field is left out."""

from __future__ import annotations

import json
import textwrap

from rich.text import Text

from kraft.console import words

#: How many of the item's newest events the tab reads: its gate decisions and
#: skips, and the notes that sent a node back.
EVENTS = 500
#: A node's mark by its state.
MARKS = {"done": "✓", "current": "●", "failed": "✗", "todo": "·", "skipped": "↷"}
#: What the open node says of its state when it is not the one the item waits on.
STATE_WORDS = {
    "done": "done",
    "failed": "failed",
    "todo": "not reached",
    "skipped": "skipped",
}
#: The cursor's mark, in front of a node's row or a session's line.
CURSOR = "▸"
INDENT = "     "


def implementing_node(item: dict) -> str | None:
    """The node whose plan the item's `progress` follows: the first with an
    agent task of no skill among its own steps (`progress.implementing_nodes`),
    read from the frozen chain. None when the detail carries no chain."""
    raw = item.get("materialized_chain")
    nodes = (json.loads(raw) if isinstance(raw, str) else {}).get("chain", {}).get("nodes") or []
    for node in nodes:
        steps = node.get("steps") or [{"tasks": node.get("tasks")}]
        if any(
            t.get("kind") == "agent" and t.get("skill") is None
            for step in steps
            for t in step.get("tasks") or []
        ):
            return node["id"]
    return None


def node_states(item: dict, events: list[dict]) -> list[str]:
    """Each node's state: `chain_marks`' done, current and to-do, with the one
    a failed item stopped on `failed` and a node a skip passed over `skipped`."""
    skipped = {e.get("node_id") for e in events if e.get("type") == "node_skipped"}
    failed = item.get("display_status") == "failed"
    states = []
    for mark in words.chain_marks(item):
        state = "current" if mark["state"] == "hot" else mark["state"]
        if mark["node"] in skipped and state != "current":
            state = "skipped"
        elif failed and state == "current":
            state = "failed"
        states.append(state)
    return states


def sessions_of(item: dict, node_id: str) -> list[dict]:
    """The node's sessions as the tab lists them: the newest pass first, the
    newest first inside it, and those that belong to no pass last."""
    mine = [s for s in item.get("worker_sessions") or [] if s.get("node_id") == node_id]
    mine.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return sorted(mine, key=lambda s: (s.get("pass") is None, -(s.get("pass") or 0)))


def first_cursor(item: dict, states: list[str]) -> int:
    """Where the cursor starts: the node the item stands on, else the last done."""
    at = next((k for k, s in enumerate(states) if s in ("current", "failed")), None)
    if at is None:
        at = max((k for k, s in enumerate(states) if s == "done"), default=0)
    return at


def _spent(item: dict, node_id: str) -> str:
    """What the node cost and took, from the item's usage; "" when unknown."""
    row = next(
        (u for u in (item.get("usage") or {}).get("by_node") or [] if u.get("node") == node_id),
        None,
    )
    if not row or not row.get("sessions"):
        return ""
    cost = row.get("cost_usd")
    money = ("" if row.get("cost_complete", True) else "≥") + words.usd(cost) if cost else ""
    took = words.elapsed(row["wall_ms"] / 1000) if row.get("wall_ms") else ""
    return " · ".join(part for part in (money, took) if part)


def _session_line(session: dict, now: float) -> str:
    """`task · attempt N · status · time · cost`, what the server gave."""
    wall = session.get("wall_ms")
    started = words._seconds(session.get("started_at"))
    if wall is None and started is not None and session.get("exited_at") is None:
        wall = (now - started) * 1000  # still running: the time so far
    cost = session.get("cost_usd")
    parts = [
        words.task_name(session.get("hook_point") or "") or "session",
        f"attempt {session['attempt']}" if session.get("attempt") is not None else "",
        "skipped" if session.get("skipped") else session.get("status") or "",
        words.elapsed(wall / 1000) if wall else "",
        ("~" if session.get("cost_estimated") else "") + words.usd(cost) if cost else "",
    ]
    return " · ".join(part for part in parts if part)


def _why(p: dict, nodes: list[dict], note: str | None) -> str:
    """What sent the chain back to start pass `p`, as the web words it, with
    the note a request-changes carried."""
    gate = f" at {words.gate_name(nodes, p['gate'])}" if p.get("gate") else ""
    said = {
        "reject": f"after changes were requested{gate}",
        "fixed": f"after a fix{gate}",
        "retry": "after a retry",
        "base_change": "after a base change",
    }
    text = said.get(p.get("reason"), "started over" if p["pass"] > 1 else "")
    return f"{text}: {note}" if text and note else text


def _notes(item: dict, node_id: str, events: list[dict]) -> dict[int, str]:
    """The note of the rejection behind each pass of the node, by pass number.
    Matched from the newest back, so a window that cut the oldest events
    still names the right ones."""
    sent = [
        p
        for p in (item.get("node_passes") or {}).get(node_id) or []
        if p.get("reason") in ("reject", "fixed") and p.get("gate")
    ]
    given: dict[str, list[str | None]] = {}
    for e in events:
        if e.get("type") == "gate_rejected":
            payload = e.get("payload") or {}
            given.setdefault(payload.get("gate"), []).append(payload.get("note"))
    notes = {}
    for p in reversed(sent):
        left = given.get(p["gate"]) or []
        if left and (note := left.pop()):
            notes[p["pass"]] = str(note)
    return notes


def _decisions(node_id: str, events: list[dict], now: float) -> list[str]:
    """The gate's decisions, newest first, in the words of the Activity."""
    lines = []
    for e in reversed(events):
        p = e.get("payload") or {}
        if e.get("type") in ("gate_approved", "gate_rejected") and p.get("gate") == node_id:
            if e["type"] == "gate_approved":
                said = (
                    "passed on its own" if p.get("by") in ("agent", "kraft") else "approved by you"
                )
            else:
                said = "changes requested" + (f": {p['note']}" if p.get("note") else "")
        elif e.get("type") == "node_skipped" and e.get("node_id") == node_id:
            said = "skipped"
        else:
            continue
        lines.append(f"{words.event_age(e.get('created_at'), now)}  {said}")
    return lines


def _state_line(item: dict, node_id: str, state: str, is_gate: bool) -> str:
    if state == "current" and is_gate and item.get("pending_gate") == node_id:
        return "waiting for you · a approves it, r requests changes"
    if state == "current":
        return words.status_words(item).lower()
    if state == "failed":
        reason = (item.get("stop") or {}).get("reason")
        return f"failed: {reason}" if reason else "failed"
    return STATE_WORDS[state]


def node_detail(item, events, k, state, at, now, colour, width=80) -> tuple[list[Text], int]:
    """The open node `k`'s lines, indented under its row, and which of them
    the session cursor `at` sits on (-1: none)."""
    nodes = words.nodes_of(item) or []
    node = nodes[k]
    node_id, muted = node["id"], colour["kraft-muted"]
    out: list[Text] = []

    def add(text: str, style: str = "") -> None:
        # A long line wraps under its own start, not at the pane's edge.
        for line in textwrap.wrap(
            text.strip() and INDENT + text, max(width, 20), subsequent_indent=INDENT + "  "
        ) or [INDENT]:
            out.append(Text(line, style=style))

    passes = (item.get("node_passes") or {}).get(node_id) or []
    add(_state_line(item, node_id, state, node.get("kind") == "gate"), "bold")
    notes = _notes(item, node_id, events)
    sessions = sessions_of(item, node_id)
    cursor_line, shown = -1, None
    for n, session in enumerate(sessions):
        if len(passes) > 1 and session.get("pass") != shown:
            shown = session.get("pass")
            out.append(Text(""))
            if shown is None:
                add("not tied to a pass", muted)
            else:
                p = next((p for p in passes if p["pass"] == shown), {"pass": shown})
                add(
                    f"pass {shown} of {len(passes)} {_why(p, nodes, notes.get(shown))}".rstrip(),
                    muted,
                )
        elif n == 0:
            out.append(Text(""))
        lit = n == at
        cursor_line = len(out) if lit else cursor_line
        add(
            f"{CURSOR if lit else ' '} {_session_line(session, now)}",
            f"bold {colour['kraft-accent']}" if lit else "",
        )
    if node.get("kind") == "gate":
        decided = _decisions(node_id, events, now)
        if decided:
            out.append(Text(""))
            for line in decided:
                add(line)
    tasks = (item.get("progress") or {}).get("tasks") or []
    if tasks and node_id == implementing_node(item):
        done = sum(t.get("state") == "done" for t in tasks)
        out += [Text(""), Text(INDENT + f"Plan tasks · {done} of {len(tasks)} done", style=muted)]
        for t in tasks:
            state_mark, style = {
                "done": ("✓", muted),
                "current": ("●", f"bold {colour['kraft-accent']}"),
            }.get(t.get("state"), ("·", ""))
            sha = f"  {t['sha']}" if t.get("sha") else ""
            late = "  not started" if t.get("state") == "pending" else ""
            add(f"  {state_mark} {t.get('n')}. {t.get('title') or ''}{sha}{late}", style)
    return out, cursor_line


def chain_lines(item, events, cursor, opened, session_at, width, now, colour):
    """The tab's lines: a row per node, the open node's detail under its row.
    Returns `(lines, line the cursor is on, first line of the open node)`."""
    nodes = words.nodes_of(item) or []
    states = node_states(item, events)
    out: list[Text] = []
    on = top = 0
    for k, node in enumerate(nodes):
        lit = k == cursor
        state = states[k]
        cursor_mark = CURSOR if lit and not opened else " "
        left = f" {cursor_mark}{MARKS[state]} {words.name_of(nodes, node['id'])}"
        left += " (gate)" if node.get("kind") == "gate" else ""
        right = _spent(item, node["id"])
        pad = max(2, width - len(left) - len(right) - 1) if right else 0
        style = {"current": f"bold {colour['kraft-accent']}", "failed": colour["error"]}.get(state)
        style = style or (colour["kraft-muted"] if state in ("done", "skipped") else "")
        if lit:
            style = f"bold {style}".strip() + f" on {colour['surface']}"
        row = Text(left, style=style)
        row.append(" " * pad + right, style=colour["kraft-muted"])
        if lit:
            on = top = len(out)
        out.append(row)
        if lit and opened:
            lines, at = node_detail(item, events, k, state, session_at, now, colour, width)
            on = top + 1 + at if at >= 0 else top
            out += lines
    return out, on, top
