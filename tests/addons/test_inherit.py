"""An add-on's ancestry: the parents it builds on, up to a root, and why a
tree does not resolve; and the left-out cascade down a tree."""

import json
from dataclasses import replace

import pytest
from support.addons import addon_json, edit_store, example_tree, installed

from kraft.addons import inherit, manifest


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
            [
                ("dept@platform", "company@corp", {"parent_range": None}),
                ("company@corp", None, {"version": "4.0.0"}),
            ],
            ("dept@platform", "company@corp"),
            id="no-range-is-not-checked",
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


_DEPT_LOOP = "dept@platform -> company@corp -> dept@platform"
_COMPANY_LOOP = "company@corp -> dept@platform -> company@corp"


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        pytest.param(
            [
                ("team@payments", "dept@platform"),
                ("other@acme", None),
                ("dept@platform", "company@corp"),
                ("company@corp", None, {"left_out": "is disabled"}),
            ],
            [
                (
                    "team@payments",
                    "its parent dept@platform is not loaded: "
                    "its parent company@corp is not loaded: is disabled",
                ),
                ("other@acme", None),
                ("dept@platform", "its parent company@corp is not loaded: is disabled"),
                ("company@corp", "is disabled"),
            ],
            id="left-out-root",
        ),
        pytest.param(
            [
                ("team@payments", "dept@platform"),
                ("other@acme", None),
                ("dept@platform", "company@corp"),
                ("company@corp", "dept@platform"),
            ],
            [
                (
                    "team@payments",
                    f"its parent dept@platform is not loaded: its ancestry {_DEPT_LOOP} is a cycle",
                ),
                ("other@acme", None),
                ("dept@platform", f"its ancestry {_DEPT_LOOP} is a cycle"),
                ("company@corp", f"its ancestry {_COMPANY_LOOP} is a cycle"),
            ],
            id="below-a-cycle",
        ),
    ],
)
def test_cascade(tmp_path, links, expected):
    """A broken tree takes every add-on below it out, each naming its own
    parent, child listed first or not; an add-on outside the tree loads."""
    out = inherit.cascade(_tree(tmp_path, *links))
    assert [(a.id, a.left_out) for a in out] == expected


def _requiring(name, kraft, harnesses, profiles):
    return addon_json(name, requires={"kraft": kraft, "harnesses": harnesses, "profiles": profiles})


#: team -> dept -> company, each with what it requires.
_REQUIRED = {
    "team": _requiring("team", "2.3", ["codex"], ["fast"]),
    "dept": _requiring("dept", "2", ["codex", "claude"], ["deep", "fast"]),
    "company": _requiring("company", "2", ["claude"], ["deep", "strong"]),
}


@pytest.mark.parametrize("read", ["from-stores", "from-manifests"])
def test_requirements(tmp_path, read):
    """The union along the chain, in chain order and without repeats, with
    `requires.kraft` the child's own. Manifests already read are not read
    again: the stores here then hold none."""
    chain = []
    for name in _REQUIRED:
        files = {manifest.ADDON_JSON: json.dumps(_REQUIRED[name])} if read == "from-stores" else {}
        chain.append(installed(tmp_path, name, files=files))
    manifests = (
        {f"{n}@acme": manifest.addon(m, manifest.ADDON_JSON) for n, m in _REQUIRED.items()}
        if read == "from-manifests"
        else None
    )
    required = inherit.requirements(chain, manifests)
    assert (required.kraft, required.harnesses, required.profiles) == (
        "2.3",
        ["codex", "claude"],
        ["fast", "deep", "strong"],
    )


def _ships_deploy_review(text="dept method"):
    return {"skills": {"deploy-review": text}}


@pytest.mark.parametrize(
    ("edits", "kind", "expected"),
    [
        pytest.param({}, "skills", {"deploy-review": "company"}, id="skill-from-root"),
        pytest.param(
            {"dept": _ships_deploy_review()},
            "skills",
            {"deploy-review": "platform-dept"},
            id="skill-overridden-by-dept",
        ),
        pytest.param({"team": _ships_deploy_review()}, "skills", {}, id="skill-own"),
        pytest.param(
            {"team": {"library": {"remove": {"skills": ["deploy-review"]}}}},
            "skills",
            {},
            id="skill-removed",
        ),
        pytest.param(
            {}, "profiles", {"fast": "company", "slow": "company"}, id="profile-from-root"
        ),
        pytest.param(
            {"dept": {"library": {"remove": {"profiles": ["slow"]}}}},
            "profiles",
            {"fast": "company"},
            id="profile-removed-by-dept",
        ),
        pytest.param(
            {"dept": _ships_deploy_review(), "alias": "plat"},
            "skills",
            {"deploy-review": "plat"},
            id="alias-parent",
        ),
        pytest.param({"asked": 0}, "skills", {}, id="root"),
        pytest.param({"left_out": "not loaded"}, "skills", {}, id="left-out"),
        pytest.param({"damage": 0}, "skills", {}, id="damaged-ancestor-store"),
        pytest.param({"without": 1}, "skills", {}, id="ancestry-raises"),
    ],
)
def test_inherited(tmp_path, edits, kind, expected):
    edits = dict(edits)
    asked = edits.pop("asked", 2)
    left_out = edits.pop("left_out", None)
    damage = edits.pop("damage", None)
    without = edits.pop("without", None)
    tree = list(example_tree(tmp_path, **edits))
    if damage is not None:
        edit_store(tree[damage].root, ".kraft-digest", "tampered")
    child = replace(tree[asked], left_out=left_out)
    if without is not None:
        del tree[without]
    assert inherit.inherited(kind, child, tree) == expected


