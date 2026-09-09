from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support.harness import (
    fake_templates_dir,
    isolated_bd,
    make_repo,
    make_repo_with_engineering,
)

from kraft import events
from kraft.adapters import beads

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

    return TestClient(api.app, client=("127.0.0.1", 54321))


def _linked_worktree(repo, tmp_path, name="wt"):
    """A second working tree of `repo` — what an agent hands work over from."""
    worktree = tmp_path / name
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree), "-b", name],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return worktree


def _poll_events(client, wid, want, timeout=30, count=1):
    """Wait until `want` has been appended at least `count` times."""
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        seen = client.get(f"/work-items/{wid}/events").json()
        if sum(e["type"] == want for e in seen) >= count:
            return seen
        time.sleep(0.2)
    raise AssertionError(f"{want} x{count} not seen; got {[e['type'] for e in seen]}")


def _await_gate(client, wid, gate, timeout=30):
    """Wait for the server to report `gate` as the one waiting on a person."""
    deadline = time.monotonic() + timeout
    item = {}
    while time.monotonic() < deadline:
        item = client.get(f"/work-items/{wid}").json()
        if item.get("pending_gate") == gate:
            return item
        time.sleep(0.2)
    raise AssertionError(f"{gate} never became pending; item={item.get('pending_gate')!r}")


def _wait_for_status(client, wid, status, timeout=30):
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/work-items/{wid}").json()
        if body["status"] == status:
            return body
        time.sleep(0.15)
    raise AssertionError(f"status never became {status!r}; last body={body}")


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
            "/work-items",
            json={
                "title": "make the failing test pass",
                "repo": str(repo),
                "chain_template": "quick-task",
            },
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
            "/work-items",
            json={
                "title": "make the failing test pass",
                "repo": str(repo),
                "chain_template": "quick-task",
            },
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed")

        item = client.get(f"/work-items/{wid}").json()
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
            docs = client.get(f"/work-items/{wid}/documents").json()["documents"]
            if docs:
                break
            time.sleep(0.05)
        summaries = [d for d in docs if d["source_kind"] == "session_summary"]
        assert summaries, f"no session summary linked to {wid}"
        assert {d["path"] for d in summaries} >= {impl_session["session_summary_ref"]}
        assert any(d["worker_session_id"] == impl_session["id"] for d in docs)

        # and the summary is searchable, carrying its links inline
        hits = client.get("/search", params={"q": "fake-claude session"}).json()["results"]
        assert hits, "session summary not searchable"
        assert any(
            ln["work_item_id"] == wid for h in hits for ln in h["links"] if ln["work_item_id"]
        )

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


def test_a_title_over_the_tracker_limit_is_refused_before_bd(tmp_path, monkeypatch):
    """Half of Kraft-cy30 landed already: `beads.intake` raises bd's own stderr
    (Kraft-ibwj) and `executor.intake` catches every bd failure into a
    `bead_warning` rather than a 502 (Kraft-7gy). What is left is quieter and
    worse -- an over-long title now *succeeds*, and the item exists with no bead
    and a warning line the caller may never read. Refuse it at the door.

    The offending title is not echoed back: the length and the limit are the
    actionable part, and this `detail` is printed straight through by
    `kraft item create`.
    """
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        long_title = "x" * 711
        r = client.post(
            "/work-items", json={"title": long_title, "repo": str(repo), "autostart": False}
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "711" in detail
        assert str(beads.MAX_TITLE) in detail
        assert long_title not in detail

        assert client.get("/work-items").json()["items"] == []

        # the boundary itself is accepted
        ok = client.post(
            "/work-items",
            json={"title": "x" * beads.MAX_TITLE, "repo": str(repo), "autostart": False},
        )
        assert ok.status_code == 201, ok.text


def test_get_unknown_work_item_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/work-items/does-not-exist").status_code == 404
        assert client.get("/worker-sessions/nope/log").status_code == 404


def test_create_accepts_a_description_and_both_payloads_return_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        r = client.post(
            "/work-items",
            json={
                "title": "short label",
                "description": "the brief the spec is written from",
                "repo": str(repo),
                "autostart": False,
            },
        )
        assert r.status_code == 201
        wid = r.json()["id"]

        detail = client.get(f"/work-items/{wid}").json()
        assert detail["description"] == "the brief the spec is written from"

        listed = client.get("/work-items").json()["items"]
        assert [i["description"] for i in listed if i["id"] == wid] == [
            "the brief the spec is written from"
        ]


def test_create_without_a_description_returns_null(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
        ).json()["id"]
        assert client.get(f"/work-items/{wid}").json()["description"] is None
        listed = client.get("/work-items").json()["items"]
        assert [i["description"] for i in listed if i["id"] == wid] == [None]


def test_patch_updates_the_description_and_records_an_event(tmp_path, monkeypatch):
    """The description feeds every agent prompt, so an edit has to be answerable
    from the timeline: `events` is the authoritative log."""
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/work-items",
            json={"title": "t", "description": "first", "repo": str(repo), "autostart": False},
        ).json()["id"]
        before = client.get(f"/work-items/{wid}").json()["updated_at"]

        r = client.patch(f"/work-items/{wid}", json={"description": "second"})
        assert r.status_code == 200
        assert r.json()["description"] == "second"

        after = client.get(f"/work-items/{wid}").json()
        assert after["description"] == "second"
        assert after["updated_at"] >= before

        evs = client.get(f"/work-items/{wid}/events").json()
        edits = [e for e in evs if e["type"] == "work_item_description_edited"]
        assert [e["payload"]["description"] for e in edits] == ["second"]


