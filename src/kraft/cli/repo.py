"""Connected repositories, and getting into their worktrees."""

from __future__ import annotations

import argparse
import asyncio
import sys

from kraft import client, detect, render
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


#: How many of the commands not proposed `connect` lists, per role.
_ALSO_SHOWN = 4


def _pick(role: str, candidates: list[dict], proposed: str | None) -> str | None:
    """Ask which of the root's `role` candidates to use, when there is a
    choice to make. Enter keeps the proposal; a number picks one; `-` says
    there is none; `y` keeps the proposal and `n` asks again, since a yes or
    no is an answer to the prompt, never a command; anything else is the
    command itself."""
    options: list[dict] = []
    for c in candidates:
        if (
            c["dir"] == ""
            and c["role"] == role
            and c["command"] not in [o["command"] for o in options]
        ):
            options.append(c)
    if len(options) < 2 and proposed is not None:
        return proposed
    print(f"{role} command for the repo root:")
    default = next((i for i, o in enumerate(options, 1) if o["command"] == proposed), None)
    for i, o in enumerate(options, 1):
        mark = "  [proposed]" if i == default else ""
        print(f"  {i}) {o['command']}    from {o['source']}{mark}")
    keep = f"Enter keeps {default}, " if default else ""
    while True:
        answer = input(f"  {keep}a number, - for none, or type a command: ").strip()
        if not answer:
            return proposed
        if answer == "-":
            return ""
        if answer.lower() in ("y", "yes") and proposed is not None:
            return proposed
        if answer.lower() in ("y", "yes", "n", "no"):
            print("  pick one by its number, - for none, or type the command to use")
            continue
        if not answer.isdigit():
            return answer
        if 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]["command"]
        # A number with no option is a typo, never a command called "7".
        print(f"  {answer} is not one of the {len(options)} listed")


def _choose_interactively(path: str | None) -> tuple[str | None, str | None]:
    """(test_command, setup_command) a person picked from the probe's
    candidates for the repo root; None for a role the proposal stands for."""
    resolved = asyncio.run(client.probe_repo(path, detect=False))["path"]
    if resolved in {r["path"] for r in asyncio.run(client.repos())}:
        return None, None  # connect says so; there is nothing to choose
    probed = asyncio.run(client.probe_repo(path))
    root = next((s for s in probed.get("scopes") or () if s["dir"] == ""), None)
    cands = probed.get("candidates") or []
    test = _pick("test", cands, root["test"] if root else None)
    setup = _pick("setup", cands, root["setup"] if root else None)
    test_command = None if root and test == root["test"] else test
    setup_command = None
    if not root or setup != root["setup"]:
        scopes = [s for s in probed.get("scopes") or () if s["dir"]]
        setup_command = detect.combine_setup([{"dir": "", "setup": setup, "test": test}, *scopes])
    return test_command, setup_command


