"""`needs_context` stops the item (both `_walk_node` branches) and can be
answered via /steer + /resume — the feature this sub-project turns on.

Executor-level scenarios follow test_fix_loop.py's shape (no fixtures, plain
`async def scenario(): ...` under `asyncio.run`). API-level scenarios follow
test_pause_resume.py's shape (a `TestClient` over the real HTTP surface).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from support.harness import (
    fake_templates_dir,
    isolated_bd,
    v1_fix_loop_node,
    v1_named_chain,
    v1_seeded_chain,
)

from kraft import events, executor, policy, store

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


_FAKE = f"{sys.executable} {_FAKE_AGENT}"


def _quick_task(tmp_path):
    """The shipped gateless `quick-task`, its agent task on the fake agent."""
    return v1_named_chain(tmp_path / "templates", agent_command=_FAKE)


def _events(database, wid):
    return database.read(lambda c: events.read_after(c, 0, wid))


def _reason(database, wid) -> str | None:
    for e in reversed(_events(database, wid)):
        if e["type"] == "work_item_needs_human":
            return e["payload"]["reason"]
    return None


# ---------------------------------------------------------------------------
# Plain branch (`loop is None`) — the implementer as a non-loop node, exactly
# as it ships in quick-task.
# ---------------------------------------------------------------------------


async def test_needs_context_stops_the_item_with_the_question_in_the_reason(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_QUESTION", "which database should this target?")
    tracker = isolated_bd(tmp_path)

    wid = await executor.intake(
        database,
        run_dirs,
        title="needs a decision",
        repo=str(repo),
        chain=_quick_task(tmp_path),
        bd_cwd=str(tracker),
    )
    result = await executor.run(
        database, run_dirs, work_item_id=wid, registry=None, bd_cwd=str(tracker)
    )
    assert result == "needs_human"
    row = database.read(
        lambda c: c.execute(
            "SELECT status, current_node_id FROM work_items WHERE id=?", (wid,)
        ).fetchone()
    )
    assert row["status"] == "needs_human"
    # the plain implementation node, not the fix loop: quick-task has none
    assert row["current_node_id"] == "implementation"
    assert _reason(database, wid) == "needs_context: which database should this target?"


# ---------------------------------------------------------------------------
# Fix-loop branch — a measuring task's needs_context must not consume a cycle,
# checked across two separate entries into the node (a single call passes
# trivially: bump_counter simply hasn't run yet the first time through).
# ---------------------------------------------------------------------------


def _fixloop_template(tmp_path):
    """`verify`'s measuring task is an agent (the fake agent) so a *measuring*
    task can report needs_context — pytest can't. No `env_setup` node: V1
    prepares the worktree first."""
    measure = {"id": "check", "kind": "agent", "harness": "fake", "prompt": "Check it."}
    return v1_seeded_chain(
        tmp_path / "templates", [v1_fix_loop_node("verify", measure)], agent_command=_FAKE
    )


def _make_policy(tmp_path, *, attempts=3, wall_clock_s=3600) -> policy.Policy:
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"loops:\n  verify.fix_loop: {{ attempts: {attempts}, wall_clock_s: {wall_clock_s} }}\n"
        f"default: {{ attempts: {attempts}, wall_clock_s: {wall_clock_s} }}\n"
        # Kraft-lpdd: this suite is about needs_context detection, not the
        # unrelated auto-escalate trigger a real cap breach would otherwise
        # also fire (and `executor.run` is called here without a `launch`).
        "auto_escalate_stuck: false\n"
    )
    return policy.load_policy(p)


async def test_needs_context_from_a_measuring_task_does_not_consume_a_cycle(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_QUESTION", "which branch is the target?")
    tracker = isolated_bd(tmp_path)

    pol = _make_policy(tmp_path)
    wid = await executor.intake(
        database,
        run_dirs,
        title="needs a decision",
        repo=str(repo),
        chain=_fixloop_template(tmp_path),
        bd_cwd=str(tracker),
    )

    # cycle 1: the measuring task itself asks a question.
    result = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        policy=pol,
    )
    assert result == "needs_human"
    assert _reason(database, wid) == "needs_context: which branch is the target?"
    assert database.read(lambda c: store.read_counter(c, wid, "verify.fix_loop")) is None

    # A human answers via the same mechanism /resume uses (set + take the
    # steer, relaunch from the current node) and the agent still can't
    # proceed — a second cycle, and the cap must still be untouched.
    await database.write(lambda c: store.set_steer(c, wid, "still can't tell"))
    steer = await database.write(lambda c: store.take_steer(c, wid))
    await database.write(lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"]))
    await database.write(lambda c: store.resume_work_item(c, wid, steer))
    result2 = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        policy=pol,
        start_index=0,  # "verify"
        steer=steer,
    )
    assert result2 == "needs_human"
    assert database.read(lambda c: store.read_counter(c, wid, "verify.fix_loop")) is None


async def test_needs_context_ignores_a_stale_row_from_an_earlier_pass(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """A first-match scan over `sessions_for_round` (instead of latest-row-per-
    hook-point) would find pass 1's stale needs_context row forever, even after
    pass 2 reports something else entirely. `test_..._does_not_consume_a_cycle`
    above cannot catch this: it never varies the outcome between passes, so it
    passes identically under a first-match implementation. This one does vary
    it — attempts=1 so a real, distinct second-pass failure runs the fix loop to
    a cap breach in one extra cycle, cheaply."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_QUESTION", "which branch is the target?")
    tracker = isolated_bd(tmp_path)

    pol = _make_policy(tmp_path, attempts=1)
    wid = await executor.intake(
        database,
        run_dirs,
        title="needs a decision",
        repo=str(repo),
        chain=_fixloop_template(tmp_path),
        bd_cwd=str(tracker),
    )

    # pass 1: the measuring task asks a question, round 0.
    result = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        policy=pol,
    )
    assert result == "needs_human"
    assert _reason(database, wid) == "needs_context: which branch is the target?"

    # answered; pass 2 re-enters at round 0 too, and this time the
    # measuring task genuinely fails — no question at all. The stale
    # needs_context row from pass 1 is still sitting in worker_sessions
    # at round 0, right alongside this new one.
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "failed")
    await database.write(lambda c: store.set_steer(c, wid, "use main"))
    steer = await database.write(lambda c: store.take_steer(c, wid))
    await database.write(lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"]))
    await database.write(lambda c: store.resume_work_item(c, wid, steer))
    result2 = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        policy=pol,
        start_index=0,  # "verify"
        steer=steer,
    )
    # A first-match scan would see the stale round-0 needs_context row
    # and stop again with the *same* question, forever. The real
    # outcome here is a cap breach (attempts=1, the agent never fixes
    # anything under status=failed) — a different needs_human, and
    # never needs_context.
    assert result2 == "needs_human"
    reason2 = _reason(database, wid)
    assert reason2 is not None
    assert not reason2.startswith("needs_context:")
    assert "exhausted" in reason2


