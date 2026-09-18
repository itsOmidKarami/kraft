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
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCSITE = ROOT / "docsite"
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

    return set(config.ACCESS_DEFAULT)


def registry_agent_default_keys() -> set[str]:
    from kraft import templates

    return set(templates._AGENT_ONLY_KEYS)


def template_composition_keys() -> set[str]:
    from kraft import templates

    return set(templates._COMPOSITION_KEYS)


def harness_capabilities() -> set[str]:
    from kraft import harness

    return set(harness.KNOWN)


# (label, extractor, docsite page). Mirrors CONTRIBUTING.md's "User-facing
# docs" table -- add a row there when you add one here.
CHECKS: list[tuple[str, Callable[[], set[str]], str]] = [
    ("kraft CLI commands", cli_commands, "cli.md"),
    ("MCP tools", mcp_tool_names, "agent-integration.md"),
    ("policy.yaml fields", policy_fields, "configuration.md"),
    ("access.yaml fields", access_fields, "configuration.md"),
    ("registry.yaml defaults.agent keys", registry_agent_default_keys, "configuration.md"),
    ("chain template composition keys", template_composition_keys, "concepts.md"),
    ("harness capabilities", harness_capabilities, "harnesses.md"),
]


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    failed = False
    for label, extractor, page in CHECKS:
        text = (DOCSITE / page).read_text()
        missing = sorted(term for term in extractor() if term not in text)
        if missing:
            failed = True
            print(f"docsite/{page}: missing {label}: {missing}")
    if not failed:
        print("ok: every checked term has at least one mention in its docs page")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
