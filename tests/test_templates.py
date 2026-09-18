import json
from pathlib import Path

import pytest
import yaml

from kraft import harness, skill, templates
from kraft.templates import (
    ATTACHMENT_GATES,
    GATE_NAMES,
    Template,
    load_registry,
    materialize,
)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
SKILLS_DIR = Path(__file__).parent.parent / "src" / "kraft" / "skills"


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
    assert registry["defaults"] == {
        "agent": {"steering": ["never-signal-processes-you-didnt-start"]}
    }
    assert {
        "on.env.prepare",
        "on.implementation.start",
        "on.test.run",
    } <= set(registry["hooks"])
    assert registry["hooks"]["on.spec.requested"] == {
        "kind": "agent",
        "harness": "claude",
        "skill": "spec",
        "artifact": "spec",
    }
    assert registry["hooks"]["on.plan.requested"] == {
        "kind": "agent",
        "harness": "claude",
        "skill": "plan",
        "artifact": "plan",
    }
    # The back half is no longer noop (Kraft-33j). `auto` and not a CLI name:
    # the registry is per install, the forge is a property of the repo, and
    # `forge.backend_for` resolves it per dispatch from the repo's repos.yaml
    # entry.
    assert registry["hooks"]["on.merge"] == {
        "kind": "forge",
        "handler": "merge",
        "backend": "auto",
    }
    assert registry["hooks"]["on.mr.open"] == {
        "kind": "forge",
        "handler": "open_mr",
        "backend": "auto",
    }
    assert registry["hooks"]["on.ci.poll"] == {
        "kind": "forge",
        "handler": "ci_poll",
        "backend": "auto",
        "on_failure": ["on.ci.repair"],
    }
    assert registry["hooks"]["on.human_review.requested"] == {
        "kind": "agent",
        "harness": "claude",
        "skill": "review-brief",
        "artifact": "review_brief",
    }
    assert registry["hooks"]["on.env.prepare"] == {"kind": "builtin", "handler": "env_setup"}
    assert registry["hooks"]["on.implementation.start"] == {
        "kind": "agent",
        "harness": "claude",
    }
    assert registry["hooks"]["on.test.run"] == {
        "kind": "subprocess",
        "command": ["uv", "run", "pytest", "-q"],
    }


def test_shipped_registry_merges_its_own_defaults():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    for hook_id in (
        "on.spec.requested",
        "on.plan.requested",
        "on.implementation.start",
        "on.chain.review_ready",
        "on.review.local.run",
        "on.review.security.run",
        "on.human_review.requested",
        "on.ci.repair",
        "on.fix_loop.judge",
        "on.mr.describe",
    ):
        assert reg.hooks[hook_id]["steering"] == ["never-signal-processes-you-didnt-start"], hook_id


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

_TWO_AGENT_HOOKS_REGISTRY = """\
hooks:
  on.a: { kind: agent, command: claude }
  on.b: { kind: agent, command: claude, steering: [own] }
  on.c: { kind: subprocess, command: [pytest] }
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


def test_shipped_default_yaml_is_the_fourteen_node_chain():
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
        "pre_mr_rebase",
        "mr_meta",
        "open_mr",
        "mr_checks",
        "human_review",
        "mr_sync",
        "merge",
        "post_merge_watch",
    ]
    gates = {n["id"]: n.get("gate_after") for n in nodes}
    assert gates["spec"] == "spec_approval"
    assert gates["plan"] == "plan_approval"
    assert gates["chain_review"] == "chain_finalized"
    assert gates["human_review"] == "human_review_approval"
    assert gates["env_setup"] is None and gates["merge"] is None and gates["mr_sync"] is None
    assert gates["post_merge_watch"] is None

    by_id = {n["id"]: n for n in nodes}
    assert by_id["implementation"]["tasks"] == ["on.implementation.start", "on.repos.scan"]
    assert by_id["pre_mr_rebase"]["rebase_bounce_to"] == "verify"
    assert by_id["pre_mr_rebase"]["tasks"] == ["on.mr.rebase"]
    assert by_id["post_merge_watch"]["tasks"] == ["on.merge.watch"]
    assert "fix_loop" not in by_id["post_merge_watch"]


def test_the_shipped_chain_scans_submodules_as_implementations_second_step():
    """Kraft-ilff3's real fix. The interim repos_scan node was a stopgap for a
    node that could not express 'after'."""
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    ts = templates.load_templates(TEMPLATES_DIR, reg)
    nodes = {n["id"]: n for n in ts.valid["default"].nodes}
    assert "repos_scan" not in nodes, "the interim node is gone"
    assert nodes["implementation"]["steps"] == [
        ["on.implementation.start"],
        ["on.repos.scan"],
    ]


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


def test_shipped_default_chain_describes_before_it_opens():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    nodes = templates.load_templates(TEMPLATES_DIR, reg).valid["default"].nodes
    ids = [n["id"] for n in nodes]
    assert ids.index("mr_meta") == ids.index("open_mr") - 1
    assert ids.index("pre_mr_rebase") < ids.index("mr_meta")
    assert nodes[ids.index("mr_meta")]["tasks"] == ["on.mr.describe"]


def test_shipped_registry_binds_the_describe_hook():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    binding = reg.hooks["on.mr.describe"]
    assert binding["kind"] == "agent"
    assert binding["skill"] == "mr-metadata"
    assert binding["artifact"] == "mr_meta"


def test_shipped_default_chain_validates():
    # `templates.validate_nodes` is what the server runs on load; a node the
    # shipped chain names must survive it.
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    nodes = templates.load_templates(TEMPLATES_DIR, reg).valid["default"].nodes
    templates.validate_nodes(nodes, reg)


# ── validate_nodes: the splice-tier validator (Kraft-unk) ──────────────────


def _registry(tmp_path):
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML)
    return templates.load_registry(tmp_path / "registry.yaml")


def test_validate_nodes_accepts_a_good_node_list(tmp_path):
    nodes = [
        {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
        {"id": "verify", "tasks": ["on.test.run"], "gate_after": None},
    ]
    assert templates.validate_nodes(nodes, _registry(tmp_path)) == []


def test_validate_nodes_rejects_an_unregistered_hook(tmp_path):
    nodes = [{"id": "n1", "tasks": ["on.bogus"], "gate_after": None}]
    errs = templates.validate_nodes(nodes, _registry(tmp_path))
    assert errs and "on.bogus" in errs[0]


def test_validate_nodes_rejects_an_unknown_gate_after(tmp_path):
    nodes = [{"id": "n1", "tasks": ["on.test.run"], "gate_after": "bogus_gate"}]
    errs = templates.validate_nodes(nodes, _registry(tmp_path))
    assert errs and "bogus_gate" in errs[0]


def test_validate_nodes_rejects_a_fix_loop_node_with_no_tasks(tmp_path):
    nodes = [{"id": "n1", "tasks": [], "fix_loop": "n1_fix_loop", "gate_after": None}]
    errs = templates.validate_nodes(nodes, _registry(tmp_path))
    assert errs and "fix_loop" in errs[0]


def test_validate_nodes_rejects_a_bare_string_on_failure(tmp_path):
    """Kraft-df4tc: the splice path never runs load_templates' bad_recover
    check, so validate_nodes itself must catch a string on_failure -- else
    walk.py iterates its characters as hook names."""
    nodes = [{"id": "n1", "tasks": ["on.test.run"], "on_failure": "on.mr_checks.repair"}]
    errs = templates.validate_nodes(nodes, _registry(tmp_path))
    assert errs and "on_failure" in errs[0]


def test_validate_nodes_rejects_an_empty_on_failure_list(tmp_path):
    nodes = [{"id": "n1", "tasks": ["on.test.run"], "on_failure": []}]
    errs = templates.validate_nodes(nodes, _registry(tmp_path))
    assert errs and "on_failure" in errs[0]


def test_validate_nodes_is_what_load_templates_calls_for_its_own_nodes(tmp_path):
    """Same error message shape, whole-template or spliced tail -- one rule
    set, two callers (Kraft-unk)."""
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "weirdgate.yaml": (
                "id: weirdgate\n"
                "nodes:\n"
                "  - { id: n1, tasks: [on.test.run], gate_after: bogus_gate }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    direct = templates.validate_nodes(
        [{"id": "n1", "tasks": ["on.test.run"], "gate_after": "bogus_gate"}], reg
    )
    assert direct[0] in ts.invalid["weirdgate"]


# ── template composition: extends/remove/insert_before/insert_after ───────

_BASE_TEMPLATE = (
    "id: base\n"
    "nodes:\n"
    "  - { id: env_setup,      tasks: [on.env.prepare],         gate_after: null }\n"
    "  - { id: implementation, tasks: [on.implementation.start], gate_after: null }\n"
    "  - { id: verify,         tasks: [on.test.run],            gate_after: null }\n"
)


def test_extends_inherits_the_base_templates_nodes(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "base.yaml": _BASE_TEMPLATE,
            "child.yaml": "id: child\nextends: base\n",
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "child" in ts.valid, ts.invalid
    assert [n["id"] for n in ts.valid["child"].nodes] == ["env_setup", "implementation", "verify"]


def test_extends_leaves_the_base_template_itself_untouched(tmp_path):
    """Resolving `child` must not mutate `base`'s own node list -- two
    templates extending the same base must not see each other's edits."""
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "base.yaml": _BASE_TEMPLATE,
            "child.yaml": "id: child\nextends: base\nremove: [env_setup]\n",
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert [n["id"] for n in ts.valid["base"].nodes] == ["env_setup", "implementation", "verify"]
    assert [n["id"] for n in ts.valid["child"].nodes] == ["implementation", "verify"]


