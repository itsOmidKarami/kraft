"""Pause / steer / resume (02 §10.2, design 4c).

An agent CLI is a one-shot subprocess with no stdin, so pausing means killing
the current attempt and resuming means launching a fresh one — optionally with
a human's note leading its prompt.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient
from support.harness import _git, fake_templates_dir, isolated_bd, make_repo

from kraft.config import git_read

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    return TestClient(api.app, client=("127.0.0.1", 54321))


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
            (
                s
                for s in rows
                if s["hook_point"] == "on.implementation.start" and s["status"] == "running"
            ),
            None,
        )

    return _wait(check, "a running agent session")


def test_pause_then_resume_with_a_steer_relaunches_the_task(tmp_path, monkeypatch):
    # a slow agent gives the test a live session to interrupt
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")
    repo = make_repo(tmp_path)
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_PROMPT_LOG", str(prompts))

    with _client(tmp_path, monkeypatch) as client:
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
            timeout=120,
        )

        # a fresh session ran the same hook, and the steer led its prompt exactly once
        rows = client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
        impl = [s for s in rows if s["hook_point"] == "on.implementation.start"]
        assert len(impl) == 2
        sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
        steered = [p for p in sent if "keep the old signature" in p]
        assert len(steered) == 1
        assert steered[0].startswith("A human has steered this run:")

        # the steer is spent, and the counters were never touched
        assert client.get(f"/api/work-items/{wid}").json()["pending_steer_context"] is None
        types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
        assert "steer_context_set" in types and "work_item_resumed" in types


def test_steer_and_resume_are_refused_while_the_item_is_running(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "10")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "busy", "chain_template": "quick-task"},
        ).json()["id"]
        _running_agent(client, wid)
        assert client.post(f"/api/work-items/{wid}/steer", json={"text": "x"}).status_code == 409
        assert client.post(f"/api/work-items/{wid}/resume", json={}).status_code == 409
        assert client.post("/api/work-items/nope/pause", json={}).status_code == 404
        client.post(f"/api/work-items/{wid}/pause", json={})


def test_a_paused_row_and_its_event_never_disagree(tmp_path):
    """reattach calls session_exited('failed') on a child that dies after a
    restart. If only the row were guarded, the event would still say 'failed' and
    the UI would follow the event."""
    import asyncio

    from kraft import db, events, store

    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
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
            return row["status"], types
        finally:
            await database.close()

    status, types = asyncio.run(scenario())
    assert status == "paused"
    assert "worker_session_exited" not in types


def test_a_pause_catches_a_session_still_in_its_pending_window(tmp_path):
    """A row is inserted before Popen returns. A pause landing in that window has
    to mark it, and the launch finishing must not undo the mark."""
    import asyncio

    from kraft import db, store

    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
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
            await database.write(
                lambda c: store.pause_work_item(c, "w", [r["id"] for r in pending])
            )
            # the launch completes a moment later
            await database.write(lambda c: store.session_running(c, "s1", 999, 1.0))
            return [r["id"] for r in pending], database.read(
                lambda c: store.session_status(c, "s1")
            )
        finally:
            await database.close()

    caught, status = asyncio.run(scenario())
    assert caught == ["s1"]


def test_resume_rebases_the_worktree_onto_a_moved_head(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")
    repo = make_repo(tmp_path)

    with _client(tmp_path, monkeypatch) as client:
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
            timeout=120,
        )
        assert item["base_ref"] == new_head
        worktree = Path(item["worktree_path"])
        assert (worktree / "moved.txt").is_file()


def test_resume_skips_rebase_when_worktree_is_dirty(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")
    repo = make_repo(tmp_path)

    with _client(tmp_path, monkeypatch) as client:
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
            timeout=120,
        )
        # dirty file survived untouched -- rebase was skipped, not stashed
        assert (worktree / "midedit.txt").read_text() == "uncommitted work\n"
        assert client.get(f"/api/work-items/{wid}").json()["base_ref"] == before_base_ref


def test_resume_marks_needs_human_on_a_rebase_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")
    repo = make_repo(tmp_path)

    with _client(tmp_path, monkeypatch) as client:
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
        (worktree / "calc.py").write_text(
            "def add(a, b):\n    return a - b - 1  # bug: should be +\n"
        )
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-m", "worktree edit")

        # repo's default branch changes the same line while the item sits paused
        (repo / "calc.py").write_text("def add(a, b):\n    return a - b - 2  # bug: should be +\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "conflicting edit")

        before_sessions = len(client.get(f"/api/work-items/{wid}").json()["worker_sessions"])

        r = client.post(f"/api/work-items/{wid}/resume", json={})
        assert r.status_code == 200
        assert r.json()["status"] == "needs_human"

        needs_human = [
            e
            for e in client.get(f"/api/work-items/{wid}/events").json()
            if e["type"] == "work_item_needs_human"
        ]
        assert needs_human and "conflict" in needs_human[-1]["payload"]["reason"].lower()

        after = client.get(f"/api/work-items/{wid}").json()
        assert len(after["worker_sessions"]) == before_sessions
        assert git_read(worktree, "status", "--porcelain") == ""


def test_resume_skips_rebase_when_branch_already_pushed(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    repo = make_repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")

    with _client(tmp_path, monkeypatch) as client:
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
            timeout=120,
        )
        # rebase was skipped: repo's moved.txt never reached the worktree, and
        # base_ref (recorded at first dispatch) is untouched, even though the
        # chain's own commits (e.g. its wip-work commit) still moved HEAD on.
        assert not (worktree / "moved.txt").is_file()
        assert client.get(f"/api/work-items/{wid}").json()["base_ref"] == item_before["base_ref"]
