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
`--print-helper-allowlist` emits `{"<name>#<sha1[:8]>": copies}` for every
duplicated helper, the key shape a checker allowlist uses. `--root` points at
another checkout.

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
from collections import Counter, defaultdict
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
    """`<name>#<first 8 hex of sha1(fingerprint)>`: how an allowlist names a group."""
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


def format_helper_allowlist(shape: dict) -> str:
    rows = sorted(shape["duplicated_helpers"]["groups"], key=lambda g: g["key"])
    return "{\n" + "".join(f'    "{g["key"]}": {g["copies"]},\n' for g in rows) + "}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    parser.add_argument(
        "--print-helper-allowlist",
        action="store_true",
        help='print {"<name>#<sha1[:8]>": copies} for every duplicated helper',
    )
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="repo root to measure (default: this checkout)"
    )
    args = parser.parse_args(argv)

    shape = measure(args.root)
    if shape["parse_errors"]:
        print("\n".join(shape["parse_errors"]))
        return 1
    if args.json:
        print(json.dumps(shape, indent=2))
    elif args.print_helper_allowlist:
        print(format_helper_allowlist(shape))
    else:
        print(format_report(shape))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