async def test_the_answer_reaches_the_next_launch(tmp_path, monkeypatch, database, run_dirs, repo):
    """The steer text a human gives in answer to a needs_context stop leads the
    next agent launch's prompt, visible on the raw argv the child received."""
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_QUESTION", "which database should this target?")
    tracker = isolated_bd(tmp_path)
    answer = "use postgres, not sqlite"

    wid = await executor.intake(
        database,
        run_dirs,
        title="needs a decision",
        repo=str(repo),
        chain=_quick_task(tmp_path),
        bd_cwd=str(tracker),
    )
    result = await executor.run(
        database, run_dirs, work_item_id=wid, registry=None, bd_cwd=str(tracker)
    )
    assert result == "needs_human"
    assert _reason(database, wid) == "needs_context: which database should this target?"

    # the human answers; the next launch should complete normally.
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "done")
    await database.write(lambda c: store.set_steer(c, wid, answer))
    steer = await database.write(lambda c: store.take_steer(c, wid))
    await database.write(lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"]))
    await database.write(lambda c: store.resume_work_item(c, wid, steer))
    result2 = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        start_index=0,  # "implementation"
        steer=steer,
    )
    assert result2 == "completed"

    lines = argv_log.read_text().splitlines()
    assert any(answer in line for line in lines)


# ---------------------------------------------------------------------------
# API surface — /steer and /resume must accept a needs_human item stopped by
# needs_context, and keep 409ing everything else (a running item above all).
# ---------------------------------------------------------------------------


