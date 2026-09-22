"""`kraft admin templates ...`: the chain template library, read-only from a
terminal -- lint it, print one chain, list its reusable components. And
`kraft admin harnesses`, the `harnesses.yaml` profiles its agent tasks select."""

from __future__ import annotations

import argparse
import asyncio

import yaml

from kraft import client, render
from kraft.cli import common


def _render_lint(report: dict) -> str:
    if report["valid"]:
        return f"{len(report['chains'])} chain(s), no errors"
    return "\n".join(
        f"{issue['chain'] or issue['file']}: {issue['message']}" for issue in report["issues"]
    )


def _cmd_lint(ns: argparse.Namespace) -> None:
    report = asyncio.run(client.lint_templates())
    common.emit(report, _render_lint, ns.json)
    if not report["valid"]:
        # exit 1 so `kraft admin templates lint && ...` works; the errors are on stdout
        raise SystemExit(1)


def _cmd_show(ns: argparse.Namespace) -> None:
    if ns.resolved:
        payload = asyncio.run(client.resolved_template(ns.template_id))
        common.emit(payload, lambda p: yaml.safe_dump(p["chain"], sort_keys=False), ns.json)
    else:
        payload = asyncio.run(client.template(ns.template_id))
        common.emit(payload, lambda p: p["text"].rstrip("\n"), ns.json)


def _render_components(payload: dict) -> str:
    rows = [{**c, "used_by": ", ".join(c["used_by"]) or "-"} for c in payload["components"]]
    return render.table(rows, [("ID", "id"), ("KIND", "kind"), ("USED BY", "used_by")])


def _render_component(c: dict) -> str:
    pairs = [("id", c["id"]), ("used by", ", ".join(c["used_by"]) or "no chain")]
    pairs += [("issue", f"{i['chain'] or i['file']}: {i['message']}") for i in c["issues"]]
    definition = yaml.safe_dump(c["definition"], sort_keys=False).rstrip("\n")
    return f"{render.kv(pairs)}\ndefinition:\n{definition}"


def _cmd_library(ns: argparse.Namespace) -> None:
    if ns.component_id:
        payload = asyncio.run(client.library_component(ns.component_id))
        common.emit(payload, _render_component, ns.json)
    else:
        common.emit(asyncio.run(client.library()), _render_components, ns.json)


def _render_profiles(payload: dict) -> str:
    rows = [
        {**p, "enabled": "yes" if p["enabled"] else "no", "used_by": ", ".join(p["used_by"]) or "-"}
        for p in payload["profiles"]
    ]
    table = render.table(
        rows,
        [("ID", "id"), ("PROVIDER", "provider"), ("ENABLED", "enabled"), ("USED BY", "used_by")],
    )
    return f"{payload['file']}: {payload['error']}" if payload["error"] else table


def _render_profile(p: dict) -> str:
    defaults = ", ".join(f"{k}={v}" for k, v in p["defaults"].items())
    return render.kv(
        [
            ("id", p["id"]),
            ("provider", p["provider"]),
            ("enabled", "yes" if p["enabled"] else "no"),
            ("executable", p["executable"] or "the provider's own"),
            ("defaults", defaults or "none"),
            ("used by", ", ".join(p["used_by"]) or "no library task"),
            ("chains", ", ".join(p["chains"]) or "no chain"),
        ]
    )


def _cmd_harnesses(ns: argparse.Namespace) -> None:
    if ns.profile_id:
        common.emit(asyncio.run(client.harness(ns.profile_id)), _render_profile, ns.json)
    else:
        common.emit(asyncio.run(client.harnesses()), _render_profiles, ns.json)


def add(subs, common_parser: argparse.ArgumentParser) -> None:
    harnesses_p = subs.add_parser(
        "harnesses",
        parents=[common_parser],
        help="the harness profiles in harnesses.yaml and the library tasks that select each",
    )
    harnesses_p.add_argument("profile_id", metavar="ID", nargs="?", help="one profile")
    harnesses_p.set_defaults(func=_cmd_harnesses)
    templates_p = subs.add_parser("templates", help="inspect the chain template library")
    verbs = templates_p.add_subparsers(dest="templates_verb", required=True)
    lint_p = verbs.add_parser(
        "lint",
        parents=[common_parser],
        help="check every chain in the installed library; exit 1 on any error",
    )
    lint_p.set_defaults(func=_cmd_lint)
    show_p = verbs.add_parser("show", parents=[common_parser], help="print one chain template")
    show_p.add_argument("template_id", metavar="ID")
    show_p.add_argument(
        "--resolved",
        action="store_true",
        help="with its library components expanded, as a work item would get it",
    )
    show_p.set_defaults(func=_cmd_show)
    library_p = verbs.add_parser(
        "library",
        parents=[common_parser],
        help="the reusable components in library.yaml and the chains that use each",
    )
    library_p.add_argument(
        "component_id",
        metavar="ID",
        nargs="?",
        help="one component: `tasks.implementer`, or a bare name no other section shares",
    )
    library_p.set_defaults(func=_cmd_library)
