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


_C, _D, _T = "company@corp", "dept@platform", "team@payments"
_S = inherit.Side

#: company 1.0.0 with team 1.0.0 under it at range 1; and with dept between.
_TWO = {_C: _S("1.0.0", None, None), _T: _S("1.0.0", _C, "1")}
_THREE = {_C: _S("1.0.0", None, None), _D: _S("1.0.0", _C, "1"), _T: _S("1.0.0", _D, "1")}

_CASCADE = {_C: _S("2.0.0", None, None), _D: _S("2.0.0", _C, "2")}
_CASCADE_DROPPED = {
    _D: f"{_D}'s range 2 does not accept {_C} 1.0.0 (needs 2.x)",
    _C: f"{_C} 2.0.0 is outside {_D}'s range 1 (needs 1.x)",
}
_C2_OUTSIDE_T = f"{_C} 2.0.0 is outside {_T}'s range 1 (needs 1.x)"


@pytest.mark.parametrize(
    ("locked", "moves", "dropped"),
    [
        pytest.param(_TWO, {}, {}, id="nothing-moves"),
        pytest.param(_TWO, {_C: _S("1.1.0", None, None)}, {}, id="compatible-parent-move"),
        pytest.param(
            _TWO, {_C: _S("2.0.0", None, None)}, {_C: _C2_OUTSIDE_T}, id="parent-held-by-child"
        ),
        pytest.param(
            _TWO,
            {_T: _S("1.1.0", _C, "2")},
            {_T: f"{_T}'s range 2 does not accept {_C} 1.0.0 (needs 2.x)"},
            id="child-held-by-its-range",
        ),
        pytest.param(
            _TWO,
            {_C: _S("2.0.0", None, None), _T: _S("1.1.0", _C, "2")},
            {},
            id="move-together",
        ),
        pytest.param(_THREE, _CASCADE, _CASCADE_DROPPED, id="cascade"),
        pytest.param(
            _TWO,
            {_C: _S("2.0.0", None, None), _T: _S("1.1.0", _C, "1")},
            {_C: _C2_OUTSIDE_T},
            id="child-keeps-old-range",
        ),
        pytest.param(
            _THREE,
            {
                _C: _S("2.0.0", None, None),
                _D: _S("1.1.0", None, None),
                _T: _S("1.1.0", _D, "2"),
            },
            {_T: f"{_T}'s range 2 does not accept {_D} 1.1.0 (needs 2.x)"},
            id="re-offer-second-round",
        ),
        pytest.param(
            {_T: _S("1.0.0", _C, "1")},
            {_C: _S("2.0.0", None, None), _D: _S("1.0.0", _C, "2")},
            {_C: _C2_OUTSIDE_T, _D: f"its parent {_C} cannot be installed: {_C2_OUTSIDE_T}"},
            id="parent-install-dropped",
        ),
        pytest.param(
            _TWO,
            {_T: _S("1.1.0", "other@corp", "1")},
            {_T: "its parent other@corp is not installed"},
            id="missing-parent",
        ),
        pytest.param(
            dict(reversed(_THREE.items())),
            dict(reversed(_CASCADE.items())),
            _CASCADE_DROPPED,
            id="order-independent",
        ),
        pytest.param(
            {_C: _S("1.0.0", None, None), _T: _S("1.0.0", _C, None)},
            {_T: _S("1.1.0", _C, "1"), _C: _S("2.0.0", None, None)},
            {_T: f"{_T}'s range 1 does not accept {_C} 2.0.0 (needs 1.x)"},
            id="re-offer-parents-first",
        ),
        pytest.param(
            {_T: _S("1.0.0", None, None)},
            {_T: _S("1.1.0", _C, "1"), _C: _S("1.0.0", "root@corp", "1")},
            {
                _T: f"its parent {_C} cannot be installed: its parent root@corp is not installed",
                _C: "its parent root@corp is not installed",
            },
            id="parent-install-dropped-after-its-child",
        ),
        pytest.param(
            {_C: _S("1.0.0", None, None), "app@corp": _S("1.0.0", _C, None)},
            {_C: _S("2.0.0", None, None), "app@corp": _S("1.1.0", _C, "1")},
            {"app@corp": f"app@corp's range 1 does not accept {_C} 2.0.0 (needs 1.x)"},
            id="re-offer-by-depth-not-id",
        ),
        pytest.param(
            {"app@corp": _S("1.0.0", None, None), "base@corp": _S("2.0.0", None, None)},
            {"app@corp": _S("2.0.0", "base@corp", "2"), "base@corp": _S("1.0.0", None, None)},
            {"base@corp": "base@corp 1.0.0 is outside app@corp's range 2 (needs 2.x)"},
            id="re-offer-depth-is-the-locked-one",
        ),
        pytest.param(
            {
                _C: _S("1.0.0", None, None),
                "app@corp": _S("1.0.0", _C, "1"),
                "web@corp": _S("1.0.0", _C, "1"),
            },
            {_C: _S("2.0.0", None, None)},
            {_C: f"{_C} 2.0.0 is outside app@corp's range 1 (needs 1.x)"},
            id="reason-names-the-first-child-by-id",
        ),
        pytest.param(
            {},
            {_C: _S("1.0.0", _T, "2"), _T: _S("1.0.0", _C, "2")},
            {
                _C: f"its parent {_T} cannot be installed: its parent {_C} is not installed",
                _T: f"its parent {_C} is not installed",
            },
            id="parent-installs-in-a-cycle",
        ),
        pytest.param(
            _TWO,
            {_T: _S("1.1.0", _C, "x")},
            {
                _T: f"{_T}'s range x does not accept {_C} 1.0.0 "
                "(invalid literal for int() with base 10: 'x')"
            },
            id="unparsable-range-breaks",
        ),
    ],
)
def test_settle(locked, moves, dropped):
    """Moves that break a range, judged with the others applied, are dropped
    until none breaks; each dropped one is offered again, parents first, and
    kept when it breaks nothing. Each still dropped says what holds it against
    the set that applies."""
    assert inherit.settle(locked, moves) == dropped


