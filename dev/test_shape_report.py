"""Print the shape of `tests/`: the numbers the test-tree diet moves.

`dev/check_tests.py` says what a test file may not be; this says how big and
how repetitive the tree is, so a change that shrinks it can show the number
move. It judges nothing and exits 0 (1 only when a file cannot be parsed:
numbers off a half-read tree are worse than none).

    test functions          every `def test_*` / `async def test_*`, module level
                            or in a class, in every `.py` under `tests/`
    collected cases (est.)  those, after `@pytest.mark.parametrize`: the product
                            of the literal list/tuple lengths of the stacked
                            decorators (a class's apply to each of its methods).
                            A non-literal second argument counts as 2 and is
                            tallied in `estimated_parametrize_sites`. Not seen:
                            fixture `params=`, `ON_FAKE_AND_REAL_BD`, `pytestmark`.
    lines under tests/      physical lines of those `.py` files. (A plain
                            `find tests -type f | xargs cat | wc -l` also counts
                            the yaml/json fixtures and reads ~1% higher.)
    verbatim-repeat lines   take every `.py` line under `tests/`, strip it, and
                            drop the trivial ones: a comment, an `import`
                            line, and anything shorter than MIN_LINE_LEN (25)
                            characters (a syntax fragment, not a copied
                            statement: that floor already drops blank lines,
                            a lone bracket, `pass`, bare `return`, `else:` and
                            a docstring's quote line).
                            What is left is the non-trivial total; a distinct
                            line seen REPEAT_AT (5) or more times counts every
                            one of its occurrences as a verbatim repeat.
                            Docstring bodies and decorators are kept.
    duplicated helpers      module-level `def`/`async def` not named `test_*` (a
                            class's methods are not), grouped by
                            `helper_fingerprint`; a group defined in two or
                            more files is a duplicated helper, reported under
                            its most common name. "N names / M copies" is N
                            groups and the M definitions in them. The fingerprint
                            is strict (the body must be the same code once
                            docstring, annotations and keyword order are gone,
                            over the same module constant values), so it
                            undercounts what a person sees as "the same
                            `_git`"; `same-name helpers` is the looser count, a
                            name defined in two or more files.
    densest modules         for each `src/kraft/**/*.py` of >= MIN_MODULE_LINES
                            code lines (not blank, not comment, not docstring),
                            the test functions in its mirrored test files,
                            `tests/<pkg>/test_<mod>.py` and `test_<mod>_*.py`,
                            per 100 code lines. A `test_<a>_<b>.py` goes to
                            module `<a>_<b>` when that exists beside `<a>`, else
                            to `<a>`. Top TOP_MODULES.

Stdlib only; `dev/check_tests.py` is loaded by path for its `_test_files` and
`_parse`, so both tools read the same set of files the same way (and a parse
failure is a failure here too). `measure(root)` is the seam: a repo root in,
a plain dict out. `main` only prints it, as text or `--json`;
`--print-helper-ceiling` emits the `DUPLICATE_HELPER_CEILING` line
`dev/check_tests.py`'s rule (e) holds, computed by that checker's own
grouping over both testpaths (the "duplicated helpers" numbers above count
`tests/` only). `--root` points at another checkout.

`--diff BASE HEAD` is the other mode: what one pull request does to the tree,
as the markdown comment `.github/workflows/tests-nudge.yml` posts (the
"--diff" section below says which three signs make it speak; otherwise it
prints "nothing to nudge about"). It measures both revisions in throwaway
`git worktree`s of `--root` and always exits 0.

Run: `uv run python dev/test_shape_report.py [--json]`, or `just shape-report`.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CHECK_TESTS = Path(__file__).resolve().with_name("check_tests.py")

REPEAT_AT = 5
MIN_LINE_LEN = 25
MIN_MODULE_LINES = 150
TOP_MODULES = 10
TOP_HELPERS = 12

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_IMPORT = re.compile(r"(import |from \S+ import )")


def _checker(root: Path):
    """A private copy of `dev/check_tests.py` pointed at `root`, so its
    `_test_files` and `_parse` work on another checkout (and on a tmp tree)."""
    spec = importlib.util.spec_from_file_location("_dev_check_tests_for_shape", _CHECK_TESTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = root
    mod.TESTS = root / "tests"
    return mod


def _module_constants(module: ast.Module) -> dict[str, list[ast.expr]]:
    """Every name `module` binds with a top-level `Assign`/`AnnAssign`, to the
    value(s) it is bound to, in source order."""
    values: dict[str, list[ast.expr]] = defaultdict(list)
    for node in module.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store):
                    values[name.id].append(value)
    return dict(values)


def helper_fingerprint(fn: ast.FunctionDef, module: ast.Module | None = None) -> str:
    """The body of a module-level helper with everything that doesn't change
    what it does stripped: docstring, annotations, return annotation, and
    keyword-argument order in calls (the 17 `_git` copies differ only in
    where `check=True` sits).

    With `module` (the file `fn` is defined in), every name in the body
    that `module` assigns at top level appends `NAME = <value>`, sorted by
    name: nine `def _row(**f): return {**DEFAULTS, **f}` over nine different
    `DEFAULTS` are nine helpers, not one. An imported or unknown name adds
    nothing past the name already in the body."""
    fn = copy.deepcopy(fn)
    if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
        fn.body = fn.body[1:]
    for node in ast.walk(fn):
        if isinstance(node, ast.arg):
            node.annotation = None
        elif isinstance(node, ast.Call):
            node.keywords.sort(key=lambda k: k.arg or "")
    fn.returns = None
    body = ast.unparse(fn.body)
    if module is None:
        return body
    constants = _module_constants(module)
    named = dict.fromkeys(
        node.id
        for stmt in fn.body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Name) and node.id in constants
    )
    return "\n".join(
        [body, *(f"{name} = {ast.unparse(v)}" for name in sorted(named) for v in constants[name])]
    )


def helper_key(name: str, fingerprint: str) -> str:
    """`<name>#<first 8 hex of sha1(fingerprint)>`: a group's name, unique per body."""
    return f"{name}#{hashlib.sha1(fingerprint.encode()).hexdigest()[:8]}"


