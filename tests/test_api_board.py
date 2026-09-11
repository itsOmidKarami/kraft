"""The board: list, and the work item detail payload's read-only fields
(deferred findings, concerns, mr_ref, stop_reason, needs_context question,
steerable) and the SPA catch-all."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

import pytest
from support.api import (
    _client,
    _force_node,
    _poll_events,
    _post_default,
    _set_status,
    _wait_for_status,
)
from support.harness import make_repo

from kraft import events, store


def test_list_work_items_shape_and_cursor(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post(
            "/api/work-items", json={"title": "make the failing test pass", "repo": str(tmp_path)}
        )
        body = client.get("/api/work-items").json()
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
        body = client.get("/api/work-items").json()
        assert body == {"items": [], "cursor": 0}


def test_spa_catchall_serves_index_when_dist_present(tmp_path, monkeypatch):
    dist = tmp_path / "fe-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>kraft</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    with _client(tmp_path, monkeypatch) as client:
        assert "<title>kraft</title>" in client.get("/").text
        # browser deep-link on a client-side route -> index.html, regardless of
        # headers, because /work-items/<id> is not a real route: the catch-all
        # is all that's left to answer it.
        html = {"accept": "text/html,application/xhtml+xml"}
        assert "<title>kraft</title>" in client.get("/work-items/abc123", headers=html).text
        assert (
            "<title>kraft</title>"
            in client.get("/work-items/abc123", headers={"accept": "application/json"}).text
        )
        # a genuine 404 under /api/ is always JSON, even from a browser
        # navigation — that prefix is unambiguous, no header can turn it HTML
        r = client.get("/api/work-items/abc123", headers=html)
        assert r.status_code == 404
        assert "detail" in r.json()
        r = client.get("/api/worker-sessions/does-not-exist/log", headers=html)
        assert r.status_code == 404
        assert "detail" in r.json()
        # a bad /api/ path with no matching route at all is also a plain JSON
        # 404, not the shell
        r = client.get("/api/nope", headers=html)
        assert r.status_code == 404
        assert "detail" in r.json()
        # real asset -> that file
        assert client.get("/assets/app.js").text == "console.log(1)"
        # real API routes work
        assert client.get("/api/health").json()["status"] in ("ok", "degraded")
        assert client.get("/api/work-items").json() == {"items": [], "cursor": 0}
        # a forged browser-navigation header on /api/ does nothing: that prefix
        # is unambiguous, so it still answers with real JSON, not the shell
        nav = {"sec-fetch-dest": "document"}
        assert client.get("/api/work-items", headers=nav).json() == {"items": [], "cursor": 0}
        assert client.get("/api/health", headers=nav).json()["status"] in ("ok", "degraded")
        # the same header on a client-side route still fast-paths to the shell,
        # with cache headers so a refresh can't be answered from a stale cache
        shell = client.get("/work-items/abc123", headers=nav)
        assert "<title>kraft</title>" in shell.text
        assert shell.headers["cache-control"] == "no-store"
        assert shell.headers["vary"] == "sec-fetch-dest"


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
        assert client.get("/api/health").status_code == 200


def test_list_hides_abandoned_items(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "paused")
        client.post(f"/api/work-items/{wid}/abandon")

        visible = client.get("/api/work-items").json()["items"]
        everything = client.get("/api/work-items?include_abandoned=true").json()["items"]

        assert wid not in [i["id"] for i in visible]
        assert wid in [i["id"] for i in everything]


def test_cli_can_list_abandoned_items(tmp_path, monkeypatch):
    """`kraft abandon` without a way to see the result makes the item vanish:
    hidden from the board by design, and unreachable from the CLI by omission."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "paused")
        client.post(f"/api/work-items/{wid}/abandon")

        import kraft.client as kc
        from kraft.client import transport

        monkeypatch.setattr(
            transport, "_get", lambda path: _as_coro(client.get(f"/api{path}").json())
        )
        visible = asyncio.run(kc.list_work_items())
        everything = asyncio.run(kc.list_work_items(include_abandoned=True))

        assert wid not in [i["id"] for i in visible]
        assert wid in [i["id"] for i in everything]


def _seed_repo(client, wid, **kwargs):
    """Write a `work_item_repos` row directly — same reasoning as `_seed_events`:
    `Database` exposes only an async `write`, and a multi-repo item's row is
    otherwise only ever created by `ensure_worktree`/the §3a submodule scan,
    neither of which a detail-payload test needs to actually run."""
    db_path = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    conn = sqlite3.connect(db_path)
    try:
        store.add_repo(conn, work_item_id=wid, **kwargs)
        conn.commit()
    finally:
        conn.close()


