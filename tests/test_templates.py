import json
from pathlib import Path

import pytest
import yaml

from kraft import templates
from kraft.templates import ATTACHMENT_GATES, GATE_NAMES, Template, materialize

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def test_shipped_yaml_parses_and_matches_spec():
    quick = yaml.safe_load((TEMPLATES_DIR / "quick-task.yaml").read_text())
    assert quick["id"] == "quick-task"
    assert [n["id"] for n in quick["nodes"]] == ["env_setup", "implementation", "verify"]
    assert [n["tasks"] for n in quick["nodes"]] == [
        ["on.env.prepare"],
        ["on.implementation.start"],
        ["on.test.run"],
    ]
    assert all(n["gate_after"] is None for n in quick["nodes"])

    registry = yaml.safe_load((TEMPLATES_DIR / "registry.yaml").read_text())
    assert {
        "on.env.prepare",
        "on.implementation.start",
        "on.test.run",
    } <= set(registry["hooks"])
    assert registry["hooks"]["on.spec.requested"] == {
        "kind": "agent",
        "command": "claude",
        "skill": "spec",
        "artifact": "spec",
    }
    assert registry["hooks"]["on.plan.requested"] == {
        "kind": "agent",
        "command": "claude",
        "skill": "plan",
        "artifact": "plan",
    }
    # The back half is no longer noop (Kraft-33j). backend is glab because this
    # repo's origin is gitlab.com; the public repo after the split uses gh.
    assert registry["hooks"]["on.merge"] == {
        "kind": "forge",
        "handler": "merge",
        "backend": "glab",
    }
    assert registry["hooks"]["on.mr.open"] == {
        "kind": "forge",
        "handler": "open_mr",
        "backend": "glab",
    }
    assert registry["hooks"]["on.ci.poll"] == {
        "kind": "forge",
        "handler": "ci_poll",
        "backend": "glab",
    }
    assert registry["hooks"]["on.human_review.requested"] == {
        "kind": "agent",
        "command": "claude",
        "skill": "review-brief",
        "artifact": "review_brief",
    }
    assert registry["hooks"]["on.env.prepare"] == {"kind": "builtin", "handler": "env_setup"}
    assert registry["hooks"]["on.implementation.start"] == {"kind": "agent", "command": "claude"}
    assert registry["hooks"]["on.test.run"] == {
        "kind": "subprocess",
        "command": ["uv", "run", "pytest", "-q"],
    }


REGISTRY_YAML = """\
hooks:
  on.env.prepare:          { kind: builtin,    handler: env_setup }
  on.implementation.start: { kind: agent,      command: claude }
  on.test.run:             { kind: subprocess, command: [pytest, -q] }
"""

GOOD_TEMPLATE = """\
id: quick-task
nodes:
  - { id: env_setup,      tasks: [on.env.prepare],         gate_after: null }
  - { id: implementation, tasks: [on.implementation.start], gate_after: null }
  - { id: verify,         tasks: [on.test.run],            gate_after: null }
"""

BAD_HOOK_TEMPLATE = """\
id: broken
nodes:
  - { id: n1, tasks: [on.bogus], gate_after: null }
"""


def test_policy_yaml_is_not_scanned_as_a_template():
    """policy.yaml sits in the templates dir but is not a chain template, so it
    must not surface as an invalid one and degrade /health (Kraft-2ih)."""
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    ts = templates.load_templates(TEMPLATES_DIR, reg)
    assert "policy" not in ts.invalid
    assert "policy" not in ts.valid


def test_shipped_default_yaml_is_the_eleven_node_chain():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    ts = templates.load_templates(TEMPLATES_DIR, reg)
    assert "default" in ts.valid, ts.invalid
    nodes = ts.valid["default"].nodes
    assert [n["id"] for n in nodes] == [
        "spec",
        "plan",
        "chain_review",
        "env_setup",
        "implementation",
        "verify",
        "open_mr",
        "mr_checks",
        "human_review",
        "mr_sync",
        "merge",
    ]
    gates = {n["id"]: n.get("gate_after") for n in nodes}
    assert gates["spec"] == "spec_approval"
    assert gates["plan"] == "plan_approval"
    assert gates["chain_review"] == "chain_finalized"
    assert gates["human_review"] == "human_review_approval"
    assert gates["env_setup"] is None and gates["merge"] is None and gates["mr_sync"] is None


