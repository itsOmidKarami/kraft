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


def _print(line: str) -> None:
    """`print`, with every character a terminal would act on written out as
    its escape: a command or a path read from a repository is shown as it
    is, never obeyed (an ANSI sequence in a CI line could otherwise show
    one command while another is saved)."""
    print("".join(c if c.isprintable() else c.encode("unicode_escape").decode() for c in line))


def _pick(
    role: str, candidates: list[dict], proposed: str | None, nested: list[str] = ()
) -> str | None:
    """Ask which of the root's `role` candidates to use, when there is a
    choice to make. Enter keeps the proposal; a number picks one; `-` says
    there is none; `y` keeps the proposal and `n` asks again, since a yes or
    no is an answer to the prompt, never a command; anything else is the
    command itself. `nested`: directories below the root with their own
    `role` command, which a root with no test needs no question about."""
    options: list[dict] = []
    for c in candidates:
        if (
            c["dir"] == ""
            and c["role"] == role
            and c["command"] not in [o["command"] for o in options]
        ):
            options.append(c)
    if proposed and proposed not in [o["command"] for o in options]:
        # Several candidates at once (`npm ci && mix deps.get`, a test run
        # per family): offered whole, so picking one does not drop the rest.
        joined = " + ".join(
            c["source"] for c in candidates if c["chosen"] and c["role"] == role and c["dir"] == ""
        )
        options.insert(0, {"command": proposed, "source": joined or "the proposal"})
    if len(options) < 2 and proposed is not None:
        return proposed
    if not options and role == "test" and nested:
        return proposed  # its scopes are the repo's tests
    where = "the root"
    if nested:
        own = "have their own" if len(nested) > 1 else "has its own"
        where += f" ({', '.join(f'{d}/' for d in nested)} {own})"
    none = "- for no tests" if role == "test" else "- for none"
    if options:
        _print(f"{role} command for {where}:")
        default = next((i for i, o in enumerate(options, 1) if o["command"] == proposed), None)
        for i, o in enumerate(options, 1):
            mark = "  [proposed]" if i == default else ""
            _print(f"  {i}) {o['command']}    from {o['source']}{mark}")
        keep = f"Enter keeps {default}, " if default else ""
        prompt = f"  {keep}a number, {none}, or type a command: "
    else:
        _print(f"no {role} command found for {where}")
        unset = "Enter leaves it unset" + (" (saved disabled)" if role == "test" else "")
        prompt = f"  type one, {none}, or {unset}: "
    while True:
        answer = input(prompt).strip()
        if not answer:
            return proposed
        if answer == "-" and role == "test":
            # Saved enabled, and every work item passes its test step: a
            # decision, not a way to put the question off.
            sure = input(
                "  no tests: the repo is enabled and its work items pass verification "
                "without running any. Save it that way? [y/N] "
            )
            if sure.strip().lower() in ("y", "yes"):
                return ""
            continue
        if answer == "-":
            return ""
        if answer.lower() in ("y", "yes") and proposed is not None:
            return proposed
        if answer.lower() in ("y", "yes", "n", "no"):
            pick = "pick one by its number, " if options else ""
            _print(f"  {pick}{none}, or type the command to use")
            continue
        if not answer.isdigit():
            return answer
        if 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]["command"]
        # A number with no option is a typo, never a command called "7".
        _print(f"  {answer} is not one of the {len(options)} listed")