def test_patch_404s_on_an_unknown_work_item(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        assert client.patch("/work-items/nope", json={"description": "x"}).status_code == 404


def test_patch_can_clear_the_description(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/work-items",
            json={"title": "t", "description": "first", "repo": str(repo), "autostart": False},
        ).json()["id"]
        assert client.patch(f"/work-items/{wid}", json={"description": ""}).status_code == 200
        assert client.get(f"/work-items/{wid}").json()["description"] is None


def test_patch_sets_the_title_without_clobbering_the_description(tmp_path, monkeypatch):
    """Absent means untouched, in both directions. A screen that patches only
    the title must not blank the brief, and vice versa."""
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/work-items",
            json={
                "title": "typed in a hurry",
                "description": "the brief",
                "repo": str(repo),
                "autostart": False,
            },
        ).json()["id"]

        r = client.patch(f"/work-items/{wid}", json={"title": "a better label"})
        assert r.status_code == 200, r.text
        assert r.json() == {"id": wid, "title": "a better label"}
        after = client.get(f"/work-items/{wid}").json()
        assert after["title"] == "a better label"
        assert after["description"] == "the brief"

        r = client.patch(f"/work-items/{wid}", json={"description": "a better brief"})
        assert r.status_code == 200, r.text
        after = client.get(f"/work-items/{wid}").json()
        assert after["title"] == "a better label"
        assert after["description"] == "a better brief"

        evs = client.get(f"/work-items/{wid}/events").json()
        assert [e["payload"]["title"] for e in evs if e["type"] == "work_item_title_edited"] == [
            "a better label"
        ]


def test_patch_refuses_an_empty_body_and_a_blank_title(tmp_path, monkeypatch):
    """`{}` is a caller bug, not a no-op to absorb; a blank title would leave the
    board with an unlabelled row and nothing to search on."""
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
        ).json()["id"]

        empty = client.patch(f"/work-items/{wid}", json={})
        assert empty.status_code == 422
        assert "nothing to patch" in empty.json()["detail"]

        blank = client.patch(f"/work-items/{wid}", json={"title": "   "})
        assert blank.status_code == 422
        assert "title cannot be empty" in blank.json()["detail"]

        assert client.get(f"/work-items/{wid}").json()["title"] == "t"


def _post_default(client, repo):
    return client.post(
        "/work-items",
        json={
            "title": "make the failing test pass",
            "repo": str(repo),
            "chain_template": "default",
        },
    ).json()["id"]