# -- test functions and collected cases -------------------------------------


def _parametrize(decorators: list[ast.expr]) -> tuple[int, int]:
    """`(cases, estimated_sites)` for one decorator list: the product of the
    literal argvalues lengths; a non-literal one counts 2 and is an estimate."""
    cases, estimated = 1, 0
    for dec in decorators:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "parametrize":
            continue
        values = (
            dec.args[1]
            if len(dec.args) > 1
            else next((k.value for k in dec.keywords if k.arg == "argvalues"), None)
        )
        if isinstance(values, (ast.List, ast.Tuple)) and not any(
            isinstance(e, ast.Starred) for e in values.elts
        ):
            cases *= max(len(values.elts), 1)
        else:
            cases *= 2
            estimated += 1
    return cases, estimated


def _tally_tests(body: list[ast.stmt], outer: int, tally: Counter) -> None:
    """Count `test_*` functions in `body` (recursing into classes only) into `tally`."""
    for node in body:
        if isinstance(node, _FUNCS) and node.name.startswith("test_"):
            cases, estimated = _parametrize(node.decorator_list)
            tally["functions"] += 1
            tally["cases"] += outer * cases
            tally["estimated"] += estimated
        elif isinstance(node, ast.ClassDef):
            cases, estimated = _parametrize(node.decorator_list)
            tally["estimated"] += estimated
            _tally_tests(node.body, outer * cases, tally)


# -- verbatim repeats -------------------------------------------------------


def _trivial(line: str) -> bool:
    return len(line) < MIN_LINE_LEN or line.startswith("#") or bool(_IMPORT.match(line))


# -- code lines of a source module ------------------------------------------


def _docstring_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, *_FUNCS)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                lines.update(range(first.lineno, first.end_lineno + 1))
    return lines


