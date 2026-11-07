"""Slicing a chain when its plan declares slices (`templates.slicing`): what the
rules pick as the body, why a chain is left alone, and what `revise` makes of it."""

from __future__ import annotations

from pathlib import Path

import pytest
from support.harness import v1_node, v1_task

from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates import revision, slicing
from kraft.templates.environment import WorkItemTarget
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import Chain, GroupNode, ResolvedChain
from templates.test_revision import _run

GATE = "revision_approval"

PLAN = """## Slices

- **Slice 1: A.** Tasks 1-2.
- **Slice 2: B.** Task 3.

### Task 1: a
### Task 2: b
### Task 3: c
"""

LIBRARY = TemplateLibrary.from_mappings({}, (), library_path=Path("library.yaml"))


def _nodes(*, build: dict | None = None, brief: dict | None = None) -> list[dict]:
    """plan -> revision_approval -> build -> check -> brief -> review (gate, shows the
    brief) -> feedback -> land (forge)."""
    return [
        _run("plan"),
        v1_node(GATE, "gate", artifact="chain_revision"),
        build or v1_node("build", tasks=[v1_task("run", kind="agent", prompt="build it")]),
        _run("check"),
        brief
        or _run(
            "brief",
            tasks=[
                v1_task("write", kind="agent", prompt="x", skill="kraft:brief", produces="brief")
            ],
        ),
        v1_node("review", "gate", artifact="brief", reject_to="build"),
        _run("feedback", on_base_changed={"restart_from": "check"}),
        v1_node("land", tasks=[v1_task("merge", kind="forge")]),
    ]


def _chain(nodes=None, target=None):
    chain = ResolvedChain.from_chain(Chain.model_validate({"id": "c", "nodes": nodes or _nodes()}))
    return chain.materialize(
        target=target or WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
    )


def _changes(**kw) -> revision.ChangeSet:
    return revision.ChangeSet.model_validate({"rationale": "the chain fits", **kw})


def _for(chain, plan=PLAN, **kw):
    return slicing.slicing_for(chain, plan, _changes(**kw), max_slices=6, at=GATE)


def test_the_body_stops_before_a_node_a_gate_shows():
    result = _for(_chain())
    assert result == slicing.SliceChange(
        group="slices", body=["build", "check"], unforked=["build"], slices=2
    )


def test_revise_wraps_the_body_and_repeats_the_rest_after_the_group():
    chain = _chain()
    changes = slicing.with_slicing(_changes(), chain, slicing.SlicingInput(PLAN, 6), at=GATE)
    revised = revision.revise(chain, changes, at=GATE, library=LIBRARY)

    nodes = revised.chain.chain.nodes
    assert [n.id for n in nodes] == [
        "plan", GATE, "slices", "check", "brief", "review", "feedback", "land",
    ]  # fmt: skip
    group = nodes[2]
    assert isinstance(group, GroupNode)
    assert [n.id for n in group.nodes] == ["build", "check"]
    assert group.unforked == ["build"]
    assert nodes[5].reject_to == "slices"
    assert nodes[6].on_base_changed.restart_from == "check"


def _own_library(tmp_path, monkeypatch):
    task = {"kind": "agent", "harness": "claude", "prompt": "resolve it"}
    return TemplateLibrary.from_mappings(
        {"tasks": {"resolve_join_conflict": task}}, (), library_path=Path("library.yaml")
    )


def _seeded_home(tmp_path, monkeypatch):
    """An empty local library: the entry comes from the built-in add-on."""
    from kraft.addons import load

    live = tmp_path / "config"
    (live / "chains").mkdir(parents=True)
    (live / "library.yaml").write_text("{}\n")
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(live))
    return TemplateLibrary.from_yaml_dir(live, addons=load.installed(live))


def _home_with_its_own(tmp_path, monkeypatch):
    """Both the local library and the built-in add-on have one: the local wins."""
    library = _seeded_home(tmp_path, monkeypatch)
    task = {"kind": "agent", "harness": "claude", "prompt": "resolve it"}
    return library.with_library({"tasks": {"resolve_join_conflict": task}}, Path("library.yaml"))


@pytest.mark.parametrize(
    ("library", "prompt"),
    [
        pytest.param(lambda *_: LIBRARY, None, id="none-in-the-library"),
        pytest.param(_own_library, "resolve it", id="its-own"),
        pytest.param(_seeded_home, "Replay the slice", id="the-built-in-add-ons"),
        pytest.param(_home_with_its_own, "resolve it", id="its-own-before-the-built-in"),
    ],
)
def test_the_group_takes_the_librarys_join_conflict_handler(tmp_path, monkeypatch, library, prompt):
    chain = _chain()
    changes = slicing.with_slicing(_changes(), chain, slicing.SlicingInput(PLAN, 6), at=GATE)

    revised = revision.revise(chain, changes, at=GATE, library=library(tmp_path, monkeypatch))

    group = revised.chain.nodes[2]
    if prompt is None:
        assert group.node.on_conflict is None and group.on_conflict == ()
        return
    [step] = group.on_conflict
    [task] = step.tasks
    assert task.path.startswith("slices.on_conflict.")
    assert task.task.kind == "agent" and task.task.prompt.startswith(prompt)


def test_the_default_chain_slices_implementation_and_verification(templates_dir):
    from support.harness import v1_named_chain

    resolved = v1_named_chain(templates_dir, "default")
    chain = resolved.materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
    )
    at = "chain_revision_approval"
    changes = slicing.with_slicing(_changes(), chain, slicing.SlicingInput(PLAN, 6), at=at)
    assert changes.slice == slicing.SliceChange(
        group="slices",
        body=["implementation", "verification"],
        unforked=["implementation"],
        slices=2,
    )

    revised = revision.revise(chain, changes, at=at, library=None)

    ids = [n.id for n in revised.chain.chain.nodes]
    assert ids[ids.index("slices") : ids.index("slices") + 3] == [
        "slices",
        "verification",
        "work_brief",
    ]
    by_id = {n.id: n for n in revised.chain.chain.nodes}
    assert by_id["local_review"].reject_to == "slices"
    assert by_id["final_review"].reject_to == "slices"
    assert by_id["merge_request_feedback"].on_base_changed.restart_from == "verification"


