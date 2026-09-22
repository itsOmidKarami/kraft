"""Loading a V1 template directory and resolving its chains: `extends`
expansion, source context in errors, and the canonical paths the design
document's own `library.yaml` / `chains/default.yaml` must produce."""

from pathlib import Path

import pytest
import yaml

from kraft.templates.library import TemplateLibrary, TemplateLibraryError
from kraft.templates.models import AgentInput, AgentTask, BuiltinAction, ForgeAction, GateNode

#: The design document's "Library components" and "Chain example" YAML,
#: copied verbatim -- the acceptance target for resolution.
DESIGN_TEMPLATES = Path(__file__).resolve().parents[1] / "fixtures" / "templates_v1"


@pytest.fixture
def design() -> TemplateLibrary:
    return TemplateLibrary.from_yaml_dir(DESIGN_TEMPLATES)


def write(root: Path, library: dict, chain: dict, chain_id: str = "default") -> TemplateLibrary:
    (root / "chains").mkdir(parents=True, exist_ok=True)
    (root / "library.yaml").write_text(yaml.safe_dump(library))
    (root / "chains" / f"{chain_id}.yaml").write_text(yaml.safe_dump(chain))
    return TemplateLibrary.from_yaml_dir(root)


def agent_task(**kw) -> dict:
    return {"kind": "agent", "harness": "codex", "prompt": "do it", **kw}


# ── the library is components plus one chain per file
# (template-library-has-shared-components-and-chain-files) ──


def test_from_yaml_dir_loads_components_and_one_chain_per_file(design):
    assert design.chain_ids == ("default",)
    assert "spec_author" in design.component_names("tasks")
    assert "implementation" in design.component_names("nodes")
    assert design.steering["project-standards"].instructions.startswith("Keep changes focused")


def test_each_chain_file_is_one_selectable_chain(tmp_path):
    library = {"tasks": {"base": agent_task()}}
    chain = {
        "id": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}],
    }
    loaded = write(tmp_path, library, chain)
    (tmp_path / "chains" / "quick-change.yaml").write_text(
        yaml.safe_dump({**chain, "id": "quick-change"})
    )
    assert TemplateLibrary.from_yaml_dir(tmp_path).chain_ids == ("default", "quick-change")
    assert loaded.chain_ids == ("default",)


def test_two_chain_files_claiming_one_id_is_an_error_naming_both(tmp_path):
    library = {"tasks": {"base": agent_task()}}
    chain = {
        "id": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}],
    }
    write(tmp_path, library, chain)
    (tmp_path / "chains" / "also-default.yaml").write_text(yaml.safe_dump(chain))
    with pytest.raises(TemplateLibraryError) as exc:
        TemplateLibrary.from_yaml_dir(tmp_path)
    assert "also-default.yaml" in str(exc.value)
    assert "chains/default.yaml" in str(exc.value)


def test_chain_level_extends_is_rejected_explicitly(tmp_path):
    library = {"tasks": {"base": agent_task()}}
    chain = {
        "id": "variant",
        "extends": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}],
    }
    with pytest.raises(TemplateLibraryError, match="chain-level 'extends' is not supported"):
        write(tmp_path, library, chain, chain_id="variant").resolve_chain("variant")


def test_an_unknown_chain_is_rejected(design):
    with pytest.raises(TemplateLibraryError, match="no chain 'nope'"):
        design.resolve_chain("nope")


# ── the design's own chain resolves to the design's own canonical paths
# (component-identifiers-are-qualified-by-node-instance,
# task-group-shorthand-resolves-to-one-step, resolved-chain-identifiers-are-unique) ──


def test_the_design_chain_resolves_to_its_documented_canonical_paths(design):
    paths = design.resolve_chain("default").task_paths
    assert set(paths) >= {
        "verification.tests.test_changed_scopes",
        "verification.review.code_review",
        "spec.main.author",
        "merge_request_feedback.on_failure.repair.repair_feedback",
        "merge_request_feedback.fix_loop.repair.repair",
        "verification.fix_loop.main.repair",
        "merge_request_feedback.fix_loop.judge",
    }
    assert len(set(paths)) == len(paths)


