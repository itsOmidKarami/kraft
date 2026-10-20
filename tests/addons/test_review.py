"""The review of an install or update: what widens a run's reach comes
first, and nothing in it can hide text from the person reading it."""

import copy
from dataclasses import replace

import pytest
from support.addons import AGENT, chain, extracted, installed

from kraft.addons import review

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


def _parent_range(spec):
    spec["manifest_fields"]["requires"]["addons"] = {"company@corp": "3.1"}


def _parent_range_was(spec):
    spec["manifest_fields"]["requires"]["addons"] = {"company@corp": "3.0"}


#: A change whose installed side differs from `BASE` too names it here.
_parent_range.was = _parent_range_was


def _parent_changed(spec):
    spec["manifest_fields"]["requires"]["addons"] = {"other@corp": "2"}


_parent_changed.was = _parent_range_was


def _parent_gained(spec):
    spec["manifest_fields"]["requires"]["addons"] = {"company@corp": "3.0"}


def _parent_dropped(spec):
    spec["manifest_fields"]["requires"].pop("addons", None)


_parent_dropped.was = _parent_range_was


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
        (_parent_range, "reach", "parent range: company@corp 3.0 -> 3.1"),
        (_parent_changed, "reach", "parent changed: company@corp 3.0 -> other@corp 2"),
        (_parent_gained, "reach", "now builds on company@corp 3.0"),
        (_parent_dropped, "reach", "no longer builds on company@corp"),
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
        "parent-range",
        "parent-changed",
        "parent-gained",
        "parent-dropped",
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
    installed, candidate = copy.deepcopy(BASE), copy.deepcopy(BASE)
    getattr(change, "was", lambda spec: None)(installed)
    change(candidate)
    found = review.review("release@acme", extracted(**installed), extracted(**candidate))
    assert says in "\n".join(getattr(found, section))
    # A change to what a run does is never filed under the wrong heading.
    other = found.content if section == "reach" else found.reach
    assert says not in "\n".join(other)


@pytest.mark.parametrize(
    "change",
    [_parent_range, _parent_changed, _parent_gained, _parent_dropped],
    ids=["parent-range", "parent-changed", "parent-gained", "parent-dropped"],
)
def test_a_parent_is_not_a_plain_requirement(change):
    """The `addons` entry of `requires` is said as a parent, not as a change
    of what the add-on requires of the instance."""
    installed, candidate = copy.deepcopy(BASE), copy.deepcopy(BASE)
    getattr(change, "was", lambda spec: None)(installed)
    change(candidate)
    found = review.review("release@acme", extracted(**installed), extracted(**candidate))
    assert "requires changed" not in "\n".join(found.reach)


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


def _child_gate_added(spec):
    _nodes(spec).append({"id": "approve", "kind": "gate"})


@pytest.mark.parametrize(
    ("change", "says"),
    [(_child_gate_added, "release:ship.nodes[approve]: added (gate)")],
    ids=["child-gate-added-is-reviewed"],
)
def test_a_childs_chains_are_reviewed_with_its_ancestors(tmp_path, change, says):
    """A child's chain resolves only through its parent: each side is read
    with that side's ancestors, so what changes in it is said."""
    parent = installed(
        tmp_path, "company", library={"tasks": {"base": AGENT}}, chains={"hotfix": chain("base")}
    )
    requires = {"kraft": "2", "harnesses": ["codex"], "addons": {parent.id: "1"}}
    child = {
        "manifest_fields": {"requires": requires},
        # Only a child may drop what it inherits: read as a root, it is refused.
        "library": {"remove": {"chains": ["hotfix"]}},
        "chains": {"ship": chain("company:base")},
    }
    candidate = copy.deepcopy(child)
    change(candidate)
    found = review.review(
        "release@payments",
        extracted(**child),
        extracted(**candidate),
        old_ancestors=[parent],
        new_ancestors=[parent],
    )
    assert says in found.content, found


_BASE_TASKS = {"tasks": {"base": AGENT}}
_PCI = {"tasks": {"base": AGENT, "pci_review": AGENT}}
_GATED = {"nodes": [*chain("base")["nodes"], {"id": "approve", "kind": "gate"}]}
_EXTRA = {"id": "extra", "kind": "exec", "tasks": [{"id": "x", "extends": "base"}]}
_SHIP = {"extends": "company:base"}
_STEP = {"tasks": [{"id": "run", "extends": "base"}]}
_DEEP = {"deep": {"model": {"codex": "m1"}}}
_AUDIT = {"audit": {"instructions": "Audit."}}


def _own(child, **sections):
    """`child` (extracted keyword arguments) with its own `sections` added."""
    return {**child, **sections}


def _shadow(kind, name, owner="company@acme"):
    return f"its {kind} {name} now overrides {owner}'s new {kind} {name}"


