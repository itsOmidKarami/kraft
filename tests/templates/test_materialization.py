"""The V1 seed, library linting, and per-work-item materialization.

Three subjects, one file because they are one boundary: what Kraft ships in
`templates/`, whether that library is valid, and the immutable snapshot one
work item runs against.

The seed is asserted against the packaged `templates/` directory rather than a
fake one. `cli.seed_home` copies that tree wholesale into `$KRAFT_HOME`, so the
tree *is* the seeded layout -- a test over a synthesised directory would pin
nothing about what an operator actually gets.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates.environment import Repository, RootPointerPolicy, WorkItemTarget, Workspace
from kraft.templates.library import CHAINS_DIR, LIBRARY_FILE, TemplateLibrary, TemplateLibraryError
from kraft.templates.models import (
    AgentTask,
    BuiltinTask,
    ForgeTask,
    GateNode,
    MaterializedChain,
    SubprocessTask,
    TaskKind,
)

ROOT = Path(__file__).resolve().parents[2]

#: What `just bundle` copies into the wheel and `cli.seed_home` copies into a
#: fresh `$KRAFT_HOME/templates`.
SEEDED = ROOT / "templates"

DESIGN_DOC = ROOT / "docs" / "templates-v1-design.md"

#: The design document's own YAML, kept byte-identical to the seed and to the
#: resolver's fixtures (see `test_the_design_documents_yaml_is_what_kraft_ships`).
FIXTURES = ROOT / "tests" / "fixtures" / "templates_v1"


def policy(**maxima) -> InstancePolicy:
    return InstancePolicy.from_input(
        InstancePolicyInput.model_validate({"defaults": {"timeout_minutes": 60}, "maxima": maxima})
    )


def repository_target() -> WorkItemTarget:
    return WorkItemTarget.for_repository(Repository(id="api", path="/work/api"))


def workspace_target() -> WorkItemTarget:
    return WorkItemTarget.from_selection(
        Workspace.model_validate(
            {
                "id": "product",
                "root": "product_root",
                "members": {"api": {"repository": "api", "path": "services/api"}},
            }
        ),
        members=["api"],
        include_root=False,
        root_pointer_policy=RootPointerPolicy.BUMP,
    )


def agent_task(**kw) -> dict:
    return {"kind": "agent", "harness": "codex_default", "prompt": "do it", **kw}


def write(root: Path, library: dict, chains: dict[str, dict]) -> TemplateLibrary:
    (root / CHAINS_DIR).mkdir(parents=True, exist_ok=True)
    (root / LIBRARY_FILE).write_text(yaml.safe_dump(library))
    for id, chain in chains.items():
        (root / CHAINS_DIR / f"{id}.yaml").write_text(yaml.safe_dump(chain))
    return TemplateLibrary.from_yaml_dir(root)


def snapshot(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ── the seeded layout (template-library-has-shared-components-and-chain-files) ──


def test_the_seed_is_a_library_file_and_one_chain_per_file():
    assert (SEEDED / LIBRARY_FILE).is_file()
    assert [p.name for p in sorted((SEEDED / CHAINS_DIR).glob("*.yaml"))] == [
        "default.yaml",
        "quick-task.yaml",
    ]


def test_the_seeded_library_resolves_its_default_chain():
    library = TemplateLibrary.from_yaml_dir(SEEDED)

    assert "default" in library.chain_ids
    resolved = library.resolve_chain("default")
    # The paths docs/templates-v1-design.md "Resolution and execution" names.
    assert "spec.main.author" in resolved.task_paths
    assert "verification.tests.test_changed_scopes" in resolved.task_paths
    assert "merge_request_feedback.fix_loop.judge" in resolved.task_paths


def test_the_seeded_quick_task_chain_is_gateless():
    """The assertion that matters about `quick-task`: it has no gate.

    `frontend/e2e/chain.spec.ts` watches a chain run to completion unattended,
    which only this chain does. A gate creeping in would not fail anything here
    -- it would silently re-break that spec, and `playwright` is a required
    check. So the absence is pinned rather than inferred.
    """
    resolved = TemplateLibrary.from_yaml_dir(SEEDED).resolve_chain("quick-task")

    assert [n.id for n in resolved.nodes if isinstance(n.node, GateNode)] == []


def test_the_seeded_quick_task_chain_resolves_and_materializes():
    """The legacy chain's shape, ported: `implementation` then `verify`, with
    `env_setup`'s work now implicit (`builtins.prepare_runtime`)."""
    resolved = TemplateLibrary.from_yaml_dir(SEEDED).resolve_chain("quick-task")

    assert [n.id for n in resolved.nodes] == ["implementation", "verify"]
    assert resolved.task_paths == (
        "implementation.main.implement",
        "verify.main.test_changed_scopes",
    )

    materialized = resolved.materialize(target=repository_target(), effective_policy=policy())

    assert materialized.task_paths == resolved.task_paths


