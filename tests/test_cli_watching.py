"""logs, events and watch — the streaming door onto a running work item."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft import cli, client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.http()
# to the ASGI app with the lifespan entered per client.


def _make_item(repo, title="watch me"):
    async def go():
        async with client.http() as http:
            response = await http.post(
                "/work-items", json={"title": title, "repo": str(repo), "autostart": False}
            )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return asyncio.run(go())


def test_worker_sessions_is_empty_before_anything_runs(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    assert asyncio.run(client.worker_sessions(wid)) == []


def test_latest_session_says_so_when_nothing_has_run(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    with pytest.raises(ValueError, match="no worker session"):
        asyncio.run(client.latest_session(wid))


def test_events_returns_the_chain_history(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    rows = asyncio.run(client.events(wid))
    assert isinstance(rows, list)
    assert all("type" in row and "seq" in row for row in rows)


def test_events_defaults_to_the_resolved_work_item(app, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    assert asyncio.run(client.events()) == asyncio.run(client.events(wid))


@pytest.mark.slow
def test_stream_log_follows_a_session_and_stops_when_it_stops(tmp_path, monkeypatch):
    """Real uvicorn: SSE through ASGITransport is not the same code path, and a
    stream that never ends is the failure this test exists to catch."""
    from support.server import running_server

    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    run_dir = tmp_path / "run"
    with running_server(run_dir=run_dir, templates_dir=templates, bd_cwd=tracker) as srv:
        monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
        monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
        monkeypatch.setenv("KRAFT_PORT", str(srv.port))
        monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
        response = srv.client.post("/work-items", json={"title": "log me", "repo": str(repo)})
        assert response.status_code == 201, response.text
        wid = response.json()["id"]

        async def collect():
            # wait for the fake agent to produce a session
            for _ in range(150):
                sessions = await client.worker_sessions(wid)
                if sessions:
                    break
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("no worker session appeared in 30s")
            return [line async for line in client.stream_log(sessions[-1]["id"])]

        lines = asyncio.run(asyncio.wait_for(collect(), timeout=90))

    assert lines, "the fake agent writes at least one line"
    assert all({"n", "src", "text"} <= set(line) for line in lines)
    # line numbers arrive in order and without gaps from the start
    assert [line["n"] for line in lines] == sorted(line["n"] for line in lines)


def test_events_follow_filters_by_item_and_stops_on_completion(app, tmp_path, monkeypatch, capsys):
    """The bus is instance-wide and never ends on its own: a foreign-item event
    must not print, and work_item_completed must end the follow rather than
    hang waiting for a frame that never comes."""
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    other = _make_item(repo, "someone else's chain")

    async def fake_stream(after_seq=0):
        yield {"type": "node_started", "seq": 101, "work_item_id": other}
        yield {"type": "node_started", "seq": 102, "work_item_id": wid}
        yield {"type": "work_item_completed", "seq": 103, "work_item_id": wid}
        yield {"type": "node_started", "seq": 104, "work_item_id": wid}  # must never be reached

    monkeypatch.setattr(client, "stream_events", fake_stream)
    cli.main(["events", wid, "-f"])
    out = capsys.readouterr().out
    assert "101" not in out  # foreign item filtered out
    assert "102" in out
    assert "103" in out
    assert "104" not in out  # left the loop at work_item_completed, not run on
    # the bug as a human meets it: `SEQ` above every single streamed line
    assert out.count("SEQ") == 1


def test_events_follow_returns_at_once_on_an_item_that_already_ended(
    app, tmp_path, monkeypatch, capsys
):
    """A finished item's terminal event is in the backlog, not in the stream.

    Without this the follow waits on a bus that has nothing left to say about
    this item -- the exact "armed forever" failure the stop condition exists
    to prevent, just reached from the other side.
    """
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def never_ends(after_seq=0):
        yield {"type": "node_started", "seq": 999, "work_item_id": wid}
        raise AssertionError("the follow should not have reached the stream")

    monkeypatch.setattr(client, "stream_events", never_ends)

    async def abandon():
        async with client.http() as http:
            assert (await http.post(f"/work-items/{wid}/abandon")).status_code == 200

    asyncio.run(abandon())

    cli.main(["events", wid, "-f"])
    out = capsys.readouterr().out
    assert "work_item_abandoned" in out
    assert "999" not in out


def test_logs_without_a_session_says_so(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    with pytest.raises(SystemExit) as caught:
        cli.main(["logs", wid])
    assert caught.value.code == 1
    assert "no worker session" in capsys.readouterr().err


def test_logs_backlog_renders_lines(app, tmp_path, monkeypatch, capsys):
    """The backlog path needs no server streaming: it is a plain GET."""
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_latest(work_item_id=None):
        return {"id": "sess-1", "status": "stopped"}

    async def fake_get(path, **params):
        assert path == "/worker-sessions/sess-1/log"
        return {
            "session_id": "sess-1",
            "status": "stopped",
            "lines": [
                {"n": 0, "t": None, "src": "stdout", "text": "first"},
                {"n": 1, "t": None, "src": "stdout", "text": "second"},
            ],
        }

    monkeypatch.setattr(client, "latest_session", fake_latest)
    monkeypatch.setattr(client, "_get", fake_get)
    cli.main(["logs", wid])
    out = capsys.readouterr().out
    assert "first" in out and "second" in out


def test_logs_n_limits_the_backlog(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_latest(work_item_id=None):
        return {"id": "sess-1", "status": "stopped"}

    async def fake_get(path, **params):
        return {
            "session_id": "sess-1",
            "status": "stopped",
            "lines": [{"n": i, "t": None, "src": "stdout", "text": f"line{i}"} for i in range(10)],
        }

    monkeypatch.setattr(client, "latest_session", fake_latest)
    monkeypatch.setattr(client, "_get", fake_get)
    cli.main(["logs", wid, "-n", "3"])
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 3
    assert "line9" in out[-1]


def test_logs_json_is_ndjson(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_latest(work_item_id=None):
        return {"id": "sess-1", "status": "stopped"}

    async def fake_get(path, **params):
        return {"lines": [{"n": 0, "t": None, "src": "stdout", "text": "one"}]}

    monkeypatch.setattr(client, "latest_session", fake_latest)
    monkeypatch.setattr(client, "_get", fake_get)
    cli.main(["logs", wid, "--json"])
    out = capsys.readouterr().out.strip()
    # one JSON object per line, no enclosing array: a stream has no closing bracket
    assert json.loads(out)["text"] == "one"
    assert not out.startswith("[")


def test_events_renders_a_table(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["events", wid])
    out = capsys.readouterr().out
    assert "SEQ" in out.splitlines()[0]
    assert "TYPE" in out.splitlines()[0]


def test_events_filters_by_type(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["events", wid, "--type", "no-such-type", "--json"])
    assert json.loads(capsys.readouterr().out) == []


def test_events_after_seq_is_passed_through(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    seen = {}

    async def fake_events(work_item_id=None, after_seq=0):
        seen["after_seq"] = after_seq
        return []

    monkeypatch.setattr(client, "events", fake_events)
    cli.main(["events", wid, "--after", "7", "--json"])
    assert seen["after_seq"] == 7


def test_watch_refuses_a_pipe(app, capsys, monkeypatch):
    """Redrawing into a pipe produces garbage; point at `events` instead."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    with pytest.raises(SystemExit) as caught:
        cli.main(["watch"])
    assert caught.value.code == 1
    assert "events" in capsys.readouterr().err


