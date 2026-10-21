"""`addons.update` installing a child whose ancestors are not installed: they
are pulled in under one review, and nothing of a half tree is taken (design
"Install pulls in missing ancestors")."""

import pytest
from support.addons import AGENT, chain, child_manifest, make_tree, publish

from kraft.addons.config import AddonEntry, AddonsLock
from kraft.config import read_yaml, write_yaml

from .test_update_tree import (
    COMPANY,
    NOT_INHERITED,
    TEAM,
    TREE,
    _company,
    _files,
    _move_together,
    _team,
    _update,
    _version,
)

DEPT = "dept@platform"
#: company 1.0.0, then dept 1.0.0 under it at range `1`, then team 1.0.0
#: under dept at `1`.
ANCESTRY = {
    COMPANY: TREE[COMPANY],
    DEPT: {"library": {"tasks": {"scan": {"extends": "company:verify"}}}, "parent": (COMPANY, "1")},
    TEAM: {"library": {"tasks": {"check": {"extends": "dept:scan"}}}, "parent": (DEPT, "1")},
}
PULLS_COMPANY = "pulls in company@corp 1.0.0 from collection corp: dept@platform builds on it"
PULLS_DEPT = "pulls in dept@platform 1.0.0 from collection platform: team@payments builds on it"
NO_CORP = (
    "its parent company@corp comes from collection corp, which this instance has not added; "
    "add it first: kraft admin addon collection add"
)
DISABLED = (
    "its parent dept@platform is not loaded: is disabled; enable it first: "
    "kraft admin addon enable dept@platform"
)
TAKEN = (
    "dept@platform installs under its name dept, which dept@other holds; install it first "
    "with its own --as: kraft admin addon install dept@platform --as ALIAS"
)
SUBPROCESS = {"verify": AGENT, "run": {"kind": "subprocess", "command": "make"}}
#: A row's tree, the ids installed and the collections not added, when not
#: `ANCESTRY` with nothing installed.
PULL_TREES = {
    "uses-installed-ancestor": (ANCESTRY, [COMPANY], ()),
    "missing-collection": (ANCESTRY, [], ["corp"]),
    "installed-ancestor-too-old": (
        {**ANCESTRY, DEPT: {**ANCESTRY[DEPT], "parent": (COMPANY, "2")}},
        [COMPANY],
        (),
    ),
    "ancestor-name-taken": (
        {**ANCESTRY, "dept@other": {"library": {"tasks": {"x": AGENT}}}},
        ["dept@other"],
        (),
    ),
    "ancestor-refused-writes-nothing": (
        {**ANCESTRY, COMPANY: {"library": {"tasks": SUBPROCESS}}},
        [],
        (),
    ),
    "re-install-moves-a-needed-ancestor": (TREE, None, ()),
    "re-install-keeps-ancestor": (TREE, None, ()),
}