def test_the_design_chain_keeps_gates_as_ordered_nodes(design):
    chain = design.resolve_chain("default")
    gates = {n.id: n.node for n in chain.nodes if isinstance(n.node, GateNode)}
    assert gates["chain_review"].chain_finalized is True
    assert gates["spec_approval"].artifact == "spec"
    assert gates["spec_approval"].reject_to == "spec"
    assert [n.id for n in chain.nodes][:4] == ["spec", "spec_approval", "plan", "plan_approval"]


def test_resolution_types_every_task_in_the_design_chain(design):
    chain = design.resolve_chain("default")
    by_path = {t.path: t.task for n in chain.nodes for s in n.steps for t in s.tasks}
    assert by_path["spec.main.author"].produces == "spec"
    assert by_path["spec.main.author"].skill == "kraft:spec"
    assert (
        by_path["verification.tests.test_changed_scopes"].ref
        is BuiltinAction.VERIFY_CHANGED_TEST_SCOPES
    )
    # Ruling 207: the draft opens from the metadata the node before it wrote.
    assert by_path["describe_merge_request.main.author"].produces == "mr_meta"
    assert by_path["describe_merge_request.main.author"].skill == "kraft:mr-metadata"
    assert by_path["draft_merge_request.main.open"].target is ForgeAction.MR_OPEN_DRAFT
    await_ci = by_path["merge_request_feedback.ci.await_ci"]
    # Its timeout is its own total cap (Ruling 196); `wait:` holds the polling.
    assert await_ci.policy.total_time_cap_minutes == 90
    assert await_ci.wait.polling.initial_interval.total_seconds() == 30


# ── extends (component-extends-has-one-parent,
# extends-merges-objects-and-replaces-arrays, extends-cannot-change-kind) ──


def test_extends_expands_a_library_node_into_the_chain(design):
    chain = design.resolve_chain("default")
    node = next(n for n in chain.nodes if n.id == "implementation")
    assert [s.id for s in node.steps] == ["main"]
    implement = node.steps[0].tasks[0].task
    assert isinstance(implement, AgentTask)
    # Inherited from the `implementer` task, with the local id kept.
    assert (implement.id, implement.effort, implement.prompt) == (
        "implement",
        "high",
        "Implement the approved plan.",
    )


# ── the default chain's shape (Ruling 87): implement once, then a verification
# node whose fix loop re-runs tests and review, then a work brief the pre-draft
# gate shows ──


def test_the_design_chain_implements_then_verifies_then_briefs_before_the_draft(design):
    chain = design.resolve_chain("default")
    ids = [n.id for n in chain.nodes]
    assert ids[ids.index("plan_approval") + 1 : ids.index("draft_merge_request")] == [
        "implementation",
        "verification",
        "work_brief",
        "local_review",
        "describe_merge_request",
    ]
    nodes = {n.id: n for n in chain.nodes}
    # Implementing has no fix loop: a repair re-runs verification, not the
    # implementer (`fix-loop-remeasures-the-whole-node` reruns a node from step 0).
    assert nodes["implementation"].node.fix_loop is None
    verification = nodes["verification"]
    # Review is its own step after the tests, so it runs only on green.
    assert [[t.path for t in s.tasks] for s in verification.steps] == [
        ["verification.tests.test_changed_scopes"],
        ["verification.review.code_review"],
    ]
    review = verification.steps[1].tasks[0].task
    assert (review.skill, review.inputs) == (
        "kraft:code-review",
        [AgentInput.REVIEW_PACKAGE, AgentInput.CARRIED_FINDINGS, AgentInput.PREVIOUS_REVIEW],
    )
    assert verification.node.fix_loop.max_attempts == 2
    assert verification.judge.task.inputs == [AgentInput.REVIEW_PACKAGE]
    # The pre-draft gate shows the document the node before it produces.
    assert nodes["work_brief"].produces() == frozenset({"work_brief"})
    assert nodes["local_review"].node.artifact == "work_brief"
    # A rejection carries a human's note, and only the implementer acts on one.
    assert nodes["local_review"].node.reject_to == "implementation"
    assert nodes["chain_review"].node.reject_to == "implementation"
    # A CI-conflict rebase in post-draft feedback re-tests and re-reviews the
    # rebased head (Kraft-bjw6a).
    assert nodes["merge_request_feedback"].node.on_base_changed.restart_from == "verification"


