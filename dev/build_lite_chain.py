"""Render a Kraft chain template to the JSON the Lite plugin ships.

The plugin's helper is stdlib-only and cannot read YAML. Hand-copying the node
list into the plugin is the drift this file exists to prevent: the YAML stays the
one source, `just lite-build` regenerates, and a test fails if they disagree.

Deliberately outside `plugins/kraft-lite/`: that directory is published as a
standalone repo and may not depend on this one, or on PyYAML.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def render(chain_yaml: Path, policy_yaml: Path) -> dict:
    chain = yaml.safe_load(chain_yaml.read_text())
    policy = yaml.safe_load(policy_yaml.read_text())
    loops = policy.get("loops") or {}
    fallback = policy.get("default") or {}
    return {
        "id": chain["id"],
        "nodes": [
            {
                "id": node["id"],
                "tasks": list(node.get("tasks") or []),
                "gate_after": node.get("gate_after"),
                "fix_loop": node.get("fix_loop"),
            }
            for node in chain["nodes"]
        ],
        # Only the caps this chain can reach, and only `attempts`: Lite ignores
        # `wall_clock_s` because an attended session has a human watching the
        # clock (spec §8). Dropping it here is the one place that is visible.
        # A loop absent from policy.yaml takes `policy.default`, the same
        # fallback Kraft's resolve_cap uses. Dropping it instead would give Lite
        # zero retries where Kraft gives three, silently on both sides.
        "loops": {
            name: {"attempts": int((loops.get(name) or fallback)["attempts"])}
            for name in {n.get("fix_loop") for n in chain["nodes"]} - {None}
        },
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    out = root / "plugins" / "kraft-lite" / "chains" / "default.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    artifact = render(root / "templates" / "default.yaml", root / "templates" / "policy.yaml")
    out.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"wrote {out}")
