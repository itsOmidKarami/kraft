"""Which tests are the sole killer of some mutant of one source module.

mutmut and friends record that a mutant died, not every test that would have
killed it, so they cannot answer "does this test pin anything another test does
not". This loop can: it generates mutants of one module with a small AST
walker, and for each mutant runs every collected test of the module's mirrored
test file *on its own*, recording pass/fail per (mutant, test). A test that is
never the only killer of any mutant pins nothing another test does not; it is a
deletion candidate whose declaration is "nothing, proven by mutation"
(`docs/testing.md`).

**This script calls pytest directly, on purpose.** CLAUDE.md says never to call
`pytest` / `uv run pytest` by hand and to go through `just test`, because a raw
run skips testmon and burns the whole suite. Neither reason holds here: each
run names exactly one test node, so nothing like the full suite runs, and
testmon's change tracking is meaningless under a mutation (it would deselect
on the mutated source, or record it). Hundreds of tiny runs is the point. So
the subprocesses below are `<venv python> -m pytest -p no:testmon ... <nodeid>`,
one test per process, and nothing else in the repo should copy the pattern.

Mutation kinds, one mutant per site:

    compare      each comparison operator flipped to its negation
                 (`==`/`!=`, `<`/`>=`, `<=`/`>`, `is`/`is not`, `in`/`not in`)
    negate_if    each `if` test (statement, conditional expression, or a
                 comprehension's `if`) wrapped in `not (...)`
    return_none  each `return <value>` (value not already `None`) made `return None`
    bool         each `True`/`False` constant swapped
    string       each string constant (not a docstring, not an f-string part)
                 replaced by a sentinel; only added when the four kinds above
                 give fewer than MIN_MUTANTS sites, or with `--strings`

Each mutant is spliced into the real source at its node's byte offsets, so the
rest of the file (comments, docstrings, line numbers) stays exactly as written.

Applying a mutant writes it over the real file. The original bytes are saved
first (also to `<scratch>/<name>.orig`, in case the process is killed outright),
written back after every mutant and in a `finally` on any exception or
KeyboardInterrupt, and every restore is checked by a byte comparison: a
mismatch stops the run. Each pytest run gets its own `TMPDIR` and `--basetemp`
under the scratch dir, `PYTHONDONTWRITEBYTECODE=1` (so no stale `.pyc` of one
mutant can be imported for the next, and concurrent runs never write the same
cache file), no cache provider, no testmon, `-o addopts=`, and no
`COVERAGE_*`/`COV_CORE_*`/`PYTEST_ADDOPTS` from the caller's environment.

Outcomes: exit 0 is `pass` (`skip` when the run only skipped), 1 is `fail`, 2-5
(collection error, internal error, usage error, nothing collected) is `error`,
and a run past `--timeout` (pytest-timeout's own ceiling, plus a margin for the
subprocess) is `timeout`. fail, error and timeout kill; pass and skip do not. A
mutant every test reports as `error` broke the module's import or collection
and is counted as killed by every test.

Before any mutant, every test runs once against the untouched file; a test that
fails there stops the run (a grid over a red baseline means nothing), and one
that skips is reported, since it can never kill.

Tests of one mutant run concurrently (`--jobs`, default the CPU count): they all
read the same mutated file and write only under their own scratch dirs, so one
checkout serves them all. Mutants run one after another. Because the headline
rests on the sole kills, and a timing-sensitive test can fail from load alone,
every cell that makes a test a sole killer is run once more by itself after the
grid; one that passes then is marked `flaky` and stops counting as a kill.
`--compare` takes an earlier run's `--json` and lists every cell whose kill
differs, the other half of that check.

Run:
    uv run python dev/mutation_pass.py src/kraft/notify.py tests/test_notify.py \\
        [--json OUT] [--markdown OUT] [--compare EARLIER.json] [--jobs N]
        [--timeout S] [--limit N] [--strings | --no-strings] [--list]

`--list` prints the mutants without running anything; `--limit` is a smoke run.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Below this many sites from the four core kinds, string constants are mutated too.
MIN_MUTANTS = 60
STRING_SENTINEL = "__mutant__"

KILLING = frozenset({"fail", "error", "timeout"})

_FLIP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.GtE: ast.Lt,
    ast.LtE: ast.Gt,
    ast.Gt: ast.LtE,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}


@dataclass(frozen=True)
class Mutant:
    id: str
    kind: str
    line: int
    col: int
    before: str
    after: str
    source: str

    def describe(self) -> str:
        return f"{self.kind} @ line {self.line}: `{self.before}` -> `{self.after}`"


# ---- generation ----


class _Source:
    """The module's text as bytes per line: AST columns are UTF-8 byte offsets."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.data = text.encode()
        self.starts = [0]
        for line in self.data.splitlines(keepends=True):
            self.starts.append(self.starts[-1] + len(line))

    def span(self, node: ast.AST) -> tuple[int, int]:
        start = self.starts[node.lineno - 1] + node.col_offset
        end = self.starts[node.end_lineno - 1] + node.end_col_offset
        return start, end

    def segment(self, node: ast.AST) -> str:
        start, end = self.span(node)
        return self.data[start:end].decode()

    def replace(self, node: ast.AST, new: str) -> str:
        start, end = self.span(node)
        return (self.data[:start] + new.encode() + self.data[end:]).decode()


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _fstring_part_ids(tree: ast.AST) -> set[int]:
    """Constants inside an f-string: their positions cover the literal text, not
    a standalone expression, so splicing a repr in would break the f-string."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant):
                    ids.add(id(sub))
    return ids


def _sites(tree: ast.AST, src: _Source, *, strings: bool) -> Iterable[tuple[str, ast.AST, str]]:
    """(kind, node to replace, replacement text) for every mutation site."""
    docstrings = _docstring_ids(tree)
    fstring_parts = _fstring_part_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                flipped = _FLIP.get(type(op))
                if flipped is None:
                    continue
                ops = list(node.ops)
                ops[i] = flipped()
                mutated = ast.Compare(left=node.left, ops=ops, comparators=node.comparators)
                yield "compare", node, ast.unparse(mutated)
        if isinstance(node, ast.If | ast.IfExp):
            yield "negate_if", node.test, f"not ({src.segment(node.test)})"
        if isinstance(node, ast.comprehension):
            for cond in node.ifs:
                yield "negate_if", cond, f"not ({src.segment(cond)})"
        if isinstance(node, ast.Return) and node.value is not None:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                yield "return_none", node.value, "None"
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            yield "bool", node, repr(not node.value)
        if (
            strings
            and isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and id(node) not in fstring_parts
        ):
            new = STRING_SENTINEL if node.value != STRING_SENTINEL else ""
            yield "string", node, repr(new)


def generate_mutants(source: str, *, strings: bool | None = None) -> list[Mutant]:
    """Every mutant of `source`, one per site, ordered by position.

    `strings=None` adds the `string` kind only when the four core kinds give
    fewer than MIN_MUTANTS; True always adds it, False never does. A splice
    that does not compile, or that changes nothing, is dropped."""
    if strings is None:
        core = generate_mutants(source, strings=False)
        if len(core) >= MIN_MUTANTS:
            return core
        strings = True
    tree = ast.parse(source)
    src = _Source(source)
    original_dump = ast.dump(tree)
    found: list[tuple[int, int, str, str, str, str]] = []
    for kind, node, new in _sites(tree, src, strings=strings):
        mutated = src.replace(node, new)
        try:
            changed = ast.dump(ast.parse(mutated)) != original_dump
            compile(mutated, "<mutant>", "exec")
        except SyntaxError:
            continue
        if changed:
            found.append((node.lineno, node.col_offset, kind, src.segment(node), new, mutated))
    found.sort(key=lambda f: (f[0], f[1], f[2]))
    return [
        Mutant(id=f"m{i:03d}", kind=kind, line=line, col=col, before=before, after=after, source=s)
        for i, (line, col, kind, before, after, s) in enumerate(found, start=1)
    ]


# ---- running ----


def killed(outcome: str) -> bool:
    return outcome in KILLING


def _child_env(tmp: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("COVERAGE_", "COV_CORE_")) and k != "PYTEST_ADDOPTS"
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = str(tmp)
    return env


class PytestRunner:
    """Runs one test node in its own pytest process and classifies the result.

    The one place in this repo that launches pytest without `just test`; the
    module docstring says why."""

    def __init__(self, *, python: str, cwd: Path, scratch: Path, timeout: float = 30.0) -> None:
        self.python = python
        self.cwd = Path(cwd)
        self.scratch = Path(scratch)
        self.timeout = timeout
        self.runs = 0
        self._lock = threading.Lock()

    def flags(self, basetemp: Path) -> list[str]:
        return [
            "-q",
            "--no-header",
            "-x",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:testmon",
            "-o",
            "addopts=",
            "-o",
            f"timeout={int(self.timeout)}",
            f"--basetemp={basetemp}",
        ]

    def collect(self, test_file: str) -> list[str]:
        tmp = self._tmp()
        try:
            flags = self.flags(tmp / "bt")
            cmd = [self.python, "-m", "pytest", "--collect-only", *flags, test_file]
            res = subprocess.run(
                cmd, cwd=self.cwd, env=_child_env(tmp), capture_output=True, text=True, check=False
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        ids = [line.strip() for line in res.stdout.splitlines() if "::" in line]
        if res.returncode != 0 or not ids:
            raise RuntimeError(f"collection of {test_file} failed:\n{res.stdout}\n{res.stderr}")
        return ids

    def _tmp(self) -> Path:
        tmp = self.scratch / f"run-{uuid.uuid4().hex[:12]}"
        tmp.mkdir(parents=True)
        return tmp

    def __call__(self, nodeid: str) -> str:
        tmp = self._tmp()
        cmd = [self.python, "-m", "pytest", *self.flags(tmp / "bt"), nodeid]
        try:
            res = subprocess.run(
                cmd,
                cwd=self.cwd,
                env=_child_env(tmp),
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout + 30,
            )
        except subprocess.TimeoutExpired:
            return "timeout"
        finally:
            with self._lock:
                self.runs += 1
            shutil.rmtree(tmp, ignore_errors=True)
        if res.returncode == 0:
            only_skipped = re.search(r"\b\d+ skipped\b", res.stdout) and not re.search(
                r"\b\d+ passed\b", res.stdout
            )
            return "skip" if only_skipped else "pass"
        if res.returncode == 1:
            return "fail"
        return "error"


def _write_checked(target: Path, data: bytes) -> None:
    target.write_bytes(data)
    if target.read_bytes() != data:
        raise RuntimeError(f"{target}: write did not land byte-for-byte")


@contextmanager
def _applied(target: Path, mutant: Mutant, original: bytes) -> Iterator[None]:
    """`mutant` over `target` for the block; `original` back after it, however
    the block ends, each write checked byte for byte."""
    _write_checked(target, mutant.source.encode())
    try:
        yield
    finally:
        _write_checked(target, original)


def run_grid(
    target: Path,
    mutants: list[Mutant],
    tests: list[str],
    run_test: Callable[[str], str],
    *,
    jobs: int = 1,
    on_mutant: Callable[[Mutant, dict[str, str]], None] | None = None,
) -> dict[str, dict[str, str]]:
    """Apply each mutant over `target`, run every test on its own, restore.

    Returns `{mutant id: {test id: outcome}}`. The original bytes go back after
    every mutant and on any exception (KeyboardInterrupt included), and each
    restore is verified by a byte comparison."""
    target = Path(target)
    original = target.read_bytes()
    grid: dict[str, dict[str, str]] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, jobs)) if jobs > 1 else None
    try:
        for mutant in mutants:
            with _applied(target, mutant, original):
                if pool is None:
                    outcomes = [run_test(t) for t in tests]
                else:
                    outcomes = list(pool.map(run_test, tests))
            grid[mutant.id] = dict(zip(tests, outcomes, strict=True))
            if on_mutant is not None:
                on_mutant(mutant, grid[mutant.id])
    finally:
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        if target.read_bytes() != original:
            _write_checked(target, original)
    return grid


def confirm_sole_kills(
    target: Path,
    mutants: list[Mutant],
    grid: dict[str, dict[str, str]],
    run_test: Callable[[str], str],
) -> dict[str, str]:
    """Run every sole-kill cell again, one at a time, with its mutant applied.

    The headline rests on those cells, and the grid ran its tests concurrently,
    so a timing-sensitive test could have failed from load rather than from the
    mutation. A cell that does not kill again becomes `flaky` in `grid` (which
    does not count as a kill). Returns `{mutant id: test id}` for those."""
    target = Path(target)
    original = target.read_bytes()
    by_id = {m.id: m for m in mutants}
    flaky: dict[str, str] = {}
    try:
        for mid, row in grid.items():
            killers = [t for t, outcome in row.items() if killed(outcome)]
            if len(killers) != 1:
                continue
            with _applied(target, by_id[mid], original):
                again = run_test(killers[0])
            if not killed(again):
                row[killers[0]] = "flaky"
                flaky[mid] = killers[0]
    finally:
        if target.read_bytes() != original:
            _write_checked(target, original)
    return flaky


# ---- the arithmetic ----


def function_of(nodeid: str) -> str:
    return nodeid.split("[", 1)[0]


def _sole(grid: dict[str, dict[str, str]], key: Callable[[str], str]) -> dict[str, list[str]]:
    sole: dict[str, list[str]] = {}
    for mid, row in grid.items():
        killers = {key(t) for t, outcome in row.items() if killed(outcome)}
        if len(killers) == 1:
            sole.setdefault(killers.pop(), []).append(mid)
    return {k: sorted(v) for k, v in sorted(sole.items())}


def summarize(grid: dict[str, dict[str, str]], tests: list[str]) -> dict:
    functions = list(dict.fromkeys(function_of(t) for t in tests))
    sole = _sole(grid, lambda t: t)
    sole_fn = _sole(grid, function_of)
    survived = sorted(m for m, row in grid.items() if not any(killed(o) for o in row.values()))
    import_failures = sorted(
        m for m, row in grid.items() if row and all(o == "error" for o in row.values())
    )
    kills = {t: sum(killed(grid[m][t]) for m in grid) for t in tests}
    return {
        "tests": len(tests),
        "functions": len(functions),
        "mutants": len(grid),
        "sole_killers": sole,
        "sole_killers_by_function": sole_fn,
        "never_sole": [t for t in tests if t not in sole],
        "never_sole_by_function": [f for f in functions if f not in sole_fn],
        "survived": survived,
        "import_failures": import_failures,
        "kills": kills,
        "headline": f"{len(sole)} of {len(tests)} tests are the sole killer of at least one mutant",
        "headline_by_function": (
            f"{len(sole_fn)} of {len(functions)} test functions are the sole killer "
            "of at least one mutant"
        ),
    }


# ---- the report ----


def _short(nodeid: str) -> str:
    return nodeid.split("::", 1)[-1]


def _code(text: str, limit: int = 70) -> str:
    one = " ".join(text.split())
    if len(one) > limit:
        one = one[: limit - 3] + "..."
    return "`" + one.replace("`", "'") + "`"


def render_markdown(result: dict) -> str:
    s = result["summary"]
    mutants = {m["id"]: m for m in result["mutants"]}
    target = result["target"]
    kinds: dict[str, int] = {}
    for m in result["mutants"]:
        kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
    lines = [
        f"# Mutation pass: `{target}` against `{result['test_file']}`",
        "",
        f"**{s['headline']}.**",
        "",
        f"Function level: {s['headline_by_function']}.",
        "",
        "## Run",
        "",
        f"- mutants: {s['mutants']} ("
        + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
        + ")",
        f"- tests (collected node ids): {s['tests']}; test functions: {s['functions']}",
        f"- pytest runs: {result['runs']} (baseline {len(result['baseline'])}, "
        f"grid {s['mutants']} x {s['tests']})",
        f"- wall time: {result['wall_seconds'] / 60:.1f} min, {result['jobs']} concurrent runs",
        f"- survived (no test kills it): {len(s['survived'])}",
        f"- killed by every test because the module would not import or collect: "
        f"{len(s['import_failures'])}",
        f"- killed by exactly one test: {sum(len(v) for v in s['sole_killers'].values())}",
        f"- per-test ceiling: {result['timeout']}s",
        f"- sole kills that did not fail again when re-run alone (dropped as flaky): "
        f"{len(result['flaky'])}"
        + "".join(f"; {m} `{_short(t)}`" for m, t in sorted(result["flaky"].items())),
    ]
    if "disagreements" in result:
        cells = result["disagreements"]
        lines.append(
            f"- cells whose kill differs from the run in `{result['compared_with']}`: {len(cells)}"
            + "".join(
                f"; {c['mutant']} `{_short(c['test'])}` {c['then']}->{c['now']}" for c in cells
            )
        )
    skipped = [t for t, o in result["baseline"].items() if o == "skip"]
    if skipped:
        lines += [
            "- skipped on the untouched module in this environment, so never a killer: "
            + ", ".join(f"`{_short(t)}`" for t in skipped)
        ]
    lines += [
        "",
        "## Sole killers",
        "",
        "| test | mutants it alone kills | mutants it kills |",
        "|---|---|---|",
    ]
    for t, mids in s["sole_killers"].items():
        sites = ", ".join(f"{mutants[m]['kind']}:{mutants[m]['line']}" for m in mids)
        lines.append(f"| `{_short(t)}` | {len(mids)} ({sites}) | {s['kills'][t]} |")
    lines += [
        "",
        "## Never a sole killer (deletion candidates)",
        "",
        "A test here pins nothing another test in the file does not, as far as these "
        "mutants reach. `kills` is how many mutants it kills at all: 0 means it never "
        f"touches `{target}`'s behaviour in a way these mutants see.",
        "",
        "| test | kills |",
        "|---|---|",
    ]
    for t in s["never_sole"]:
        lines.append(f"| `{_short(t)}` | {s['kills'][t]} |")
    if s["never_sole_by_function"] != s["never_sole"]:
        by_fn = ", ".join(f"`{_short(f)}`" for f in s["never_sole_by_function"])
        lines += ["", f"Function level: {by_fn}"]
    lines += [
        "",
        "## Survived mutants",
        "",
        "| mutant | site | kind | before | after |",
        "|---|---|---|---|---|",
    ]
    for mid in s["survived"]:
        m = mutants[mid]
        lines.append(
            f"| {mid} | `{target}:{m['line']}` | {m['kind']} | {_code(m['before'])} | "
            f"{_code(m['after'])} |"
        )
    if s["import_failures"]:
        lines += ["", "## Mutants that broke import or collection", ""]
        for mid in s["import_failures"]:
            m = mutants[mid]
            change = f"{_code(m['before'])} -> {_code(m['after'])}"
            lines.append(f"- {mid} `{target}:{m['line']}` {m['kind']}: {change}")
    return "\n".join(lines) + "\n"


# ---- main ----


def _venv_python() -> str:
    """The project environment's interpreter, resolved once through `uv run`,
    so the hundreds of runs below do not each pay `uv run`'s sync check."""
    res = subprocess.run(
        ["uv", "run", "python", "-c", "import sys; print(sys.executable)"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", help="the source module to mutate, e.g. src/kraft/notify.py")
    ap.add_argument("test_file", help="its mirrored tests, e.g. tests/test_notify.py")
    ap.add_argument("--json", type=Path, help="write the full grid and summary here")
    ap.add_argument("--markdown", type=Path, help="write the markdown report here")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--timeout", type=float, default=30.0, help="per-test ceiling, seconds")
    ap.add_argument("--limit", type=int, help="only the first N mutants (a smoke run)")
    strings = ap.add_mutually_exclusive_group()
    strings.add_argument("--strings", dest="strings", action="store_true", default=None)
    strings.add_argument("--no-strings", dest="strings", action="store_false")
    ap.add_argument("--list", action="store_true", help="print the mutants and exit")
    ap.add_argument(
        "--compare", type=Path, help="a previous run's --json: list the cells whose kill differs"
    )
    args = ap.parse_args(argv)

    target = (ROOT / args.target).resolve()
    original = target.read_bytes()
    mutants = generate_mutants(original.decode(), strings=args.strings)
    if args.limit:
        mutants = mutants[: args.limit]
    if args.list:
        for m in mutants:
            print(f"{m.id} {m.describe()}")
        print(f"{len(mutants)} mutants", file=sys.stderr)
        return 0

    scratch = Path(tempfile.mkdtemp(prefix="kraft-mutation-"))
    (scratch / f"{target.name}.orig").write_bytes(original)
    print(f"original saved to {scratch / (target.name + '.orig')}", file=sys.stderr)
    runner = PytestRunner(python=_venv_python(), cwd=ROOT, scratch=scratch, timeout=args.timeout)
    started = time.monotonic()
    tests = runner.collect(args.test_file)
    print(f"{len(mutants)} mutants x {len(tests)} tests, {args.jobs} at a time", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        baseline = dict(zip(tests, pool.map(runner, tests), strict=True))
    red = {t: o for t, o in baseline.items() if killed(o)}
    if red:
        for t, o in red.items():
            print(f"baseline {o}: {t}", file=sys.stderr)
        print("the untouched module is red; a mutation grid over it means nothing", file=sys.stderr)
        return 1

    def progress(mutant: Mutant, row: dict[str, str]) -> None:
        n = sum(killed(o) for o in row.values())
        elapsed = (time.monotonic() - started) / 60
        line = f"{mutant.id} {n:2d} killers  [{elapsed:5.1f} min]  {mutant.describe()}"
        print(line, file=sys.stderr)

    try:
        grid = run_grid(target, mutants, tests, runner, jobs=args.jobs, on_mutant=progress)
        print("re-running each sole kill on its own", file=sys.stderr)
        flaky = confirm_sole_kills(target, mutants, grid, runner)
    finally:
        if target.read_bytes() != original:
            raise SystemExit(f"{target} was not restored; the original is in {scratch}")
    wall = time.monotonic() - started

    mutant_rows = [{k: v for k, v in asdict(m).items() if k != "source"} for m in mutants]
    result = {
        "target": args.target,
        "test_file": args.test_file,
        "tests": tests,
        "mutants": mutant_rows,
        "baseline": baseline,
        "grid": grid,
        "flaky": flaky,
        "runs": runner.runs,
        "wall_seconds": round(wall, 1),
        "jobs": args.jobs,
        "timeout": args.timeout,
    }
    if args.compare:
        previous = json.loads(args.compare.read_text())
        if previous["mutants"] != mutant_rows or previous["tests"] != tests:
            raise SystemExit(f"{args.compare} ran other mutants or tests; nothing to compare")
        result["compared_with"] = str(args.compare)
        result["disagreements"] = [
            {"mutant": m, "test": t, "then": previous["grid"][m][t], "now": o}
            for m, row in grid.items()
            for t, o in row.items()
            if killed(previous["grid"][m][t]) != killed(o)
        ]
    result["summary"] = summarize(grid, tests)
    markdown = render_markdown(result)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n")
    if args.markdown:
        args.markdown.write_text(markdown)
    print(markdown)
    shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
