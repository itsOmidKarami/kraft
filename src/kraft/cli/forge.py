"""`kraft admin forge`: the forge accounts in `forges.yaml`. The server owns the file;
every verb asks it."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys

from pydantic import ValidationError

from kraft import answers, client, config, forges
from kraft.cli import common
from kraft.client import transport
from kraft.paths import config_dir

_SOURCES = "git-credential, env:NAME, file:PATH or command:CMD"


def _render_list(result: dict) -> str:
    accounts = result["accounts"]
    if not accounts:
        return "no forge accounts"
    return "\n".join(
        f"{name}  {a['forge']}  {a['web_url']}" + ("  [default]" if a["default_for_host"] else "")
        for name, a in accounts.items()
    )


def parse_source(text: str, kind: str) -> dict:
    """`--token-from`'s text as the `token:` the server takes. Never quotes `text`: a
    token pasted here by mistake must not come back in an error."""
    if text == "git-credential":
        return {"from": text, "kind": kind}
    head, _, rest = text.partition(":")
    if head not in ("env", "file", "command") or not rest:
        raise ValueError(f"--token-from takes {_SOURCES}")
    if head == "env" and (why := forges.env_name_problem(rest)):
        raise ValueError(f"--token-from env: the name {why}")
    return {"from": head, head: rest, "kind": kind}


def _die(message: str) -> SystemExit:
    return SystemExit(f"error: {message}")


def _token_flags(ns: argparse.Namespace) -> tuple[dict | None, bool]:
    """(the source to send, whether a token is read from stdin). Either implies
    `--client api`; with `--client cli` it is an error."""
    given = bool(ns.token_from or ns.token_stdin)
    if given and ns.client == "cli":
        raise _die("--client cli keeps no token: gh and glab keep their own sign-in")
    try:
        source = parse_source(ns.token_from, ns.kind) if ns.token_from else None
    except ValueError as exc:
        raise _die(str(exc)) from None
    return source, bool(ns.token_stdin)


def _read_stdin_token() -> str:
    # Whole stdin, never argv: a process listing shows argv.
    value = sys.stdin.read().strip()
    if not value:
        raise _die("--token-stdin: nothing on stdin")
    return value


def _on_this_machine() -> None:
    """A command source runs code as the server's user, so it is written where the server
    runs, by a person: not from a worker, not over the network."""
    if os.environ.get("KRAFT_WORK_ITEM_ID"):
        raise _die("a command source runs code on the server's machine: a worker does not set it")
    try:
        asyncio.run(client.health())  # raises when the server's run_dir is not this machine's
    except transport.ClientError as exc:
        if exc.kind is not transport.Failure.OTHER_INSTANCE:
            raise
        raise _die("a command source is written on the server's machine; run this there") from None


def _write_command(name: str, change) -> dict:
    """forges.yaml on disk with `change(accounts)` applied, then the server asked to reload.
    A reload that refuses it puts the old text back."""
    _on_this_machine()
    path = config_dir() / forges.ForgesFile.FILE
    old = path.read_text() if path.exists() else None
    try:
        accounts = dict(forges.ForgesFile.load(path).accounts)
        change(accounts)
        forges.ForgesFile.model_validate({"accounts": accounts}).save(path)
    except config.ConfigError as exc:
        raise _die(f"{exc}; fix the file first") from None
    except ValidationError as exc:
        raise _die(forges.validation_text(exc)) from None
    result = asyncio.run(client.reload_templates())
    if answers.reload_refused(result):
        if old is None:
            path.unlink(missing_ok=True)
        else:
            config.write_text(path, old)
        asyncio.run(client.reload_templates())  # the refusal's mark is not the file's
        raise _die(f"the server refused forges.yaml: {result.get('refused_forges')}")
    return result


def create_account(
    name: str, account: dict, *, source: dict | None, token: str | None, kind: str, as_api: bool
) -> dict:
    """The account added: `account` is the forge, web_url, api_url, default_for_host and
    ca_file to send. A `command` source is written on disk; a pasted `token` goes to
    `set_forge_token` alone, and a failed paste leaves no half-made account behind."""
    if source and source["from"] == "command":

        def put(accounts: dict) -> None:
            if name in accounts:
                raise _die(f"forge account {name!r} already exists")
            accounts[name] = {
                **{k: v for k, v in account.items() if v},
                "client": "api",
                "token": source,
            }

        _write_command(name, put)
        return {"name": name, **account}
    result = asyncio.run(
        client.add_forge(
            name,
            account["forge"],
            account["web_url"],
            api_url=account["api_url"],
            default_for_host=account["default_for_host"],
            client="api" if as_api else "cli",
            ca_file=account["ca_file"],
            token=source,
        )
    )
    if token is not None:
        try:
            result = asyncio.run(client.set_forge_token(name, token, kind))
        except Exception:
            # No half-made account: an API account with no token would only fail later.
            with contextlib.suppress(Exception):  # the paste's error is the one to show
                asyncio.run(client.remove_forge(name))
            raise
    return result


def set_command_source(name: str, fields: dict) -> dict:
    """`fields` applied to the account in forges.yaml on disk (a command source's way in)."""

    def change(accounts: dict) -> None:
        if name not in accounts:
            raise _die(f"no forge account named {name!r}")
        accounts[name] = {
            **accounts[name].model_dump(by_alias=True, exclude_none=True),
            **fields,
        }

    return _write_command(name, change)


def _cmd_add(ns: argparse.Namespace) -> None:
    source, stdin = _token_flags(ns)
    account = {
        "forge": ns.forge,
        "web_url": ns.web_url,
        "api_url": ns.api_url,
        "default_for_host": ns.default_for_host,
        "ca_file": ns.ca_file,
    }
    result = create_account(
        ns.name,
        account,
        source=source,
        token=_read_stdin_token() if stdin else None,
        kind=ns.kind,
        as_api=bool(source or stdin) or ns.client == "api",
    )
    if source and source["from"] == "command":
        common.emit({"name": ns.name}, lambda r: f"added {r['name']} (client: api)", ns.json)
        return
    common.emit(result, lambda r: f"added {ns.name} ({r['forge']}, {r['web_url']})", ns.json)


def _cmd_edit(ns: argparse.Namespace) -> None:
    source, stdin = _token_flags(ns)
    fields = {
        k: v
        for k, v in {
            "api_url": ns.api_url,
            "ca_file": ns.ca_file,
            "client": "api" if source or stdin else ns.client,
            "token": source,
        }.items()
        if v is not None
    }
    if not fields:
        raise _die("nothing to change: give a flag")
    if source and source["from"] == "command":
        set_command_source(ns.name, fields)
        common.emit({"name": ns.name}, lambda r: f"edited {r['name']}", ns.json)
        return
    token = _read_stdin_token() if stdin else None
    result = asyncio.run(client.edit_forge(ns.name, fields))
    if token is not None:
        result = asyncio.run(client.set_forge_token(ns.name, token, ns.kind))
    common.emit(result, lambda r: f"edited {ns.name}", ns.json)


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


def _token_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--client",
        choices=["cli", "api"],
        help="cli: gh/glab keep their own sign-in; api: Kraft reads a token (implied by --token-*)",
    )
    p.add_argument("--ca-file", metavar="PATH", help="the CA that signs the forge (api only)")
    source = p.add_mutually_exclusive_group()
    source.add_argument(
        "--token-from",
        metavar="SOURCE",
        help=f"where the token is read from: {_SOURCES}; command runs on the server's machine",
    )
    source.add_argument(
        "--token-stdin", action="store_true", help="read the token from stdin; never from argv"
    )
    p.add_argument(
        "--kind", choices=["pat", "oauth", "unknown"], default="unknown", help="what the token is"
    )


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
    _token_args(add_p)
    add_p.set_defaults(func=_cmd_add)

    edit_p = verbs.add_parser(
        "edit",
        parents=[common_parser],
        help="change an account; only the flags given change",
    )
    edit_p.add_argument("name")
    edit_p.add_argument("--api-url", metavar="URL", help="same origin as the web address")
    _token_args(edit_p)
    edit_p.set_defaults(func=_cmd_edit)

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
