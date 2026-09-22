"""Filing an item on a base branch (Kraft-v9gbi): POST /work-items names the
branch the item's work starts from and its merge request targets, frozen into
its target -- and a branch origin does not have is refused before anything is
filed."""

from __future__ import annotations

import json
import subprocess

import pytest
from support.harness import _git, make_repo


@pytest.fixture
def repo(tmp_path):
    """A repository whose origin has `main` and `release`."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    repo = make_repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main", "main:release")
    return repo


def _file(client, repo, **body):
    return client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False, **body}
    )


@pytest.mark.parametrize(
    "body, frozen", [({"base_branch": "release"}, "release"), ({}, None)], ids=["named", "unnamed"]
)
def test_intake_freezes_the_base_branch_on_the_items_target(client, repo, body, frozen):
    r = _file(client, repo, **body)

    assert r.status_code == 201, r.text
    item = client.get(f"/api/work-items/{r.json()['id']}").json()
    assert json.loads(item["materialized_chain"])["target"]["base_branch"] == frozen


@pytest.mark.parametrize(
    "branch, says",
    [
        ("nope", "no branch 'nope' on origin"),
        ("-x", "not a valid branch name"),
        ("a..b", "not a valid branch name"),
    ],
    ids=["missing-on-origin", "an-option", "an-invalid-name"],
)
def test_intake_refuses_a_base_branch_origin_does_not_have(client, repo, branch, says):
    r = _file(client, repo, base_branch=branch)

    assert r.status_code == 422
    assert says in r.json()["detail"]
    assert client.get("/api/work-items").json()["items"] == [], "nothing was filed"


def test_intake_refuses_a_base_branch_on_a_repository_with_no_origin(client, tmp_path):
    r = _file(client, make_repo(tmp_path, name="local"), base_branch="release")

    assert r.status_code == 422
    assert "has no origin remote" in r.json()["detail"]
