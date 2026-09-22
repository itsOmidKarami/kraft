"""Kraft-ta8nv: both intake doors file only against a connected repo.

`default_setup=False`: the suite's default client treats every path as
connected (`support.api._client`), which is the very thing pinned here."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.api_client(default_setup=False)

_BODIES = {
    "work-items": {"title": "t", "autostart": False},
    "triggers": {"title": "t"},
}


@pytest.mark.parametrize("route", sorted(_BODIES))
def test_an_unconnected_repo_is_refused_naming_repo_connect(client, repo, route):
    r = client.post(f"/api/{route}", json={**_BODIES[route], "repo": str(repo)})
    assert r.status_code == 422, r.text
    assert "kraft repo connect" in r.json()["detail"]


@pytest.mark.parametrize("route", sorted(_BODIES))
def test_a_connected_repo_is_filed(client, repo, route):
    connected = client.post(
        "/api/repos", json={"path": str(repo), "setup_command": "", "test_command": "true"}
    )
    assert connected.status_code == 201, connected.text
    r = client.post(f"/api/{route}", json={**_BODIES[route], "repo": str(repo)})
    assert r.status_code == 201, r.text
