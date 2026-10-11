"""A connected repository's repo library over HTTP: reviewed, accepted, and
forgotten at disconnect."""

from __future__ import annotations

import pytest
from support.addons import repo_with_library
from support.harness import make_repo

from kraft.addons.config import RepoLibrariesLock

#: No default repo entry for an unconnected repo (`support.api._client`).
pytestmark = pytest.mark.api_client(default_setup=False)


def _with_library(tmp_path, client):
    repo = repo_with_library(tmp_path)
    assert client.post("/api/repos", json={"path": str(repo)}).status_code == 201
    return repo


def test_a_repo_library_is_reviewed_then_accepted(tmp_path, client, templates_dir):
    repo = _with_library(tmp_path, client)

    seen = client.get("/api/repos/library", params={"path": str(repo)}).json()

    assert (seen["outcome"], seen["id"]) == ("waiting", "app")
    assert "Content:" in seen["review"]["text"]
    assert not (templates_dir / "repo-libraries.lock").exists()

    took = client.post(
        "/api/repos/library", params={"path": str(repo)}, json={"digest": seen["digest"]}
    )

    assert took.status_code == 200
    assert took.json()["outcome"] == "accepted"
    assert "app" in RepoLibrariesLock.load(templates_dir / "repo-libraries.lock").repos
    assert client.get("/api/repos").json()["repos"][0]["id"] == "app"
    health = client.get("/api/health").json()
    assert "repo library app" not in health.get("invalid_templates", {})
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