def test_no_explicit_chain_template_still_runs_default_and_is_distinguishable(
    tmp_path, monkeypatch
):
    """Kraft-cd47: an item created with no `chain_template` runs the `default`
    template's chain like it always did, but its row stores that nothing was
    chosen -- not the string "default", which an item that named that template
    outright also stores. The two must not collide."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        unset_wid = client.post("/work-items", json={"title": "t", "repo": str(repo)}).json()["id"]
        named_wid = client.post(
            "/work-items", json={"title": "t", "repo": str(repo), "chain_template": "default"}
        ).json()["id"]
        unset = client.get(f"/work-items/{unset_wid}").json()
        named = client.get(f"/work-items/{named_wid}").json()
        assert unset["chain_template"] is None
        assert named["chain_template"] == "default"
        assert unset["chain_definition"]["nodes"] == named["chain_definition"]["nodes"]


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


def _chain_review_path(client, wid):
    run_dir = Path(client.app.state.run_dirs.base)
    return run_dir / "worktrees" / wid / ".engineering" / "chain_reviews" / f"{wid}.md"


def _write_chain_review(client, wid, envelope):
    path = _chain_review_path(client, wid)
    path.parent.mkdir(parents=True, exist_ok=True)
    front_matter = f"---\nwork_item_ids: [{wid}]\nkind: chain_reviews\ntitle: t\n---\n\n"
    path.write_text(front_matter + json.dumps(envelope) + "\n")


def test_chain_review_splice_runs_the_revised_tail(tmp_path, monkeypatch):
    """Kraft-hm0: a genuinely revised tail is spliced into `chain_definition`
    on `chain_finalized` approval, and the next node runs the added task, not
    whatever the template originally had there."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert client.post(f"/work-items/{wid}/gates/{gate}/approve").status_code == 200
        _await_gate(client, wid, "chain_finalized")

        tail = client.get(f"/work-items/{wid}").json()["chain_definition"]["nodes"]
        original = [n for n in tail if n["id"] not in ("spec", "plan", "chain_review")]
        revised = [
            {"id": "extra_check", "tasks": ["on.review.local.run"], "gate_after": None},
            *original,
        ]
        _write_chain_review(
            client,
            wid,
            {"status": "ready_for_approval", "revised_chain_nodes": revised, "rationale": "t"},
        )

        r = client.post(f"/work-items/{wid}/gates/chain_finalized/approve")
        assert r.status_code == 200, r.text
        _await_gate(client, wid, "human_review_approval")
        started = [
            e["payload"]["node_id"]
            for e in client.get(f"/work-items/{wid}/events").json()
            if e["type"] == "node_started"
        ]
        assert "extra_check" in started
        assert client.get(f"/work-items/{wid}").json()["chain_definition"]["nodes"][3]["id"] == (
            "extra_check"
        )


