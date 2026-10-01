"""A dry run of create (B33), and node_overrides' per-node storage that its
caps resolution leans on. A sibling of test_work_items.py."""

from __future__ import annotations

from support.harness import make_repo_with_engineering

from kraft.adapters import beads

from .test_work_items import _paused

# --- B33: create dry run -----------------------------------------------------


def test_dry_run_reports_an_attachment_covered_node_skipped_and_writes_nothing(client, tmp_path):
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/s.md": "# spec\n"})
    r = client.post(
        "/api/work-items?dry_run=1",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "attachments": [{"kind": "spec", "path": ".engineering/specs/s.md"}],
            "cwd": str(repo),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert {"node": "spec", "why": "covered_by", "kind": "spec"} in body["skipped"]
    assert "spec" not in [n["id"] for n in body["nodes"]]
    # Nothing written: no row (so no bead either -- a bead is filed on the row
    # `executor.intake` creates, which a dry run never reaches).
    assert client.get("/api/work-items").json()["items"] == []


def test_dry_run_lists_a_skip_nodes_request_as_skip(client, repo):
    r = client.post(
        "/api/work-items?dry_run=1",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "skip_nodes": ["local_review"],
        },
    )
    assert r.status_code == 200, r.text
    assert {"node": "local_review", "why": "skip"} in r.json()["skipped"]


def test_dry_run_resolves_per_node_caps_the_way_the_walk_does(client, repo):
    r = client.post(
        "/api/work-items?dry_run=1",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "node_overrides": {
                "verification": {"attempts": 5},
                "merge_request_feedback": {"wall_clock_s": 999},
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    caps = body["caps"]["nodes"]
    # Each node's own override applies only to that node, not the other --
    # `verification`'s own `attempts` override left `merge_request_feedback`'s
    # alone, and vice versa for `wall_clock_s`.
    assert caps["verification"]["attempts"] == 5
    assert caps["merge_request_feedback"]["attempts"] != 5
    assert caps["merge_request_feedback"]["wall_clock_s"] == 999
    assert caps["verification"]["wall_clock_s"] != 999
    # No `budget_usd` on the body: the policy default applies, same as create.
    assert body["caps"]["budget_source"] == "policy"
    assert body["caps"]["budget_usd"] == 10.0  # templates/policy.yaml's shipped default
    assert body["caps"]["daily_usd"] == 50.0


def test_dry_run_caps_budget_source_is_item_when_the_body_sets_its_own_budget_usd(client, repo):
    r = client.post(
        "/api/work-items?dry_run=1",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "budget_usd": 3.0},
    )
    assert r.status_code == 200, r.text
    assert r.json()["caps"]["budget_source"] == "item"
    assert r.json()["caps"]["budget_usd"] == 3.0


def test_dry_run_answers_the_same_422_as_create(client, repo):
    body = {"title": "t" * (beads.MAX_TITLE + 1), "repo": str(repo)}
    assert client.post("/api/work-items", json=body).status_code == 422
    assert client.post("/api/work-items?dry_run=1", json=body).status_code == 422


def test_node_overrides_with_different_values_and_no_item_wide_value_are_stored_as_given(
    client, repo
):
    """§34's first point: `node_overrides` already takes `attempts` and
    `wall_clock_s` per node with different values and no item-wide value --
    the dry run's per-node `caps` (B33) leans on this holding true."""
    wid = _paused(
        client,
        repo,
        chain_template="default",
        node_overrides={
            "verification": {"attempts": 5},
            "merge_request_feedback": {"attempts": 7, "wall_clock_s": 999},
        },
    )
    stored = client.get(f"/api/work-items/{wid}").json()["node_overrides"]
    assert stored["verification"] == {"attempts": 5}
    assert stored["merge_request_feedback"] == {"attempts": 7, "wall_clock_s": 999}
