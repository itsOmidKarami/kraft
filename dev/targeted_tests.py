"""The tests a change can break, for `just test-targeted` (Kraft's own repo).

Verification runs this in place of the full suite. pytest-testmon picks the
tests whose executed code changed; it cannot see a file no test imports (a chain
YAML, a skill's markdown, a script a test runs as a subprocess), so a change to
one of those runs the full suite instead. The same blind spots decide
`just test`'s `--no-testmon` warning, so they are listed once, here, and the
justfile asks this script for them (`pathspecs`).

    targeted_tests.py pathspecs           # git pathspecs for the blind spots
    targeted_tests.py run [--dry-run]     # the plan, run (or printed)

The changed paths come from `$KRAFT_CHANGED_FILES`, a file of paths one per
line, which Kraft hands the command: after a failed round that is the whole
branch, after a clean one only what changed since. Without it the branch is
measured against `origin/main`'s merge base. If neither is known the full
suite runs; this never falls back to `HEAD`, which would see only uncommitted
work.

Standard library only: it runs before anything is installed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: (directory, skips_python). testmon follows Python execution only, so a change
#: under one of these is invisible to it. Under `src/kraft` only the non-Python
#: files are: its `.py` is what testmon traces. `tests/support` and `fixtures`
#: code also runs as subprocesses testmon cannot trace.
BLIND: tuple[tuple[str, bool], ...] = (
    ("src/kraft", True),
    ("fixtures", False),
    ("config", False),
    ("tests/support", False),
    ("docs/intent", False),
    ("plugins/kraft/skills", False),
)

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


#: The whole suite, still recorded: a full run that records nothing leaves the
#: next selection larger, and the record never recovers (Kraft-4t45n.5).
#: `--testmon-noselect` runs everything; `-m` would force it anyway.
_FULL = Step(
    [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "not e2e",
        "-n",
        "logical",
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


def pathspecs() -> list[str]:
    """Git pathspecs selecting exactly what `is_blind` does."""
    specs: list[str] = []
    for directory, skips_python in BLIND:
        specs.append(directory)
        if skips_python:
            specs.append(f":(glob,exclude){directory}/**/*.py")
    return specs


def is_blind(path: str) -> bool:
    for directory, skips_python in BLIND:
        if path == directory or path.startswith(f"{directory}/"):
            if skips_python and path.endswith(".py"):
                continue
            return True
    return False


def _git(cwd: Path, *args: str) -> str | None:
    done = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout if done.returncode == 0 else None


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
    base = _git(cwd, "merge-base", "HEAD", "origin/main")
    if not base or not base.strip():
        return None
    tracked = _git(cwd, "diff", "--name-only", "--no-renames", base.strip())
    untracked = _git(cwd, "ls-files", "--others", "--exclude-standard")
    if tracked is None or untracked is None:
        return None
    return sorted({line for line in (tracked + untracked).splitlines() if line})


def plan(changed: list[str] | None) -> Plan:
    if changed is None:
        return Plan("no changed-file list and no merge base with origin/main", [_FULL])
    blind = [path for path in changed if is_blind(path)]
    if blind:
        more = f" (and {len(blind) - 5} more)" if len(blind) > 5 else ""
        return Plan(f"testmon cannot see a change to {', '.join(blind[:5])}{more}", [_FULL])
    return Plan(
        None,
        [
            # A second run, so it cannot disturb the record the next one reads.
            Step([sys.executable, "-m", "pytest", *GENERATED_TESTS, "--no-testmon", "-q"]),
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
                    "logical",
                    "--testmon",
                    "--testmon-forceselect",
                ],
                {"COVERAGE_CORE": "ctrace"},
            ),
        ],
    )


def main(argv: Sequence[str]) -> int:
    command, *rest = argv
    if command == "pathspecs":
        print("\n".join(pathspecs()))
        return 0
    if command != "run" or set(rest) - {"--dry-run"}:
        print(__doc__, file=sys.stderr)
        return 2
    changed = read_changed(os.environ, Path.cwd())
    chosen = plan(changed)
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