def test_chain_review_unchanged_tail_round_trips(tmp_path, monkeypatch):
    """The skill's own documented common case: emitting the tail unchanged is
    a no-op in effect, the same code path as a real splice (Kraft-hm0)."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert client.post(f"/work-items/{wid}/gates/{gate}/approve").status_code == 200
        _await_gate(client, wid, "chain_finalized")
        before = client.get(f"/work-items/{wid}").json()["chain_definition"]["nodes"]

        # fake-claude.sh's default answer for on.chain.review_ready is the
        # unchanged tail — no override needed here.
        r = client.post(f"/work-items/{wid}/gates/chain_finalized/approve")
        assert r.status_code == 200, r.text
        after = client.get(f"/work-items/{wid}").json()["chain_definition"]["nodes"]
        assert after == before


@pytest.mark.parametrize(
    ("envelope", "reason_has"),
    [
        (
            {"status": "error", "revised_chain_nodes": [], "rationale": "cannot decide"},
            "cannot decide",
        ),
        (
            {"status": "ready_for_approval", "revised_chain_nodes": [{"bad": "shape"}]},
            "chain_review",
        ),
    ],
)
def test_chain_review_error_or_invalid_tail_stops_at_needs_human(
    tmp_path, monkeypatch, envelope, reason_has
):
    """Kraft-hm0/unk: `status: "error"` and an invalid `revised_chain_nodes`
    both stop the item at needs_human rather than advancing, and never touch
    `chain_definition`."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert client.post(f"/work-items/{wid}/gates/{gate}/approve").status_code == 200
        _await_gate(client, wid, "chain_finalized")
        before = client.get(f"/work-items/{wid}").json()["chain_definition"]["nodes"]

        _write_chain_review(client, wid, envelope)
        r = client.post(f"/work-items/{wid}/gates/chain_finalized/approve")
        assert r.status_code == 200, r.text
        item = client.get(f"/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert item["chain_definition"]["nodes"] == before
        stops = [
            e
            for e in client.get(f"/work-items/{wid}/events").json()
            if e["type"] == "work_item_needs_human"
        ]
        assert stops and reason_has in stops[-1]["payload"]["reason"]


def test_chain_review_missing_artifact_stops_at_needs_human(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert client.post(f"/work-items/{wid}/gates/{gate}/approve").status_code == 200
        _await_gate(client, wid, "chain_finalized")
        _chain_review_path(client, wid).unlink()

        r = client.post(f"/work-items/{wid}/gates/chain_finalized/approve")
        assert r.status_code == 200, r.text
        assert client.get(f"/work-items/{wid}").json()["status"] == "needs_human"


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


def test_gate_reject_requires_note_and_re_runs_the_producer(tmp_path, monkeypatch):
    """A producer gate rejection is backward motion, not a dead end (02 §7.2).

    Before this, reject only appended an event: the gate stopped being pending
    and the node had no fix loop, so approve/retry/pause/resume all 409'd and
    the work item could never move again.
    """
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
        assert rej and rej[0]["payload"] == {
            "gate": "spec_approval",
            "note": "too vague",
            "node": "spec",
        }

        # the spec node runs again and asks for its gate a second time
        _poll_events(client, wid, "gate_requested", count=2)
        item = client.get(f"/work-items/{wid}").json()
        assert item["pending_gate"] == "spec_approval"
        assert client.post(f"/work-items/{wid}/gates/spec_approval/approve").status_code == 200


def test_gate_reject_is_bounded_by_its_reject_loop(tmp_path, monkeypatch):
    """Rejections are capped like a fix loop; the breach stops the item."""
    repo = make_repo(tmp_path)
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates / "policy.yaml").write_text(
        "loops: {}\ndefault: {attempts: 1, wall_clock_s: 3600}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        assert (
            client.post(
                f"/work-items/{wid}/gates/spec_approval/reject", json={"note": "again"}
            ).status_code
            == 200
        )
        _poll_events(client, wid, "gate_requested", count=2)
        assert (
            client.post(
                f"/work-items/{wid}/gates/spec_approval/reject", json={"note": "still no"}
            ).status_code
            == 200
        )
        item = client.get(f"/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert item["pending_gate"] is None
        stops = [
            e
            for e in client.get(f"/work-items/{wid}/events").json()
            if e["type"] == "work_item_needs_human"
        ]
        assert stops and "spec_approval_reject_loop" in stops[-1]["payload"]["reason"]


def test_rejecting_the_final_gate_re_enters_at_implementation(tmp_path, monkeypatch):
    """Kraft-ko7j. `human_review_approval` used to be terminal: the note went
    into an event nothing read and the item sat in needs_human with approve
    or abandon as its only exits. It now routes to the node named by the
    chain's `reject_to`, carrying the note as that node's steer."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    prompts = tmp_path / "prompts.log"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_PROMPT_LOG", str(prompts))
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval", "chain_finalized"):
            _await_gate(client, wid, gate)
            assert client.post(f"/work-items/{wid}/gates/{gate}/approve").status_code == 200
        _await_gate(client, wid, "human_review_approval")

        r = client.post(
            f"/work-items/{wid}/gates/human_review_approval/reject",
            json={"note": "the retry path is untested"},
        )
        assert r.status_code == 200, r.text

        # the chain went back to work rather than stopping
        rejected = [
            e for e in _poll_events(client, wid, "gate_rejected") if e["type"] == "gate_rejected"
        ]
        assert rejected[-1]["payload"]["node"] == "implementation"

        # ...walked implementation -> verify -> open_mr -> mr_checks -> human_review
        # and asked for the same gate a second time
        _poll_events(client, wid, "gate_requested", count=5)
        starts = [
            e["payload"]["node_id"]
            for e in client.get(f"/work-items/{wid}/events").json()
            if e["type"] == "node_started"
        ]
        assert starts.count("implementation") == 2
        assert client.get(f"/work-items/{wid}").json()["pending_gate"] == ("human_review_approval")

        # and the note led the re-run's instruction rather than dying in an event
        assert "the retry path is untested" in prompts.read_text()


def test_reject_refuses_a_node_after_its_gate(tmp_path, monkeypatch):
    """A rejection is backward motion. Naming a later node would let the human
    skip every node between the gate and the target."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _await_gate(client, wid, "spec_approval")
        assert client.post(f"/work-items/{wid}/gates/spec_approval/approve").status_code == 200
        _await_gate(client, wid, "plan_approval")

        r = client.post(
            f"/work-items/{wid}/gates/plan_approval/reject",
            json={"note": "wrong", "node": "merge"},
        )
        assert r.status_code == 400, r.text
        assert "merge" in r.json()["detail"]
        # nothing was spawned and nothing was recorded
        item = client.get(f"/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert item["pending_gate"] == "plan_approval"
        assert not [
            e
            for e in client.get(f"/work-items/{wid}/events").json()
            if e["type"] == "gate_rejected"
        ]


def test_reject_refuses_a_node_that_is_not_in_this_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _await_gate(client, wid, "spec_approval")
        r = client.post(
            f"/work-items/{wid}/gates/spec_approval/reject",
            json={"note": "wrong", "node": "no_such_node"},
        )
        assert r.status_code == 400, r.text


def test_a_rejection_note_is_the_default_steer_for_the_next_retry(tmp_path, monkeypatch):
    """Kraft-ko7j: a human who typed a reason should not have to retype it to
    make it count."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates / "policy.yaml").write_text(
        "loops: {}\ndefault: {attempts: 1, wall_clock_s: 3600}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates) as client:
        wid = _post_default(client, repo)
        _await_gate(client, wid, "spec_approval")
        client.post(f"/work-items/{wid}/gates/spec_approval/reject", json={"note": "again"})
        _await_gate(client, wid, "spec_approval")
        client.post(f"/work-items/{wid}/gates/spec_approval/reject", json={"note": "still no"})
        _wait_for_status(client, wid, "needs_human")

        r = client.post(f"/work-items/{wid}/retry", json={})
        assert r.status_code == 200, r.text
        assert r.json()["steer"] == "still no"
        retried = [
            e
            for e in client.get(f"/work-items/{wid}/events").json()
            if e["type"] == "work_item_retried"
        ]
        assert retried[-1]["payload"]["steer"] == "still no"


def test_retry_clears_the_gate_reject_counter(tmp_path, monkeypatch):
    """Kraft-ko7j §A4. Retry is the human's override of the reject cap: after
    it, the re-opened gate can be rejected again rather than 'exhausted'
    forever."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates / "policy.yaml").write_text(
        "loops: {}\ndefault: {attempts: 1, wall_clock_s: 3600}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates) as client:
        wid = _post_default(client, repo)
        _await_gate(client, wid, "spec_approval")
        for note in ("again", "still no"):
            client.post(f"/work-items/{wid}/gates/spec_approval/reject", json={"note": note})
            if note == "again":
                _await_gate(client, wid, "spec_approval")
        _wait_for_status(client, wid, "needs_human")

        assert client.post(f"/work-items/{wid}/retry", json={}).status_code == 200
        _await_gate(client, wid, "spec_approval")
        r = client.post(
            f"/work-items/{wid}/gates/spec_approval/reject", json={"note": "third time"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active", (
            "the cleared cap did not give the gate a fresh budget"
        )


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
        shell = client.get("/work-items", headers=nav)
        assert "<title>kraft</title>" in shell.text
        assert "<title>kraft</title>" in client.get("/health", headers=nav).text
        # The shell is served under URLs that are also API routes, and a browser
        # caches by URL. Cached, it would answer the SPA's own fetch for the same
        # path — the detail screen rendered an empty husk on every deep link.
        assert shell.headers["cache-control"] == "no-store"
        assert shell.headers["vary"] == "sec-fetch-dest"
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


def test_work_item_usage_rollup_is_captured_from_the_agent_envelope(tmp_path, monkeypatch):
    """The whole capture path in one go: the agent reports tokens on its final
    envelope, the adapter prices and stores them, and GET /work-items/{id}
    rolls them up per node and per item."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "make it pass", "chain_template": "quick-task"},
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed", timeout=120)

        usage = client.get(f"/work-items/{wid}").json()["usage"]
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


def test_cross_repo_intake_records_submodules_and_orders_the_merge(tmp_path, monkeypatch):
    """Design 1g's cross-repo disclosure and 3a's repos panel: the submodules
    chosen at intake come back ordered deepest-first, because a submodule has to
    merge before the parent that points at it."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={
                "repo": str(repo),
                "title": "bump the pointers",
                "chain_template": "quick-task",
                "submodules": ["libs/a", "vendor/deep/b"],
                "root_merge_policy": "skip",
            },
        ).json()["id"]
        body = client.get(f"/work-items/{wid}").json()
        assert [r["path"] for r in body["repos"]] == ["vendor/deep/b", "libs/a", str(repo)]
        assert [r["role"] for r in body["repos"]] == ["submodule", "submodule", "root"]
        assert [r["merge_rank"] for r in body["repos"]] == [1, 2, 3]
        assert all(r["state"] == "pending" for r in body["repos"])
        assert body["root_merge_policy"] == "skip"

        bad = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "x", "root_merge_policy": "nonsense"},
        )
        assert bad.status_code == 422


