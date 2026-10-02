"""Connected repositories, and getting into their worktrees."""

from __future__ import annotations

import argparse
import asyncio

from kraft import client, render
from kraft.cli import common

_REPO_COLUMNS = [
    ("", "here"),
    ("NAME", "name"),
    ("STATE", "state"),
    ("CHAIN", "default_chain_template"),
    ("PATH", "path"),
]

#: Printed by `kraft repo path --shell`. A subprocess cannot change its parent's
#: directory, so the real `cd` has to be a function in the user's shell.
SHELL_WRAPPER = """\
# Add to ~/.zshrc or ~/.bashrc:
kcd() { cd "$(kraft repo path "$@")" || return; }
"""


def _render_repos(rows: list[dict], here: str | None) -> str:
    """The cwd's repo gets a `*` in the first column: it is the answer to "why
    did my last command pick that repo"."""
    shaped = [
        {
            **row,
            "here": "*" if row["path"] == here else " ",
            # The word, not just dim paint: spec D §3 lists enabled as output,
            # and paint alone vanishes into a pipe or under NO_COLOR. Dim moves
            # onto this cell so there is one signal with colour as an accent.
            "state": "enabled"
            if row.get("enabled", True)
            else render.paint("disabled", render.DIM),
        }
        for row in rows
    ]
    return render.table(shaped, _REPO_COLUMNS)


def _cmd_repos(ns: argparse.Namespace) -> None:
    async def run():
        return await client.repos(), await client.resolve_repo()

    rows, here = asyncio.run(run())
    if ns.json or ns.all:
        common.emit(rows, lambda value: _render_repos(value, here), ns.json)
        return
    # Default to managed rows only — the CLI's answer to Settings' collapsed
    # "Detected" section (Kraft-jknn0). Without this, connecting a
    # superproject with thirty submodules dumps thirty auto-connected,
    # never-touched rows into this table alongside the repos a human
    # actually set up.
    visible = [r for r in rows if r.get("managed", True)]
    print(_render_repos(visible, here))
    hidden = len(rows) - len(visible)
    if hidden:
        print(f"\n{hidden} more detected, not managed — kraft repo list --all")


def _cmd_connect(ns: argparse.Namespace) -> None:
    result = asyncio.run(client.ensure_repo(ns.path))
    if ns.json:
        common.emit(result, str, True)
        return
    verb = "already connected" if result.get("already_connected") else "connected"
    print(f"{verb}: {result['path']}")
    if result.get("already_connected"):
        return
    if result.get("test_command"):
        markers = result.get("test_markers")
        source = f" (from {', '.join(markers)})" if markers else ""
        print(f"test command: {result['test_command']}{source}")
    if result.get("setup_command"):
        print(f"setup command: {result['setup_command']}")
    elif result.get("setup_command") is None:
        # Undeclared stops the repo's first work item; "" is a declared none.
        print(
            "setup command: none found (it looks for an npm, yarn, pnpm or uv lockfile, "
            "Cargo.toml or go.mod, and a pyproject.toml without uv.lock gets none); "
            "set `setup_command` in its repos.yaml "
            'entry, `""` if it needs no preparation'
        )
    if result.get("enabled") is False:
        print(
            "saved disabled: no test command found (it looks for a justfile `test` recipe, "
            "uv.lock, package.json, Cargo.toml or go.mod, and a pyproject.toml without "
            "uv.lock gets none); set `test_command` in "
            "its repos.yaml entry, then `enabled: true`"
        )


def _cmd_disconnect(ns: argparse.Namespace) -> None:
    result = asyncio.run(client.disconnect_repo(ns.path))
    if ns.json:
        common.emit(result, str, True)
        return
    print(f"disconnected: {result['path']}")


def _cmd_path(ns: argparse.Namespace) -> None:
    if ns.shell:
        print(SHELL_WRAPPER, end="")
        return
    item = asyncio.run(client.get_work_item(ns.id))
    # exactly one line: this is consumed by cd "$(kraft repo path ID)"
    print(item["worktree_path"])


def _cmd_open(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.open_worktree(ns.id, ns.editor)), common._render_action, ns.json)


def _add_repo(subs, common: argparse.ArgumentParser) -> None:
    """Connected repositories, and getting into their worktrees."""
    repos = subs.add_parser("list", parents=[common], help="connected repositories")
    repos.add_argument(
        "--all",
        action="store_true",
        help="also list auto-connected repos nobody has touched yet",
    )
    repos.set_defaults(func=_cmd_repos)

    connect = subs.add_parser("connect", parents=[common], help="connect a repo (idempotent)")
    connect.add_argument("path", nargs="?", help="default: the current directory")
    connect.set_defaults(func=_cmd_connect)

    disconnect = subs.add_parser(
        "disconnect", parents=[common], help="forget a repo (work items are untouched)"
    )
    disconnect.add_argument("path", nargs="?", help="default: the current directory")
    disconnect.set_defaults(func=_cmd_disconnect)

    # No `--json` (no parent parser): one bare line is the contract that makes
    # `cd "$(kraft repo path ID)"` work; `view show --json` has the payload.
    path = subs.add_parser("path", aliases=["cd"], help="print a work item's worktree path")
    path.add_argument("id", nargs="?")
    path.add_argument("--shell", action="store_true", help="print a shell function that cds")
    path.set_defaults(func=_cmd_path)

    open_p = subs.add_parser("open", parents=[common], help="open the worktree in an editor")
    open_p.add_argument("id", nargs="?")
    open_p.add_argument("--editor", help="code, cursor, zed, obsidian (default: system)")
    open_p.set_defaults(func=_cmd_open)
