"""The doors at a slice group that has forked and not joined: what a parent
refuses whatever its status, and what pausing, resuming and ending it do to its
children."""

from __future__ import annotations

import dataclasses
from functools import partial

import pytest
from support.api import _budget_stopped_item, _paused, _status_of
from support.api import _orchestrator_db as _db

from kraft import store
from kraft.api import deps
from kraft.executor import slices


def _family(client, repo, *, parent_status="waiting", children=("running", "running")):
    """A parent at a forked group and one child per status, written directly."""
    parent = _paused(client, repo, chain_template="default")
    kids = [_paused(client, repo, chain_template="default") for _ in children]
    state = store.GroupState(group="slices", phase="forked", generation=1)
    with _db() as c, c:
        c.execute(
            "UPDATE work_items SET status = ?, current_node_id = 'implementation', "
            "stop_kind = ?, group_state = ? WHERE id = ?",
            (
                parent_status,
                "slices" if parent_status == "waiting" else None,
                state.to_json(),
                parent,
            ),
        )
        for key, (kid, status) in enumerate(zip(kids, children, strict=True), 1):
            c.execute(
                "UPDATE work_items SET parent_id = ?, slice_generation = 1, slice_key = ?, "
                "status = ?, current_node_id = 'implementation' WHERE id = ?",
                (parent, key, {"running": "active"}.get(status, status), kid),
            )
    return parent, kids


@pytest.mark.parametrize("status", ["waiting", "paused", "needs_human"])
@pytest.mark.parametrize(
    ("verb", "path", "body"),
    [
        pytest.param("skip", "skip", {}, id="skip"),
        pytest.param("complete", "complete", {"reason": "by hand"}, id="complete"),
    ],
)
def test_an_unjoined_parent_refuses(client, repo, status, verb, path, body):
    parent, kids = _family(client, repo, parent_status=status)

    r = client.post(f"/api/work-items/{parent}/{path}", json=body)

    assert r.status_code == 409, r.text
    assert kids[0] in r.json()["detail"]
    assert _status_of(client, parent) == status


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"path": "plan"}, id="path-plan"),
        pytest.param({"restart": True}, id="restart"),
    ],
)
def test_an_unjoined_parent_refuses_a_retry_from_an_earlier_node(client, repo, body):
    parent, kids = _family(client, repo, parent_status="needs_human")
    with _db() as c, c:
        c.execute("UPDATE work_items SET current_node_id = 'verification' WHERE id = ?", (parent,))

    r = client.post(f"/api/work-items/{parent}/retry", json=body)

    assert r.status_code == 409, r.text
    assert "unjoined" in r.json()["detail"]


def test_pause_and_resume_cascade_to_the_children_the_parent_paused(client, repo, monkeypatch):
    parent, kids = _family(client, repo, parent_status="active", children=("running", "paused"))
    with _db() as c, c:
        c.execute("UPDATE work_items SET stop_kind = NULL WHERE id = ?", (parent,))
    cancelled = []

    async def cancel(app, wid, timeout=None):
        cancelled.append(wid)

    monkeypatch.setattr(deps, "cancel", cancel)

    assert client.post(f"/api/work-items/{parent}/pause").status_code == 200

    # The running child's walk task is ended, as the pause door ends an item's.
    assert cancelled == [kids[0], parent]

    assert [_status_of(client, k) for k in kids] == ["paused", "paused"]
    with _db() as c:
        flags = [
            c.execute("SELECT paused_by_parent FROM work_items WHERE id=?", (k,)).fetchone()[0]
            for k in kids
        ]
    assert flags == [1, 0]  # the already-paused one stays a person's

    woken = client.portal.call(partial(slices.resume_family, client.app.state.db, parent))
    assert woken == [kids[0]]
    assert _status_of(client, kids[0]) == "queued"
    assert _status_of(client, kids[1]) == "paused"


def test_abandon_pauses_then_abandons_every_child(client, repo):
    parent, kids = _family(
        client, repo, parent_status="paused", children=("running", "needs_human")
    )

    r = client.post(f"/api/work-items/{parent}/abandon", json={"reason": "drop it"})

    assert r.status_code == 200, r.text
    assert [_status_of(client, k) for k in kids] == ["abandoned", "abandoned"]
    assert _status_of(client, parent) == "abandoned"


def test_cancel_keeps_the_childrens_worktrees(client, repo):
    parent, kids = _family(client, repo, parent_status="paused", children=("needs_human", "paused"))

    r = client.post(f"/api/work-items/{parent}/cancel", json={"reason": "stop"})

    assert r.status_code == 200, r.text
    assert [_status_of(client, k) for k in kids] == ["abandoned", "abandoned"]
    events = client.get(f"/api/work-items/{kids[0]}/events").json()
    assert "work_item_cancelled" in [e["type"] for e in events]


