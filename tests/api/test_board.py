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


def _seed_session(client, wid, *, session_id, hook_point):
    """Write a `worker_sessions` row directly — same reasoning as `_seed_repo`:
    a judge session normally comes from `dispatch.launch_hook`, which a
    detail-payload test needs no more than it needs `ensure_worktree`."""
    db_path = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    conn = sqlite3.connect(db_path)
    try:
        store.create_session(
            conn,
            id=session_id,
            work_item_id=wid,
            node_id="verify",
            hook_point=hook_point,
            log_path=f"/tmp/{session_id}.log",
            result_path=f"/tmp/{session_id}.json",
        )
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


def _seed_escalation_session(client, wid, *, session_id, thread, status="done"):
    """Write an escalation `worker_sessions` row directly with an explicit
    `thread` and a terminal status -- same reasoning as `_seed_session`, but
    this test needs several rows spread across threads, not the one
    `store.create_session` default `_seed_session` gives."""
    db_path = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        store.create_session(
            conn,
            id=session_id,
            work_item_id=wid,
            node_id="implementation",
            hook_point="escalation",
            log_path=f"/tmp/{session_id}.log",
            result_path=f"/tmp/{session_id}.json",
            thread=thread,
        )
        store.session_exited(conn, session_id, status)
        conn.commit()
    finally:
        conn.close()


def test_get_work_item_includes_escalation_threads(tmp_path, monkeypatch):
    """Kraft-dkb6g: `escalation_threads` projects one entry per thread,
    oldest first, with the shape the UI reads (`thread`, `session_id`,
    `turns`, `started_at`, `ended_at`, `status`)."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        _seed_escalation_session(client, wid, session_id="e1", thread=1, status="done")
        _seed_escalation_session(client, wid, session_id="e2", thread=1, status="done")
        _seed_escalation_session(client, wid, session_id="e3", thread=2, status="needs_context")

        body = client.get(f"/api/work-items/{wid}").json()
        threads = body["escalation_threads"]
        assert [t["thread"] for t in threads] == [1, 2]
        assert [t["turns"] for t in threads] == [2, 1]
        assert threads[0]["session_id"] == "e2"
        assert threads[1]["session_id"] == "e3"
        assert threads[1]["status"] == "needs_context"
        assert threads[0]["started_at"] and threads[0]["ended_at"]


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


def test_concerns_excludes_the_judges_own_reasoning(tmp_path, monkeypatch):
    """JUDGE_PROMPT/SKILL.md require the judge to write `concerns` on every
    verdict, including a plain `continue`. Without the same `worker_sessions`
    join `walk._diagnosis_bundle` uses to skip the judge's own sessions, that
    routine reasoning would show up here as a concern the human owes an
    answer for."""
    from kraft.executor import dispatch

    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        _seed_session(client, wid, session_id="measure-1", hook_point="on.check")
        _seed_session(client, wid, session_id="judge-1", hook_point=dispatch.JUDGE_HOOK)
        _seed_events(
            client,
            wid,
            [
                {
                    "session_id": "measure-1",
                    "status": "done_with_concerns",
                    "concerns": "flaky under load",
                }
            ],
            event_type="worker_session_exited",
        )
        _seed_events(
            client,
            wid,
            [{"session_id": "judge-1", "status": "continue", "concerns": "judge's own reasoning"}],
            event_type="worker_session_exited",
        )

        body = client.get(f"/api/work-items/{wid}").json()
        assert body["concerns"] == ["flaky under load"]


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
    rather than offer text `retry` would 409 on. `merge`, not `open_mr`
    (Kraft-cbr): `mr_checks` right after `open_mr` now carries `fix_loop`,
    so a steer given there could reach it; `merge` is the chain's last node
    and forge-kind with no fix_loop, so nothing downstream can ever steer."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)

        _force_node(wid, "merge", "needs_human")
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

        # the non-agent node ran but reports no tokens — that is not a hole in
        # the billing, and must not make the total read as a floor. (V1
        # quick-task has no `env_setup` node; `verify` is the non-agent one.)
        verify = next(n for n in usage["by_node"] if n["node"] == "verify")
        assert verify["tokens_in"] == 0
        assert verify["cost_complete"] is True

        assert usage["total"]["tokens_in"] == impl["tokens_in"]
        assert usage["total"]["cost_usd"] == pytest.approx(0.035)
        assert usage["total"]["cost_complete"] is True


def test_judge_stop_note_reaches_the_detail_payload(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        finding = {
            "severity": "important",
            "message": "still broken",
            "file": "a.py",
            "line": 4,
            "source_plugin": "on.check",
        }
        continued = {
            "node_id": "verify",
            "cycle": 1,
            "verdict": "continue",
            "reasoning": "shrinking, worth another cycle",
            "findings": [finding],
        }
        downgraded = {
            "node_id": "verify",
            "cycle": 2,
            "verdict": "stop_downgrade",
            "reasoning": "real but not worth chasing further",
            "findings": [finding],
        }
        _seed_events(client, wid, [continued, downgraded], event_type="judge_verdict")

        body = client.get(f"/api/work-items/{wid}").json()
        assert len(body["judge_stop_note"]) == 1  # only the stop_downgrade verdict
        note = body["judge_stop_note"][0]
        assert note["node_id"] == "verify"
        assert note["reasoning"] == "real but not worth chasing further"
        assert [f["message"] for f in note["findings"]] == ["still broken"]


def test_judge_stop_note_stops_at_the_last_resolved_gate(tmp_path, monkeypatch):
    """A note the human already saw at the gate they approved must not be
    re-posed at every later gate — the same Kraft-ub2 boundary `_concerns`
    keeps."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        finding = {
            "severity": "important",
            "message": "still broken",
            "file": "a.py",
            "line": 4,
            "source_plugin": "on.check",
        }
        answered = {
            "node_id": "verify",
            "cycle": 2,
            "verdict": "stop_downgrade",
            "reasoning": "the human already approved this one",
            "findings": [finding],
        }
        _seed_events(client, wid, [answered], event_type="judge_verdict")
        _seed_events(client, wid, [{"gate": "human_review_approval"}], event_type="gate_approved")

        assert client.get(f"/api/work-items/{wid}").json()["judge_stop_note"] == []

        later = dict(answered, node_id="mr_checks", reasoning="new, after the gate")
        _seed_events(client, wid, [later], event_type="judge_verdict")
        notes = client.get(f"/api/work-items/{wid}").json()["judge_stop_note"]
        assert [n["reasoning"] for n in notes] == ["new, after the gate"]


