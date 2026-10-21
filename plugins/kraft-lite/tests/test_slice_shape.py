"""Slicing on any chain: the slice gate, first build node, MR node and child
chain read from the chain's declarations, never its node ids; what each node
means to a sliced controller; and a child started from the controller's
exported chain, walking it with the slice bindings layer."""

from __future__ import annotations

import copy
import json
import sys
import textwrap
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402
import kl_chains  # noqa: E402
import kl_shape  # noqa: E402

#: The default's child chain: the steps a slice builds, checks and opens a PR for.
DEFAULT_CHILD = (
    "implementation",
    "verification",
    "describe_merge_request",
    "draft_merge_request",
    "merge_request_feedback",
)

#: Chain files, by what they test, each resolved against the bundled library.
CHAIN_FILES = {
    # A plan gate and no chain revision: sliced at the plan's approval.
    "plan_gate_only": """\
        nodes:
          - {id: spec, kind: exec, tasks: [{id: t, kind: agent, prompt: S., produces: spec}]}
          - {id: plan, kind: exec, tasks: [{id: t, kind: agent, prompt: P., produces: plan}]}
          - {id: plan_approval, kind: gate, artifact: plan}
          - {id: impl, kind: exec, tasks: [{id: t, kind: agent, prompt: Build.}]}
          - id: verify
            kind: exec
            tasks: [{id: t, kind: builtin, ref: kraft.verify_changed_test_scopes}]
          - {id: open_mr, kind: exec, tasks: [{id: t, kind: forge, target: mr.open_draft}]}
        """,
    "no_mr": "extends: default\nremove: [draft_merge_request]\n",
    "finalized_slice_gate": """\
        nodes:
          - {id: plan, kind: exec, tasks: [{id: t, kind: agent, prompt: P., produces: plan}]}
          - {id: plan_approval, kind: gate, artifact: plan, chain_finalized: true}
          - {id: impl, kind: exec, tasks: [{id: t, kind: agent, prompt: Build.}]}
          - {id: open_mr, kind: exec, tasks: [{id: t, kind: forge, target: mr.open_draft}]}
        """,
    # Producing its description, the MR node is no build node.
    "mr_before_build": """\
        nodes:
          - {id: plan, kind: exec, tasks: [{id: t, kind: agent, prompt: P., produces: plan}]}
          - id: open_mr
            kind: exec
            tasks:
              - {id: describe, kind: agent, prompt: D., produces: mr_meta}
              - {id: open, kind: forge, target: mr.open_draft}
          - {id: plan_approval, kind: gate, artifact: plan}
          - {id: impl, kind: exec, tasks: [{id: t, kind: agent, prompt: Build.}]}
        """,
    "no_build": """\
        nodes:
          - {id: plan, kind: exec, tasks: [{id: t, kind: agent, prompt: P., produces: plan}]}
          - {id: plan_approval, kind: gate, artifact: plan}
        """,
}


def chain_file(where: Path, name: str) -> Path:
    path = where / f"{name}.yaml"
    path.write_text(textwrap.dedent(CHAIN_FILES[name]))
    return path


def renamed(chain: dict) -> dict:
    """`chain` with every node id renamed, and every reference to one with it."""
    names = {node["id"]: f"step{i}" for i, node in enumerate(chain["nodes"])}
    out = copy.deepcopy(chain)
    for node in out["nodes"]:
        node["id"] = names[node["id"]]
        if node["kind"] == "gate" and node["reject_to"]:
            node["reject_to"] = names[node["reject_to"]]
        elif node["kind"] == "exec" and node["on_base_changed"]:
            node["on_base_changed"]["restart_from"] = names[node["on_base_changed"]["restart_from"]]
    return out


@pytest.fixture
def chain_of(default_chain, tmp_path):
    """The resolved chain a test names: the bundled default, the default with
    every id renamed, a bundled library chain, or one of `CHAIN_FILES`."""

    def make(name: str) -> dict:
        if name == "bundled_default":
            return default_chain
        if name == "renamed_nodes":
            return renamed(default_chain)
        if name == "quick_task":
            return kl_chains.resolve(tmp_path, {}, "quick-task")
        return kl_chains.resolve(tmp_path, {}, path=chain_file(tmp_path, name))

    return make