def only_task(library: TemplateLibrary):
    return library.resolve_chain("default").nodes[0].steps[0].tasks[0].task


def test_extends_replaces_arrays_wholesale_and_scalars_including_null(tmp_path):
    library = {
        "steering": {"a": {"instructions": "A"}, "b": {"instructions": "B"}},
        "tasks": {"base": agent_task(steering=["a"], model="gpt-5.6-terra", effort="low")},
    }
    chain = {
        "id": "default",
        "nodes": [
            {
                "id": "n",
                "kind": "exec",
                "tasks": [{"id": "t", "extends": "base", "steering": ["b"], "effort": None}],
            }
        ],
    }
    task = only_task(write(tmp_path, library, chain))
    assert task.steering == ["b"]  # array replaced wholesale, not merged
    assert task.model == "gpt-5.6-terra"  # untouched inherited scalar
    assert task.effort is None  # an explicit null replaces


def test_extends_merges_maps_recursively(tmp_path):
    library = {
        "tasks": {
            "base": {
                "kind": "forge",
                "target": "mr.ci",
                "policy": {"total_time_cap_minutes": 10},
                "wait": {"polling": {"initial_interval": "30s"}},
            }
        }
    }
    chain = {
        "id": "default",
        "nodes": [
            {
                "id": "n",
                "kind": "exec",
                "tasks": [
                    {"id": "t", "extends": "base", "wait": {"polling": {"max_interval": "5m"}}}
                ],
            }
        ],
    }
    task = only_task(write(tmp_path, library, chain))
    assert task.policy.total_time_cap_minutes == 10
    assert task.wait.polling.initial_interval.total_seconds() == 30
    assert task.wait.polling.max_interval.total_seconds() == 300


def test_extends_expands_a_reusable_step(tmp_path):
    library = {
        "tasks": {"base": agent_task()},
        "steps": {"verification": {"tasks": [{"id": "run", "extends": "base"}]}},
    }
    chain = {
        "id": "default",
        "nodes": [
            {"id": "n", "kind": "exec", "steps": [{"id": "checks", "extends": "verification"}]}
        ],
    }
    assert write(tmp_path, library, chain).resolve_chain("default").task_paths == ("n.checks.run",)


def test_extends_cannot_change_the_parent_kind(tmp_path):
    library = {"tasks": {"base": agent_task()}}
    chain = {
        "id": "default",
        "nodes": [
            {
                "id": "n",
                "kind": "exec",
                "tasks": [{"id": "t", "extends": "base", "kind": "subprocess"}],
            }
        ],
    }
    with pytest.raises(TemplateLibraryError, match="cannot change kind 'agent' to 'subprocess'"):
        write(tmp_path, library, chain).resolve_chain("default")


def test_extends_cannot_change_a_kind_inherited_further_up_the_chain(tmp_path):
    library = {"tasks": {"base": agent_task(), "middle": {"extends": "base", "effort": "high"}}}
    chain = {
        "id": "default",
        "nodes": [
            {
                "id": "n",
                "kind": "exec",
                "tasks": [{"id": "t", "extends": "middle", "kind": "subprocess"}],
            }
        ],
    }
    with pytest.raises(TemplateLibraryError, match="cannot change kind 'agent' to 'subprocess'"):
        write(tmp_path, library, chain).resolve_chain("default")


def test_extends_rejects_more_than_one_parent(tmp_path):
    library = {"tasks": {"a": agent_task(), "b": agent_task()}}
    chain = {
        "id": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": ["a", "b"]}]}],
    }
    with pytest.raises(TemplateLibraryError, match="exactly one parent"):
        write(tmp_path, library, chain).resolve_chain("default")


def test_extends_rejects_a_cycle(tmp_path):
    library = {
        "tasks": {
            "a": {"kind": "agent", "harness": "h", "prompt": "p", "extends": "b"},
            "b": {"extends": "a"},
        }
    }
    chain = {
        "id": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "a"}]}],
    }
    with pytest.raises(TemplateLibraryError, match="cycle"):
        write(tmp_path, library, chain).resolve_chain("default")


