from __future__ import annotations

import time

import pytest
from support.harness import e2e_templates_dir, isolated_bd, make_repo
from support.server import running_server

pytestmark = pytest.mark.e2e


def _poll(client, wid, want, timeout=180):
    deadline = time.monotonic() + timeout
    types = []
    while time.monotonic() < deadline:
        evs = client.get(f"/work-items/{wid}/events").json()
        types = [e["type"] for e in evs]
        if want in types:
            return evs
        if "work_item_needs_human" in types:
            raise AssertionError(f"chain went to needs_human; events={types}")
        time.sleep(1.0)
    raise AssertionError(f"{want} not seen in {timeout}s; got {types}")


def test_e2e_happy_path(tmp_path):
    run_dir = tmp_path / "run"
    templates = e2e_templates_dir(tmp_path)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    claude_md = repo / "CLAUDE.md"
    claude_md_before = claude_md.read_text() if claude_md.exists() else None

    with running_server(run_dir=run_dir, templates_dir=templates, bd_cwd=tracker) as srv:
        wid = srv.client.post(
            "/work-items", json={"title": "make the failing test pass", "repo": str(repo)}
        ).json()["id"]
        _poll(srv.client, wid, "work_item_completed")

        item = srv.client.get(f"/work-items/{wid}").json()
        assert item["status"] == "completed"

        docs = srv.client.get(f"/work-items/{wid}/documents").json()["documents"]
        summaries = [d for d in docs if d["source_kind"] == "session_summary"]
        assert summaries, f"no session summary linked to {wid}; got {docs}"
        assert summaries[0]["path"].startswith(".engineering/sessions/")
        assert any(d["worker_session_id"] for d in docs)
        assert "a + b" in (run_dir / "worktrees" / wid / "calc.py").read_text()

        # context-injection boundary: the agent never wrote into the target repo
        after = claude_md.read_text() if claude_md.exists() else None
        assert after == claude_md_before

    # bead closed in the isolated tracker
    import subprocess

    show = subprocess.run(
        ["bd", "show", item["bead_id"], "--json"],
        cwd=tracker,
        capture_output=True,
        text=True,
        check=True,
    )
    import json as _json

    assert _json.loads(show.stdout)[0]["status"] == "closed"
