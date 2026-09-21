"""Pause / steer / resume (02 §10.2, design 4c).

An agent CLI is a one-shot subprocess with no stdin, so pausing means killing
the current attempt and resuming means launching a fresh one — optionally with
a human's note leading its prompt.
"""

from __future__ import annotations

import asyncio
import signal
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from support.harness import _git, fake_templates_dir

from kraft.config import git_read

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"
#: quick-task's implementer, by its canonical path.
_IMPLEMENT = "implementation.main.implement"


@pytest.fixture
def templates_dir(tmp_path):
    """noop_verify: these tests assert on pause/resume/rebase, not on verify's
    real `python -m pytest -q` subprocess -- pure incidental cost here."""
    return fake_templates_dir(tmp_path, str(_FAKE_CLAUDE), noop_verify=True)


# ponytail: the four "resumed item to complete" waits below run real git
# worktree/rebase ops -- CI-load-fragile (Kraft-6dqk, Kraft-x527: passes in
# ~7s locally, ate the full prior 120s budget twice on a loaded shared
# runner). Bumped to 300s rather than making the wait event-driven; revisit
# if it still times out. the `templates_dir` above passes `noop_verify=True`, so the
# verify node itself is a no-op here -- it was a real `uv run pytest -q`
# subprocess when Kraft-6dqk/x527 were filed, but that's no longer what
# these waits are paying for.
def _wait(fn, what, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = fn()
        if got:
            return got
        time.sleep(0.15)
    raise AssertionError(f"timed out waiting for {what}")


def _running_agent(client, wid):
    def check():
        rows = client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
        return next(
            (s for s in rows if s["hook_point"] == _IMPLEMENT and s["status"] == "running"),
            None,
        )

    return _wait(check, "a running agent session")


def test_pause_then_resume_with_a_steer_relaunches_the_task(tmp_path, monkeypatch, repo, client):
    # a slow agent gives the test a live session to interrupt
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_PROMPT_LOG", str(prompts))

    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "pause me", "chain_template": "quick-task"},
    ).json()["id"]
    session = _running_agent(client, wid)

    r = client.post(f"/api/work-items/{wid}/pause", json={})
    assert r.status_code == 200
    assert r.json()["paused_sessions"] == [session["id"]]

    item = _wait(
        lambda: (lambda b: b if b["status"] == "paused" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the item to read paused",
    )
    paused = next(s for s in item["worker_sessions"] if s["id"] == session["id"])
    # the SIGTERM's non-zero exit must not re-resolve the row as a failure
    assert paused["status"] == "paused"
    types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
    assert "pause_requested" in types and "worker_session_paused" in types

    # pausing twice is a client error, not a second kill
    assert client.post(f"/api/work-items/{wid}/pause", json={}).status_code == 409

    assert client.post(f"/api/work-items/{wid}/steer", json={"text": "  "}).status_code == 400
    assert (
        client.post(
            f"/api/work-items/{wid}/steer", json={"text": "keep the old signature"}
        ).status_code
        == 200
    )

    # this time let the agent finish
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    r = client.post(f"/api/work-items/{wid}/resume", json={})
    assert r.status_code == 200
    assert r.json()["steer"] == "keep the old signature"

    _wait(
        lambda: any(
            e["type"] == "work_item_completed"
            for e in client.get(f"/api/work-items/{wid}/events").json()
        ),
        "the resumed item to complete",
        timeout=300,
    )

    # a fresh session ran the same hook, and the steer led its prompt exactly once
    rows = client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
    impl = [s for s in rows if s["hook_point"] == _IMPLEMENT]
    assert len(impl) == 2
    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    steered = [p for p in sent if "keep the old signature" in p]
    assert len(steered) == 1
    assert steered[0].startswith("A human has steered this run:")

    # the steer is spent, and the counters were never touched
    assert client.get(f"/api/work-items/{wid}").json()["pending_steer_context"] is None
    types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
    assert "steer_context_set" in types and "work_item_resumed" in types


def test_steer_and_resume_are_refused_while_the_item_is_running(monkeypatch, repo, client):
    """Also the needs_context guard's other half: that guard widened what
    steer/resume accept, it did not remove the refusal for an item mid-run."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "10")
    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "busy", "chain_template": "quick-task"},
    ).json()["id"]
    _running_agent(client, wid)
    assert client.post(f"/api/work-items/{wid}/steer", json={"text": "x"}).status_code == 409
    assert client.post(f"/api/work-items/{wid}/resume", json={}).status_code == 409
    assert client.post("/api/work-items/nope/pause", json={}).status_code == 404
    client.post(f"/api/work-items/{wid}/pause", json={})


async def test_a_paused_row_and_its_event_never_disagree(database):
    """reattach calls session_exited('failed') on a child that dies after a
    restart. If only the row were guarded, the event would still say 'failed' and
    the UI would follow the event."""

    from kraft import events, store

    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "('w','t','/r','quick-task','{}','active','now','now')"
        )
    )
    await database.write(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id="w",
            node_id="verify",
            hook_point="on.test.run",
            log_path="l",
            result_path="r",
        )
    )
    await database.write(lambda c: store.pause_work_item(c, "w", ["s1"]))
    # the SIGTERMed child's exit arrives afterwards
    await database.write(lambda c: store.session_exited(c, "s1", "failed"))
    row = database.read(
        lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
    )
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w"))]
    assert row["status"] == "paused"
    assert "worker_session_exited" not in types


async def test_a_pause_catches_a_session_still_in_its_pending_window(database):
    """A row is inserted before Popen returns. A pause landing in that window has
    to mark it, and the launch finishing must not undo the mark."""

    from kraft import store

    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, "
            "chain_definition, status, current_node_id, created_at, updated_at) "
            "VALUES ('w','t','/r','quick-task','{}','active','verify','now','now')"
        )
    )
    await database.write(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id="w",
            node_id="verify",
            hook_point="on.test.run",
            log_path="l",
            result_path="r",
        )
    )
    pending = database.read(lambda c: store.running_sessions_for_node(c, "w"))
    await database.write(lambda c: store.pause_work_item(c, "w", [r["id"] for r in pending]))
    # the launch completes a moment later
    await database.write(lambda c: store.session_running(c, "s1", 999, 1.0))
    assert [r["id"] for r in pending] == ["s1"]
    assert database.read(lambda c: store.session_status(c, "s1")) == "paused"


def test_resume_rebases_the_worktree_onto_a_moved_head(monkeypatch, repo, client):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")

    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "refresh me", "chain_template": "quick-task"},
    ).json()["id"]
    _running_agent(client, wid)
    assert client.post(f"/api/work-items/{wid}/pause", json={}).status_code == 200
    _wait(
        lambda: (lambda b: b if b["status"] == "paused" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the item to read paused",
    )

    # repo's default branch moves on while the item sits paused
    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on")
    new_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    r = client.post(f"/api/work-items/{wid}/resume", json={})
    assert r.status_code == 200

    item = _wait(
        lambda: (lambda b: b if b["status"] == "completed" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the resumed item to complete",
        timeout=300,
    )
    assert item["base_ref"] == new_head
    worktree = Path(item["worktree_path"])
    assert (worktree / "moved.txt").is_file()


def test_resume_records_a_mismatch_if_the_worktree_moved_after_rebase(monkeypatch, repo, client):
    """Kraft-vd8d: a rebase Kraft recorded that the worktree did not have,
    moments later. Simulate the race directly rather than chasing the
    one-off -- wrap refresh_worktree_base so that, after it does the real
    rebase and picks the sha to report, something else moves the worktree off
    that commit before the caller reads it back. The event resume writes must
    show the mismatch, not silently agree with the sha it was handed.
    """
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")

    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "raced rebase", "chain_template": "quick-task"},
    ).json()["id"]
    _running_agent(client, wid)
    assert client.post(f"/api/work-items/{wid}/pause", json={}).status_code == 200
    _wait(
        lambda: (lambda b: b if b["status"] == "paused" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the item to read paused",
    )

    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on")

    import kraft.builtins as builtins_mod

    real_refresh = builtins_mod.refresh_worktree_base

    async def racy_refresh(worktree_arg, repo_arg, branch_arg, **kw):
        new_head = await real_refresh(worktree_arg, repo_arg, branch_arg, **kw)
        if new_head:
            # something else (a later chain node, a concurrent walk)
            # moves the worktree off the commit refresh_worktree_base
            # just produced, before resume's caller reads HEAD back.
            _git(worktree_arg, "checkout", "-q", "--detach", "HEAD~1")
        return new_head

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", racy_refresh)

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    r = client.post(f"/api/work-items/{wid}/resume", json={})
    assert r.status_code == 200

    events = client.get(f"/api/work-items/{wid}/events").json()
    verified = [e for e in events if e["type"] == "worktree_rebase_verified"]
    assert verified, "no worktree_rebase_verified event was written"
    payload = verified[-1]["payload"]
    assert payload["reported_head"] != payload["worktree_head"], (
        "the event must show the mismatch, not hide it"
    )


def test_resume_skips_rebase_when_worktree_is_dirty(monkeypatch, repo, client):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")

    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "dirty resume", "chain_template": "quick-task"},
    ).json()["id"]
    _running_agent(client, wid)
    assert client.post(f"/api/work-items/{wid}/pause", json={}).status_code == 200
    item = _wait(
        lambda: (lambda b: b if b["status"] == "paused" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the item to read paused",
    )
    before_base_ref = item["base_ref"]
    worktree = Path(item["worktree_path"])
    # models a SIGTERM catching the agent mid-edit, nothing committed yet
    (worktree / "midedit.txt").write_text("uncommitted work\n")

    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on")

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    r = client.post(f"/api/work-items/{wid}/resume", json={})
    assert r.status_code == 200

    _wait(
        lambda: (lambda b: b if b["status"] == "completed" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the resumed item to reach a terminal state",
        timeout=300,
    )
    # dirty file survived untouched -- rebase was skipped, not stashed
    assert (worktree / "midedit.txt").read_text() == "uncommitted work\n"
    assert client.get(f"/api/work-items/{wid}").json()["base_ref"] == before_base_ref


def test_resume_marks_needs_human_on_a_rebase_conflict(monkeypatch, repo, client):
    """A rebase conflict at `/resume` stops the item for a human, and
    auto-escalation (armed by default) follows.

    Kraft-s7c04.23 dispatched a rebase-conflict *resolver* agent first; that
    resolver (`walk.resolve_rebase_conflict`) was deleted with the legacy
    rebase layer (Task 4a), and V1 resolves a conflict only through a chain's
    explicit handler (Task 7, `rebase-conflict-requires-explicit-handler`).
    The route still called it and raised `AttributeError` -- so this now pins
    that no resolver runs, rather than that one does.

    The status assertion is a poll, not the `/resume` response's own body:
    the stop-and-escalate sequence is the item's own spawned task (review
    findings 1 & 2), so the route returns before the stop is recorded.
    """
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")

    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "conflict resume", "chain_template": "quick-task"},
    ).json()["id"]
    _running_agent(client, wid)
    assert client.post(f"/api/work-items/{wid}/pause", json={}).status_code == 200
    item = _wait(
        lambda: (lambda b: b if b["status"] == "paused" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the item to read paused",
    )
    worktree = Path(item["worktree_path"])

    # the branch already has a commit touching calc.py, as an earlier node would
    (worktree / "calc.py").write_text("def add(a, b):\n    return a - b - 1  # bug: should be +\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "worktree edit")

    # repo's default branch changes the same line while the item sits paused
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b - 2  # bug: should be +\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "conflicting edit")

    # The initial running-agent setup needed the 30s "slow" mode so pause
    # had something real to interrupt; the escalation this test drives
    # through next doesn't, and paying it again would double this test's
    # wall-clock cost for nothing. And the escalation must not edit the
    # worktree itself, or the cleanliness check below reads its edit as
    # a rebase that was left half-applied.
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "0")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")

    r = client.post(f"/api/work-items/{wid}/resume", json={})
    assert r.status_code == 200

    item = _wait(
        lambda: (lambda b: b if b["status"] == "needs_human" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the item to read needs_human",
    )

    needs_human = [
        e
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "work_item_needs_human"
    ]
    # Not "conflict": git's own wording for a failed rebase varies by
    # version (Kraft-3a8m saw "could not apply ..." with no "conflict"
    # substring at all on some runners). "rebase failed for" is
    # builtins.py's own literal, version-independent of git's message.
    assert needs_human and "rebase failed for" in needs_human[-1]["payload"]["reason"].lower()

    sessions = _wait(
        lambda: (lambda rows: rows if any(s["hook_point"] == "escalation" for s in rows) else None)(
            client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
        ),
        "the escalation session",
    )
    # The implementer ran once, before the pause, and nothing re-ran it.
    assert [s["hook_point"] for s in sessions].count(_IMPLEMENT) == 1
    escalation_sessions = [s for s in sessions if s["hook_point"] == "escalation"]
    assert len(escalation_sessions) == 1, "escalation did not follow the conflict stop"
    # fake-claude.sh writes its own session summary under
    # `.engineering/sessions/` -- an untracked directory left behind by
    # that, not evidence of anything left uncommitted.
    dirty = [
        line
        for line in (git_read(worktree, "status", "--porcelain") or "").splitlines()
        if ".engineering" not in line
    ]
    assert not dirty, dirty


def test_resume_skips_rebase_when_branch_already_pushed(tmp_path, monkeypatch, repo, client):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")

    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "pushed resume", "chain_template": "quick-task"},
    ).json()["id"]
    _running_agent(client, wid)
    assert client.post(f"/api/work-items/{wid}/pause", json={}).status_code == 200
    item_before = _wait(
        lambda: (lambda b: b if b["status"] == "paused" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the item to read paused",
    )
    worktree = Path(item_before["worktree_path"])
    _git(worktree, "push", "-q", "-u", "origin", item_before["branch"])

    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on")

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    r = client.post(f"/api/work-items/{wid}/resume", json={})
    assert r.status_code == 200

    _wait(
        lambda: (lambda b: b if b["status"] == "completed" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the resumed item to complete",
        timeout=300,
    )
    # rebase was skipped: repo's moved.txt never reached the worktree, and
    # base_ref (recorded at first dispatch) is untouched, even though the
    # chain's own commits (e.g. its wip-work commit) still moved HEAD on.
    assert not (worktree / "moved.txt").is_file()
    assert client.get(f"/api/work-items/{wid}").json()["base_ref"] == item_before["base_ref"]


_WAIT_CHAIN = (
    '{"template_id": "quick-task", "nodes": ['
    '{"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": null},'
    '{"id": "mr_checks", "tasks": ["on.ci.poll"], "gate_after": null}]}'
)


def _seed_waiting(client, repo, wid="w1"):
    """A node parked on a pipeline (Kraft-ru98), built straight from `store` so
    no real agent or forge CLI ever runs -- these tests are about the status
    guard on pause/abandon, not the wait itself.

    Run through `client.portal` -- the app's writer connection was opened in
    TestClient's own portal thread (`sqlite3` objects are thread-bound), and
    `db.Database.write` queues onto that thread's event loop, so the write has
    to run there too rather than in a loop this sync test function starts.
    """
    from kraft import store

    async def go():
        db_ = client.app.state.db
        await db_.write(
            lambda c: store.create_work_item(
                c,
                id=wid,
                bead_id=None,
                title="t",
                repo=str(repo),
                chain_template="quick-task",
                chain_definition=_WAIT_CHAIN,
            )
        )
        await db_.write(lambda c: store.enter_node(c, wid, "mr_checks"))
        await db_.write(
            lambda c: store.mark_waiting(c, wid, "mr_checks", "2099-01-01T00:00:00+00:00")
        )

    client.portal.call(go)


def test_pausing_a_waiting_item_is_accepted(monkeypatch, repo, client):
    """It used to 409: only 'active' was pausable, and a parked item is not
    active any more (Kraft-tnak)."""
    _seed_waiting(client, repo)
    r = client.post("/api/work-items/w1/pause", json={})
    assert r.status_code == 200
    assert client.get("/api/work-items/w1").json()["status"] == "paused"


def test_pausing_a_waiting_item_clears_retry_at(monkeypatch, repo, client):
    """Otherwise ci_wait.tick wakes it straight back up -- the pause would look
    like it worked and then silently undo itself."""
    _seed_waiting(client, repo)
    assert client.post("/api/work-items/w1/pause", json={}).status_code == 200
    assert client.get("/api/work-items/w1").json()["retry_at"] is None


def test_a_paused_item_is_not_woken_by_the_ci_wait_poller(monkeypatch, repo, client):
    """The behavioural assertion the other two exist to support: run tick()
    after the pause and assert nothing was re-entered."""
    from kraft import ci_wait

    _seed_waiting(client, repo)
    assert client.post("/api/work-items/w1/pause", json={}).status_code == 200
    assert client.portal.call(ci_wait.tick, client.app) == []
    assert client.get("/api/work-items/w1").json()["status"] == "paused"


def test_abandoning_a_waiting_item_is_accepted(monkeypatch, repo, client):
    """abandon refuses only 'active' (kraft.api.routes.lifecycle) -- a waiting item has no session
    to stop, so it can go straight to abandoned and reclaim its worktree."""
    _seed_waiting(client, repo)
    r = client.post("/api/work-items/w1/abandon", json={})
    assert r.status_code == 200
    assert client.get("/api/work-items/w1").json()["status"] == "abandoned"


def test_pause_cancels_the_walk_task_not_just_the_session(monkeypatch, repo, client):
    """Kraft-e7pm: pause must stop the walk itself, not only signal the
    session -- otherwise the task that was awaiting it keeps running past the
    live node into the next one."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")

    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "pause me", "chain_template": "quick-task"},
    ).json()["id"]
    _running_agent(client, wid)

    r = client.post(f"/api/work-items/{wid}/pause", json={})
    assert r.status_code == 200

    task = client.app.state.tasks.get(wid)
    assert task is None or task.done(), "pause returned before the walk task actually stopped"


