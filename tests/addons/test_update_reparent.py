"""`addons.update` when a child changes parent: its old parent's tree, when
that parent moves too, and its new one are reviewed and accepted as one
(design "Update moves a set")."""

import pytest
from support.addons import chain, child_manifest, make_tree, publish

from .test_update_tree import COMPANY, TEAM, TREE, _company, _update, _version

OTHER = "other@corp"
DEPT = "dept@corp"
X = "x@payments"
#: The tree each row runs on, when it is not `TREE` with `OTHER` beside company.
TREES = {
    "dept-stays-its-own-group": {
        **TREE,
        OTHER: TREE[COMPANY],
        DEPT: TREE[TEAM],
        X: {
            "library": {"tasks": {"scan": {"extends": "other:verify"}}},
            "chains": {"check": chain("scan")},
            "parent": (OTHER, "1"),
        },
    }
}


def _team_reparents(repos, chains=None):
    """company 2.0.0, and team 1.1.0 built on other@corp instead: company 2
    is fine only because team leaves it."""
    _company(repos, "2.0.0")
    publish(
        repos["payments"],
        "team",
        version="1.1.0",
        manifest=child_manifest(OTHER, "1"),
        library={"tasks": {"scan": {"extends": "other:verify"}}},
        chains=chains or {"check": chain("scan")},
    )


def _dept_and_broken_x(repos):
    """team leaves company, which does not move, for other@corp, under which
    x 1.1.0 breaks a chain; dept 1.1.0, still under company, is harmless."""
    publish(
        repos["payments"],
        "team",
        version="1.1.0",
        manifest=child_manifest(OTHER, "1"),
        library={"tasks": {"scan": {"extends": "other:verify"}}},
        chains={"check": chain("scan")},
    )
    publish(repos["corp"], "dept", version="1.1.0", manifest=child_manifest(COMPANY, "1"))
    publish(
        repos["payments"],
        "x",
        version="1.1.0",
        manifest=child_manifest(OTHER, "1"),
        chains={"check": chain("nosuch")},
    )


def _not_team(group):
    """Declines a group with team in it."""
    return TEAM not in {r.addon_id for r in group.reviews}


@pytest.mark.parametrize(
    ("published", "ids", "accept", "outcomes", "titles", "versions"),
    [
        pytest.param(
            _team_reparents,
            None,
            True,
            {COMPANY: "applied", TEAM: "applied", OTHER: "current"},
            ["company@corp, team@payments"],
            ("2.0.0", "1.1.0"),
            id="reparent-both-apply",
        ),
        pytest.param(
            _team_reparents,
            None,
            _not_team,
            {COMPANY: "declined", TEAM: "declined", OTHER: "current"},
            ["company@corp, team@payments"],
            ("1.0.0", "1.0.0"),
            id="reparent-decline-keeps-old-parent",
        ),
        pytest.param(
            lambda repos: _team_reparents(repos, {"check": chain("nosuch")}),
            [COMPANY],
            True,
            {COMPANY: "refused", TEAM: "refused"},
            [],
            ("1.0.0", "1.0.0"),
            id="reparent-broken-child-refuses-the-old-parent",
        ),
        pytest.param(
            _dept_and_broken_x,
            None,
            _not_team,
            {
                COMPANY: "current",
                DEPT: "applied",
                OTHER: "current",
                TEAM: "refused",
                X: "refused",
            },
            [DEPT],
            ("1.0.0", "1.0.0"),
            id="dept-stays-its-own-group",
        ),
    ],
)
def test_a_reparenting_child_moves_with_both_trees(
    tmp_path, request, published, ids, accept, outcomes, titles, versions
):
    """team 1.1.0 leaves company for other@corp while company moves to 2.0.0,
    outside team's old range: team's old tree and its new one are reviewed
    and accepted as one, so company 2 never applies without team leaving it.
    An old parent that does not move joins nothing: dept, its other child,
    keeps its own group."""
    tree = TREES.get(request.node.callspec.id, {**TREE, OTHER: TREE[COMPANY]})
    repos, home = make_tree(tmp_path, tree)
    published(repos)

    results, groups = _update(home, ids, accept=accept)

    assert {i: r.outcome for i, r in results.items()} == outcomes, results
    assert [g.title for g in groups] == titles
    assert (_version(home, COMPANY), _version(home, TEAM)) == versions
    for team in [r for g in groups for r in g.reviews if r.addon_id == TEAM]:
        # company's group says team leaves it.
        assert f"parent changed: {COMPANY} 1 -> {OTHER} 1" in team.reach, team