def test_archiving_a_parent_already_archived_alone_archives_its_slices_too(client, repo):
    """The auto-archive poller archives the parent on its own age: its slices,
    left behind with their worktrees, go the next time anything archives it."""
    parent, kids = _family(
        client, repo, parent_status="abandoned", children=("abandoned", "abandoned")
    )
    with _db() as c, c:
        c.execute(
            "UPDATE work_items SET archived_at = '2026-01-01T00:00:00+00:00', "
            "archived_by = 'auto' WHERE id = ?",
            (parent,),
        )

    r = client.post(f"/api/work-items/{parent}/archive")

    assert r.status_code == 200, r.text
    with _db() as c:
        archived = [
            c.execute("SELECT archived_at FROM work_items WHERE id=?", (k,)).fetchone()[0]
            for k in kids
        ]
    assert all(archived)


def test_a_child_cannot_be_archived_alone(client, repo):
    parent, kids = _family(client, repo, children=("completed", "completed"))

    r = client.post(f"/api/work-items/{kids[0]}/archive")

    assert r.status_code == 409, r.text
    assert parent in r.json()["detail"]


@pytest.mark.parametrize("acts_on", ["parent", "sibling"])
def test_a_worker_cannot_act_on_its_own_family(client, repo, acts_on):
    """A worker of one slice does not pause its parent or a sibling either."""
    parent, kids = _family(client, repo, parent_status="paused", children=("paused", "paused"))
    with _db() as c, c:
        store.create_session(
            c,
            id="s1",
            work_item_id=kids[0],
            node_id="implementation",
            hook_point="implementation.main.x",
            log_path="/tmp/kraft-test.log",
            result_path="/tmp/kraft-test.json",
        )
    target = parent if acts_on == "parent" else kids[1]

    r = client.post(
        f"/api/work-items/{target}/pause", headers={"X-Kraft-Session-Id": f"{kids[0]}/s1"}
    )

    assert r.status_code == 403, r.text


@pytest.mark.parametrize(
    ("body", "refused"),
    [
        pytest.param({}, True, id="retry-alone"),
        pytest.param({"path": "implementation"}, True, id="at-the-group"),
        pytest.param({"path": "plan"}, False, id="rewind-to-the-plan"),
        pytest.param({"restart": True}, False, id="restart"),
    ],
)
def test_a_retry_with_every_slice_dropped_wants_a_rewind(client, repo, body, refused):
    """Nothing to replay: the way on is the plan or earlier, or abandon."""
    parent, kids = _family(
        client, repo, parent_status="needs_human", children=("abandoned", "abandoned")
    )
    with _db() as c, c:
        state = store.GroupState(group="slices", phase="joining", generation=1)
        c.execute("UPDATE work_items SET group_state = ? WHERE id = ?", (state.to_json(), parent))

    r = client.post(f"/api/work-items/{parent}/retry", json=body)

    if refused:
        assert r.status_code == 409, r.text
        assert "abandon" in r.json()["detail"]
        assert "plan" in r.json()["detail"]
    else:
        assert r.status_code == 200, r.text
        assert _status_of(client, parent) != "needs_human"


@pytest.mark.parametrize(
    ("verb", "body", "at"),
    [
        pytest.param("retry", {}, "plan", id="retry-alone"),
        pytest.param("retry", {"restart": True}, "spec", id="restart-at-the-first-node"),
        pytest.param("skip", {}, "plan", id="skip"),
    ],
)
def test_a_parent_being_replanned_has_every_door_but_the_groups(client, repo, verb, body, at):
    """A rewind to the plan leaves the group's old phase on the row until chain
    revision resets it. If the plan then stops for a person, abandon is not the
    only way out: the cursor is behind the group, so nothing is unjoined."""
    parent, kids = _family(
        client, repo, parent_status="needs_human", children=("abandoned", "abandoned")
    )
    with _db() as c, c:
        state = store.GroupState(group="implementation", phase="joining", generation=1)
        c.execute(
            "UPDATE work_items SET group_state = ?, current_node_id = ? WHERE id = ?",
            (state.to_json(), at, parent),
        )

    r = client.post(f"/api/work-items/{parent}/{verb}", json=body)

    assert r.status_code == 200, r.text
    assert _status_of(client, parent) != "needs_human"


@pytest.mark.parametrize(
    ("path", "status"),
    [
        pytest.param("implementation.main.implement", 409, id="at-the-group"),
        pytest.param("work_brief.main.author", 200, id="after-the-group"),
    ],
)
def test_a_draft_may_not_move_an_unjoined_parent_back(client, repo, path, status):
    parent, _ = _family(client, repo, parent_status="paused")
    client.put(f"/api/work-items/{parent}/draft", json={"ops": [{"op": "skip", "path": path}]})

    r = client.post(f"/api/work-items/{parent}/draft/apply")

    assert r.status_code == status, r.text
    if status == 409:
        assert "slices have not ended" in r.json()["detail"]


def _stopped_child(kid, budget):
    """`kid` stopped for `budget`, the way the walk records a spend-cap stop."""
    with _db() as c, c:
        c.execute("UPDATE work_items SET current_node_id = 'implementation' WHERE id = ?", (kid,))
        store.mark_needs_human(c, kid, "implementation", "stopped", None, budget, kind="budget")