def _code_lines(text: str, tree: ast.Module) -> int:
    skip = _docstring_lines(tree)
    return sum(
        1
        for number, line in enumerate(text.splitlines(), 1)
        if line.strip() and not line.strip().startswith("#") and number not in skip
    )


def _owner(test_stem: str, module_stems: set[str]) -> str | None:
    """The source module a `test_<test_stem>.py` mirrors: the longest module
    stem that is the whole name or its `_`-delimited prefix."""
    matches = [s for s in module_stems if test_stem == s or test_stem.startswith(s + "_")]
    return max(matches, key=len) if matches else None


def _densest(root: Path, tests_per_file: dict[str, int], errors: list[str], ck) -> list[dict]:
    src = root / "src" / "kraft"
    owned: dict[tuple[str, str], int] = defaultdict(int)  # (reldir, module stem) -> tests
    stems_by_dir: dict[str, set[str]] = {}

    def stems(reldir: str) -> set[str]:
        if reldir not in stems_by_dir:
            here = src / reldir if reldir != "." else src
            stems_by_dir[reldir] = {
                p.stem for p in here.glob("*.py") if p.stem not in ("__init__", "__main__")
            }
        return stems_by_dir[reldir]

    for relpath, count in tests_per_file.items():
        path = Path(relpath)
        if not path.name.startswith("test_"):
            continue
        reldir = path.parent.relative_to("tests").as_posix()
        owner = _owner(path.stem[len("test_") :], stems(reldir))
        if owner is not None:
            owned[(reldir, owner)] += count

    rows = []
    for path in sorted(src.rglob("*.py")):
        if path.stem in ("__init__", "__main__"):
            continue
        tree, error = ck._parse(path)
        if error is not None:
            errors.append(error)
            continue
        code = _code_lines(path.read_text(), tree)
        if code < MIN_MODULE_LINES:
            continue
        reldir = path.parent.relative_to(src).as_posix()
        tests = owned.get((reldir, path.stem), 0)
        if tests:
            rows.append(
                {
                    "module": path.relative_to(src).as_posix(),
                    "tests": tests,
                    "code_lines": code,
                    "per_100": round(100 * tests / code, 1),
                }
            )
    rows.sort(key=lambda r: (-r["tests"] / r["code_lines"], r["module"]))
    return rows[:TOP_MODULES]


# -- the seam ----------------------------------------------------------------


def measure(root: Path = ROOT) -> dict:
    """The shape of `root/tests` (and `root/src/kraft`) as a plain dict."""
    root = Path(root)
    ck = _checker(root)
    tests_dir = root / "tests"
    errors: list[str] = []
    tally: Counter = Counter()
    lines = 0
    line_counts: Counter = Counter()
    tests_per_file: dict[str, int] = {}
    defs: dict[str, list[tuple[str, str]]] = defaultdict(list)  # fingerprint -> (relpath, name)

    for path in ck._test_files():
        if not path.is_relative_to(tests_dir):
            continue  # the plugin's testpath is not `tests/`
        relpath = path.relative_to(root).as_posix()
        tree, error = ck._parse(path)
        if error is not None:
            errors.append(error)
            continue
        text = path.read_text()
        file_lines = text.splitlines()
        lines += len(file_lines)
        for raw in file_lines:
            line = raw.strip()
            if not _trivial(line):
                line_counts[line] += 1
        before = tally["functions"]
        _tally_tests(tree.body, 1, tally)
        tests_per_file[relpath] = tally["functions"] - before
        for node in tree.body:
            if isinstance(node, _FUNCS) and not node.name.startswith("test_"):
                defs[helper_fingerprint(node, tree)].append((relpath, node.name))

    nontrivial = sum(line_counts.values())
    repeated = sum(n for n in line_counts.values() if n >= REPEAT_AT)

    groups = []
    for fingerprint, found in defs.items():
        files = sorted({f for f, _ in found})
        if len(files) < 2:
            continue
        names = Counter(n for _, n in found)
        name = min(names, key=lambda n: (-names[n], n))
        groups.append(
            {
                "name": name,
                "copies": len(found),
                "files": files,
                "key": helper_key(name, fingerprint),
            }
        )
    groups.sort(key=lambda g: (-g["copies"], g["name"], g["key"]))

    by_name: dict[str, list[str]] = defaultdict(list)
    for found in defs.values():
        for relpath, name in found:
            by_name[name].append(relpath)
    same_name = sorted(
        ({"name": n, "copies": len(f)} for n, f in by_name.items() if len(set(f)) >= 2),
        key=lambda g: (-g["copies"], g["name"]),
    )

    densest = (
        _densest(root, tests_per_file, errors, ck) if (root / "src" / "kraft").is_dir() else []
    )
    return {
        "test_functions": tally["functions"],
        "collected_cases": tally["cases"],
        "estimated_parametrize_sites": tally["estimated"],
        "lines": lines,
        "nontrivial_lines": nontrivial,
        "verbatim_repeat_lines": repeated,
        "verbatim_repeat_pct": round(100 * repeated / nontrivial, 1) if nontrivial else 0.0,
        "duplicated_helpers": {
            "names": len(groups),
            "copies": sum(g["copies"] for g in groups),
            "groups": groups,
        },
        "same_name_helpers": {
            "names": len(same_name),
            "copies": sum(g["copies"] for g in same_name),
            "groups": same_name,
        },
        "densest_modules": densest,
        "parse_errors": errors,
    }