def test_the_harness_resolves_a_named_seeded_chain(tmp_path):
    """`support.harness.v1_named_chain` is the door 5b's ~156
    `template="quick-task"` call sites go through, so it resolves the seeded
    chain by name out of a test's own templates directory."""
    from support.harness import v1_named_chain

    assert v1_named_chain(tmp_path).id == "quick-task"
    assert v1_named_chain(tmp_path, "default").id == "default"


def test_a_harness_resolved_chain_launches_no_real_agent_and_no_real_builtin(tmp_path):
    """The seed `v1_library` writes is a neutered one, on an unseeded directory
    too -- without rewriting a single `harness:` id.

    Seeded without an `agent_command`, the profile `codex_default` launches the
    operator's `codex` and the real `kraft.verify_changed_test_scopes` runs this
    suite inside itself. Pinned rather than documented, because ~156 of 5b's
    call sites come through here and would inherit the hazard silently. The
    task keeps the shipped id; the *profile* it names is what is on the fake.
    """
    import yaml
    from support.harness import v1_named_chain

    tasks = [t.task for node in v1_named_chain(tmp_path).nodes for t in node.tasks()]

    assert [t.harness for t in tasks if isinstance(t, AgentTask)] == ["codex_default"]
    profile = yaml.safe_load((tmp_path / "harnesses.yaml").read_text())["harnesses"][
        "codex_default"
    ]
    assert profile["provider"] == "fake" and "executable" not in profile, profile
    assert [t for t in tasks if isinstance(t, BuiltinTask)] == []
    assert [t.command for t in tasks if isinstance(t, SubprocessTask)] == ["true"]


def test_the_seeded_library_has_no_lint_errors():
    assert TemplateLibrary.from_yaml_dir(SEEDED).lint() == []


# ── registry.yaml is not a task configuration source ──


def test_task_configuration_resolves_from_the_library_alone(tmp_path):
    """The seeded chain's tasks carry their own typed configuration, so a
    directory holding only `library.yaml` and `chains/` resolves every one of
    them -- there is no `registry.yaml` for a hook name to be looked up in."""
    (tmp_path / CHAINS_DIR).mkdir()
    (tmp_path / LIBRARY_FILE).write_text((SEEDED / LIBRARY_FILE).read_text())
    (tmp_path / CHAINS_DIR / "default.yaml").write_text(
        (SEEDED / CHAINS_DIR / "default.yaml").read_text()
    )
    assert not list(tmp_path.glob("registry.yaml"))

    resolved = TemplateLibrary.from_yaml_dir(tmp_path).resolve_chain("default")

    tasks = [t.task for node in resolved.nodes for t in node.tasks()]
    assert tasks, "the seeded chain has tasks to check"
    # Each is one of the four discriminated task models, fully configured from
    # the library -- not a string naming a registry hook.
    assert all(isinstance(t, (BuiltinTask, AgentTask, SubprocessTask, ForgeTask)) for t in tasks)
    assert {t.kind for t in tasks} <= set(TaskKind)
    # An agent task names its harness profile itself, which is the field a
    # registry hook binding used to hold.
    assert all(t.harness for t in tasks if isinstance(t, AgentTask))


def test_the_seed_ships_no_legacy_configuration():
    """A fresh home seeds only V1: no hook registry, and no chain outside
    `chains/` -- the two files the legacy loader read."""
    assert not (SEEDED / "registry.yaml").exists()
    legacy_chains = [
        p.name
        for p in SEEDED.glob("*.yaml")
        if isinstance(data := yaml.safe_load(p.read_text()), dict)
        and isinstance(data.get("nodes"), list)
    ]
    assert legacy_chains == []


# ── the design document's YAML is the seed ──


def _fenced(name: str) -> str:
    """The `yaml` block in `docs/templates-v1-design.md` whose first line is
    `# <name>`. The document is public reference documentation and the source
    of the seeded templates, so it has to stay loadable."""
    blocks = re.findall(r"^```yaml\n(.*?)^```$", DESIGN_DOC.read_text(), re.DOTALL | re.MULTILINE)
    matching = [b for b in blocks if b.startswith(f"# {name}\n")]
    assert len(matching) == 1, f"expected one '# {name}' yaml block, found {len(matching)}"
    return matching[0]