@pytest.mark.parametrize(
    ("scope", "policy"),
    [
        pytest.param("usd", {"budget_usd": 0.05}, id="the-policy-cap"),
        pytest.param("work_item", None, id="the-items-own-cap"),
    ],
)
def test_raising_the_parents_cap_reaches_and_retries_the_slices_it_stopped(
    monkeypatch, client, repo, scope, policy
):
    """The slices hold a copy of the parent's cap: the raise reaches it, and the
    retry queues the ones the family cap stopped, not the ones stopped for
    anything else."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    cap = {"scope": scope, "path": "", "spent_usd": 0.07, "cap_usd": 0.05, "unknown_launches": 0}
    parent = _budget_stopped_item(client, repo, cap, **({"policy": policy} if policy else {}))
    kids = [_paused(client, repo, chain_template="default") for _ in range(4)]
    state = store.GroupState(group="slices", phase="forked", generation=1)
    with _db() as c, c:
        c.execute("UPDATE work_items SET group_state = ? WHERE id = ?", (state.to_json(), parent))
        for key, kid in enumerate(kids, 1):
            c.execute(
                "UPDATE work_items SET parent_id = ?, slice_generation = 1, slice_key = ? "
                "WHERE id = ?",
                (parent, key, kid),
            )
    _stopped_child(kids[0], cap)
    _stopped_child(kids[1], {**cap, "scope": "tokens", "path": "implementation"})
    with _db() as c, c:
        c.execute("UPDATE work_items SET status = 'needs_human' WHERE id = ?", (kids[2],))
    # The family's token budget is as much the parent's as its dollar cap.
    _stopped_child(kids[3], {"scope": "tokens", "path": "", "spent_tokens": 9, "cap_tokens": 5})

    r = client.post(f"/api/work-items/{parent}/budget/raise", json={"budget_usd": 1.0})

    assert r.status_code == 200, r.text
    caps = [client.get(f"/api/work-items/{k}").json()["budget_cap"]["cap_usd"] for k in kids]
    assert caps == [1.0] * 4
    assert [_status_of(client, k) in ("queued", "active") for k in kids] == [
        True,
        False,
        False,
        True,
    ]
    assert [_status_of(client, k) for k in kids[1:3]] == ["needs_human", "needs_human"]


@pytest.mark.parametrize(
    ("verb", "status"),
    [("resume", "paused"), ("retry", "needs_human")],
)
def test_a_parent_refusal_leaves_the_slices_asleep(client, repo, monkeypatch, verb, status):
    """Its own refusals come before the cascade: a 409 never leaves a slice woken."""
    parent, kids = _family(client, repo, parent_status=status, children=("paused", "paused"))
    with _db() as c, c:
        c.execute("UPDATE work_items SET paused_by_parent = 1 WHERE id IN (?, ?)", kids)
    monkeypatch.setattr(deps, "task_is_live", lambda app, wid: wid == parent)

    r = client.post(f"/api/work-items/{parent}/{verb}", json={})

    assert r.status_code == 409, r.text
    assert "already running" in r.json()["detail"]
    assert [_status_of(client, k) for k in kids] == ["paused", "paused"]


@pytest.mark.parametrize(
    ("verb", "status"),
    [("resume", "paused"), ("retry", "needs_human")],
)
def test_a_parent_that_only_queues_leaves_the_slices_asleep(client, repo, verb, status):
    """The cascade follows the parent's own claim: a full board queues the parent
    without waking anything, and the start that makes the request again wakes them."""
    client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)
    parent, kids = _family(client, repo, parent_status=status, children=("paused", "paused"))
    busy = _paused(client, repo, chain_template="default")
    with _db() as c, c:
        c.execute("UPDATE work_items SET paused_by_parent = 1 WHERE id IN (?, ?)", kids)
        c.execute("UPDATE work_items SET status = 'active' WHERE id = ?", (busy,))

    r = client.post(f"/api/work-items/{parent}/{verb}", json={})

    assert r.status_code == 200, r.text
    assert _status_of(client, parent) == "queued"
    assert [_status_of(client, k) for k in kids] == ["paused", "paused"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"budget_usd": 7.0}, id="the-items-own-cap"),
        pytest.param({"policy": {"budget_usd": 7.0}}, id="the-policy-cap"),
    ],
)
def test_a_cap_set_on_the_parent_is_the_cap_its_slices_check(client, repo, body):
    """However a person sets the parent's cap, a slice's next launch reads that
    cap, not the copy it was filed with."""
    parent, kids = _family(client, repo, parent_status="paused", children=("paused", "paused"))

    r = client.patch(f"/api/work-items/{parent}", json=body)

    assert r.status_code == 200, r.text
    caps = [client.get(f"/api/work-items/{k}").json()["budget_cap"]["cap_usd"] for k in kids]
    assert caps == [7.0, 7.0]