def _choose_interactively(path: str | None) -> tuple[str | None, str | None]:
    """(test_command, setup_command) a person picked from the probe's
    candidates for the repo root; None for a role the proposal stands for."""
    resolved = asyncio.run(client.probe_repo(path, detect=False))["path"]
    if resolved in {r["path"] for r in asyncio.run(client.repos())}:
        return None, None  # connect says so; there is nothing to choose
    probed = asyncio.run(client.probe_repo(path))
    if probed.get("read_from") is None:
        return None, None  # no commit: connect refuses it, so there is nothing to choose
    root = next((s for s in probed.get("scopes") or () if s["dir"] == ""), None)
    cands = probed.get("candidates") or []
    below = [s for s in probed.get("scopes") or () if s["dir"]]
    test = _pick(
        "test", cands, root["test"] if root else None, [s["dir"] for s in below if s["test"]]
    )
    setup = _pick(
        "setup", cands, root["setup"] if root else None, [s["dir"] for s in below if s["setup"]]
    )
    test_command = None if root and test == root["test"] else test
    setup_command = None
    if not root or setup != root["setup"]:
        scopes = [s for s in probed.get("scopes") or () if s["dir"]]
        setup_command = detect.combine_setup([{"dir": "", "setup": setup, "test": test}, *scopes])
    return test_command, setup_command


def _runs(saved: str, c: dict) -> bool:
    """Whether `saved`, a command as saved, already runs candidate `c`:
    such a candidate is no alternative to list."""
    if c["dir"]:
        return f"cd {c['dir']} && {c['command']}" in saved
    command = c["command"]
    return saved == command or saved.startswith(f"{command} && ") or f" && {command}" in saved


def _say_connected(result: dict) -> None:
    _print(f"connected: {result['path']}")
    ref = result.get("read_from")
    if ref and ref.startswith("refs/remotes/origin/"):
        # Work items start from origin, so a commit not pushed yet is not read.
        _print(f"  read from {ref.removeprefix('refs/remotes/')}, where work items start")
    elif ref:
        _print(f"  read from {ref}")
    _say_tests(result)
    _say_setup(result)
    _say_rest(result)
    if result.get("enabled") is False:
        _print(
            "saved disabled: no test command found in its task runners, toolchain files or CI; "
            "pass --test-command (--no-tests for a repo with none) or set "
            "`test_command` in its repos.yaml entry, then `enabled: true`"
        )


def _source(result: dict, d: str, role: str, command: str | None) -> str:
    """ " (from …)" for `command`, directory `d`'s `role` command: the
    candidate it is (one picked at the prompt included), else the chosen
    ones it combines (a test run per family)."""
    here = [c for c in result.get("candidates") or () if (c["dir"], c["role"]) == (d, role)]
    hit = next((c for c in here if c.get("chosen") and c["command"] == command), None)
    hit = hit or next((c for c in here if c["command"] == command), None)
    if hit:
        return f" (from {hit['source']})"
    parts = [c["source"] for c in here if c.get("chosen")]
    return f" (from {' + '.join(parts)})" if parts else ""


def _say_tests(result: dict) -> None:
    """The test command, or, with directories below the root tested on their
    own, every scope: a monorepo's first scope is not the repo's command."""
    by_dir = {s["dir"]: s for s in result.get("scopes") or ()}
    root = by_dir.get("", {})
    nested = [(d, s["test"]) for d, s in by_dir.items() if d and s.get("test")]
    test = result.get("test_command")
    if nested:
        _print("test scopes:")
        if root.get("test"):
            _print(f"  the root: {root['test']}{_source(result, '', 'test', root['test'])}")
        elif test == "":
            _print('  the root: "" (no tests)')
    elif test == "":
        _print('test command: "" (no tests)')
    elif test:
        _print(f"test command: {test}{_source(result, '', 'test', test)}")
    for d, command in nested:
        _print(f"  {d}/: {command}{_source(result, d, 'test', command)}")
    if test == "" and result.get("enabled"):
        _print("  saved enabled: its work items pass verification without running a test")


