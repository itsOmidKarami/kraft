"""The fix loop reading findings rather than task names.

Backward compatibility is as much under test as the new behaviour: the
no-findings path must stay byte-identical, which `tests/test_fix_loop.py`
guards by staying green unedited.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, policy, store
from kraft.paths import RunDirs
from kraft.templates import Registry, Template

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE_REVIEWER = Path(__file__).parent / "support" / "fake_reviewer.py"


def _registry():
    """`on.review.local.run` becomes the scripted reviewer; everything else stands."""
    base = fake_registry(sys.executable, _FAKE_AGENT)
    hooks = dict(base.hooks)
    hooks["on.review.local.run"] = {
        "kind": "subprocess",
        "command": [sys.executable, str(_FAKE_REVIEWER)],
    }
    return Registry(hooks=hooks)


def _template() -> Template:
    """env_setup builds the worktree; `review` is the fix-loop node under test."""
    return Template(
        id="findings",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {
                "id": "review",
                "tasks": ["on.review.local.run"],
                "gate_after": None,
                "fix_loop": "verify_fix_loop",
            },
        ],
    )


def _policy(tmp_path, *, attempts=3, severities=None) -> policy.Policy:
    p = tmp_path / "policy.yaml"
    text = (
        f"loops:\n  verify_fix_loop: {{ attempts: {attempts}, wall_clock_s: 3600 }}\n"
        f"default: {{ attempts: {attempts}, wall_clock_s: 3600 }}\n"
        # Kraft-lpdd: this suite is about the findings loop's own cap, not
        # the unrelated auto-escalate trigger a `needs_human` cap breach
        # would otherwise also fire.
        "auto_escalate_stuck: false\n"
    )
    if severities is not None:
        text += f"findings:\n  loop_severities: {json.dumps(severities)}\n"
    p.write_text(text)
    return policy.load_policy(p)


def _finding(message, severity="critical", *, file="a.py", line=3):
    return {
        "severity": severity,
        "message": message,
        "file": file,
        "line": line,
        "source_plugin": "fake",
    }


def _run(tmp_path, monkeypatch, entries, *, attempts=3, severities=None, registry=None):
    """Drive one work item through the review node. Returns a dict of what happened.

    Each call gets its own scratch subdirectory rather than writing straight into
    `tmp_path`: `tmp_path` is per-test, not per-call, and `isolated_bd`/`make_repo`
    already refuse to `copytree` onto an existing "tracker"/"sample" dir, so a
    second `_run()` in the same test needs fresh paths anyway. That also keeps
    the fake reviewer's invocation-count sidecar (`plan_path.with_suffix(".count")`)
    from leaking between calls — without this, a second `_run()` would resume
    counting where the first left off and every cycle would read the wrong plan
    entry.
    """
    call_dir = Path(tempfile.mkdtemp(dir=tmp_path))
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    plan = call_dir / "review-plan.json"
    plan.write_text(json.dumps(entries))
    monkeypatch.setenv("KRAFT_FAKE_REVIEW_PLAN", str(plan))
    tracker = isolated_bd(call_dir)
    repo = make_repo(call_dir)
    out = {}

    async def scenario():
        rd = RunDirs(call_dir / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="review me",
                repo=str(repo),
                template=_template(),
                bd_cwd=str(tracker),
            )
            out["result"] = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry or _registry(),
                bd_cwd=str(tracker),
                policy=_policy(call_dir, attempts=attempts, severities=severities),
            )
            out["events"] = database.read(lambda c: events.read_after(c, 0, wid))
            out["sessions"] = database.read(
                lambda c: list(
                    c.execute("SELECT * FROM worker_sessions WHERE work_item_id = ?", (wid,))
                )
            )
            out["wid"] = wid
        finally:
            await database.close()

    asyncio.run(scenario())
    return out


def _cycles(out):
    return sum(e["type"] == "fix_cycle_started" for e in out["events"])


def _measured(out):
    return [e for e in out["events"] if e["type"] == "findings_measured"]


def _needs_human_reason(out):
    for e in reversed(out["events"]):
        if e["type"] == "work_item_needs_human":
            return e["payload"]["reason"]
    return None


def _noop_registry():
    """`on.review.local.run` back on the shipped noop binding, which is what any
    repo that has not bound a reviewer is actually running."""
    hooks = dict(_registry().hooks)
    hooks["on.review.local.run"] = {"kind": "builtin", "handler": "noop"}
    return Registry(hooks=hooks)


def test_findings_measured_names_the_tasks_that_were_noops(tmp_path, monkeypatch):
    """A noop task contributes no findings, exits 'done' in milliseconds, and is
    otherwise indistinguishable in this event from a review that ran and found
    nothing (Kraft-yenu part b)."""
    out = _run(tmp_path, monkeypatch, [{"status": "done"}], registry=_noop_registry())
    measured = _measured(out)
    assert measured, "no findings_measured event was written"
    assert measured[-1]["payload"]["noop_hooks"] == ["on.review.local.run"]


def test_findings_measured_reports_no_noops_when_every_task_ran(tmp_path, monkeypatch):
    """The empty case is explicit: readers get [] rather than a missing key, so
    nobody has to tell 'no noops' from 'old event, field did not exist yet'."""
    out = _run(tmp_path, monkeypatch, [{"status": "done"}])
    assert _measured(out)[-1]["payload"]["noop_hooks"] == []


def _broken_binary_registry():
    """`on.review.local.run` bound to a command that does not exist — the
    Kraft-579 incident in miniature."""
    hooks = dict(_registry().hooks)
    hooks["on.review.local.run"] = {
        "kind": "subprocess",
        "command": ["kraft-nonexistent-binary-xyz"],
    }
    return Registry(hooks=hooks)


def test_a_task_that_never_launched_stops_without_burning_a_cycle(tmp_path, monkeypatch):
    """The whole point of Kraft-579: no fix agent is dispatched and no attempt is
    spent, because no agent can install a missing binary by editing source. The
    incident this comes from spent six cycles and about an hour on exactly this."""
    out = _run(tmp_path, monkeypatch, [{"status": "done"}], registry=_broken_binary_registry())

    assert out["result"] == "needs_human"
    assert _cycles(out) == 0, "a config error must not open a fix cycle"
    reason = _needs_human_reason(out)
    assert "kraft-nonexistent-binary-xyz" in reason or "on.review.local.run" in reason
    assert "task failed" not in reason, "a launch failure must not read as a test failure"

    # and no fix agent was dispatched at it
    hooks = {s["hook_point"] for s in out["sessions"]}
    assert "on.implementation.start" not in hooks


def test_the_scripted_reviewer_drives_the_loop(tmp_path, monkeypatch):
    """Smoke test for the fixture itself: a failing cycle, then a clean one."""
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": [_finding("boom")]},
            {"status": "done", "findings": []},
        ],
    )
    assert out["result"] == "completed"
    assert _cycles(out) == 1


def test_run_called_twice_does_not_leak_the_invocation_counter(tmp_path, monkeypatch):
    """Guards the fake reviewer's invocation-count sidecar against leaking across
    `_run()` calls (see `_run`'s docstring). If the counter leaked, the second
    call's fake reviewer would resume at invocation 2 and immediately read its
    plan's last ("done") entry instead of its first ("failed") one, so the
    second call would see zero fix cycles instead of one."""
    entries = [
        {"status": "failed", "findings": [_finding("boom")]},
        {"status": "done", "findings": []},
    ]
    first = _run(tmp_path, monkeypatch, entries)
    assert first["result"] == "completed"
    assert _cycles(first) == 1

    second = _run(tmp_path, monkeypatch, entries)
    assert second["result"] == "completed"
    assert _cycles(second) == 1


def test_minor_only_findings_complete_the_node(tmp_path, monkeypatch):
    """A `minor` finding does not burn a cycle — it rolls up to the gate."""
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": [_finding("naming nit", "minor")]},
        ],
    )
    assert out["result"] == "completed"
    assert _cycles(out) == 0


def test_important_finding_enters_the_loop(tmp_path, monkeypatch):
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": [_finding("swallowed error", "important")]},
            {"status": "done", "findings": []},
        ],
    )
    assert out["result"] == "completed"
    assert _cycles(out) == 1


def test_important_finding_enters_the_loop_even_when_the_task_exits_done(tmp_path, monkeypatch):
    """A review skill can exit clean (status: done) and still report findings —
    that is the normal case, not the failed one every other test in this file
    drives. `verdict == "ok"` must not short-circuit past eligible findings."""
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "done", "findings": [_finding("swallowed error", "important")]},
            {"status": "done", "findings": []},
        ],
    )
    assert out["result"] == "completed"
    assert _cycles(out) == 1


def test_findings_only_cycle_does_not_tell_the_fix_agent_a_check_failed(tmp_path, monkeypatch):
    """`_FIX_PROMPT`'s "checks ... failed. Failing hook points: {failed}" is
    only true when a task actually failed. A findings-only cycle (task exits
    done) has no failed hook point to name — the fix agent must get findings
    framing instead, not a "failed" claim with a blank list after the colon."""
    log = tmp_path / "prompts.log"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(log))
    _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "done", "findings": [_finding("swallowed error", "important")]},
            {"status": "done", "findings": []},
        ],
    )
    prompts = [p for p in log.read_text().split("\n\x00\n") if p.strip()]
    assert "failed" not in prompts[0].lower()
    assert "Failing hook points:" not in prompts[0]
    assert "swallowed error" in prompts[0]


def test_failure_with_no_findings_still_enters_the_loop(tmp_path, monkeypatch):
    """A measuring task can fail without saying why; that is still non-clean."""
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": []},
            {"status": "done", "findings": []},
        ],
    )
    assert out["result"] == "completed"
    assert _cycles(out) == 1


def test_unchanged_findings_escalate_before_the_cap(tmp_path, monkeypatch):
    same = [_finding("unfixed")]
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": same},
            {"status": "failed", "findings": same},
            {"status": "failed", "findings": same},
        ],
        attempts=5,
    )
    assert out["result"] == "needs_human"
    assert _needs_human_reason(out).startswith("stuck:")
    assert _cycles(out) < 5  # escalated at cycle 1, well short of the cap


def test_no_progress_stop_attaches_a_diagnosis_bundle(tmp_path, monkeypatch):
    """Kraft-39ep: the stuck stop gathers a diagnosis a human would otherwise
    reconstruct by hand -- the worktree's own state, at minimum."""
    same = [_finding("unfixed")]
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": same},
            {"status": "failed", "findings": same},
            {"status": "failed", "findings": same},
        ],
        attempts=5,
    )
    payload = next(
        e["payload"] for e in reversed(out["events"]) if e["type"] == "work_item_needs_human"
    )
    assert "bundle" in payload
    assert "git_status" in payload["bundle"]
    assert "recent_log" in payload["bundle"]


