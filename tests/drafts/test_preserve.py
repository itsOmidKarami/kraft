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

schedules:
  - {cron: '0 9 * * 1', title: weekly}

# why the archive
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
            ["archive:", "after_days"],
            id="a-dropped-key-takes-its-lines",
        ),
        pytest.param(
            lambda d: d.pop("schedules"),
            ["# why the archive", "daily_usd: 50"],
            ["weekly"],
            id="a-dropped-list-keeps-the-next-keys-comment",
        ),
        pytest.param(
            lambda d: d.__setitem__("schedules", [{"cron": "0 8 * * *", "title": "daily"}]),
            ["# why the archive", "daily"],
            ["weekly"],
            id="a-replaced-list-keeps-the-next-keys-comment",
        ),
        pytest.param(
            lambda d: d["budget"].pop("daily_usd"),
            ["# why the archive", "work_item_usd: 10     # per item"],
            ["daily_usd"],
            id="a-dropped-nested-key-keeps-what-follows",
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


@pytest.mark.parametrize(
    "change",
    [
        lambda d: (d["budget"].update(work_item_usd=25), d.update(max_concurrent=5)),
        lambda d: d.pop("archive"),
        lambda d: d.pop("budget"),
        lambda d: d["budget"].pop("daily_usd"),
    ],
    ids=["an-edit", "dropping-archive", "dropping-budget", "dropping-budget-daily-usd"],
)
def test_the_shipped_policy_keeps_its_documentation_through_an_edit(change):
    """The seeded `policy.yaml` is mostly the documentation of its keys; one
    Settings click must not delete it. Dropping a key once deleted the block
    after it: 106 lines of the levels and maxima after `archive`."""
    text = SHIPPED_POLICY.read_text()
    data = yaml.safe_load(text)
    change(data)
    out = preserve.rewrite(text, data)
    assert yaml.safe_load(out) == data
    comments = [line for line in text.splitlines() if line.startswith("#")]
    assert comments and [line for line in comments if line not in out] == []
