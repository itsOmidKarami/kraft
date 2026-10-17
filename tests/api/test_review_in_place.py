"""A review of an item that runs in its repo's own checkout: what it reads is
the item's branch, never the checkout's HEAD, which may be the person's."""

from __future__ import annotations

import json
from pathlib import Path

from support.api import _in_state
from support.harness import commit_all, git, write

# Shared, not copied: a thread filed with the review page's defaults.
from api.test_review import _new_thread

DOORS = json.loads((Path(__file__).parent / "lifecycle_doors.json").read_text())


def test_an_in_place_item_anchors_and_reviews_at_its_branch_tip(client, repo, monkeypatch):
    """The checkout is on main, one commit behind the item's branch: a thread
    filed with no anchor, and the review's head, are the branch's tip."""

    async def reply(*a, **kw):
        return "done"

    monkeypatch.setattr("kraft.review_reply.run", reply)
    wid = _in_state(client, repo, DOORS["states"]["gate"], in_place=True)
    git(repo, "checkout", "-q", client.get(f"/api/work-items/{wid}").json()["branch"])
    write(repo, "a.py", "one\ntwo\nthree\nfour\n")
    tip = commit_all(repo, "the item's work")
    git(repo, "checkout", "-q", "main")

    anchor = _new_thread(client, wid, label="question").json()["anchor_sha"]
    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/review", json={"outcome": "comment"}
    )

    assert r.status_code == 200, r.text
    assert (anchor, client.get(f"/api/work-items/{wid}").json()["last_review_sha"]) == (tip, tip)