def _seed_events(client, wid, payloads, event_type="findings_measured"):
    """Write events directly — no orchestration needed to test a read path.
    `Database` exposes only an async `write`, so this opens its own sqlite3
    connection to the run directory's database."""
    db_path = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    conn = sqlite3.connect(db_path)
    try:
        for payload in payloads:
            events.append(conn, wid, event_type, payload)
        conn.commit()
    finally:
        conn.close()


async def _as_coro(value):
    return value


def test_deferred_minor_findings_reach_the_detail_payload(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        nit = {
            "severity": "minor",
            "message": "naming nit",
            "file": "a.py",
            "line": 3,
            "source_plugin": "fake",
        }
        real = {
            "severity": "important",
            "message": "real",
            "file": "a.py",
            "line": 9,
            "source_plugin": "fake",
        }
        payload = {"node_id": "review", "cycle": 0, "findings": [nit, real], "fingerprints": []}
        # twice: the roll-up must deduplicate by fingerprint
        _seed_events(client, wid, [payload, payload])

        body = client.get(f"/api/work-items/{wid}").json()
        assert [f["message"] for f in body["deferred_findings"]] == ["naming nit"]


def test_get_work_item_survives_an_invalid_policy(tmp_path, monkeypatch):
    """startup.py sets app.state.policy to None on a PolicyError; the detail
    route must fall back to NO_BUDGET like every other st.policy reader
    instead of raising AttributeError on st.policy.budget."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        client.app.state.policy = None

        resp = client.get(f"/api/work-items/{wid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == wid


def test_concerns_reach_the_detail_payload(tmp_path, monkeypatch):
    """`done_with_concerns` text rides `worker_session_exited` (written by
    adapters.subprocess.run_task at session exit) — read from the event log,
    the same shape as `deferred_findings`, one entry per session that reported
    a concern."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        _seed_events(
            client,
            wid,
            [{"session_id": "s1", "status": "done_with_concerns", "concerns": "untested path"}],
            event_type="worker_session_exited",
        )

        body = client.get(f"/api/work-items/{wid}").json()
        assert body["concerns"] == ["untested path"]


def test_mr_ref_reaches_the_detail_payload(tmp_path, monkeypatch):
    """A single-repo item gets no `work_item_repos` row (`repos_for`), so
    `mr_opened` (Kraft-d2sq) is the only place its merge request lives --
    the detail screen's "Open MR" link reads it from here."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        assert client.get(f"/api/work-items/{wid}").json()["mr_ref"] is None

        _seed_events(
            client,
            wid,
            [{"number": 12, "url": "https://forge.example/mr/12"}],
            event_type="mr_opened",
        )
        body = client.get(f"/api/work-items/{wid}").json()
        assert body["mr_ref"] == {"number": 12, "url": "https://forge.example/mr/12"}

        # A retry that reuses the MR logs a fresh event -- the latest one wins,
        # not the first-open URL for a branch since force-pushed.
        _seed_events(
            client,
            wid,
            [{"number": 12, "url": "https://forge.example/mr/12?refresh"}],
            event_type="mr_opened",
        )
        body = client.get(f"/api/work-items/{wid}").json()
        assert body["mr_ref"]["url"] == "https://forge.example/mr/12?refresh"


def test_mr_ref_stays_silent_on_a_multi_repo_item(tmp_path, monkeypatch):
    """A multi-repo item's `open_mr` node emits one `mr_opened` per target
    repo with no repo identifier in the payload -- the latest one is as
    likely to be a submodule's as the root's, so this must not guess."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        _seed_repo(client, wid, repo_path="/wt", role="root", merge_rank=1)
        _seed_events(
            client,
            wid,
            [{"number": 3, "url": "https://forge.example/mr/3"}],
            event_type="mr_opened",
        )
        body = client.get(f"/api/work-items/{wid}").json()
        assert body["mr_ref"] is None


