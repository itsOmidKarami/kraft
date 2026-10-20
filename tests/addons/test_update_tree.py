"""`addons.update` over a tree of add-ons: the moves settle as a set, a move
a range holds waits, and each tree is reviewed and accepted as one (design
"Update moves a set")."""

import pytest
from support.addons import AGENT, chain, child_manifest, install, make_tree, publish

from kraft.addons import update
from kraft.addons.config import AddonsLock

COMPANY = "company@corp"
TEAM = "team@payments"
TOOLS = "tools@corp"
#: company 1.0.0, with team 1.0.0 under it at range `1`, whose task and chain
#: are built on company's `verify`.
TREE = {
    COMPANY: {"library": {"tasks": {"verify": AGENT}}, "chains": {"default": chain("verify")}},
    TEAM: {
        "library": {"tasks": {"scan": {"extends": "company:verify"}}},
        "chains": {"check": chain("scan")},
        "parent": (COMPANY, "1"),
    },
}


def _update(home, ids=None, *, accept=True, entries=None, **kw):
    """`update` on `home`: results by id, and every group `accept` was asked."""
    groups = []

    def answer(group):
        groups.append(group)
        return accept

    results = update.update(
        ids, accept=answer, entries=entries, config_dir=home[0], addons_root=home[1], **kw
    )
    return {r.addon_id: r for r in results}, groups


def _files(home):
    """Both files `update` writes, and the store's entries."""
    files = [home[0] / "addons.yaml", home[0] / "addons.lock"]
    return [f.read_text() for f in files], sorted(p.name for p in (home[1] / "store").iterdir())


def _version(home, addon_id):
    return AddonsLock.load(home[0] / "addons.lock").addons[addon_id].version


def _company(repos, version, **spec):
    publish(repos["corp"], "company", version=version, **spec)


def _team(repos, version, range_, **spec):
    publish(
        repos["payments"], "team", version=version, manifest=child_manifest(COMPANY, range_), **spec
    )


R1 = "company@corp 2.0.0 is outside team@payments's range 1 (needs 1.x)"


def _move_together(repos):
    _company(repos, "2.0.0")
    _team(repos, "1.1.0", "2")


def _company_drops_verify(repos):
    _company(
        repos,
        "2.0.0",
        library={"tasks": {"checked": AGENT}},
        chains={"default": chain("checked")},
    )
    _team(repos, "1.1.0", "2")


def _company_adds_base(repos):
    _company(repos, "2.0.0", chains={"default": chain("verify"), "base": chain("verify")})
    _team(repos, "1.1.0", "2", chains={"check": chain("scan"), "ship": {"extends": "company:base"}})


def _unrelated(repos):
    _company(repos, "1.1.0")
    publish(repos["corp"], "tools", version="1.1.0")


def _unrelated_refused(repos):
    """tools 1.1.0 names company's namespace, and is refused; company's own
    plain release is not judged against it."""
    publish(repos["corp"], "tools", version="1.1.0", library={"tasks": {"t": _skilled("company")}})
    _company(repos, "1.1.0")


def _team_removes_extra(repos):
    """team 1.1.0, still at range `1`, drops `extra`, which only a newer
    company has."""
    _team(
        repos,
        "1.1.0",
        "1",
        library={"tasks": {"scan": {"extends": "company:verify"}}, "remove": {"tasks": ["extra"]}},
    )


def _held_parent_adds_extra(repos):
    _company(repos, "2.0.0", library={"tasks": {"verify": AGENT, "extra": AGENT}})
    _team_removes_extra(repos)


def _refused_parent_adds_extra(repos):
    _company(repos, "1.1.0", library={"tasks": {"verify": _skilled("tools"), "extra": AGENT}})
    _team_removes_extra(repos)


def _parent_needs_a_harness(repos, version="1.1.0"):
    _company(repos, version, manifest={"requires": {"kraft": "2", "harnesses": ["nosuch"]}})
    _team(repos, "1.1.0", "1")