def test_the_default_chain_syncs_the_mr_after_the_review_gate():
    """§5. `_measure_node` runs a node's tasks concurrently, so a sync sharing
    the human_review node could push before the brief was written -- and after
    Kraft-nh5m the pushed head is what merge is checked against. The gate stays
    on the node carrying the brief, because `_gate_artifact` scans the gate
    node's own tasks."""
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    nodes = templates.load_templates(TEMPLATES_DIR, reg).valid["default"].nodes
    at = {n["id"]: i for i, n in enumerate(nodes)}

    sync = next(n for n in nodes if "on.mr.sync" in n["tasks"])
    gate = next(n for n in nodes if n.get("gate_after") == "human_review_approval")

    assert sync["tasks"] == ["on.mr.sync"], "the sync still shares a node with another task"
    assert "on.human_review.requested" in gate["tasks"]
    assert "on.mr.sync" not in gate["tasks"]
    assert at[gate["id"]] < at[sync["id"]] < at["merge"]


def test_unknown_gate_after_quarantines_template(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "quick-task.yaml": GOOD_TEMPLATE,
            "weirdgate.yaml": (
                "id: weirdgate\n"
                "nodes:\n"
                "  - { id: n1, tasks: [on.test.run], gate_after: bogus_gate }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "quick-task" in ts.valid
    assert "weirdgate" in ts.invalid
    assert "bogus_gate" in ts.invalid["weirdgate"]


def _dir(tmp_path, **files):
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    return tmp_path


def test_load_registry_ok(tmp_path):
    d = _dir(tmp_path, **{"registry.yaml": REGISTRY_YAML})
    reg = templates.load_registry(d / "registry.yaml")
    assert set(reg.hooks) == {"on.env.prepare", "on.implementation.start", "on.test.run"}


def test_load_registry_rejects_an_unknown_key(tmp_path):
    """A misspelt key would configure nothing and fail nowhere — the same
    failure an unknown profile is already rejected for (Kraft-fza)."""
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, deny_tool: Bash }\n"
    )
    with pytest.raises(templates.RegistryError, match="deny_tool"):
        templates.load_registry(tmp_path / "registry.yaml")

    # and the agent-only keys stay rejected on other kinds, by their own message
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: subprocess, command: [pytest], model: opus }\n"
    )
    with pytest.raises(templates.RegistryError, match="only to an agent hook"):
        templates.load_registry(tmp_path / "registry.yaml")


@pytest.mark.parametrize(
    "body",
    [
        "hooks:\n  on.x: { handler: foo }\n",  # missing kind
        "hooks:\n  on.x: { kind: wat, handler: foo }\n",  # unknown kind
        "hooks:\n  on.x: { kind: builtin }\n",  # builtin needs handler
        "hooks:\n  on.x: { kind: subprocess, command: pytest }\n",  # subprocess needs list
        "hooks:\n  on.x: { kind: agent, command: [claude] }\n",  # agent needs string
    ],
)
def test_load_registry_rejects_bad_bindings(tmp_path, body):
    (tmp_path / "registry.yaml").write_text(body)
    with pytest.raises(templates.RegistryError):
        templates.load_registry(tmp_path / "registry.yaml")


# ── new agent-hook keys: profile, model, deny_tools, steering ──────────────────


def test_agent_hook_without_new_keys_loads_exactly_as_before(tmp_path):
    d = _dir(tmp_path, **{"registry.yaml": REGISTRY_YAML})
    reg = templates.load_registry(d / "registry.yaml")
    assert reg.hooks["on.implementation.start"] == {"kind": "agent", "command": "claude"}


def test_load_registry_rejects_unknown_profile(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, profile: nope }\n"
    )
    with pytest.raises(templates.RegistryError, match="nope") as exc_info:
        templates.load_registry(tmp_path / "registry.yaml")
    assert "claude" in str(exc_info.value)


def test_load_registry_rejects_non_string_model(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, model: 3 }\n"
    )
    with pytest.raises(templates.RegistryError):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_accepts_any_model_string(tmp_path):
    """Kraft has no model list; an unknown-looking model string is not its job to reject."""
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, model: anything-at-all }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert reg.hooks["on.x"]["model"] == "anything-at-all"


