"""Statically enforce the shape rules `docs/testing.md` states in prose.

Mechanical only, in the style of `dev/check_docs_coverage.py`: each rule here
is something an AST walk can decide without running anything. What actually
proves a test pins something -- the mutate-and-watch-it-fail procedure --
needs a human or an agent, and stays out of this script on purpose.

Four checks:

  (a) every `pytest.mark.e2e` names at least one CLI. The runtime version of
      this lives in `tests/conftest.py`'s `pytest_collection_modifyitems`;
      this is the same rule, caught before collection.
  (b) no test outside the e2e tier calls a real `bd`/`claude`/`gh`/`glab`
      binary via `subprocess`. The autouse fixtures in `tests/conftest.py`
      are the real guard, at test time; this is belt-and-braces, on the
      source. A test (or a helper nested inside one) that carries an
      `e2e("...")` marker -- on itself, its class's `pytestmark`, or its
      module's `pytestmark` -- is exempt; nothing else is, including a
      module-level helper function with no test wrapped around it, because
      there is nothing here to say which tests would call it safely.
  (c) a per-file line budget for tests/**. A file already over budget when
      this check was written is allowlisted at its current size -- it may
      shrink, never grow past that, and a new file starts at the same
      budget as everything else. Two more rules keep that allowlist itself
      honest rather than a one-way ratchet that only ever loosens: an entry
      for a file that has shrunk to LINE_BUDGET or under is stale (remove
      it), and an entry whose recorded ceiling sits more than
      STALE_ALLOWLIST_MARGIN lines above the file's real current size is
      also stale (tighten it) -- otherwise nothing stops a file shrinking
      once and then quietly regrowing most of the way back up under a
      ceiling nobody revisited.
  (d) every `def test_*`/`async def test_*` has an `assert`, a
      `pytest.raises`/`pytest.warns`/`pytest.deprecated_call`, an
      `assert*`-named call, or delegates to a same-module helper function
      that does. Measured against this tree: 9 tests flagged without one of
      the above; two are genuinely unassertable beyond "did not raise" (a
      swallow-and-return-None guard with no observable side effect) and
      stay in EXPECTATION_ALLOWLIST, each with an inline note saying so.
      The other seven got an explicit assertion instead of an allowlist
      entry once one was possible -- one of them (`test_shipped_default_
      chain_validates`) had been calling a function that *returns* error
      strings rather than raising, and discarding the result, which this
      rule caught as a real bug, not a false positive. An allowlisted test
      id that no longer exists (renamed, deleted, moved) is also a
      violation -- nothing here says the omission was re-earned.

A file this script cannot parse is a failure, not a skip: a checker that
reads a parse error as "nothing to check here" is the exact bug this
project keeps finding in the wild (a chain that swallows an error into
"done" is how work never surfaces as failed) -- this script must not repeat
it on itself.

Run directly: `uv run python dev/check_tests.py`, or `just check-tests`.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent
TESTS = ROOT / "tests"

LINE_BUDGET = 800

#: Files already over LINE_BUDGET when this check was last regenerated
#: (`wc -l`, 2026-09-21, tip of claude/v1-test-guideline after merging #92
#: and #93). A file here may shrink -- and should, over time -- but must
#: never grow past the number recorded, and `check_line_budget_allowlist_
#: is_current` fails the build if the recorded ceiling drifts more than
#: STALE_ALLOWLIST_MARGIN lines above the file's real size, or if the file
#: has shrunk to LINE_BUDGET or under (the entry is then dead weight, not a
#: ceiling). A file that isn't here has never earned an exception: it is
#: held to LINE_BUDGET from the day it's added.
LINE_BUDGET_ALLOWLIST: dict[str, int] = {
    "tests/adapters/test_agent.py": 856,
    "tests/executor/test_gates.py": 903,
    "tests/executor/test_dispatch.py": 898,
    "tests/executor/test_walk.py": 1287,
}

#: How far an allowlisted ceiling may sit above the file's real current size
#: before the check demands it be tightened down to match. Not zero: a
#: one-line edit to an allowlisted file shouldn't force an allowlist edit in
#: the same commit. Not large either -- the whole point of a ratchet is that
#: it only ever tightens, so a wide margin is just a slower version of the
#: rot this exists to catch. 20 lines is under a screenful either way.
STALE_ALLOWLIST_MARGIN = 20

#: The real CLIs a unit-tier test may not shell out to. Not `git`: `git` is
#: real everywhere on purpose (worktrees, rebases and submodules are the
#: product), only these four are mocked at their adapter seam.
GUARDED_BINARIES = {"bd", "claude", "gh", "glab"}

_SUBPROCESS_CALL_NAMES = {"run", "Popen", "call", "check_call", "check_output"}

#: `path::qualname` pairs for a `def test_` with no assert/raises/warns
#: anywhere in its own body or a same-module helper it calls. Each entry
#: earns its place with a one-line reason: the function under test returns
#: nothing and has no observable side effect, so "it did not raise" is the
#: only honest thing left to assert, and a sibling test elsewhere proves the
#: negative path does raise. Every other test that used to have this shape
#: got a real assertion instead -- see the module docstring's (d).
#: `check_expectation_allowlist_is_current` fails the build if an entry's
#: test id no longer exists.
EXPECTATION_ALLOWLIST: set[str] = {
    # lifecycle._terminate(pid) swallows ProcessLookupError/PermissionError
    # and returns None either way -- no return value, no state it touches
    # that the test could inspect instead.
    "tests/test_pause_resume.py::test_terminate_still_swallows_a_process_that_is_already_gone",
}


def _test_files() -> list[Path]:
    return sorted(TESTS.rglob("*.py"))


def _parse(path: Path) -> tuple[ast.Module | None, str | None]:
    """`(tree, None)` on success, `(None, message)` on a parse failure --
    never silently `None, None`: a caller that gets a failure must count it,
    not skip the file. `OSError` (permission denied, gone between listing
    and reading) counts the same as a syntax error or a bad encoding: every
    one of them is "could not check this file", not "nothing to check"."""
    try:
        return ast.parse(path.read_text(), filename=str(path)), None
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        return None, f"{path.relative_to(ROOT)}: could not parse ({exc})"


# ---------------------------------------------------------------------------
# (a) every e2e marker names a CLI
# ---------------------------------------------------------------------------


def _is_e2e_mark_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "e2e"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "mark"
    )


def _is_bare_e2e_mark(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "e2e"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
    )


def _e2e_sites(expr: ast.AST):
    """Every `pytest.mark.e2e` usage reachable from a decorator, a
    `pytestmark` assignment, or a `marks=` keyword -- including through a
    list/tuple of marks, the shape `pytest.param(..., marks=[...])` and a
    module's own `pytestmark = [...]` both use."""
    if isinstance(expr, (ast.List, ast.Tuple)):
        for elt in expr.elts:
            yield from _e2e_sites(elt)
        return
    if _is_e2e_mark_call(expr) or _is_bare_e2e_mark(expr):
        yield expr