# -- output ------------------------------------------------------------------


def _wrapped(items: list[str], per_line: int) -> list[str]:
    return ["  " + "   ".join(items[i : i + per_line]) for i in range(0, len(items), per_line)]


def format_report(shape: dict) -> str:
    dup, same = shape["duplicated_helpers"], shape["same_name_helpers"]
    pct = shape["verbatim_repeat_pct"]
    out = [
        f"{'test functions':<24}{shape['test_functions']}",
        f"{'collected cases (est.)':<24}{shape['collected_cases']}",
        f"{'lines under tests/':<24}{shape['lines']}",
        f"{'verbatim-repeat lines':<24}{shape['verbatim_repeat_lines']} ({pct:.0f}%)",
        f"{'duplicated helpers':<24}{dup['names']} names / {dup['copies']} copies",
        *_wrapped([f"{g['name']} {g['copies']}" for g in dup["groups"][:TOP_HELPERS]], 6),
        f"{'same-name helpers':<24}{same['names']} names / {same['copies']} copies",
        *_wrapped([f"{g['name']} {g['copies']}" for g in same["groups"][:TOP_HELPERS]], 6),
        f"densest modules (tests per 100 code lines, modules >= {MIN_MODULE_LINES} lines)",
        *_wrapped([f"{m['per_100']:.1f}  {m['module']}" for m in shape["densest_modules"]], 3),
    ]
    return "\n".join(out)


def helper_ceiling(root: Path = ROOT) -> tuple[dict[str, int], list[str]]:
    """Rule (e)'s two numbers for `root`, and any parse failures: what
    `dev/check_tests.py` itself computes, over the files its `main()` walks
    (`plugins/kraft-lite/tests/` included), so a ceiling seeded from here is
    the one the check holds the tree to."""
    ck = _checker(root)
    trees, support, errors = ck.trees_and_support()
    return ck.duplicate_helper_totals(ck.duplicate_helper_counts(trees, support)), errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    parser.add_argument(
        "--print-helper-ceiling",
        action="store_true",
        help="print rule (e)'s DUPLICATE_HELPER_CEILING for dev/check_tests.py: duplicated "
        "helper groups and copies over both testpaths, as that checker counts them",
    )
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="repo root to measure (default: this checkout)"
    )
    parser.add_argument(
        "--diff",
        nargs=2,
        metavar=("BASE", "HEAD"),
        help="print what HEAD does to the tree relative to BASE, as a PR comment (always exits 0)",
    )
    args = parser.parse_args(argv)

    if args.diff:
        return run_diff(*args.diff, root=args.root)
    if args.print_helper_ceiling:
        totals, errors = helper_ceiling(args.root)
        print("\n".join(errors) if errors else f"DUPLICATE_HELPER_CEILING = {json.dumps(totals)}")
        return 1 if errors else 0
    shape = measure(args.root)
    if shape["parse_errors"]:
        print("\n".join(shape["parse_errors"]))
        return 1
    if args.json:
        print(json.dumps(shape, indent=2))
    else:
        print(format_report(shape))
    return 0


