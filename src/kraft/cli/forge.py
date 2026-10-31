"""`kraft admin forge`: the forge accounts in `forges.yaml`. The server owns the file;
every verb asks it."""

from __future__ import annotations

import argparse
import asyncio

from kraft import client
from kraft.cli import common


def _render_list(result: dict) -> str:
    accounts = result["accounts"]
    if not accounts:
        return "no forge accounts"
    return "\n".join(
        f"{name}  {a['forge']}  {a['web_url']}" + ("  [default]" if a["default_for_host"] else "")
        for name, a in accounts.items()
    )


def _cmd_add(ns: argparse.Namespace) -> None:
    result = asyncio.run(
        client.add_forge(
            ns.name,
            ns.forge,
            ns.web_url,
            api_url=ns.api_url,
            default_for_host=ns.default_for_host,
        )
    )
    common.emit(result, lambda r: f"added {ns.name} ({r['forge']}, {r['web_url']})", ns.json)


def _cmd_list(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.list_forges()), _render_list, ns.json)


def _cmd_remove(ns: argparse.Namespace) -> None:
    asyncio.run(client.remove_forge(ns.name))
    common.emit({"removed": ns.name}, lambda r: f"removed {r['removed']}", ns.json)


def _cmd_test(ns: argparse.Namespace) -> None:
    result = asyncio.run(client.test_forge(ns.name))
    common.emit(
        result, lambda r: f"{r['output'].rstrip()}\n{'ok' if r['ok'] else 'failed'}", ns.json
    )
    if not result["ok"]:
        raise SystemExit(1)


def add(subs, common_parser: argparse.ArgumentParser) -> None:
    forge_p = subs.add_parser("forge", help="forge accounts: a host's forge and web address")
    verbs = forge_p.add_subparsers(dest="forge_verb", metavar="verb", required=True)

    add_p = verbs.add_parser(
        "add", parents=[common_parser], help="name a forge account (the CLI client)"
    )
    add_p.add_argument("name", help="lowercase letters, digits, . - and _")
    add_p.add_argument("--forge", required=True, choices=["github", "gitlab"])
    add_p.add_argument("--web-url", required=True, metavar="URL", help="https:// address")
    add_p.add_argument(
        "--api-url", metavar="URL", help="same host as --web-url (default: the forge's usual one)"
    )
    add_p.add_argument(
        "--default-for-host",
        action="store_true",
        help="repos on this host use it unless they name another account",
    )
    add_p.set_defaults(func=_cmd_add)

    list_p = verbs.add_parser("list", parents=[common_parser], help="the accounts")
    list_p.set_defaults(func=_cmd_list)

    remove_p = verbs.add_parser(
        "remove", parents=[common_parser], help="forget an account; refused while a repo names it"
    )
    remove_p.add_argument("name")
    remove_p.set_defaults(func=_cmd_remove)

    test_p = verbs.add_parser(
        "test",
        parents=[common_parser],
        help="ask the server whether gh/glab is signed in for the account's host",
    )
    test_p.add_argument("name")
    test_p.set_defaults(func=_cmd_test)