def _client(tmp_path, monkeypatch, *, templates_dir=None):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv(
        "KRAFT_TEMPLATES_DIR",
        str(templates_dir or fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))),
    )
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    return TestClient(api.app, client=("127.0.0.1", 54321))


def _wait(fn, what, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = fn()
        if got:
            return got
        time.sleep(0.15)
    raise AssertionError(f"timed out waiting for {what}")


def _wait_for_status(client, wid, status, timeout=30):
    return _wait(
        lambda: (lambda b: b if b["status"] == status else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        f"status == {status!r}",
        timeout=timeout,
    )


def _approve_retrying(client, wid, gate, timeout=30):
    """Approve a gate, retrying past a 409.

    An auto_escalate node's walk may still be inside its own auto-review agent
    call (spawn's AlreadyRunning refusal, Kraft-11e0) at the moment this gate's
    `pending_gate` first appears -- same idiom as test_api_gates.py's fix-loop
    test, which already retries `.../approve` past exactly this race.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.post(f"/api/work-items/{wid}/gates/{gate}/approve")
        if r.status_code == 200:
            return r
        time.sleep(0.15)
    raise AssertionError(f"timed out approving {gate!r}: last status {r.status_code}")


def test_steer_accepts_a_needs_context_stop(tmp_path, monkeypatch, repo):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which repo does this target?")
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "needs a decision", "chain_template": "quick-task"},
        ).json()["id"]
        _wait_for_status(client, wid, "needs_human")

        r = client.post(f"/api/work-items/{wid}/steer", json={"text": "use the fork"})
        assert r.status_code == 200
        assert r.json()["steer"] == "use the fork"


def test_resume_accepts_a_needs_context_stop(tmp_path, monkeypatch, repo):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which repo does this target?")
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "needs a decision", "chain_template": "quick-task"},
        ).json()["id"]
        _wait_for_status(client, wid, "needs_human")

        r = client.post(f"/api/work-items/{wid}/resume", json={"steer": "use the fork"})
        assert r.status_code == 200
        assert r.json()["steer"] == "use the fork"


def test_steer_still_409s_on_a_running_item(tmp_path, monkeypatch, repo):
    """The guard widened, it did not disappear: an item mid-run is not paused
    and did not stop for needs_context, so it stays refused."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "10")
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "busy", "chain_template": "quick-task"},
        ).json()["id"]
        _wait(
            lambda: next(
                (
                    s
                    for s in client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
                    if s["hook_point"] == "implementation.main.implement"
                    and s["status"] == "running"
                ),
                None,
            ),
            "a running agent session",
        )
        assert client.post(f"/api/work-items/{wid}/steer", json={"text": "x"}).status_code == 409
        assert client.post(f"/api/work-items/{wid}/resume", json={}).status_code == 409
        client.post(f"/api/work-items/{wid}/pause", json={})


