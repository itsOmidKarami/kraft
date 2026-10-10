"""The review of an install or update: what widens a run's reach comes
first, and nothing in it can hide text from the person reading it."""

import copy

import pytest
from support.plugins import AGENT, extracted

from kraft.plugins import review

BASE = {
    "library": {
        "steering": {"house": {"instructions": "Be careful."}},
        "tasks": {
            "base": {**AGENT, "steering": ["house"], "policy": {"time_cap_minutes": 30}},
            "open": {"kind": "forge", "target": "mr.open_draft"},
        },
    },
    "chains": {
        "ship": {
            "nodes": [
                {"id": "build", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]},
                {"id": "approve", "kind": "gate"},
                {"id": "land", "kind": "exec", "tasks": [{"id": "open", "extends": "open"}]},
            ]
        }
    },
    "skills": {"deploy-review": "Check the rollout.\n"},
    "profiles": {"deep": {"model": {"codex": "m1"}}},
    "manifest_fields": {"version": "1.4.0", "requires": {"kraft": "2", "harnesses": ["codex"]}},
}


def _nodes(spec):
    return spec["chains"]["ship"]["nodes"]


def _gate_removed(spec):
    del _nodes(spec)[1]


def _gate_replaced(spec):
    _nodes(spec)[1].update(kind="exec", tasks=[{"id": "open", "extends": "open"}])


def _auto_review(spec):
    _nodes(spec)[1]["auto_review"] = {**AGENT, "id": "reviewer"}


def _merge_step(spec):
    spec["library"]["tasks"]["merge"] = {"kind": "forge", "target": "mr.merge"}
    _nodes(spec).append(
        {"id": "merge", "kind": "exec", "tasks": [{"id": "merge", "extends": "merge"}]}
    )


def _forge_target(spec):
    spec["library"]["tasks"]["open"]["target"] = "mr.sync"


def _harness(spec):
    spec["library"]["tasks"]["base"]["harness"] = "claude"


def _limit_raised(spec):
    spec["library"]["tasks"]["base"]["policy"]["time_cap_minutes"] = 90


def _requires(spec):
    spec["manifest_fields"]["requires"]["profiles"] = ["strong"]


def _prompt(spec):
    spec["library"]["tasks"]["base"]["prompt"] = "Do it, and skip the tests."


def _steering(spec):
    spec["library"]["steering"]["house"]["instructions"] = "Be quick."


def _skill(spec):
    spec["skills"]["deploy-review"] = "Check the rollout.\nThen approve it.\n"


def _profile(spec):
    spec["profiles"]["deep"]["model"]["codex"] = "m2"


def _plugin_ref(spec):
    spec["library"]["tasks"]["base"]["skill"] = "superpowers:brainstorming"


def _downgrade(spec):
    spec["manifest_fields"]["version"] = "1.3.9"


@pytest.mark.parametrize(
    ("change", "section", "says"),
    [
        (_gate_removed, "reach", "release:ship.nodes[approve]: gate removed"),
        (_gate_replaced, "reach", "release:ship.nodes[approve]: gate removed, now exec"),
        (_auto_review, "reach", "a gate's own agent review changed"),
        (_merge_step, "reach", "mr.merge step set to 'mr.merge'; a gate comes before it"),
        (_forge_target, "reach", "forge target 'mr.sync', which this chain did not use before"),
        (_harness, "reach", "harness 'codex' -> 'claude'"),
        (_limit_raised, "reach", "limit time_cap_minutes raised, 30 -> 90"),
        (_requires, "reach", "requires changed"),
        (_prompt, "content", "prompt changed:\nDo it, and skip the tests."),
        (_steering, "content", "steering house changed"),
        (_skill, "content", "+Then approve it."),
        (_profile, "content", "agent profile deep changed"),
        (_plugin_ref, "content", "new plugin skill reference 'superpowers:brainstorming'"),
        (_downgrade, "reach", "downgrade: 1.4.0 -> 1.3.9"),
    ],
    ids=[
        "gate-removed",
        "gate-replaced",
        "auto-review",
        "merge-step",
        "forge-target",
        "harness",
        "limit-raised",
        "requires",
        "prompt",
        "steering",
        "skill",
        "profile",
        "plugin-ref",
        "downgrade",
    ],
)
def test_review_calls_out(change, section, says):
    candidate = copy.deepcopy(BASE)
    change(candidate)
    found = review.review("release@acme", extracted(**BASE), extracted(**candidate))
    assert says in "\n".join(getattr(found, section))
    # A change to what a run does is never filed under the wrong heading.
    other = found.content if section == "reach" else found.reach
    assert says not in "\n".join(other)


def test_a_merge_with_no_gate_before_it_says_so():
    candidate = copy.deepcopy(BASE)
    _merge_step(candidate)
    _gate_removed(candidate)
    found = review.review("release@acme", extracted(**BASE), extracted(**candidate))
    assert "NO gate comes before it" in "\n".join(found.reach)


def test_an_install_reviews_everything():
    found = review.review("release@acme", None, extracted(**BASE))
    assert found.old_version is None and found.changed
    text = review.render(found)
    assert "release@acme new, 1.4.0" in text
    assert "harness set to 'codex'" in text and "prompt new:" in text
    assert "limit time_cap_minutes set" in text and "skill deploy-review changed" in text


def test_an_unchanged_plugin_is_up_to_date():
    found = review.review("release@acme", extracted(**BASE), extracted(**BASE))
    assert (found.changed, found.reach, found.content) == (False, (), ())
    assert "up to date" in review.render(found)


def test_review_escapes_what_it_prints():
    """A prompt that reorders or hides its own text on a terminal is printed
    with those characters spelled out."""
    candidate = copy.deepcopy(BASE)
    candidate["library"]["tasks"]["base"]["prompt"] = "approve\u202e ton od\u200b\x1b[2K"
    text = review.render(review.review("release@acme", extracted(**BASE), extracted(**candidate)))
    assert "\\u{202E}" in text and "\\u{200B}" in text and "\\u{001B}" in text
    assert not any(ch in text for ch in "\u202e\u200b\x1b")


def test_versions_sort_by_semver_precedence():
    ordered = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
        "1.0.1",
        "1.10.0",
        "2.0.0",
    ]
    assert sorted(reversed(ordered), key=review.precedence) == ordered
