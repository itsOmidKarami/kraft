"""Connecting a repo with submodules over HTTP: the workspace it declares,
and the entries it connects for its members. The rest of repo CRUD:
tests/api/test_repos.py."""

from __future__ import annotations

import pytest
from support.harness import ALLOW_FILE, git, make_repo, make_repo_with_submodule

#: No default repo entry for an unconnected repo (`support.api._client`): these read real config.
pytestmark = pytest.mark.api_client(default_setup=False)


def test_connecting_a_workspace_auto_connects_its_submodules_disabled(tmp_path, client):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    client.post("/api/repos", json={"path": str(root)})

    repos = {r["path"]: r for r in client.get("/api/repos").json()["repos"]}
    child = repos[str(root / "libs/a")]
    assert child["enabled"] is False
    # detected, nobody has looked at it yet
    assert child["managed"] is False
    # the human typed the workspace path, so it is a decision from the start
    assert repos[str(root)]["managed"] is True


def test_connecting_a_workspace_declares_it_with_its_submodules_as_members(tmp_path, client):
    """Typed membership replaces reading `.gitmodules` at intake: connecting a
    root declares its workspace, each submodule a member mounted at its path,
    under repository ids the declaration can name. The pointer default is the
    shipped `ignore` (`workspace-root-pointer-update-defaults-to-ignore`)."""
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    client.post("/api/repos", json={"path": str(root), "enabled": False})

    body = client.get("/api/repos").json()
    ids = {r["path"]: r.get("id") for r in body["repos"]}
    assert ids == {str(root): "ws", str(root / "libs/a"): "a"}
    assert body["workspaces"] == {
        "ws": {
            "id": "ws",
            "root": "ws",
            "root_pointer_default": "ignore",
            "members": {"a": {"repository": "a", "path": "libs/a"}},
        }
    }


@pytest.mark.parametrize("shape", ["member-is-its-origin", "both-clone-one-remote"])
def test_a_member_connected_before_its_root_is_the_workspace_member(tmp_path, client, shape):
    """Kraft-d7aj3. The member is connected on its own first; the root's
    submodule checkout is another clone of it. The workspace names that
    entry, with its settings, rather than a second, empty one for the
    submodule's path that fan-out would then run."""
    if shape == "member-is-its-origin":
        root, member = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    else:
        remote, member = tmp_path / "pkg.git", tmp_path / "member"
        git(tmp_path, "clone", "-q", "--bare", str(make_repo(tmp_path, name="pkg")), str(remote))
        git(tmp_path, "clone", "-q", str(remote), str(member))
        root = make_repo(tmp_path, name="ws")
        git(root, *ALLOW_FILE, "submodule", "add", "-q", str(remote), "libs/a")
        git(root, "commit", "-q", "-m", "add submodule")
    client.post("/api/repos", json={"path": str(member), "test_command": "make test"})
    client.post("/api/repos", json={"path": str(root), "enabled": False})

    body = client.get("/api/repos").json()
    entries = {r["path"]: r for r in body["repos"]}
    assert set(entries) == {str(root), str(member.resolve())}
    ((ws_id, ws),) = body["workspaces"].items()
    assert ws["members"] == {
        entries[str(member.resolve())]["id"]: {
            "repository": entries[str(member.resolve())]["id"],
            "path": "libs/a",
        }
    }
    assert entries[str(member.resolve())]["test_command"] == "make test"


@pytest.mark.parametrize("shape", ["the-root-itself", "another-roots-submodule"])
def test_a_submodule_is_not_matched_to_an_entry_nobody_connected_for_it(tmp_path, client, shape):
    """Only a member a person connected is reused. A root that mounts its own
    repository (a docs branch), or a library another root already mounts, is
    no such member: the submodule gets its own entry, as before Kraft-d7aj3."""
    lib = make_repo(tmp_path, name="lib")
    if shape == "the-root-itself":
        root = make_repo(tmp_path, name="ws")
        git(root, "remote", "add", "origin", str(root))
        git(root, *ALLOW_FILE, "submodule", "add", "-q", str(root), "docs")
        rel = "docs"
    else:
        other = make_repo(tmp_path, name="other")
        git(other, *ALLOW_FILE, "submodule", "add", "-q", str(lib), "libs/lib")
        git(other, "commit", "-q", "-m", "add submodule")
        client.post("/api/repos", json={"path": str(other), "enabled": False})
        root = make_repo(tmp_path, name="ws")
        git(root, *ALLOW_FILE, "submodule", "add", "-q", str(lib), "libs/lib")
        rel = "libs/lib"
    git(root, "commit", "-q", "-m", "add submodule")
    client.post("/api/repos", json={"path": str(root), "enabled": False})

    body = client.get("/api/repos").json()
    ws = next(w for w in body["workspaces"].values() if w["root"] == "ws")
    (member,) = ws["members"].values()
    entry = next(r for r in body["repos"] if r["id"] == member["repository"])
    assert entry["path"] == str((root / rel).resolve())


def test_disconnecting_a_repository_a_workspace_mounts_is_refused(tmp_path, client, templates_dir):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    client.post("/api/repos", json={"path": str(root), "enabled": False})
    before = (templates_dir / "repos.yaml").read_text()

    r = client.delete(f"/api/repos?path={root / 'libs/a'}")
    assert r.status_code == 422, r.text
    assert "'a'" in r.json()["detail"]
    assert (templates_dir / "repos.yaml").read_text() == before


def test_a_hand_added_repo_is_managed_even_when_left_disabled(tmp_path, client):
    repo = make_repo(tmp_path)
    body = client.post("/api/repos", json={"path": str(repo), "enabled": False}).json()
    assert body["enabled"] is False
    assert body["managed"] is True


def test_auto_connect_never_overwrites_an_existing_entry(tmp_path, client):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    child = str(root / "libs/a")
    client.post("/api/repos", json={"path": child, "test_command": "cargo test"})
    client.post("/api/repos", json={"path": str(root)})

    repos = {r["path"]: r for r in client.get("/api/repos").json()["repos"]}
    # the hand-connected child keeps its command and its latch
    assert repos[child]["test_command"] == "cargo test"
    assert repos[child]["managed"] is True


def test_patching_a_detected_child_latches_it_managed(tmp_path, client):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    client.post("/api/repos", json={"path": str(root)})
    child = str(root / "libs/a")

    # editing a field without enabling is still a touch
    client.patch(f"/api/repos?path={child}", json={"test_command": "cargo test"})
    repos = {r["path"]: r for r in client.get("/api/repos").json()["repos"]}
    assert repos[child]["managed"] is True
    assert repos[child]["enabled"] is False


def test_managed_never_returns_to_false(tmp_path, client):
    repo = make_repo(tmp_path)
    client.post("/api/repos", json={"path": str(repo)})
    client.patch(f"/api/repos?path={repo}", json={"enabled": False})
    repos = {r["path"]: r for r in client.get("/api/repos").json()["repos"]}
    assert repos[str(repo)]["managed"] is True