def test_diagnosis_bundle_skips_the_judges_own_concerns(tmp_path, monkeypatch):
    """`_diagnosis_bundle`'s `last_session_concerns` documents the measuring
    session, not the judge that reasons about it -- JUDGE_PROMPT/SKILL.md
    require the judge to write concerns on every verdict too, and its
    worker_session_exited always lands after the measuring session's in the
    same walk_node iteration. worker_session_exited carries no hook_point of
    its own, so the fix has to join back to worker_sessions to tell the two
    apart."""
    from kraft.executor import dispatch, walk

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        repo = make_repo(tmp_path)
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id=None,
                    title="t",
                    repo=str(repo),
                    chain_template="default",
                    chain_definition="{}",
                )
            )

            async def exit_session(hook_point, session_id, concerns):
                await database.write(
                    lambda c: store.create_session(
                        c,
                        id=session_id,
                        work_item_id=wid,
                        node_id="verify",
                        hook_point=hook_point,
                        log_path=str(tmp_path / f"{session_id}.log"),
                        result_path=str(tmp_path / f"{session_id}.json"),
                    )
                )
                await database.write(
                    lambda c: store.session_exited(c, session_id, "done", concerns=concerns)
                )

            # The measuring session exits first, with its own concerns...
            await exit_session("on.check", "measure-1", "flaky under load")
            # ...then the judge, told to write concerns on every verdict,
            # exits after it in the same iteration.
            await exit_session(dispatch.JUDGE_HOOK, "judge-1", "judge's own reasoning")

            return await walk._diagnosis_bundle(database, wid, {"id": "verify"}, repo)
        finally:
            await database.close()

    bundle = asyncio.run(scenario())
    assert bundle["last_session_concerns"] == "flaky under load"