@pytest.mark.parametrize(
    ("locked", "kept", "named", "expected"),
    [
        pytest.param(
            _TWO,
            {_C: _S("1.1.0", None, None), _T: _S("1.1.0", _C, "1")},
            [_C],
            {_C},
            id="named-alone",
        ),
        pytest.param(
            _TWO,
            {_C: _S("2.0.0", None, None), _T: _S("1.1.0", _C, "2")},
            [_C],
            {_C, _T},
            id="needs-its-child",
        ),
        pytest.param(
            {},
            {_C: _S("1.0.0", None, None), _T: _S("1.0.0", _C, "1")},
            [_T],
            {_C, _T},
            id="needs-its-parent-install",
        ),
        pytest.param(
            _THREE,
            {**_CASCADE, _T: _S("1.1.0", _D, "2")},
            [_T],
            {_C, _D, _T},
            id="transitive",
        ),
    ],
)
def test_needed(locked, kept, named, expected):
    """The named moves and every kept move they cannot apply without."""
    assert inherit.needed(named, locked, kept) == expected


_CHAIN = dict(_levels(10))
_PARENTS = {_C: None, _D: _C, _T: _D, "tools@acme": None, "a@corp": _C, "b@corp": _C}


@pytest.mark.parametrize(
    ("ids", "parents", "expected"),
    [
        pytest.param([_T, _C], {_C: None, _T: _C}, [(_C, _T)], id="one-tree"),
        pytest.param(
            ["tools@acme", _T, _C], _PARENTS, [("tools@acme",), (_C, _T)], id="first-appearance"
        ),
        pytest.param(
            ["a@corp", "m@corp", "z@corp"],
            {"z@corp": None, "m@corp": "z@corp", "a@corp": "m@corp"},
            [("z@corp", "m@corp", "a@corp")],
            id="depth-order",
        ),
        pytest.param(
            ["b@corp", "a@corp", _C], _PARENTS, [(_C, "a@corp", "b@corp")], id="siblings-by-id"
        ),
        pytest.param([_T, _C], _PARENTS, [(_C, _T)], id="through-a-still-member"),
        pytest.param(
            ["w@corp", "x@corp", "y@corp", "z@corp"],
            {"w@corp": "x@corp", "x@corp": "y@corp", "y@corp": "x@corp", "z@corp": None},
            [("w@corp",), ("x@corp",), ("y@corp",), ("z@corp",)],
            id="cycle-members-alone",
        ),
        pytest.param(
            ["l1@acme", "l9@acme"], _CHAIN, [("l9@acme", "l1@acme")], id="deepest-allowed-chain"
        ),
        pytest.param(
            ["l0@acme", "l9@acme"], _CHAIN, [("l0@acme",), ("l9@acme",)], id="chain-too-deep"
        ),
        pytest.param(
            ["tools@acme", _C, "a@corp"],
            ({**_PARENTS, "a@corp": "tools@acme"}, _PARENTS),
            [(_C, "tools@acme", "a@corp")],
            id="reparented-joins-both-trees",
        ),
        pytest.param(
            ["x@corp", "a@corp", "b@corp", "y@corp"],
            ({"a@corp": "s@corp", "b@corp": "t@corp"}, {"a@corp": "r@corp", "b@corp": "s@corp"}),
            [("x@corp",), ("a@corp", "b@corp"), ("y@corp",)],
            id="joined-through-a-shared-root",
        ),
    ],
)
def test_trees(ids, parents, expected):
    """Ids grouped by the root they reach, through add-ons not among them too;
    with several maps (a tuple), by the roots they reach through any."""
    maps = parents if isinstance(parents, tuple) else (parents,)
    assert inherit.trees(ids, *maps) == expected
