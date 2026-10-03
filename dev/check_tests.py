"""Statically enforce the shape rules `docs/testing.md` states in prose.

Mechanical only, in the style of `dev/check_docs_coverage.py`: each rule here
is something an AST walk can decide without running anything. What actually
proves a test pins something -- the mutate-and-watch-it-fail procedure --
needs a human or an agent, and stays out of this script on purpose.

Five checks, over both testpaths (`tests/` and `plugins/kraft-lite/tests/`):

  (a) every `pytest.mark.e2e` names at least one CLI. The runtime version of
      this lives in `tests/conftest.py`'s `pytest_collection_modifyitems`;
      this is the same rule, caught before collection.
  (b) no test outside the e2e tier launches a real `bd`, agent CLI or
      `gh`/`glab` (GUARDED_BINARIES): through `subprocess` (an argv
      positional or `args=`, or a shell string), `asyncio`'s
      `create_subprocess_exec`/`_shell`, `os.system`/`popen`/`exec*`, or the
      forge adapter's `run_git(repo, argv)`; unwrapping `env`, `nohup` and
      `sh -c "<line>"`, and following a variable or a module function back
      to the argv it holds or returns. The autouse fixtures in
      `tests/conftest.py` are the real guard, at test time; this is
      belt-and-braces, on the source. A test (or a helper nested inside one)
      that carries an `e2e("...")` marker -- on itself, its class's
      `pytestmark`, or its module's `pytestmark` -- is exempt; nothing else
      is, including a module-level helper function or a module-level table
      with no test wrapped around it, because there is nothing here to say
      which tests would call it safely. A file in REAL_CLI_ALLOWLIST is
      exempt for the reason it gives, until it has no hit left.
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
      violation -- nothing here says the omission was re-earned. An
      expectation that cannot fail does not count: `assert True` (or
      `x or True`, or a non-empty tuple), `pytest.raises(Exception)` with no
      `match=`, an assert in the branch an `if <constant>` rules out, and
      one only inside a nested function the test never names.
  (e) no helper body is copied across test files. A module-level function
      not named `test_*` whose `helper_fingerprint` matches one in another
      file is a violation per copy beyond the first, naming the copy and
      the original. The fingerprint is the body once its docstring,
      annotations and keyword order are gone, plus the value of every
      module-level constant it names: the same `return {**DEFAULTS, **f}`
      over nine different `DEFAULTS` is nine helpers. It comes from
      `dev/test_shape_report.py`, loaded by path, so the report's
      `--print-helper-allowlist` keys and this rule's cannot disagree. When
      the original is a public function in `tests/support/`, the message
      names what to import (`use support.harness.commit_all`). The groups
      that existed when the rule landed are in DUPLICATE_HELPER_ALLOWLIST at
      their copy count, keyed `<name>#<sha1[:8]>`, with the same ratchet as
      (c): a count may shrink, never grow, and an entry left with one copy,
      or more than STALE_ALLOWLIST_MARGIN_COPIES under its ceiling, is
      stale. Rule (c) is satisfied by splitting a file; this is what makes
      a split safe.

A file this script cannot parse is a failure, not a skip: a checker that
reads a parse error as "nothing to check here" is the exact bug this
project keeps finding in the wild (a chain that swallows an error into
"done" is how work never surfaces as failed) -- this script must not repeat
it on itself.

Run directly: `uv run python dev/check_tests.py`, or `just check-tests`.
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).parent.parent
TESTS = ROOT / "tests"
#: The other testpath (pyproject `testpaths`), relative to ROOT.
PLUGIN_TESTS = Path("plugins") / "kraft-lite" / "tests"

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

#: The real CLIs a unit-tier test may not shell out to: `bd`, every agent CLI
#: and both forge CLIs, the names `tests/support/real_binaries.py` stubs out
#: at runtime (tests/test_check_tests.py keeps the two lists equal). Not
#: `git`: `git` is real everywhere on purpose (worktrees, rebases and
#: submodules are the product); these are mocked at their adapter seam.
GUARDED_BINARIES = {
    "bd",
    *("claude", "codex", "gemini", "opencode", "amp", "agent", "cursor-agent", "cursor", "agy"),
    *("gh", "glab"),
}

#: `subprocess.<name>(argv)`, the argv first or as `args=`. Only `Popen`,
#: `check_call` and `check_output` count when imported bare: a bare `run` or
#: `call` is as likely a fixture of the test's own.
_ARGV_CALLS = {"run", "Popen", "call", "check_call", "check_output"}
_BARE_ARGV_CALLS = {"Popen", "check_call", "check_output"}
#: The program first, its arguments after (`asyncio.create_subprocess_exec`).
_PROGRAM_CALLS = {"create_subprocess_exec", "subprocess_exec"}
_EXEC_CALLS = {
    *("execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe"),
    *("posix_spawn", "posix_spawnp"),
}
#: A shell command line first (`os.system`, `asyncio.create_subprocess_shell`).
_SHELL_CALLS = {"system", "popen", "getoutput", "getstatusoutput"} | {
    "create_subprocess_shell",
    "subprocess_shell",
}
#: Programs that run the next word as a command, and the shell keywords after
#: which a word is one.
_WRAPPERS = {"env", "nohup", "exec", "command", "sudo", "time", "nice", "xargs"}
_SHELL_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{"}
_SHELLS = {"sh", "bash", "zsh", "dash"}
_SHELL_PUNCTUATION = set("();<>|&")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: Test files whose rule-(b) hits are on purpose, each with its reason. A file
#: here that no longer has one is stale and fails the check.
REAL_CLI_ALLOWLIST: dict[str, str] = {
    # Probes the runtime guard itself: every launch here is refused by the
    # stubs in tests/support/real_binaries.py, and each test asserts that.
    "tests/test_no_real_agent.py": "probes the runtime real-binary guard",
}

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
    # ast.parse(kl.py, feature_version=(3, 10)) raising SyntaxError is the
    # failure; a tree that parsed has nothing further to say about the floor.
    "plugins/kraft-lite/tests/test_skills.py::test_kl_parses_on_the_oldest_supported_python",
}


#: Helper fingerprints duplicated across test files when this rule landed
#: (2026-10-03, `just shape-report`), keyed by the name they go by, valued by
#: how many copies existed. A count may shrink, never grow; an entry whose
#: real count has dropped to 1 is stale (remove it), one whose count sits
#: more than STALE_ALLOWLIST_MARGIN_COPIES below the recorded ceiling is
#: stale (tighten it). A name not here has never earned an exception.
DUPLICATE_HELPER_ALLOWLIST: dict[str, int] = {
    # generated by `just shape-report --print-helper-allowlist`; hand-edit only to tighten
    "_agent#96b3cf64": 4,
    "_agent#c5f8b84d": 7,
    "_agent#e36d73e4": 2,
    "_await_gate#b8ddd1a9": 2,
    "_base_ref#1aaa8a71": 2,
    "_by_name#3083e449": 3,
    "_cap#d8825abc": 2,
    "_capture#601f0018": 2,
    "_claude_profile#e3d12ce4": 2,
    "_closed_port#deb4dc33": 2,
    "_cycles#28a90170": 2,
    "_dispatch#a1fa46d1": 2,
    "_dispatch#d9bd549e": 2,
    "_exec#41010529": 2,
    "_exec#aa452c34": 2,
    "_exec#c0920489": 2,
    "_fallbacks#f411c9ff": 2,
    "_file#4cd7a538": 3,
    "_forge_task#c38ff87f": 2,
    "_head#4682633e": 2,
    "_instance#4798ec17": 2,
    "_invalid_policy#cfd00c64": 2,
    "_launches#d192c64d": 2,
    "_members#6d06bb92": 2,
    "_names#00e0f6fe": 2,
    "_needs_human_reason#ff663548": 2,
    "_node#a3fb972a": 2,
    "_paused#483554e5": 3,
    "_paused_item#7bc47ea9": 2,
    "_policy#2a2ff9e2": 2,
    "_poll_for#cb741314": 2,
    "_porcelain#1bd0b559": 2,
    "_quick_task#4d1620ad": 4,
    "_reason#6b8c673d": 4,
    "_row#3af4ea33": 3,
    "_run#09898476": 2,
    "_run#68c8edcf": 2,
    "_seed#798d1ff4": 2,
    "_set_origin#56ab506e": 2,
    "_snapshot#055f6a1d": 2,
    "_spend#133f7742": 2,
    "_state#31de82fd": 2,
    "_status#f2e14f34": 2,
    "_sub#57868203": 2,
    "_sub#f848d2a4": 2,
    "_system_prompt#09e55c69": 2,
    "_task#0748fc3b": 2,
    "_task#f49e6883": 2,
    "_templates#1e9b30bf": 2,
    "_types#dfdf0027": 3,
    "_unavailable#89429623": 2,
    "_wait#cdd971f8": 2,
    "_wait_for#fa7c68ac": 2,
    "_walk#5bfcee0f": 3,
    "_walk#81571db0": 2,
    "_walk#9b598337": 2,
    "_walk#e4e690bb": 2,
    "_workspace_item#131b1971": 2,
    "_worktree#6fa35a84": 2,
    "agent_task#7964819a": 2,
    "connected#60551aef": 2,
    "ct#bd32a8a1": 2,
    "database#b12a954f": 3,
    "exec_node#8bf2c3fe": 2,
    "forge_node#bc955aa7": 2,
    "policy#d7440099": 2,
    "repo#6b017fb1": 2,
    "resolved#f2661386": 3,
    "run_dir#f0267614": 2,
    "set_value#b3b305c2": 2,
    "snapshot#70705e4f": 5,
    "st#2339d587": 2,
    "templates_dir#2208cebb": 2,
    "templates_dir#356e2663": 3,
}

#: STALE_ALLOWLIST_MARGIN for DUPLICATE_HELPER_ALLOWLIST, in copies: retiring
#: one or two copies shouldn't force an allowlist edit in the same commit.
STALE_ALLOWLIST_MARGIN_COPIES = 2


def _test_files() -> list[Path]:
    """Both testpaths: `tests/` and the kraft-lite plugin's own."""
    return sorted([*TESTS.rglob("*.py"), *(ROOT / PLUGIN_TESTS).rglob("*.py")])


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
# (b) no real bd, agent or forge CLI outside the e2e tier
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


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _launched_expr(call: ast.Call) -> tuple[ast.AST, str] | None:
    """`(the expression naming what `call` launches, how it reads)`, where
    `how` is "argv" (a list, or a string that is a command line), "program"
    (one executable) or "shell" (a command line). None for any other call."""
    name = _call_name(call)
    keyword = {k.arg: k.value for k in call.keywords}
    if name in _ARGV_CALLS and (isinstance(call.func, ast.Attribute) or name in _BARE_ARGV_CALLS):
        expr = keyword.get("args", call.args[0] if call.args else None)
        return (expr, "argv") if expr is not None else None
    if name == "run_git":
        expr = keyword.get("args", call.args[1] if len(call.args) > 1 else None)
        return (expr, "argv") if expr is not None else None
    if not call.args:
        return None
    first = call.args[0]
    if name in _PROGRAM_CALLS or name in _EXEC_CALLS:
        if isinstance(first, ast.Starred):
            return first.value, "argv"
        return first, "program"
    if name in _SHELL_CALLS:
        return first, "shell"
    return None