def test_a_single_repo_item_has_no_repos_panel(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "solo", "chain_template": "quick-task"},
        ).json()["id"]
        assert client.get(f"/work-items/{wid}").json()["repos"] == []


def test_intake_with_a_plan_attachment_trims_the_chain_and_reports_it(tmp_path, monkeypatch):
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "chain_template": "default",
                "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
            },
        )
        assert r.status_code == 201, r.text
        wid = r.json()["id"]
        item = client.get(f"/work-items/{wid}").json()
        assert item["attachments"] == [{"kind": "plan", "path": ".engineering/plans/p.md"}]
        assert "plan_approval" not in [n["gate_after"] for n in item["chain_definition"]["nodes"]]
        listed = next(i for i in client.get("/work-items").json()["items"] if i["id"] == wid)
        assert listed["attachments"] == [{"kind": "plan", "path": ".engineering/plans/p.md"}]


def test_intake_with_a_plan_attachment_never_runs_the_plan_node(tmp_path, monkeypatch):
    """The trimmed node must be absent from the run, not merely from the
    chain_definition the UI reads (see the _trims_the_chain_and_reports_it
    test above for that check)."""
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "chain_template": "default",
                "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
            },
        ).json()["id"]
        _await_gate(client, wid, "spec_approval")
        client.post(f"/work-items/{wid}/gates/spec_approval/approve")
        events = _poll_events(client, wid, "node_started", count=2)
        started = [e["payload"]["node_id"] for e in events if e["type"] == "node_started"]
        assert "plan" not in started
        assert "chain_review" in started


