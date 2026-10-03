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
removal plus an addition: list the old id. A parametrize case the PR drops
counts too (`path::name[id]`, its ids read off the source when they can be),
and so does a test it newly marks `@pytest.mark.skip`, by its own id. A test
removed whole is declared by its id, cases and all. Anything after the id on a
line is free text for the reviewer; a heading may end in a colon.

A fold declares its cluster in one line: an id whose test name ends in `*`
covers every removed test in that same file whose id starts with the rest.

    - tests/test_notify.py::test_get_notify_with_* -- folded into
      tests/test_notify.py::test_get_notify_reports_a_bad_config_file_cleanly[...]

The path stays literal (a wildcard across files would hide a revert, the very
thing this checks for), the prefix must say more than `test_`, the line must
name a `path::test` that exists at HEAD (the replacement), and a wildcard that
covers nothing is reported, so a typo in the prefix cannot pass in silence.

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
_COMMENT = re.compile(r"<!--.*?-->", re.S)
#: A ``` or ~~~ fence, to a closing run of the same character at least as
#: long, or to the end of the body when it is never closed (as CommonMark).
#: Stripping one hides nothing a reviewer reads as live: a declaration lost
#: to it is reported missing, never passed.
_FENCE = re.compile(
    r"^ {0,3}(?P<run>(?P<c>[`~])(?P=c){2,})[^\n]*$.*?(?:^ {0,3}(?P=run)(?P=c)*[ \t]*$|\Z)",
    re.S | re.M,
)
_SECTIONS = {"removed tests": "tests", "removed requirements": "requirements"}
#: ponytail: frontend tests count per file, not per `it(...)` -- parsing TS
#: is not worth it until a frontend-only revert slips through.
_FRONTEND_TEST = re.compile(r"\.(test|spec)\.[jt]sx?$")


def _is_py_test(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _test_functions(path: str, source: str | None):
    """`(id, function, skipped by its module or class)` for every top-level
    test function (`path::name`) and test method (`path::Class::name`). A
    file that does not parse raises: a checker that cannot read a file must
    not read it as "nothing removed"."""
    if source is None:
        return
    tree = ast.parse(source, filename=path)
    module_skipped = any(_skips(m) for m in _pytestmark(tree.body))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            yield f"{path}::{node.name}", node, module_skipped
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            class_skipped = module_skipped or any(
                _skips(m) for m in [*node.decorator_list, *_pytestmark(node.body)]
            )
            for f in node.body:
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name.startswith(
                    "test"
                ):
                    yield f"{path}::{node.name}::{f.name}", f, class_skipped


def _pytestmark(body: list[ast.stmt]) -> list[ast.AST]:
    marks: list[ast.AST] = []
    for stmt in body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets
        ):
            value = stmt.value
            marks.extend(value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value])
    return marks


def _mark_name(expr: ast.AST) -> str | None:
    """`skip` for `pytest.mark.skip` or `pytest.mark.skip(reason=...)`."""
    if isinstance(expr, ast.Call):
        expr = expr.func
    if (
        isinstance(expr, ast.Attribute)
        and isinstance(expr.value, ast.Attribute)
        and expr.value.attr == "mark"
    ):
        return expr.attr
    return None


def _skips(expr: ast.AST) -> bool:
    """An unconditional skip: a `skipif` still runs where its condition is
    false, so only `skip` counts."""
    return _mark_name(expr) == "skip"


def _case_id(value: ast.AST, argnames: list[str], index: int) -> str:
    """pytest's id for one `argvalues` entry with no `ids=`: a constant's own
    text, joined with `-` across argnames; anything else, argname+index."""
    values = (
        value.elts if len(argnames) > 1 and isinstance(value, (ast.Tuple, ast.List)) else [value]
    )
    parts = []
    for name, v in zip(argnames, values, strict=False):
        negative = isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.USub)
        constant = v.operand if negative else v
        if isinstance(constant, ast.Constant) and isinstance(
            constant.value, str | int | float | bool | None
        ):
            if negative and not isinstance(constant.value, int | float):
                parts.append(f"{name}{index}")
            else:
                parts.append(("-" if negative else "") + str(constant.value))
        else:
            parts.append(f"{name}{index}")
    return "-".join(parts)


