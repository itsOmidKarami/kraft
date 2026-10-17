"""Filing an item in place: POST /work-items freezes whether it runs in the
repository's own checkout -- its own `in_place`, else its repository's
`worktrees` -- and refuses an item that would run in place and sandboxed."""

from __future__ import annotations

import json

import pytest
from support.api import _paused
from support.harness import connect_repo

_SANDBOX = {"kind": "docker", "image": "img"}


@pytest.mark.parametrize(
    "worktrees, asked, frozen",
    [(True, None, False), (False, None, True), (True, True, True), (False, False, False)],
    ids=["repo-default", "repo-in-place", "item-in-place", "item-worktree"],
)
def test_intake_freezes_in_place_from_the_item_or_its_repo(client, repo, worktrees, asked, frozen):
    connect_repo(repo, worktrees=worktrees)
    body = {} if asked is None else {"in_place": asked}

    wid = _paused(client, repo, **body)

    item = client.get(f"/api/work-items/{wid}").json()
    assert json.loads(item["materialized_chain"])["target"]["in_place"] is frozen


def _sandboxed_chain(templates_dir):
    (templates_dir / "chains" / "boxed.yaml").write_text(
        "id: boxed\nnodes: [{id: work, kind: exec, policy: {sandbox: {kind: docker, image: img}},"
        " tasks: [{id: run, kind: agent, harness: fake, prompt: go}]}]\n"
    )


@pytest.mark.api_client(edit_templates=_sandboxed_chain)
@pytest.mark.parametrize(
    "entry, body, names",
    [
        ({"sandbox": _SANDBOX}, {}, "repos.yaml entry"),
        ({}, {"chain_template": "boxed"}, "chain boxed"),
        ({}, {"policy": {"sandbox": _SANDBOX}}, "the item's own policy"),
    ],
    ids=["repo-entry", "chain", "item-policy"],
)
def test_intake_refuses_a_sandboxed_in_place_item(client, repo, entry, body, names):
    connect_repo(repo, **entry)

    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "autostart": False, "in_place": True, **body},
    )

    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith("this item would run in place and sandboxed; ")
    assert names in r.json()["detail"]
    assert client.get("/api/work-items").json()["items"] == [], "nothing was filed"