def test_intake_rejects_a_traversing_attachment_path(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [{"kind": "plan", "path": "../outside.md"}],
            },
        )
        assert r.status_code == 422
        assert "escapes" in r.text


def test_intake_rejects_an_absolute_attachment_path(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    # Absolute and outside the repo, but real — proves rejection is about
    # location, not existence (an absolute path to a missing file would 422
    # for the wrong reason).
    outside = tmp_path / "outside.md"
    outside.write_text("# outside\n")
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [{"kind": "plan", "path": str(outside)}],
            },
        )
        assert r.status_code == 422
        assert "escapes" in r.text


def test_intake_rejects_a_symlink_that_escapes_the_repo(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# outside\n")
    # The only thing that makes this path escape is the symlink target; the
    # path string itself is repo-relative, so this fails only if .resolve()
    # actually follows the symlink before the is_relative_to check.
    escape = repo / ".engineering" / "plans" / "escape.md"
    escape.parent.mkdir(parents=True)
    escape.symlink_to(outside)
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [{"kind": "plan", "path": ".engineering/plans/escape.md"}],
            },
        )
        assert r.status_code == 422
        assert "escapes" in r.text


def test_intake_rejects_a_missing_attachment_and_a_duplicate_kind(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        missing = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [{"kind": "plan", "path": ".engineering/plans/nope.md"}],
            },
        )
        assert missing.status_code == 422
        (repo / ".engineering" / "plans").mkdir(parents=True)
        (repo / ".engineering" / "plans" / "p.md").write_text("# p\n")
        dupe = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [
                    {"kind": "plan", "path": ".engineering/plans/p.md"},
                    {"kind": "plan", "path": ".engineering/plans/p.md"},
                ],
            },
        )
        assert dupe.status_code == 422