def _say_connected(result: dict) -> None:
    print(f"connected: {result['path']}")
    ref = result.get("read_from")
    if ref and ref.startswith("refs/remotes/origin/"):
        # Work items start from origin, so a commit not pushed yet is not read.
        print(f"  read from {ref.removeprefix('refs/remotes/')}, where work items start")
    elif ref:
        print(f"  read from {ref}")
    elif "read_from" in result:
        print("  read from the working copy: the repo has no commit yet")
    by_dir = {s["dir"]: s for s in result.get("scopes") or ()}
    chosen = [c for c in result.get("candidates") or () if c.get("chosen")]

    def source(d: str, role: str, command: str | None) -> str:
        hit = next(
            (c for c in chosen if (c["dir"], c["role"], c["command"]) == (d, role, command)), None
        )
        return f" (from {hit['source']})" if hit else ""

    root = by_dir.get("", {})
    if result.get("test_command") == "":
        print('test command: "" (no tests)')
    elif root.get("test") and root["test"] == result.get("test_command"):
        print(f"test command: {root['test']}{source('', 'test', root['test'])}")
    elif result.get("test_command"):
        print(f"test command: {result['test_command']}")
    for d, s in by_dir.items():
        if d and s.get("test"):
            print(f"  {d}/: {s['test']}{source(d, 'test', s['test'])}")
    setup = result.get("setup_command")
    if setup:
        root_setup = root.get("setup")
        told = source("", "setup", root_setup) if setup == root_setup else ""
        print(f"setup command: {setup}{told}")
    elif setup == "":
        print('setup command: "" (nothing to prepare)')
    else:
        missing = ", ".join(result.get("missing_setup") or []) or "the repo"
        # Undeclared stops the repo's first work item; "" is a declared none.
        print(
            f"setup command: none found for {missing} (it reads task runners, CI and "
            "lockfiles); pass --setup-command or set `setup_command` in its repos.yaml "
            'entry, `""` if it needs no preparation'
        )
    others = [
        c for c in result.get("candidates") or () if not c.get("chosen") and c["tier"] != "ci"
    ] + [c for c in result.get("candidates") or () if not c.get("chosen") and c["tier"] == "ci"]
    for role in ("test", "setup"):
        rest = [c for c in others if c["role"] == role]
        if rest:
            shown = ", ".join(
                f"{c['dir'] + '/: ' if c['dir'] else ''}{c['command']} ({c['source']})"
                for c in rest[:_ALSO_SHOWN]
            )
            more = (
                f" and {len(rest) - _ALSO_SHOWN} more (--json)" if len(rest) > _ALSO_SHOWN else ""
            )
            print(f"  also found for {role}: {shown}{more}")
    for stop in result.get("stopped") or ():
        print(f"  no test command proposed: {stop['dir']} is {stop['reason']}")
    if result.get("enabled") is False:
        print(
            "saved disabled: no test command found in its task runners, toolchain files or CI; "
            "pass --test-command (--no-tests for a repo with none) or set "
            "`test_command` in its repos.yaml entry, then `enabled: true`"
        )


def _cmd_connect(ns: argparse.Namespace) -> None:
    test_command = "" if ns.no_tests else ns.test_command
    setup_command = ns.setup_command
    interactive = not (ns.json or ns.yes) and sys.stdin.isatty() and sys.stdout.isatty()
    if interactive and test_command is None and setup_command is None:
        test_command, setup_command = _choose_interactively(ns.path)
    result = asyncio.run(
        client.ensure_repo(ns.path, test_command=test_command, setup_command=setup_command)
    )
    if ns.json:
        common.emit(result, str, True)
    elif result.get("already_connected"):
        print(f"already connected: {result['path']}")
        if test_command is not None or setup_command is not None:
            print(
                "  its commands are unchanged: edit its repos.yaml entry "
                "(Templates, Repos) to change them"
            )
    else:
        _say_connected(result)
    if not ns.verify:
        return
    # Imported here, not at the top: it reaches `kraft.builtins`, and every
    # `kraft` invocation imports this module -- the permission hook a worker
    # runs before each tool call included, which must not load the server.
    from kraft.cli import verify as verify_mod

    say = (lambda _line: None) if ns.json else print
    if not verify_mod.verify(result, say=say, timeout_minutes=ns.timeout, on_host=ns.on_host):
        raise SystemExit(1)


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
    connect.add_argument(
        "--test-command", metavar="CMD", help="the repo's test command, instead of the proposal"
    )
    connect.add_argument(
        "--setup-command",
        metavar="CMD",
        help='the command that prepares a fresh checkout, instead of the proposal ("" for none)',
    )
    connect.add_argument(
        "--no-tests",
        action="store_true",
        help='the repo has no tests to run (saves test_command: "")',
    )
    connect.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="take the proposal without asking, even in a terminal",
    )
    connect.add_argument(
        "--verify",
        action="store_true",
        help="then run its setup and test commands once in a throwaway worktree; "
        "exit 1 on a failure",
    )
    connect.add_argument(
        "--timeout",
        type=float,
        default=30,
        metavar="MIN",
        help="--verify: minutes each command may take (default 30)",
    )
    connect.add_argument(
        "--on-host",
        action="store_true",
        help="--verify: run a sandboxed repo's commands on this machine anyway",
    )
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
