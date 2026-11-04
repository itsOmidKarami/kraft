#!/usr/bin/env python3
"""Write tests/shard_durations.json: seconds per test file, for the shard split.

CI shards the unit tier by test file (`conftest.py`), the longest file first onto
the lightest shard. A file the table does not name is placed by a hash of its
path, so the table need only be roughly right; refresh it when the shards drift
apart (the busiest shard's `pytest` step much longer than the others').

    # a recent green main run; repeat for a few
    gh run download RUN_ID -n test-reports -D /tmp/run-1
    uv run python dev/shard_durations.py /tmp/run-1 /tmp/run-2 /tmp/run-3

Each argument is a directory holding one run's junit-*.xml. A file's time is the
median over the runs of its tests' summed time, as the shards measured it.
Prints what a 3 to 6 way split of the new table predicts.
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "tests" / "shard_durations.json"
TREES = ("tests", "plugins/kraft-lite/tests")


def modules() -> dict[str, str]:
    """`tests.adapters.test_x` to `tests/adapters/test_x.py`: junit names a test by
    its module, and the shard split keys on the path."""
    paths = (
        p.relative_to(ROOT)
        for tree in TREES
        for p in (ROOT / tree).rglob("*.py")
        if p.name.startswith("test_") or p.name.endswith("_test.py")
    )
    return {p.with_suffix("").as_posix().replace("/", "."): p.as_posix() for p in paths}


def file_of(classname: str, known: dict[str, str]) -> str | None:
    """The longest module prefix of a class name: its tail may be a test class."""
    parts = classname.split(".")
    for end in range(len(parts), 0, -1):
        if path := known.get(".".join(parts[:end])):
            return path
    return None


def per_file(run: Path, known: dict[str, str]) -> dict[str, float]:
    seconds: dict[str, float] = {}
    for xml in sorted(run.glob("junit*.xml")):
        for case in ET.parse(xml).getroot().iter("testcase"):
            if path := file_of(case.get("classname", ""), known):
                seconds[path] = seconds.get(path, 0.0) + float(case.get("time", 0))
    return seconds


def build(runs: list[Path], known: dict[str, str]) -> dict[str, float]:
    measured = [per_file(run, known) for run in runs]
    names = sorted(set().union(*measured))
    return {n: round(statistics.median(m[n] for m in measured if n in m), 1) for n in names}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    table = build([Path(a) for a in argv], modules())
    TABLE.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n")
    total = sum(table.values())
    print(f"{len(table)} files, {total:.0f}s of test time -> {TABLE.relative_to(ROOT)}")
    spec = importlib.util.spec_from_file_location("root_conftest", ROOT / "conftest.py")
    conftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conftest)
    for count in range(3, 7):
        plan = conftest._split(table, count)
        loads = [sum(s for f, s in table.items() if plan[f] == k) for k in range(count)]
        worst = max(loads)
        print(
            f"  {count} shards: busiest {worst:.0f}s of {total:.0f}s,"
            f" {worst / (total / count):.2f}x the ideal"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
