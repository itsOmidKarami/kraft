"""Shared plumbing every verb group reaches for: JSON-or-table output, the
`--json` flag parser, and the repo scoping rule every verb shares."""

from __future__ import annotations

import argparse
import asyncio
import json

from kraft import client, render


def emit(value, renderer, as_json: bool) -> None:
    """One place decides human-or-JSON, so no verb can forget the contract.

    `--json` prints exactly what `client.py` returned. The CLI must never become
    a second definition of what a work item is — the MCP door reads the same
    value, and the two are only guaranteed identical if neither reshapes.
    """
    if as_json:
        print(json.dumps(value, indent=2))
    else:
        print(renderer(value))


def json_flag() -> argparse.ArgumentParser:
    """A parent parser so `--json` works on every verb without eight copies."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json", action="store_true", help="print the raw API payload instead of a table"
    )
    return parent


def _render_action(result: dict) -> str:
    """An act response is small and shapeless; a kv block beats inventing a table.
    An object or a list reads as the JSON `--json` prints, and nothing as "-",
    never Python's `None` or `{'a': 1}`."""
    return render.kv([(key, _action_value(value)) for key, value in result.items()]) or "ok"


def _action_value(value) -> str:
    if isinstance(value, dict | list):
        return json.dumps(value)
    return "-" if value is None else str(value)


def _is_item_row(result: object) -> bool:
    """A whole `work_items` row, which the gate and lifecycle routes hand back
    (`deps.work_item_answer`): some 30 KB with its frozen chain, which a person
    who typed `kraft item approve` never asked to read (R7a-05)."""
    return isinstance(result, dict) and "chain_definition" in result and "status" in result


#: What a row's `status` reads as, after "the item is now".
_NOW = {
    "active": "running",
    "paused": "paused",
    "completed": "complete",
    # Cancel and abandon both store `abandoned`: only the verb that ended it can tell.
    "abandoned": "ended",
    "waiting": "waiting on something outside Kraft",
    "rate_limited": "waiting for the agent's rate limit to reset",
}


def item_now(row: dict) -> str:
    """Where the item stands, as the end of a sentence."""
    if row["status"] == "needs_human":
        return f"stopped for a person: kraft view show {row['id']} says why"
    return _NOW.get(row["status"], row["status"])


def item_action(done, *, status: bool = True):
    """The renderer for a verb whose answer may be the item's whole row: one
    line saying what was done (`done`, a format string taking `{id}`, or a
    function of the answer) and, unless `status` is off, what the item is now.
    Any other answer is small, and reads as `_render_action`'s kv block.
    `--json` is untouched: `emit` prints the payload itself."""

    def render(result: dict) -> str:
        if not _is_item_row(result):
            return _render_action(result)
        head = done(result) if callable(done) else done.format(id=result["id"])
        return f"{head}; the item is now {item_now(result)}" if status else head

    return render


def repo_scope(ns: argparse.Namespace) -> str | None:
    """Explicit --repo, then the cwd's connected repo, then nothing.

    The one precedence rule every verb shares. `--all` opts out of the implicit
    half, for when the scoping is what surprised you.
    """
    if getattr(ns, "all", False):
        return None
    if getattr(ns, "repo", None):
        return client.absolute_path(ns.repo)
    return asyncio.run(client.resolve_repo())
