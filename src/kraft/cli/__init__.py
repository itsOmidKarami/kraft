"""The `kraft` command: seed a home if there isn't one, then serve.

`python -m kraft` and the installed console script are the same code path, so a
checkout and an install can only ever differ in where their paths point.
"""

from __future__ import annotations

import argparse
import sys

import argcomplete

from kraft.cli import admin, common, item, repo, view
from kraft.cli.admin import *  # noqa: F403
from kraft.cli.common import *  # noqa: F403
from kraft.cli.item import *  # noqa: F403
from kraft.cli.repo import *  # noqa: F403
from kraft.cli.view import *  # noqa: F403


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kraft",
        description="Kraft: run it with no arguments to serve; subcommands talk to a server.",
    )
    # An install can silently fall behind the checkout it was built from — the
    # build that predated this file's argparse answered every subcommand by
    # serving, and nothing said so (Kraft-krd).
    parser.add_argument("--version", action="version", version=f"kraft {admin._version()}")
    subs = parser.add_subparsers(dest="group", required=True)
    common_parser = common.json_flag()

    for name, help_text, adder in _GROUPS:
        group = subs.add_parser(name, help=help_text)
        adder(group.add_subparsers(dest="verb", required=True), common_parser)
    return parser


#: The four groups, in help order. `build_parser` walks this, so adding a group
#: is one tuple rather than a second place that has to agree with the first.
_GROUPS = (
    ("item", "act on a work item", item._add_item),
    ("view", "read a work item, or the board", view._add_view),
    ("repo", "connected repositories and their worktrees", repo._add_repo),
    ("admin", "this machine's server and its install", admin._add_admin),
)


#: The verbs that were top-level before the groups, and where each one went.
#: Data, not aliases: nothing here dispatches. It is checked before argparse
#: sees argv because argparse's own "invalid choice" prints the four group
#: names and says nothing about where `list` has gone.
MOVED = {
    "create": "item create",
    "approve": "item approve",
    "reject": "item reject",
    "pause": "item pause",
    "resume": "item resume",
    "retry": "item retry",
    "abandon": "item abandon",
    "list": "view list",
    "show": "view show",
    "search": "view search",
    "logs": "view logs",
    "events": "view events",
    "watch": "view watch",
    "diff": "view diff",
    "docs": "view docs",
    "doc": "view doc",
    "artifact": "view artifact",
    "repos": "repo list",
    "connect": "repo connect",
    "disconnect": "repo disconnect",
    "path": "repo path",
    "cd": "repo cd",
    "open": "repo open",
    "serve": "admin start",
    "health": "admin health",
    "doctor": "admin doctor",
    "reindex": "admin reindex",
    "init": "admin init",
    "mcp": "admin mcp",
}


def main(argv: list[str] | None = None) -> None:
    """Bare `kraft` serves, as it always has. Subcommands are the two front doors.

    The zero-argument check happens before argparse sees anything: serving must
    stay the default, and argparse would print usage for an empty argv. (This
    file used to avoid argparse entirely, on the grounds that one string compare
    was the whole dispatch. That stopped being true at eight verbs with flags.)
    """
    args = sys.argv[1:] if argv is None else list(argv)
    parser = build_parser()
    # No-ops unless COMP_LINE etc are set, i.e. unless a shell completion
    # script (see `register-python-argcomplete kraft`) is asking for
    # completions; in that case it prints them and exits, never reaching the
    # code below. Built before the zero-arg short-circuit so `kraft <TAB>`
    # completes rather than serving.
    argcomplete.autocomplete(parser)
    if not args:
        admin._serve()
        return
    if args[0] in MOVED:
        print(f"kraft: '{args[0]}' moved to `kraft {MOVED[args[0]]}`", file=sys.stderr)
        raise SystemExit(2)
    ns = parser.parse_args(args)
    try:
        ns.func(ns)
    except (ValueError, PermissionError) as exc:
        # ValueError is what client.py raises for every API and context failure;
        # PermissionError is the worker self-action guard.
        print(f"kraft: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        raise SystemExit(130) from None