def check_e2e_names_cli(tree: ast.Module, relpath: str) -> list[str]:
    violations = []
    sites: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                sites.extend(_e2e_sites(dec))
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            sites.extend(_e2e_sites(node.value))
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "marks":
                    sites.extend(_e2e_sites(kw.value))
    # A zero-arg `pytest.mark.e2e(...)` call is also a defect wherever it
    # appears, decorator or not -- collect those directly too.
    for node in ast.walk(tree):
        if _is_e2e_mark_call(node):
            sites.append(node)
    seen = set()
    for site in sites:
        if id(site) in seen:
            continue
        seen.add(id(site))
        if isinstance(site, ast.Call):
            if not site.args:
                violations.append(
                    f"{relpath}:{site.lineno}: @pytest.mark.e2e() needs at least one "
                    f'CLI name, e.g. e2e("bd")'
                )
        else:
            violations.append(
                f"{relpath}:{site.lineno}: pytest.mark.e2e used with no call and no "
                f'CLI name, e.g. e2e("bd")'
            )
    return violations


# ---------------------------------------------------------------------------
# (b) no real bd/claude/gh/glab outside the e2e tier
# ---------------------------------------------------------------------------


def _decorator_list_is_e2e(decorator_list: list[ast.AST]) -> bool:
    # `_e2e_sites` is a generator function: `_e2e_sites(dec)` alone is a
    # generator *object*, always truthy regardless of what it yields, so
    # `any(_e2e_sites(dec) for dec in ...)` checked the objects' truthiness,
    # never their contents -- any decorator at all (`@pytest.mark.
    # parametrize`, `@pytest.fixture`, ...) made this return True. Consuming
    # each one via `list(...)` first (as every other caller of `_e2e_sites`
    # already does) fixes it.
    return any(list(_e2e_sites(dec)) for dec in decorator_list)


