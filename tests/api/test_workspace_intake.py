"""Filing a workspace work item: POST /work-items with a workspace, the
members it selects and a root-pointer policy, frozen into the item's target."""

from __future__ import annotations

import json

import pytest
from support.harness import make_repo_with_submodule


def _paused(client, repo, **body):
    """File a not-yet-started item (`autostart: False`); return its id."""
    r = client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False, **body}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _workspace(client, tmp_path):
    """Connect a root with one submodule: `POST /repos` declares workspace
    `ws` mounting member `a` at `libs/a`. Returns the root path."""
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    assert client.post("/api/repos", json={"path": str(root), "enabled": False}).status_code == 201
    return root


@pytest.mark.parametrize(
    ("policy", "expected"),
    [({}, "ignore"), ({"root_pointer_policy": "bump"}, "bump")],
    ids=["the-workspace-default", "the-item-chooses"],
)
def test_a_workspace_intake_freezes_its_selected_members_and_pointer_policy(
    client, tmp_path, policy, expected
):
    """`work-item-target-selection-is-immutable`: the members selected, where
    each is mounted and the root-pointer policy -- the workspace's default
    unless the item chose one (`workspace-root-pointer-update-is-explicit`)
    -- are captured in the item's snapshot at intake. The repos panel waits
    for `ensure_worktree` to write its rows (Kraft-qlsf)."""
    root = _workspace(client, tmp_path)
    wid = _paused(client, root, workspace="ws", members=["a"], **policy)

    body = client.get(f"/api/work-items/{wid}").json()
    target = json.loads(body["materialized_chain"])["target"]
    assert target == {
        "kind": "workspace",
        "repository": None,
        "workspace": "ws",
        "root": "ws",
        "members": ["a"],
        "mounts": {"a": {"repository": "a", "path": "libs/a"}},
        "include_root": True,
        "root_pointer_policy": expected,
        "base_branch": None,
    }
    assert body["repos"] == []


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ({"workspace": "nope", "members": ["a"]}, "no workspace 'nope'"),
        ({"workspace": "ws", "members": ["b"]}, "does not mount"),
        ({"members": ["a"]}, "names no workspace"),
    ],
    ids=["an-undeclared-workspace", "a-member-it-does-not-mount", "members-without-a-workspace"],
)
def test_a_workspace_selection_that_cannot_assemble_is_a_422(client, tmp_path, body, match):
    root = _workspace(client, tmp_path)
    r = client.post(
        "/api/work-items", json={"title": "t", "repo": str(root), "autostart": False, **body}
    )
    assert r.status_code == 422, r.text
    assert match in r.json()["detail"]


def test_a_workspace_is_filed_only_against_its_own_root(client, tmp_path, repo):
    _workspace(client, tmp_path)
    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "workspace": "ws", "members": ["a"]},
    )
    assert r.status_code == 422, r.text
    assert "rooted" in r.json()["detail"]
