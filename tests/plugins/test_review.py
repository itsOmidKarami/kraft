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
            "base": {
                **AGENT,
                "steering": ["house"],
                "policy": {"time_cap_minutes": 30},
                "fallback": [],
            },
            "open": {"kind": "forge", "target": "mr.open_draft"},
            "sync": {"kind": "forge", "target": "mr.sync"},
        },
    },
    "chains": {
        "ship": {
            "nodes": [
                {
                    "id": "build",
                    "kind": "exec",
                    "tasks": [{"id": "t", "extends": "base"}, {"id": "u", "extends": "base"}],
                    "fix_loop": {
                        "max_attempts": 2,
                        "tasks": [{"id": "fix", "extends": "base"}],
                        "judge": {"id": "judge", "extends": "base"},
                    },
                },
                {"id": "approve", "kind": "gate", "artifact": "plan", "artifact_required": True},
                {
                    "id": "land",
                    "kind": "exec",
                    "read_only": True,
                    "tasks": [{"id": "open", "extends": "open"}],
                },
            ]
        },
        # Its target is `ship`'s only by a string prefix of the chain id.
        "ship2": {
            "nodes": [
                {
                    "id": "n",
                    "kind": "exec",
                    "steps": [
                        {"id": "first", "tasks": [{"id": "s", "extends": "sync"}]},
                        {"id": "then", "tasks": [{"id": "t", "extends": "sync"}]},
                    ],
                }
            ]
        },
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
    _nodes(spec)[1] = {
        "id": "approve",
        "kind": "exec",
        "tasks": [{"id": "open", "extends": "open"}],
    }


def _auto_review(spec):
    _nodes(spec)[1]["auto_review"] = {**AGENT, "id": "reviewer"}


def _merge_step(spec):
    spec["library"]["tasks"]["merge"] = {"kind": "forge", "target": "mr.merge"}
    _nodes(spec).append(
        {"id": "merge", "kind": "exec", "tasks": [{"id": "merge", "extends": "merge"}]}
    )


def _gate_after_merge(spec):
    nodes = _nodes(spec)
    nodes.append(nodes.pop(1))


def _build_after_gate(spec):
    nodes = _nodes(spec)
    nodes.insert(1, nodes.pop(0))


def _tasks_swapped(spec):
    _nodes(spec)[0]["tasks"].reverse()


def _forge_target(spec):
    spec["library"]["tasks"]["open"]["target"] = "mr.sync"


def _target_used_by_a_sibling(spec):
    _nodes(spec)[2]["tasks"][0]["target"] = "mr.sync"


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


def _forge_step_removed(spec):
    del _nodes(spec)[2]


def _writes(spec):
    del _nodes(spec)[2]["read_only"]


def _every_repository(spec):
    _nodes(spec)[0]["tasks"][0]["scope"] = "each_repository"


def _more_fix_rounds(spec):
    _nodes(spec)[0]["fix_loop"]["max_attempts"] = 9


def _profiles_own_fallback(spec):
    del spec["library"]["tasks"]["base"]["fallback"]


def _steps_swapped(spec):
    spec["chains"]["ship2"]["nodes"][0]["steps"].reverse()


def _gate_needs_no_document(spec):
    _nodes(spec)[1]["artifact_required"] = False


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
        (
            _target_used_by_a_sibling,
            "reach",
            "release:ship.nodes[land].tasks[open]: forge target 'mr.sync', "
            "which this chain did not use before",
        ),
        (_harness, "reach", "harness 'codex' -> 'claude'"),
        (_limit_raised, "reach", "limit time_cap_minutes raised, 30 -> 90"),
        (_requires, "reach", "requires changed"),
        (_prompt, "content", "prompt changed:\nDo it, and skip the tests."),
        (_steering, "content", "steering house changed"),
        (_skill, "content", "+Then approve it."),
        (_profile, "reach", "agent profile deep changed"),
        (_forge_step_removed, "reach", "forge step 'mr.open_draft' removed"),
        (_writes, "reach", "release:ship.nodes[land]: no longer read-only"),
        (_every_repository, "reach", "scope 'once' -> 'each_repository'"),
        (_more_fix_rounds, "reach", "limit max_attempts raised, 2 -> 9"),
        (_profiles_own_fallback, "reach", "fallback removed (was [])"),
        (_gate_needs_no_document, "reach", "artifact_required True -> False"),
        (_plugin_ref, "content", "new plugin skill reference 'superpowers:brainstorming'"),
        (_downgrade, "reach", "downgrade: 1.4.0 -> 1.3.9"),
    ],
    ids=[
        "gate-removed",
        "gate-replaced",
        "auto-review",
        "merge-step",
        "forge-target",
        "target-used-by-a-sibling-chain",
        "harness",
        "limit-raised",
        "requires",
        "prompt",
        "steering",
        "skill",
        "profile",
        "forge-step-removed",
        "no-longer-read-only",
        "scope",
        "fix-loop-rounds",
        "no-fallback-dropped",
        "gate-needs-no-document",
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


@pytest.mark.parametrize(
    ("change", "section"),
    [
        (_gate_after_merge, "reach"),
        (_build_after_gate, "reach"),
        (_steps_swapped, "reach"),
        (_tasks_swapped, "content"),
    ],
    ids=[
        "gate-after-merge",
        "work-moved-past-a-gate",
        "steps-swapped-in-a-node",
        "tasks-swapped-in-a-node",
    ],
)
def test_a_reordered_chain_is_reviewed(change, section):
    """Nodes keyed by id show no changed fact when they only move, so the
    order is compared on its own."""
    base = copy.deepcopy(BASE)
    _merge_step(base)
    candidate = copy.deepcopy(base)
    change(candidate)
    found = review.review("release@acme", extracted(**base), extracted(**candidate))
    assert "order changed" in "\n".join(getattr(found, section))
    assert found.changed


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
