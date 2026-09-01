from pathlib import Path

import yaml

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
