from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
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
    # Hermetic against a real frontend/dist appearing (3B `npm run build`);
    # a test that already pinned its own dist keeps it.
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
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


def test_health_reports_invalid_policy(tmp_path, monkeypatch):
    bad = fake_templates_dir(tmp_path, "claude")
    (bad / "policy.yaml").write_text("default: { attempts: 0, wall_clock_s: 1 }\n")
    with _client(tmp_path, monkeypatch, templates_dir=bad) as client:
        h = client.get("/health").json()
        assert h["status"] == "degraded"
        assert h["invalid_policy"]


def test_health_ok_with_valid_policy(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        h = client.get("/health").json()
        assert h["invalid_policy"] == []


def test_post_refused_when_policy_invalid(tmp_path, monkeypatch):
    bad = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (bad / "policy.yaml").write_text("default: { attempts: 0, wall_clock_s: 1 }\n")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch, templates_dir=bad) as client:
        r = client.post(
            "/work-items",
            json={"title": "x", "repo": str(repo), "chain_template": "default"},
        )
        assert r.status_code != 201
        assert "policy" in r.json()["detail"].lower()


def test_default_chain_fix_loop_breach_over_http(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")
    repo = make_repo(tmp_path)
    # Own policy fixture — do not gate on the shipped attempts value.
    tdir = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (tdir / "policy.yaml").write_text(
        "loops:\n  verify_fix_loop: { attempts: 2, wall_clock_s: 3600 }\n"
        "default: { attempts: 2, wall_clock_s: 3600 }\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=tdir) as client:
        wid = client.post(
            "/work-items",
            json={
                "title": "make the failing test pass",
                "repo": str(repo),
                "chain_template": "default",
            },
        ).json()["id"]
        # verify (fix_loop) sits before the human_review gate, so the cap breach
        # drops the item to needs_human before human_review_approval is ever reached.
        for gate in ("spec_approval", "plan_approval", "chain_finalized"):
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                r = client.post(f"/work-items/{wid}/gates/{gate}/approve")
                if r.status_code == 200:
                    break
                time.sleep(0.2)
            assert r.status_code == 200, r.text
        events = _poll_events(client, wid, "work_item_needs_human", timeout=60)

        assert len([e for e in events if e["type"] == "fix_cycle_started"]) == 2

        item = client.get(f"/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert item["current_node_id"] == "verify"
        assert any(
            s["node_id"] == "verify" and s["status"] == "capped_out"
            for s in item["worker_sessions"]
        )


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


def test_list_work_items_shape_and_cursor(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post(
            "/work-items", json={"title": "make the failing test pass", "repo": str(tmp_path)}
        )
        body = client.get("/work-items").json()
        assert set(body) == {"items", "cursor"}
        assert isinstance(body["cursor"], int) and body["cursor"] > 0
        item = body["items"][0]
        assert set(item) >= {
            "id",
            "title",
            "repo",
            "status",
            "chain_template",
            "chain_definition",
            "current_node_id",
            "bead_id",
            "created_at",
            "updated_at",
        }
        assert isinstance(item["chain_definition"], dict)
        assert item["chain_definition"]["nodes"][0]["id"]


def test_list_work_items_empty(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        body = client.get("/work-items").json()
        assert body == {"items": [], "cursor": 0}


def test_templates_lists_only_resolvable_sorted(tmp_path, monkeypatch):
    bad = fake_templates_dir(tmp_path, "claude")
    (bad / "broken.yaml").write_text(
        "id: broken\nnodes:\n  - {id: x, tasks: [on.nope], gate_after: null}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=bad) as client:
        got = client.get("/templates").json()
        ids = [t["id"] for t in got]
        assert "broken" not in ids
        assert ids == sorted(ids)
        assert "quick-task" in ids


def test_spa_catchall_serves_index_when_dist_present(tmp_path, monkeypatch):
    dist = tmp_path / "fe-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>kraft</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    with _client(tmp_path, monkeypatch) as client:
        assert "<title>kraft</title>" in client.get("/").text
        # browser navigation deep link -> index.html
        html = {"accept": "text/html,application/xhtml+xml"}
        assert "<title>kraft</title>" in client.get("/work-items/abc123", headers=html).text
        # XHR (Accept: application/json) still gets a real JSON 404
        r = client.get("/work-items/abc123", headers={"accept": "application/json"})
        assert r.status_code == 404
        assert "detail" in r.json()
        r = client.get(
            "/worker-sessions/does-not-exist/log", headers={"accept": "application/json"}
        )
        assert r.status_code == 404
        assert "detail" in r.json()
        # real asset -> that file
        assert client.get("/assets/app.js").text == "console.log(1)"
        # API route still wins
        assert client.get("/health").json()["status"] in ("ok", "degraded")
        assert client.get("/work-items").json() == {"items": [], "cursor": 0}
        # browser navigation to a path that shadows a real API route (never 404s,
        # so the exception handler can't catch it) -> SPA shell, not raw JSON
        nav = {"sec-fetch-dest": "document"}
        assert "<title>kraft</title>" in client.get("/work-items", headers=nav).text
        assert "<title>kraft</title>" in client.get("/health", headers=nav).text
        # a script/style/XHR fetch (dest != document) still hits the API
        assert client.get("/health", headers={"sec-fetch-dest": "empty"}).json()["status"]


@pytest.mark.parametrize(
    "path",
    ["../secret", "../../etc/passwd", "/etc/passwd", "//etc/passwd", "assets/../../secret"],
)
def test_spa_catchall_never_serves_files_outside_dist(tmp_path, monkeypatch, path):
    dist = tmp_path / "fe-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>kraft shell</title>")
    (tmp_path / "secret").write_text("TOP SECRET")
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    with _client(tmp_path, monkeypatch) as client:
        r = client.get(f"/{path}")
        assert r.status_code == 200
        assert "TOP SECRET" not in r.text
        assert "kraft shell" in r.text


def test_spa_catchall_404s_when_dist_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "nope"))
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/some/spa/route").status_code == 404
        assert client.get("/health").status_code == 200
