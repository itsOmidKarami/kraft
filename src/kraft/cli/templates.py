"""`kraft admin templates ...`: the chain template library, read-only from a
terminal -- lint it, print one chain, list its reusable components. And
`kraft admin harnesses`, the `harnesses.yaml` profiles its agent tasks select,
and the agent profiles (model tiers) under them."""

from __future__ import annotations

import argparse
import asyncio

import yaml

from kraft import client, render
from kraft.cli import common


def _render_lint(report: dict) -> str:
    lines = [
        f"{issue['file']}:{issue['line']}:{issue['column']}: {issue['message']}"
        for issue in (*report["issues"], *report.get("unchecked", ()))
    ]
    if report["valid"]:
        lines.insert(0, f"{len(report['chains'])} chain(s), no errors")
    return "\n".join(lines)


def _lint_dir_report(path: str) -> dict:
    """`--dir`'s in-process answer: the route's `lint_report` over `path`,
    never the daemon. No `skills_dir` -- an operator's `~/.kraft/skills`
    overlay is that instance's, not the checkout's, so this only sees bundled
    skills. No `instance_policy` either -- that is this run's `policy.yaml`,
    also instance state, so a chain past that instance's `maxima:` ceiling
    will not show up here even though the server route would catch it."""
    from kraft.api.config_check import lint_report  # the daemon's modules, only for --dir

    return lint_report(path, plugins=None)  # offline: no instance, so no plugins


def _cmd_lint(ns: argparse.Namespace) -> None:
    report = _lint_dir_report(ns.dir) if ns.dir else asyncio.run(client.lint_templates())
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
        common.emit(payload, _render_chain_text, ns.json)


def _render_chain_text(payload: dict) -> str:
    """A chain file as written; a plugin's is headed by where it comes from."""
    header = f"# {_named(payload)}\n" if payload.get("plugin") else ""
    return header + payload["text"].rstrip("\n")


def _named(entry: dict) -> str:
    """An id, followed by the plugin it comes from: `release:ship (release@acme 1.4.0)`."""
    plugin = entry.get("plugin")
    return entry["id"] + (f" ({plugin['id']} {plugin['version']})" if plugin else "")


def _render_components(payload: dict) -> str:
    rows = [
        {**c, "id": _named(c), "used_by": ", ".join(c["used_by"]) or "-"}
        for c in payload["components"]
    ]
    return render.table(rows, [("ID", "id"), ("KIND", "kind"), ("USED BY", "used_by")])


def _render_component(c: dict) -> str:
    pairs = [("id", _named(c)), ("used by", ", ".join(c["used_by"]) or "no chain")]
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
    # CHAINS as well as USED BY: a chain can set `harness:` on its own task,
    # which no library task records (Kraft-9efnk.35).
    rows = [
        {
            **p,
            "enabled": "yes" if p["enabled"] else "no",
            "used_by": ", ".join(p["used_by"]) or "-",
            "chains": ", ".join(p["chains"]) or "-",
        }
        for p in payload["profiles"]
    ]
    table = render.table(
        rows,
        [
            ("ID", "id"),
            ("PROVIDER", "provider"),
            ("ENABLED", "enabled"),
            ("CHAINS", "chains"),
            ("USED BY", "used_by"),
        ],
    )
    if payload["error"]:
        return f"{payload['file']}: {payload['error']}"
    tiers = [
        {
            **p,
            "effort": p["effort"] or ("per provider" if p["providers"] else "-"),
            "model": ", ".join(
                f"{k}={e['model']}"
                + (f" ({e['effort']})" if e["effort"] and not p["effort"] else "")
                for k, e in p["providers"].items()
            ),
            "used_by": ", ".join(p["used_by"]) or "-",
        }
        for p in payload.get("agent_profiles", [])
    ]
    if not tiers:
        return table
    agent = render.table(
        tiers,
        [("PROFILE", "id"), ("EFFORT", "effort"), ("MODEL", "model"), ("USED BY", "used_by")],
    )
    problems = [why for p in payload["agent_profiles"] for why in p["problems"]]
    return "\n\n".join([table, agent, *(["\n".join(problems)] if problems else [])])


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
    templates_p = subs.add_parser("templates", help="inspect the library and its chains")
    verbs = templates_p.add_subparsers(dest="templates_verb", required=True)
    lint_p = verbs.add_parser(
        "lint",
        parents=[common_parser],
        help="check every chain in the installed library; exit 1 on any error",
    )
    lint_p.add_argument(
        "--dir",
        metavar="PATH",
        help="lint a template directory in-process instead of asking the daemon -- "
        "no network, no $KRAFT_HOME. Skips an installed skills override and this "
        "instance's policy.yaml ceilings, since neither belongs to a bare directory; "
        "everything else matches the server route",
    )
    lint_p.set_defaults(func=_cmd_lint)
    show_p = verbs.add_parser("show", parents=[common_parser], help="print one chain")
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
