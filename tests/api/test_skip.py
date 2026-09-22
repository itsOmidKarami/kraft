"""POST /work-items/{wid}/skip — advance past a node or gate without running it."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from support.api import _poll_events, _poll_node_started, _wait_for_status


def test_skip_is_refused_on_an_item_that_has_not_started(client, repo):
    """`autostart: false` is `create_work_item`'s own "not started" case
    (kraft.api.routes.work_items):
    `current_node_id` stays NULL until `/resume`. `chain_template: quick-task`'s
    fake agent otherwise finishes so fast that a plain create would already
    have a `current_node_id` by the time this second request lands."""
    wid = client.post(
        "/api/work-items",
        json={
            "title": "not started",
            "repo": str(repo),
            "chain_template": "quick-task",
            "autostart": False,
        },
    ).json()["id"]
    r = client.post(f"/api/work-items/{wid}/skip", json={})
    assert r.status_code == 409
    assert client.post("/api/work-items/nope/skip", json={}).status_code == 404


def test_skip_advances_past_a_stopped_task_node_without_rerunning_it(client, repo):
    """quick-task: env_setup (builtin) -> implementation (agent) -> verify
    (subprocess). KRAFT_FAIL fails the agent-kind node, `implementation`,
    leaving `verify` unrun. Skip must move straight to `verify` — proof it did
    not retry `implementation` is a second `node_started` for `implementation`
    never showing up."""
    wid = client.post(
        "/api/work-items",
        json={
            "title": "KRAFT_FAIL once",
            "repo": str(repo),
            "chain_template": "quick-task",
            # Kraft-lpdd: this test is about skip, not the unrelated
            # auto-escalate trigger racing it onto the same needs_human
            # stop `_wait_for_status` below is waiting on.
            "node_overrides": {"implementation": {"auto_escalate_stuck": False}},
        },
    ).json()["id"]
    item = _wait_for_status(client, wid, "needs_human")
    assert item["current_node_id"] == "implementation"

    r = client.post(f"/api/work-items/{wid}/skip", json={"note": "known flake"})
    assert r.status_code == 200, r.text

    evts = _poll_node_started(client, wid, "verify")
    skipped = [e for e in evts if e["type"] == "node_skipped"]
    assert skipped == [
        {
            **skipped[0],
            "payload": {"node_id": "implementation", "gate": None, "note": "known flake"},
        }
    ]
    started = [e["payload"]["node_id"] for e in evts if e["type"] == "node_started"]
    assert started.count("implementation") == 1  # only the original attempt
    assert "verify" in started


def test_skip_bypasses_a_pending_gate_without_approving_it(client, repo, monkeypatch):
    """default template's first gate is spec_approval, after the `spec` node.
    Skip must reach `plan` without a `gate_approved` event — approval means
    the artifact was ingested, and skip never ingests one."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default"},
    ).json()["id"]
    _poll_events(client, wid, "gate_requested")
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["pending_gate"] == "spec_approval"

    r = client.post(f"/api/work-items/{wid}/skip", json={})
    assert r.status_code == 200, r.text

    evts = _poll_node_started(client, wid, "plan")
    skipped = [e for e in evts if e["type"] == "node_skipped"]
    # A V1 gate is its own node, so skipping it skips the gate node.
    assert skipped[0]["payload"] == {
        "node_id": "spec_approval",
        "gate": "spec_approval",
        "note": None,
    }
    assert not any(e["type"] == "gate_approved" for e in evts)
    assert any(e["type"] == "node_started" and e["payload"]["node_id"] == "plan" for e in evts)


def test_skip_while_active_kills_the_running_session_and_still_advances(client, repo, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "5")
    wid = client.post(
        "/api/work-items",
        json={"title": "KRAFT_SLOW go", "repo": str(repo), "chain_template": "quick-task"},
    ).json()["id"]

    deadline = time.monotonic() + 20
    sessions = []
    while time.monotonic() < deadline:
        item = client.get(f"/api/work-items/{wid}").json()
        sessions = item.get("worker_sessions", [])
        if item["status"] == "active" and any(s["status"] == "running" for s in sessions):
            break
        time.sleep(0.1)
    assert item["status"] == "active", "the slow node never started running"
    running_id = next(s["id"] for s in sessions if s["status"] == "running")

    r = client.post(f"/api/work-items/{wid}/skip", json={})
    assert r.status_code == 200, r.text

    killed = client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
    assert next(s for s in killed if s["id"] == running_id)["status"] == "paused"
    evts = _poll_events(client, wid, "node_skipped")
    assert any(e["type"] == "node_skipped" for e in evts)