def _case_ids(dec: ast.AST) -> list[str] | None:
    """The ids `@pytest.mark.parametrize(...)` gives its cases, or None when
    they cannot be read off the source (argvalues built at runtime)."""
    if not isinstance(dec, ast.Call) or _mark_name(dec) != "parametrize" or len(dec.args) < 2:
        return None
    names, values = dec.args[0], dec.args[1]
    if isinstance(names, ast.Constant) and isinstance(names.value, str):
        argnames = [n.strip() for n in names.value.split(",") if n.strip()]
    elif isinstance(names, (ast.List, ast.Tuple)):
        argnames = [n.value for n in names.elts if isinstance(n, ast.Constant)]
    else:
        return None
    if not isinstance(values, (ast.List, ast.Tuple)):
        return None
    ids_kw = next((k.value for k in dec.keywords if k.arg == "ids"), None)
    if ids_kw is not None:
        if not isinstance(ids_kw, (ast.List, ast.Tuple)):
            return None
        explicit = [e.value if isinstance(e, ast.Constant) else None for e in ids_kw.elts]
    else:
        explicit = [None] * len(values.elts)
    ids = []
    for i, value in enumerate(values.elts):
        given = explicit[i] if i < len(explicit) else None
        if isinstance(value, ast.Call) and getattr(value.func, "attr", None) == "param":
            kw = {k.arg: k.value for k in value.keywords}
            if isinstance(kw.get("id"), ast.Constant):
                given = kw["id"].value
            value = ast.Tuple(elts=value.args) if len(value.args) != 1 else value.args[0]
        ids.append(str(given) if given is not None else _case_id(value, argnames, i))
    return ids


def _cases(fn: ast.AST) -> list[str]:
    """`[id]` suffixes for every case of a parametrized test, [] otherwise.
    Stacked decorators multiply, the one nearest the function first, as
    pytest names them."""
    cases = [""]
    for dec in reversed(fn.decorator_list):
        ids = _case_ids(dec)
        if ids is None:
            continue
        cases = [f"{c}-{i}" if c else i for c in cases for i in ids]
    return [] if cases == [""] else [f"[{c}]" for c in cases]


def tests_in(path: str, source: str | None) -> set[str]:
    """Every test id in `source`: `path::name`, `path::Class::name`, and
    `path::name[id]` for each case of a parametrize whose ids can be read off
    the source, so a dropped case counts as removed."""
    ids = set()
    for test_id, fn, _ in _test_functions(path, source):
        ids.add(test_id)
        ids.update(f"{test_id}{case}" for case in _cases(fn))
    return ids


def skipped_in(path: str, source: str | None) -> set[str]:
    """The test ids an unconditional `@pytest.mark.skip` takes out of the run,
    on the test itself, its class, or the module's `pytestmark`."""
    return {
        test_id
        for test_id, fn, inherited in _test_functions(path, source)
        if inherited or any(_skips(d) for d in fn.decorator_list)
    }


def req_ids(text: str | None) -> set[str]:
    return set(_REQ.findall(text or ""))


def _entries(body: str):
    """`(section, id, free-text words)` for each line under a `Removed ...`
    heading: the id is the first word, bullets and backticks stripped. An HTML
    comment says nothing: the PR template's example sections sit in one, and a
    reviewer never sees it. Nor does a fenced code block, which a reviewer
    reads as an example (a body showing how to write a wildcard)."""
    section = None
    for line in _FENCE.sub("", _COMMENT.sub("", body)).splitlines():
        if heading := _HEADING.match(line):
            # `## Removed tests:` is the same heading; a reviewer reads it so.
            section = _SECTIONS.get(heading.group(1).lower().rstrip(":").rstrip())
        elif section and (words := line.strip().lstrip("-*+ ").split()):
            yield section, words[0].strip("`"), words[1:]


def declared(body: str) -> dict[str, set[str]]:
    """The ids listed under each `Removed ...` heading, as written: a
    wildcard is kept raw here and read by `_wildcards`."""
    out: dict[str, set[str]] = {"tests": set(), "requirements": set()}
    for section, entry, _ in _entries(body):
        out[section].add(entry)
    return out


def _named(words: list[str]) -> set[str]:
    """The `path::test` ids a line's free text names, quotes and trailing
    punctuation stripped; `name[case]` and `name[...]` also name `name`."""
    ids = set()
    for word in words:
        word = word.strip("`'\"()<>,.;:!?")
        if "::" in word:
            ids |= {word, word.split("[")[0]}
    return ids


def named_tests(body: str) -> set[str]:
    """Every id the wildcard lines of `Removed tests` name as a replacement:
    what the check has to look up at HEAD."""
    wild = (text for kind, entry, text in _entries(body) if kind == "tests" and "*" in entry)
    return set().union(*(_named(text) for text in wild))


#: The prefix before a wildcard's `*` has to say more than this: `test_*`
#: covers every test in the file, which is a deleted file without the delete.
_BARE_PREFIX = "test_"


