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
    """An act response is small and shapeless; a kv block beats inventing a table."""
    return render.kv([(key, str(value)) for key, value in result.items()]) or "ok"


def repo_scope(ns: argparse.Namespace) -> str | None:
    """Explicit --repo, then the cwd's connected repo, then nothing.

    The one precedence rule every verb shares. `--all` opts out of the implicit
    half, for when the scoping is what surprised you.
    """
    if getattr(ns, "all", False):
        return None
    if getattr(ns, "repo", None):
        return ns.repo
    return asyncio.run(client.resolve_repo())
