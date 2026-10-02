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


def test_the_detail_reports_the_item_wide_override_as_an_object_and_a_patch_replaces_it(
    client, repo
):
    """The column is JSON text; the detail decodes it, as it does
    `policy_override`. A PATCH replaces the whole override, as in 1.4: a
    field it leaves out is gone, and one sent as `null` is not stored."""
    wid = _paused_item(client, repo)
    assert client.get(f"/api/work-items/{wid}").json()["agent_overrides"] is None

    client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {"model": "opus"}})
    client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {"effort": "low"}})
    assert client.get(f"/api/work-items/{wid}").json()["agent_overrides"] == {"effort": "low"}

    r = client.patch(
        f"/api/work-items/{wid}", json={"agent_overrides": {"model": "opus", "effort": None}}
    )
    assert r.status_code == 200, r.text
    assert client.get(f"/api/work-items/{wid}").json()["agent_overrides"] == {"model": "opus"}


def test_a_model_that_is_no_model_id_is_refused(client, repo):
    wid = _paused_item(client, repo)
    r = client.patch(
        f"/api/work-items/{wid}", json={"agent_overrides": {"model": "not a model; rm -rf"}}
    )
    assert r.status_code == 422 and "is not a model id" in r.json()["detail"]
    r = client.patch(f"/api/work-items/{wid}", json={"node_overrides": {"plan": {"model": "a b"}}})
    assert r.status_code == 422 and "is not a model id" in r.json()["detail"]


def test_a_null_node_field_drops_only_that_field(client, repo):
    wid = _paused_item(client, repo)
    client.patch(
        f"/api/work-items/{wid}",
        json={"node_overrides": {"plan": {"model": "opus", "effort": "high"}}},
    )
    r = client.patch(f"/api/work-items/{wid}", json={"node_overrides": {"plan": {"model": None}}})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/work-items/{wid}").json()["node_overrides"] == {
        "plan": {"effort": "high"}
    }


def test_the_patch_echoes_the_overrides_as_stored_after_the_merge(client, repo):
    """A node field the request left out is still stored, so the echo says
    so; the item-wide override is replaced, so its echo is what was sent."""
    wid = _paused_item(client, repo)
    client.patch(
        f"/api/work-items/{wid}",
        json={"agent_overrides": {"model": "opus"}, "node_overrides": {"plan": {"model": "opus"}}},
    )
    r = client.patch(
        f"/api/work-items/{wid}",
        json={"agent_overrides": {"effort": "low"}, "node_overrides": {"plan": {"effort": "high"}}},
    )
    assert r.json()["agent_overrides"] == {"effort": "low"}
    assert r.json()["node_overrides"] == {"plan": {"model": "opus", "effort": "high"}}
    # Cleared, the override echoes `{}`, as 1.4's did: never `null`.
    cleared = client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {}})
    assert cleared.json()["agent_overrides"] == {}


def test_a_node_override_echoes_only_the_nodes_it_named(client, repo):
    """`kraft item set-node-override --node implementation --clear --json`
    echoed 1.4's `{"implementation": {}}`; it must not become `{}`, or carry
    another node's entry."""
    wid = _paused_item(client, repo)
    client.patch(f"/api/work-items/{wid}", json={"node_overrides": {"plan": {"model": "opus"}}})
    r = client.patch(
        f"/api/work-items/{wid}", json={"node_overrides": {"implementation": {"effort": "low"}}}
    )
    assert r.json()["node_overrides"] == {"implementation": {"effort": "low"}}
    cleared = client.patch(
        f"/api/work-items/{wid}", json={"node_overrides": {"implementation": {}}}
    )
    assert cleared.json()["node_overrides"] == {"implementation": {}}