@pytest.mark.parametrize(
    ("published", "entry", "outcomes", "problems"),
    [
        pytest.param(
            None,
            True,
            {TEAM: "applied", COMPANY: "applied", DEPT: "applied"},
            {},
            id="pulls-in-two",
        ),
        pytest.param(
            None, True, {TEAM: "applied", DEPT: "applied"}, {}, id="uses-installed-ancestor"
        ),
        pytest.param(
            None,
            True,
            {TEAM: "refused", DEPT: "refused"},
            {DEPT: NO_CORP, TEAM: f"its parent dept@platform cannot be installed: {NO_CORP}"},
            id="missing-collection",
        ),
        pytest.param(
            lambda repos: _company(repos, "2.0.0"),
            True,
            {TEAM: "refused", COMPANY: "refused", DEPT: "refused"},
            {
                DEPT: "company@corp 2.0.0 is outside dept@platform's range 1 (needs 1.x); point "
                "collection corp's ref at a 1.x tag, or wait for dept@platform to accept 2",
                TEAM: "its parent dept@platform cannot be installed: company@corp 2.0.0 is outside",
                COMPANY: "pulled in for dept@platform, which cannot be installed",
            },
            id="ancestor-too-new",
        ),
        pytest.param(
            None,
            True,
            {TEAM: "refused", DEPT: "refused"},
            {
                DEPT: "its parent company@corp is 1.0.0, outside its range 2 (needs 2.x)",
                TEAM: "its parent dept@platform cannot be installed: its parent company@corp is "
                "1.0.0, outside its range 2",
            },
            id="installed-ancestor-too-old",
        ),
        pytest.param(
            None,
            {"as": "tp"},
            {TEAM: "applied", COMPANY: "applied", DEPT: "applied"},
            {},
            id="as-names-only-the-child",
        ),
        pytest.param(
            None,
            {"auto_update": True},
            {TEAM: "applied", COMPANY: "applied", DEPT: "applied"},
            {},
            id="auto-update-only-the-child",
        ),
        pytest.param(
            None,
            True,
            {TEAM: "refused", DEPT: "refused"},
            {DEPT: TAKEN, TEAM: f"its parent dept@platform cannot be installed: {TAKEN}"},
            id="ancestor-name-taken",
        ),
        pytest.param(None, True, {TEAM: "refused"}, {TEAM: DISABLED}, id="ancestor-disabled"),
        pytest.param(
            None,
            True,
            {TEAM: "refused", COMPANY: "refused", DEPT: "refused"},
            {
                COMPANY: "an add-on may not carry a subprocess task",
                DEPT: "its parent company@corp cannot be installed: ",
                TEAM: "its parent dept@platform cannot be installed: its parent company@corp "
                "cannot be installed: ",
            },
            id="ancestor-refused-writes-nothing",
        ),
        pytest.param(
            None,
            True,
            {TEAM: "declined", COMPANY: "declined", DEPT: "declined"},
            {},
            id="pull-in-declined",
        ),
        pytest.param(
            _move_together,
            None,
            {TEAM: "applied", COMPANY: "applied"},
            {},
            id="re-install-moves-a-needed-ancestor",
        ),
        pytest.param(
            lambda repos: _company(repos, "1.1.0"),
            None,
            {TEAM: "applied"},
            {},
            id="re-install-keeps-ancestor",
        ),
    ],
)
def test_install_pulls_in_missing_ancestors(
    tmp_path, request, published, entry, outcomes, problems
):
    """Installing a child installs its missing ancestors with it, under one
    review and one accept; `--as` and `--auto-update` reach only the child,
    and `--re-install` moves an ancestor only when the child's range needs it
    (design "Install pulls in missing ancestors")."""
    row = request.node.callspec.id
    tree, installed, missing = PULL_TREES.get(row, (ANCESTRY, [], ()))
    repos, home = make_tree(tmp_path, tree, installed=installed, missing=missing)
    if published:
        published(repos)
    if row == "ancestor-disabled":
        # Listed but turned off, and never installed: it is not pulled in.
        config = read_yaml(home[0] / "addons.yaml")
        write_yaml(home[0] / "addons.yaml", {**config, "addons": {DEPT: {"enabled": False}}})
    before = _files(home)
    locked = AddonsLock.load(home[0] / "addons.lock").addons
    re_install = row.startswith("re-install-")
    entries = {TEAM: entry if entry is True else AddonEntry(**entry)} if entry else None

    results, groups = _update(
        home, [TEAM], accept=row != "pull-in-declined", entries=entries, re_install=re_install
    )

    assert {i: r.outcome for i, r in results.items()} == outcomes, results
    # The named id first, then the rest in tree order: `outcomes`' own.
    assert list(results) == list(outcomes), results
    for addon_id, said in problems.items():
        assert any(said in why for why in results[addon_id].problems), results[addon_id]
    if "applied" not in outcomes.values():
        assert _files(home) == before
    if not re_install and "applied" in outcomes.values():
        (group,) = groups
        assert group.title == ", ".join(i for i in ANCESTRY if i in outcomes)
        pulled = [PULLS_COMPANY, PULLS_DEPT] if COMPANY in outcomes else [PULLS_DEPT]
        assert list(group.pulled_in) == pulled
        written = read_yaml(home[0] / "addons.yaml")["addons"]
        assert {i: written[i] for i in outcomes} == {
            TEAM: entry,
            DEPT: True,
            **({COMPANY: True} if COMPANY in outcomes else {}),
        }
        assert AddonsLock.load(home[0] / "addons.lock").addons[DEPT].namespace == "dept"
    if row == "uses-installed-ancestor":
        assert AddonsLock.load(home[0] / "addons.lock").addons[COMPANY] == locked[COMPANY]
    if row == "re-install-moves-a-needed-ancestor":
        assert [g.title for g in groups] == ["company@corp, team@payments"]
        assert (_version(home, COMPANY), _version(home, TEAM)) == ("2.0.0", "1.1.0")
    if row == "re-install-keeps-ancestor":
        assert [g.title for g in groups] == [TEAM]
        assert AddonsLock.load(home[0] / "addons.lock").addons[COMPANY] == locked[COMPANY]