def test_the_design_documents_library_and_chain_resolve(tmp_path):
    (tmp_path / CHAINS_DIR).mkdir()
    (tmp_path / LIBRARY_FILE).write_text(_fenced("library.yaml"))
    (tmp_path / CHAINS_DIR / "default.yaml").write_text(_fenced("chains/default.yaml"))

    resolved = TemplateLibrary.from_yaml_dir(tmp_path).resolve_chain("default")

    assert resolved.id == "default"
    assert "spec.main.author" in resolved.task_paths


def test_the_design_documents_yaml_is_what_kraft_ships():
    """One text, three places: the document, the seed, and the resolver's
    fixtures. Phase 1 made the fixtures byte-identical deliberately -- it is
    what keeps the resolver's tests from being circular -- and the seed has to
    join them, or the shipped chain drifts from its own documentation."""
    for name, relative in (
        ("library.yaml", Path(LIBRARY_FILE)),
        ("chains/default.yaml", Path(CHAINS_DIR) / "default.yaml"),
    ):
        assert _fenced(name) == (SEEDED / relative).read_text(), f"{relative} differs from the doc"
        assert _fenced(name) == (FIXTURES / relative).read_text(), f"{relative} differs from doc"


# ── lint reports every error, and writes nothing
# (template-lint-reports-library-validity, template-resolution-preserves-source-context) ──


def test_lint_reports_an_unknown_reference_with_its_source(tmp_path):
    library = write(
        tmp_path,
        {"tasks": {"base": agent_task()}},
        {"broken": {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"extends": "typo"}]}]}},
    )

    issues = library.lint()

    assert len(issues) == 1
    assert issues[0].chain == "broken"
    assert issues[0].file == tmp_path / CHAINS_DIR / "broken.yaml"
    assert "extends no task named 'typo'" in issues[0].message


def test_lint_reports_a_cycle_with_its_source(tmp_path):
    library = write(
        tmp_path,
        {"tasks": {"a": {"extends": "b"}, "b": {"extends": "a"}}},
        {"looping": {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"extends": "a"}]}]}},
    )

    issues = library.lint()

    assert len(issues) == 1
    assert issues[0].chain == "looping"
    assert "'extends' cycle: a -> b -> a" in issues[0].message


def test_lint_reports_a_duplicate_sibling_identifier_with_its_source(tmp_path):
    library = write(
        tmp_path,
        {"tasks": {"base": agent_task()}},
        {
            "dupes": {
                "nodes": [
                    {
                        "id": "n",
                        "kind": "exec",
                        "tasks": [{"id": "t", "extends": "base"}, {"id": "t", "extends": "base"}],
                    }
                ]
            }
        },
    )

    issues = library.lint()

    assert len(issues) == 1
    assert issues[0].chain == "dupes"
    assert "duplicate task id 't'" in issues[0].message


def test_lint_reports_every_broken_chain_rather_than_the_first(tmp_path):
    """The point of a lint over a raising loader: an author fixing a library
    sees all of it, not one error per edit-and-rerun cycle."""
    library = write(
        tmp_path,
        {"tasks": {"base": agent_task()}},
        {
            "a_broken": {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"extends": "nope"}]}]},
            "b_fine": {
                "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}]
            },
            "c_broken": {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"extends": "also"}]}]},
        },
    )

    assert [issue.chain for issue in library.lint()] == ["a_broken", "c_broken"]


def test_lint_writes_nothing_and_reloads_nothing(tmp_path):
    library = write(
        tmp_path,
        {"tasks": {"base": agent_task()}},
        {"broken": {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"extends": "typo"}]}]}},
    )
    before = snapshot(tmp_path)
    # A library already in memory must not go back to disk: an edit landing
    # mid-lint would otherwise be reported against a file the caller never
    # loaded.
    edited = tmp_path / CHAINS_DIR / "broken.yaml"
    edited.write_text(
        yaml.safe_dump({"nodes": [{"id": "n", "kind": "exec", "tasks": [{"extends": "base"}]}]})
    )

    issues = library.lint()

    assert [i.chain for i in issues] == ["broken"]
    assert "typo" in issues[0].message
    # Content, not just the file list: a lint that rewrote a file in place --
    # normalising YAML, say -- would leave the names untouched and pass.
    after = snapshot(tmp_path)
    assert after.keys() == before.keys()
    assert {p: c for p, c in after.items() if p != edited} == {
        p: c for p, c in before.items() if p != edited
    }


