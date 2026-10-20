"""`addons.update` over a tree of add-ons: the moves settle as a set, a move
a range holds waits, and each tree is reviewed and accepted as one (design
"Update moves a set")."""

import pytest
from support.addons import AGENT, chain, child_manifest, make_tree, publish

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
                COMPANY: "its chain team:check does not resolve",
                TEAM: "its chain team:check does not resolve",
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
    ],
)
def test_a_tree_moves_as_a_set(tmp_path, request, published, accept, outcomes, problems):
    row = request.node.callspec.id
    tree = {**TREE, TOOLS: TREE[COMPANY]} if row == "unrelated-keep-their-own-review" else TREE
    repos, home = make_tree(tmp_path, tree)
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