def _refusal(pattern: str, text: list[str], at_head: set[str]) -> str | None:
    """Why the wildcard `pattern` declares nothing, or None when it may."""
    path, sep, name = pattern.rpartition("::")
    if not sep or "*" in path:
        return (
            "a wildcard is `path::test_prefix*` and the path stays literal: name one file "
            "(a deleted file is declared by its path alone, with no `*`)"
        )
    if name.count("*") != 1 or not name.endswith("*"):
        return "a wildcard has one `*`, at the end of the test name"
    if len(name) - 1 <= len(_BARE_PREFIX):
        return (
            f"the prefix before `*` must be longer than `{_BARE_PREFIX}`: "
            "name the cluster (`test_get_notify_with_*`)"
        )
    if not _named(text) & at_head:
        return (
            "name the test that replaces the cluster, as `path::test` after the wildcard; "
            "it must exist at HEAD"
        )
    return None


def _wildcards(body: str, at_head: set[str]) -> tuple[list[str], list[str]]:
    """(the prefix each usable wildcard in `Removed tests` covers, a problem
    line for each refused one).

    Matching is on the prefix, not `fnmatch`: a wildcard is one trailing `*`,
    so the two agree except that fnmatch reads `[` as a character class, and
    `test_y[*` must cover the parametrize case `test_y[refused]`."""
    prefixes, problems = [], []
    for section, entry, text in _entries(body):
        if section != "tests" or "*" not in entry:
            continue
        if why := _refusal(entry, text, at_head):
            problems.append(f"- {entry} -- {why}")
        else:
            prefixes.append(entry[:-1])
    return prefixes, problems


def wildcard_problems(removed: set[str], body: str, at_head: set[str]) -> list[str]:
    """A line for each wildcard in `Removed tests` that declares nothing:
    refused, or covering no test `removed` holds (a typo in the prefix)."""
    prefixes, problems = _wildcards(body, at_head)
    return problems + [
        f"- {p}* -- matches no test this PR removes: check the prefix for a typo"
        for p in prefixes
        if not any(t.startswith(p) for t in removed)
    ]


def undeclared(
    removed: set[str],
    deleted: set[str],
    removed_reqs: set[str],
    body: str,
    at_head: set[str] = frozenset(),
) -> dict[str, list[str]]:
    """What `removed` (test ids and frontend test file paths) and
    `removed_reqs` leave out of the body. A test in a `deleted` file is
    declared by that file's path too, a case of a test removed whole by
    that test's id, and a test by a usable wildcard covering it (`at_head`:
    the test ids that exist at HEAD, which a wildcard's replacement must be
    one of; with none given, no wildcard declares anything)."""
    listed = declared(body)["tests"]
    prefixes, _ = _wildcards(body, at_head)

    def said(t: str) -> bool:
        path = t.split("::")[0]
        test = t.split("[")[0]
        return (
            t in listed
            or (path in deleted and path in listed)
            or any(t.startswith(p) for p in prefixes)
            or (test != t and test in removed and said(test))
        )

    reqs = removed_reqs - declared(body)["requirements"]
    missing = {t for t in removed if not said(t)}
    # A test removed whole is named once, not once more per case.
    tests = sorted(t for t in missing if t.split("[")[0] == t or t.split("[")[0] not in missing)
    return {"tests": tests, "requirements": sorted(reqs)}


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
            before = _show(fork, path)
            removed |= tests_in(path, before) - tests_in(path, after)
            # A test that newly skips is gone from the run as surely.
            removed |= (skipped_in(path, after) - skipped_in(path, before)) & tests_in(path, before)
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
    removed, deleted, reqs = removals(base, head)
    paths = {t.split("::")[0] for t in named_tests(body)}
    at_head = set().union(*(tests_in(p, _show(head, p)) for p in paths if _is_py_test(p)))
    missing = undeclared(removed, deleted, reqs, body, at_head)
    problems = wildcard_problems(removed, body, at_head)
    if not any(missing.values()) and not problems:
        print("ok: every removed test and requirement is declared in the PR body")
        return 0
    if problems:
        # Printed first, so the block below still runs to the end, ready to paste.
        print("These wildcards under `Removed tests` declare nothing. Fix each line:\n")
        print("\n".join(problems) + "\n")
    if any(missing.values()):
        print("This PR removes tests or intent requirements its body does not declare.")
        print("Add each under the heading shown (then re-run this job), or restore it:\n")
        for kind, ids in missing.items():
            if ids:
                print(f"## Removed {kind}")
                print("\n".join(f"- {i}" for i in ids) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