def _tree(tmp_path, company, grandchild):
    """`(the add-on the child builds on, its ancestors parent first)`."""
    top = installed(tmp_path, "company", **company)
    if not grandchild:
        return top, [top]
    mid = installed(tmp_path, "team", library=_BASE_TASKS)
    mid = replace(mid, parent=top.id, parent_range="1")
    return mid, [mid, top]


def _reviewed(tmp_path, was, now, child, child_now, grandchild):
    old_parent, old_ancestors = _tree(tmp_path, was, grandchild)
    _new_parent, new_ancestors = _tree(tmp_path, now, grandchild)
    fields = {
        "manifest_fields": {
            "requires": {"kraft": "2", "harnesses": ["codex"], "addons": {old_parent.id: "1"}}
        }
    }
    old, new = _own(child, **fields), _own(child_now or child, **fields)
    return review.review(
        "release@payments",
        extracted(**old),
        extracted(**new),
        old_ancestors=old_ancestors,
        new_ancestors=new_ancestors,
    )


@pytest.mark.parametrize(
    ("was", "now", "child", "child_now", "grandchild", "section", "says", "lacks"),
    [
        pytest.param(
            {"library": _BASE_TASKS, "chains": {"default": _GATED}},
            {"library": _BASE_TASKS, "chains": {"default": chain("base")}},
            {},
            None,
            False,
            "reach",
            "release:default.nodes[approve]: gate removed",
            None,
            id="inherited-chain-change",
        ),
        pytest.param(
            {"library": _BASE_TASKS, "chains": {"base": chain("base")}},
            {"library": _BASE_TASKS, "chains": {"base": chain("base")}},
            {"chains": {"ship": _SHIP}},
            {"chains": {"ship": {**_SHIP, "add": [{"after": "n", "node": _EXTRA}]}}},
            False,
            "content",
            "release:ship.nodes[extra]: added (exec)",
            "removed",  # adding a node is not also a removal
            id="child-extends-parent-chain",
        ),
        pytest.param(
            {"library": _BASE_TASKS, "chains": {"hotfix": chain("base")}},
            {"library": _BASE_TASKS},
            {"library": {"remove": {"chains": ["hotfix"]}}},
            None,
            False,
            "content",
            "warning: release: remove.chains: hotfix is not inherited from company; "
            "it does nothing",
            None,
            id="stale-remove",
        ),
        pytest.param(
            {"library": _BASE_TASKS},
            {"library": _PCI},
            {"library": {"tasks": {"pci_review": AGENT}}},
            None,
            False,
            "reach",
            _shadow("task", "pci_review"),
            None,
            id="shadow-task",
        ),
        pytest.param(
            {"library": _BASE_TASKS},
            {"library": {**_BASE_TASKS, "steps": {"verify": _STEP}}},
            {"library": {"steps": {"verify": _STEP}}},
            None,
            False,
            "reach",
            _shadow("step", "verify"),
            None,
            id="shadow-step",
        ),
        pytest.param(
            {"library": _BASE_TASKS},
            {"library": {**_BASE_TASKS, "nodes": {"approve": {"kind": "gate"}}}},
            {"library": {"nodes": {"approve": {"kind": "gate"}}}},
            None,
            False,
            "reach",
            _shadow("node", "approve"),
            None,
            id="shadow-node",
        ),
        pytest.param(
            {"library": _BASE_TASKS},
            {"library": {**_BASE_TASKS, "steering": _AUDIT}},
            {"library": {"steering": _AUDIT}},
            None,
            False,
            "reach",
            _shadow("steering profile", "audit"),
            None,
            id="shadow-steering",
        ),
        pytest.param(
            {"library": _BASE_TASKS},
            {"library": _BASE_TASKS, "chains": {"hotfix": chain("base")}},
            {"chains": {"hotfix": chain("company:base")}},
            None,
            False,
            "reach",
            _shadow("chain", "hotfix"),
            None,
            id="shadow-chain",
        ),
        pytest.param(
            {"library": _BASE_TASKS},
            {"library": _BASE_TASKS, "skills": {"deploy-review": "Theirs.\n"}},
            {"skills": {"deploy-review": "Ours.\n"}},
            None,
            False,
            "reach",
            _shadow("skill", "deploy-review"),
            None,
            id="shadow-skill",
        ),
        pytest.param(
            {"library": _BASE_TASKS},
            {"library": _BASE_TASKS, "profiles": _DEEP},
            {"profiles": _DEEP},
            None,
            False,
            "reach",
            _shadow("agent profile", "deep"),
            None,
            id="shadow-profile",
        ),
        pytest.param(
            {"library": _BASE_TASKS},
            {"library": _PCI},
            {"library": {"tasks": {"pci_review": AGENT}}},
            None,
            True,
            "reach",
            _shadow("task", "pci_review"),
            None,
            id="shadow-from-grandparent",
        ),
        pytest.param(
            {"library": _PCI},
            {"library": _PCI},
            {"library": {"tasks": {"pci_review": AGENT}}},
            None,
            False,
            "reach",
            "",
            "now overrides",
            id="already-inherited-is-no-shadow",
        ),
        pytest.param(
            {"library": _BASE_TASKS},
            {"library": _BASE_TASKS},
            {"library": {"remove": {"chains": ["hotfix"]}}},
            None,
            False,
            "content",
            "",
            "warning:",
            id="stale-remove-already-stale",
        ),
    ],
)
def test_review_with_ancestors(
    tmp_path, was, now, child, child_now, grandchild, section, says, lacks
):
    """A child is reviewed together with its ancestors: what changes in what
    it inherits, a stale `remove:`, and a new ancestor name it already
    declares (which its own now wins over) are all said."""
    found = _reviewed(tmp_path, was, now, child, child_now, grandchild)
    lines = "\n".join(getattr(found, section))
    assert says in lines, found
    if lacks is not None:
        assert lacks not in lines, found


