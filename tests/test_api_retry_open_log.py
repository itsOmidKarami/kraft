"""API-3: retry after a cap, open-in-editor, and the structured session log."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft import logs

_REPO_ROOT = Path(__file__).resolve().parents[1]
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
    monkeypatch.setattr("kraft.api.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "kraft.api.subprocess.Popen", lambda argv, **kw: launched.append(argv) or object()
    )
    return launched


# ── log classification ───────────────────────────────────────────────────────


def test_classify_separates_agent_tool_and_plain_output():
    assert logs.classify("running pytest") == ("stdout", None)
    assert logs.classify('{"type": "result", "is_error": false}') == ("agent", None)
    assert logs.classify('{"type": "tool_use", "name": "Bash"}') == ("tool", None)
    # a JSON line that carries its own time keeps it
    assert logs.classify('{"type": "text", "timestamp": "12:00:01"}') == ("agent", "12:00:01")
    # not-quite-JSON is output, not a parse error
    assert logs.classify("{oops") == ("stdout", None)


def test_jsonl_numbers_lines_and_can_resume_mid_file(tmp_path):
    p = tmp_path / "s.log"
    p.write_text("one\ntwo\nthree\n")
    assert [line["n"] for line in logs.jsonl(p)] == [0, 1, 2]
    assert [line["text"] for line in logs.jsonl(p, start_line=2)] == ["three"]
    assert list(logs.jsonl(tmp_path / "gone.log")) == []


# ── endpoints ────────────────────────────────────────────────────────────────


def _completed_item(client, repo):
    import time

    wid = client.post(
        "/work-items",
        json={"repo": str(repo), "title": "make it pass", "chain_template": "quick-task"},
    ).json()["id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        evs = client.get(f"/work-items/{wid}/events").json()
        if any(e["type"] == "work_item_completed" for e in evs):
            return wid
        time.sleep(0.2)
    raise AssertionError("work item never completed")


def test_log_jsonl_and_plain_text_are_both_served(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        sid = client.get(f"/work-items/{wid}").json()["worker_sessions"][0]["id"]

        body = client.get(f"/worker-sessions/{sid}/log?format=jsonl").json()
        assert body["session_id"] == sid
        assert {line["src"] for line in body["lines"]} <= set(logs.SOURCES)
        assert all("t" in line and "text" in line for line in body["lines"])

        plain = client.get(f"/worker-sessions/{sid}/log")
        assert plain.headers["content-type"].startswith("text/plain")

        assert client.get("/worker-sessions/nope/log?format=jsonl").status_code == 404


def test_log_follow_streams_the_lines_then_ends(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        sid = client.get(f"/work-items/{wid}").json()["worker_sessions"][0]["id"]
        # the session is already finished, so the tail drains and closes
        with client.stream("GET", f"/worker-sessions/{sid}/log?format=jsonl&follow=1") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            body = "".join(r.iter_text())
        assert body.rstrip().endswith("event: end\ndata: {}")
        payloads = [
            json.loads(ln[len("data: ") :])
            for ln in body.splitlines()
            if ln.startswith("data: ") and ln != "data: {}"
        ]
        assert payloads and payloads[0]["n"] == 0


def test_open_document_reports_501_when_no_editor_is_installed(tmp_path, monkeypatch):
    """The SPA needs a 501, not a 500, so it can fall back to the URL scheme."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        docs = client.get(f"/work-items/{wid}/documents").json()["documents"]
        assert docs, "the fake agent writes a session summary"
        doc_id = docs[0]["document_id"]

        monkeypatch.setattr("kraft.api.shutil.which", lambda _: None)
        r = client.post(f"/documents/{doc_id}/open", json={"editor": "zed"})
        assert r.status_code == 501

        assert client.post("/documents/nope/open", json={}).status_code == 404
        assert client.post(f"/documents/{doc_id}/open", json={"editor": "vi"}).status_code == 400


