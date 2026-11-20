"""The review of an add-on update reads a forge task's keys in the `config:`
form, so moving them there is no change and a change keeps its wording."""

import copy

from support.addons import extracted

from addons.test_review import BASE, _reviewers_added
from kraft.addons import review

_FLAT = {"kind": "forge", "target": "mr.automated_review", "reviewers": [{"bot": "x"}]}


def test_moving_a_component_to_the_config_form_is_no_change():
    installed, candidate = copy.deepcopy(BASE), copy.deepcopy(BASE)
    installed["library"]["tasks"]["review"] = _FLAT
    candidate["library"]["tasks"]["review"] = {
        "kind": "forge",
        "target": "mr.automated_review",
        "config": {"reviewers": [{"bot": "x"}]},
    }
    found = review.review("release@acme", extracted(**installed), extracted(**candidate))
    assert not [line for line in (*found.reach, *found.content) if "tasks.review" in line]


def test_a_reviewers_change_keeps_its_wording():
    installed, candidate = copy.deepcopy(BASE), copy.deepcopy(BASE)
    _reviewers_added.was(installed)
    _reviewers_added(candidate)
    found = review.review("release@acme", extracted(**installed), extracted(**candidate))
    assert found.reach == (
        "release:ship.nodes[land].tasks[review]: reviewers set to [{'bot': 'x'}]",
    )