# -- `--diff BASE HEAD`: what one pull request does to the tree ---------------
#
# `tests-nudge.yml` posts this as a PR comment, and only when a PR shows one of
# three signs of the shape `docs/testing.md` ("One behaviour, one test") argues
# against; the rest of the time it says nothing, because a nudge that fires on
# every PR trains everyone to ignore it (the reason `docs-nudge.yml` gives too):
#
#   - three or more test functions added, none of them parametrized;
#   - a module-level helper added whose fingerprint already exists in another
#     file (a copy outside `tests/support`, where the shared ones live);
#   - the added lines' verbatim-repeat share is above the tree's own.
#
# "Already present elsewhere" is judged against the BASE tree, never HEAD: in
# HEAD a PR's own lines would count as each other's repeats.

QUIET = "nothing to nudge about"
NUDGE_MIN_FUNCTIONS = 3
# A share of a handful of lines says nothing (one added `assert response.status_code == 200`
# is "100% repeat"), so the share only counts from this many added non-trivial lines.
NUDGE_MIN_ADDED_LINES = 20
SUPPORT = "tests/support/"
MAX_HELPER_ROWS = 3

_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DEF = re.compile(r"\s*(?:async\s+)?def\s+(\w+)")


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    return done.stdout


@contextmanager
def _checkout(root: Path, rev: str) -> Iterator[Path]:
    """`rev` checked out detached in a temp dir, removed again however the body ends."""
    with tempfile.TemporaryDirectory(prefix="kraft-shape-") as tmp:
        tree = Path(tmp) / "tree"
        _git(root, "worktree", "add", "--detach", str(tree), rev)
        try:
            yield tree
        finally:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "remove", "--force", str(tree)],
                capture_output=True,
            )
            subprocess.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True)


def _scan(root: Path) -> tuple[Counter, dict[str, list[tuple[str, str]]]]:
    """`(line_counts, helpers)` of `root/tests`, read the way `measure` reads it:
    every non-trivial stripped line with its count, and the module-level
    helpers as fingerprint -> [(relpath, name)]."""
    ck = _checker(root)
    line_counts: Counter = Counter()
    helpers: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for path in ck._test_files():
        if not path.is_relative_to(root / "tests"):
            continue
        tree, error = ck._parse(path)
        if error is not None:
            continue  # `measure` reports it
        for raw in path.read_text().splitlines():
            if not _trivial(raw.strip()):
                line_counts[raw.strip()] += 1
        relpath = path.relative_to(root).as_posix()
        for node in tree.body:
            if isinstance(node, _FUNCS) and not node.name.startswith("test_"):
                helpers[helper_fingerprint(node, tree)].append((relpath, node.name))
    return line_counts, helpers


def _added_by_file(root: Path, base: str, head: str) -> dict[str, dict]:
    """`git diff BASE HEAD -- tests/` for `.py` files: per new path, the added
    lines `[(line number in HEAD, stripped text)]` and the names of the `def`s
    that lines were removed from. Renames are followed, so a moved file adds
    only what it changed."""
    diff = _git(
        root,
        "-c",
        "core.quotepath=off",
        "diff",
        "-M",
        "--unified=0",
        "--no-color",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        base,
        head,
        "--",
        "tests/",
    )
    files: dict[str, dict] = {}
    current: dict | None = None
    old_left = new_left = new_line = 0
    for line in diff.splitlines():
        if old_left or new_left:  # inside a hunk: its header says how many lines it has
            if line.startswith("-"):
                old_left -= 1
                match = _DEF.match(line[1:])
                if match and current is not None:
                    current["removed_defs"].add(match.group(1))
            elif line.startswith("+"):
                new_left -= 1
                if current is not None:
                    current["added"].append((new_line, line[1:].strip()))
                new_line += 1
            continue
        if line.startswith("+++ "):
            path = line[4:]
            current = None
            if path.startswith("b/") and path.endswith(".py"):
                current = files.setdefault(path[2:], {"added": [], "removed_defs": set()})
            continue
        match = _HUNK.match(line)
        if match:
            old_left = int(match.group(1) if match.group(1) is not None else 1)
            new_line = int(match.group(2))
            new_left = int(match.group(3) if match.group(3) is not None else 1)
    return files


