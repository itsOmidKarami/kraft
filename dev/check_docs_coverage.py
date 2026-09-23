"""Fail if something real has zero mentions in the docsite page that's
supposed to cover it.

Mechanical only: it can tell "this CLI command is never mentioned in cli.md"
but not "this paragraph no longer describes what the command does" -- that
still needs a human (or an agent) to actually read the page. See
CONTRIBUTING.md's "User-facing docs" table, which this script's CHECKS list
mirrors one row at a time.

A generic term (a single common word like "model" or "port") gives a weak
signal on purpose: it will never be flagged as missing, because it's likely
to appear in prose regardless of whether that specific field is documented.
That's a safe failure mode -- a check that cries wolf on generic words gets
ignored, not fixed. This catches the sharper case: a distinctive name
(a CLI subcommand pair, a snake_case field or tool name) with zero mentions
at all, which is what actually went missing when this script was written
(Kraft docs audit, 2026-09-18).

Run directly: `uv run python dev/check_docs_coverage.py`.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import re
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCSITE = ROOT / "docsite" / "content"
SRC = ROOT / "src" / "kraft"


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def cli_commands() -> set[str]:
    """Every `kraft <group> <verb>` pair the real parser accepts."""
    from kraft.cli import build_parser

    top = _subparsers_action(build_parser())
    assert top is not None, "kraft.cli.build_parser() grew no subcommands at all"
    terms = set()
    for group, subparser in top.choices.items():
        sub = _subparsers_action(subparser)
        if sub is None:
            continue
        terms.update(f"{group} {name}" for name in sub.choices)
    return terms


def mcp_tool_names() -> set[str]:
    """Every function decorated `@server.tool()` in mcp.py."""
    tree = ast.parse((SRC / "mcp.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "tool"
                ):
                    names.add(node.name)
    return names


#: A dataclass field name that isn't how the docs (correctly) spell the same
#: thing, because the YAML shape nests it under a parent key the Python
#: attribute flattens away. Add here, not to the docs -- the doc's spelling
#: is the operator-facing one and is what should stay put.
_POLICY_FIELD_RENAMES = {"archive_after_days": "archive.after_days"}


def policy_fields() -> set[str]:
    from kraft import policy

    names = {f.name for f in dataclasses.fields(policy.Policy)}
    return {_POLICY_FIELD_RENAMES.get(n, n) for n in names}


def access_fields() -> set[str]:
    from kraft import config

    return set(config.Access.model_fields)


def agent_task_keys() -> set[str]:
    from kraft.templates.models import AgentTask

    return set(AgentTask.model_fields)


def library_sections() -> set[str]:
    from kraft.templates.library import Namespace

    return {n.value for n in Namespace}


def harness_capabilities() -> set[str]:
    from kraft import harness

    return set(harness.KNOWN)


# (label, extractor, docsite page or folder; a folder counts every page in
# it). Mirrors CONTRIBUTING.md's "User-facing docs" table -- add a row there when you add one here.
_LIBRARY = "4.reference/2.configuration/4.library-and-chains.md"
CHECKS: list[tuple[str, Callable[[], set[str]], str]] = [
    ("kraft CLI commands", cli_commands, "4.reference/1.cli"),
    ("MCP tools", mcp_tool_names, "3.guides/1.agent-integration.md"),
    ("policy.yaml fields", policy_fields, "4.reference/2.configuration/3.policy.md"),
    ("access.yaml fields", access_fields, "4.reference/2.configuration/6.access.md"),
    (
        "library.yaml agent task keys",
        agent_task_keys,
        _LIBRARY,
    ),
    (
        "library.yaml sections",
        library_sections,
        _LIBRARY,
    ),
    ("harness capabilities", harness_capabilities, "4.reference/5.harnesses"),
]


#: A backticked gate-shaped name in the docsite: `spec_approval`, and the
#: legacy `human_review_approval` this check exists to catch (Kraft-cusz8).
_GATE_NAME = re.compile(r"`([a-z][a-z0-9_]*_approval)`")


def unknown_gates() -> list[str]:
    """`page: name` for every gate-shaped name the docsite gives that no
    shipped chain has -- the reverse direction of CHECKS: a doc naming a gate
    that does not exist sends a reader looking for it."""
    from kraft.templates.library import TemplateLibrary
    from kraft.templates.models import GateNode

    library = TemplateLibrary.from_yaml_dir(ROOT / "templates")
    gates = {
        n.id
        for id in library.chain_ids
        for n in library.resolve_chain(id).nodes
        if isinstance(n.node, GateNode)
    }
    return [
        f"{page.relative_to(DOCSITE)}: {name}"
        for page in sorted(DOCSITE.glob("**/*.md"))
        for name in sorted(set(_GATE_NAME.findall(page.read_text())))
        if name not in gates
    ]


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    failed = False
    for unknown in unknown_gates():
        failed = True
        print(f"docsite/content/{unknown}: names a gate no shipped chain has")
    for label, extractor, page in CHECKS:
        target = DOCSITE / page
        pages = sorted(target.glob("**/*.md")) if target.is_dir() else [target]
        text = "\n".join(p.read_text() for p in pages)
        missing = sorted(term for term in extractor() if term not in text)
        if missing:
            failed = True
            print(f"docsite/content/{page}: missing {label}: {missing}")
    if not failed:
        print("ok: every checked term has at least one mention in its docs page")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