def test_steer_and_resume_409_on_a_needs_human_stop_that_is_not_needs_context(
    tmp_path, monkeypatch, repo
):
    """The widened guard discriminates *within* needs_human by stop reason —
    only a needs_context stop may pass, everything else (a plain task failure
    here) still 409s. Also pins the sequence a reviewer traced by hand: stopped
    for needs_context -> answered -> ran on -> later stopped for a different
    reason. `_needs_context_stop` walks events newest-first, so it must find
    the later, non-needs_context stop and refuse — not the earlier one.

    The first stop is on `implementation` (needs_context); the second is on
    `verify`, where its real pytest run genuinely fails on calc.py's untouched
    bug. C1 (Kraft-s7c04.8) briefly moved the second stop to `implementation`
    by running `on.test.run` there directly; reverted 2026-09-16, so this is
    back to its original two-node sequence.

    quick-task's shape with a real suite at `verify`: the seeded fixture turns
    the changed-test-scope builtin into `true`, which could never fail."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which repo does this target?")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")  # leaves calc.py's bug in place throughout
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates / "chains" / "quick-task-pytest.yaml").write_text(
        "id: quick-task-pytest\n"
        "nodes:\n"
        "  - id: implementation\n"
        "    kind: exec\n"
        "    tasks:\n"
        "      - id: implement\n"
        "        extends: implementer\n"
        "  - id: verify\n"
        "    kind: exec\n"
        "    tasks:\n"
        "      - id: suite\n"
        "        kind: subprocess\n"
        f"        command: {sys.executable} -m pytest -q\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates) as client:
        wid = client.post(
            "/api/work-items",
            json={
                "repo": str(repo),
                "title": "needs a decision",
                "chain_template": "quick-task-pytest",
            },
        ).json()["id"]
        _wait_for_status(client, wid, "needs_human")
        assert client.get(f"/api/work-items/{wid}").json()["current_node_id"] == "implementation"

        # answered — resumes past the needs_context stop.
        monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "done")
        assert (
            client.post(f"/api/work-items/{wid}/resume", json={"steer": "use the fork"}).status_code
            == 200
        )

        # verify's real pytest run genuinely fails (calc.py's bug was never
        # touched — KRAFT_FAKE_CLAUDE=noop) — a different needs_human stop.
        _wait(
            lambda: (
                lambda b: (
                    b if b["status"] == "needs_human" and b["current_node_id"] == "verify" else None
                )
            )(client.get(f"/api/work-items/{wid}").json()),
            "verify to fail for real",
        )

        assert client.post(f"/api/work-items/{wid}/steer", json={"text": "x"}).status_code == 409
        assert client.post(f"/api/work-items/{wid}/resume", json={}).status_code == 409


def _await_gate(client, wid, gate, timeout=60):
    return _wait(
        lambda: client.get(f"/api/work-items/{wid}").json()["pending_gate"] == gate,
        f"pending gate {gate!r}",
        timeout=timeout,
    )


def test_a_gate_after_an_answered_needs_context_is_not_a_needs_context_stop(
    tmp_path, monkeypatch, repo
):
    """A pending gate also sets status 'needs_human' and appends no
    `work_item_needs_human`, so without an end boundary the answered-and-resumed
    needs_context stop still reads as live: the question keeps rendering (hiding
    the gate card, which for `human_review_approval` is the only approve
    affordance) and /steer + /resume answer a stop that is long over — /resume
    re-walking the gated node.

    V1's default chain runs its own spec and plan agents (the legacy fixture
    bound both hooks to a noop), so the question is switched on only once
    they are through: the node under test is `implementation`, and the gate
    after it is `local_review`."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which database should this target?")
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "needs a decision", "chain_template": "default"},
        ).json()["id"]
        _await_gate(client, wid, "spec_approval")
        _approve_retrying(client, wid, "spec_approval")
        _await_gate(client, wid, "plan_approval")
        monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
        _approve_retrying(client, wid, "plan_approval")

        # implementation asks its question and stops.
        _wait(
            lambda: (lambda b: b if b["needs_context_question"] else None)(
                client.get(f"/api/work-items/{wid}").json()
            ),
            "the needs_context question",
        )
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["needs_context_question"] == "which database should this target?"
        assert item["current_node_id"] == "implementation"

        # answered — the chain runs on to the next gate.
        monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "done")
        assert (
            client.post(f"/api/work-items/{wid}/resume", json={"steer": "the fork"}).status_code
            == 200
        )
        _await_gate(client, wid, "local_review")

        item = client.get(f"/api/work-items/{wid}").json()
        assert item["pending_gate"] == "local_review"
        assert item["needs_context_question"] is None
        assert client.post(f"/api/work-items/{wid}/steer", json={"text": "x"}).status_code == 409
        assert client.post(f"/api/work-items/{wid}/resume", json={}).status_code == 409


def test_retry_racing_resume_on_a_needs_context_stop_produces_one_winner(
    tmp_path, monkeypatch, repo
):
    """Kraft-11e0. A needs_context stop is `needs_human` and admits both
    `/retry` (always) and `/resume` (via `_needs_context_stop`) -- the one
    item state where the two doors' claimable statuses overlap."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which repo does this target?")
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "needs a decision", "chain_template": "quick-task"},
        ).json()["id"]
        _wait_for_status(client, wid, "needs_human")
        app = client.app

        async def scenario():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://kraft") as ac:
                return await asyncio.gather(
                    ac.post(f"/api/work-items/{wid}/resume", json={"steer": "use the fork"}),
                    ac.post(f"/api/work-items/{wid}/retry", json={}),
                )

        a, b = client.portal.call(scenario)
        assert sorted([a.status_code, b.status_code]) == [200, 409]
        types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
        assert types.count("work_item_resumed") + types.count("work_item_retried") == 1
