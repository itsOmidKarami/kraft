"""The tests a change can break, for `just test-targeted` (Kraft's own repo).

Verification runs this in place of the full suite. pytest-testmon picks the
tests whose executed code changed; it cannot see a file no test imports (a chain
YAML, a skill's markdown, a script a test runs as a subprocess), so a change to
one of those runs the full suite instead. The same blind spots decide
`just test`'s `--no-testmon` warning, so they are listed once, here, and the
justfile asks this script which changed paths they are (`blind`).

    targeted_tests.py blind               # of the paths on stdin, those `just test` warns about
    targeted_tests.py run [--dry-run]     # the plan, run (or printed)

The changed paths come from `$KRAFT_CHANGED_FILES`, a file of paths one per
line, which Kraft hands the command: after a failed round that is the whole
branch, after a clean one only what changed since. Without it the branch is
measured against `origin/main`'s merge base. If neither is known the full
suite runs; this never falls back to `HEAD`, which would see only uncommitted
work.

Two kinds of change to a blind path need not run the full suite, and both are
judged against the base (`$KRAFT_TEST_BASE`, else that merge base; with
neither, the path stays blind):

- an edit to a file under `EDIT_TESTS`, which adds the tests listed for it. A
  file added or deleted there stays blind: a reader that globs the tree, or
  names each file, is not something an edit's list can promise to cover.
- a YAML or JSON file whose change is comments and blank lines only
  (`comments_only`), which adds `COMMENT_TESTS` for its tree.

`blind` keeps to the path rule: `just test` has no way to add those tests.

Standard library only: it runs before anything is installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: (path, skips_python): a directory, or one file. testmon follows Python
#: execution only, so a change under one of these is invisible to it. Under
#: `src/kraft` and `tests/support` only the non-Python files are: their `.py` is
#: imported in-process, which testmon traces. The `tests/support` scripts and the
#: sample repo are the exception, named below because no test imports them.
BLIND: tuple[tuple[str, bool], ...] = (
    ("src/kraft", True),
    ("fixtures", False),
    ("config", False),
    ("tests/support", True),
    # Run as subprocesses, found by grepping how tests start them; a test in
    # tests/test_dev_targeted_tests.py fails when a new one is not listed.
    ("tests/support/fake_agent.py", False),
    ("tests/support/fake_reviewer.py", False),
    ("tests/support/fake_codex_app_server.py", False),
    # Copied into a repo and run by its own test command.
    ("tests/support/sample_repo", False),
    ("plugins/kraft/skills", False),
)

#: A blind file whose readers are known, so an edit to it adds them rather than
#: running the full suite. Found by tracing what reads the file, not by
#: grepping for its name: a test missing here is skipped silently (CI's full
#: suite still runs it), so a prefix whose readers cannot be listed stays out.
EDIT_TESTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # The console's stylesheet: read only when the Textual app starts.
    ("src/kraft/console/kraft.tcss", ("tests/console", "tests/test_theme_contrast.py")),
    # The plugin's skills: copied by `kraft admin init`, and read for their
    # frontmatter, wording and leaks. Not `src/kraft/skills`: that text goes
    # into every agent launch, and the fake agents scan it for the markers
    # that steer them, so an edit can change any test that walks a chain.
    (
        "plugins/kraft/skills",
        (
            "tests/test_init.py",
            "tests/skills/test_shipped_skills_are_generic.py",
            "tests/test_plugin_docs_surface.py",
        ),
    ),
)

#: Under `COMMENT_TREES` a YAML or JSON change of comments and blank lines only
#: is not blind. testmon still ties no test to the file, so the tests that read
#: its text, rather than its data, are added: a byte-for-byte copy, a doc that
#: quotes it, a `count` or `replace` on it, a rewrite that must keep its comments.
COMMENT_TREES = ("config", "src/kraft/detectors.yaml", "src/kraft/prices.json")
COMMENT_TESTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "config",
        (
            "tests/plugins/test_kraft_lite_parity.py",
            "tests/templates/test_materialization.py",
            "tests/drafts",
            "tests/api/test_drafts.py",
            "tests/api/test_settings.py",
            "tests/api/test_settings_templates.py",
            "tests/api/test_config_check.py",
            "tests/executor/test_dispatch.py",
            "tests/addons/test_builtin.py",
            "tests/addons/test_builtin_preview.py",
            "tests/addons/test_builtin_seed.py",
            # `fake_templates_dir` copies config/chains verbatim into every
            # `client` test; these edit or print that copy's text.
            "tests/cli/test_admin.py",
            "tests/api/test_gates.py",
            "tests/api/test_skip.py",
            "tests/api/test_one_off_intake.py",
            "tests/api/test_one_off_approval.py",
            "tests/api/test_attachment_patch.py",
        ),
    ),
)
_STRUCTURED = (".yaml", ".yml", ".json")

#: Read only by `INTENT_TESTS` and `python -m kraft.intent` (in `just ci-checks`,
#: which `test-targeted` runs first), so a change here adds those tests rather
#: than running the full suite. testmon still cannot see it, so `just test` warns.
INTENT_TREE = "docs/intent"
INTENT_TESTS = ("tests/test_intent_origins.py",)

#: Kraft's own collection beside `config/`, read as data by `COLLECTION_TESTS`
#: alone, so a change here adds them rather than running the full suite.
COLLECTION = ("addons", ".kraft")
COLLECTION_TESTS = ("tests/addons/test_kraft_sdlc_collection.py",)

#: Tests that a committed generated file is current. A class body runs once at
#: import, before any test, so testmon ties a change to it to no test; these are
#: cheap, so they always run.
GENERATED_TESTS = (
    "tests/test_config_schemas.py",
    "tests/api/test_api_shapes.py",
    "tests/test_vocab_generated.py",
)


@dataclass(frozen=True)
class Step:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)


def workers(env: Mapping[str, str]) -> str:
    """xdist's `-n`: `$KRAFT_TEST_WORKERS` when it is a positive count (a
    machine's own, set in its repos.yaml `env:`), else the logical CPUs plus
    two, as `just test` runs: the suite's tests wait on timers, sockets and
    child processes, and the extra workers fill those waits (#1273)."""
    given = env.get("KRAFT_TEST_WORKERS", "").strip()
    if given.isdigit() and int(given) > 0:
        return given
    cpus = os.cpu_count()
    return str(cpus + 2) if cpus else "logical"


def _full(n: str) -> Step:
    """The whole suite, still recorded: a full run that records nothing leaves
    the next selection larger, and the record never recovers (Kraft-4t45n.5).
    `--testmon-noselect` runs everything; `-m` would force it anyway."""
    return Step(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not e2e",
            "-n",
            n,
            "--testmon",
            "--testmon-noselect",
        ],
        {"COVERAGE_CORE": "ctrace"},
    )


