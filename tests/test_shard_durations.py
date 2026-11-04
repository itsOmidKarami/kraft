"""`dev/shard_durations.py` turns the shards' junit files into the table that
`conftest.py` splits by, so a mistake in it unbalances the shards unseen."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "shard_durations", _ROOT / "dev" / "shard_durations.py"
)
shard_durations = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shard_durations)

_SUITE = "tests.test_suite_config"
_PLUGIN = "plugins.kraft-lite.tests.test_bindings"


def _run(directory: Path, cases: list[tuple[str, float]]) -> Path:
    body = "".join(
        f'<testcase classname="{c}" name="t{i}" time="{t}"/>' for i, (c, t) in enumerate(cases)
    )
    directory.mkdir()
    (directory / "junit-1.xml").write_text(
        f"<testsuites><testsuite>{body}</testsuite></testsuites>"
    )
    return directory


def test_a_files_time_is_its_tests_summed_and_the_median_over_the_runs(tmp_path):
    """Three runs, one slow: the median ignores it. A class name's tail is a test
    class, not part of the path; a module the tree does not hold is left out, and
    a file seen in only some runs takes the median of those."""
    runs = [
        _run(tmp_path / "a", [(_SUITE, 1.0), (f"{_SUITE}.TestX", 2.0), ("no.such.module", 9.0)]),
        _run(tmp_path / "b", [(_SUITE, 4.0), (_PLUGIN, 1.0)]),
        _run(tmp_path / "c", [(f"{_SUITE}.TestY", 20.0), (_PLUGIN, 3.0)]),
    ]
    table = shard_durations.build(runs, shard_durations.modules())
    assert table == {
        "plugins/kraft-lite/tests/test_bindings.py": 2.0,
        "tests/test_suite_config.py": 4.0,
    }