def test_two_concurrent_resumes_produce_one_two_hundred_and_one_409(monkeypatch, repo, client):
    """Kraft-11e0. Two callers racing `/resume` on the same paused item must
    produce exactly one winner and one 409 -- never two walks."""
    wid = client.post(
        "/api/work-items",
        json={
            "repo": str(repo),
            "title": "t",
            "chain_template": "quick-task",
            "autostart": False,
        },
    ).json()["id"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "paused"
    app = client.app

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://kraft") as ac:
            return await asyncio.gather(
                ac.post(f"/api/work-items/{wid}/resume", json={}),
                ac.post(f"/api/work-items/{wid}/resume", json={}),
            )

    # Run through `client.portal` -- the app's writer connection was opened
    # in TestClient's own portal thread, so both concurrent requests have
    # to run on that thread's event loop too (same reason as `_seed_waiting`
    # above).
    a, b = client.portal.call(scenario)
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    assert len(client.app.state.tasks) <= 1


def test_pause_interrupts_the_agent_rather_than_terminating_it(monkeypatch):
    """Kraft-s7c04.18: SIGTERM kills a claude process mid-turn without a word.
    SIGINT makes it flush the result envelope carrying total_cost_usd, which is
    the only cost figure Kraft will ever have for a session a human stopped.
    Measured against claude 2.1.273."""
    from kraft.api.routes import lifecycle

    sent = []
    monkeypatch.setattr(lifecycle.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(lifecycle.os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))

    lifecycle._terminate(4242)

    assert sent == [(4242, signal.SIGINT)]


def test_terminate_still_swallows_a_process_that_is_already_gone(monkeypatch):
    """Unchanged posture: the row moves to paused either way."""
    from kraft.api.routes import lifecycle

    def boom(pgid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(lifecycle.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(lifecycle.os, "killpg", boom)

    lifecycle._terminate(4242)  # must not raise
    lifecycle._terminate(None)  # must not raise


def test_resume_leaves_the_position_to_the_walk(monkeypatch, repo, client):
    """Kraft-c3dab: `/resume` relaunched at the paused node's first step, so a
    pause during verification reran the implementer. It hands the walk no
    position now; `walk.run_once` resumes at the item's cursor
    (tests/executor/test_entry_paths.py)."""
    from kraft import executor, store

    seen = {}

    async def fake_run(*args, **kw):
        seen.update(kw)
        return "completed"

    monkeypatch.setattr(executor, "run", fake_run)
    _seed_waiting(client, repo)
    assert client.post("/api/work-items/w1/pause", json={}).status_code == 200
    client.portal.call(
        lambda: client.app.state.db.write(lambda c: store.set_current_step(c, "w1", 1))
    )

    assert client.post("/api/work-items/w1/resume", json={}).status_code == 200
    _wait(lambda: seen, "the resumed walk")
    assert "start_index" not in seen and "start_step" not in seen
    assert seen["work_item_id"] == "w1"