# ── materialization (authored-resolved-and-materialized-chains-are-distinct) ──


def test_materialization_freezes_chain_policy_and_target():
    resolved = TemplateLibrary.from_yaml_dir(SEEDED).resolve_chain("default")
    target, effective = repository_target(), policy()

    materialized = resolved.materialize(target=target, effective_policy=effective)

    assert materialized.chain is resolved
    assert materialized.target == target
    assert materialized.policy == effective
    assert materialized.task_paths == resolved.task_paths


def test_a_materialized_chain_cannot_be_changed_while_the_item_executes():
    """`materialized-chain-is-immutable-work-item-input`: the snapshot, its
    target and the resolved chain it wraps all refuse assignment, so nothing
    an executor holds can retarget a running item."""
    materialized = (
        TemplateLibrary.from_yaml_dir(SEEDED)
        .resolve_chain("default")
        .materialize(target=repository_target(), effective_policy=policy())
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        materialized.target = workspace_target()
    with pytest.raises(dataclasses.FrozenInstanceError):
        materialized.policy = policy(timeout_minutes=120)
    with pytest.raises(dataclasses.FrozenInstanceError):
        materialized.chain = None
    with pytest.raises(ValidationError):
        materialized.target.members = ("other",)


def test_the_resolved_chain_is_reusable_across_work_items():
    """Two materializations of one resolved chain are distinct snapshots that
    share it -- the resolved stage carries no work-item state."""
    resolved = TemplateLibrary.from_yaml_dir(SEEDED).resolve_chain("default")

    one = resolved.materialize(target=repository_target(), effective_policy=policy())
    two = resolved.materialize(target=workspace_target(), effective_policy=policy())

    assert one.chain is two.chain
    assert one.target != two.target


def test_resolution_is_expansion_only_and_repeatable():
    """`resolved-template-is-deterministic`: resolving is inheritance and
    component expansion, nothing else. The same library resolves to the same
    chain every time, and the gates an attachment would satisfy are all still
    there -- that trimming is materialization's, and materialization has not
    happened."""
    library = TemplateLibrary.from_yaml_dir(SEEDED)

    once, twice = library.resolve_chain("default"), library.resolve_chain("default")

    assert once.chain == twice.chain
    assert once.task_paths == twice.task_paths
    # Every authored gate survives resolution, `spec_approval` and
    # `plan_approval` included -- the two an intake attachment satisfies.
    gates = [n.id for n in once.nodes if isinstance(n.node, GateNode)]
    assert "spec_approval" in gates
    assert "plan_approval" in gates
    # And the resolved stage has no work-item state to be deterministic about:
    # target and policy arrive only at materialization.
    assert not hasattr(once, "target")
    assert not hasattr(once, "policy")


# ── serialization (work-item-target-is-typed-and-immutable) ──


def test_a_materialized_chain_round_trips_through_its_own_json():
    materialized = (
        TemplateLibrary.from_yaml_dir(SEEDED)
        .resolve_chain("default")
        .materialize(target=workspace_target(), effective_policy=policy(timeout_minutes=120))
    )

    restored = MaterializedChain.from_json(materialized.to_json())

    assert restored.task_paths == materialized.task_paths
    assert restored.target == materialized.target
    assert restored.policy == materialized.policy
    assert restored.chain.chain == materialized.chain.chain


def test_serialization_is_deterministic():
    """Re-serializing a restored snapshot reproduces the same bytes, so a caller
    can compare two stored snapshots without normalising them first.

    Deliberately does not claim `resolved-template-is-deterministic` -- that
    requirement is about a resolved-template *response* being expansion only, and
    is pinned to `test_resolution_is_expansion_only_and_repeatable` instead."""
    stored = (
        TemplateLibrary.from_yaml_dir(SEEDED)
        .resolve_chain("default")
        .materialize(target=repository_target(), effective_policy=policy())
        .to_json()
    )

    assert MaterializedChain.from_json(stored).to_json() == stored


def test_the_stored_form_keeps_durations_in_their_authored_units():
    """A `timeout: 90m` that came back as `PT1H30M` would not re-read: the
    template duration grammar is whole s/m/h/d. Round-tripping through the
    authored form is what makes the column re-readable."""
    stored = (
        TemplateLibrary.from_yaml_dir(SEEDED)
        .resolve_chain("default")
        .materialize(target=repository_target(), effective_policy=policy())
        .to_json()
    )

    assert '"90m"' in stored
    assert "PT" not in stored


def test_the_target_selection_survives_serialization():
    """`work-item-target-selection-is-immutable` /
    `work-item-target-is-typed-and-immutable`: the selected members, the root
    choice and the pointer policy are captured at materialization and come back
    as the same typed target, not a dict."""
    target = workspace_target()
    materialized = (
        TemplateLibrary.from_yaml_dir(SEEDED)
        .resolve_chain("default")
        .materialize(target=target, effective_policy=policy())
    )

    restored = MaterializedChain.from_json(materialized.to_json()).target

    assert isinstance(restored, WorkItemTarget)
    assert restored.kind == "workspace"
    assert restored.workspace == "product"
    assert restored.members == ("api",)
    assert restored.include_root is False
    assert restored.root_pointer_policy is RootPointerPolicy.BUMP
    assert restored == target


def test_the_snapshot_does_not_carry_fork_lineage():
    """`RunFork.parent` (the `run_forks` row) is the only record of a fork's
    parent. A field on the snapshot as well would be a second source of truth
    nothing keeps equal to the fork's own row."""
    materialized = (
        TemplateLibrary.from_yaml_dir(SEEDED)
        .resolve_chain("default")
        .materialize(target=repository_target(), effective_policy=policy())
    )

    assert not hasattr(materialized, "run_parent")
    assert "run_parent" not in materialized.to_json()


def test_a_stored_snapshot_that_is_not_a_materialized_chain_is_an_error():
    with pytest.raises(TemplateLibraryError, match="not a materialized chain"):
        MaterializedChain.from_json('{"chain": {"nodes": []}}')


def test_lint_reports_a_chain_policy_the_instance_ceiling_refuses(tmp_path):
    """Kraft-ib2af: a chain `policy:` past the instance maxima is an authoring
    error lint names, not a 500 the first intake finds."""
    library = write(
        tmp_path,
        {"tasks": {"base": agent_task()}},
        {
            "wide": {
                "policy": {"allowed_tools": ["git", "rm_rf"]},
                "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}],
            },
            "fine": {
                "policy": {"allowed_tools": ["git"]},
                "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}],
            },
        },
    )

    assert library.lint() == []  # no ceiling given: nothing to exceed
    issues = library.lint(instance_policy=policy(allowed_tools=["git"]))

    assert [i.chain for i in issues] == ["wide"]
    assert issues[0].file == tmp_path / CHAINS_DIR / "wide.yaml"
    assert "rm_rf" in issues[0].message


def test_lint_reports_a_scope_its_chain_refuses_without_any_instance_ceiling(tmp_path):
    """Per-scope policy is checked by lint too, and needs no instance policy
    to be wrong: a task that widens its own node's `allowed_tools` can never
    materialize, whatever the instance allows. The library component's own
    `policy:` reaches the task through `extends`."""
    library = write(
        tmp_path,
        {"tasks": {"base": agent_task(policy={"allowed_tools": ["git", "rm_rf"]})}},
        {
            "narrow": {
                "nodes": [
                    {
                        "id": "n",
                        "kind": "exec",
                        "policy": {"allowed_tools": ["git"]},
                        "tasks": [{"id": "t", "extends": "base"}],
                    }
                ],
            },
        },
    )

    issues = library.lint()

    assert [i.chain for i in issues] == ["narrow"]
    assert issues[0].message.startswith("n.main.t: 'allowed_tools'"), issues[0].message


def test_a_task_fanned_out_to_a_repository_runs_under_that_repositorys_policy():
    """Kraft-jc39p: each selected repository's own policy is frozen with the
    snapshot, the chain's layer and the task's scopes applied over it exactly
    as over the assembled checkout's."""
    resolved = TemplateLibrary.from_yaml_dir(SEEDED).resolve_chain("default")
    assembled = policy().apply_template_override({"deny_tools": ["WebFetch"]})
    member = policy().apply_template_override({"deny_tools": ["Bash"]})

    materialized = MaterializedChain.from_json(
        resolved.materialize(
            target=workspace_target(),
            effective_policy=assembled,
            repository_policies={"api": member},
        ).to_json()
    )
    task = next(iter(materialized.chain.nodes[0].tasks()))

    assert materialized.policy_for(task).deny_tools == ("WebFetch",)
    assert materialized.policy_for(task, repository="api").deny_tools == ("Bash",)
    with pytest.raises(LookupError, match="nope"):
        materialized.policy_for(task, repository="nope")