def test_stop_reason_reaches_the_detail_payload(tmp_path, monkeypatch):
    """Kraft-esc: the screen has to tell a loop escalation from a crash that
    happened to stop the item on a fix-loop node, and the reason is the only
    thing that distinguishes them."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        assert client.get(f"/api/work-items/{wid}").json()["stop_reason"] is None

        _seed_events(
            client,
            wid,
            [{"node_id": "verify", "reason": "executor crashed: RuntimeError('boom')"}],
            event_type="work_item_needs_human",
        )
        body = client.get(f"/api/work-items/{wid}").json()
        assert body["stop_reason"] == "executor crashed: RuntimeError('boom')"


def test_concerns_stop_at_the_gate_that_answered_them(tmp_path, monkeypatch):
    """Kraft-ub2: a concern belongs to the *next* gate. Once a gate is resolved,
    concerns raised before it must not be re-posed at every later gate — only
    what a session reported since then."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        _seed_events(
            client,
            wid,
            [{"session_id": "s1", "status": "done_with_concerns", "concerns": "old worry"}],
            event_type="worker_session_exited",
        )
        _seed_events(client, wid, [{"gate": "spec_approval"}], event_type="gate_approved")
        assert client.get(f"/api/work-items/{wid}").json()["concerns"] == []

        _seed_events(
            client,
            wid,
            [{"session_id": "s2", "status": "done_with_concerns", "concerns": "new worry"}],
            event_type="worker_session_exited",
        )
        assert client.get(f"/api/work-items/{wid}").json()["concerns"] == ["new worry"]


def test_needs_context_question_reaches_the_detail_payload(tmp_path, monkeypatch):
    """The agent's question, end to end: fake-claude writes it to the result
    file, the executor folds it into the `needs_context: <question>` reason
    on `work_item_needs_human`, and the detail endpoint reads it back from
    that reason — without ever reading `result_path` off disk itself."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which repo does this target?")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "needs a decision", "chain_template": "quick-task"},
        ).json()["id"]
        body = _wait_for_status(client, wid, "needs_human")
        assert body["needs_context_question"] == "which repo does this target?"


def test_needs_context_question_does_not_resurface_a_stale_answer(tmp_path, monkeypatch):
    """A second `needs_context` stop whose result file omitted `question`
    (the fallback kraft.executor.dispatch.needs_context_question uses is folded into the
    reason as `"(no question given)"`) must show that fallback, not an
    earlier stop's already-answered question — the bug an unbounded scan of
    `worker_session_exited.question` events would have produced."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        _seed_events(
            client,
            wid,
            [
                {"node_id": "implementation", "reason": "needs_context: which db?"},
                {"node_id": "implementation", "reason": "needs_context: (no question given)"},
            ],
            event_type="work_item_needs_human",
        )

        body = client.get(f"/api/work-items/{wid}").json()
        assert body["needs_context_question"] == "(no question given)"


def test_work_item_detail_reports_steerable_per_current_node(tmp_path, monkeypatch):
    """Kraft-bz9b: the detail screen drops its steer box on `steerable: false`
    rather than offer text `retry` would 409 on."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)

        _force_node(wid, "open_mr", "needs_human")
        assert client.get(f"/api/work-items/{wid}").json()["steerable"] is False

        _force_node(wid, "implementation", "needs_human")
        assert client.get(f"/api/work-items/{wid}").json()["steerable"] is True


def test_work_item_usage_rollup_is_captured_from_the_agent_envelope(tmp_path, monkeypatch):
    """The whole capture path in one go: the agent reports tokens on its final
    envelope, the adapter prices and stores them, and GET /work-items/{id}
    rolls them up per node and per item."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "make it pass", "chain_template": "quick-task"},
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed", timeout=120)

        usage = client.get(f"/api/work-items/{wid}").json()["usage"]
        impl = next(n for n in usage["by_node"] if n["node"] == "implementation")
        # 1000 input + 500 cache-read, 200 output
        assert (impl["tokens_in"], impl["tokens_out"]) == (1500, 200)
        # cost is the agent's own number, carried through untouched
        assert impl["cost_usd"] == pytest.approx(0.035)
        assert impl["cost_complete"] is True
        assert impl["wall_ms"] is not None and impl["rounds"] == 1

        # the subprocess and builtin nodes ran but report no tokens — that is not
        # a hole in the billing, and must not make the total read as a floor
        env = next(n for n in usage["by_node"] if n["node"] == "env_setup")
        assert env["tokens_in"] == 0
        assert env["cost_complete"] is True

        assert usage["total"]["tokens_in"] == impl["tokens_in"]
        assert usage["total"]["cost_usd"] == pytest.approx(0.035)
        assert usage["total"]["cost_complete"] is True