def test_extends_rejects_a_cross_namespace_parent(tmp_path):
    library = {"tasks": {"implementer": agent_task()}}
    chain = {"id": "default", "nodes": [{"id": "n", "extends": "implementer"}]}
    with pytest.raises(TemplateLibraryError, match="which is a task, not a node"):
        write(tmp_path, library, chain).resolve_chain("default")


def test_extends_rejects_an_unknown_parent(tmp_path):
    library = {"tasks": {}}
    chain = {
        "id": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "ghost"}]}],
    }
    with pytest.raises(TemplateLibraryError, match="no task named 'ghost'"):
        write(tmp_path, library, chain).resolve_chain("default")


# ── errors name the input to correct
# (template-resolution-preserves-source-context) ──


def test_an_inheritance_error_names_the_file_and_the_component(tmp_path):
    library = {"tasks": {"base": agent_task()}}
    chain = {
        "id": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "ghost"}]}],
    }
    with pytest.raises(TemplateLibraryError) as exc:
        write(tmp_path, library, chain).resolve_chain("default")
    message = str(exc.value)
    assert "chains/default.yaml" in message
    assert "n.main.t" in message


def test_a_schema_error_names_the_library_file_the_component_came_from(tmp_path):
    library = {"tasks": {"broken": {"kind": "builtin", "ref": "kraft.nope"}}}
    chain = {
        "id": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "broken"}]}],
    }
    with pytest.raises(TemplateLibraryError) as exc:
        write(tmp_path, library, chain).resolve_chain("default")
    message = str(exc.value)
    assert "library.yaml" in message
    assert "broken" in message


def test_a_duplicate_identifier_error_names_the_container_and_the_id(tmp_path):
    library = {"tasks": {"base": agent_task()}}
    chain = {
        "id": "default",
        "nodes": [
            {
                "id": "n",
                "kind": "exec",
                "steps": [
                    {"id": "s", "tasks": [{"id": "t", "extends": "base"}]},
                    {"id": "s", "tasks": [{"id": "t", "extends": "base"}]},
                ],
            }
        ],
    }
    with pytest.raises(TemplateLibraryError) as exc:
        write(tmp_path, library, chain).resolve_chain("default")
    assert "duplicate step id 's'" in str(exc.value)
    assert "chains/default.yaml" in str(exc.value)


# ── references are validated before use (resolved-chain-is-validated-before-use) ──


def test_an_unknown_steering_reference_is_rejected(tmp_path):
    library = {"tasks": {"base": agent_task(steering=["nowhere"])}}
    chain = {
        "id": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}],
    }
    with pytest.raises(TemplateLibraryError, match="selects no steering profile 'nowhere'"):
        write(tmp_path, library, chain).resolve_chain("default")


@pytest.mark.parametrize("name", ["kraft:no-such-method", "no-such-method"])
def test_a_skill_that_resolves_to_nothing_is_a_lint_error(tmp_path, name):
    """Kraft-vhcop: a selected skill that names no method is refused when the
    chain resolves -- lint, intake -- not discovered by an agent at launch or
    silently replaced by an instruction to load a plugin nobody ships."""
    library = {"tasks": {"base": agent_task(skill=name)}}
    chain = {
        "id": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}],
    }
    lib = write(tmp_path, library, chain)
    with pytest.raises(TemplateLibraryError, match=f"n.main.t.*{name}"):
        lib.resolve_chain("default")
    assert [i.chain for i in lib.lint()] == ["default"]


def test_a_skill_the_operator_overlay_provides_resolves(tmp_path):
    """The overlay is a real source: a method only the operator ships is not a
    lint error."""
    skills = tmp_path / "skills"
    (skills / "house-method").mkdir(parents=True)
    (skills / "house-method" / "SKILL.md").write_text("ours")
    library = {"tasks": {"base": agent_task(skill="kraft:house-method")}}
    chain = {
        "id": "default",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}],
    }
    write(tmp_path / "templates", library, chain)
    lib = TemplateLibrary.from_yaml_dir(tmp_path / "templates", skills_dir=skills)
    assert lib.lint() == []