@dataclass(frozen=True)
class Plan:
    #: Set when the full suite runs: why testmon cannot be trusted.
    reason: str | None
    steps: list[Step]


def _within(path: str, directory: str) -> bool:
    return path == directory or path.startswith(f"{directory}/")


def is_blind(path: str) -> bool:
    return any(
        _within(path, directory) and not (skips_python and path.endswith(".py"))
        for directory, skips_python in BLIND
    )


def is_intent(path: str) -> bool:
    return _within(path, INTENT_TREE)


def is_collection(path: str) -> bool:
    return any(_within(path, directory) for directory in COLLECTION)


def _tests_for(path: str, table: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[str, ...]:
    return tuple(test for prefix, tests in table if _within(path, prefix) for test in tests)


def _text(path: str, base: str | None, cwd: Path) -> str | None:
    """The file at `base` (a commit), or in the working tree when `base` is None;
    None when it is not there."""
    if base is not None:
        return _git(cwd, "show", f"{base}:{path}")
    try:
        return (cwd / path).read_text()
    except (OSError, UnicodeDecodeError):
        return None


def _parse(path: str, text: str) -> tuple[bool, object]:
    """(parsed, data). A YAML parser is imported here, not at the top: this
    script runs before anything is installed, and without one nothing is
    judged equal."""
    try:
        if path.endswith(".json"):
            return True, json.loads(text)
        import yaml

        return True, yaml.safe_load(text)
    except Exception:  # ImportError, or a parse error of either library
        return False, None


def _code_lines(text: str) -> list[str]:
    return [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


def comments_only(path: str, base: str, cwd: Path) -> bool:
    """Whether a YAML or JSON file changed in nothing but whole-line comments
    and blank lines since `base`. Both checks: the lines alone would take a `#`
    line inside a block scalar for a comment, and the data alone would let a
    test that `replace`s a line it expects miss a reformatted one."""
    if not (path.endswith(_STRUCTURED) and any(_within(path, t) for t in COMMENT_TREES)):
        return False
    then, now = _text(path, base, cwd), _text(path, None, cwd)
    if then is None or now is None or _code_lines(then) != _code_lines(now):
        return False
    # A reader that strips the file's end (the CLI prints it `rstrip`ped) sees
    # a blank line there, though not one between lines.
    if then[len(then.rstrip()) :] != now[len(now.rstrip()) :]:
        return False
    (ok_then, data_then), (ok_now, data_now) = _parse(path, then), _parse(path, now)
    return ok_then and ok_now and data_then == data_now


def edited(path: str, base: str, cwd: Path) -> bool:
    """Whether `path` is there both at `base` and in the working tree: an edit,
    not an add or a delete (a rename is both, changed-file lists use
    `--no-renames`)."""
    return _git(cwd, "cat-file", "-e", f"{base}:{path}") is not None and (cwd / path).is_file()


def _git(cwd: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except UnicodeDecodeError:  # a file `git show` printed that is not UTF-8
        return None
    return done.stdout if done.returncode == 0 else None


def _merge_base(cwd: Path) -> str | None:
    base = _git(cwd, "merge-base", "HEAD", "origin/main")
    return base.strip() if base and base.strip() else None


def read_base(env: Mapping[str, str], cwd: Path) -> str | None:
    """What the change is measured against: the item's whole-branch base, which
    Kraft hands as `$KRAFT_TEST_BASE`, else the merge base with origin/main."""
    return env.get("KRAFT_TEST_BASE") or _merge_base(cwd)


def read_changed(env: Mapping[str, str], cwd: Path) -> list[str] | None:
    """The changed paths, or None when they cannot be known (run everything).
    An empty list is an answer: nothing changed."""
    listing = env.get("KRAFT_CHANGED_FILES")
    if listing:
        try:
            text = Path(listing).read_text()
        except (OSError, UnicodeDecodeError):
            return None
        return [line for line in text.splitlines() if line]
    base = _merge_base(cwd)
    if base is None:
        return None
    tracked = _git(cwd, "diff", "--name-only", "--no-renames", base)
    untracked = _git(cwd, "ls-files", "--others", "--exclude-standard")
    if tracked is None or untracked is None:
        return None
    return sorted({line for line in (tracked + untracked).splitlines() if line})


def plan(
    changed: list[str] | None, base: str | None = None, cwd: Path = Path(), n: str = "logical"
) -> Plan:
    if changed is None:
        return Plan("no changed-file list and no merge base with origin/main", [_full(n)])
    blind, known = [], []
    for path in (p for p in changed if is_blind(p)):
        mapped = _tests_for(path, EDIT_TESTS)
        if base is not None and mapped and edited(path, base, cwd):
            known += mapped
        elif base is not None and comments_only(path, base, cwd):
            known += _tests_for(path, COMMENT_TESTS)
        else:
            blind.append(path)
    if blind:
        more = f" (and {len(blind) - 5} more)" if len(blind) > 5 else ""
        return Plan(f"testmon cannot see a change to {', '.join(blind[:5])}{more}", [_full(n)])
    tests = (
        GENERATED_TESTS
        + (INTENT_TESTS if any(is_intent(path) for path in changed) else ())
        + (COLLECTION_TESTS if any(is_collection(path) for path in changed) else ())
    )
    known = [test for test in dict.fromkeys(known) if test not in tests]
    # Mapped tests can be whole directories: xdist, and the full run's marker.
    spread = ["-m", "not e2e", "-n", n] if known else []
    return Plan(
        None,
        [
            # A second run, so it cannot disturb the record the next one reads.
            Step([sys.executable, "-m", "pytest", *tests, *known, "--no-testmon", "-q", *spread]),
            # xdist, because single-process testmon is ~9x slower on this suite;
            # forceselect, because `-m` would otherwise turn selection off.
            Step(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-m",
                    "not e2e",
                    "-n",
                    n,
                    "--testmon",
                    "--testmon-forceselect",
                ],
                {"COVERAGE_CORE": "ctrace"},
            ),
        ],
    )


def main(argv: Sequence[str]) -> int:
    command, *rest = argv
    if command == "blind":
        paths = sys.stdin.read().splitlines()
        print("\n".join(p for p in paths if is_blind(p) or is_intent(p)))
        return 0
    if command != "run" or set(rest) - {"--dry-run"}:
        print(__doc__, file=sys.stderr)
        return 2
    changed = read_changed(os.environ, Path.cwd())
    chosen = plan(changed, read_base(os.environ, Path.cwd()), Path.cwd(), workers(os.environ))
    if chosen.reason:
        print(f"test-targeted: full suite -- {chosen.reason}", flush=True)
    else:
        print(f"test-targeted: testmon selection, {len(changed or [])} changed files", flush=True)
    for step in chosen.steps:
        if "--dry-run" in rest:
            print(" ".join(step.argv))
            continue
        done = subprocess.run(step.argv, env={**os.environ, **step.env}, check=False)
        if done.returncode:
            return done.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["run"]))
