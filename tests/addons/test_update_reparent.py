"""`addons.update` when a child changes parent: its old tree and its new one
are reviewed and accepted as one, and the moves left settle again after each
tree (design "Update moves a set")."""

import pytest
from support.addons import chain, child_manifest, make_tree, publish

from kraft.addons import update

from .test_update_tree import COMPANY, R1, TEAM, TREE, _company, _update, _version

OTHER = "other@corp"


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
            id="reparent-held-child-holds-the-old-parent",
        ),
    ],
)
def test_a_reparenting_child_moves_with_both_trees(
    tmp_path, published, ids, accept, outcomes, titles, versions
):
    """team 1.1.0 leaves company for other@corp while company moves to 2.0.0,
    outside team's old range: team's old tree and its new one are reviewed
    and accepted as one, so company 2 never applies without team leaving it."""
    repos, home = make_tree(tmp_path, {**TREE, OTHER: TREE[COMPANY]})
    published(repos)

    results, groups = _update(home, ids, accept=accept)

    assert {i: r.outcome for i, r in results.items()} == outcomes, results
    assert [g.title for g in groups] == titles
    assert (_version(home, COMPANY), _version(home, TEAM)) == versions
    if groups:
        team = next(r for r in groups[0].reviews if r.addon_id == TEAM)
        # company's group says team leaves it.
        assert f"parent changed: {COMPANY} 1 -> {OTHER} 1" in team.reach, team


@pytest.mark.parametrize(
    ("accept", "outcomes", "titles", "versions"),
    [
        pytest.param(
            _not_team,
            {TEAM: "declined", COMPANY: "held"},
            [TEAM],
            ("1.0.0", "1.0.0"),
            id="declined-partner-holds",
        ),
        pytest.param(
            True,
            {TEAM: "applied", COMPANY: "applied"},
            [TEAM, COMPANY],
            ("2.0.0", "1.1.0"),
            id="accepted-partner-is-its-new-side",
        ),
    ],
)
def test_the_moves_left_settle_again_after_each_tree(
    tmp_path, monkeypatch, accept, outcomes, titles, versions
):
    """The moves still to decide are settled again after each tree, over the
    sides the trees before left: were team's two trees grouped apart,
    declining team's holds company 2 rather than apply it over team's locked
    range, and accepting it lets company 2 follow."""
    trees = update.inherit.trees
    monkeypatch.setattr(update.inherit, "trees", lambda ids, parents, *_more: trees(ids, parents))
    repos, home = make_tree(tmp_path, {**TREE, OTHER: TREE[COMPANY]})
    _team_reparents(repos)

    results, groups = _update(home, [TEAM], accept=accept)

    assert {i: r.outcome for i, r in results.items()} == outcomes, results
    if outcomes[COMPANY] == "held":
        assert (results[COMPANY].problems, results[COMPANY].kind) == ((R1,), None)
    assert [g.title for g in groups] == titles
    assert (_version(home, COMPANY), _version(home, TEAM)) == versions