def test_extends_remove_rejects_an_unknown_node_id(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "base.yaml": _BASE_TEMPLATE,
            "child.yaml": "id: child\nextends: base\nremove: [bogus]\n",
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "bogus" in ts.invalid["child"]


def test_extends_insert_before_and_after_an_anchor(tmp_path):
    child = (
        "id: child\n"
        "extends: base\n"
        "insert_before: { verify: [{ id: pre, tasks: [on.env.prepare] }] }\n"
        "insert_after:  { verify: [{ id: post, tasks: [on.env.prepare] }] }\n"
    )
    d = _dir(
        tmp_path,
        **{"registry.yaml": REGISTRY_YAML, "base.yaml": _BASE_TEMPLATE, "child.yaml": child},
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert [n["id"] for n in ts.valid["child"].nodes] == [
        "env_setup",
        "implementation",
        "pre",
        "verify",
        "post",
    ]


def test_extends_insert_before_rejects_an_unknown_anchor(tmp_path):
    child = (
        "id: child\nextends: base\n"
        "insert_before: { bogus: [{ id: pre, tasks: [on.env.prepare] }] }\n"
    )
    d = _dir(
        tmp_path,
        **{"registry.yaml": REGISTRY_YAML, "base.yaml": _BASE_TEMPLATE, "child.yaml": child},
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "bogus" in ts.invalid["child"]


def test_extends_and_nodes_together_is_a_load_error(tmp_path):
    child = (
        "id: child\nextends: base\n"
        "nodes:\n  - { id: x, tasks: [on.env.prepare], gate_after: null }\n"
    )
    d = _dir(
        tmp_path,
        **{"registry.yaml": REGISTRY_YAML, "base.yaml": _BASE_TEMPLATE, "child.yaml": child},
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "child" in ts.invalid


def test_plain_string_nodes_are_quarantined_not_silently_dropped(tmp_path):
    """A `nodes:` list of bare strings (an easy YAML slip) must land in
    `invalid`, not load as a valid template with zero nodes -- a zero-node
    chain would complete without running anything."""
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "typo.yaml": "id: typo\nnodes: [implementation, verify]\n",
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "typo" in ts.invalid
    assert "typo" not in ts.valid


def test_extends_of_a_plain_string_nodes_base_is_a_load_error(tmp_path):
    """A malformed base (bare-string `nodes:`) must quarantine both itself
    and any child that extends it, not raise AttributeError out of the
    loader when the extends branch inspects the base's node dicts."""
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "typo.yaml": "id: typo\nnodes: [implementation, verify]\n",
            "child.yaml": "id: child\nextends: typo\n",
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "typo" in ts.invalid
    assert "child" in ts.invalid
    assert "child" not in ts.valid


def test_extends_unknown_base_is_a_load_error(tmp_path):
    d = _dir(
        tmp_path, **{"registry.yaml": REGISTRY_YAML, "child.yaml": "id: child\nextends: bogus\n"}
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "bogus" in ts.invalid["child"]


def test_extends_cycle_is_a_load_error(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "a.yaml": "id: a\nextends: b\n",
            "b.yaml": "id: b\nextends: a\n",
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "cycle" in ts.invalid["a"]
    assert "cycle" in ts.invalid["b"]


def test_remove_without_extends_is_a_load_error(tmp_path):
    tmpl = (
        "id: solo\nnodes:\n  - { id: x, tasks: [on.env.prepare], gate_after: null }\nremove: [x]\n"
    )
    d = _dir(tmp_path, **{"registry.yaml": REGISTRY_YAML, "solo.yaml": tmpl})
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "extends" in ts.invalid["solo"]


def test_insert_introducing_a_duplicate_node_id_is_a_load_error(tmp_path):
    child = (
        "id: child\nextends: base\n"
        "insert_after: { verify: [{ id: verify, tasks: [on.env.prepare] }] }\n"
    )
    d = _dir(
        tmp_path,
        **{"registry.yaml": REGISTRY_YAML, "base.yaml": _BASE_TEMPLATE, "child.yaml": child},
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "duplicate" in ts.invalid["child"]


def test_a_deep_extends_chain_resolves(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "base.yaml": _BASE_TEMPLATE,
            "child.yaml": "id: child\nextends: base\nremove: [env_setup]\n",
            "grandchild.yaml": "id: grandchild\nextends: child\n",
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert [n["id"] for n in ts.valid["grandchild"].nodes] == ["implementation", "verify"]


def test_extending_a_template_whose_own_nodes_fail_validation_still_reports_the_root_cause(
    tmp_path,
):
    """A child inherits its base's problems too -- each template is still
    validated independently, so the child's own error names the real defect
    rather than a generic 'base is broken'."""
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "base.yaml": "id: base\nnodes:\n  - { id: x, tasks: [on.bogus], gate_after: null }\n",
            "child.yaml": "id: child\nextends: base\n",
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "on.bogus" in ts.invalid["child"]


def test_validate_agent_overrides_rejects_unknown_effort():
    errs = templates.validate_agent_overrides({"effort": "turbo"})
    assert errs and "effort" in errs[0]


def test_validate_agent_overrides_rejects_non_string_model():
    errs = templates.validate_agent_overrides({"model": 5})
    assert errs and "model" in errs[0]


def test_validate_agent_overrides_accepts_a_partial_object():
    assert templates.validate_agent_overrides({"model": "opus"}) == []


def test_validate_model_effort_fields_rejects_bad_effort():
    errs = templates.validate_model_effort_fields({"effort": "turbo"})
    assert errs and "effort" in errs[0]


def test_validate_model_effort_fields_accepts_null_model():
    assert templates.validate_model_effort_fields({"model": None, "effort": "high"}) == []


def test_validate_agent_overrides_still_rejects_non_string_model():
    """Unchanged behaviour through the refactor -- same message shape as
    before Kraft-df4tc moved the field checks into a shared function."""
    errs = templates.validate_agent_overrides({"model": 5})
    assert errs == ["agent_overrides 'model' must be a string or null"]


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


_AUTO_TEMPLATE = (
    "id: autos\n"
    "nodes:\n"
    "  - { id: n1, tasks: [on.test.run], gate_after: spec_approval, auto_escalate: true }\n"
    "  - { id: n2, tasks: [on.test.run], gate_after: null }\n"
)


def test_materialize_carries_auto_escalate(tmp_path):
    d = _dir(tmp_path, **{"registry.yaml": REGISTRY_YAML, "autos.yaml": _AUTO_TEMPLATE})
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    nodes = materialize(ts.valid["autos"])["nodes"]
    assert nodes[0]["auto_escalate"] is True
    # A node that never opted in reads as None -- not a KeyError, and not
    # False-by-accident, so `node.get(...)` in the executor stays honest.
    assert nodes[1]["auto_escalate"] is None


def test_auto_escalate_must_be_a_bool_beside_a_gate(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "badtype.yaml": (
                "id: badtype\n"
                "nodes:\n"
                "  - { id: n1, tasks: [on.test.run], gate_after: spec_approval, "
                "auto_escalate: yes please }\n"
            ),
            "nogate.yaml": (
                "id: nogate\n"
                "nodes:\n"
                "  - { id: n1, tasks: [on.test.run], gate_after: null, auto_escalate: true }\n"
            ),
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "auto_escalate" in ts.invalid["badtype"]
    # auto_escalate on a gateless node reviews nothing. A config key that
    # silently does nothing is how a human concludes the feature is broken.
    assert "auto_escalate" in ts.invalid["nogate"]


_AUTO_STUCK_TEMPLATE = (
    "id: autostuck\n"
    "nodes:\n"
    "  - { id: n1, tasks: [on.test.run], gate_after: null, auto_escalate_stuck: false }\n"
    "  - { id: n2, tasks: [on.test.run], gate_after: null }\n"
)


def test_materialize_carries_auto_escalate_stuck_on_a_gateless_node(tmp_path):
    d = _dir(tmp_path, **{"registry.yaml": REGISTRY_YAML, "autostuck.yaml": _AUTO_STUCK_TEMPLATE})
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    nodes = materialize(ts.valid["autostuck"])["nodes"]
    assert nodes[0]["auto_escalate_stuck"] is False
    assert nodes[1]["auto_escalate_stuck"] is None


def test_auto_escalate_stuck_must_be_a_bool(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "badtype.yaml": (
                "id: badtype\n"
                "nodes:\n"
                "  - { id: n1, tasks: [on.test.run], gate_after: null, "
                "auto_escalate_stuck: yes please }\n"
            ),
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "auto_escalate_stuck" in ts.invalid["badtype"]


_AUTO_DELAY_TEMPLATE = (
    "id: autodelay\n"
    "nodes:\n"
    "  - { id: n1, tasks: [on.test.run], gate_after: null, auto_escalate_delay_s: 60 }\n"
    "  - { id: n2, tasks: [on.test.run], gate_after: null }\n"
)


def test_materialize_carries_auto_escalate_delay_s(tmp_path):
    d = _dir(tmp_path, **{"registry.yaml": REGISTRY_YAML, "autodelay.yaml": _AUTO_DELAY_TEMPLATE})
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    nodes = materialize(ts.valid["autodelay"])["nodes"]
    assert nodes[0]["auto_escalate_delay_s"] == 60
    assert nodes[1]["auto_escalate_delay_s"] is None


def test_auto_escalate_delay_s_must_be_a_non_negative_int(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "badtype.yaml": (
                "id: badtype\n"
                "nodes:\n"
                "  - { id: n1, tasks: [on.test.run], gate_after: null, "
                "auto_escalate_delay_s: -5 }\n"
            ),
        },
    )
    ts = templates.load_templates(d, templates.load_registry(d / "registry.yaml"))
    assert "auto_escalate_delay_s" in ts.invalid["badtype"]


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


# ── registry `defaults:` (Kraft-6m2x6 phase 1) ─────────────────────────────


def _steering_dir_with(tmp_path, *names):
    d = tmp_path / "steering"
    d.mkdir(exist_ok=True)
    for name in names:
        (d / f"{name}.md").write_text("# House style\nBe direct.\n")
    return d


def test_defaults_agent_steering_merges_into_a_hook_with_none(tmp_path):
    _steering_dir_with(tmp_path, "shared", "own")
    (tmp_path / "registry.yaml").write_text(
        "defaults:\n  agent: { steering: [shared] }\n" + _TWO_AGENT_HOOKS_REGISTRY
    )
    reg = templates.load_registry(tmp_path / "registry.yaml", steering_dir=tmp_path / "steering")
    assert reg.hooks["on.a"]["steering"] == ["shared"]


def test_defaults_agent_steering_prepends_before_the_bindings_own(tmp_path):
    _steering_dir_with(tmp_path, "shared", "own")
    (tmp_path / "registry.yaml").write_text(
        "defaults:\n  agent: { steering: [shared] }\n" + _TWO_AGENT_HOOKS_REGISTRY
    )
    reg = templates.load_registry(tmp_path / "registry.yaml", steering_dir=tmp_path / "steering")
    assert reg.hooks["on.b"]["steering"] == ["shared", "own"]


def test_defaults_agent_steering_dedupes_a_repeated_entry(tmp_path):
    _steering_dir_with(tmp_path, "shared")
    (tmp_path / "registry.yaml").write_text(
        "defaults:\n  agent: { steering: [shared] }\n"
        "hooks:\n  on.a: { kind: agent, command: claude, steering: [shared] }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml", steering_dir=tmp_path / "steering")
    assert reg.hooks["on.a"]["steering"] == ["shared"]


def test_defaults_agent_scalar_does_not_override_the_bindings_own_value(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "defaults:\n  agent: { model: opus }\n"
        "hooks:\n"
        "  on.a: { kind: agent, command: claude, model: sonnet }\n"
        "  on.b: { kind: agent, command: claude }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert reg.hooks["on.a"]["model"] == "sonnet"
    assert reg.hooks["on.b"]["model"] == "opus"


def test_defaults_do_not_apply_to_a_non_agent_kind(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "defaults:\n  agent: { model: opus }\n"
        "hooks:\n  on.a: { kind: builtin, handler: env_setup }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert "model" not in reg.hooks["on.a"]


def test_defaults_rejects_an_unknown_top_level_key(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "defaults:\n  subprocess: {}\nhooks:\n  on.a: { kind: builtin, handler: env_setup }\n"
    )
    with pytest.raises(templates.RegistryError, match="defaults"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_defaults_agent_rejects_an_unknown_key(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "defaults:\n  agent: { handler: nope }\n"
        "hooks:\n  on.a: { kind: builtin, handler: env_setup }\n"
    )
    with pytest.raises(templates.RegistryError, match="defaults.agent"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_defaults_agent_steering_must_be_a_list_of_strings(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "defaults:\n  agent: { steering: not-a-list }\n"
        "hooks:\n  on.a: { kind: agent, command: claude }\n"
    )
    with pytest.raises(templates.RegistryError, match="steering"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_defaults_agent_merge_rejects_a_bindings_own_non_list_value(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "defaults:\n  agent: { deny_tools: [WebFetch] }\n"
        "hooks:\n  on.a: { kind: agent, command: claude, deny_tools: Bash }\n"
    )
    with pytest.raises(templates.RegistryError, match="deny_tools"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_defaults_missing_entirely_is_fine(tmp_path):
    (tmp_path / "registry.yaml").write_text("hooks:\n  on.a: { kind: agent, command: claude }\n")
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert reg.hooks["on.a"] == {"kind": "agent", "command": "claude", "harness": "claude"}


# ── new agent-hook keys: profile, model, deny_tools, steering ──────────────────


def test_agent_hook_without_new_keys_loads_exactly_as_before(tmp_path):
    d = _dir(tmp_path, **{"registry.yaml": REGISTRY_YAML})
    reg = templates.load_registry(d / "registry.yaml")
    assert reg.hooks["on.implementation.start"] == {
        "kind": "agent",
        "command": "claude",
        "harness": "claude",
    }


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


def test_load_registry_accepts_a_model_matching_its_harness(tmp_path):
    """Superseded by harness-declared `values:` (design leak 10): a model
    string is validated against the named harness's own patterns rather than
    accepted unconditionally."""
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, model: claude-opus-5 }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert reg.hooks["on.x"]["model"] == "claude-opus-5"


def test_load_registry_rejects_a_model_not_matching_its_harness(tmp_path):
    """Checked against codex, not claude: a model id is an open set Anthropic
    owns, so `claude.yaml` declares no `model` `values:` at all and anything
    is accepted there. codex keeps a real pattern, so it is what still pins
    load_registry's model check.
    """
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, harness: codex, model: anything-at-all }\n"
    )
    with pytest.raises(templates.RegistryError, match="'model'.*'anything-at-all'"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_accepts_any_model_for_a_harness_without_values(tmp_path):
    """The upgrade case: `model: fable` was valid before harnesses existed and
    must stay valid, because load_registry raising here stops the daemon
    booting (api/startup.py does not catch RegistryError).
    """
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, harness: claude, model: fable }\n"
    )
    assert templates.load_registry(tmp_path / "registry.yaml").hooks["on.x"]["model"] == "fable"


def test_load_registry_rejects_deny_tools_not_a_list(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, deny_tools: WebFetch }\n"
    )
    with pytest.raises(templates.RegistryError):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_rejects_unknown_permission_mode(tmp_path):
    """A typo in a permission grant must fail at config load, not at dispatch --
    by dispatch the item has already paid for a worktree and a session."""
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, permission_mode: yolo }\n"
    )
    with pytest.raises(templates.RegistryError):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_rejects_allowed_tools_not_a_list(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, allowed_tools: Read }\n"
    )
    with pytest.raises(templates.RegistryError):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_accepts_a_per_node_grant(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, "
        "allowed_tools: [Read, Grep], permission_mode: auto }\n"
    )
    hooks = templates.load_registry(tmp_path / "registry.yaml").hooks
    assert hooks["on.x"]["allowed_tools"] == ["Read", "Grep"]


def test_a_non_agent_hook_may_not_carry_a_permission_grant(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: builtin, handler: noop, allowed_tools: [Read] }\n"
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
    inlined into the operator's config on the next save -- except `harness`,
    which is deliberately normalised into every agent binding so no consumer
    has to re-derive the `claude` default (Task 3)."""
    (tmp_path / "steering").mkdir()
    (tmp_path / "steering" / "house-style.md").write_text("# House style\nBe direct.\n")
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, steering: [house-style] }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    binding = reg.hooks["on.x"]
    assert binding == {
        "kind": "agent",
        "command": "claude",
        "steering": ["house-style"],
        "harness": "claude",
    }
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
                "steps": [["on.env.prepare"]],
                "gate_after": None,
                "fix_loop": None,
                "on_failure": None,
                "reject_to": None,
                "rebase_bounce_to": None,
                "auto_escalate": None,
                "auto_escalate_stuck": None,
                "auto_escalate_delay_s": None,
            },
            {
                "id": "implementation",
                "tasks": ["on.implementation.start"],
                "steps": [["on.implementation.start"]],
                "gate_after": None,
                "fix_loop": None,
                "on_failure": None,
                "reject_to": None,
                "rebase_bounce_to": None,
                "auto_escalate": None,
                "auto_escalate_stuck": None,
                "auto_escalate_delay_s": None,
            },
            {
                "id": "verify",
                "tasks": ["on.test.run"],
                "steps": [["on.test.run"]],
                "gate_after": None,
                "fix_loop": None,
                "on_failure": None,
                "reject_to": None,
                "rebase_bounce_to": None,
                "auto_escalate": None,
                "auto_escalate_stuck": None,
                "auto_escalate_delay_s": None,
            },
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


def test_materialize_carries_on_failure(tmp_path):
    """Kraft-rv6i. The walker reads the node dicts out of the materialized
    chain, so a repair list the template declares and materialize drops is a
    repair that never runs."""
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "recovering.yaml": (
                "id: recovering\nnodes:\n"
                "  - { id: n1, tasks: [on.test.run], on_failure: [on.env.prepare] }\n"
                "  - { id: n2, tasks: [on.test.run] }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    mat = templates.materialize(templates.load_templates(d, reg).valid["recovering"])
    by_id = {n["id"]: n for n in mat["nodes"]}
    assert by_id["n1"]["on_failure"] == ["on.env.prepare"]
    assert by_id["n2"]["on_failure"] is None


def test_bad_on_failure_quarantines_template(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "quick-task.yaml": GOOD_TEMPLATE,
            "badrecover.yaml": (
                "id: badrecover\nnodes:\n"
                "  - { id: n1, tasks: [on.test.run], on_failure: on.env.prepare }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "quick-task" in ts.valid
    assert "on_failure" in ts.invalid["badrecover"]


def test_an_unknown_hook_in_on_failure_quarantines_template(tmp_path):
    """The same check the task list gets: a repair bound to nothing would only
    be discovered by a node failing, which is the worst moment to find out."""
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "ghost.yaml": (
                "id: ghost\nnodes:\n  - { id: n1, tasks: [on.test.run], on_failure: [on.bogus] }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "on.bogus" in ts.invalid["ghost"]


def test_a_node_may_have_both_fix_loop_and_on_failure(tmp_path):
    """Kraft-cbr: `mr_checks` now needs both -- the repair runs once, ahead of
    the first paid fix cycle, inside the same fix loop (walk.py), so the two
    no longer race for the same failure the way the old check assumed."""
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "both.yaml": (
                "id: both\nnodes:\n"
                "  - { id: n1, tasks: [on.test.run], fix_loop: n1_fix, "
                "on_failure: [on.env.prepare] }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "both" in ts.valid
    assert "both" not in ts.invalid


def test_default_templates_mr_checks_has_the_ci_fix_loop_shape(tmp_path):
    """The real `templates/default.yaml`/`registry.yaml` pair: `mr_checks`
    carries `fix_loop` and `rebase_bounce_to`. Its repair now lives on the
    `on.ci.poll` binding, not on this node (Kraft-uhev1 phase 2) -- see
    `test_shipped_registry_hangs_the_ci_repair_off_the_poll_binding`."""
    from pathlib import Path

    real_dir = Path(__file__).resolve().parents[1] / "templates"
    reg = templates.load_registry(real_dir / "registry.yaml")
    ts = templates.load_templates(real_dir, reg)
    assert "default" in ts.valid, ts.invalid.get("default")
    node = next(n for n in ts.valid["default"].nodes if n["id"] == "mr_checks")
    assert node["fix_loop"] == "ci_fix_loop"
    assert node["rebase_bounce_to"] == "verify"
    assert node.get("on_failure") in (None, [])


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


def test_forge_binding_accepts_the_auto_backend(tmp_path):
    """`auto` is a name the registry may carry and `forge.resolve` never sees:
    `forge.backend_for` translates it at dispatch against the repo's entry."""
    p = tmp_path / "registry.yaml"
    p.write_text("hooks:\n  on.mr.open: {kind: forge, handler: open_mr, backend: auto}\n")
    registry = templates.load_registry(p)
    assert registry.hooks["on.mr.open"]["backend"] == "auto"


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


def test_reject_to_must_name_an_earlier_node_of_the_same_template(tmp_path):
    """A rejection routes backwards. A `reject_to` pointing forwards would let
    a gate skip the nodes between it and its target."""
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "quick-task.yaml": GOOD_TEMPLATE,
            "forward.yaml": (
                "id: forward\n"
                "nodes:\n"
                "  - { id: plan,  tasks: [on.test.run], reject_to: merge }\n"
                "  - { id: merge, tasks: [on.test.run] }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "quick-task" in ts.valid
    assert "forward" in ts.invalid
    assert "reject_to" in ts.invalid["forward"]


def test_reject_to_naming_no_node_at_all_quarantines_the_template(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "nosuch.yaml": (
                "id: nosuch\nnodes:\n  - { id: n1, tasks: [on.test.run], reject_to: ghost }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "nosuch" in ts.invalid
    assert "reject_to" in ts.invalid["nosuch"]


def test_a_node_may_name_itself_as_its_own_reject_target(tmp_path):
    """`reject_to` at the declaring node's own index is today's behaviour
    written down, not an error."""
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "self.yaml": (
                "id: self\nnodes:\n  - { id: n1, tasks: [on.test.run], reject_to: n1 }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "self" in ts.valid, ts.invalid


def test_validate_nodes_reject_to_may_target_a_node_outside_the_list():
    """`_splice_chain_review` (Kraft-df4tc) validates only the not-yet-run
    tail; a tail node's reject_to routinely names an already-run node
    outside that list, which must not be treated as a forward reference."""
    from kraft.templates import Registry, validate_nodes

    registry = Registry(hooks={})
    nodes = [{"id": "human_review", "tasks": [], "gate_after": None, "reject_to": "plan"}]
    assert validate_nodes(nodes, registry, preceding_ids=frozenset({"plan"})) == []


def test_validate_nodes_reject_to_forward_reference_still_rejected():
    from kraft.templates import Registry, validate_nodes

    registry = Registry(hooks={})
    nodes = [
        {"id": "a", "tasks": [], "gate_after": None, "reject_to": "b"},
        {"id": "b", "tasks": [], "gate_after": None},
    ]
    errs = validate_nodes(nodes, registry, preceding_ids=frozenset())
    assert errs and "reject_to" in errs[0]


def test_validate_nodes_rebase_bounce_to_unknown_node_rejected_even_with_preceding_ids():
    from kraft.templates import Registry, validate_nodes

    registry = Registry(hooks={})
    nodes = [{"id": "a", "tasks": [], "gate_after": None, "rebase_bounce_to": "ghost"}]
    errs = validate_nodes(nodes, registry, preceding_ids=frozenset({"other"}))
    assert errs and "rebase_bounce_to" in errs[0]


def test_rebase_bounce_to_must_name_an_earlier_node(tmp_path):
    reg_path = tmp_path / "registry.yaml"
    reg_path.write_text(
        "hooks:\n"
        "  on.a: { kind: builtin, handler: noop }\n"
        "  on.b: { kind: builtin, handler: noop }\n"
    )
    reg = templates.load_registry(reg_path)
    (tmp_path / "default.yaml").write_text(
        "id: default\n"
        "nodes:\n"
        "  - { id: verify, tasks: [on.a], gate_after: null }\n"
        "  - { id: pre_mr_rebase, tasks: [on.b], gate_after: null, "
        "rebase_bounce_to: open_mr }\n"
        "  - { id: open_mr, tasks: [on.a], gate_after: null }\n"
    )
    ts = templates.load_templates(tmp_path, reg)
    assert "default" in ts.invalid
    assert "rebase_bounce_to" in ts.invalid["default"]


def test_rebase_bounce_to_an_earlier_node_is_valid_and_survives_materialize(tmp_path):
    reg_path = tmp_path / "registry.yaml"
    reg_path.write_text(
        "hooks:\n"
        "  on.a: { kind: builtin, handler: noop }\n"
        "  on.b: { kind: builtin, handler: noop }\n"
    )
    reg = templates.load_registry(reg_path)
    (tmp_path / "default.yaml").write_text(
        "id: default\n"
        "nodes:\n"
        "  - { id: verify, tasks: [on.a], gate_after: null }\n"
        "  - { id: pre_mr_rebase, tasks: [on.b], gate_after: null, "
        "rebase_bounce_to: verify }\n"
        "  - { id: open_mr, tasks: [on.a], gate_after: null }\n"
    )
    ts = templates.load_templates(tmp_path, reg)
    assert "default" in ts.valid, ts.invalid
    materialized = templates.materialize(ts.valid["default"])
    by_id = {n["id"]: n for n in materialized["nodes"]}
    assert by_id["pre_mr_rebase"]["rebase_bounce_to"] == "verify"
    assert by_id["verify"]["rebase_bounce_to"] is None


def test_the_default_chain_sends_a_rejected_review_back_to_implementation():
    """Kraft-ko7j: the last gate is no longer a dead end, and where it goes is
    chain shape, so it lives in the template."""
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    tmpl = templates.load_templates(TEMPLATES_DIR, reg).valid["default"]
    by_id = {n["id"]: n for n in templates.materialize(tmpl)["nodes"]}
    assert by_id["human_review"]["reject_to"] == "implementation"
    assert by_id["plan"]["reject_to"] is None


def test_load_registry_accepts_timeout_on_subprocess(tmp_path):
    reg = tmp_path / "registry.yaml"
    reg.write_text("hooks:\n  on.test.run: { kind: subprocess, command: [pytest], timeout: 20 }\n")
    registry = templates.load_registry(reg)
    assert registry.hooks["on.test.run"]["timeout"] == 20


def test_load_registry_rejects_timeout_on_builtin(tmp_path):
    reg = tmp_path / "registry.yaml"
    reg.write_text("hooks:\n  on.env.prepare: { kind: builtin, handler: env_setup, timeout: 5 }\n")
    with pytest.raises(templates.RegistryError):
        templates.load_registry(reg)


def test_load_registry_accepts_per_repo_override(tmp_path):
    reg = tmp_path / "registry.yaml"
    reg.write_text(
        "hooks:\n  on.test.run: { kind: subprocess, command: [pytest], "
        "repos: { /repo-a: { enabled: true, command: [pytest, -q] } } }\n"
    )
    registry = templates.load_registry(reg)
    assert registry.hooks["on.test.run"]["repos"]["/repo-a"]["enabled"] is True


def test_load_registry_rejects_repo_override_missing_enabled(tmp_path):
    reg = tmp_path / "registry.yaml"
    reg.write_text(
        "hooks:\n  on.test.run: { kind: subprocess, command: [pytest], repos: { /repo-a: {} } }\n"
    )
    with pytest.raises(templates.RegistryError):
        templates.load_registry(reg)


def test_load_registry_accepts_the_judge_hook_id():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    assert reg.hooks["on.fix_loop.judge"]["kind"] == "agent"
    assert reg.hooks["on.fix_loop.judge"]["skill"] == "fix-loop-judge"


def test_default_template_still_loads_with_the_judge_hook_present():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    ts = templates.load_templates(TEMPLATES_DIR, reg)
    assert "default" in ts.valid
    assert ts.invalid == {}


def test_the_security_review_hook_is_registered_and_its_skill_resolves():
    """Kraft-l2cg: `chain_review`'s own rules tell it to add a security-review
    task when a plan touches auth, sessions, tokens, secrets or permission
    checks, and to refuse to approximate with a different hook when the right
    one is missing. Before this hook existed, the only possible outcome for such
    a plan was a rationale noting the gap and a chain with no security review.

    Registered but deliberately in no default chain -- `chain_review` adds it.
    """
    registry = load_registry(TEMPLATES_DIR / "registry.yaml", skills_dir=SKILLS_DIR)
    hook = registry.hooks["on.review.security.run"]

    assert hook["kind"] == "agent"
    assert hook["skill"] == "security-review"
    # Not a lookup for its own sake: `skill.validate` raising here is exactly how
    # a registry naming a skill directory nobody wrote would be caught.
    skill.validate(SKILLS_DIR, hook["skill"], where="registry.yaml")

    for template in ("default.yaml", "quick-task.yaml"):
        chain = yaml.safe_load((TEMPLATES_DIR / template).read_text())
        tasks = [t for node in chain["nodes"] for t in node.get("tasks", [])]
        assert "on.review.security.run" not in tasks


def test_load_registry_accepts_sandbox_on_an_agent_hook(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n"
        "  on.x: { kind: agent, command: claude, sandbox: {kind: docker, image: kraft-worker} }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert reg.hooks["on.x"]["sandbox"] == {"kind": "docker", "image": "kraft-worker"}


def test_load_registry_accepts_sandbox_on_a_subprocess_hook(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n"
        "  on.x: { kind: subprocess, command: [pytest], "
        "sandbox: {kind: docker, image: kraft-worker} }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert reg.hooks["on.x"]["sandbox"]["image"] == "kraft-worker"


def test_load_registry_rejects_sandbox_on_a_builtin_hook(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n"
        "  on.x: { kind: builtin, handler: noop, sandbox: {kind: docker, image: kraft-worker} }\n"
    )
    with pytest.raises(templates.RegistryError, match="only to a subprocess or agent hook"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_rejects_sandbox_on_a_forge_hook(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n"
        "  on.x: { kind: forge, handler: merge, backend: auto, "
        "sandbox: {kind: docker, image: kraft-worker} }\n"
    )
    with pytest.raises(templates.RegistryError, match="only to a subprocess or agent hook"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_load_registry_rejects_a_malformed_sandbox(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.x: { kind: agent, command: claude, sandbox: {kind: podman, image: x} }\n"
    )
    with pytest.raises(templates.RegistryError, match="podman"):
        templates.load_registry(tmp_path / "registry.yaml")


def _bound(tmp_path, binding: str) -> Path:
    p = tmp_path / "registry.yaml"
    p.write_text(f"hooks:\n  on.implementation.start: {{ {binding} }}\n")
    return p


def test_absent_harness_defaults_to_claude(tmp_path):
    """Every existing install writes `command: claude` and no `harness:`."""
    reg = templates.load_registry(_bound(tmp_path, "kind: agent, command: claude"))
    assert reg.hooks["on.implementation.start"]["harness"] == "claude"


def test_profile_is_accepted_as_a_deprecated_alias(tmp_path):
    reg = templates.load_registry(_bound(tmp_path, "kind: agent, command: claude, profile: codex"))
    assert reg.hooks["on.implementation.start"]["harness"] == "codex"


def test_harness_wins_over_the_alias(tmp_path):
    reg = templates.load_registry(_bound(tmp_path, "kind: agent, harness: codex, profile: claude"))
    assert reg.hooks["on.implementation.start"]["harness"] == "codex"


def test_agent_hook_needs_neither_command_nor_harness(tmp_path):
    """`templates.py:178` used to require a string `command`; a binding may
    now name only a harness."""
    reg = templates.load_registry(_bound(tmp_path, "kind: agent, harness: codex"))
    assert reg.hooks["on.implementation.start"]["harness"] == "codex"


def test_unknown_harness_is_rejected(tmp_path):
    with pytest.raises(templates.RegistryError, match="unknown harness 'nope'"):
        templates.load_registry(_bound(tmp_path, "kind: agent, harness: nope"))


def test_a_quarantined_harness_is_not_usable(tmp_path):
    """A harness file that failed validation must not silently behave like an
    absent one."""
    hs = harness.HarnessSet(valid={}, invalid={"codex": "codex.yaml: boom"})
    with pytest.raises(templates.RegistryError, match="codex.yaml: boom"):
        templates.load_registry(_bound(tmp_path, "kind: agent, harness: codex"), harnesses=hs)


def test_capability_a_harness_does_not_declare_is_rejected(tmp_path):
    with pytest.raises(
        templates.RegistryError,
        match=r"hook 'on.implementation.start'.*harness 'codex'.*'deny_tools'",
    ):
        templates.load_registry(
            _bound(tmp_path, "kind: agent, harness: codex, deny_tools: [Write]")
        )


def test_value_outside_the_harnesss_own_values_is_rejected(tmp_path):
    with pytest.raises(templates.RegistryError, match="'effort'.*'ludicrous'"):
        templates.load_registry(_bound(tmp_path, "kind: agent, harness: claude, effort: ludicrous"))


def test_a_model_for_the_wrong_harness_is_rejected(tmp_path):
    """The point of harness-declared values: `model: sonnet` is right for
    claude and meaningless for codex."""
    templates.load_registry(_bound(tmp_path, "kind: agent, harness: claude, model: sonnet"))
    with pytest.raises(templates.RegistryError, match="'model'.*'sonnet'"):
        templates.load_registry(_bound(tmp_path, "kind: agent, harness: codex, model: sonnet"))


def test_effort_is_no_longer_one_global_list(tmp_path):
    """Spec leak 10. `minimal` is real for codex and invalid for claude."""
    templates.load_registry(_bound(tmp_path, "kind: agent, harness: codex, effort: minimal"))
    with pytest.raises(templates.RegistryError, match="'effort'.*'minimal'"):
        templates.load_registry(_bound(tmp_path, "kind: agent, harness: claude, effort: minimal"))


def test_binding_command_must_be_a_string(tmp_path):
    with pytest.raises(templates.RegistryError, match="'command' must be a string"):
        templates.load_registry(
            _bound(tmp_path, "kind: agent, harness: claude, command: [claude, x]")
        )


def test_the_shipped_registry_still_loads():
    """The one test that matters for every existing install."""
    reg = templates.load_registry(Path("templates/registry.yaml"))
    assert reg.hooks["on.implementation.start"]["harness"] == "claude"


def test_binding_on_failure_is_accepted_on_a_non_agent_kind(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n"
        "  on.poll:   { kind: builtin, handler: noop, on_failure: [on.repair] }\n"
        "  on.repair: { kind: builtin, handler: noop }\n"
    )
    reg = templates.load_registry(tmp_path / "registry.yaml")
    assert reg.hooks["on.poll"]["on_failure"] == ["on.repair"]


def test_binding_on_failure_must_be_a_non_empty_list_of_strings(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.poll: { kind: builtin, handler: noop, on_failure: on.repair }\n"
    )
    with pytest.raises(templates.RegistryError, match="non-empty list of strings"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_binding_on_failure_rejects_an_empty_list(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.poll: { kind: builtin, handler: noop, on_failure: [] }\n"
    )
    with pytest.raises(templates.RegistryError, match="non-empty list of strings"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_binding_on_failure_rejects_an_unknown_hook(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.poll: { kind: builtin, handler: noop, on_failure: [on.nope] }\n"
    )
    with pytest.raises(templates.RegistryError, match="unknown hook"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_binding_on_failure_rejects_naming_itself(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "hooks:\n  on.poll: { kind: builtin, handler: noop, on_failure: [on.poll] }\n"
    )
    with pytest.raises(templates.RegistryError, match="its own repair"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_shipped_registry_hangs_the_ci_repair_off_the_poll_binding():
    """The repair's whole job is reading a red pipeline, so it belongs to
    `on.ci.poll`, not to whichever node happens to poll. Named for what it
    knows about rather than for its old position in one chain."""
    reg = templates.load_registry(Path("templates/registry.yaml"))
    assert reg.hooks["on.ci.poll"]["on_failure"] == ["on.ci.repair"]
    assert "on.ci.repair" in reg.hooks
    assert "on.mr_checks.repair" not in reg.hooks


def test_shipped_default_chain_no_longer_carries_a_node_level_on_failure():
    reg = templates.load_registry(Path("templates/registry.yaml"))
    ts = templates.load_templates(Path("templates"), reg)
    nodes = ts.valid["default"].nodes
    assert all(n.get("on_failure") in (None, []) for n in nodes), [
        n["id"] for n in nodes if n.get("on_failure")
    ]


def test_defaults_agent_still_rejects_on_failure(tmp_path):
    (tmp_path / "registry.yaml").write_text(
        "defaults:\n  agent: { on_failure: [on.repair] }\n"
        "hooks:\n"
        "  on.a:      { kind: agent, command: claude }\n"
        "  on.repair: { kind: builtin, handler: noop }\n"
    )
    with pytest.raises(templates.RegistryError, match="defaults.agent"):
        templates.load_registry(tmp_path / "registry.yaml")


def test_chain_review_skill_teaches_the_node_binding_split():
    """The skill is the schema the chain-review agent writes nodes from. If it
    still presents on_failure as a plain node field, the agent will keep
    proposing one (templates.py's NODE_CARRYOVER_FIELDS comment is about
    exactly this class of drift)."""
    text = Path("src/kraft/skills/chain-review/SKILL.md").read_text()
    assert "binding" in text.lower().split("on_failure")[1][:400], (
        "the on_failure paragraph must say a task-level repair lives on the "
        "registry binding, not on the node"
    )


def _reg():
    return templates.Registry(
        hooks={f"on.{c}": {"kind": "builtin", "handler": "noop"} for c in "abcd"}, raw={}
    )


def test_with_steps_derives_groups_from_a_flat_task_list():
    out = templates.with_steps({"id": "n", "tasks": ["on.a", "on.b"]})
    assert out["steps"] == [["on.a", "on.b"]], "a flat list is one concurrent group"
    assert out["tasks"] == ["on.a", "on.b"]


def test_with_steps_derives_a_flat_task_list_from_groups_in_order():
    out = templates.with_steps({"id": "n", "steps": [["on.a"], ["on.b", "on.c"]]})
    assert out["steps"] == [["on.a"], ["on.b", "on.c"]]
    assert out["tasks"] == ["on.a", "on.b", "on.c"], "flat union, in group order"


def test_a_template_may_not_declare_both_steps_and_tasks(tmp_path):
    (tmp_path / "t.yaml").write_text(
        "id: t\nnodes:\n- id: n\n  tasks: [on.a]\n  steps: [[on.b]]\n  gate_after: null\n"
    )
    ts = templates.load_templates(tmp_path, _reg())
    assert "t" in ts.invalid
    assert "both" in ts.invalid["t"]


def test_steps_must_be_a_non_empty_list_of_non_empty_string_lists(tmp_path):
    for bad in ("[]", "[[]]", "[on.a]", "[[1]]"):
        (tmp_path / "t.yaml").write_text(
            f"id: t\nnodes:\n- id: n\n  steps: {bad}\n  gate_after: null\n"
        )
        ts = templates.load_templates(tmp_path, _reg())
        assert "t" in ts.invalid, bad


def test_steps_hooks_are_checked_against_the_registry(tmp_path):
    (tmp_path / "t.yaml").write_text(
        "id: t\nnodes:\n- id: n\n  steps: [[on.nope]]\n  gate_after: null\n"
    )
    ts = templates.load_templates(tmp_path, _reg())
    assert "t" in ts.invalid
    assert "not in the registry" in ts.invalid["t"]


def test_materialize_emits_both_keys_for_a_steps_node(tmp_path):
    (tmp_path / "t.yaml").write_text(
        "id: t\nnodes:\n- id: n\n  steps: [[on.a], [on.b]]\n  gate_after: null\n"
    )
    ts = templates.load_templates(tmp_path, _reg())
    node = templates.materialize(ts.valid["t"])["nodes"][0]
    assert node["steps"] == [["on.a"], ["on.b"]]
    assert node["tasks"] == ["on.a", "on.b"]


def test_materialize_still_emits_steps_for_a_plain_tasks_node(tmp_path):
    """The no-regression case: every existing template keeps working, and the
    executor can read `steps` unconditionally."""
    (tmp_path / "t.yaml").write_text(
        "id: t\nnodes:\n- id: n\n  tasks: [on.a, on.b]\n  gate_after: null\n"
    )
    ts = templates.load_templates(tmp_path, _reg())
    node = templates.materialize(ts.valid["t"])["nodes"][0]
    assert node["tasks"] == ["on.a", "on.b"]
    assert node["steps"] == [["on.a", "on.b"]]


def _write_inputs_registry(tmp_path, hooks):
    import yaml

    p = tmp_path / "registry.yaml"
    p.write_text(yaml.safe_dump({"hooks": hooks}))
    return p


def test_an_unknown_input_name_is_rejected_at_load(tmp_path):
    p = _write_inputs_registry(
        tmp_path,
        {
            "on.review.local.run": {
                "kind": "subprocess",
                "command": ["x"],
                "inputs": {"nonsense": {"channel": "env", "name": "X"}},
            }
        },
    )
    with pytest.raises(templates.RegistryError, match="unknown input 'nonsense'"):
        templates.load_registry(p)


def test_an_input_on_a_channel_it_does_not_support_is_rejected(tmp_path):
    p = _write_inputs_registry(
        tmp_path,
        {
            "on.test.run": {
                "kind": "subprocess",
                "command": ["x"],
                "inputs": {"test_scopes": {"channel": "env", "name": "X"}},
            }
        },
    )
    with pytest.raises(templates.RegistryError, match="channel 'env'"):
        templates.load_registry(p)


def test_an_env_input_without_a_name_is_rejected(tmp_path):
    p = _write_inputs_registry(
        tmp_path,
        {
            "on.review.local.run": {
                "kind": "subprocess",
                "command": ["x"],
                "inputs": {"review_package": {"channel": "env"}},
            }
        },
    )
    with pytest.raises(templates.RegistryError, match="needs a string 'name'"):
        templates.load_registry(p)


def test_a_binding_with_no_inputs_key_still_loads(tmp_path):
    p = _write_inputs_registry(
        tmp_path, {"on.test.run": {"kind": "subprocess", "command": ["uv", "run", "pytest"]}}
    )
    assert templates.load_registry(p).hooks["on.test.run"]["command"] == ["uv", "run", "pytest"]
