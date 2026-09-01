import json
from pathlib import Path

import pytest
import yaml

from kraft import templates

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
    assert set(registry["hooks"]) == {
        "on.env.prepare",
        "on.implementation.start",
        "on.test.run",
    }
    assert registry["hooks"]["on.env.prepare"] == {"kind": "builtin", "handler": "env_setup"}
    assert registry["hooks"]["on.implementation.start"] == {"kind": "agent", "command": "claude"}
    assert registry["hooks"]["on.test.run"] == {"kind": "subprocess", "command": ["pytest", "-q"]}


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


def _dir(tmp_path, **files):
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    return tmp_path


def test_load_registry_ok(tmp_path):
    d = _dir(tmp_path, **{"registry.yaml": REGISTRY_YAML})
    reg = templates.load_registry(d / "registry.yaml")
    assert set(reg.hooks) == {"on.env.prepare", "on.implementation.start", "on.test.run"}


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
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
            {"id": "verify", "tasks": ["on.test.run"], "gate_after": None},
        ],
    }
    assert "current_node_id" not in chain
    assert json.loads(json.dumps(chain)) == chain  # round-trips
