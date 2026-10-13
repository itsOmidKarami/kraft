"""A connected repository's repo library over HTTP: reviewed, accepted, and
forgotten at disconnect."""

from __future__ import annotations

import pytest
from support.addons import load_release, repo_with_library
from support.harness import git, make_repo

from kraft.addons.config import RepoLibrariesLock

#: No default repo entry for an unconnected repo (`support.api._client`).
pytestmark = pytest.mark.api_client(default_setup=False)


def _with_library(tmp_path, client, name="app"):
    repo = repo_with_library(tmp_path, name)
    assert client.post("/api/repos", json={"path": str(repo)}).status_code == 201
    return repo


@pytest.mark.parametrize(
    "name, addon, minted",
    [
        pytest.param("app", False, "app", id="own-name"),
        pytest.param("release", True, "release-2", id="addon-namespace"),
        pytest.param("kraft", False, "kraft-2", id="reserved"),
    ],
)
def test_a_repo_library_is_reviewed_then_accepted(
    tmp_path, client, templates_dir, name, addon, minted
):
    if addon:
        load_release(client, tmp_path)
    repo = _with_library(tmp_path, client, name)

    seen = client.get("/api/repos/library", params={"path": str(repo)}).json()

    assert (seen["outcome"], seen["id"]) == ("waiting", minted)
    assert "Content:" in seen["review"]["text"]
    assert not (templates_dir / "repo-libraries.lock").exists()

    took = client.post(
        "/api/repos/library", params={"path": str(repo)}, json={"digest": seen["digest"]}
    )

    assert took.status_code == 200
    assert took.json()["outcome"] == "accepted"
    assert minted in RepoLibrariesLock.load(templates_dir / "repo-libraries.lock").repos
    assert client.get("/api/repos").json()["repos"][0]["id"] == minted
    health = client.get("/api/health").json()
    assert f"repo library {minted}" not in health.get("invalid_templates", {})
    assert "add-on release@acme" not in health.get("invalid_templates", {})
    again = client.get("/api/repos/library", params={"path": str(repo)}).json()
    assert again["outcome"] == "current"


@pytest.mark.parametrize(
    "digest, status",
    [
        pytest.param("sha256:" + "0" * 64, 409, id="another-digest"),
        pytest.param(None, 422, id="no-digest"),
    ],
)
def test_accepting_what_was_not_reviewed_writes_nothing(
    tmp_path, client, templates_dir, digest, status
):
    repo = _with_library(tmp_path, client)

    body = {"digest": digest} if digest else {}
    answer = client.post("/api/repos/library", params={"path": str(repo)}, json=body)

    assert answer.status_code == status
    assert not (templates_dir / "repo-libraries.lock").exists()
    assert not client.get("/api/repos").json()["repos"][0].get("id")


def test_a_repo_with_no_library_answers_none(tmp_path, client):
    repo = make_repo(tmp_path, "plain")
    client.post("/api/repos", json={"path": str(repo)})

    assert client.get("/api/repos/library", params={"path": str(repo)}).json()["outcome"] == "none"
    assert client.get("/api/repos/library", params={"path": "/nope"}).status_code == 404


def _accepted(tmp_path, client):
    repo = _with_library(tmp_path, client)
    seen = client.get("/api/repos/library", params={"path": str(repo)}).json()
    client.post("/api/repos/library", params={"path": str(repo)}, json={"digest": seen["digest"]})
    return repo


def test_disconnect_forgets_the_repo_library(tmp_path, client, templates_dir):
    repo = _accepted(tmp_path, client)

    assert client.delete("/api/repos", params={"path": str(repo)}).status_code == 204

    assert RepoLibrariesLock.load(templates_dir / "repo-libraries.lock").repos == {}
    assert "repo library app" not in client.get("/api/health").json().get("invalid_templates", {})


def test_disconnect_is_refused_while_something_names_the_library(tmp_path, client, templates_dir):
    repo = _accepted(tmp_path, client)
    other = make_repo(tmp_path, "other")
    client.post("/api/repos", json={"path": str(other), "default_chain": "app:ship"})

    answer = client.delete("/api/repos", params={"path": str(repo)})

    assert answer.status_code == 409
    assert "default_chain 'app:ship'" in answer.json()["detail"]
    assert "app" in RepoLibrariesLock.load(templates_dir / "repo-libraries.lock").repos


def test_its_own_default_chain_does_not_hold_a_repo(tmp_path, client):
    repo = _accepted(tmp_path, client)
    client.patch("/api/repos", params={"path": str(repo)}, json={"default_chain": "app:ship"})

    assert client.delete("/api/repos", params={"path": str(repo)}).status_code == 204


def test_the_library_view_says_first_branch_and_auto_update(tmp_path, client):
    repo = _with_library(tmp_path, client)
    params = {"path": str(repo)}
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")

    seen = client.get("/api/repos/library", params=params).json()
    assert (seen["first"], seen["branch"], seen["auto_update"]) == (True, branch, False)

    client.post("/api/repos/library", params=params, json={"digest": seen["digest"]})
    assert (
        client.patch("/api/repos", params=params, json={"library_auto_update": True}).status_code
        == 200
    )

    again = client.get("/api/repos/library", params=params).json()
    assert (again["first"], again["auto_update"]) == (False, True)


def test_a_repo_with_no_library_has_no_branch_to_name(tmp_path, client):
    repo = make_repo(tmp_path, "plain")
    client.post("/api/repos", json={"path": str(repo)})

    assert client.get("/api/repos/library", params={"path": str(repo)}).json()["branch"] is None