def test_a_child_that_removes_and_declares_a_name_still_shadows(tmp_path):
    """Pinned as the code stands: the shadow rule looks only at what the
    child declares against what the parent's library newly offers, so a
    `remove:` of the same name does not silence it."""
    child = {"library": {"remove": {"tasks": ["pci_review"]}, "tasks": {"pci_review": AGENT}}}
    found = _reviewed(
        tmp_path, {"library": _BASE_TASKS}, {"library": _PCI}, child, None, grandchild=False
    )
    assert _shadow("task", "pci_review") in found.reach, found


def test_a_child_that_gains_a_parent_shadows_each_name_it_shares(tmp_path):
    """Before, no ancestor offered anything; every name the child declares
    that the new parent has is a shadow, said beside the new parent."""
    parent = installed(
        tmp_path,
        "company",
        library={**_PCI, "steering": _AUDIT},
        skills={"deploy-review": "Theirs.\n"},
    )
    own = {
        "library": {"tasks": {"pci_review": AGENT, "mine": AGENT}, "steering": _AUDIT},
        "skills": {"deploy-review": "Ours.\n"},
    }
    requires = {"kraft": "2", "harnesses": ["codex"]}
    old = _own(own, manifest_fields={"requires": requires})
    new = _own(own, manifest_fields={"requires": {**requires, "addons": {parent.id: "1"}}})
    found = review.review(
        "release@payments", extracted(**old), extracted(**new), new_ancestors=[parent]
    )
    assert f"now builds on {parent.id} 1" in found.reach
    shadows = [line for line in found.reach if "now overrides" in line]
    assert sorted(shadows) == sorted(
        [
            _shadow("task", "pci_review"),
            _shadow("steering profile", "audit"),
            _shadow("skill", "deploy-review"),
        ]
    ), found


def test_a_shadow_names_an_aliased_ancestor_by_its_id(tmp_path):
    """The ancestor is registered under its alias; the line names its id."""
    parent = installed(tmp_path, "company", alias="corp", library=_PCI)
    old_parent = installed(tmp_path, "company", alias="corp", library=_BASE_TASKS)
    own = {
        "library": {"tasks": {"pci_review": AGENT}},
        "manifest_fields": {
            "requires": {"kraft": "2", "harnesses": ["codex"], "addons": {parent.id: "1"}}
        },
    }
    found = review.review(
        "release@payments",
        extracted(**own),
        extracted(**own),
        old_ancestors=[old_parent],
        new_ancestors=[parent],
    )
    assert _shadow("task", "pci_review") in found.reach, found


def test_an_ancestor_whose_remove_is_malformed_keeps_the_levels_aligned(tmp_path):
    """The parent's `remove:` does not read, so the parent counts as offering
    nothing; the grandparent's names still belong to the grandparent."""
    top_was = installed(tmp_path, "company", library=_BASE_TASKS)
    top = installed(tmp_path, "company", library=_PCI)
    mid = installed(tmp_path, "team", library={"remove": {"bogus": ["x"]}})
    mid = replace(mid, parent=top.id, parent_range="1")
    own = {
        "library": {"tasks": {"pci_review": AGENT}},
        "manifest_fields": {
            "requires": {"kraft": "2", "harnesses": ["codex"], "addons": {mid.id: "1"}}
        },
    }
    found = review.review(
        "release@payments",
        extracted(**own),
        extracted(**own),
        old_ancestors=[mid, top_was],
        new_ancestors=[mid, top],
    )
    assert _shadow("task", "pci_review") in found.reach, found


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


def test_an_unchanged_addon_is_up_to_date():
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
