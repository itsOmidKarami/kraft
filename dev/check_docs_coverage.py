"""Fail if something real has zero mentions in the docsite page that's
supposed to cover it.

Mechanical only: it can tell "this CLI command is never mentioned in cli.md"
but not "this paragraph no longer describes what the command does" -- that
still needs a human (or an agent) to actually read the page. See
CONTRIBUTING.md's "User-facing docs" table, which lists every source and its
page; CHECKS below covers only the subset with a machine-readable list of
names.

A generic term (a single common word like "model" or "port") gives a weak
signal on purpose: it will never be flagged as missing, because it's likely
to appear in prose regardless of whether that specific field is documented.
That's a safe failure mode -- a check that cries wolf on generic words gets
ignored, not fixed. This catches the sharper case: a distinctive name
(a CLI subcommand pair, a snake_case field or tool name) with zero mentions
at all, which is what most often goes missing.

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
    """Every function decorated `@server.tool()` in mcp.py. Async too: every
    tool is `async def`, and matching `FunctionDef` alone found none of them."""
    tree = ast.parse((SRC / "mcp.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
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
_POLICY_FIELD_RENAMES = {
    "archive_after_days": "archive.after_days",
    "storage_limit_bytes": "storage.worktrees.limit",
    "storage_quota_bytes": "storage.worktrees.quota",
    "storage_auto_cleanup_min_age_s": "storage.worktrees.auto_cleanup.min_age",
}


def policy_fields() -> set[str]:
    from kraft import policy

    names = {f.name for f in dataclasses.fields(policy.Policy)}
    return {_POLICY_FIELD_RENAMES.get(n, n) for n in names}


def access_fields() -> set[str]:
    from kraft import config

    return set(config.Access.model_fields)


def sandbox_fields() -> set[str]:
    from kraft import config

    return set(config.SandboxHost.model_fields)


def sandbox_policy_fields() -> set[str]:
    from kraft import policy

    return set(policy.SandboxPolicy.model_fields) | set(policy.SandboxResources.model_fields)


def agent_task_keys() -> set[str]:
    from kraft.templates.models import AgentTask

    return set(AgentTask.model_fields)


def library_sections() -> set[str]:
    from kraft.templates.library import Namespace

    return {n.value for n in Namespace}


def harness_capabilities() -> set[str]:
    from kraft import harness

    return set(harness.KNOWN)


_ENV_NAME = re.compile(r"KRAFT_[A-Z0-9_]+")


def env_vars() -> set[str]:
    """Every `KRAFT_*` environment variable the source names: a string literal
    that is exactly such a name, the way `os.environ.get("KRAFT_HOME")` and the
    worker allowlists spell one. A name inside prose (`$KRAFT_RESULT_PATH` in a
    prompt) or an identifier (`_KRAFT_ROOTS`) is not a string of its own."""
    return {
        node.value
        for path in SRC.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _ENV_NAME.fullmatch(node.value)
    }


# (label, extractor, docsite page or folder; a folder counts every page in
# it). A subset of CONTRIBUTING.md's "User-facing docs" table: only sources with a
# machine-readable list of names.
_LIBRARY = "5.reference/03.configuration/04.library-and-chains.md"
CHECKS: list[tuple[str, Callable[[], set[str]], str]] = [
    ("kraft CLI commands", cli_commands, "5.reference/01.cli"),
    ("MCP tools", mcp_tool_names, "5.reference/09.mcp-tools.md"),
    ("environment variables", env_vars, "5.reference/03.configuration/11.environment-variables.md"),
    ("policy.yaml fields", policy_fields, "5.reference/03.configuration/03.policy.md"),
    ("access.yaml fields", access_fields, "5.reference/03.configuration/06.access.md"),
    (
        "sandbox.yaml fields",
        sandbox_fields,
        "5.reference/03.configuration/10.sandbox/6.sandbox-yaml.md",
    ),
    ("sandbox policy fields", sandbox_policy_fields, "5.reference/03.configuration/10.sandbox"),
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
    ("harness capabilities", harness_capabilities, "5.reference/05.harnesses"),
]


#: A backticked gate-shaped name in the docsite: `spec_approval`, and the
#: legacy `human_review_approval` this check exists to catch.
_GATE_NAME = re.compile(r"`([a-z][a-z0-9_]*_approval)`")


def unknown_gates() -> list[str]:
    """`page: name` for every gate-shaped name the docsite gives that no
    shipped chain has -- the reverse direction of CHECKS: a doc naming a gate
    that does not exist sends a reader looking for it."""
    from kraft.templates.library import TemplateLibrary

    library = TemplateLibrary.from_yaml_dir(ROOT / "config")
    # Every node, not only gates: `external_approval` is an exec node, and a
    # real one.
    gates = {n.id for id in library.chain_ids for n in library.resolve_chain(id).nodes}
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
        terms = extractor()
        if not terms:
            # An extractor that finds nothing makes its check pass vacuously:
            # how the MCP check sat silent while it matched no tool at all.
            failed = True
            print(f"{label}: the extractor found nothing, so nothing was checked")
            continue
        missing = sorted(term for term in terms if term not in text)
        if missing:
            failed = True
            print(f"docsite/content/{page}: missing {label}: {missing}")
    if not failed:
        print("ok: every checked term has at least one mention in its docs page")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