def test_intake_accepts_an_uncommitted_attachment(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    plan = repo / ".engineering" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# uncommitted\n")
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
            },
        )
        assert r.status_code == 201, r.text


def test_intake_accepts_an_attachment_from_another_worktree_of_the_repo(tmp_path, monkeypatch):
    """Kraft-85wk. The document exists only in the working tree the caller is
    standing in — which for a Kraft worker is always true, because the
    registered repo is the main checkout by construction (config.probe_repo
    normalizes a worktree to it). Resolving under the repo alone 422s the exact
    handoff attachments exist for."""
    repo = make_repo(tmp_path)
    worktree = _linked_worktree(repo, tmp_path)
    spec = worktree / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# written in the worktree\n")
    assert not (repo / ".engineering" / "specs" / "s.md").exists()
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "autostart": False,
                "cwd": str(worktree),
                "attachments": [{"kind": "spec", "path": ".engineering/specs/s.md"}],
            },
        )
        assert r.status_code == 201, r.text
        stored = client.get(f"/work-items/{r.json()['id']}").json()["attachments"]
        # repo-relative path, unchanged in shape: it is what the prompt note,
        # the board badge and index/service._attachment_docs all read.
        assert stored[0]["kind"] == "spec"
        assert stored[0]["path"] == ".engineering/specs/s.md"
        # and the absolute source, because `repo / path` does not exist
        assert stored[0]["source"] == str(spec.resolve())


def test_intake_without_a_cwd_still_scopes_attachments_to_the_repo(tmp_path, monkeypatch):
    """The browser sends no cwd, so its reachable set stays exactly the repo,
    as Kraft-7izl set it. Same file, same request, minus one field."""
    repo = make_repo(tmp_path)
    worktree = _linked_worktree(repo, tmp_path)
    spec = worktree / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# written in the worktree\n")
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "autostart": False,
                "attachments": [{"kind": "spec", "path": ".engineering/specs/s.md"}],
            },
        )
        assert r.status_code == 422
        assert "not found" in r.text


def test_intake_ignores_a_cwd_in_a_different_repo(tmp_path, monkeypatch):
    """`cwd` is not "read any file on this machine": a second root is added only
    when it is another working tree of the *same* repository. An unrelated repo
    holding a file at the same relative path proves the check is the shared
    `.git` and not the path string."""
    repo = make_repo(tmp_path)
    stranger = make_repo(tmp_path, name="stranger")
    spec = stranger / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# not this repo's spec\n")
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "autostart": False,
                "cwd": str(stranger),
                "attachments": [{"kind": "spec", "path": ".engineering/specs/s.md"}],
            },
        )
        assert r.status_code == 422
        assert "not found" in r.text


