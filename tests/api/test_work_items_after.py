"""Filing a work item with the items it comes after (`depends_on`)."""

import pytest
from support.api import _paused, _set_status


def _create(client, repo, **body):
    return client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "chain_template": "default", **body}
    )


@pytest.mark.parametrize(
    ("status", "code", "detail"),
    [(None, 422, "no work item"), ("abandoned", 422, "abandoned"), ("completed", 201, None)],
    ids=["unknown", "abandoned", "completed"],
)
def test_what_an_item_may_be_filed_after(client, repo, status, code, detail):
    dep = "0" * 32
    if status is not None:
        dep = _paused(client, repo, chain_template="default")
        _set_status(dep, status)

    r = _create(client, repo, depends_on=[dep])

    assert r.status_code == code, r.text
    if detail:
        assert detail in r.json()["detail"]
    else:
        assert r.json()["status"] == "paused"
        got = client.get(f"/api/work-items/{r.json()['id']}").json()["dependencies"]
        assert [(d["id"], d["met"]) for d in got] == [(dep, True)]


def test_autostart_after_an_unfinished_item_is_filed_blocked(client, repo):
    dep = _paused(client, repo, chain_template="default")

    r = _create(client, repo, depends_on=[dep], autostart=True)

    assert r.status_code == 201, r.text
    assert r.json()["status"] == "blocked"
    assert [d["id"] for d in r.json()["waiting_on"]] == [dep]
    assert client.get(f"/api/work-items/{r.json()['id']}").json()["status"] == "blocked"