SQUAD = "squad@payments"
XX = "xx@corp"


def _relative_held_company(tmp_path):
    """company and xx (under it) installed; company 1.1.0 drops `gone`, which
    a local chain extends, for `extra`, which team (dept to be pulled in)
    removes. Named with xx, company's move joins, and judged again without
    the relatives it is held."""
    tree = {
        COMPANY: {"library": {"tasks": {"verify": AGENT, "gone": AGENT}}},
        XX: {"library": {"tasks": {"xt": AGENT}}, "parent": (COMPANY, "1")},
        DEPT: {"library": {"tasks": {"scan": AGENT}}, "parent": (COMPANY, "1")},
        TEAM: {
            "library": {"tasks": {"check": AGENT}, "remove": {"tasks": ["extra"]}},
            "parent": (DEPT, "1"),
        },
    }
    repos, home = make_tree(
        tmp_path, tree, installed=[COMPANY, XX], chains={"mine": chain("company:gone")}
    )
    _company(repos, "1.1.0", library={"tasks": {"verify": AGENT, "extra": AGENT}})
    return home


def _shared_dept(team_tasks):
    """company installed; team (with `team_tasks`) and squad both build on
    dept, which is not."""

    def setup(tmp_path):
        tree = {
            COMPANY: {"library": {"tasks": {"verify": AGENT}}},
            DEPT: {"library": {"tasks": {"scan": AGENT}}, "parent": (COMPANY, "1")},
            TEAM: {"library": {"tasks": team_tasks}, "parent": (DEPT, "1")},
            SQUAD: {"library": {"tasks": {"u": AGENT}}, "parent": (DEPT, "1")},
        }
        return make_tree(tmp_path, tree, installed=[COMPANY])[1]

    return setup


def _republished(company, version, range_, children=None, **team):
    """`TREE` (and `children`) installed; company publishes `company` (none
    when None), and team `version` at `range_` (with `team`)."""

    def setup(tmp_path):
        repos, home = make_tree(tmp_path, {**TREE, **(children or {})})
        if company:
            _company(repos, company)
        _team(repos, version, range_, **team)
        return home

    return setup


def _both_need_company(tmp_path):
    """`TREE` and xx (under company) installed; company publishes 1.6.0, and
    team and xx 1.1.0 both need it at `1.5`; team's carries a subprocess task."""
    repos, home = make_tree(tmp_path, {**TREE, XX: {**TREE[TEAM], "parent": (COMPANY, "1")}})
    _company(repos, "1.6.0")
    _team(repos, "1.1.0", "1.5", library={"tasks": SUBPROCESS})
    publish(repos["corp"], "xx", version="1.1.0", manifest=child_manifest(COMPANY, "1.5"))
    return home


def _parent_uninstalled(tmp_path):
    """team installed, and company taken out of `addons.yaml` and the lock;
    team 1.1.0 is published."""
    repos, home = make_tree(tmp_path, TREE)
    for name in ("addons.yaml", "addons.lock"):
        data = read_yaml(home[0] / name)
        del data["addons"][COMPANY]
        write_yaml(home[0] / name, data)
    _team(repos, "1.1.0", "1")
    return home


def _parent_update_refused(tmp_path):
    """company installed; its 1.1.0 carries a subprocess task."""
    repos, home = make_tree(tmp_path, TREE, installed=[COMPANY])
    _company(repos, "1.1.0", library={"tasks": SUBPROCESS})
    return home


SHARED = "pulls in dept@platform 1.0.0 from collection platform: "
NEEDED = "needed by team@payments, which cannot be re-installed"