def _module_pytestmark_is_e2e(tree: ast.Module) -> bool:
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets
        ):
            if list(_e2e_sites(stmt.value)):
                return True
    return False


def _class_pytestmark_is_e2e(cls: ast.ClassDef) -> bool:
    if _decorator_list_is_e2e(cls.decorator_list):
        return True
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets
        ):
            if list(_e2e_sites(stmt.value)):
                return True
    return False


def _first_arg_binary(call: ast.Call) -> str | None:
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return Path(head.value).name
    return None


def _guarded_calls_in(node: ast.AST):
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr in _SUBPROCESS_CALL_NAMES
        ):
            binary = _first_arg_binary(sub)
            if binary in GUARDED_BINARIES:
                yield sub, binary


def _is_pytest_collected(relpath: str) -> bool:
    """pytest's own default `python_files` patterns: `test_*.py`/`*_test.py`.
    `tests/support/**` (harness.py, fake_beads.py, ...) is deliberately out
    of scope -- it is fixture/fake infrastructure, never itself collected as
    a test module, and some of it (`fake_beads.Bd._cli`, `harness._git`'s bd
    counterpart) *is* the real-CLI path an `e2e` test asks for through the
    `bd` fixture. What matters is that no *test* reaches it unmarked; the
    runtime guard in tests/conftest.py is what actually stops that."""
    name = Path(relpath).name
    return name.startswith("test_") or name.endswith("_test.py")


def check_no_real_cli_outside_e2e(tree: ast.Module, relpath: str) -> list[str]:
    if not _is_pytest_collected(relpath):
        return []
    violations = []
    module_e2e = _module_pytestmark_is_e2e(tree)

    def handle_function(fn: ast.AST, e2e: bool) -> None:
        for call, binary in _guarded_calls_in(fn):
            if not e2e:
                violations.append(
                    f"{relpath}:{call.lineno}: real {binary!r} reached via "
                    f"subprocess outside the e2e tier -- mark the test "
                    f'e2e("{binary}") or use the fake'
                )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_e2e = module_e2e or _decorator_list_is_e2e(node.decorator_list)
            if node.name.startswith("test_"):
                handle_function(node, fn_e2e)
            else:
                # A module-level helper, not itself a test: nothing here
                # says which test would call it safely, so it is held to
                # the module's own e2e status only.
                handle_function(node, module_e2e)
        elif isinstance(node, ast.ClassDef):
            class_e2e = module_e2e or _class_pytestmark_is_e2e(node)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    item_e2e = class_e2e or _decorator_list_is_e2e(item.decorator_list)
                    handle_function(item, item_e2e if item.name.startswith("test_") else class_e2e)
    return violations


# ---------------------------------------------------------------------------
# (c) per-file line budget
# ---------------------------------------------------------------------------


def _line_count(path: Path) -> int | None:
    """None on an unreadable file (permission denied, gone between listing
    and reading) rather than letting `OSError` crash the script -- `_parse`'s
    call on the same path is what reports the violation naming the file."""
    try:
        with path.open(encoding="utf-8", errors="surrogateescape") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def check_line_budget(path: Path, relpath: str) -> list[str]:
    lines = _line_count(path)
    if lines is None:
        return []  # unreadable -- _parse reports this file's violation instead
    budget = LINE_BUDGET_ALLOWLIST.get(relpath, LINE_BUDGET)
    if lines > budget:
        if relpath in LINE_BUDGET_ALLOWLIST:
            return [
                f"{relpath}: {lines} lines, over its allowlisted ceiling of {budget} "
                f"(dev/check_tests.py:LINE_BUDGET_ALLOWLIST) -- it may shrink, not grow"
            ]
        return [
            f"{relpath}: {lines} lines, over the {LINE_BUDGET}-line budget -- split it, "
            f"or add it to LINE_BUDGET_ALLOWLIST in dev/check_tests.py with a reason"
        ]
    return []