@pytest.mark.parametrize(
    "name, shape",
    [
        pytest.param(
            "bundled_default",
            ("chain_revision_approval", "implementation", "draft_merge_request", DEFAULT_CHILD),
            id="bundled_default",
        ),
        pytest.param(
            "plan_gate_only",
            ("plan_approval", "impl", "open_mr", ("impl", "verify", "open_mr")),
            id="plan_gate_only",
        ),
        # By position and declaration, never by name.
        pytest.param(
            "renamed_nodes",
            ("step5", "step6", "step11", ("step6", "step7", "step10", "step11", "step12")),
            id="renamed_nodes",
        ),
    ],
)
def test_slice_shape(chain_of, name, shape):
    assert kl_shape.slice_shape(chain_of(name)) == kl_shape.SliceShape(*shape)


@pytest.mark.parametrize(
    "name, missing",
    [
        pytest.param("quick_task", "no gate before the first build node", id="quick_task_refused"),
        pytest.param(
            "no_mr", "no node opens a merge request (forge mr.open_draft)", id="no_mr_refused"
        ),
        pytest.param(
            "finalized_slice_gate",
            "the gate before the first build node is the chain_finalized gate",
            id="finalized_slice_gate_refused",
        ),
        pytest.param(
            "mr_before_build",
            "open_mr opens a merge request (forge mr.open_draft) before the first build node impl",
            id="mr_before_build_refused",
        ),
        pytest.param(
            "no_build",
            "no build node (an exec node whose tasks produce nothing)",
            id="no_build_refused",
        ),
    ],
)
def test_slice_shape_refuses(chain_of, name, missing):
    chain = chain_of(name)
    with pytest.raises(kl_chains.ChainError) as refused:
        kl_shape.slice_shape(chain)
    assert refused.value.detail == f"chain {chain['id']!r} cannot be sliced: {missing}"


#: The spec's table for the bundled default, typed out rather than derived, so
#: the test fails when the rules drift from the spec.
DEFAULT_ACTIONS = {
    "spec": ("run", "run"),
    "spec_approval": ("run", "run"),
    "plan": ("run", "run"),
    "plan_approval": ("run", "run"),
    "chain_revision": ("run", "run"),
    "chain_revision_approval": ("run", "run"),
    "implementation": ("run_slices", "run_slices"),
    "verification": ("scoped_verify", "skip"),
    "work_brief": ("run", "skip"),
    "local_review": ("run", "skip"),
    "describe_merge_request": ("run", "skip"),
    "draft_merge_request": ("run", "skip"),
    "merge_request_feedback": ("run", "skip"),
    "work_item_summary": ("run", "skip"),
    "final_review": ("run", "await_user_merges"),
    "mark_ready": ("run", "skip"),
    "external_approval": ("run", "skip"),
    "merge": ("run", "await_user_merges"),
    "post_merge_ci": ("run", "skip"),
}


def test_the_action_table_covers_the_default(default_chain):
    assert list(DEFAULT_ACTIONS) == [n["id"] for n in default_chain["nodes"]]


@pytest.mark.parametrize(
    "mode, node, action",
    [
        pytest.param(mode, node, actions[column], id=f"{mode}-{node}")
        for column, mode in enumerate(kl.MODES)
        for node, actions in DEFAULT_ACTIONS.items()
    ],
)
def test_sliced_action(default_chain, mode, node, action):
    shape = kl_shape.slice_shape(default_chain)
    assert kl_shape.sliced_action(default_chain, shape, node, mode) == action
    assert action in kl_shape.ACTIONS


def restarting_from(chain: dict, node: str, target: str) -> dict:
    out = copy.deepcopy(chain)
    [found] = [n for n in out["nodes"] if n["id"] == node]
    found["on_base_changed"] = {"restart_from": target}
    return out


@pytest.mark.parametrize(
    "target, redirected",
    [
        # Kept: left as it is.
        pytest.param("verification", "verification", id="kept"),
        # Dropped (it produces the local_review gate's artifact): the nearest
        # kept exec node before it.
        pytest.param("work_brief", "verification", id="dropped_node"),
        # Before the child's span: no kept node before it, so the child's first.
        pytest.param("plan", "implementation", id="outside_span"),
    ],
)
def test_child_chain_redirects_restart_from(default_chain, target, redirected):
    chain = restarting_from(default_chain, "merge_request_feedback", target)

    child = kl_shape.child_chain(chain)

    assert (child["format"], child["id"]) == ("v1", "default/slice")
    assert tuple(n["id"] for n in child["nodes"]) == DEFAULT_CHILD
    feedback = child["nodes"][-1]
    assert feedback["on_base_changed"] == {"restart_from": redirected}
    # Every other node is the controller's, as it froze it.
    by_id = {n["id"]: n for n in chain["nodes"]}
    assert all(n == by_id[n["id"]] for n in child["nodes"][:-1])