def test_line_movement_alone_is_not_progress(tmp_path, monkeypatch):
    """The fix edited the file and the finding shifted down. Still no progress."""
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": [_finding("unfixed", line=3)]},
            {"status": "failed", "findings": [_finding("unfixed", line=41)]},
        ],
        attempts=5,
    )
    assert out["result"] == "needs_human"
    assert _needs_human_reason(out).startswith("stuck:")


def test_a_new_finding_is_progress(tmp_path, monkeypatch):
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": [_finding("one")]},
            {"status": "failed", "findings": [_finding("two")]},
            {"status": "done", "findings": []},
        ],
    )
    assert out["result"] == "completed"
    assert _cycles(out) == 2  # both cycles ran; neither was mistaken for no-progress


def test_a_growing_set_is_progress(tmp_path, monkeypatch):
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": [_finding("one")]},
            {"status": "failed", "findings": [_finding("one"), _finding("two", file="b.py")]},
            {"status": "done", "findings": []},
        ],
    )
    assert out["result"] == "completed"
    assert _cycles(out) == 2  # both cycles ran; neither was mistaken for no-progress


def test_no_progress_does_not_mark_sessions_capped_out(tmp_path, monkeypatch):
    """The sessions did not cap out; only a real cap breach may claim they did."""
    same = [_finding("unfixed")]
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": same},
            {"status": "failed", "findings": same},
        ],
        attempts=5,
    )
    assert _needs_human_reason(out).startswith("stuck:")
    assert not any(s["status"] == "capped_out" for s in out["sessions"])


