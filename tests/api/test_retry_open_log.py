"""API-3: retry after a cap, open-in-editor, and the structured session log."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
from support.harness import (
    _git,
    fake_templates_dir,
    isolated_bd,
    make_repo,
    make_repo_with_engineering,
)

from kraft import logs

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch, *, templates_dir=None, peer=("127.0.0.1", 54321)):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv(
        "KRAFT_TEMPLATES_DIR",
        str(templates_dir or fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))),
    )
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    return TestClient(api.app, client=peer)


def _as_authenticated_lan_peer(client, monkeypatch):
    """Make this client remote *and* fully credentialed.

    A password so `_perimeter` (Task 3) has nothing to say, and the MCP bearer so
    `_authenticate` has nothing to say — then anything it is refused, it is
    refused for being on another machine, which is the only thing under test.
    """
    st = client.app.state
    monkeypatch.setattr(st, "access", {**st.access, "password_hash": "x"}, raising=False)
    client.headers["authorization"] = f"Bearer {st.mcp_token}"


def _spy_on_launches(monkeypatch):
    """Every editor is installed and every launch is recorded, never performed."""
    launched = []
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: launched.append(argv) or object())
    return launched


def _as_git_scan_doc(client, monkeypatch, doc_id):
    """Force `origin='git_scan'` on an indexed document for a test that is
    about editor-launch mechanics, not about the index architecture.

    Every document `_completed_item` produces is a session summary --
    `origin='event_ingest'`, same as every gate artifact now
    (`forge._work_product_pathspec` keeps `.engineering/` out of git
    entirely) -- so `open_document` refuses it before ever reaching the
    launch code these tests exist to cover. Reusing the escape-guard test's
    own technique: monkeypatch `get_document` to hand back the real row with
    just `origin` overridden, the same as forcing any other row shape a live
    scan could produce.
    """
    indexer = client.app.state.indexer
    original = indexer.get_document
    doc = client.get(f"/api/documents/{doc_id}").json()
    forced = {**doc, "origin": "git_scan"}
    # Only this one id is forced -- an unrelated lookup (a 404 case, say) must
    # still reach the real indexer instead of getting this document back too.
    monkeypatch.setattr(
        indexer, "get_document", lambda _id: forced if _id == doc_id else original(_id)
    )
    return forced


# ── log classification ───────────────────────────────────────────────────────


def test_classify_separates_agent_tool_and_plain_output():
    assert logs.classify("running pytest") == ("stdout", None)
    assert logs.classify('{"type": "tool_use", "name": "Bash"}') == ("tool", None)
    # a JSON line that carries its own time keeps it
    assert logs.classify('{"type": "text", "timestamp": "12:00:01"}') == ("agent", "12:00:01")
    # not-quite-JSON is output, not a parse error
    assert logs.classify("{oops") == ("stdout", None)
    # the stream's own bookkeeping lines are the session's, not the agent's
    assert logs.classify('{"type": "result", "is_error": false}') == ("sys", None)
    assert logs.classify('{"type": "system", "subtype": "init"}') == ("sys", None)


def test_classify_reads_tool_use_out_of_a_stream_json_message():
    """stream-json nests tool use inside `message.content[]`, so a top-level
    check filed every line under `agent` and the modal's `tool` chip selected
    nothing."""
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "..."},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "src/kraft/api.py"},
                    },
                ]
            },
        }
    )
    assert logs.classify(line)[0] == "tool"
    user = json.dumps(
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}
    )
    assert logs.classify(user)[0] == "tool"


def test_summary_of_a_thinking_block_skips_the_raw_signature_blob():
    """A thinking block's `signature` is a base64 blob no reader wants dumped
    raw -- and under extended thinking + prompt caching `thinking` itself is
    routinely empty, so both cases need a summary, not a fallthrough."""
    empty = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "", "signature": "Ep0F..."}]},
        }
    )
    assert logs.classify(empty)[0] == "agent"
    assert logs.summary(json.loads(empty), empty) == "thinking"

    worded = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "thinking", "thinking": "Checking the test first.\nThen fix."}]
            },
        }
    )
    assert logs.summary(json.loads(worded), worded) == "thinking: Checking the test first."


def test_jsonl_summarises_each_line_and_truncates_a_very_long_one(tmp_path):
    """The modal renders every line; a tool_result for a large file read is
    tens of KB of JSON. The summary is what a reader scans, `text` is the raw
    line capped, and the plain-text endpoint keeps the whole thing."""
    huge = "x" * 5000
    p = tmp_path / "s.log"
    p.write_text(
        "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-5"}),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Read",
                                    "input": {"file_path": "src/kraft/api.py"},
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [{"type": "text", "text": "Reading the API.\nThen:"}]
                        },
                    }
                ),
                json.dumps(
                    {"type": "user", "message": {"content": [{"type": "text", "text": huge}]}}
                ),
                json.dumps({"type": "result", "is_error": False}),
                "plain stdout",
            ]
        )
        + "\n"
    )
    rows = list(logs.jsonl(p))
    assert [r["summary"] for r in rows][:3] == [
        "init claude-opus-5",
        "Read(src/kraft/api.py)",
        "Reading the API.",
    ]
    assert rows[4]["summary"] == "result: success"
    assert rows[5]["summary"] == "plain stdout"
    assert len(rows[3]["text"]) == 2001 and rows[3]["text"].endswith("…")
    # the plain-text endpoint serves the file itself, which is untouched
    assert huge in p.read_text()


def test_jsonl_numbers_lines_and_can_resume_mid_file(tmp_path):
    p = tmp_path / "s.log"
    p.write_text("one\ntwo\nthree\n")
    assert [line["n"] for line in logs.jsonl(p)] == [0, 1, 2]
    assert [line["text"] for line in logs.jsonl(p, start_line=2)] == ["three"]
    assert list(logs.jsonl(tmp_path / "gone.log")) == []


def test_split_lines_is_the_one_numbering_rule(tmp_path):
    """The writer counts bytes and the reader counts lines; they have to agree.

    `str.splitlines()` splits on \\r, \\v, \\x1c and U+2028 as well as \\n. An
    incremental reader watching a byte stream cannot, so a raw \\r inside a
    line used to invent a line the sidecar had no timestamp for.
    """
    assert logs.split_lines("a\nb\n") == ["a", "b"]
    assert logs.split_lines("a\nb") == ["a", "b"]  # a partial last line still counts
    assert logs.split_lines("") == []
    assert logs.split_lines("\n") == [""]
    assert logs.split_lines("a\rb\n") == ["a\rb"]
    assert logs.split_lines("a b\n") == ["a b"]

    p = tmp_path / "s.log"
    p.write_text("a\rb\nc\n")
    assert [row["n"] for row in logs.jsonl(p)] == [0, 1]


# ── endpoints ────────────────────────────────────────────────────────────────


def _completed_item(client, repo):
    import time

    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "make it pass", "chain_template": "quick-task"},
    ).json()["id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        evs = client.get(f"/api/work-items/{wid}/events").json()
        if any(e["type"] == "work_item_completed" for e in evs):
            return wid
        time.sleep(0.2)
    raise AssertionError("work item never completed")


def test_work_item_documents_carry_the_run_their_session_came_from(tmp_path, monkeypatch):
    """W13 A: a session summary row names its session's attempt, round and
    status, joined from worker_sessions on worker_session_id."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        sessions = {
            s["id"]: s for s in client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
        }
        docs = client.get(f"/api/work-items/{wid}/documents").json()["documents"]
        summaries = [d for d in docs if d["worker_session_id"]]
        assert summaries, "the fake agent writes a session summary"
        for d in summaries:
            s = sessions[d["worker_session_id"]]
            assert (d["attempt"], d["round"], d["session_status"]) == (
                s["attempt"],
                s["round"],
                s["status"],
            )
            assert isinstance(d["attempt"], int) and isinstance(d["round"], int)


