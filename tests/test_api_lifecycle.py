"""The happy path end to end, and abandon/resume/retry."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from support.api import _client, _force_node, _poll_events, _post_default, _set_status
from support.harness import make_repo


def test_happy_path_via_api(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={
                "title": "make the failing test pass",
                "repo": str(repo),
                "chain_template": "quick-task",
            },
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed")

        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "completed"
        assert len(item["worker_sessions"]) == 3

        run_dir = Path(client.app.state.run_dirs.base)
        assert "a + b" in (run_dir / "worktrees" / wid / "calc.py").read_text()

        # 4B: each agent session's summary is ingested and linked to the work item
        impl_session = next(s for s in item["worker_sessions"] if s["node_id"] == "implementation")
        assert impl_session["session_summary_ref"] == (
            f".engineering/sessions/{impl_session['id']}.md"
        )
        for _ in range(100):
            docs = client.get(f"/api/work-items/{wid}/documents").json()["documents"]
            if docs:
                break
            time.sleep(0.05)
        summaries = [d for d in docs if d["source_kind"] == "session_summary"]
        assert summaries, f"no session summary linked to {wid}"
        assert {d["path"] for d in summaries} >= {impl_session["session_summary_ref"]}
        assert any(d["worker_session_id"] == impl_session["id"] for d in docs)

        # and the summary is searchable, carrying its links inline
        hits = client.get("/api/search", params={"q": "fake-claude session"}).json()["results"]
        assert hits, "session summary not searchable"
        assert any(
            ln["work_item_id"] == wid for h in hits for ln in h["links"] if ln["work_item_id"]
        )

        # log endpoint returns the agent's stdout
        impl = next(s for s in item["worker_sessions"] if s["node_id"] == "implementation")
        log = client.get(f"/api/worker-sessions/{impl['id']}/log")
        assert log.status_code == 200
        assert "is_error" in log.text


def test_executor_crash_marks_needs_human(tmp_path, monkeypatch):
    """A non-task exception in the spawned run task must not wedge the item in 'active'."""
    repo = make_repo(tmp_path)
    from kraft import executor

    async def boom(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(executor, "run", boom)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post("/api/work-items", json={"title": "x", "repo": str(repo)}).json()["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = client.get(f"/api/work-items/{wid}").json()["status"]
            if status == "needs_human":
                break
            time.sleep(0.2)
        assert status == "needs_human"


def test_retry_refuses_explicit_steer_on_a_node_with_no_agent_task(tmp_path, monkeypatch):
    """Kraft-bz9b: `open_mr` is forge-kind with no fix_loop, so nothing ever
    calls `Steer.take()` for it. `--steer` used to be accepted and echoed back
    as if it would reach the next launch, when it was silently dropped."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _force_node(wid, "open_mr", "needs_human")

        r = client.post(f"/api/work-items/{wid}/retry", json={"steer": "commit the leftover file"})

        assert r.status_code == 409, r.text
        assert "open_mr" in r.json()["detail"]


def test_retry_without_explicit_steer_still_works_on_a_node_with_no_agent_task(
    tmp_path, monkeypatch
):
    """The guard is for text a caller just typed and expects used, not for
    Kraft's own last-rejection carry-forward (Kraft-ko7j) -- that must keep
    working even on a node with nothing to steer."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _force_node(wid, "open_mr", "needs_human")

        r = client.post(f"/api/work-items/{wid}/retry", json={})

        assert r.status_code == 200, r.text


def test_resume_refuses_when_all_slots_are_busy(tmp_path, monkeypatch):
    """A manual start is bounded by the same limit as auto-intake (Kraft-n2d).

    The limit lived only in the intake tick, so `resume` started an item no
    matter how many were already running.
    """
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        busy = _post_default(client, repo)
        _poll_events(client, busy, "gate_requested")
        idle = _post_default(client, repo)
        _poll_events(client, idle, "gate_requested")
        _set_status(busy, "active")
        _set_status(idle, "paused")
        client.app.state.intake["max_concurrent"] = 1

        r = client.post(f"/api/work-items/{idle}/resume", json={})

        assert r.status_code == 409, r.text
        assert "1" in r.json()["detail"]
        assert client.get(f"/api/work-items/{busy}").json()["status"] == "active"


def test_resume_works_when_a_slot_is_free(tmp_path, monkeypatch):
    """The guard must not wedge the ordinary single-item case shut."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "paused")
        client.app.state.intake["max_concurrent"] = 1

        r = client.post(f"/api/work-items/{wid}/resume", json={})

        assert r.status_code == 200, r.text


def test_abandon_sets_terminal_status_and_removes_the_worktree(tmp_path, monkeypatch):
    """A rejected or dead item stayed on the board forever, worktree and all."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
        assert worktree.is_dir(), "fixture never made a worktree; the test would prove nothing"
        _set_status(wid, "paused")

        r = client.post(f"/api/work-items/{wid}/abandon")

        assert r.status_code == 200, r.text
        assert r.json()["status"] == "abandoned"
        assert not worktree.exists()


def test_abandon_reclaims_the_attachment_storage(tmp_path, monkeypatch):
    """The worktree is already reclaimed; the documents that fed it should not
    outlive it in $KRAFT_HOME."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        doc = repo / ".engineering" / "specs" / "s.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("# s\n")
        wid = client.post(
            "/api/work-items",
            json={
                "title": "x",
                "repo": str(repo),
                "attachments": [{"kind": "spec", "path": ".engineering/specs/s.md"}],
            },
        ).json()["id"]
        stored = client.app.state.run_dirs.attachments / wid
        assert stored.is_dir()
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "paused")

        client.post(f"/api/work-items/{wid}/abandon")

        assert not stored.exists()


def test_abandon_refuses_an_active_item(tmp_path, monkeypatch):
    """Pause first. Otherwise this races a running agent's writes."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "active")

        r = client.post(f"/api/work-items/{wid}/abandon")

        assert r.status_code == 409, r.text
        assert "active" in r.json()["detail"]


def test_abandon_kills_a_process_left_running_from_the_worktree(tmp_path, monkeypatch):
    """Kraft-ugm6: a server (or anything else) an agent started by hand from
    inside the worktree is invisible to `pause`'s session teardown and used to
    outlive the directory it was launched from."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
        orphan = subprocess.Popen(["sleep", "60"], cwd=worktree, start_new_session=True)
        try:
            _set_status(wid, "paused")

            r = client.post(f"/api/work-items/{wid}/abandon")

            assert r.status_code == 200, r.text
            for _ in range(50):
                if orphan.poll() is not None:
                    break
                time.sleep(0.1)
            assert orphan.poll() is not None, "orphan process outlived the abandon"
        finally:
            if orphan.poll() is None:
                orphan.kill()
            orphan.wait()


def test_post_triggers_files_a_paused_item(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/api/triggers",
            json={"repo": str(repo), "title": "from a trigger", "description": "d"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "paused"
        assert body["title"] == "from a trigger"