def test_findings_measured_carries_all_findings_but_eligible_fingerprints(tmp_path, monkeypatch):
    """`findings` is the record at every severity; `fingerprints` is what the
    no-progress comparison uses, so it must exclude the deferred severities."""
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {
                "status": "failed",
                "findings": [
                    _finding("nit", "minor"),
                    _finding("real", "important"),
                ],
            },
            {"status": "done", "findings": []},
        ],
    )
    first = _measured(out)[0]["payload"]
    assert first["cycle"] == 0
    assert len(first["findings"]) == 2
    assert len(first["fingerprints"]) == 1


def test_the_fix_prompt_names_findings_and_marks_repeats(tmp_path, monkeypatch):
    """Cycle 1's prompt must mark the finding it already tried and failed to fix.

    A growing set, not a repeated one: an unchanged set escalates instead of
    dispatching a second fix, so REPEAT is only reachable when something else
    changed too.
    """
    log = tmp_path / "prompts.log"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(log))
    _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": [_finding("sticky thing")]},
            {
                "status": "failed",
                "findings": [
                    _finding("sticky thing"),
                    _finding("new thing", file="b.py"),
                ],
            },
            {"status": "done", "findings": []},
        ],
    )
    # The fix-loop judge shares this same prompt log (it's dispatched through
    # the same fake agent) and is asked once between these two fix cycles --
    # filtered out here since this test is about the *fix* agent's prompt,
    # not the judge's.
    prompts = [
        p
        for p in log.read_text().split("\n\x00\n")
        if p.strip() and "is about to spend another cycle" not in p
    ]
    assert "sticky thing" in prompts[0]
    assert "a.py:3" in prompts[0]
    assert "REPEAT" not in prompts[0]
    assert "REPEAT" in prompts[1]