def _has_parametrize(decorators: list[ast.expr]) -> bool:
    for dec in decorators:
        func = dec.func if isinstance(dec, ast.Call) else dec
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "parametrize":
            return True
    return False


def _tests_with_decorators(body: list[ast.stmt], outer: list[ast.expr]) -> Iterator[tuple]:
    """`(test function node, decorators of it and of its enclosing classes)`."""
    for node in body:
        if isinstance(node, _FUNCS) and node.name.startswith("test_"):
            yield node, [*outer, *node.decorator_list]
        elif isinstance(node, ast.ClassDef):
            yield from _tests_with_decorators(node.body, [*outer, *node.decorator_list])


def _added_tests(head_tree: Path, changes: dict[str, dict]) -> tuple[int, int]:
    """`(added test functions, how many of them carry a parametrize)`: a test
    function whose `def` line the diff added, unless the same diff removed a
    `def` of that name from the file (an edited signature, not a new test)."""
    ck = _checker(head_tree)
    added = parametrized = 0
    for relpath, change in sorted(changes.items()):
        path = head_tree / relpath
        if not path.is_file() or not change["added"]:
            continue
        tree, error = ck._parse(path)
        if error is not None:
            continue
        added_at = {number for number, _ in change["added"]}
        for node, decorators in _tests_with_decorators(tree.body, []):
            if node.lineno in added_at and node.name not in change["removed_defs"]:
                added += 1
                parametrized += _has_parametrize(decorators)
    return added, parametrized


def _shared_name(copies: list[tuple[str, str]]) -> str | None:
    """`support.harness.commit_all` for a copy that lives under `tests/support`."""
    for relpath, name in sorted(copies):
        if relpath.startswith(SUPPORT):
            module = relpath[len("tests/") : -len(".py")].replace("/", ".")
            return f"{module}.{name}"
    return None


def _added_helpers(before: dict, after: dict) -> list[dict]:
    """The helper groups a PR grew by a file that is not under `tests/support`:
    HEAD has more files with that fingerprint than BASE, and at least two."""
    rows = []
    for fingerprint, copies in after.items():
        files_now = {f for f, _ in copies}
        files_before = {f for f, _ in before.get(fingerprint, [])}
        new_files = {f for f in files_now - files_before if not f.startswith(SUPPORT)}
        if not new_files or len(files_now) < 2 or len(files_now) <= len(files_before):
            continue
        added = [(f, n) for f, n in copies if f in new_files]
        names = Counter(n for _, n in added)
        rows.append(
            {
                "name": min(names, key=lambda n: (-names[n], n)),
                "added": len(added),
                "exist": len(copies),
                "shared": _shared_name(copies),
            }
        )
    rows.sort(key=lambda r: (-r["added"], r["name"]))
    return rows