def test_every_seeded_skill_reaches_the_agent_as_its_bundled_method():
    """Kraft-vhcop: the seed named `kraft:spec`, `kraft:plan` and
    `kraft:review-brief`, none of which resolved, so the bundled methods never
    reached an agent. Every seeded skill must read as its shipped method."""
    from kraft import skill

    seed = Path(__file__).resolve().parents[2] / "templates"
    lib = TemplateLibrary.from_yaml_dir(seed)
    seen = set()
    for cid in lib.chain_ids:
        for node in lib.resolve_chain(cid).nodes:
            for t in node.tasks():
                if isinstance(t.task, AgentTask) and t.task.skill is not None:
                    name = t.task.skill.removeprefix("kraft:")
                    bundled = (skill.BUNDLED / name / "SKILL.md").read_text()
                    assert skill.read(None, t.task.skill) == bundled, t.path
                    seen.add(t.task.skill)
    assert {"kraft:spec", "kraft:plan", "kraft:review-brief"} <= seen


def test_a_malformed_template_file_is_a_configuration_error(tmp_path):
    (tmp_path / "chains").mkdir()
    (tmp_path / "library.yaml").write_text("tasks: [not, a, mapping]\n")
    with pytest.raises(TemplateLibraryError, match="library.yaml"):
        TemplateLibrary.from_yaml_dir(tmp_path)


def test_a_missing_template_directory_is_a_configuration_error(tmp_path):
    with pytest.raises(TemplateLibraryError, match="library.yaml"):
        TemplateLibrary.from_yaml_dir(tmp_path / "absent")


def test_lint_refuses_a_node_mixing_tasks_with_and_without_produces(tmp_path):
    """Ruling 35's first edge. `trim_for_attachments` is a *node*-level rule and
    `produces` is task-level, so a node holding one spec-producing task plus
    another task survives an attached spec and writes the spec again. Closed in
    validation instead of at runtime: a node either wholly produces a kind or
    declares none, and the trim is then unambiguous.

    Dropping the single producing task and rebuilding the step is not the
    alternative -- `Step.tasks` has `min_length=1`, so an emptied step is
    invalid.
    """
    library = {
        "tasks": {
            "author": agent_task(produces="spec"),
            "other": agent_task(),
        }
    }
    chain = {
        "id": "default",
        "nodes": [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [{"id": "a", "extends": "author"}, {"id": "b", "extends": "other"}],
            }
        ],
    }
    library_obj = write(tmp_path, library, chain)
    with pytest.raises(TemplateLibraryError, match="does not agree on what it produces"):
        library_obj.resolve_chain("default")
    # `lint()` is the reporting door onto the same check, and it names the file
    # and chain rather than raising at the first one.
    assert [i.chain for i in library_obj.lint()] == ["default"]


def test_lint_allows_a_node_whose_every_task_produces_the_same_kind(tmp_path):
    library = {"tasks": {"author": agent_task(produces="spec")}}
    chain = {
        "id": "default",
        "nodes": [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [{"id": "a", "extends": "author"}, {"id": "b", "extends": "author"}],
            }
        ],
    }
    assert write(tmp_path, library, chain).lint() == []


def test_the_design_chain_has_no_node_mixing_produces(design):
    """The rule above would be a trap if the shipped chain broke it."""
    assert design.lint() == []


def test_lint_refuses_a_node_whose_tasks_produce_two_different_kinds(tmp_path):
    """The other half of the same ambiguity. A node producing both a spec and a
    plan is not trimmed by an attached spec (the rule is "all of its tasks produce
    this kind"), so the spec is written again -- and trimming it *would* throw the
    plan away. `produces` has to agree across a node either way, so the rule is
    `len(produces) > 1`, not `> 1 and None in produces`.
    """
    library = {"tasks": {"spec": agent_task(produces="spec"), "plan": agent_task(produces="plan")}}
    chain = {
        "id": "default",
        "nodes": [
            {
                "id": "design",
                "kind": "exec",
                "tasks": [{"id": "a", "extends": "spec"}, {"id": "b", "extends": "plan"}],
            }
        ],
    }
    with pytest.raises(TemplateLibraryError, match="does not agree on what it produces"):
        write(tmp_path, library, chain).resolve_chain("default")