def test_log_jsonl_and_plain_text_are_both_served(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        sid = client.get(f"/api/work-items/{wid}").json()["worker_sessions"][0]["id"]

        body = client.get(f"/api/worker-sessions/{sid}/log?format=jsonl").json()
        assert body["session_id"] == sid
        assert {line["src"] for line in body["lines"]} <= set(logs.SOURCES)
        assert all("t" in line and "text" in line for line in body["lines"])

        plain = client.get(f"/api/worker-sessions/{sid}/log")
        assert plain.headers["content-type"].startswith("text/plain")

        assert client.get("/api/worker-sessions/nope/log?format=jsonl").status_code == 404


def test_log_follow_streams_the_lines_then_ends(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        sid = client.get(f"/api/work-items/{wid}").json()["worker_sessions"][0]["id"]
        # the session is already finished, so the tail drains and closes
        with client.stream("GET", f"/api/worker-sessions/{sid}/log?format=jsonl&follow=1") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            body = "".join(r.iter_text())
        assert body.rstrip().endswith("event: end\ndata: {}")
        payloads = [
            json.loads(ln[len("data: ") :])
            for ln in body.splitlines()
            if ln.startswith("data: ") and ln != "data: {}"
        ]
        assert payloads and payloads[0]["n"] == 0


def test_log_follow_keeps_streaming_a_pending_session(tmp_path, monkeypatch):
    """A session that has not started yet is followed, not closed on the first poll.

    `_tail` only ever stopped on a status that was not exactly 'running', so a
    log opened a beat early got one poll's worth of output and an `event: end`.
    Driven from outside the app: a helper thread appends a line to the log a
    second in (well past the 0.4s poll a pending session used to survive) and
    only then marks the session done, so the stream terminates on its own.
    """
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        sid = client.get(f"/api/work-items/{wid}").json()["worker_sessions"][0]["id"]

        db = tmp_path / "run" / "orchestrator.db"
        conn = sqlite3.connect(db)
        log_path = Path(
            conn.execute("SELECT log_path FROM worker_sessions WHERE id = ?", (sid,)).fetchone()[0]
        )
        conn.execute("UPDATE worker_sessions SET status = 'pending' WHERE id = ?", (sid,))
        conn.commit()

        def finish():
            time.sleep(1.0)
            with log_path.open("a") as fh:
                fh.write("late line from a pending session\n")
            side = sqlite3.connect(db)
            side.execute("UPDATE worker_sessions SET status = 'done' WHERE id = ?", (sid,))
            side.commit()
            side.close()

        worker = threading.Thread(target=finish)
        worker.start()
        try:
            with client.stream("GET", f"/api/worker-sessions/{sid}/log?format=jsonl&follow=1") as r:
                body = "".join(r.iter_text())
        finally:
            worker.join()
            conn.close()

        # the line written a second in only arrives if the tail was still
        # following a session in 'pending'
        assert "late line from a pending session" in body
        assert body.rstrip().endswith("event: end\ndata: {}")


def test_open_document_reports_501_when_no_editor_is_installed(tmp_path, monkeypatch):
    """The SPA needs a 501, not a 500, so it can fall back to the URL scheme."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        docs = client.get(f"/api/work-items/{wid}/documents").json()["documents"]
        assert docs, "the fake agent writes a session summary"
        doc_id = docs[0]["document_id"]
        _as_git_scan_doc(client, monkeypatch, doc_id)

        monkeypatch.setattr("shutil.which", lambda _: None)
        r = client.post(f"/api/documents/{doc_id}/open", json={"editor": "zed"})
        assert r.status_code == 501

        assert client.post("/api/documents/nope/open", json={}).status_code == 404
        assert (
            client.post(f"/api/documents/{doc_id}/open", json={"editor": "vi"}).status_code == 400
        )


def test_open_document_launches_the_named_editor(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        doc_id = client.get(f"/api/work-items/{wid}/documents").json()["documents"][0][
            "document_id"
        ]
        doc = _as_git_scan_doc(client, monkeypatch, doc_id)

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/api/documents/{doc_id}/open", json={"editor": "code"})
        assert r.status_code == 200
        assert r.json()["editor"] == "code"
        assert launched == [["/usr/bin/code", str(Path(doc["repo"]) / doc["path"])]]


def test_open_document_on_an_attachment_resolves_the_worktree_not_the_repo(tmp_path, monkeypatch):
    """Kraft-2jy6: a synthetic `attachment:{id}:{kind}` doc's file lives on
    the item's own branch, not the connected repo's checkout — `open_document`
    must resolve it the same worktree-first way `GET /documents/{id}` already
    does, not `doc['repo'] / doc['path']`."""
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# Repo copy\nold\n"})
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={
                "repo": str(repo),
                "title": "carry the plan over",
                "chain_template": "quick-task",
                "autostart": False,
                "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
            },
        ).json()["id"]

        # The worktree copy is what a running item would have on its own
        # branch -- newer than what's on the registered repo's checkout, so a
        # resolution that fell back to `doc['repo'] / doc['path']` would open
        # the stale file instead.
        st = client.app.state
        wt = st.run_dirs.worktrees / wid / ".engineering" / "plans"
        wt.mkdir(parents=True)
        (wt / "p.md").write_text("# Worktree copy\nnew\n")

        doc_id = f"attachment:{wid}:plan"
        assert client.get(f"/api/documents/{doc_id}").json()["content"] == "# Worktree copy\nnew\n"

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/api/documents/{doc_id}/open", json={"editor": "code"})
        assert r.status_code == 200
        assert launched == [["/usr/bin/code", str((wt / "p.md").resolve())]]


def test_open_document_refuses_a_document_with_no_file_in_the_repo(tmp_path, monkeypatch):
    """A session summary or gate artifact never lands in the connected repo's
    checkout (`forge._work_product_pathspec` keeps `.engineering/` out of git
    entirely) -- `origin='event_ingest'` says so, and this must be a 409 that
    tells the SPA not to bother falling back to a `vscode://` URL either,
    rather than launching an editor (or handing the SPA a path) that points
    at a file which was never written."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        doc = client.get(f"/api/work-items/{wid}/documents").json()["documents"][0]
        assert client.get(f"/api/documents/{doc['document_id']}").json()["origin"] == (
            "event_ingest"
        )

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/api/documents/{doc['document_id']}/open", json={"editor": "code"})
        assert r.status_code == 409
        assert launched == []


def test_retry_is_refused_on_an_item_that_is_not_stopped(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        r = client.post(f"/api/work-items/{wid}/retry", json={"steer": "try harder"})
        assert r.status_code == 409
        assert "not stopped" in r.json()["detail"]
        assert client.post("/api/work-items/nope/retry", json={}).status_code == 404


def test_retry_restarts_a_stopped_node_that_has_no_fix_loop(tmp_path, monkeypatch):
    """Retry is the only door back onto an item stopped by a task failure.

    quick-task's nodes carry no fix_loop, so refusing them here left the item
    with no route at all — resume wants paused, pause wants running, and
    approve/reject want a pending gate (Kraft-bzwi).
    """
    import time

    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        # KRAFT_FAIL steers the fake agent into failing its node
        wid = client.post(
            "/api/work-items",
            json={
                "repo": str(repo),
                "title": "KRAFT_FAIL once",
                "chain_template": "quick-task",
                # Kraft-lpdd: this test is about retry, not the unrelated
                # auto-escalate trigger racing it onto the same needs_human
                # stop the poll loop below is waiting on.
                "node_overrides": {"implementation": {"auto_escalate_stuck": False}},
            },
        ).json()["id"]

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            item = client.get(f"/api/work-items/{wid}").json()
            if item["status"] == "needs_human":
                break
            time.sleep(0.2)
        assert item["status"] == "needs_human"
        node_id = item["current_node_id"]
        chain = item["chain_definition"]
        assert not next(n for n in chain["nodes"] if n["id"] == node_id).get("fix_loop")

        r = client.post(f"/api/work-items/{wid}/retry", json={"steer": "the tests pass now"})
        assert r.status_code == 200, r.text
        assert r.json()["node_id"] == node_id
        assert r.json()["loop"] is None

        evts = client.get(f"/api/work-items/{wid}/events").json()
        retried = [e for e in evts if e["type"] == "work_item_retried"]
        assert retried and retried[-1]["payload"]["steer"] == "the tests pass now"


def test_log_lines_carry_the_time_the_parent_saw_them(tmp_path, monkeypatch):
    """The child writes to a pipe the parent drains, so every line gets a stamp
    without the plain-text log — which copy and the envelope parser read — gaining
    a prefix."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        sid = client.get(f"/api/work-items/{wid}").json()["worker_sessions"][0]["id"]

        body = client.get(f"/api/worker-sessions/{sid}/log?format=jsonl").json()
        assert body["lines"], "the fake agent writes at least its envelope"
        assert all(line["t"] for line in body["lines"])

        plain = client.get(f"/api/worker-sessions/{sid}/log").text
        # verbatim: no timestamp leaked into the copyable log
        assert plain.splitlines() == [line["text"] for line in body["lines"]]


def test_a_log_with_no_sidecar_reads_back_with_blank_times(tmp_path):
    """An adopted session, or a log written before the sidecar existed."""
    log = tmp_path / "s.log"
    log.write_text("one\ntwo\n")
    assert [line["t"] for line in logs.jsonl(log)] == [None, None]


def test_open_worktree_launches_the_editor_on_the_checkout(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        worktree = client.get(f"/api/work-items/{wid}").json()["worktree_path"]
        assert Path(worktree).is_dir()

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/api/work-items/{wid}/open-worktree", json={"editor": "zed"})
        assert r.status_code == 200 and r.json()["path"] == worktree
        assert launched == [["/usr/bin/zed", worktree]]

        assert client.post("/api/work-items/nope/open-worktree", json={}).status_code == 404


def test_bead_search_is_live_and_quiet_on_an_empty_query(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/beads/search?q=").json() == {"query": "", "beads": []}
        body = client.get("/api/beads/search?q=zzz-no-such-bead").json()
        assert body["beads"] == []


def test_open_document_is_refused_for_a_non_loopback_client(tmp_path, monkeypatch):
    """Opening an editor starts a process and puts a window on the *server's*
    desktop. One shared password says nothing about who is asking for that."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch, peer=("10.0.0.5", 54321)) as client:
        _as_authenticated_lan_peer(client, monkeypatch)
        wid = _completed_item(client, repo)
        doc = client.get(f"/api/work-items/{wid}/documents").json()["documents"][0]

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/api/documents/{doc['document_id']}/open", json={"editor": "code"})
        assert r.status_code == 403, r.text
        assert "own machine" in r.json()["detail"]
        assert launched == [], "no process may start for a remote caller"


def test_open_worktree_is_refused_for_a_non_loopback_client(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch, peer=("10.0.0.5", 54321)) as client:
        _as_authenticated_lan_peer(client, monkeypatch)
        wid = _completed_item(client, repo)
        assert Path(client.get(f"/api/work-items/{wid}").json()["worktree_path"]).is_dir()

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/api/work-items/{wid}/open-worktree", json={"editor": "zed"})
        assert r.status_code == 403, r.text
        assert launched == []


def test_open_document_refuses_a_document_path_outside_its_repo(tmp_path, monkeypatch):
    """The path is assembled from an index row rather than from the caller — and
    the index row is the only thing standing between `../../..` and an editor
    opened on /etc/passwd. Resolve it and require the repo above it."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        doc_id = client.get(f"/api/work-items/{wid}/documents").json()["documents"][0][
            "document_id"
        ]
        escaped = {
            **client.get(f"/api/documents/{doc_id}").json(),
            "path": "../../../etc/passwd",
            # This guard is unrelated to `origin`; pin it to 'git_scan' so a
            # real row's actual 'event_ingest' doesn't trip the *other* guard
            # first and mask what this test is checking.
            "origin": "git_scan",
        }
        monkeypatch.setattr(client.app.state.indexer, "get_document", lambda _id: escaped)

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/api/documents/{doc_id}/open", json={"editor": "code"})
        assert r.status_code == 400, r.text
        assert "escapes its repo" in r.json()["detail"]
        assert launched == []


def test_retry_rebases_the_worktree_onto_a_moved_head(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        # KRAFT_FAIL steers the fake agent into failing its node, same trick
        # test_retry_restarts_a_stopped_node_that_has_no_fix_loop uses above
        wid = client.post(
            "/api/work-items",
            json={
                "repo": str(repo),
                "title": "KRAFT_FAIL once",
                "chain_template": "quick-task",
                # Kraft-lpdd: this test is about retry/rebase, not the
                # unrelated auto-escalate trigger racing it onto the same
                # needs_human stop the poll loop below is waiting on.
                "node_overrides": {"implementation": {"auto_escalate_stuck": False}},
            },
        ).json()["id"]

        deadline = time.monotonic() + 120
        item = None
        while time.monotonic() < deadline:
            item = client.get(f"/api/work-items/{wid}").json()
            if item["status"] == "needs_human":
                break
            time.sleep(0.2)
        assert item is not None and item["status"] == "needs_human"

        # repo's default branch moves on while the item sits stopped
        (repo / "moved.txt").write_text("moved on\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "moved on")
        new_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        r = client.post(f"/api/work-items/{wid}/retry", json={"steer": "the tests pass now"})
        assert r.status_code == 200, r.text

        after = client.get(f"/api/work-items/{wid}").json()
        assert after["base_ref"] == new_head
        worktree = Path(after["worktree_path"])
        assert (worktree / "moved.txt").is_file()
