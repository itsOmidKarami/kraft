"""An add-on's ancestry: the parents it builds on, up to a root, and why a
tree does not resolve; and the left-out cascade down a tree."""

from dataclasses import replace

import pytest
from support.addons import installed

from kraft.addons import inherit


def _tree(tmp_path, *links):
    """One `InstalledAddon` per link, `(id, parent id or None, **fields)`, all
    on one empty store. A child accepts its parent's 1.x unless it says
    otherwise (`parent_range`)."""
    store = installed(tmp_path, "any")
    out = []
    for addon_id, parent, *fields in links:
        over = fields[0] if fields else {}
        out.append(
            replace(
                store,
                id=addon_id,
                name=addon_id.split("@", 1)[0],
                namespace=addon_id.split("@", 1)[0],
                **{"parent": parent, "parent_range": "1" if parent else None, **over},
            )
        )
    return out


def _levels(n):
    """`l0@acme` builds on `l1@acme`, ... on `l<n-1>@acme`, the root."""
    return [(f"l{i}@acme", f"l{i + 1}@acme" if i + 1 < n else None) for i in range(n)]


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        pytest.param([("company@corp", None)], ("company@corp",), id="root"),
        pytest.param(
            [
                ("team@payments", "dept@platform"),
                ("dept@platform", "company@corp"),
                ("company@corp", None),
            ],
            ("team@payments", "dept@platform", "company@corp"),
            id="three-levels",
        ),
        pytest.param(
            [("dept@platform", "company@corp")],
            "its parent company@corp is not installed",
            id="missing-parent",
        ),
        pytest.param(
            [
                ("dept@platform", "company@corp"),
                ("company@corp", None, {"left_out": "is disabled"}),
            ],
            "its parent company@corp is not loaded: is disabled",
            id="left-out-parent",
        ),
        pytest.param(
            [
                ("dept@platform", "company@corp", {"parent_range": "3.1"}),
                ("company@corp", None, {"version": "4.0.0"}),
            ],
            "its parent company@corp is 4.0.0, outside its range 3.1 (needs 3.x)",
            id="out-of-range",
        ),
        pytest.param(
            [("dept@platform", "company@corp"), ("company@corp", "dept@platform")],
            "its ancestry dept@platform -> company@corp -> dept@platform is a cycle",
            id="cycle",
        ),
        pytest.param(
            _levels(8),
            tuple(f"l{i}@acme" for i in range(8)),
            id="depth-8",
        ),
        pytest.param(_levels(9), "its ancestry is deeper than 8 add-ons", id="depth-9"),
        pytest.param(
            [("company@payments", "company@corp"), ("company@corp", None)],
            "its ancestor company@corp has its name company",
            id="same-name-ancestor",
        ),
        pytest.param(
            [("dept@platform", "library@repo:app"), ("library@repo:app", None)],
            "its parent library@repo:app is not installed",
            id="repo-library-not-a-parent",
        ),
    ],
)
def test_ancestry(tmp_path, links, expected):
    addons = _tree(tmp_path, *links)
    if isinstance(expected, str):
        with pytest.raises(inherit.InheritanceError) as raised:
            inherit.ancestry(addons[0], addons)
        assert str(raised.value) == expected
    else:
        assert tuple(a.id for a in inherit.ancestry(addons[0], addons)) == expected


def test_cascade(tmp_path):
    """A root left out takes its whole tree with it, each add-on naming its own
    parent, child listed first or not; an add-on outside the tree loads."""
    addons = _tree(
        tmp_path,
        ("team@payments", "dept@platform"),
        ("other@acme", None),
        ("dept@platform", "company@corp"),
        ("company@corp", None, {"left_out": "is disabled"}),
    )
    out = inherit.cascade(addons)
    assert [(a.id, a.left_out) for a in out] == [
        (
            "team@payments",
            "its parent dept@platform is not loaded: "
            "its parent company@corp is not loaded: is disabled",
        ),
        ("other@acme", None),
        ("dept@platform", "its parent company@corp is not loaded: is disabled"),
        ("company@corp", "is disabled"),
    ]