def test_load_registry_rejects_deny_tools_not_a_list(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, deny_tools: WebFetch }\n"
    )
    with pytest.raises(templates.RegistryError):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_rejects_steering_naming_a_missing_file(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, steering: [missing] }\n"
    )
    with pytest.raises(templates.RegistryError):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_does_not_mutate_the_binding(tmp_path):
    """Regression guard: `GET /registry` hands out this exact dict and Settings
    -> Plugins PUTs it back unedited. Any key load_registry writes into it gets
    inlined into the operator's config on the next save."""
    (tmp_path / "steering").mkdir()
    (tmp_path / "steering" / "house-style.md").write_text("# House style\nBe direct.\n")
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, steering: [house-style] }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    binding = reg.hooks["on.x"]
    assert binding == {"kind": "agent", "command": "claude", "steering": ["house-style"]}
    assert "steering_texts" not in binding
    assert binding["steering"] == ["house-style"]
    assert "profile" not in binding


def test_load_registry_rejects_model_on_a_subprocess_hook(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: subprocess, command: [pytest], model: claude-x }\n"
    )
    with pytest.raises(templates.RegistryError):
        templates.load_registry(tmp_path / "registry.yaml")


def test_unknown_hook_quarantines_only_that_template(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "quick-task.yaml": GOOD_TEMPLATE,
            "broken.yaml": BAD_HOOK_TEMPLATE,
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)

    assert "quick-task" in ts.valid
    assert "broken" in ts.invalid
    assert "on.bogus" in ts.invalid["broken"]


def test_shape_failures_and_dup_ids_quarantine(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "quick-task.yaml": GOOD_TEMPLATE,
            "noid.yaml": "nodes: []\n",
            "dup.yaml": GOOD_TEMPLATE,  # id: quick-task again
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)

    assert "quick-task" in ts.valid
    assert "noid" in ts.invalid
    assert "quick-task" in ts.invalid  # the later file, quarantined
    assert "duplicate" in ts.invalid["quick-task"].lower()


def test_materialize_quick_task_from_shipped_templates():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    ts = templates.load_templates(TEMPLATES_DIR, reg)
    chain = templates.materialize(ts.valid["quick-task"])

    assert chain == {
        "template_id": "quick-task",
        "nodes": [
            {
                "id": "env_setup",
                "tasks": ["on.env.prepare"],
                "gate_after": None,
                "fix_loop": None,
            },
            {
                "id": "implementation",
                "tasks": ["on.implementation.start"],
                "gate_after": None,
                "fix_loop": None,
            },
            {"id": "verify", "tasks": ["on.test.run"], "gate_after": None, "fix_loop": None},
        ],
    }
    assert "current_node_id" not in chain
    assert json.loads(json.dumps(chain)) == chain  # round-trips


def test_default_yaml_verify_node_has_fix_loop():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    ts = templates.load_templates(TEMPLATES_DIR, reg)
    assert "default" in ts.valid, ts.invalid
    verify = next(n for n in ts.valid["default"].nodes if n["id"] == "verify")
    assert verify["fix_loop"] == "verify_fix_loop"


def test_materialize_carries_fix_loop():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    tmpl = templates.load_templates(TEMPLATES_DIR, reg).valid["default"]
    mat = templates.materialize(tmpl)
    by_id = {n["id"]: n for n in mat["nodes"]}
    assert by_id["verify"]["fix_loop"] == "verify_fix_loop"
    assert by_id["env_setup"]["fix_loop"] is None