def diff(base: str, head: str, root: Path = ROOT) -> dict:
    """The delta HEAD makes to `root`'s test tree relative to BASE, as a plain
    dict (`format_nudge` words it, `signs` says whether it is worth saying)."""
    root = Path(root)
    changes = _added_by_file(root, base, head)
    with _checkout(root, base) as base_tree, _checkout(root, head) as head_tree:
        before, after = measure(base_tree), measure(head_tree)
        base_counts, base_helpers = _scan(base_tree)
        _, head_helpers = _scan(head_tree)
        added_functions, added_parametrized = _added_tests(head_tree, changes)
    added_lines = [text for c in changes.values() for _, text in c["added"] if not _trivial(text)]
    repeated = sum(1 for text in added_lines if base_counts[text] >= REPEAT_AT)
    base_nontrivial = before["nontrivial_lines"]
    return {
        "test_functions": after["test_functions"] - before["test_functions"],
        "collected_cases": after["collected_cases"] - before["collected_cases"],
        "lines": after["lines"] - before["lines"],
        "added_functions": added_functions,
        "added_parametrized": added_parametrized,
        "added_nontrivial_lines": len(added_lines),
        "added_repeated_lines": repeated,
        "base_repeat_pct": (
            100 * before["verbatim_repeat_lines"] / base_nontrivial if base_nontrivial else 0.0
        ),
        "helpers": _added_helpers(base_helpers, head_helpers),
        "parse_errors": [*before["parse_errors"], *after["parse_errors"]],
    }


def signs(delta: dict) -> list[str]:
    """Which of the three signs `delta` shows, by name; empty means stay quiet."""
    found = []
    if delta["added_functions"] >= NUDGE_MIN_FUNCTIONS and delta["added_parametrized"] == 0:
        found.append("functions")
    if delta["helpers"]:
        found.append("helper")
    added = delta["added_nontrivial_lines"]
    if (
        added >= NUDGE_MIN_ADDED_LINES
        and 100 * delta["added_repeated_lines"] / added > delta["base_repeat_pct"]
    ):
        found.append("repeats")
    return found


def format_nudge(delta: dict) -> str:
    """The PR comment (without its marker; the workflow adds that)."""
    added, parametrized = delta["added_functions"], delta["added_parametrized"]
    net = f"{delta['test_functions']:+d}"
    which = "of them" if added == delta["test_functions"] else f"of the {added} added"
    rows = [
        f"  {'test functions':<19}{net:<6}({parametrized} {which} parametrized)",
        f"  {'collected cases':<19}{delta['collected_cases']:+d}",
        f"  {'lines in tests/':<19}{delta['lines']:+d}",
        f"  {'verbatim-repeat':<19}{delta['added_repeated_lines']:+d} lines already present "
        f"{REPEAT_AT}+ times elsewhere in tests/",
    ]
    label = "helpers"
    for row in delta["helpers"][:MAX_HELPER_ROWS]:
        copies = "copy" if row["added"] == 1 else "copies"
        shared = f"; `{row['shared']}` is the shared one" if row["shared"] else ""
        rows.append(
            f"  {label:<19}{row['added']:+d} {copies} of `{row['name']}` "
            f"({row['exist']} exist{shared})"
        )
        label = ""
    if len(delta["helpers"]) > MAX_HELPER_ROWS:
        rows.append(f"  {'':<19}(and {len(delta['helpers']) - MAX_HELPER_ROWS} more)")
    return "\n".join(
        [
            "This PR changes the test tree:",
            "```",
            *rows,
            "```",
            "If the new tests are rows of one behavior, fold them into a table",
            '(docs/testing.md, "One behaviour, one test"). If they are new behaviors, ignore this.',
        ]
    )


def run_diff(base: str, head: str, root: Path = ROOT) -> int:
    """Print the nudge, or `QUIET`; exit 0 always. A revision that cannot be
    measured is said on stderr and is quiet on stdout: this reminds, it never
    fails a pull request."""
    try:
        delta = diff(base, head, root)
    except (subprocess.CalledProcessError, OSError) as exc:
        detail = getattr(exc, "stderr", None) or exc
        print(f"tests nudge skipped: {detail}".rstrip(), file=sys.stderr)
        print(QUIET)
        return 0
    if delta["parse_errors"]:
        print("tests nudge skipped:\n" + "\n".join(delta["parse_errors"]), file=sys.stderr)
        print(QUIET)
    elif signs(delta):
        print(format_nudge(delta))
    else:
        print(QUIET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