def _roots(company, dept, team):
    return {"company": company.root, "platform-dept": dept.root, "team-payments": team.root}


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        pytest.param(
            {},
            lambda c, d, t: {
                **_roots(c, d, t),
                "platform-dept:deploy-review": c.root,
                "team-payments:deploy-review": c.root,
            },
            id="inherited-maps-to-owner",
        ),
        pytest.param(
            {"team": _ships_deploy_review("team method")},
            lambda c, d, t: {**_roots(c, d, t), "platform-dept:deploy-review": c.root},
            id="own-has-no-key",
        ),
        pytest.param(
            {"team": {"library": {"remove": {"skills": ["deploy-review"]}}}},
            lambda c, d, t: {**_roots(c, d, t), "platform-dept:deploy-review": c.root},
            id="removed-has-no-key",
        ),
        pytest.param(
            {"damage": "library.yaml"},
            lambda c, d, t: {"company": c.root, "platform-dept": None, "team-payments": None},
            id="unreadable-tree",
        ),
        pytest.param(
            {"without": 0},
            lambda c, d, t: {"platform-dept": None, "team-payments": None},
            id="ancestry-raises",
        ),
        pytest.param(
            {"alias": "plat", "dept": _ships_deploy_review()},
            lambda c, d, t: {
                "company": c.root,
                "plat": d.root,
                "team-payments": t.root,
                "team-payments:deploy-review": d.root,
            },
            id="alias-parent",
        ),
        pytest.param({"only": 1}, lambda c, d, t: {"company": c.root}, id="root-unchanged"),
    ],
)
def test_skill_dirs(tmp_path, edits, expected):
    """Every namespace to its store, as the library always mapped it, and
    each skill a child inherits under the child's name to the store of the
    ancestor that defines it. A child whose tree does not read maps to None,
    so its skills are refused as not loaded, never looked for in its own store."""
    edits = dict(edits)
    damage = edits.pop("damage", None)
    without = edits.pop("without", None)
    only = edits.pop("only", 3)
    tree = example_tree(tmp_path, **edits)
    if damage is not None:
        edit_store(tree[0].root, damage, "tasks: {}\n")
    given = [a for i, a in enumerate(tree[:only]) if i != without]
    dirs = inherit.skill_dirs(given)
    assert dirs == expected(*tree)
    assert not any(inherit.LAYER_MARK in key for key in dirs)


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        pytest.param(
            [("release@acme", None), ("tools@acme", None)],
            ["release@acme", "tools@acme"],
            id="roots-keep-order",
        ),
        pytest.param(
            [
                ("team@acme", "dept@acme"),
                ("company@acme", None),
                ("dept@acme", "company@acme"),
            ],
            ["company@acme", "dept@acme", "team@acme"],
            id="child-first",
        ),
        pytest.param(
            [("team@acme", "dept@acme"), ("tools@acme", None)],
            ["team@acme", "tools@acme"],
            id="parent-absent",
        ),
        pytest.param(
            [("a@acme", "b@acme"), ("b@acme", "a@acme"), ("root@acme", None)],
            ["root@acme", "a@acme", "b@acme"],
            id="cycle-ends",
        ),
    ],
)
def test_tree_order(tmp_path, links, expected):
    """Parents before children, and otherwise the order given."""
    assert [a.id for a in inherit.tree_order(_tree(tmp_path, *links))] == expected


@pytest.mark.parametrize(
    ("value", "found"),
    [
        ("team^1:scan", "team^1:scan"),
        ("team-payments^12:x", "team-payments^12:x"),
        ("^1:base", None),
        ("team^:x", None),
        ("team^1x:y", None),
        ("plain:name", None),
        ("-" * 50000, None),
    ],
    ids=["found", "hyphenated", "no-name", "no-depth", "junk-depth", "no-caret", "long-hyphens"],
)
def test_layer_reference(value, found):
    """A value in the layered shape is spotted, one that only resembles it is
    not, and a long run of `-` is answered at once (no backtracking)."""
    assert inherit.layer_reference({"extends": value}) == found
