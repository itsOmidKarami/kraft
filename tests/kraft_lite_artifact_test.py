"""The plugin ships JSON because it has no YAML parser. The YAML is still the
source of truth, so the shipped copy must be exactly what rendering produces —
otherwise Lite runs a chain that Kraft no longer has.

This test cannot travel to the kraft-lite repo: it is the seam between the two,
and the seam belongs here."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "kraft-lite"
sys.path.insert(0, str(ROOT / "dev"))

import build_lite_chain as build_chain  # noqa: E402


def test_rendered_artifact_matches_the_committed_one():
    rendered = build_chain.render(
        ROOT / "templates" / "default.yaml", ROOT / "templates" / "policy.yaml"
    )
    committed = json.loads((PLUGIN / "chains" / "default.json").read_text())
    assert rendered == committed, "run `just lite-build` — chain YAML and shipped JSON have drifted"


def test_every_node_keeps_its_hooks_and_gate():
    rendered = build_chain.render(
        ROOT / "templates" / "default.yaml", ROOT / "templates" / "policy.yaml"
    )
    by_id = {n["id"]: n for n in rendered["nodes"]}
    assert by_id["spec"]["tasks"] == ["on.spec.requested"]
    assert by_id["spec"]["gate_after"] == "spec_approval"
    assert by_id["verify"]["fix_loop"] == "verify_fix_loop"
    assert by_id["env_setup"]["gate_after"] is None


def test_the_fix_loop_cap_comes_along():
    rendered = build_chain.render(
        ROOT / "templates" / "default.yaml", ROOT / "templates" / "policy.yaml"
    )
    assert rendered["loops"]["verify_fix_loop"]["attempts"] == 3


def test_a_loop_absent_from_policy_takes_the_default_cap():
    """Kraft's resolve_cap falls back to `policy.default`. Dropping the loop
    instead would give Lite zero retries where Kraft gives three."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        chain = Path(tmp) / "c.yaml"
        policy = Path(tmp) / "p.yaml"
        chain.write_text(
            "id: t\nnodes:\n  - {id: verify, tasks: [on.test.run], "
            "gate_after: null, fix_loop: unlisted_loop}\n"
        )
        policy.write_text("loops: {}\ndefault: {attempts: 3, wall_clock_s: 3600}\n")
        rendered = build_chain.render(chain, policy)
    assert rendered["loops"]["unlisted_loop"]["attempts"] == 3
