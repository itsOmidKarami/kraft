from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch, *, templates_dir=None):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv(
        "KRAFT_TEMPLATES_DIR",
        str(templates_dir or fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))),
    )
    import kraft.api as api

    return TestClient(api.app)


def _poll_events(client, wid, want, timeout=30):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        seen = client.get(f"/work-items/{wid}/events").json()
        if any(e["type"] == want for e in seen):
            return seen
        time.sleep(0.2)
    raise AssertionError(f"{want} not seen; got {[e['type'] for e in seen]}")


def test_health_ok_and_degraded(tmp_path, monkeypatch):
    # a templates dir with one bad-hook template
    bad = fake_templates_dir(tmp_path, "claude")
    (bad / "broken.yaml").write_text(
        "id: broken\nnodes:\n  - {id: x, tasks: [on.nope], gate_after: null}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=bad) as client:
        body = client.get("/health").json()
        assert body["status"] == "degraded"
        assert "broken" in body["invalid_templates"]
        assert "reattach_summary" in body


def test_post_materializes_chain(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/work-items", json={"title": "make the failing test pass", "repo": str(repo)}
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["current_node_id"] == "env_setup"
        assert [n["id"] for n in body["chain_definition"]["nodes"]] == [
            "env_setup",
            "implementation",
            "verify",
        ]
        assert body["bead_id"]


def test_post_invalid_template_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/work-items", json={"title": "x", "repo": "/tmp", "chain_template": "nope"}
        )
        assert r.status_code == 422


def test_post_missing_repo_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post("/work-items", json={"title": "x"})
        assert r.status_code == 422


def test_happy_path_via_api(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items", json={"title": "make the failing test pass", "repo": str(repo)}
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed")

        item = client.get(f"/work-items/{wid}").json()
        assert item["status"] == "completed"
        assert len(item["worker_sessions"]) == 3

        run_dir = Path(client.app.state.run_dirs.base)
        assert "a + b" in (run_dir / "worktrees" / wid / "calc.py").read_text()

        # log endpoint returns the agent's stdout
        impl = next(s for s in item["worker_sessions"] if s["node_id"] == "implementation")
        log = client.get(f"/worker-sessions/{impl['id']}/log")
        assert log.status_code == 200
        assert "is_error" in log.text


def test_executor_crash_marks_needs_human(tmp_path, monkeypatch):
    """A non-task exception in the spawned run task must not wedge the item in 'active'."""
    repo = make_repo(tmp_path)
    import kraft.api as api

    async def boom(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api.executor, "run", boom)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post("/work-items", json={"title": "x", "repo": str(repo)}).json()["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = client.get(f"/work-items/{wid}").json()["status"]
            if status == "needs_human":
                break
            time.sleep(0.2)
        assert status == "needs_human"


def test_post_nonexistent_repo_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post("/work-items", json={"title": "x", "repo": "/no/such/dir"})
        assert r.status_code == 422


def test_get_unknown_work_item_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/work-items/does-not-exist").status_code == 404
        assert client.get("/worker-sessions/nope/log").status_code == 404


def _post_default(client, repo):
    return client.post(
        "/work-items",
        json={
            "title": "make the failing test pass",
            "repo": str(repo),
            "chain_template": "default",
        },
    ).json()["id"]


def test_gate_approve_advances_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        item = client.get(f"/work-items/{wid}").json()
        assert item["status"] == "needs_human"

        r = client.post(f"/work-items/{wid}/gates/spec_approval/approve")
        assert r.status_code == 200, r.text
        _poll_events(client, wid, "gate_approved")
        assert any(
            e["type"] == "node_started" and e["payload"]["node_id"] == "plan"
            for e in client.get(f"/work-items/{wid}/events").json()
        )


def test_gate_approve_wrong_gate_409(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        r = client.post(f"/work-items/{wid}/gates/plan_approval/approve")
        assert r.status_code == 409


def test_gate_unknown_name_404(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        assert client.post(f"/work-items/{wid}/gates/not_a_gate/approve").status_code == 404


def test_gate_reject_requires_note_and_is_terminal(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")

        assert (
            client.post(f"/work-items/{wid}/gates/spec_approval/reject", json={}).status_code == 422
        )

        r = client.post(f"/work-items/{wid}/gates/spec_approval/reject", json={"note": "too vague"})
        assert r.status_code == 200, r.text
        evts = client.get(f"/work-items/{wid}/events").json()
        rej = [e for e in evts if e["type"] == "gate_rejected"]
        assert rej and rej[0]["payload"] == {"gate": "spec_approval", "note": "too vague"}
        assert client.get(f"/work-items/{wid}").json()["status"] == "needs_human"


def test_gate_approve_unknown_work_item_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.post("/work-items/nope/gates/spec_approval/approve").status_code == 404