@pytest.mark.parametrize(
    ("kw", "says"),
    [
        pytest.param({"plan": None}, "no ## Slices", id="no-section"),
        pytest.param(
            {"plan": "## Slices\n\n- **Slice 1: A.** Task 1.\n### Task 1: a\n"},
            "not used",
            id="refused-section",
        ),
        pytest.param(
            {"target": WorkItemTarget(kind="workspace", workspace="w", root="r")},
            "workspace",
            id="workspace-item",
        ),
        pytest.param(
            {
                "target": WorkItemTarget.for_repository("target").model_copy(
                    update={"in_place": True}
                )
            },
            "in-place",
            id="in-place-item",
        ),
        pytest.param({"at": "review"}, "already run", id="implementing-node-already-ran"),
        pytest.param(
            {"skip": [{"node": "build", "evidence": "x"}]},
            "skips",
            id="agent-skipped-implementation",
        ),
        pytest.param(
            {
                "nodes": _nodes(
                    build=v1_node(
                        "build",
                        tasks=[
                            v1_task("run", kind="agent", prompt="build"),
                            v1_task("merge", kind="forge"),
                        ],
                    )
                )
            },
            "forge",
            id="span-holds-a-forge-task",
        ),
    ],
)
def test_it_does_not_slice_and_says_why(kw, says):
    at = kw.get("at", GATE)
    chain = _chain(kw.get("nodes"), kw.get("target"))
    result = slicing.slicing_for(
        chain,
        kw["plan"] if "plan" in kw else PLAN,
        _changes(skip=kw.get("skip", [])),
        max_slices=6,
        at=at,
    )
    assert isinstance(result, str)
    assert says in result


def test_with_slicing_says_why_a_present_section_was_not_used():
    chain = _chain()
    bad = "## Slices\n\n- **Slice 1: A.** Task 1.\n### Task 1: a\n"
    changes = slicing.with_slicing(_changes(), chain, slicing.SlicingInput(bad, 6), at=GATE)
    assert changes.empty
    assert "Not sliced" in changes.rationale


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param(
            "slice", '{"body": ["build"], "unforked": ["build"], "slices": 2}', id="slice"
        ),
        pytest.param("unslice", '"slices"', id="unslice"),
    ],
)
def test_an_agent_cannot_declare_a_slice(key, value):
    text = f'---\nwork_item_ids: [w1]\n---\n```json\n{{"rationale": "r", "{key}": {value}}}\n```\n'
    with pytest.raises(revision.RevisionError, match=f"may not declare {key}"):
        revision.parse(text)


@pytest.mark.parametrize(
    ("plan", "ids", "reject_to"),
    [
        pytest.param(
            PLAN,
            ["plan", GATE, "slices", "check", "brief", "review", "feedback", "land"],
            "slices",
            id="rebuilt",
        ),
        pytest.param(
            "### Task 1: a\n",
            ["plan", GATE, "build", "check", "brief", "review", "feedback", "land"],
            "build",
            id="left-out",
        ),
    ],
)
def test_revision_again_takes_the_group_out_and_decides_afresh(plan, ids, reject_to):
    """Chain revision running again after a rewind to the plan, over a chain
    that already holds the group: it comes out (body in place, copies gone,
    targets back), and the plan as it stands now rebuilds it or leaves it out."""
    chain = _chain()
    once = slicing.with_slicing(_changes(), chain, slicing.SlicingInput(PLAN, 6), at=GATE)
    sliced = revision.revise(chain, once, at=GATE, library=LIBRARY)

    changes = slicing.with_slicing(_changes(), sliced, slicing.SlicingInput(plan, 6), at=GATE)
    revised = revision.revise(sliced, changes, at=GATE, library=LIBRARY)

    assert changes.unslice == "slices"
    assert not changes.empty  # the gate asks a person either way
    nodes = revised.chain.chain.nodes
    assert [n.id for n in nodes] == ids
    by_id = {n.id: n for n in nodes}
    assert by_id["review"].reject_to == reject_to
    assert by_id["feedback"].on_base_changed.restart_from == "check"
    assert ("Not sliced" in changes.rationale) is (reject_to == "build")


def test_the_revising_task_is_not_shown_a_chain_whose_group_cannot_come_out(monkeypatch):
    chain = _chain()
    once = slicing.with_slicing(_changes(), chain, slicing.SlicingInput(PLAN, 6), at=GATE)
    sliced = revision.revise(chain, once, at=GATE, library=LIBRARY)

    def refuse(chain, group):
        raise ValueError("the group cannot come out")

    monkeypatch.setattr(slicing, "unsliced", refuse)

    with pytest.raises(ValueError, match="cannot come out"):
        revision.context_note(sliced, "plan")


def test_an_item_filed_with_a_plan_keeps_the_revision_gate_that_approves_its_split(templates_dir):
    """`--plan` trims `plan` and `plan_approval`, never `chain_revision`, so every
    split is approved somewhere."""
    from support.harness import v1_named_chain

    chain = v1_named_chain(templates_dir, "default").materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
        attachment_kinds=frozenset({"plan"}),
    )
    ids = [n.id for n in chain.chain.nodes]
    assert "plan_approval" not in ids
    assert {"chain_revision", "chain_revision_approval"} <= set(ids)