def test_deferred_minor_findings_reach_the_detail_payload(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
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

        body = client.get(f"/work-items/{wid}").json()
        assert [f["message"] for f in body["deferred_findings"]] == ["naming nit"]


def test_concerns_reach_the_detail_payload(tmp_path, monkeypatch):
    """`done_with_concerns` text rides `worker_session_exited` (written by
    adapters.subprocess.run_task at session exit) — read from the event log,
    the same shape as `deferred_findings`, one entry per session that reported
    a concern."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        _seed_events(
            client,
            wid,
            [{"session_id": "s1", "status": "done_with_concerns", "concerns": "untested path"}],
            event_type="worker_session_exited",
        )

        body = client.get(f"/work-items/{wid}").json()
        assert body["concerns"] == ["untested path"]


def test_stop_reason_reaches_the_detail_payload(tmp_path, monkeypatch):
    """Kraft-esc: the screen has to tell a loop escalation from a crash that
    happened to stop the item on a fix-loop node, and the reason is the only
    thing that distinguishes them."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        assert client.get(f"/work-items/{wid}").json()["stop_reason"] is None

        _seed_events(
            client,
            wid,
            [{"node_id": "verify", "reason": "executor crashed: RuntimeError('boom')"}],
            event_type="work_item_needs_human",
        )
        body = client.get(f"/work-items/{wid}").json()
        assert body["stop_reason"] == "executor crashed: RuntimeError('boom')"


def test_concerns_stop_at_the_gate_that_answered_them(tmp_path, monkeypatch):
    """Kraft-ub2: a concern belongs to the *next* gate. Once a gate is resolved,
    concerns raised before it must not be re-posed at every later gate — only
    what a session reported since then."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        _seed_events(
            client,
            wid,
            [{"session_id": "s1", "status": "done_with_concerns", "concerns": "old worry"}],
            event_type="worker_session_exited",
        )
        _seed_events(client, wid, [{"gate": "spec_approval"}], event_type="gate_approved")
        assert client.get(f"/work-items/{wid}").json()["concerns"] == []

        _seed_events(
            client,
            wid,
            [{"session_id": "s2", "status": "done_with_concerns", "concerns": "new worry"}],
            event_type="worker_session_exited",
        )
        assert client.get(f"/work-items/{wid}").json()["concerns"] == ["new worry"]


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
            "/work-items",
            json={"repo": str(repo), "title": "needs a decision", "chain_template": "quick-task"},
        ).json()["id"]
        body = _wait_for_status(client, wid, "needs_human")
        assert body["needs_context_question"] == "which repo does this target?"


def test_needs_context_question_does_not_resurface_a_stale_answer(tmp_path, monkeypatch):
    """A second `needs_context` stop whose result file omitted `question`
    (the fallback executor._needs_context_question uses is folded into the
    reason as `"(no question given)"`) must show that fallback, not an
    earlier stop's already-answered question — the bug an unbounded scan of
    `worker_session_exited.question` events would have produced."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
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

        body = client.get(f"/work-items/{wid}").json()
        assert body["needs_context_question"] == "(no question given)"


def _set_status(wid: str, status: str) -> None:
    """Force a work item's status. The states this test needs — one item wedged
    active while another waits paused — are transient under real orchestration,
    so they are written directly rather than raced for."""
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute("UPDATE work_items SET status = ? WHERE id = ?", (status, wid))
        conn.commit()
    finally:
        conn.close()


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

        r = client.post(f"/work-items/{idle}/resume", json={})

        assert r.status_code == 409, r.text
        assert "1" in r.json()["detail"]
        assert client.get(f"/work-items/{busy}").json()["status"] == "active"


def test_resume_works_when_a_slot_is_free(tmp_path, monkeypatch):
    """The guard must not wedge the ordinary single-item case shut."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "paused")
        client.app.state.intake["max_concurrent"] = 1

        r = client.post(f"/work-items/{wid}/resume", json={})

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

        r = client.post(f"/work-items/{wid}/abandon")

        assert r.status_code == 200, r.text
        assert r.json()["status"] == "abandoned"
        assert not worktree.exists()


def test_abandon_refuses_an_active_item(tmp_path, monkeypatch):
    """Pause first. Otherwise this races a running agent's writes."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "active")

        r = client.post(f"/work-items/{wid}/abandon")

        assert r.status_code == 409, r.text
        assert "active" in r.json()["detail"]


def test_list_hides_abandoned_items(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "paused")
        client.post(f"/work-items/{wid}/abandon")

        visible = client.get("/work-items").json()["items"]
        everything = client.get("/work-items?include_abandoned=true").json()["items"]

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
        client.post(f"/work-items/{wid}/abandon")

        import kraft.client as kc

        monkeypatch.setattr(kc, "_get", lambda path: _as_coro(client.get(path).json()))
        visible = asyncio.run(kc.list_work_items())
        everything = asyncio.run(kc.list_work_items(include_abandoned=True))

        assert wid not in [i["id"] for i in visible]
        assert wid in [i["id"] for i in everything]


async def _as_coro(value):
    return value
