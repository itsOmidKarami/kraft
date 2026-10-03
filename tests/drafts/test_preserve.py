"""`drafts.preserve.rewrite`: a Settings op writes its mapping back over the
file's own text, and the comments, order and layout it did not touch stay."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kraft.drafts import preserve

SHIPPED_POLICY = Path(__file__).resolve().parents[2] / "config" / "policy.yaml"

AUTHORED = """\
# the operator's header
loops: {}
default:             { attempts: 3, wall_clock_s: 3600 }   # trailing note

# why the budget
budget:
  work_item_usd: 10     # per item
  daily_usd: 50

archive:
  after_days: 30
"""


@pytest.mark.parametrize(
    ("change", "expect", "gone"),
    [
        pytest.param(
            lambda d: d["budget"].__setitem__("work_item_usd", 20),
            ["# the operator's header", "work_item_usd: 20     # per item", "# why the budget"],
            [],
            id="a-changed-scalar-keeps-its-comment",
        ),
        pytest.param(
            lambda d: d.__setitem__("max_concurrent", 4),
            ["after_days: 30\nmax_concurrent: 4"],
            [],
            id="an-added-key-goes-last",
        ),
        pytest.param(
            lambda d: d.pop("archive"),
            ["# why the budget"],
            ["archive", "after_days"],
            id="a-dropped-key-takes-its-lines",
        ),
        pytest.param(
            lambda d: d["default"].__setitem__("attempts", 5),
            ["default: {attempts: 5, wall_clock_s: 3600}", "# trailing note"],
            [],
            id="a-flow-mapping-stays-flow",
        ),
        pytest.param(
            lambda d: None,
            ["# the operator's header", "# why the budget", "# trailing note", "# per item"],
            [],
            id="no-change-keeps-every-comment",
        ),
    ],
)
def test_rewrite_applies_the_change_and_keeps_the_rest(change, expect, gone):
    data = yaml.safe_load(AUTHORED)
    change(data)
    out = preserve.rewrite(AUTHORED, data)
    assert yaml.safe_load(out) == data, out
    for text in expect:
        assert text in out, out
    for text in gone:
        assert text not in out, out


@pytest.mark.parametrize(
    "text",
    ["", "   \n", "- a\n- list\n", "key: [unterminated\n"],
    ids=["empty", "blank", "a-list", "unparsable"],
)
def test_rewrite_falls_back_to_a_plain_dump_with_nothing_to_keep(text):
    out = preserve.rewrite(text, {"a": 1, "b": {"c": [1, 2]}})
    assert yaml.safe_load(out) == {"a": 1, "b": {"c": [1, 2]}}


def test_the_shipped_policy_keeps_its_documentation_through_an_edit():
    """The seeded `policy.yaml` is mostly the documentation of its keys; one
    Settings click must not delete it."""
    text = SHIPPED_POLICY.read_text()
    data = yaml.safe_load(text)
    data["budget"]["work_item_usd"] = 25
    data["max_concurrent"] = 5
    out = preserve.rewrite(text, data)
    assert yaml.safe_load(out) == data
    comments = [line for line in text.splitlines() if line.startswith("#")]
    assert comments and all(line in out for line in comments)
    assert "work_item_usd: 25" in out and "max_concurrent: 5" in out