class _Scope:
    """What a name was bound to, for resolving `subprocess.run(cmd)` back to
    the list `cmd` holds: assignments in the enclosing function, then in the
    module, and the `return` values of the module's own functions."""

    def __init__(self, tree: ast.Module, fn: ast.AST | None):
        self.bindings: dict[str, list[ast.AST]] = {}
        self.returns: dict[str, list[ast.AST]] = {}
        for owner in (fn, tree):
            if owner is None:
                continue
            for node in ast.walk(owner):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self.bindings.setdefault(target.id, []).append(node.value)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if node.value is not None:
                        self.bindings.setdefault(node.target.id, []).append(node.value)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.returns[node.name] = [
                    r.value
                    for r in ast.walk(node)
                    if isinstance(r, ast.Return) and r.value is not None
                ]


def _strings(expr: ast.AST, scope: _Scope, depth: int = 0) -> list[str]:
    """Every string constant `expr` can be, through names it was bound to."""
    if depth > 4:
        return []
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return [expr.value]
    if isinstance(expr, ast.Name):
        return [s for v in scope.bindings.get(expr.id, ()) for s in _strings(v, scope, depth + 1)]
    return []


def _argvs(expr: ast.AST, scope: _Scope, depth: int = 0) -> list[list[ast.AST]]:
    """Every argv list `expr` can be: a literal, a name bound to one, a call to
    a module function that returns one, or the left of a `+`."""
    if depth > 4:
        return []
    if isinstance(expr, (ast.List, ast.Tuple)):
        return [list(expr.elts)]
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _argvs(expr.left, scope, depth + 1)
    if isinstance(expr, ast.Name):
        return [a for v in scope.bindings.get(expr.id, ()) for a in _argvs(v, scope, depth + 1)]
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        return [a for v in scope.returns.get(expr.func.id, ()) for a in _argvs(v, scope, depth + 1)]
    return []


