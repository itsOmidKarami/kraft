"""Plan-driven chain revision (Kraft-oydes, Ruling 208): the change set a
`chain_revision` artifact carries, read strictly, and applied to a
materialized chain only when the result still validates as one."""

import json
from pathlib import Path

import pytest

from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates import revision
from kraft.templates.environment import WorkItemTarget
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import Chain, ResolvedChain


def _artifact(change: dict | str, *, before: str = "", after: str = "") -> str:
    body = change if isinstance(change, str) else json.dumps(change)
    return f"---\nwork_item_ids: [w1]\n---\n{before}```json\n{body}\n```\n{after}"


# ── the artifact is read strictly ──


def test_a_fenced_change_set_parses():
    changes = revision.parse(
        _artifact(
            {
                "rationale": "the plan adds a migration",
                "skip": [{"node": "brief", "evidence": "plan: no brief needed"}],
                "overrides": {"build.main.run": {"effort": "high", "evidence": "plan task 3"}},
            }
        )
    )
    assert [s.node for s in changes.skip] == ["brief"]
    assert changes.overrides["build.main.run"].effort == "high"
    assert not changes.empty


def test_a_change_set_with_only_a_rationale_is_empty():
    assert revision.parse(_artifact({"rationale": "the chain fits"})).empty


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (_artifact({"rationale": "ok"}, before="Here it is:\n"), "exactly one"),
        (_artifact({"rationale": "ok"}, after="\nThanks.\n"), "exactly one"),
        ("---\nwork_item_ids: [w1]\n---\n" + json.dumps({"rationale": "ok"}), "exactly one"),
        (_artifact("{not json"), "not JSON"),
        (_artifact('{"rationale": "a", "rationale": "b"}'), "twice"),
        (_artifact({"rationale": "ok", "reorder": ["a"]}), "reorder"),
        (_artifact({"rationale": ""}), "rationale"),
        (_artifact({"rationale": "ok", "skip": ["brief"]}), "skip"),
        (_artifact({"rationale": "ok", "skip": [{"node": "brief"}]}), "evidence"),
        (
            _artifact(
                {
                    "rationale": "ok",
                    "overrides": {"a.main.b": {"allowed_tools": [], "evidence": "e"}},
                }
            ),
            "allowed_tools",
        ),
        (_artifact({"rationale": "ok", "overrides": {"a": {"evidence": "e"}}}), "sets nothing"),
        (
            _artifact(
                {
                    "rationale": "ok",
                    "add": [
                        {
                            "after": "build",
                            "evidence": "e",
                            "node": {"id": "x", "tasks": [{"id": "t", "prompt": "anything"}]},
                        }
                    ],
                }
            ),
            r"node\.tasks\.0",
        ),
        (
            _artifact(
                {
                    "rationale": "ok",
                    "add": [{"after": "build", "evidence": "e", "node": {"id": "x"}}],
                }
            ),
            "extends",
        ),
    ],
    ids=[
        "prose-before",
        "prose-after",
        "no-fence",
        "not-json",
        "duplicate-key",
        "unknown-key",
        "empty-rationale",
        "bare-skip-id",
        "skip-without-evidence",
        "safety-policy-field",
        "override-setting-nothing",
        "added-task-not-from-the-library",
        "added-node-with-no-shape",
    ],
)
def test_anything_but_one_strict_change_set_is_refused(text, match):
    with pytest.raises(revision.RevisionError, match=match):
        revision.parse(text)


# ── applying a change set to a materialized chain ──

GATE = "revision_approval"


def _run(id: str, **kw) -> dict:
    return {
        "id": id,
        "kind": "exec",
        "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
        **kw,
    }


def _chain(**maxima):
    """plan -> revision_approval (the revision's gate) -> build -> brief ->
    review (gate) -> feedback -> final (chain_finalized) -> land."""
    nodes = [
        _run("plan"),
        {"id": GATE, "kind": "gate", "artifact": "chain_revision"},
        {
            "id": "build",
            "kind": "exec",
            "policy": {"time_cap_minutes": 60},
            "tasks": [{"id": "run", "kind": "agent", "harness": "fake", "prompt": "build it"}],
        },
        _run("brief"),
        {"id": "review", "kind": "gate", "artifact": "work_brief", "reject_to": "build"},
        _run("feedback", on_base_changed={"restart_from": "feedback"}),
        _run("pinned", skippable=False),
        {"id": "final", "kind": "gate", "chain_finalized": True, "artifact": "review_brief"},
        {
            "id": "land",
            "kind": "exec",
            "tasks": [{"id": "merge", "kind": "forge", "target": "mr.merge"}],
        },
    ]
    instance = InstancePolicy.from_input(InstancePolicyInput.model_validate({"maxima": maxima}))
    return ResolvedChain.from_chain(Chain.model_validate({"id": "c", "nodes": nodes})).materialize(
        target=WorkItemTarget.for_repository("target"), effective_policy=instance
    )


