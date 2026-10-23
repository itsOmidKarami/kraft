"""`kraft admin worker`: the machines that may run work item steps.

`add`, `list` and `remove` ask the server, which owns `workers.yaml`. `serve`
and `check` run on the machine itself and need no server of their own."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

from kraft import client, render, update
from kraft.cli import common


def _render_added(result: dict) -> str:
    worker = result["worker"]
    if "token" not in result:
        probe = result["probe"]
        line = "connected" if probe["connected"] else f"could not connect: {probe['detail']}"
        return f"added {worker['name']} (ssh)\n{line}"
    lines = [
        f"added {worker['name']} (dial-in)",
        "",
        f"token (shown once, keep it): {result['token']}",
        "",
        "run this on the machine:",
        f"  {result['serve_command']}",
    ]
    if result.get("note"):
        lines += ["", result["note"]]
    return "\n".join(lines)


def _seen(stamp: str | None) -> str:
    return stamp[:19].replace("T", " ") if stamp else "-"


def _render_list(result: dict) -> str:
    rows = result["workers"]
    if not rows:
        return "no workers"
    table = [("NAME", "MODE", "STATE", "SLOTS", "REPOS", "LAST SEEN", "VERSION")]
    for w in rows:
        table.append(
            (
                w["name"],
                w["mode"],
                "online" if w["online"] else f"offline: {w['reason']}",
                f"{w['slots_used']}/{w['capacity']}",
                ",".join(w["repos"]) if w["repos"] else "all",
                _seen(w["last_seen"]),
                w["kraft_version"] or "-",
            )
        )
    widths = [max(len(row[i]) for row in table) for i in range(len(table[0]) - 1)]
    return "\n".join(
        "  ".join([c.ljust(widths[i]) for i, c in enumerate(row[:-1])] + [row[-1]]).rstrip()
        for row in table
    )


def _cmd_add(ns: argparse.Namespace) -> None:
    repos = [r for r in (ns.repos or "").split(",") if r] or None
    result = asyncio.run(
        client.add_worker(
            ns.name,
            ssh=ns.ssh,
            capacity=ns.capacity,
            repos=repos,
            give_up_after=ns.give_up_after,
        )
    )
    common.emit(result, _render_added, ns.json)
    # The entry is kept (a sleeping machine is a fine thing to register), but
    # a person who asked for a connection is told it did not happen.
    if "probe" in result and not result["probe"]["connected"]:
        raise SystemExit(1)


def _cmd_list(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.list_workers()), _render_list, ns.json)


def _cmd_remove(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.remove_worker(ns.name)), lambda r: f"removed {r['removed']}", ns.json
    )


def _cmd_serve(ns: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if ns.stdio and ns.token:
        parser.error("--stdio takes no --token: ssh already says who is calling")
    if ns.server and not ns.token:
        parser.error("--server needs --token")
    from kraft.remote import machine

    version = update.installed()
    if ns.daemon:
        _serve_daemon(version)
        return
    if ns.stdio:
        # The protocol owns the real stdout. Anything else printed, by this
        # process or a library, goes to stderr so it cannot corrupt a frame.
        proto = os.fdopen(os.dup(1), "wb", buffering=0)
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        asyncio.run(machine.run_stdio(kraft_version=version, stdout=proto))
        return

    async def serve() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        await machine.dial_in(ns.server, ns.token, kraft_version=version, stop=stop)

    asyncio.run(serve())


def _serve_daemon(version: str) -> None:
    from kraft import paths
    from kraft.remote import daemon

    async def serve() -> None:
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        await daemon.serve(paths.kraft_home(), kraft_version=version, stop=stop)

    asyncio.run(serve())


def _cmd_attach(ns: argparse.Namespace) -> None:
    from kraft.remote import standin

    sys.exit(asyncio.run(standin.attach(Path(ns.run_dir), ns.item, ns.session)))


def _cmd_check(ns: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if bool(ns.server) != bool(ns.token):
        parser.error("--server and --token go together")
    from kraft.remote import check

    rows = asyncio.run(
        check.run(server=ns.server, token=ns.token, kraft_version=update.installed())
    )
    common.emit(rows, render.doctor_block, ns.json)
    if any(not row["ok"] for row in rows):
        # exit 1 so `kraft admin worker check && ...` works; the failures are already printed
        raise SystemExit(1)


def add(subs, common_parser: argparse.ArgumentParser) -> None:
    worker_p = subs.add_parser("worker", help="machines that may run work item steps")
    verbs = worker_p.add_subparsers(dest="worker_verb", required=True)

    add_p = verbs.add_parser(
        "add", parents=[common_parser], help="register a machine; a dial-in one gets a token"
    )
    add_p.add_argument("name", help="lowercase letters, digits, - and _")
    add_p.add_argument("--ssh", metavar="HOST", help="a host or ssh config alias Kraft dials")
    add_p.add_argument("--capacity", type=int, default=1, help="steps it runs at once (default 1)")
    add_p.add_argument("--repos", metavar="A,B", help="limit it to these connected repos")
    add_p.add_argument(
        "--give-up-after",
        metavar="DURATION",
        help="how long a vanished machine's work is waited for: 2h, 30m, 1d or never",
    )
    add_p.set_defaults(func=_cmd_add)

    list_p = verbs.add_parser(
        "list", parents=[common_parser], help="the machines, online or offline and why"
    )
    list_p.set_defaults(func=_cmd_list)

    remove_p = verbs.add_parser(
        "remove", parents=[common_parser], help="forget a machine and end its connection"
    )
    remove_p.add_argument("name")
    remove_p.set_defaults(func=_cmd_remove)

    serve_p = verbs.add_parser(
        "serve",
        help="on a machine: connect to a Kraft server (run by hand, or by ssh with --stdio)",
    )
    how = serve_p.add_mutually_exclusive_group(required=True)
    how.add_argument("--server", metavar="URL", help="dial in to this Kraft server")
    how.add_argument("--stdio", action="store_true", help="speak on stdin and stdout, for ssh")
    how.add_argument(
        "--daemon", action="store_true", help=argparse.SUPPRESS
    )  # started by --stdio, not by hand
    serve_p.add_argument("--token", help="the token `worker add` printed")
    serve_p.set_defaults(func=lambda ns: _cmd_serve(ns, serve_p))

    check_p = verbs.add_parser(
        "check", parents=[common_parser], help="on a machine: what it has for running steps"
    )
    check_p.add_argument("--server", metavar="URL", help="also try a handshake with this server")
    check_p.add_argument("--token", help="the token for --server")
    check_p.set_defaults(func=lambda ns: _cmd_check(ns, check_p))

    attach_p = verbs.add_parser(
        "attach", help="run by Kraft, not by hand: carry a remote step's log and signals"
    )
    attach_p.add_argument("--run-dir", required=True, help="the server's run directory")
    attach_p.add_argument("--item", required=True, help="the work item id")
    attach_p.add_argument("--session", required=True, help="the session id")
    attach_p.set_defaults(func=_cmd_attach)