def test_two_concurrent_skips_produce_one_advance_and_one_409(client, repo, monkeypatch):
    """Kraft-qx1q / one-walk-per-item: two callers racing `/skip` on the same
    item must produce exactly one advance and one 409."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default"},
    ).json()["id"]
    _poll_events(client, wid, "gate_requested")
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["pending_gate"] == "spec_approval"
    app = client.app

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://kraft") as ac:
            return await asyncio.gather(
                ac.post(f"/api/work-items/{wid}/skip", json={}),
                ac.post(f"/api/work-items/{wid}/skip", json={}),
            )

    a, b = client.portal.call(scenario)
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    evts = _poll_events(client, wid, "node_skipped")
    assert sum(e["type"] == "node_skipped" for e in evts) == 1


def test_skip_refuses_and_writes_nothing_when_a_walk_is_still_live_at_a_stop(
    client, repo, monkeypatch
):
    """`needs_human` (a pending gate under auto_escalate review, or the brief
    window while the walk that just called request_gate/mark_needs_human is
    still unwinding) can still have a live walk task behind it. `/skip` must
    refuse before it claims and writes `skip_node` -- not claim to `active`,
    record the node skipped, and only then discover the live walk (leaving
    the item `active` with the node skipped and no walk behind it)."""
    from kraft.api import deps

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default"},
    ).json()["id"]
    _poll_events(client, wid, "gate_requested")
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["pending_gate"] == "spec_approval"

    async def _never_returning():
        await asyncio.Event().wait()

    async def inject():
        deps.spawn(client.app, wid, _never_returning())

    client.portal.call(inject)

    r = client.post(f"/api/work-items/{wid}/skip", json={})

    assert r.status_code == 409, r.text
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "needs_human"
    assert item["pending_gate"] == "spec_approval"
    evts = client.get(f"/api/work-items/{wid}/events").json()
    assert not any(e["type"] == "node_skipped" for e in evts)

    async def cleanup():
        task = client.app.state.tasks.pop(wid)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    client.portal.call(cleanup)


def test_skip_cancels_the_live_walk_before_it_writes(client, repo, monkeypatch):
    """The old walk has to be dead *before* `/skip` claims the item and
    records the node skipped. Cancelling afterwards left every await in
    between as a chance for the event loop to resume that walk, which then
    dispatches the next node and orphans its agent in the worktree."""
    from kraft import store
    from kraft.api import deps

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "5")
    live_at_write = []
    real_skip_node = store.skip_node

    wid = client.post(
        "/api/work-items",
        json={"title": "KRAFT_SLOW go", "repo": str(repo), "chain_template": "quick-task"},
    ).json()["id"]

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        item = client.get(f"/api/work-items/{wid}").json()
        if item["status"] == "active" and any(
            s["status"] == "running" for s in item.get("worker_sessions", [])
        ):
            break
        time.sleep(0.1)
    assert item["status"] == "active", "the slow node never started running"

    def spy(conn, work_item_id, *a, **kw):
        live_at_write.append(deps.task_is_live(client.app, work_item_id))
        return real_skip_node(conn, work_item_id, *a, **kw)

    monkeypatch.setattr(store, "skip_node", spy)
    r = client.post(f"/api/work-items/{wid}/skip", json={})
    assert r.status_code == 200, r.text

    assert live_at_write == [False], "skip wrote skip_node with the old walk still live"


def _unskippable_verification(templates_dir):
    chain = templates_dir / "chains" / "default.yaml"
    text = chain.read_text()
    old = "  - id: verification\n    extends: verification\n"
    assert old in text
    chain.write_text(text.replace(old, old + "    skippable: false\n"))


def _stopped_in_verification(client, repo):
    """An item stopped in `verification`, never walked there: the node is
    forced, so nothing about the stop depends on an agent."""
    from support.api import _force_node

    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    _force_node(wid, "verification", "needs_human")
    return wid


@pytest.fixture
def walked(monkeypatch):
    """What `executor.run` was handed, instead of walking it."""
    from kraft import executor

    seen = []

    async def fake(*args, **kwargs):
        seen.append(kwargs)
        return "completed"

    monkeypatch.setattr(executor, "run", fake)
    return seen


@pytest.mark.parametrize(
    "path", ["verification.review.code_review", "verification.review"], ids=["task", "step"]
)
def test_skipping_a_task_or_step_records_it_and_walks_on_from_the_cursor(
    client, repo, walked, path
):
    """A stopped item is walked again from where it stands, the skipped scope
    now counted done (tests/executor/test_skip_scopes.py). The node itself is
    not skipped."""
    wid = _stopped_in_verification(client, repo)

    r = client.post(f"/api/work-items/{wid}/skip", json={"path": path, "note": "known"})

    assert r.status_code == 200, r.text
    for _ in range(200):
        if walked:
            break
        time.sleep(0.02)
    assert walked and "start_index" not in walked[0]
    evts = client.get(f"/api/work-items/{wid}/events").json()
    assert [e["payload"] for e in evts if e["type"] == "scope_skipped"] == [
        {"path": path, "note": "known"}
    ]
    assert not [e for e in evts if e["type"] == "node_skipped"]


def test_skipping_a_task_stops_only_its_own_session(client, repo, monkeypatch):
    """`skip-stops-only-the-selected-scope`, at the route: of two sessions
    running in the node, only the skipped task's is stopped, and the item stays
    active under the walk that owns the other."""
    from kraft import store
    from kraft.api.routes import lifecycle

    signalled = []
    monkeypatch.setattr(lifecycle, "_terminate", signalled.append)
    wid = _stopped_in_verification(client, repo)
    db = client.app.state.db

    async def seed():
        for sid, path, pid in (
            ("s-test", "verification.tests.test_changed_scopes", 111),
            ("s-review", "verification.review.code_review", 222),
        ):
            await db.write(
                lambda c, sid=sid, path=path: store.create_session(
                    c,
                    id=sid,
                    work_item_id=wid,
                    node_id="verification",
                    hook_point=path,
                    log_path="/l",
                    result_path="/r",
                )
            )
            await db.write(lambda c, sid=sid, pid=pid: store.session_running(c, sid, pid, 1.0))
        await db.write(
            lambda c: c.execute("UPDATE work_items SET status = 'active' WHERE id = ?", (wid,))
        )

    client.portal.call(seed)

    r = client.post(f"/api/work-items/{wid}/skip", json={"path": "verification.review.code_review"})

    assert r.status_code == 200, r.text
    assert signalled == [222]
    sessions = {
        s["id"]: s["status"] for s in client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
    }
    assert (sessions["s-review"], sessions["s-test"]) == ("paused", "running")
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "active"


@pytest.mark.parametrize(
    ("path", "status", "says"),
    [
        ("verification.nope", 422, "path: 'verification.nope': node"),
        ("spec.main.author", 409, "is not inside the node the item stands on"),
        ("merge", 409, "is not the node the item stands on"),
    ],
    ids=["unknown", "another-nodes-task", "another-node"],
)
def test_a_skip_path_the_item_cannot_take_is_refused(client, repo, walked, path, status, says):
    wid = _stopped_in_verification(client, repo)

    r = client.post(f"/api/work-items/{wid}/skip", json={"path": path})

    assert r.status_code == status, r.text
    assert says in r.json()["detail"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"
    assert walked == []


@pytest.mark.api_client(edit_templates=_unskippable_verification)
@pytest.mark.parametrize("path", [None, "verification"], ids=["current-node", "by-path"])
def test_a_node_that_disallows_skipping_is_not_skipped(client, repo, walked, path):
    """`task-step-and-node-are-skippable-by-default` -- unless the component
    says `skippable: false`."""
    wid = _stopped_in_verification(client, repo)

    r = client.post(f"/api/work-items/{wid}/skip", json={"path": path} if path else {})

    assert r.status_code == 409, r.text
    assert "'verification' does not allow skipping" in r.json()["detail"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"


def _unskippable_code_review(templates_dir):
    import yaml

    library = templates_dir / "library.yaml"
    parsed = yaml.safe_load(library.read_text())
    review = parsed["nodes"]["verification"]["steps"][1]["tasks"][0]
    assert review["id"] == "code_review"
    review["skippable"] = False
    library.write_text(yaml.safe_dump(parsed, sort_keys=False))


@pytest.mark.api_client(edit_templates=_unskippable_code_review)
def test_a_task_that_disallows_skipping_is_not_skipped(client, repo, walked):
    """The task's own flag: its step still allows skipping as a whole."""
    wid = _stopped_in_verification(client, repo)

    r = client.post(f"/api/work-items/{wid}/skip", json={"path": "verification.review.code_review"})

    assert r.status_code == 409, r.text
    assert "does not allow skipping" in r.json()["detail"]
    assert walked == []


def _unskippable_spec_approval(templates_dir):
    chain = templates_dir / "chains" / "default.yaml"
    text = chain.read_text()
    old = "  - id: spec_approval\n    kind: gate\n"
    assert old in text
    chain.write_text(text.replace(old, old + "    skippable: false\n"))


@pytest.mark.api_client(edit_templates=_unskippable_spec_approval)
def test_a_pending_gate_that_disallows_skipping_is_not_skipped(client, repo, walked):
    """Kraft-v1iz2: the no-path `/skip` on a pending gate honours the gate's own
    `skippable: false`."""
    from kraft import store

    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    db = client.app.state.db

    async def at_the_gate():
        await db.write(lambda c: store.load_chain(c, wid, "spec_approval"))
        await db.write(lambda c: store.request_gate(c, wid, "spec_approval", "spec_approval"))

    client.portal.call(at_the_gate)

    r = client.post(f"/api/work-items/{wid}/skip", json={})

    assert r.status_code == 409, r.text
    assert "'spec_approval' does not allow skipping" in r.json()["detail"]
    assert client.get(f"/api/work-items/{wid}").json()["pending_gate"] == "spec_approval"
