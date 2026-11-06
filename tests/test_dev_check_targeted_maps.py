"""`dev/check_targeted_maps.py`: the experiment that checks targeted_tests' lists.

The suite runs themselves are not tested here; what decides the verdict is.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_targeted_maps.py"


@pytest.fixture(scope="module")
def cm():
    spec = importlib.util.spec_from_file_location("_dev_check_targeted_maps", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules[spec.name]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("FAILED tests/a.py::test_x - AssertionError: no\n", {"tests/a.py::test_x"}),
        ("ERROR tests/b.py::test_y[row-1]\n", {"tests/b.py::test_y[row-1]"}),
        ("tests/a.py ..F\n1 failed in 2s\n  FAILED is not at the start\n", set()),
    ],
    ids=["a-failure-with-its-message", "an-error-with-a-param-id", "no-summary-line"],
)
def test_failures_are_read_from_the_short_summary(cm, output, expected):
    assert cm.failures(output) == expected


@pytest.mark.parametrize(
    ("baseline", "perturbed", "named", "expected"),
    [
        (set(), {"tests/drafts/test_a.py::t"}, ["tests/drafts"], []),
        (set(), {"tests/api/test_drafts.py::t"}, ["tests/api/test_drafts.py"], []),
        (
            set(),
            {"tests/drafts_old/test_a.py::t"},
            ["tests/drafts"],
            ["tests/drafts_old/test_a.py::t"],
        ),
        (set(), {"tests/x.py::t"}, ["tests/y.py"], ["tests/x.py::t"]),
        ({"tests/x.py::t"}, {"tests/x.py::t"}, [], []),
    ],
    ids=[
        "under-a-named-directory",
        "a-named-file",
        "a-directory-name-is-not-a-prefix",
        "not-named",
        "failing-in-the-baseline-too",
    ],
)
def test_a_new_failure_no_named_test_holds_is_missed(cm, baseline, perturbed, named, expected):
    assert cm.missed(baseline, perturbed, named) == expected


@pytest.mark.parametrize(
    ("path", "text", "kept"),
    [
        # A comment above the list item, at the item's own indent.
        (
            "c.yaml",
            "a:\n  b: 1\n  c:\n    - d\n",
            "    # check_targeted_maps: an injected comment\n    - d",
        ),
        # Nothing inside the block scalar, where a `#` line or a blank is text.
        ("c.yaml", "a: |\n  one\n\n  two\nb: 1\n", "a: |\n  one\n\n  two\n"),
        ("c.json", '{\n  "a": 1,\n  "b": [\n    2\n  ]\n}\n', '\n\n  "a": 1,'),
    ],
    ids=["nested-mapping", "block-scalar", "json-takes-blank-lines-only"],
)
def test_comments_go_everywhere_the_data_stays_equal(cm, path, text, kept):
    after = cm.comment_everywhere(path, text)
    load = json.loads if path.endswith(".json") else yaml.safe_load
    assert load(after) == load(text)
    assert kept in after
    assert after.endswith(text.splitlines(keepends=True)[-1])
    assert ("#" in after) != path.endswith(".json")
    assert after.count("\n") > text.count("\n")