@pytest.mark.parametrize(
    "name, gate, marked",
    [
        pytest.param("default", "chain_revision_approval", True, id="default_slice_gate"),
        pytest.param("default", "spec_approval", False, id="default_other_gate"),
        pytest.param("plan_gate_only", "plan_approval", True, id="plan_gate_only"),
        # No shape, no slice gate: walked unsliced.
        pytest.param("no_mr", "chain_revision_approval", False, id="no_shape"),
    ],
)
def test_state_marks_the_slice_gate(walk, name, gate, marked, kl_cli, tmp_path):
    ref = "default" if name == "default" else str(chain_file(tmp_path, name))
    kl_cli(tmp_path, "start", "--title", "t", "--chain", ref)
    walk.to(tmp_path, gate)

    assert kl_cli(tmp_path, "state")["gate"]["slice_gate"] is marked


def test_a_chain_with_no_shape_refuses_slices_naming_what_is_missing(walk, kl_cli, tmp_path):
    kl_cli(tmp_path, "start", "--title", "t", "--chain", str(chain_file(tmp_path, "no_mr")))
    walk.to(tmp_path, "chain_revision_approval")
    kl_cli(tmp_path, "gate")

    refused = kl_cli(
        tmp_path, "approve", "--slices", str(walk.write_plan(tmp_path, walk.plan())), ok=False
    )

    assert (
        "kraft-lite: chain 'no_mr' cannot be sliced: "
        "no node opens a merge request (forge mr.open_draft)" in refused.stderr
    )
    assert kl_cli(tmp_path, "state")["status"] == "blocked"
    # Walked on unsliced, as any chain is.
    assert kl_cli(tmp_path, "approve")["node"] == "implementation"


def test_a_plan_gate_chain_slices_at_its_plan_gate(walk, kl_cli, tmp_path):
    ref = str(chain_file(tmp_path, "plan_gate_only"))
    kl_cli(tmp_path, "start", "--title", "t", "--chain", ref)
    walk.to(tmp_path, "plan_approval")
    kl_cli(tmp_path, "gate")

    state = kl_cli(tmp_path, "approve", "--slices", str(walk.write_plan(tmp_path, walk.plan())))

    assert (state["node"], state["sliced"]) == ("impl", {"mode": "feature", "action": "run_slices"})
    walk.merge_slices(tmp_path)
    assert kl_cli(tmp_path, "close")["sliced"]["action"] == "scoped_verify"


