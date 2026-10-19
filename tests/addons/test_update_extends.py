"""`addons.update` and local chains that extend an add-on's chain: an update
that breaks one is refused, and the review says what one will run."""

import pytest
from support.addons import AGENT, instance, make_collection, publish

from kraft.addons import update

ID = "release@acme"
N = {"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}
M = {**N, "id": "m"}
RELEASE = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": {"nodes": [N, M]}}}


def _update(home, *, install: bool = False):
    config_dir, addons_dir = home
    (result,) = update.update(
        [ID],
        accept=lambda review: True,
        entries={ID: True} if install else None,
        config_dir=config_dir,
        addons_root=addons_dir,
    )
    return result


@pytest.mark.parametrize(
    ("mine", "ship"),
    [
        ({"remove": ["m"]}, {"nodes": [N]}),
        ({"add": [{"after": "m", "node": {**N, "id": "x"}}]}, {"nodes": [N]}),
        ({"overrides": {"m.main.t": {"prompt": "p"}}}, {"nodes": [N]}),
        (
            {"add": [{"after": "n", "node": {**N, "id": "x"}}]},
            {"nodes": [N, M, {**N, "id": "x"}]},
        ),
    ],
    ids=["removed-node-gone", "anchor-gone", "override-path-gone", "added-id-taken"],
)
def test_an_update_that_breaks_an_extending_chain_is_refused(tmp_path, mine, ship):
    repo = make_collection(tmp_path, {"release": RELEASE})
    home = instance(
        tmp_path,
        {"acme": {"git": repo.as_uri()}},
        chains={"mine": {"extends": "release:ship", **mine}},
    )
    assert _update(home, install=True).outcome == "applied"
    publish(repo, "release", version="1.1.0", chains={"ship": ship})

    result = _update(home)

    assert result.outcome == "refused"
    assert any("chain mine would stop resolving" in why for why in result.problems), (
        result.problems
    )


def test_the_review_shows_what_an_extending_chain_will_run(tmp_path):
    repo = make_collection(tmp_path, {"release": RELEASE})
    home = instance(
        tmp_path, {"acme": {"git": repo.as_uri()}}, chains={"mine": {"extends": "release:ship"}}
    )
    _update(home, install=True)
    gated = {"nodes": [N, M, {"id": "ok", "kind": "gate", "reject_to": "n"}]}
    publish(repo, "release", version="1.1.0", chains={"ship": gated})

    result = _update(home)

    assert any(line.startswith("mine.nodes[ok]") for line in result.review.reach), result.review