def test_minor_only_findings_burn_a_cycle_when_configured(tmp_path, monkeypatch):
    """Mirror of test_minor_only_findings_complete_the_node: with `minor` in
    loop_severities, a minor-only finding does burn a fix cycle. This is what
    makes Task 2's "[critical, important, minor] restores today's behaviour"
    claim real, rather than merely asserted."""
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": [_finding("naming nit", "minor")]},
            {"status": "done", "findings": []},
        ],
        severities=["critical", "important", "minor"],
    )
    assert out["result"] == "completed"
    assert _cycles(out) == 1


def test_fix_sessions_result_is_excluded_from_the_next_cycles_findings(tmp_path, monkeypatch):
    """`_collect_findings` filters to the node's own measuring tasks. Pin it: the
    fake fix agent reports a finding of its own at the same round the next
    measuring pass runs at, and that finding must never appear in any
    `findings_measured` payload — it belongs to `on.implementation.start`, not
    to `on.review.local.run`. Without the hook_point filter this test is silent
    (the fake fix agent normally never writes a `findings` key at all), which is
    exactly the gap KRAFT_FAKE_AGENT_FINDING closes."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT_FINDING", "agent noise")
    out = _run(
        tmp_path,
        monkeypatch,
        [
            {"status": "failed", "findings": [_finding("real bug")]},
            {"status": "done", "findings": []},
        ],
    )
    assert out["result"] == "completed"
    all_messages = [f["message"] for e in _measured(out) for f in e["payload"]["findings"]]
    assert "agent noise" not in all_messages


def test_retry_after_no_progress_dispatches_a_fix_instead_of_re_escalating(tmp_path, monkeypatch):
    """`POST /retry` clears the loop counter and re-runs `executor.run` from the
    node (`store.retry_after_cap` + `start_index`), which re-enters `_walk_node`
    at round 0 and measures *before* it fixes. If the no-progress comparison did
    not require a `fix_cycle_started` between the two measurements it compares,
    that first re-measurement — on code nothing has touched yet — would report
    the same fingerprints as the escalating cycle and bounce straight back to
    needs_human, and the steered retry would never reach a fix agent."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    same = [_finding("unfixed")]
    call_dir = Path(tempfile.mkdtemp(dir=tmp_path))
    plan = call_dir / "review-plan.json"
    # Two entries: cycle 0 fails, cycle 1's re-measurement (still unfixed code)
    # reports the identical finding and escalates before the cap (5 attempts).
    # A third invocation (the retry's first re-measurement) clamps to the last
    # entry and reports the same finding again — the exact no-progress shape.
    plan.write_text(json.dumps([{"status": "failed", "findings": same}] * 2))
    monkeypatch.setenv("KRAFT_FAKE_REVIEW_PLAN", str(plan))
    tracker = isolated_bd(call_dir)
    repo = make_repo(call_dir)

    async def scenario():
        rd = RunDirs(call_dir / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _registry()
            pol = _policy(call_dir, attempts=5)
            wid = await executor.intake(
                database,
                rd,
                title="review me",
                repo=str(repo),
                template=_template(),
                bd_cwd=str(tracker),
            )
            first = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker), policy=pol
            )
            assert first == "needs_human"
            events_after_first = database.read(lambda c: events.read_after(c, 0, wid))
            assert _needs_human_reason({"events": events_after_first}).startswith("stuck:")

            await database.write(
                lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"])
            )
            await database.write(
                lambda c: store.retry_after_cap(c, wid, "review", "verify_fix_loop", None)
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                start_index=1,
            )
            return database.read(lambda c: events.read_after(c, 0, wid))
        finally:
            await database.close()

    all_events = asyncio.run(scenario())
    retried_seq = next(e["seq"] for e in all_events if e["type"] == "work_item_retried")
    post_retry_cycles = sum(
        e["type"] == "fix_cycle_started" and e["seq"] > retried_seq for e in all_events
    )
    assert post_retry_cycles >= 1