def check_line_budget_allowlist_is_current(actual_lines: dict[str, int]) -> list[str]:
    """The ratchet only tightens: an allowlisted file that no longer exists,
    has shrunk to the ordinary budget or under, or has shrunk further than
    its recorded ceiling admits is stale, not merely generous."""
    violations = []
    for relpath, ceiling in LINE_BUDGET_ALLOWLIST.items():
        actual = actual_lines.get(relpath)
        if actual is None:
            violations.append(
                f"LINE_BUDGET_ALLOWLIST names {relpath!r}, which no longer exists in "
                f"tests/ -- remove the entry from dev/check_tests.py"
            )
        elif actual <= LINE_BUDGET:
            violations.append(
                f"LINE_BUDGET_ALLOWLIST[{relpath!r}] = {ceiling} is stale: the file is "
                f"now {actual} lines, at or under the {LINE_BUDGET}-line budget -- "
                f"remove the entry"
            )
        elif ceiling - actual > STALE_ALLOWLIST_MARGIN:
            violations.append(
                f"LINE_BUDGET_ALLOWLIST[{relpath!r}] = {ceiling} sits {ceiling - actual} "
                f"lines above the file's real {actual} -- tighten it to {actual} "
                f"(margin is {STALE_ALLOWLIST_MARGIN} lines)"
            )
    return violations


# ---------------------------------------------------------------------------
# (d) every test has an expectation
# ---------------------------------------------------------------------------


def _is_expectation_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    else:
        name = None
    return bool(name) and (
        name in ("raises", "warns", "deprecated_call") or name.startswith("assert")
    )


def _has_expectation(node: ast.AST, funcs_by_name: dict[str, ast.AST], seen: set[str]) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assert):
            return True
        if isinstance(sub, ast.Call):
            if _is_expectation_call(sub):
                return True
            if (
                isinstance(sub.func, ast.Name)
                and sub.func.id in funcs_by_name
                and sub.func.id not in seen
            ):
                seen.add(sub.func.id)
                if _has_expectation(funcs_by_name[sub.func.id], funcs_by_name, seen):
                    return True
    return False


def check_every_test_has_an_expectation(tree: ast.Module, relpath: str) -> list[str]:
    violations = []
    funcs_by_name = {
        n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for node in ast.walk(tree):
        is_test_fn = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if is_test_fn and node.name.startswith("test_"):
            qualname = f"{relpath}::{node.name}"
            if qualname in EXPECTATION_ALLOWLIST:
                continue
            if not _has_expectation(node, funcs_by_name, set()):
                violations.append(
                    f"{relpath}:{node.lineno}: {node.name} has no assert, pytest.raises/"
                    f"warns, or assert*-helper -- or add it to EXPECTATION_ALLOWLIST in "
                    f"dev/check_tests.py with the same justification as its neighbours"
                )
    return violations


def _test_qualnames(tree: ast.Module, relpath: str) -> set[str]:
    return {
        f"{relpath}::{n.name}"
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
    }


def check_expectation_allowlist_is_current(all_test_ids: set[str]) -> list[str]:
    """An allowlisted id that isn't a real test any more -- renamed, deleted,
    moved to another file -- is an exception nobody re-earned. Its absence
    should have deleted the entry; failing here says so instead of the
    allowlist quietly protecting nothing."""
    return [
        f"EXPECTATION_ALLOWLIST names {qualname!r}, which no longer exists -- "
        f"remove the entry from dev/check_tests.py"
        for qualname in sorted(EXPECTATION_ALLOWLIST)
        if qualname not in all_test_ids
    ]


# ---------------------------------------------------------------------------


def main() -> int:
    violations: list[str] = []
    actual_lines: dict[str, int] = {}
    all_test_ids: set[str] = set()
    for path in _test_files():
        relpath = path.relative_to(ROOT).as_posix()
        lines = _line_count(path)
        if lines is not None:
            actual_lines[relpath] = lines
        violations.extend(check_line_budget(path, relpath))
        tree, error = _parse(path)
        if error is not None:
            violations.append(error)
            continue
        all_test_ids |= _test_qualnames(tree, relpath)
        violations.extend(check_e2e_names_cli(tree, relpath))
        violations.extend(check_no_real_cli_outside_e2e(tree, relpath))
        violations.extend(check_every_test_has_an_expectation(tree, relpath))

    violations.extend(check_line_budget_allowlist_is_current(actual_lines))
    violations.extend(check_expectation_allowlist_is_current(all_test_ids))

    if violations:
        for v in violations:
            print(v)
        print(f"\n{len(violations)} violation(s)")
        return 1
    print(
        "ok: e2e markers name a CLI, no unit test reaches a real bd/claude/gh/glab, "
        "every tests/** file is in budget, every test has an expectation"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
