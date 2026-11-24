"""Kraft in Herdr: one tab per live work item, its state in Herdr's sidebar.

Stdlib only, and Python 3.10 at the floor. It imports nothing from Kraft: it
talks to Kraft through the `kraft` command and to Herdr through the `herdr`
command, so either can change inside and this keeps working.

Entry points (`herdr-plugin.toml`), each `python3 kraft_herdr.py <name>`:

  start     one-shot startup hook: open the bridge pane unless it is open
  bridge    the long-running loop that keeps the tabs and states current
  item      one item's tab: follow its worker sessions' log (reads KRAFT_ITEM)
  takeover  action: open `kraft item takeover` beside the focused item tab
"""

from __future__ import annotations

import sys

#: Kraft's `display_status` (`kraft.vocab.display.DisplayStatus`) to the state
#: Herdr understands. `report-agent` takes only idle, working, blocked and
#: unknown: Herdr derives "done" itself (idle and not yet seen), so a finished
#: item is idle. `blocked` is Herdr's word for "a person is needed", which is
#: Kraft's `needs_you` and `failed`; Kraft's own `blocked` (waiting on another
#: item) needs nobody, so it is idle.
STATES: dict[str, str] = {
    "running": "working",
    "escalated": "working",
    "needs_you": "blocked",
    "failed": "blocked",
    "paused": "idle",
    "waiting": "idle",
    "queued": "idle",
    "blocked": "idle",
    "done": "idle",
    "cancelled": "idle",
    "archived": "idle",
}

#: The label shown beside the state, where the item's own row has nothing
#: better to say.
_LABELS: dict[str, str] = {
    "running": "running",
    "escalated": "helper agent",
    "needs_you": "needs you",
    "failed": "failed",
    "paused": "paused",
    "waiting": "waiting",
    "queued": "queued",
    "blocked": "after another item",
    "done": "merged",
    "cancelled": "cancelled",
    "archived": "archived",
}

#: A status Kraft sends that this file has never heard of reads as unknown, not
#: as a crash: the bridge outlives Kraft upgrades.
UNKNOWN = "unknown"


def state_for(item: dict) -> tuple[str, str]:
    """`(herdr_state, label)` for one row of `kraft view list --json`."""
    status = item.get("display_status") or ""
    state = STATES.get(status, UNKNOWN)
    label = _LABELS.get(status, status or UNKNOWN)
    if status == "running" and item.get("current_node_id"):
        label = str(item["current_node_id"])
    elif status == "needs_you" and item.get("pending_gate"):
        label = str(item["pending_gate"])
    return state, label


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in ("start", "bridge", "item", "takeover"):
        print(__doc__, file=sys.stderr)
        return 2
    print(f"kraft_herdr: {args[0]} is not implemented yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