def _shell_heads(line: str) -> list[str]:
    """The words of a shell command line that are run as commands: the first,
    and each after an operator, a `(`, or a wrapper that runs its argument."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:  # an unclosed quote: fall back to whitespace
        words = line.split()
    heads, expect, skip = [], True, False
    for i, word in enumerate(words):
        if skip:
            skip = False
        elif word and set(word) <= _SHELL_PUNCTUATION:
            # A redirect's target is a file, not a command; `;`, `&&`, `|`
            # and `(` start a new one.
            skip = "<" in word or ">" in word
            expect = expect or not skip
        elif not expect or _ASSIGNMENT.match(word) or word.startswith("-"):
            continue  # an argument, `X=1`, or a wrapper's option (`env -i`)
        else:
            name = Path(word).name
            heads.append(name)
            expect = name in _WRAPPERS or name in _SHELL_KEYWORDS
            if name in _SHELLS:
                rest = words[i + 1 :]
                if "-c" in rest[:-1]:
                    heads.extend(_shell_heads(rest[rest.index("-c") + 1]))
    return heads


def _argv_heads(elts: list[ast.AST], scope: _Scope) -> list[str]:
    """The programs an argv runs: its first word, unwrapped through `env`,
    `nohup` and friends, and through `sh -c "<command line>"`."""
    heads: list[str] = []
    for i, elt in enumerate(elts):
        words = _strings(elt, scope)
        if not words:
            return heads
        word = words[0]
        if _ASSIGNMENT.match(word) or (i and word.startswith("-")):
            continue
        name = Path(word).name
        heads.append(name)
        if name in _SHELLS:
            rest = [w for e in elts[i + 1 :] for w in _strings(e, scope)[:1]]
            if "-c" in rest[:-1]:
                heads.extend(_shell_heads(rest[rest.index("-c") + 1]))
            return heads
        if name not in _WRAPPERS:
            return heads
    return heads


def _heads(expr: ast.AST, how: str, scope: _Scope) -> list[str]:
    if how == "shell":
        return [h for s in _strings(expr, scope) for h in _shell_heads(s)]
    if how == "program":
        return [Path(s).name for s in _strings(expr, scope)]
    lines = _strings(expr, scope)  # `run("claude -p hi", shell=True)`
    return [h for s in lines for h in _shell_heads(s)] + [
        h for argv in _argvs(expr, scope) for h in _argv_heads(argv, scope)
    ]


def _guarded_calls_in(node: ast.AST, tree: ast.Module, fn: ast.AST | None = None):
    scope = None
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        launched = _launched_expr(sub)
        if launched is None:
            continue
        scope = scope or _Scope(tree, fn)
        for binary in dict.fromkeys(_heads(*launched, scope)):
            if binary in GUARDED_BINARIES:
                yield sub, binary


def _is_pytest_collected(relpath: str) -> bool:
    """pytest's own default `python_files` patterns: `test_*.py`/`*_test.py`.
    `tests/support/**` (harness.py, fake_beads.py, ...) is deliberately out
    of scope -- it is fixture/fake infrastructure, never itself collected as
    a test module, and some of it (`fake_beads.Bd._cli`, `harness.git`'s bd
    counterpart) *is* the real-CLI path an `e2e` test asks for through the
    `bd` fixture. What matters is that no *test* reaches it unmarked; the
    runtime guard in tests/conftest.py is what actually stops that."""
    name = Path(relpath).name
    return name.startswith("test_") or name.endswith("_test.py")


def real_cli_hits(tree: ast.Module, relpath: str) -> list[str]:
    """Rule (b)'s violations in one file, before `REAL_CLI_ALLOWLIST`."""
    if not _is_pytest_collected(relpath):
        return []
    violations = []
    module_e2e = _module_pytestmark_is_e2e(tree)

    def handle(node: ast.AST, e2e: bool, fn: ast.AST | None) -> None:
        for call, binary in _guarded_calls_in(node, tree, fn):
            if not e2e:
                violations.append(
                    f"{relpath}:{call.lineno}: real {binary!r} reached via "
                    f"a subprocess outside the e2e tier -- mark the test "
                    f'e2e("{binary}") or use the fake'
                )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_e2e = module_e2e or _decorator_list_is_e2e(node.decorator_list)
            if node.name.startswith("test_"):
                handle(node, fn_e2e, node)
            else:
                # A module-level helper, not itself a test: nothing here
                # says which test would call it safely, so it is held to
                # the module's own e2e status only.
                handle(node, module_e2e, node)
        elif isinstance(node, ast.ClassDef):
            class_e2e = module_e2e or _class_pytestmark_is_e2e(node)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    item_e2e = class_e2e or _decorator_list_is_e2e(item.decorator_list)
                    e2e = item_e2e if item.name.startswith("test_") else class_e2e
                    handle(item, e2e, item)
        else:
            # Module-level code (a table of lambdas, a constant argv) runs for
            # whichever test reads it: held to the module's status too.
            handle(node, module_e2e, None)
    return violations


