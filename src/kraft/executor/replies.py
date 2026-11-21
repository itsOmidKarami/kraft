"""Kraft's replies to the review bots, posted at the two posting points of a node
with a fix loop (`walk`): each time a measurement of it returns, and when it
would complete.

Each posting point reads what the node's latest triage and its fix cycles wrote,
creates each reply's intent once (found again by its key at every later posting
point, so a wait parked and woken ten times still posts once), and sends it
through the reply record, the gate and the body gate (`adapters.forge.replies`).
It never parks, stops or raises into the walk: what is not sent waits for the
next posting point of the same node run, and what is left when the node completes
is listed in the reply report the brief reads (part 2b).

A node whose review wait names no bot with replies on returns before anything
else: no forge call, no event."""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path

from kraft.adapters.forge.replies.gate import TRIAGE_KINDS
from kraft.worker.worktree_read import read_no_follow


def _result(path) -> dict:
    """A worker's result file, read without following a link; {} when unreadable."""
    try:
        data = json.loads(read_no_follow(Path(path)))
    except (OSError, ValueError, RecursionError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _rows(data: dict, key: str) -> list[dict]:
    raw = data.get(key)
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def _text(row: dict) -> str | None:
    body = row.get("body")
    return body if isinstance(body, str) and body.strip() else None


def triage_replies(result_path, threads: Collection[str]) -> list[dict]:
    """The triage's `replies`, one per collected thread (the first wins), each with
    an allowed `kind` and a body."""
    out: dict[str, dict] = {}
    for r in _rows(_result(result_path), "replies"):
        tid = r.get("thread")
        if (
            isinstance(tid, str)
            and tid in threads
            and tid not in out
            and r.get("kind") in TRIAGE_KINDS
            and _text(r)
        ):
            out[tid] = {"thread": tid, "kind": r["kind"], "body": r["body"]}
    return list(out.values())