def installed_skill(root: Path, plugin: str, skill: str) -> None:
    """`plugin:skill` installed in `root`'s `.claude/skills`."""
    base = root / ".claude" / "skills" / plugin
    (base / ".claude-plugin").mkdir(parents=True)
    (base / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": plugin}))
    (base / skill).mkdir()
    (base / skill / "SKILL.md").write_text(f"---\nname: {skill}\n---\n")


def exported(walk, kl_cli, tmp_path) -> tuple[Path, Path, str]:
    """A controller sliced and exported: its directory, the export, its chain id."""
    controller, out = tmp_path / "controller", tmp_path / "state"
    controller.mkdir()
    chain_id = walk.slice(controller)
    kl_cli(controller, "export", "--to", str(out))
    return controller, out, chain_id


def test_child_start_uses_slice_layer(walk, kl_cli, default_chain, tmp_path):
    controller, out, chain_id = exported(walk, kl_cli, tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    installed_skill(child, "superpowers", "subagent-driven-development")

    started = kl_cli(child, "start", "--slice-of", str(out / "chain.json"), "--title", "Slice a")

    frozen = json.loads(kl._frozen_path(child, started["chain_id"]).read_text())
    assert frozen == kl_shape.child_chain(default_chain)
    state = kl_cli(child, "state")
    assert (state["node"], state["sliced"], state["gate"]) == ("implementation", None, None)
    [implementer] = state["steps"][0]["tasks"]
    assert implementer["source"] == "slice-default:tasks.implementer"
    assert implementer["skill"] == "superpowers:subagent-driven-development"
    kl_cli(child, "close")
    [tests] = kl_cli(child, "state")["steps"][0]["tasks"]
    assert tests["source"] == "slice-default:builtins.kraft.verify_changed_test_scopes"
    # The controller's own walk takes no slice layer.
    installed_skill(controller, "superpowers", "subagent-driven-development")
    [own] = kl_cli(controller, "state")["steps"][0]["tasks"]
    assert own["source"] == "default:agent"
    # A child chain is never sliced again.
    refused = kl_cli(child, "approve", "--slices", str(walk.write_plan(child)), ok=False)
    assert "a slice's child chain is never sliced" in refused.stderr


@pytest.mark.parametrize(
    "write, args, named",
    [
        pytest.param(
            lambda p: p.write_text(json.dumps({"id": "slice", "nodes": []})),
            [],
            "was written by an older Kraft Lite: not a V1 chain",
            id="not_v1",
        ),
        pytest.param(lambda p: None, [], "cannot read", id="no_such_file"),
        pytest.param(lambda p: p.write_text("{nope"), [], "cannot read", id="not_json"),
        pytest.param(
            lambda p: p.write_text(json.dumps({"format": "v1", "id": "slice"})),
            [],
            "is not a usable V1 chain: it has no list of nodes",
            id="v1_without_nodes",
        ),
        pytest.param(
            lambda p: p.write_text(
                json.dumps({"format": "v1", "id": "x", "nodes": [{"id": "a", "steps": []}]})
            ),
            [],
            "is not a usable V1 chain: node 'a' has no kind (exec or gate)",
            id="v1_node_without_kind",
        ),
        pytest.param(
            lambda p: p.write_text(
                json.dumps({"format": "v1", "id": "x", "nodes": [{"kind": "gate"}]})
            ),
            [],
            "is not a usable V1 chain: node 1 has no id",
            id="v1_node_without_id",
        ),
        pytest.param(
            lambda p: p.write_text(
                json.dumps({"format": "v1", "id": "x", "nodes": [{"id": "a", "kind": "exec"}]})
            ),
            [],
            "is not a usable V1 chain: exec node 'a' has no steps",
            id="v1_exec_without_steps",
        ),
        pytest.param(
            lambda p: None,
            ["--chain", "default"],
            "--slice-of takes no --chain, --spec or --plan",
            id="with_chain",
        ),
        pytest.param(
            lambda p: p.write_text(json.dumps(kl_chains.resolve(p.parent, {}, "quick-task"))),
            [],
            "chain 'quick-task' cannot be sliced: no gate before the first build node",
            id="unsliceable",
        ),
    ],
)
def test_start_slice_of_refuses(write, args, named, kl_cli, tmp_path):
    path = tmp_path / "chain.json"
    write(path)

    refused = kl_cli(tmp_path, "start", "--slice-of", str(path), "--title", "t", *args, ok=False)

    assert named in refused.stderr and "Traceback" not in refused.stderr
    assert not (tmp_path / ".kraft-lite" / "chains").exists()


@pytest.mark.parametrize("case", ["export_has_chain_json", "import_refuses_old_export"])
def test_the_export_carries_the_chain_a_child_starts_from(
    walk, kl_cli, default_chain, case, tmp_path
):
    controller, out, chain_id = exported(walk, kl_cli, tmp_path)
    there = tmp_path / "there"
    there.mkdir()
    frozen = json.loads(kl._frozen_path(controller, chain_id).read_text())

    if case == "export_has_chain_json":
        assert json.loads((out / "chain.json").read_text()) == frozen == default_chain
        kl_cli(there, "import", "--from", str(out))
        assert json.loads(kl._frozen_path(there, chain_id).read_text()) == frozen
        return
    # As an export from before `chain.json` was: the chain under `chains/`.
    (out / "chains").mkdir()
    (out / "chain.json").rename(out / "chains" / f"{chain_id}.json")
    refused = kl_cli(there, "import", "--from", str(out), ok=False)
    assert f"kraft-lite: {out} was exported by an older Kraft Lite: no chain.json" in refused.stderr
    assert not (there / ".kraft-lite").exists()