def check_no_real_cli_outside_e2e(tree: ast.Module, relpath: str) -> list[str]:
    if relpath in REAL_CLI_ALLOWLIST:
        return []
    return real_cli_hits(tree, relpath)


def check_real_cli_allowlist_is_current(hits: dict[str, int]) -> list[str]:
    """`hits` is rule (b)'s count per file, allowlist ignored. An entry for a
    file that is gone, or that no longer reaches a guarded CLI, is stale."""
    return [
        f"REAL_CLI_ALLOWLIST names {relpath!r}, which "
        f"{'no longer exists' if relpath not in hits else 'reaches no guarded CLI any more'} "
        f"-- remove the entry from dev/check_tests.py"
        for relpath in sorted(REAL_CLI_ALLOWLIST)
        if not hits.get(relpath)
    ]


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
    if name == "raises" and _catches_anything(call):
        return False
    return bool(name) and (
        name in ("raises", "warns", "deprecated_call") or name.startswith("assert")
    )


_ANY_EXCEPTION = {"Exception", "BaseException"}


def _catches_anything(call: ast.Call) -> bool:
    """`pytest.raises(Exception)` with no `match=`: a typo, a wrong argument or
    an unrelated crash passes it as well as the refusal it means to pin."""
    if not call.args or any(k.arg == "match" for k in call.keywords):
        return False
    first = call.args[0]
    name = first.attr if isinstance(first, ast.Attribute) else getattr(first, "id", None)
    return name in _ANY_EXCEPTION