def _say_setup(result: dict) -> None:
    chosen = [c for c in result.get("candidates") or () if c.get("chosen")]
    setup = result.get("setup_command")
    if setup:
        sources = [
            f"{c['dir'] + '/: ' if c['dir'] else ''}{c['source']}"
            for c in chosen
            if c["role"] == "setup"
        ]
        _print(f"setup command: {setup}" + (f" (from {' + '.join(sources)})" if sources else ""))
    elif setup == "":
        _print('setup command: "" (nothing to prepare)')
    else:
        missing = [d if d != "." else "the root" for d in result.get("missing_setup") or ()]
        found = [
            detect.in_dir(c["dir"], c["command"], shell=True)
            for c in chosen
            if c["role"] == "setup"
        ]
        # Undeclared stops the repo's first work item; "" is a declared none.
        why = (
            f"{' && '.join(found)} found, but {', '.join(missing)} "
            + ("have nothing to prepare them" if len(missing) > 1 else "has nothing to prepare it")
            if found and missing
            else f"none found for {', '.join(missing) or 'the repo'}"
        )
        _print(
            f"setup command: {why}; pass --setup-command or set `setup_command` in its "
            'repos.yaml entry, `""` if it needs no preparation, or tick No setup needed '
            "under Templates › Repos"
        )


def _say_rest(result: dict, *, tested: bool | None = None) -> None:
    """What else the evidence held: the commands not proposed, the programs
    missing here, and why a directory proposes no test command -- not the
    root's when it has one anyway (`tested`, by default whether `result`
    names one): it was given, and there is nothing left to propose."""
    if tested is None:
        tested = result.get("test_command") is not None
    saved = [result.get("test_command") or "", result.get("setup_command") or ""]
    saved += [s["command"] for s in result.get("test_scopes") or ()]
    others = [
        c
        for tier_ci in (False, True)
        for c in result.get("candidates") or ()
        if not c.get("chosen")
        and (c["tier"] == "ci") == tier_ci
        and not any(_runs(command, c) for command in saved if command)
    ]
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
            _print(f"  also found for {role}: {shown}{more}")
    for missing in result.get("missing_tools") or ():
        where = "the root" if missing["dir"] == "." else f"{missing['dir']}/"
        _print(
            f"  not installed here: {missing['tool']}, which {where}'s commands run; "
            "install it, or a work item fails on it (--verify shows how)"
        )
    if result.get("probe_failed"):
        _print(f"  saved with the commands given; the probe failed: {result['probe_failed']}")
    for stop in result.get("stopped") or ():
        if stop["dir"] == "." and tested:
            continue
        where = "the root" if stop["dir"] == "." else f"{stop['dir']}/"
        _print(f"  no test command proposed: {where} is {stop['reason']}")


def _gains(stored: dict, probed: dict, test_command: str | None, setup: str | None) -> dict:
    """What connecting a connected repo again saves: only what its entry
    leaves undecided -- a test command where it has neither one nor test
    scopes, a setup command where none is declared -- from the commands
    given, else from the probe. A repo saved disabled for want of a test
    command is enabled once it has one. Anything decided stays as it is."""
    patch: dict = {}
    if stored.get("test_command") is None and not stored.get("test_scopes"):
        test = test_command if test_command is not None else probed.get("test_command")
        if test is not None:
            patch["test_command"] = test
            scopes = probed.get("test_scopes") or []
            if any(s.get("paths") != ["**"] for s in scopes):
                patch["test_scopes"] = scopes
    if stored.get("setup_command") is None:
        found = setup if setup is not None else probed.get("setup_command")
        if found is not None:
            patch["setup_command"] = found
    if "test_command" in patch and stored.get("enabled") is False:
        patch["enabled"] = True
    return patch