def _team_takes_own_skill(repos):
    _company(repos, "2.0.0")
    _team(
        repos,
        "1.1.0",
        "2",
        library={"tasks": {"scan": {"extends": "company:verify", "skill": "x"}}},
        skills={"x": "---\nname: x\ndescription: x\n---\nbody\n"},
    )


def _skilled(namespace):
    """A task naming another add-on's `namespace`: refused."""
    return {**AGENT, "skill": f"{namespace}:x"}


NOT_INHERITED = "remove.tasks: extra is not inherited from company"
NOSUCH = "requires harness 'nosuch', which harnesses.yaml does not define"
#: The tree each row runs on, when it is not `TREE`.
TREES = {
    "unrelated-keep-their-own-review": {**TREE, TOOLS: TREE[COMPANY]},
    "unrelated-refusal-does-not-leak": {COMPANY: TREE[COMPANY], TOOLS: TREE[COMPANY]},
    "child-not-judged-against-refused-parent": {**TREE, TOOLS: TREE[COMPANY]},
}
#: Local chains a row's instance has.
LOCAL = {"tree-skill-of-a-later-member-is-its-own": {"mine": {"extends": "team:check"}}}


@pytest.mark.parametrize(
    ("published", "accept", "outcomes", "problems"),
    [
        pytest.param(
            _move_together, True, {COMPANY: "applied", TEAM: "applied"}, {}, id="move-together"
        ),
        pytest.param(
            lambda repos: _company(repos, "2.0.0"),
            True,
            {COMPANY: "held", TEAM: "current"},
            {COMPANY: R1},
            id="parent-held",
        ),
        pytest.param(
            lambda repos: (_company(repos, "2.0.0"), _team(repos, "1.1.0", "1")),
            True,
            {COMPANY: "held", TEAM: "applied"},
            {COMPANY: R1},
            id="child-applies-parent-held",
        ),
        pytest.param(
            lambda repos: _team(repos, "1.1.0", "2"),
            True,
            {COMPANY: "current", TEAM: "held"},
            {TEAM: "team@payments's range 2 does not accept company@corp 1.0.0 (needs 2.x)"},
            id="child-held",
        ),
        pytest.param(
            _move_together,
            False,
            {COMPANY: "declined", TEAM: "declined"},
            {},
            id="tree-declined-writes-nothing",
        ),
        pytest.param(
            _company_adds_base,
            True,
            {COMPANY: "applied", TEAM: "applied"},
            {},
            id="tree-review-uses-moved-parent",
        ),
        pytest.param(
            _company_drops_verify,
            True,
            {COMPANY: "refused", TEAM: "refused"},
            {
                COMPANY: "team:check does not resolve: team@payments/chains/check.yaml",
                TEAM: "team:check does not resolve: team@payments/chains/check.yaml",
            },
            id="tree-break-refuses-every-member",
        ),
        pytest.param(
            _unrelated,
            True,
            {COMPANY: "applied", TEAM: "current", TOOLS: "applied"},
            {},
            id="unrelated-keep-their-own-review",
        ),
        pytest.param(
            _unrelated_refused,
            True,
            {COMPANY: "applied", TOOLS: "refused"},
            {TOOLS: "skill 'company:x' names another installed Kraft add-on"},
            id="unrelated-refusal-does-not-leak",
        ),
        pytest.param(
            _held_parent_adds_extra,
            True,
            {COMPANY: "held", TEAM: "refused"},
            {COMPANY: R1, TEAM: NOT_INHERITED},
            id="child-not-judged-against-held-parent",
        ),
        pytest.param(
            _refused_parent_adds_extra,
            True,
            {COMPANY: "refused", TEAM: "refused", TOOLS: "current"},
            {COMPANY: "skill 'tools:x' names another installed Kraft add-on", TEAM: NOT_INHERITED},
            id="child-not-judged-against-refused-parent",
        ),
        pytest.param(
            _parent_needs_a_harness,
            True,
            {COMPANY: "refused", TEAM: "applied"},
            {COMPANY: NOSUCH},
            id="child-not-refused-for-parents-requirements",
        ),
        pytest.param(
            lambda repos: _parent_needs_a_harness(repos, "2.0.0"),
            True,
            {COMPANY: "refused", TEAM: "applied"},
            {COMPANY: NOSUCH},
            id="child-not-refused-for-held-parents-requirements",
        ),
        pytest.param(
            _team_takes_own_skill,
            True,
            {COMPANY: "applied", TEAM: "applied"},
            {},
            id="tree-skill-of-a-later-member-is-its-own",
        ),
    ],
)
def test_a_tree_moves_as_a_set(tmp_path, request, published, accept, outcomes, problems):
    row = request.node.callspec.id
    repos, home = make_tree(tmp_path, TREES.get(row, TREE), chains=LOCAL.get(row))
    published(repos)
    before = _files(home)

    results, groups = _update(home, accept=accept)

    assert {i: r.outcome for i, r in results.items()} == outcomes, results
    for addon_id, said in problems.items():
        assert any(said in why for why in results[addon_id].problems), results[addon_id]
    if "applied" not in outcomes.values():
        assert _files(home) == before
    if row in ("move-together", "tree-declined-writes-nothing"):
        assert [g.title for g in groups] == ["company@corp, team@payments"]
    if row == "move-together":
        assert (_version(home, COMPANY), _version(home, TEAM)) == ("2.0.0", "1.1.0")
    if row == "child-applies-parent-held":
        assert (_version(home, COMPANY), _version(home, TEAM)) == ("1.0.0", "1.1.0")
    if row == "tree-review-uses-moved-parent":
        team = next(r for g in groups for r in g.reviews if r.addon_id == TEAM)
        assert "team:ship.nodes[n]: added (exec)" in team.content, team
        assert not any("removed" in line for line in (*team.reach, *team.content)), team
    if row == "unrelated-keep-their-own-review":
        assert [g.title for g in groups] == [COMPANY, TOOLS]
    if row == "child-not-refused-for-parents-requirements":
        assert (_version(home, COMPANY), _version(home, TEAM)) == ("1.0.0", "1.1.0")
    if row == "tree-skill-of-a-later-member-is-its-own":
        # The tree's local lines go to company, but team's own skill is no
        # other add-on's.
        lines = [line for g in groups for r in g.reviews for line in r.content]
        assert "mine.nodes[n].tasks[t]: skill set to 'team:x'" in lines, lines
        assert not any("plugin skill" in line for line in lines), lines


@pytest.mark.parametrize(
    ("team_moves", "team"),
    [
        pytest.param(None, "current", id="child-stays"),
        pytest.param("1", "refused", id="child-move-refused"),
        pytest.param("2", "held", id="child-move-held"),
    ],
)
def test_a_parent_move_that_leaves_its_child_out_is_refused(tmp_path, team_moves, team):
    """company 1.1.0 builds on another root named team, so team@payments,
    under it, would no longer load. That team's own move (at range
    `team_moves`) is refused or held leaves it on its locked side, still left
    out."""
    other = {"library": {"tasks": {"t": AGENT}}}
    repos, home = make_tree(tmp_path, {**TREE, "team@tools": other}, installed=[COMPANY, TEAM])
    install(*home, repos["tools"], "team", alias="other")
    _company(repos, "1.1.0", manifest=child_manifest("team@tools", "1"))
    if team_moves:
        _team(repos, "1.1.0", team_moves)
    before = _files(home)

    results, _groups = _update(home)

    assert (results[COMPANY].outcome, results[TEAM].outcome) == ("refused", team), results
    assert results[COMPANY].problems == (
        "team@payments would no longer load: its ancestor team@tools has its name team",
    )
    assert _files(home) == before
