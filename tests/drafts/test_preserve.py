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

repos:
  - path: /a   # the dev repo
    project: null
  # the scratch repo
  - path: /b
    setup_command: x

# the cron list
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
        pytest.param(
            lambda d: d["budget"].__setitem__("daily_usd", 60),
            ["default:             { attempts: 3, wall_clock_s: 3600 }   # trailing note"],
            [],
            id="an-untouched-flow-mapping-keeps-its-spacing",
        ),
        pytest.param(
            lambda d: d["repos"][1].__setitem__("setup_command", "y"),
            [
                "  - path: /a   # the dev repo\n    project: null\n"
                "  # the scratch repo\n  - path: /b"
            ],
            ["setup_command: x"],
            id="one-entry-of-a-list-edited-keeps-the-others-comments",
        ),
        pytest.param(
            lambda d: d["repos"].pop(0),
            ["repos:\n  # the scratch repo\n  - path: /b\n    setup_command: x"],
            ["/a"],
            id="an-entry-removed-keeps-the-rest-as-written",
        ),
        pytest.param(
            lambda d: d["repos"].pop(),
            ["    project: null\n\n# the cron list\nschedules:"],
            ["/b", "# the scratch repo"],
            id="the-last-entry-removed-keeps-the-next-keys-comment",
        ),
        pytest.param(
            lambda d: d["repos"].append({"path": "/c"}),
            ["  - path: /c\n\n# the cron list\nschedules:"],
            [],
            id="an-entry-appended-goes-before-the-next-keys-comment",
        ),
        pytest.param(
            lambda d: d["repos"][1].__setitem__("env", {"on": "x"}),
            ["'on': x"],
            [],
            id="a-new-key-on-stays-a-string",
        ),
        *[
            pytest.param(
                lambda d, word=word: d["repos"][1].__setitem__("env", {"DEBUG": word}),
                [f"DEBUG: '{word}'"],
                [],
                id=f"the-string-{word}-stays-a-string",
            )
            for word in ("yes", "no", "on", "off", "Yes", "OFF")
        ],
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
    "text, data, written",
    [
        (
            "loops:\n  fix:\n    attempts: 3\n\n# the fallback\ndefault: 1\n",
            {"loops": {}, "default": 1},
            "loops: {}\n\n# the fallback\ndefault: 1\n",
        ),
        ("a:\n  b:\n    c: 1\n# after\nd: 2\n", {"a": {"b": {}}, "d": 2}, "  b: {}\n# after\n"),
        ("a:\n  only: 1\n# after a\nz: 2\n", {"a": {}, "z": 2}, "a: {}\n# after a\n"),
    ],
    ids=["the-last-loop-removed", "a-nested-mapping-emptied", "the-only-key-dropped"],
)
def test_an_emptied_mapping_is_written_as_yaml_with_the_comment_after_it(text, data, written):
    """A block mapping emptied by an op (Settings › Policy removing the last
    loop) was dumped as a bare `{}` on the next line, after the comment that
    followed it: not YAML, so the draft could not be published."""
    out = preserve.rewrite(text, data)
    assert yaml.safe_load(out) == data
    assert written in out, out


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


ENTRIES = """\
repos:
  # A: the api
  - path: /a
  # B: the web
  - path: /b
  # C: the cli
  - path: /c
# after the list
other: 1
"""


@pytest.mark.parametrize(
    "removed",
    [(0,), (1,), (2,), (0, 1), (0, 2), (1, 2)],
    ids=["first", "middle", "last", "first-two", "first-and-last", "last-two"],
)
def test_a_removed_entrys_own_comment_goes_with_it_and_no_other(removed):
    """ruamel keeps a comment line above an entry on the entry before it, so
    removing one took the next entry's comment and left its own labelling
    the next entry (R12 review P2-3)."""
    data = yaml.safe_load(ENTRIES)
    kept = [e for i, e in enumerate(data["repos"]) if i not in removed]
    out = preserve.rewrite(ENTRIES, {**data, "repos": kept})
    assert yaml.safe_load(out) == {**data, "repos": kept}
    labels = [line.strip() for line in out.splitlines() if line.lstrip().startswith("#")]
    names = {"/a": "# A: the api", "/b": "# B: the web", "/c": "# C: the cli"}
    assert labels == [names[e["path"]] for e in kept] + ["# after the list"]
    for entry in kept:
        assert f"{names[entry['path']]}\n  - path: {entry['path']}" in out