def _connect_again(
    stored: dict, test_command: str | None, setup_command: str | None, *, ask: bool, say: bool
) -> dict:
    """Connect an already-connected repo again: probe it for what its entry
    leaves undecided (`_gains`), say what that would save, and save it --
    after a yes, when `ask`. A lockless project told to commit a lockfile
    and connect again gets its commands this way."""
    out = _print if say else (lambda _line: None)
    path = stored["path"]
    needs_tests = stored.get("test_command") is None and not stored.get("test_scopes")
    needs_setup = stored.get("setup_command") is None
    out(f"already connected: {path}")
    if (test_command is not None and not needs_tests) or (
        setup_command is not None and not needs_setup
    ):
        out(
            "  its commands are unchanged: edit its repos.yaml entry "
            "(Templates › Repos) to change them"
        )
    if not needs_tests and not needs_setup:
        if stored.get("enabled") is False:
            out(
                "  it is disabled: enable it under Templates › Repos "
                "(`enabled: true` in repos.yaml)"
            )
        return stored
    try:
        probed = asyncio.run(
            client.probe_repo(path, test_command=test_command if needs_tests else None)
        )
    except ValueError as exc:
        out(f"  could not probe it again: {exc}")
        return stored
    patch = _gains(stored, probed, test_command, setup_command)
    if say:
        _print("  probed again, for what its entry leaves undecided:")
        if needs_tests:
            # What is saved: the probe answers `test_command: None` for `""`.
            test = patch.get("test_command", probed.get("test_command"))
            enabled = patch.get("enabled", stored.get("enabled"))
            _say_tests({**probed, "test_command": test, "enabled": enabled})
        if needs_setup and setup_command is not None:
            # What is saved, not the probe's advice about a command it lacks.
            shown = (
                f'"{setup_command}" (nothing to prepare)' if setup_command == "" else setup_command
            )
            _print(f"setup command: {shown} (given)")
        elif needs_setup:
            _say_setup(probed)
        tested = patch.get("test_command", stored.get("test_command")) is not None
        _say_rest(probed, tested=tested)
    if not patch:
        out("  nothing new to save")
        return stored
    if ask:
        answer = input(f"  save {', '.join(_FIELDS[k] for k in patch)}? [Y/n] ")
        if answer.strip().lower() not in ("", "y", "yes"):
            out("  nothing saved")
            return stored
    saved = asyncio.run(client.update_repo(path, patch))
    out(f"saved {', '.join(_FIELDS[k] for k in patch)}")
    return {**saved, "already_connected": True, "updated": sorted(patch)}


#: How `_connect_again` names a field it saves.
_FIELDS = {
    "test_command": "the test command",
    "test_scopes": "its test scopes",
    "setup_command": "the setup command",
    "enabled": "enabled",
}


def _cmd_connect(ns: argparse.Namespace) -> None:
    test_command = "" if ns.no_tests else ns.test_command
    setup_command = ns.setup_command
    interactive = not (ns.json or ns.yes) and sys.stdin.isatty() and sys.stdout.isatty()
    if interactive and test_command is None and setup_command is None:
        test_command, setup_command = _choose_interactively(ns.path)
    result = asyncio.run(
        client.ensure_repo(ns.path, test_command=test_command, setup_command=setup_command)
    )
    if result.get("already_connected"):
        result = _connect_again(
            result, test_command, setup_command, ask=interactive, say=not ns.json
        )
    elif not ns.json:
        _say_connected(result)
    passed = True
    if ns.verify:
        # Imported here, not at the top: it reaches `kraft.builtins`, and every
        # `kraft` invocation imports this module -- the permission hook a worker
        # runs before each tool call included, which must not load the server.
        from kraft.cli import verify as verify_mod

        said: list[str] = []
        say = said.append if ns.json else print
        passed = verify_mod.verify(result, say=say, timeout_minutes=ns.timeout, on_host=ns.on_host)
        if ns.json:
            # What the plain output prints, so a failure says why here too.
            result = {**result, "verify": {"passed": passed, "output": said}}
    if ns.json:
        common.emit(result, str, True)
    if not passed:
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

    connect = subs.add_parser(
        "connect",
        parents=[common],
        help="connect a repo; again, to fill what it left undecided",
    )
    connect.add_argument("path", nargs="?", help="default: the current directory")
    tests = connect.add_mutually_exclusive_group()
    tests.add_argument(
        "--test-command", metavar="CMD", help="the repo's test command, instead of the proposal"
    )
    connect.add_argument(
        "--setup-command",
        metavar="CMD",
        help='the command that prepares a fresh checkout, instead of the proposal ("" for none)',
    )
    tests.add_argument(
        "--no-tests",
        action="store_true",
        help='the repo has no tests to run (saves test_command: "", and the repo enabled)',
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