LIBRARY = TemplateLibrary.from_mappings(
    {
        "tasks": {
            "check": {"kind": "subprocess", "command": "just check"},
            "merge_it": {"kind": "forge", "target": "mr.merge"},
            "costly": {"kind": "subprocess", "command": "true", "policy": {"token_budget": 500}},
        },
        "nodes": {
            "extra_check": {"kind": "exec", "tasks": [{"id": "run", "extends": "check"}]},
            "a_gate": {"kind": "gate", "message": "look"},
        },
    },
    (),
    library_path=Path("library.yaml"),
)


def _changes(**kw) -> revision.ChangeSet:
    return revision.ChangeSet.model_validate({"rationale": "the plan says so", **kw})


def _skip(node):
    return {"node": node, "evidence": "plan: not needed"}


def _add(after, node):
    return {"after": after, "node": node, "evidence": "plan: needs it"}


def _override(**values):
    return {**values, "evidence": "plan: heavier than expected"}


def _revise(chain, **kw):
    return revision.revise(chain, _changes(**kw), gate=GATE, library=LIBRARY)


def test_an_empty_change_set_changes_nothing():
    chain = _chain()
    assert _revise(chain) is chain


def test_a_change_set_skips_adds_and_overrides_only_what_it_names():
    before = _chain()
    after = _revise(
        before,
        skip=[_skip("brief")],
        add=[
            _add("build", {"id": "checked", "extends": "extra_check"}),
            _add("feedback", {"id": "rechecked", "tasks": [{"id": "again", "extends": "check"}]}),
        ],
        overrides={"build.main.run": _override(effort="high", time_cap_minutes=30)},
    )

    assert [n.id for n in after.chain.nodes] == [
        "plan",
        GATE,
        "build",
        "checked",
        "review",
        "feedback",
        "rechecked",
        "pinned",
        "final",
        "land",
    ]
    build = next(n for n in after.chain.nodes if n.id == "build")
    task = build.steps[0].tasks[0].task
    assert (task.effort, task.policy.time_cap_minutes) == ("high", 30)
    # Everything it did not name is what it was: the review gate keeps its
    # reject target, the base-change span its restart point.
    assert next(n for n in after.chain.nodes if n.id == "review").node.reject_to == "build"
    assert after.policy == before.policy and after.target == before.target
    assert revision.diff(before.chain, after.chain) == [
        "  plan",
        f"  {GATE}",
        "  build",
        "~ build.main.run.effort: None -> 'high'",
        "~ build.main.run.policy.time_cap_minutes: None -> 30",
        "- brief",
        "+ checked",
        "  review",
        "  feedback",
        "+ rechecked",
        "  pinned",
        "  final",
        "  land",
    ]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"skip": [_skip("plan")]}, "already run"),
        ({"skip": [_skip(GATE)]}, "already run"),
        ({"overrides": {"plan.main.run": _override(effort="high")}}, "already run"),
        ({"add": [_add("plan", {"id": "early", "extends": "extra_check"})]}, "already run"),
    ],
    ids=["skip-a-run-node", "skip-its-own-gate", "override-a-run-node", "add-before-the-gate"],
)
def test_a_revision_changes_only_the_nodes_after_its_gate(change, match):
    with pytest.raises(revision.RevisionError, match=match):
        _revise(_chain(), **change)


@pytest.mark.parametrize(
    "change",
    [
        {"skip": [_skip("review")]},
        {"skip": [_skip("final")]},
        {"overrides": {"review": _override(time_cap_minutes=5)}},
        {"overrides": {"final": _override(token_budget=5)}},
    ],
    ids=["skip-a-gate", "skip-the-final-gate", "override-a-gate", "override-the-final-gate"],
)
def test_a_revision_can_never_skip_or_change_a_gate(change):
    with pytest.raises(revision.RevisionError, match="is a gate"):
        _revise(_chain(), **change)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"skip": [_skip("nowhere")]}, "no node 'nowhere'"),
        ({"skip": [_skip("pinned")]}, "not skippable"),
        ({"skip": [_skip("build")]}, "reject_to 'build'"),
        ({"add": [_add("nowhere", {"id": "x", "extends": "extra_check"})]}, "no node 'nowhere'"),
        ({"add": [_add("build", {"id": "x", "extends": "missing"})]}, "extends no node"),
        ({"add": [_add("build", {"id": "x", "extends": "a_gate"})]}, "cannot change kind"),
        ({"add": [_add("build", {"id": "brief", "extends": "extra_check"})]}, "duplicate node"),
        (
            {
                "skip": [_skip("brief")],
                "add": [_add("brief", {"id": "x", "extends": "extra_check"})],
            },
            "which is skipped",
        ),
        (
            {"add": [_add("build", {"id": "x", "tasks": [{"id": "m", "extends": "merge_it"}]})]},
            "before the final gate",
        ),
        ({"overrides": {"brief.main.run": _override(model="big")}}, "model"),
        ({"overrides": {"build.main.run": _override(time_cap_minutes=90)}}, "cannot exceed"),
        ({"overrides": {"build.main.nope": _override(effort="high")}}, "no node, step or task"),
    ],
    ids=[
        "skip-unknown",
        "skip-unskippable",
        "skip-a-reject-target",
        "add-after-unknown",
        "add-unknown-component",
        "add-a-gate",
        "add-a-duplicate-id",
        "add-after-a-skipped-node",
        "add-a-merge-before-the-final-gate",
        "model-on-a-subprocess",
        "cap-above-its-parent",
        "override-unknown-path",
    ],
)
def test_a_change_set_that_would_not_validate_is_refused(change, match):
    with pytest.raises(revision.RevisionError, match=match):
        _revise(_chain(), **change)