@pytest.mark.parametrize(
    ("setup", "ids", "installs", "outcomes", "problems", "pulled_in"),
    [
        pytest.param(
            _relative_held_company,
            [TEAM, XX],
            [TEAM],
            {TEAM: "refused", XX: "current", COMPANY: "held", DEPT: "refused"},
            {
                TEAM: NOT_INHERITED.replace("company", "dept"),
                DEPT: "pulled in for team@payments, which cannot be installed",
            },
            [],
            id="recheck-without-relatives-refuses-the-pulled-in",
        ),
        pytest.param(
            _shared_dept({"t": {"kind": "subprocess", "command": "make"}}),
            [TEAM, SQUAD],
            [TEAM, SQUAD],
            {TEAM: "refused", SQUAD: "applied", DEPT: "applied"},
            {},
            [[SHARED + "squad@payments builds on it"]],
            id="shared-ancestor-names-a-builder-that-applies",
        ),
        pytest.param(
            _shared_dept({"t": AGENT}),
            [TEAM, SQUAD],
            [TEAM, SQUAD],
            {TEAM: "applied", SQUAD: "applied", DEPT: "applied"},
            {},
            [[SHARED + "team@payments, squad@payments build on it"]],
            id="shared-ancestor-names-every-builder",
        ),
        pytest.param(
            _republished(None, "1.1.0", "2"),
            [TEAM],
            [],
            {TEAM: "held"},
            {TEAM: "team@payments's range 2 does not accept company@corp 1.0.0 (needs 2.x)"},
            [],
            id="re-install-takes-no-ancestor-that-leaves-it-outside",
        ),
        pytest.param(
            _republished("1.6.0", "1.1.0", "1.5", library={"tasks": SUBPROCESS}),
            [TEAM],
            [],
            {TEAM: "refused", COMPANY: "held"},
            {TEAM: "an add-on may not carry a subprocess task", COMPANY: NEEDED},
            [],
            id="re-install-holds-the-ancestor-of-a-refused-child",
        ),
        pytest.param(
            _republished(
                "1.6.0",
                "2.0.0",
                "1.5",
                {SQUAD: {"library": {"tasks": {"u": AGENT}}, "parent": (TEAM, "1")}},
            ),
            [TEAM],
            [],
            {TEAM: "held", COMPANY: "held"},
            {COMPANY: NEEDED},
            [],
            id="re-install-holds-the-ancestor-of-a-held-child",
        ),
        pytest.param(
            _both_need_company,
            [TEAM, XX],
            [],
            {TEAM: "refused", XX: "applied", COMPANY: "applied"},
            {},
            [[]],
            id="re-install-moves-an-ancestor-another-child-needs",
        ),
        pytest.param(
            _parent_uninstalled,
            [TEAM],
            [],
            {TEAM: "refused"},
            {TEAM: "its parent company@corp is not installed; install it first"},
            [],
            id="update-pulls-in-nothing",
        ),
        pytest.param(
            _parent_update_refused,
            [COMPANY, TEAM],
            [TEAM],
            {COMPANY: "refused", TEAM: "applied"},
            {COMPANY: "an add-on may not carry a subprocess task"},
            [[]],
            id="install-under-a-refused-update-builds-on-the-locked-parent",
        ),
    ],
)
def test_no_half_tree_is_taken(
    tmp_path, request, setup, ids, installs, outcomes, problems, pulled_in
):
    """An ancestor an install pulls in, or a re-install needs, is taken only
    with a child that builds on it, and its `pulls in` line names only the
    children that apply. An update pulls nothing in, and an install under a
    locked parent whose move is refused builds on the locked side (design
    "Install pulls in missing ancestors")."""
    home = setup(tmp_path)
    before = _files(home)
    re_install = request.node.callspec.id.startswith("re-install-")

    results, groups = _update(
        home, ids, entries=dict.fromkeys(installs, True) or None, re_install=re_install
    )

    assert {i: r.outcome for i, r in results.items()} == outcomes, results
    for addon_id, said in problems.items():
        assert any(said in why for why in results[addon_id].problems), results[addon_id]
    assert [list(g.pulled_in) for g in groups] == pulled_in, groups
    if "applied" not in outcomes.values():
        assert _files(home) == before
    lock = AddonsLock.load(home[0] / "addons.lock").addons
    assert {i for i, o in outcomes.items() if o == "applied"} <= lock.keys(), lock
    assert not {i for i, o in outcomes.items() if o == "refused" and i in installs} & lock.keys()