def _always_true(test: ast.AST) -> bool:
    """`assert True`, `assert x or True`, `assert (x, "message")`."""
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, ast.Tuple):
        return bool(test.elts)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return any(_always_true(v) for v in test.values)
    return False


def _live_nodes(node: ast.AST, referenced: set[str]):
    """`node` and every node under it that can run: not the branch an
    `if <constant>` rules out, and not a nested function nothing names."""
    yield node
    if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
        children = node.body if node.test.value else node.orelse
    else:
        children = ast.iter_child_nodes(node)
    for child in children:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            child.name not in referenced
        ):
            continue
        yield from _live_nodes(child, referenced)


def _has_expectation(node: ast.AST, funcs_by_name: dict[str, ast.AST], seen: set[str]) -> bool:
    referenced = {
        n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    for sub in _live_nodes(node, referenced):
        if isinstance(sub, ast.Assert) and not _always_true(sub.test):
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
# (e) no duplicated helper body across test files
# ---------------------------------------------------------------------------

_SHAPE_REPORT = Path(__file__).resolve().with_name("test_shape_report.py")


@functools.cache
def _shape_report():
    """`dev/test_shape_report.py`, loaded by path on first use: its
    `helper_fingerprint` and `helper_key` are what `--print-helper-allowlist`
    keys DUPLICATE_HELPER_ALLOWLIST with, so this rule must compute the very
    same ones. Loaded here, not at import: the report loads this file too."""
    spec = importlib.util.spec_from_file_location("_dev_test_shape_report_for_check", _SHAPE_REPORT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A NamedTuple, not a dataclass: this file is loaded by path without a
# `sys.modules` entry, which `@dataclass` needs to resolve its annotations.
class _Helper(NamedTuple):
    relpath: str
    lineno: int
    name: str
    #: a public (not `_`-prefixed) function in `tests/support/`: what every
    #: other copy should import instead.
    public_support: bool

    @property
    def dotted(self) -> str:
        """`support.harness.commit_all` for `tests/support/harness.py`'s."""
        parts = Path(self.relpath).with_suffix("").parts
        return ".".join([*parts[parts.index("support") :], self.name])


def duplicate_helper_groups(
    trees: dict[str, ast.Module], support: dict[str, ast.Module]
) -> dict[str, list[_Helper]]:
    """Every module-level non-`test_*` function in `trees` and `support`,
    grouped by `helper_fingerprint` and keyed `<most common name>#<sha1[:8]>`,
    keeping only the groups defined in two or more files. Each group's first
    entry is the one the rest duplicate: a public `tests/support` function
    when one is in the group, else the first copy in path order."""
    report = _shape_report()
    by_fingerprint: dict[str, list[_Helper]] = {}
    for relpath, tree in [*trees.items(), *support.items()]:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not (
                node.name.startswith("test_")
            ):
                public = relpath in support and not node.name.startswith("_")
                by_fingerprint.setdefault(report.helper_fingerprint(node, tree), []).append(
                    _Helper(relpath, node.lineno, node.name, public)
                )
    groups = {}
    for fingerprint, found in by_fingerprint.items():
        if len({h.relpath for h in found}) < 2:
            continue
        names = Counter(h.name for h in found)
        name = min(names, key=lambda n: (-names[n], n))
        groups[report.helper_key(name, fingerprint)] = sorted(
            found, key=lambda h: (not h.public_support, h.relpath, h.lineno)
        )
    return groups


def duplicate_helper_counts(
    trees: dict[str, ast.Module], support: dict[str, ast.Module]
) -> dict[str, int]:
    """Copies per duplicated group, the number DUPLICATE_HELPER_ALLOWLIST records."""
    return {key: len(found) for key, found in duplicate_helper_groups(trees, support).items()}


def check_duplicate_helpers(
    trees: dict[str, ast.Module], support: dict[str, ast.Module]
) -> list[str]:
    """One violation per copy beyond the first of each duplicated helper; an
    allowlisted group only once it has more copies than its recorded ceiling."""
    violations = []
    for key, (original, *copies) in duplicate_helper_groups(trees, support).items():
        at = f"{original.relpath}:{original.lineno}"
        ceiling = DUPLICATE_HELPER_ALLOWLIST.get(key)
        if ceiling is not None:
            if len(copies) + 1 > ceiling:
                fix = (
                    f"use {original.dotted} ({at})"
                    if original.public_support
                    else f"move the copy at {at} into tests/support/ and import it"
                )
                violations.append(
                    f"{key}: {len(copies) + 1} copies, over its allowlisted ceiling of {ceiling} "
                    f"(dev/check_tests.py:DUPLICATE_HELPER_ALLOWLIST) -- it may shrink, not grow: "
                    f"{fix} instead of adding a copy"
                )
            continue
        for dup in copies:
            where = f"{dup.relpath}:{dup.lineno}: {dup.name}() has the same body as"
            violations.append(
                f"{where} {original.dotted} ({at}) -- use {original.dotted} instead"
                if original.public_support
                else f"{where} {original.name}() at {at} -- move it into tests/support/ "
                f"and import it from there"
            )
    return violations


def check_duplicate_helper_allowlist_is_current(counts: dict[str, int]) -> list[str]:
    """The same ratchet as LINE_BUDGET_ALLOWLIST's: an entry whose group is no
    longer duplicated across files (one copy left, or none: `counts` holds
    only groups in two or more files) is stale, and so is one whose ceiling
    sits more than STALE_ALLOWLIST_MARGIN_COPIES above the real count."""
    violations = []
    for key, ceiling in DUPLICATE_HELPER_ALLOWLIST.items():
        actual = counts.get(key)
        if actual is None:
            violations.append(
                f"DUPLICATE_HELPER_ALLOWLIST[{key!r}] = {ceiling} is stale: it is no longer "
                f"duplicated across test files -- remove the entry"
            )
        elif ceiling - actual > STALE_ALLOWLIST_MARGIN_COPIES:
            violations.append(
                f"DUPLICATE_HELPER_ALLOWLIST[{key!r}] = {ceiling} sits {ceiling - actual} "
                f"copies above the real {actual} -- tighten it to {actual} "
                f"(margin is {STALE_ALLOWLIST_MARGIN_COPIES} copies)"
            )
    return violations


# ---------------------------------------------------------------------------


def main() -> int:
    violations: list[str] = []
    actual_lines: dict[str, int] = {}
    all_test_ids: set[str] = set()
    real_cli: dict[str, int] = {}
    trees: dict[str, ast.Module] = {}
    support: dict[str, ast.Module] = {}
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
        (support if path.is_relative_to(TESTS / "support") else trees)[relpath] = tree
        all_test_ids |= _test_qualnames(tree, relpath)
        violations.extend(check_e2e_names_cli(tree, relpath))
        real_cli[relpath] = len(real_cli_hits(tree, relpath))
        violations.extend(check_no_real_cli_outside_e2e(tree, relpath))
        violations.extend(check_every_test_has_an_expectation(tree, relpath))

    violations.extend(check_line_budget_allowlist_is_current(actual_lines))
    violations.extend(check_expectation_allowlist_is_current(all_test_ids))
    violations.extend(check_real_cli_allowlist_is_current(real_cli))
    violations.extend(check_duplicate_helpers(trees, support))
    violations.extend(
        check_duplicate_helper_allowlist_is_current(duplicate_helper_counts(trees, support))
    )

    if violations:
        for v in violations:
            print(v)
        print(f"\n{len(violations)} violation(s)")
        return 1
    print(
        "ok: e2e markers name a CLI, no unit test reaches a real bd/agent/forge CLI, "
        "every test file is in budget, every test has an expectation, "
        "no helper body is copied across test files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