def test_steerable_is_answered_off_the_v1_snapshot(tmp_path, monkeypatch):
    """Kraft-bz9b on a V1 row. `chain_definition` is `"{}"` now, so a reader
    that still looked there would either 500 on a missing `nodes` key or fail
    open for every item -- offering a steer box on a node whose remaining chain
    runs no agent at all, and then 409ing the text.

    Both ends of the answer are pinned: a node with an agent task after it is
    steerable, one whose tail is forge-only is not.
    """
    import asyncio

    from support.harness import v1_chain, v1_item, v1_walk  # noqa: F401

    from kraft import db as _db
    from kraft.api.routes import lifecycle
    from kraft.paths import RunDirs

    repo = make_repo(tmp_path)
    chain = v1_chain(
        [
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [{"id": "build", "kind": "subprocess", "command": "true"}],
            },
            {
                "id": "review",
                "kind": "exec",
                "tasks": [{"id": "look", "kind": "agent", "harness": "fake", "prompt": "review"}],
            },
            {
                "id": "merge",
                "kind": "exec",
                "tasks": [{"id": "go", "kind": "forge", "target": "mr.merge"}],
            },
        ],
        repo=repo,
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await _db.Database.open(rd.db)
        try:
            await v1_item(database, chain, repo=repo, wid="w1")
            answers = {}
            for node_id in ("implementation", "merge"):
                await database.write(
                    lambda c, n=node_id: c.execute(
                        "UPDATE work_items SET current_node_id = ? WHERE id = 'w1'", (n,)
                    )
                )
                row = database.read(
                    lambda c: c.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone()
                )
                answers[node_id] = lifecycle.steer_reachable(row, None)
            return answers
        finally:
            await database.close()

    assert asyncio.run(scenario()) == {"implementation": True, "merge": False}


def test_a_v1_item_lists_and_renders_its_chain_nodes(tmp_path, monkeypatch):
    """H1's other half. `chain_definition` is `"{}"` on a V1 row, and the board
    draws its stage bar and names the current node from
    `chain_definition.nodes` -- so the raw column made every board row blank
    and `PeekPane`/`Board`'s unguarded `.nodes` a crash. Both the list and the
    detail route project the frozen snapshot into the same envelope instead, so
    the board is *correct* for a V1 item and not merely non-crashing.

    A V1 gate node reports **itself** under `gate_after`, and carries
    `kind: "gate"` beside it. Eleven SPA consumers ask one of three questions of
    `gate_after` -- is this a gate, what is it called, which node owns gate X --
    and for a gate node the answer to all three is its own id: a V1 gate is
    where its own document lives. Leaving the field null answered all three with
    "this chain has no gates", which is why `ItemCard.gateNodeId` returned null
    for every V1 item and the gate's document door disappeared.
    """
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        created = client.post(
            "/api/work-items",
            json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
        ).json()
        wid = created["id"]

        listed = next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)
        detail = client.get(f"/api/work-items/{wid}").json()

        for payload in (listed, detail):
            nodes = payload["chain_definition"]["nodes"]
            assert [n["id"] for n in nodes[:2]] == ["spec", "spec_approval"]
            assert nodes[0]["kind"] == "exec" and nodes[1]["kind"] == "gate"
            # A gate declares no execution shape; an exec node's tasks are
            # canonical paths, which is what V1 has instead of hook names.
            assert nodes[0]["tasks"] == ["spec.main.author"] and nodes[1]["tasks"] == []
            assert nodes[0]["gate_after"] is None, "an exec node is not a gate"
            assert nodes[1]["gate_after"] == "spec_approval"
            assert nodes[1]["reject_to"] == "spec"
            # `gateNodeId`'s question -- "which node owns gate `spec_approval`"
            # -- resolves, which is the door to the gate's document.
            assert [n["id"] for n in nodes if n["gate_after"] == "spec_approval"] == [
                "spec_approval"
            ]
            # The fields the Config tab renders, where a blank reads as
            # "not configured": the fix-loop *cap key*, and whether the gate
            # declares a reviewing task.
            impl = next(n for n in nodes if n["id"] == "implementation")
            assert impl["fix_loop"] == "implementation.fix_loop"
            assert impl["steps"] == [
                ["implementation.implementation.implement"],
                ["implementation.verification.test_changed_scopes"],
            ]
            assert nodes[1]["auto_escalate"] is False
            assert payload["chain_definition"]["template_id"] == "default"

        # And the third door: `POST /work-items`' own 201 body, which only
        # carries a chain on the autostart branch. It shipped the raw column, so
        # `kraft item create --json` printed a chain with no nodes.
        started = client.post(
            "/api/work-items",
            json={"title": "t2", "repo": str(repo), "chain_template": "default"},
        ).json()
        assert [n["id"] for n in started["chain_definition"]["nodes"][:2]] == [
            "spec",
            "spec_approval",
        ]
        assert started["current_node_id"] == "spec"
