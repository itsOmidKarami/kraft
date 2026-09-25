"""Fail a pull request that removes a test or an intent requirement it does not own up to.

A PR that reverts merged work passes CI when the tests pinning that work leave
with it: nothing is left to go red (Kraft-79382, where one commit silently
undid two merged PRs, security tests included). So a removal has to be said
out loud. Every test function the PR deletes (`path::name`, or
`path::Class::name`), every frontend test file it deletes, and every
`## REQ <name>` heading it drops must be listed in the PR body under a
`Removed tests` or `Removed requirements` heading, one per line:

    ## Removed tests
    - tests/test_old.py::test_gone -- replaced by tests/test_new.py::test_here
    - tests/test_dead_file.py

    ## Removed requirements
    - some-req-name -- superseded by other-req-name

A deleted test file may be listed by its path alone (a file that is only
edited may not: list each test it loses). A renamed test is a
removal plus an addition: list the old id. Anything after the id on a line is
free text for the reviewer.

Usage: python3 dev/check_removals.py <base-rev> <pr-body-file> [<head-rev>]
Exit 1, naming each undeclared removal, when any is missing.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import PurePosixPath

_REQ = re.compile(r"^## REQ (\S+)", re.M)
_HEADING = re.compile(r"^#+\s*(.*?)\s*$")
_SECTIONS = {"removed tests": "tests", "removed requirements": "requirements"}
#: ponytail: frontend tests count per file, not per `it(...)` -- parsing TS
#: is not worth it until a frontend-only revert slips through.
_FRONTEND_TEST = re.compile(r"\.(test|spec)\.[jt]sx?$")


def _is_py_test(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def tests_in(path: str, source: str | None) -> set[str]:
    """`path::name` for every top-level test function and `path::Class::name`
    for every test method. A file that does not parse raises: a checker that
    cannot read a file must not read it as "nothing removed"."""
    if source is None:
        return set()
    ids = set()
    for node in ast.parse(source, filename=path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            ids.add(f"{path}::{node.name}")
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            ids.update(
                f"{path}::{node.name}::{f.name}"
                for f in node.body
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                and f.name.startswith("test")
            )
    return ids


def req_ids(text: str | None) -> set[str]:
    return set(_REQ.findall(text or ""))


def declared(body: str) -> dict[str, set[str]]:
    """The ids listed under each `Removed ...` heading: the first word of each
    line, bullets and backticks stripped."""
    out: dict[str, set[str]] = {"tests": set(), "requirements": set()}
    section = None
    for line in body.splitlines():
        if heading := _HEADING.match(line):
            section = _SECTIONS.get(heading.group(1).lower())
        elif section and (words := line.strip().lstrip("-*+ ").split()):
            out[section].add(words[0].strip("`"))
    return out


def undeclared(
    removed: set[str], deleted: set[str], removed_reqs: set[str], body: str
) -> dict[str, list[str]]:
    """What `removed` (test ids and frontend test file paths) and
    `removed_reqs` leave out of the body. A test in a `deleted` file is
    declared by that file's path too."""
    listed = declared(body)["tests"]

    def said(t: str) -> bool:
        path = t.split("::")[0]
        return t in listed or (path in deleted and path in listed)

    reqs = removed_reqs - declared(body)["requirements"]
    return {"tests": sorted(t for t in removed if not said(t)), "requirements": sorted(reqs)}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _show(rev: str, path: str) -> str | None:
    # Every changed file is read, binaries included (a PNG is not UTF-8); only
    # test and markdown files are ever parsed, so lossy decoding costs nothing.
    done = subprocess.run(
        ["git", "show", f"{rev}:{path}"], capture_output=True, text=True, errors="replace"
    )
    return done.stdout if done.returncode == 0 else None


def removals(base: str, head: str) -> tuple[set[str], set[str], set[str]]:
    """(removed test ids and frontend test files, deleted test files, removed
    REQ names) from the merge base of `base` and `head` to `head`."""
    fork = _git("merge-base", base, head).strip()
    changed = _git("diff", "--name-only", "--no-renames", fork, head).splitlines()
    removed, deleted, reqs = set(), set(), set()
    for path in changed:
        after = _show(head, path)
        if after is None and (_is_py_test(path) or _FRONTEND_TEST.search(path)):
            deleted.add(path)
        if _is_py_test(path):
            removed |= tests_in(path, _show(fork, path)) - tests_in(path, after)
        elif _FRONTEND_TEST.search(path) and after is None:
            removed.add(path)
        elif path.endswith(".md"):
            reqs |= req_ids(_show(fork, path)) - req_ids(after)
    # A REQ moved from one file to another was not removed.
    moved = set().union(*(req_ids(_show(head, p)) for p in changed if p.endswith(".md")))
    return removed, deleted, reqs - moved


def main(argv: list[str]) -> int:
    base, body_file, head = argv[1], argv[2], argv[3] if len(argv) > 3 else "HEAD"
    with open(body_file, encoding="utf-8") as f:
        body = f.read()
    missing = undeclared(*removals(base, head), body)
    if not any(missing.values()):
        print("ok: every removed test and requirement is declared in the PR body")
        return 0
    print("This PR removes tests or intent requirements its body does not declare.")
    print("Add each under the heading shown (then re-run this job), or restore it:\n")
    for kind, ids in missing.items():
        if ids:
            print(f"## Removed {kind}")
            print("\n".join(f"- {i}" for i in ids) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