_FAILING_SUBPROCESS = (
    "import sys; "
    "print('\\u2718 1 e2e/x.spec.ts:1:1 \\u203a a flaky test (2.0m)'); "
    "print('Error: locator.click: Test timeout of 120000ms exceeded.'); "
    "sys.exit(1)"
)


def _blind_failure_registry():
    """`on.check` is a real subprocess that fails with no result file at all --
    the genuinely blind case `on.review.local.run`'s scripted reviewer (which
    always writes a result file, even an empty-findings one) can't exercise."""
    hooks = dict(_registry().hooks)
    hooks["on.check"] = {
        "kind": "subprocess",
        "command": [sys.executable, "-c", _FAILING_SUBPROCESS],
    }
    return Registry(hooks=hooks)


def _blind_failure_template() -> Template:
    return Template(
        id="blind",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {
                "id": "verify",
                "tasks": ["on.check"],
                "gate_after": None,
                "fix_loop": "verify_fix_loop",
            },
        ],
    )


def _run_template(tmp_path, monkeypatch, template, registry, *, attempts=3):
    """Like `_run`, but for a caller-supplied template/registry instead of the
    scripted-reviewer `review` node -- the blind-failure tests need a real
    subprocess, not the fake reviewer's plan file."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    out = {}

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="blind failure",
                repo=str(repo),
                template=template,
                bd_cwd=str(tracker),
            )
            out["result"] = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=_policy(tmp_path, attempts=attempts),
            )
            out["events"] = database.read(lambda c: events.read_after(c, 0, wid))
            out["wid"] = wid
        finally:
            await database.close()

    asyncio.run(scenario())
    return out


def test_blind_subprocess_failure_becomes_a_synthetic_finding(tmp_path, monkeypatch):
    out = _run_template(
        tmp_path, monkeypatch, _blind_failure_template(), _blind_failure_registry(), attempts=2
    )
    measured = _measured(out)
    assert measured, "no findings_measured event was written"
    findings = measured[0]["payload"]["findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f["source_plugin"] == "on.check"
    assert f["severity"] == "critical"
    assert "e2e/x.spec.ts:1:1" in f["message"]
    assert "Test timeout" in f["message"]


def test_blind_subprocess_failure_fingerprint_is_stable_across_rounds(tmp_path, monkeypatch):
    """The regression this whole feature exists to fix: an identical blind
    failure recurring for several rounds must now trip the stuck-detector, the
    same way a recurring review finding already does."""
    out = _run_template(
        tmp_path,
        monkeypatch,
        _blind_failure_template(),
        _blind_failure_registry(),
        attempts=5,
    )
    assert out["result"] == "needs_human"
    assert _needs_human_reason(out).startswith("stuck:")


def test_blind_failure_findings_are_labeled_by_source_in_judge_history(tmp_path, monkeypatch):
    from kraft.executor import dispatch as _dispatch

    out = _run_template(
        tmp_path, monkeypatch, _blind_failure_template(), _blind_failure_registry(), attempts=2
    )

    async def read_history():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            return _dispatch.judge_history(
                database, out["wid"], "verify", frozenset({"critical", "important"})
            )
        finally:
            await database.close()

    # judge_history reconstructs from the same on-disk DB `_run_template` just
    # closed -- reopen it read-only for this assertion rather than threading a
    # second handle through `_run_template`.
    history = asyncio.run(read_history())
    assert history, "no rounds recorded"
    assert any(f.source_plugin == "on.check" for h in history for f in h["findings"])