def test_an_override_stays_within_the_administrator_maxima():
    chain = _chain(max_attempts=3)
    with pytest.raises(revision.RevisionError, match="max_attempts"):
        _revise(chain, overrides={"feedback": _override(max_attempts=9)})
    assert _revise(chain, overrides={"feedback": _override(max_attempts=2)}) is not chain


def test_an_added_node_stays_within_the_administrator_maxima():
    """Nothing in the change set names a policy value here: the one it breaks
    comes with the library task, and the revised chain is checked whole."""
    added = {"id": "x", "tasks": [{"id": "c", "extends": "costly"}]}
    with pytest.raises(revision.RevisionError, match="token_budget"):
        _revise(_chain(token_budget=100), add=[_add("build", added)])


def test_adding_a_node_needs_the_library():
    with pytest.raises(revision.RevisionError, match="library"):
        revision.revise(
            _chain(),
            _changes(add=[_add("build", {"id": "x", "extends": "extra_check"})]),
            gate=GATE,
            library=None,
        )


# ── what the person at the gate reads, and what the revising agent is shown ──


def test_the_gate_reads_the_rationale_each_change_with_its_evidence_then_the_diff():
    text = _artifact({"rationale": "docs only", "skip": [_skip("brief")]})

    shown = revision.render(text, _chain(), GATE, LIBRARY)

    assert shown.index("docs only") < shown.index("- skip `brief` -- plan: not needed")
    assert shown.index("plan: not needed") < shown.index("```diff") < shown.index("- brief")


@pytest.mark.parametrize(
    ("text", "heading", "reason"),
    [
        (_artifact("{nope"), "## Cannot be read", "not JSON"),
        (_artifact({"rationale": "r", "skip": [_skip("review")]}), "## Cannot be applied", "gate"),
    ],
    ids=["unreadable", "unappliable"],
)
def test_the_gate_says_why_a_proposal_cannot_be_approved(text, heading, reason):
    shown = revision.render(text, _chain(), GATE, LIBRARY)

    assert heading in shown and reason in shown and "```diff" not in shown


def test_the_revising_task_is_shown_only_the_nodes_it_may_change():
    note = revision.context_note(_chain(), "plan")

    assert f"including `{GATE}`" in note
    tail = json.loads(note[note.index("```json") + 7 : note.rindex("```")])
    assert [n["node"]["id"] for n in tail][:2] == ["build", "brief"]
    assert tail[0]["task_paths"] == ["build.main.run"]


# ── the shipped chains ──

SHIPPED = Path(__file__).resolve().parents[2] / "templates"


def test_the_default_chain_revises_itself_right_after_the_plan_is_approved():
    chain = TemplateLibrary.from_yaml_dir(SHIPPED).resolve_chain("default")
    ids = [n.id for n in chain.nodes]
    at = ids.index("plan_approval") + 1

    assert ids[at : at + 2] == ["chain_revision", "chain_revision_approval"]
    [revise] = chain.nodes[at].tasks()
    assert (revise.task.produces, revise.task.skill, revise.task.harness) == (
        revision.CHAIN_REVISION,
        "kraft:chain-review",
        "claude",
    )
    gate = chain.nodes[at + 1].node
    assert (gate.artifact, gate.reject_to) == (revision.CHAIN_REVISION, "chain_revision")
    # The final gate is still the one it was.
    assert [n.id for n in chain.nodes if getattr(n.node, "chain_finalized", False)] == [
        "chain_review"
    ]


def test_the_quick_task_chain_has_no_revision():
    chain = TemplateLibrary.from_yaml_dir(SHIPPED).resolve_chain("quick-task")

    assert revision.CHAIN_REVISION not in {n.covered_by for n in chain.nodes}