def test_non_string_fix_loop_quarantines_template(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "quick-task.yaml": GOOD_TEMPLATE,
            "badloop.yaml": (
                "id: badloop\nnodes:\n  - { id: n1, tasks: [on.test.run], fix_loop: 123 }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "quick-task" in ts.valid
    assert "badloop" in ts.invalid
    assert "fix_loop" in ts.invalid["badloop"]


def test_fix_loop_with_no_tasks_quarantines_template(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "emptyloop.yaml": (
                "id: emptyloop\nnodes:\n  - { id: n1, tasks: [], fix_loop: n1_fix_loop }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "emptyloop" in ts.invalid
    assert "fix_loop" in ts.invalid["emptyloop"]


def test_config_files_in_the_templates_dir_are_not_read_as_templates(tmp_path):
    """Settings writes repos.yaml and access.yaml next to the chain templates.
    Reading those as malformed templates is how the whole app ends up reporting
    degraded health for no reason."""
    from support.harness import fake_templates_dir

    d = fake_templates_dir(tmp_path, "claude")
    (d / "repos.yaml").write_text("repos: []\n")
    (d / "access.yaml").write_text("bind: 127.0.0.1\n")
    registry = templates.load_registry(d / "registry.yaml")
    loaded = templates.load_templates(d, registry)
    assert loaded.invalid == {}
    assert set(loaded.valid) == {"quick-task", "default"}


def test_materialize_drops_nodes_whose_gate_is_satisfied():
    template = Template(
        id="t",
        nodes=[
            {"id": "spec", "tasks": ["on.spec.requested"], "gate_after": "spec_approval"},
            {"id": "plan", "tasks": ["on.plan.requested"], "gate_after": "plan_approval"},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
        ],
    )
    out = materialize(template, satisfied_gates=frozenset({"spec_approval", "plan_approval"}))
    assert [n["id"] for n in out["nodes"]] == ["implementation"]


def test_materialize_keys_on_the_gate_not_the_node_id():
    # A custom template may name the node anything; the gate is the vocabulary.
    template = Template(
        id="t",
        nodes=[
            {"id": "write-the-plan", "tasks": ["on.plan.requested"], "gate_after": "plan_approval"},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
        ],
    )
    out = materialize(template, satisfied_gates=frozenset({"plan_approval"}))
    assert [n["id"] for n in out["nodes"]] == ["implementation"]


def test_materialize_without_satisfied_gates_is_unchanged():
    template = Template(
        id="t",
        nodes=[{"id": "spec", "tasks": ["on.spec.requested"], "gate_after": "spec_approval"}],
    )
    assert [n["id"] for n in materialize(template)["nodes"]] == ["spec"]


def test_materialize_on_a_template_without_those_gates_is_a_noop():
    template = Template(
        id="quick",
        nodes=[{"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None}],
    )
    out = materialize(template, satisfied_gates=frozenset({"plan_approval"}))
    assert [n["id"] for n in out["nodes"]] == ["implementation"]


def test_attachment_gates_are_real_gate_names():
    assert set(ATTACHMENT_GATES.values()) <= GATE_NAMES


def test_a_registry_with_invalid_bytes_is_a_registry_error(tmp_path):
    """`read_text()` raises `UnicodeDecodeError` — a `ValueError`, not an
    `OSError` and not a `yaml.YAMLError`. `lifespan` and the settings-save path
    catch only `RegistryError`, so anything else escaping here refuses to boot
    the server with no usable message."""
    p = tmp_path / "registry.yaml"
    p.write_bytes(b"hooks:\n  on.env.prepare: { kind: builtin, handler: \xff\xfe }\n")
    with pytest.raises(templates.RegistryError):
        templates.load_registry(p)


def test_a_template_with_invalid_bytes_is_quarantined_not_a_crash(tmp_path):
    """`load_templates` quarantines a bad file rather than raising; a file that
    is not UTF-8 is just another kind of bad file."""
    d = _dir(tmp_path, **{"registry.yaml": REGISTRY_YAML})
    (d / "broken.yaml").write_bytes(b"id: broken\nnodes: \xff\xfe\n")
    registry = templates.load_registry(d / "registry.yaml")
    result = templates.load_templates(d, registry)
    assert "broken" in result.invalid


def test_load_registry_accepts_escalate_model_on_an_agent_hook(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, model: sonnet, escalate_model: opus }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert reg.hooks["on.x"]["escalate_model"] == "opus"


def test_load_registry_rejects_escalate_model_that_is_not_a_string(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, escalate_model: 3 }\n"
    )
    with pytest.raises(templates.RegistryError, match="escalate_model"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_rejects_escalate_model_on_a_subprocess_hook(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: subprocess, command: [pytest], escalate_model: opus }\n"
    )
    with pytest.raises(templates.RegistryError, match="only to an agent hook"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_an_agent_hook_accepts_skill_and_artifact(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n"
        "  on.spec.requested: { kind: agent, command: claude, "
        "skill: chain-review, artifact: spec }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml", skills_dir=tmp_path / "skills")
    assert reg.hooks["on.spec.requested"]["artifact"] == "spec"


def test_an_unknown_skill_fails_the_registry(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.spec.requested: { kind: agent, command: claude, skill: nope }\n"
    )
    with pytest.raises(templates.RegistryError, match="nope"):
        templates.load_registry(tmp_path / "registry.yaml", skills_dir=tmp_path / "skills")


def test_a_bad_artifact_kind_fails_the_registry(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.spec.requested: { kind: agent, command: claude, artifact: ../etc }\n"
    )
    with pytest.raises(templates.RegistryError, match="artifact"):
        templates.load_registry(tmp_path / "registry.yaml", skills_dir=tmp_path / "skills")


def test_skill_and_artifact_are_agent_only(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.merge: { kind: builtin, handler: noop, skill: spec }\n"
    )
    with pytest.raises(templates.RegistryError, match="only to an agent hook"):
        templates.load_registry(tmp_path / "registry.yaml", skills_dir=tmp_path / "skills")


def test_forge_binding_loads(tmp_path):
    p = tmp_path / "registry.yaml"
    p.write_text("hooks:\n  on.mr.open: {kind: forge, handler: open_mr, backend: glab}\n")
    registry = templates.load_registry(p)
    assert registry.hooks["on.mr.open"]["backend"] == "glab"


def test_forge_binding_needs_a_known_handler(tmp_path):
    p = tmp_path / "registry.yaml"
    p.write_text("hooks:\n  on.mr.open: {kind: forge, handler: teleport, backend: glab}\n")
    with pytest.raises(templates.RegistryError, match="teleport"):
        templates.load_registry(p)


def test_forge_binding_needs_a_known_backend(tmp_path):
    """Named, never probed — so an unknown name has to fail at load, not at
    dispatch three nodes into a chain."""
    p = tmp_path / "registry.yaml"
    p.write_text("hooks:\n  on.mr.open: {kind: forge, handler: open_mr, backend: bitbucket}\n")
    with pytest.raises(templates.RegistryError, match="bitbucket"):
        templates.load_registry(p)


# ── poll keys on a ci_poll forge hook ─────────────────────────────────────────


def test_ci_poll_hook_accepts_poll_keys(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.ci.poll: { kind: forge, handler: ci_poll, backend: glab, "
        "poll_timeout: 600, poll_interval: 10 }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert reg.hooks["on.ci.poll"]["poll_timeout"] == 600


def test_poll_keys_rejected_on_a_forge_hook_that_never_polls(tmp_path):
    """open_mr would read neither key: a setting that silently does nothing."""
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.mr.open: { kind: forge, handler: open_mr, backend: glab, "
        "poll_timeout: 600 }\n"
    )
    with pytest.raises(templates.RegistryError, match="only to a ci_poll handler"):
        templates.load_registry(tmp_path / "registry.yaml")


@pytest.mark.parametrize("value", ["-1", "never", "true"])
def test_poll_timeout_must_be_a_non_negative_number(tmp_path, value):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.ci.poll: { kind: forge, handler: ci_poll, backend: glab, "
        f"poll_timeout: {value} }}\n"
    )
    with pytest.raises(templates.RegistryError, match="non-negative number"):
        templates.load_registry(tmp_path / "registry.yaml")


@pytest.mark.parametrize("key", ["poll_timeout", "poll_interval"])
@pytest.mark.parametrize("value", [".inf", ".nan"])
def test_poll_keys_reject_infinity_and_nan(tmp_path, key, value):
    """`.inf` is a node that never returns and never frees its intake slot;
    `.nan` goes straight into asyncio.sleep."""
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.ci.poll: { kind: forge, handler: ci_poll, backend: glab, "
        f"{key}: {value} }}\n"
    )
    with pytest.raises(templates.RegistryError):
        templates.load_registry(tmp_path / "registry.yaml")


@pytest.mark.parametrize("value", ["0", "-1", "never", "true"])
def test_poll_interval_must_be_positive(tmp_path, value):
    """Zero here is a hot loop: the forge CLI re-run as fast as a thread
    returns, for the whole timeout."""
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.ci.poll: { kind: forge, handler: ci_poll, backend: glab, "
        f"poll_interval: {value} }}\n"
    )
    with pytest.raises(templates.RegistryError, match="positive number"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_accepts_effort_on_an_agent_hook(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, model: opus, effort: high }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert reg.hooks["on.x"]["effort"] == "high"


def test_load_registry_rejects_an_effort_level_the_cli_does_not_take(tmp_path):
    """Caught at config load, not mid-run: a typo here would otherwise reach
    the CLI as an unknown flag value and fail a node that had already been
    paid for."""
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, effort: highest }\n"
    )
    with pytest.raises(templates.RegistryError, match="effort"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_rejects_effort_on_a_subprocess_hook(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: subprocess, command: [pytest], effort: high }\n"
    )
    with pytest.raises(templates.RegistryError, match="effort"):
        templates.load_registry(tmp_path / "registry.yaml")
