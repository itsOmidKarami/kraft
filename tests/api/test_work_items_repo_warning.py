"""What `POST /api/work-items` warns of, filing on a repo that cannot run
the item yet."""

from __future__ import annotations

import pytest
from support.harness import connect_repo, make_repo


@pytest.mark.api_client(default_setup=False)
@pytest.mark.parametrize(
    ("fields", "said"),
    [
        ({"test_command": "make test"}, None),
        ({"test_command": None}, ["has no test command", "stops at verification"]),
        ({"test_command": "make test", "setup_command": None}, ["declares no setup command"]),
        ({"test_command": None, "setup_command": None}, ["no setup command", "no test command"]),
    ],
    ids=["ready", "no-test-command", "no-setup-command", "neither"],
)
def test_filing_on_a_repo_that_cannot_run_an_item_warns(client, tmp_path, fields, said):
    """An item on a repo with no test command ran its agent tasks, spending
    budget, before it stopped at verification, and nothing said so when it
    was filed. Warned, not refused: a disabled repo still takes items filed
    by hand."""
    repo = connect_repo(make_repo(tmp_path), **{"enabled": False, **fields})
    r = client.post("/api/work-items", json={"title": "t", "repo": str(repo)})
    assert r.status_code == 201, r.text
    warning = r.json().get("repo_warning")
    if said is None:
        assert warning is None
    else:
        assert all(s in warning for s in said), warning
        assert "before you start this item" in warning


@pytest.mark.api_client(default_setup=False)
def test_an_item_filed_is_answered_201_whatever_repos_yaml_says_after(
    client, tmp_path, monkeypatch
):
    """The entry the warning reads was looked up after intake, so a repo
    disconnected meanwhile answered 422 for an item already filed."""
    from fastapi import HTTPException

    from kraft import executor
    from kraft.api import deps

    repo = connect_repo(make_repo(tmp_path), test_command=None)
    filed = []
    intake, lookup = executor.intake, deps.connected_or_422

    async def filing(*args, **kw):
        filed.append(await intake(*args, **kw))
        return filed[-1]

    def looked_up(st, path):
        if filed:
            raise HTTPException(422, f"{path} is not a connected repo")
        return lookup(st, path)

    monkeypatch.setattr(executor, "intake", filing)
    monkeypatch.setattr(deps, "connected_or_422", looked_up)
    r = client.post("/api/work-items", json={"title": "t", "repo": str(repo)})
    assert r.status_code == 201, r.text
    assert r.json()["id"] == filed[0]
    assert "has no test command" in r.json()["repo_warning"]