def test_open_document_launches_the_named_editor(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        doc = client.get(f"/work-items/{wid}/documents").json()["documents"][0]

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/documents/{doc['document_id']}/open", json={"editor": "code"})
        assert r.status_code == 200
        assert r.json()["editor"] == "code"
        assert launched == [["/usr/bin/code", str(Path(doc["repo"]) / doc["path"])]]


def test_retry_is_refused_on_a_node_with_no_fix_loop(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        r = client.post(f"/work-items/{wid}/retry", json={"steer": "try harder"})
        assert r.status_code == 409
        assert client.post("/work-items/nope/retry", json={}).status_code == 404


def test_log_lines_carry_the_time_the_parent_saw_them(tmp_path, monkeypatch):
    """The child writes to a pipe the parent drains, so every line gets a stamp
    without the plain-text log — which copy and the envelope parser read — gaining
    a prefix."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        sid = client.get(f"/work-items/{wid}").json()["worker_sessions"][0]["id"]

        body = client.get(f"/worker-sessions/{sid}/log?format=jsonl").json()
        assert body["lines"], "the fake agent writes at least its envelope"
        assert all(line["t"] for line in body["lines"])

        plain = client.get(f"/worker-sessions/{sid}/log").text
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
        worktree = client.get(f"/work-items/{wid}").json()["worktree_path"]
        assert Path(worktree).is_dir()

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/work-items/{wid}/open-worktree", json={"editor": "zed"})
        assert r.status_code == 200 and r.json()["path"] == worktree
        assert launched == [["/usr/bin/zed", worktree]]

        assert client.post("/work-items/nope/open-worktree", json={}).status_code == 404


def test_bead_search_is_live_and_quiet_on_an_empty_query(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/beads/search?q=").json() == {"query": "", "beads": []}
        body = client.get("/beads/search?q=zzz-no-such-bead").json()
        assert body["beads"] == []


def test_open_document_is_refused_for_a_non_loopback_client(tmp_path, monkeypatch):
    """Opening an editor starts a process and puts a window on the *server's*
    desktop. One shared password says nothing about who is asking for that."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch, peer=("10.0.0.5", 54321)) as client:
        _as_authenticated_lan_peer(client, monkeypatch)
        wid = _completed_item(client, repo)
        doc = client.get(f"/work-items/{wid}/documents").json()["documents"][0]

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/documents/{doc['document_id']}/open", json={"editor": "code"})
        assert r.status_code == 403, r.text
        assert "own machine" in r.json()["detail"]
        assert launched == [], "no process may start for a remote caller"


def test_open_worktree_is_refused_for_a_non_loopback_client(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch, peer=("10.0.0.5", 54321)) as client:
        _as_authenticated_lan_peer(client, monkeypatch)
        wid = _completed_item(client, repo)
        assert Path(client.get(f"/work-items/{wid}").json()["worktree_path"]).is_dir()

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/work-items/{wid}/open-worktree", json={"editor": "zed"})
        assert r.status_code == 403, r.text
        assert launched == []


def test_open_document_refuses_a_document_path_outside_its_repo(tmp_path, monkeypatch):
    """The path is assembled from an index row rather than from the caller — and
    the index row is the only thing standing between `../../..` and an editor
    opened on /etc/passwd. Resolve it and require the repo above it."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _completed_item(client, repo)
        doc_id = client.get(f"/work-items/{wid}/documents").json()["documents"][0]["document_id"]
        escaped = {**client.get(f"/documents/{doc_id}").json(), "path": "../../../etc/passwd"}
        monkeypatch.setattr(client.app.state.indexer, "get_document", lambda _id: escaped)

        launched = _spy_on_launches(monkeypatch)
        r = client.post(f"/documents/{doc_id}/open", json={"editor": "code"})
        assert r.status_code == 400, r.text
        assert "escapes its repo" in r.json()["detail"]
        assert launched == []
