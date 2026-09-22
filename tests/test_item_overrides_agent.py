"""Kraft-1qlgc: item-wide `agent_overrides` held to the same harness check a
per-node override already gets (`overrides.harness_refusal`; see
tests/test_item_overrides.py's `_node_override_the_node_harness_refuses_...`
test). A bad value here used to surface only when a task launched, possibly
hours into the item."""

from __future__ import annotations

import pytest
from support.harness import write_harness_profiles


def _paused_item(client, repo) -> str:
    return client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]


@pytest.mark.parametrize(
    ("fields", "refused"),
    [
        ({"effort": "max"}, "'effort'"),
        ({"model": "opus"}, "'model'"),
        ({"escalate_model": "opus"}, "'escalate_model'"),
        ({"model": "gpt-5", "escalate_model": "gpt-5-pro", "effort": "high"}, None),
    ],
    ids=["effort", "model", "escalate_model", "accepted"],
)
def test_an_item_wide_agent_override_the_chain_harness_refuses_is_refused(
    client, repo, templates_dir, fields, refused
):
    """The same `claude`-on-`codex` mismatch
    `test_a_node_override_the_node_harness_refuses_is_refused_at_both_doors`
    uses, applied item-wide instead of to one node: `codex`'s `values:`
    refuse `max` and a non-OpenAI model. `set_agent_overrides` (CLI
    `set-overrides`, MCP `set_agent_overrides`) both funnel through this PATCH
    route, so pinning it here pins both callers."""
    wid = _paused_item(client, repo)
    write_harness_profiles(templates_dir, {"claude": {"provider": "codex"}})

    r = client.patch(f"/api/work-items/{wid}", json={"agent_overrides": fields})
    if refused is None:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 422, r.text
        assert refused in r.json()["detail"] and "codex" in r.json()["detail"]