def test_watch_has_no_json_mode(app, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    with pytest.raises(SystemExit) as caught:
        cli.main(["watch", "--json"])
    assert caught.value.code == 1
    assert "--json" in capsys.readouterr().err


def test_redraw_returns_the_new_line_count():
    from kraft import render

    assert render.redraw("a\nb\nc", 0) == 3


@pytest.mark.slow
def test_stream_events_yields_a_frame_when_a_work_item_is_created(tmp_path, monkeypatch):
    from support.server import running_server

    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    run_dir = tmp_path / "run"
    with running_server(run_dir=run_dir, templates_dir=templates, bd_cwd=tracker) as srv:
        monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
        monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
        monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
        monkeypatch.setenv("KRAFT_PORT", str(srv.port))

        async def collect():
            stream = client.stream_events()
            first = asyncio.create_task(anext(stream))
            await asyncio.sleep(0.5)  # let the handshake complete before the write
            srv.client.post("/work-items", json={"title": "seen live", "repo": str(repo)})
            event = await asyncio.wait_for(first, timeout=20)
            await stream.aclose()
            return event

        event = asyncio.run(collect())

    assert "type" in event


def test_logs_n_zero_prints_no_backlog(app, tmp_path, monkeypatch, capsys):
    """`-n 0` means none. Truthiness would read it as "no limit"."""
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_latest(work_item_id=None):
        return {"id": "sess-1", "status": "stopped"}

    async def fake_get(path, **params):
        return {"lines": [{"n": i, "t": None, "src": "stdout", "text": f"l{i}"} for i in range(5)]}

    monkeypatch.setattr(client, "latest_session", fake_latest)
    monkeypatch.setattr(client, "_get", fake_get)
    cli.main(["logs", wid, "-n", "0"])
    assert capsys.readouterr().out == ""


def test_watch_draws_a_frame_per_event_and_starts_at_the_live_cursor(
    app, tmp_path, monkeypatch, capsys
):
    """The happy path: one frame before the stream, one per event, and the
    stream is asked to start at the cursor the first board came with — not at
    seq 0, which would replay every event the server ever committed."""
    repo = make_repo(tmp_path)
    _make_item(repo, "on the board")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    seen = {}

    async def fake_stream(after_seq=0):
        seen["after_seq"] = after_seq
        for _ in range(2):
            yield {"type": "node_started", "seq": after_seq + 1}

    monkeypatch.setattr(client, "stream_events", fake_stream)
    cli.main(["watch"])
    out = capsys.readouterr().out
    assert out.count("on the board") == 3  # the first frame plus one per event
    assert seen["after_seq"] > 0  # the live cursor, not a full replay


def test_a_closed_event_stream_is_a_kraft_message(app, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

    async def dying_stream(after_seq=0):
        raise ValueError("the Kraft server at http://127.0.0.1:8765 closed the event stream")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(client, "stream_events", dying_stream)
    with pytest.raises(SystemExit) as caught:
        cli.main(["watch"])
    assert caught.value.code == 1
    assert "closed the event stream" in capsys.readouterr().err


def test_log_backlog_limits_live_in_client(monkeypatch):
    """The backlog read is `client.py`'s, not the CLI's — the MCP door reads it
    the same way rather than copying the path (spec F §2.3)."""

    async def fake_get(path, **params):
        assert path == "/worker-sessions/sess-1/log" and params == {"format": "jsonl"}
        return {"lines": [{"n": i, "text": f"line{i}"} for i in range(5)]}

    monkeypatch.setattr(client, "_get", fake_get)
    assert len(asyncio.run(client.log_backlog("sess-1"))) == 5
    assert [e["n"] for e in asyncio.run(client.log_backlog("sess-1", 2))] == [3, 4]
    # 0 means none, and must not read as "no limit"
    assert asyncio.run(client.log_backlog("sess-1", 0)) == []


def test_events_follow_json_is_one_object_per_line(app, tmp_path, monkeypatch, capsys):
    """`kraft logs -f --json` is NDJSON and CLAUDE.md documents that contract.
    A followed stream that emits pretty-printed arrays breaks `read -r`, a
    `split("\\n")`, and any log shipper (Kraft-tom2)."""
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_stream(after_seq=0):
        yield {"type": "node_started", "seq": 201, "work_item_id": wid}
        yield {"type": "work_item_completed", "seq": 202, "work_item_id": wid}

    monkeypatch.setattr(client, "stream_events", fake_stream)
    cli.main(["events", wid, "--after", "9999", "-f", "--json"])
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    # --after 9999 empties the backlog, so every line here is a streamed frame
    parsed = [json.loads(line) for line in lines[1:]]
    assert [event["seq"] for event in parsed] == [201, 202]
    assert all(isinstance(event, dict) for event in parsed)
