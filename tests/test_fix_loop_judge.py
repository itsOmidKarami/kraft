import asyncio
import json
import sys
from pathlib import Path

import pytest
from support.harness import isolated_bd, make_repo

from kraft import db, events, executor, policy, store
from kraft import findings as _findings
from kraft.executor import dispatch, prompts
from kraft.paths import RunDirs
from kraft.templates import Registry, Template

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


@pytest.mark.parametrize(
    "status,verdict,expected",
    [
        ("done", "continue", "continue"),
        ("done", "stop_needs_human", "stop_needs_human"),
        ("done_with_concerns", "stop_downgrade", "stop_downgrade"),
        ("done", "STOP EVERYTHING", "continue"),
        ("done", None, "continue"),
        ("failed", "stop_needs_human", "continue"),
        ("needs_context", "continue", "continue"),
    ],
)
def test_judge_result_resolution(status, verdict, expected):
    assert dispatch._judge_result(status, verdict) == expected


def test_format_judge_history_empty():
    assert prompts.format_judge_history([]) == "(no rounds measured yet)"


def test_format_judge_history_shows_the_trend_across_rounds():
    history = [
        {
            "round": 0,
            "findings": [_findings.Finding("critical", "boom", "a.py", 1, "p")],
            "fix_result_path": None,
        },
        {"round": 1, "findings": [], "fix_result_path": "/r1"},
    ]
    text = prompts.format_judge_history(history)
    assert "round 0" in text and "boom" in text
    assert "round 1: clean" in text
    assert "/r1" in text


def test_format_judge_history_labels_each_finding_by_source():
    """A recurring blind test failure (`source_plugin` = the hook name, via
    `from_blind_failure`) must read distinctly from a recurring review
    finding (`source_plugin` = the reviewer's own name) -- both are just
    fingerprinted lines in the same round otherwise, and the judge's whole
    job is telling "review findings narrowing" apart from "the test is still
    red"."""
    history = [
        {
            "round": 0,
            "findings": [
                _findings.Finding("critical", "boom", "a.py", 1, "code-review"),
                _findings.Finding("critical", "still red", None, None, "on.test.run"),
            ],
            "fix_result_path": None,
        },
    ]
    text = prompts.format_judge_history(history)
    assert "(code-review)" in text
    assert "(on.test.run)" in text


def test_judge_history_keeps_only_eligible_findings_with_their_fix_pointer(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id=None,
                    title="t",
                    repo="r",
                    chain_template="default",
                    chain_definition="{}",
                )
            )
            await database.write(
                lambda c: events.append(
                    c,
                    wid,
                    "findings_measured",
                    {
                        "node_id": "verify",
                        "cycle": 0,
                        "findings": [
                            {
                                "severity": "critical",
                                "message": "boom",
                                "file": "a.py",
                                "line": 1,
                                "source_plugin": "p",
                            },
                            {
                                "severity": "minor",
                                "message": "nit",
                                "file": "a.py",
                                "line": 2,
                                "source_plugin": "p",
                            },
                        ],
                    },
                )
            )
            await database.write(
                lambda c: events.append(
                    c,
                    wid,
                    "findings_measured",
                    {
                        "node_id": "verify",
                        "cycle": 1,
                        "findings": [
                            {
                                "severity": "critical",
                                "message": "boom",
                                "file": "a.py",
                                "line": 1,
                                "source_plugin": "p",
                            }
                        ],
                    },
                )
            )
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="fix1",
                    work_item_id=wid,
                    node_id="verify",
                    hook_point="on.implementation.start",
                    log_path="/l",
                    result_path="/r1",
                    round=1,
                )
            )
            history = dispatch.judge_history(
                database, wid, "verify", frozenset({"critical", "important"})
            )
            assert [h["round"] for h in history] == [0, 1]
            assert [f.message for f in history[0]["findings"]] == ["boom"]  # minor excluded
            assert history[0]["fix_result_path"] is None  # cycle 0 predates any fix
            assert history[1]["fix_result_path"] == "/r1"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_judge_verdict_fails_open_when_the_hook_is_not_registered(tmp_path):
    """A registry that predates this feature (or a hand-built test registry,
    Task 2's own fixtures among them) has no `dispatch.JUDGE_HOOK` entry at
    all -- this must never `KeyError`, only fail open, same as any other
    untrusted judge outcome."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id=None,
                    title="t",
                    repo="r",
                    chain_template="default",
                    chain_definition="{}",
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id=?", (wid,)).fetchone()
            )
            pol = policy.Policy(loops={}, default=policy.Cap(attempts=3, wall_clock_s=3600))
            verdict, reasoning = await dispatch.judge_verdict(
                database,
                rd,
                wid,
                {"id": "verify", "fix_loop": "verify_fix_loop"},
                row,
                Registry(hooks={}),
                rd.worktrees / wid,
                round=1,
                key="verify_fix_loop",
                eligible=[],
                policy=pol,
                launch=None,
                budget=policy.NO_BUDGET,
            )
            assert (verdict, reasoning) == ("continue", "")
        finally:
            await database.close()

    asyncio.run(scenario())


def _judge_template():
    return Template(
        id="judgeloop",
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


#: A measuring task that never resolves on its own -- only the cap or the
#: judge can stop this loop. Mirrors `test_fix_loop.py`'s `_CHECK_SCRIPT`
#: shape (a subprocess writing a `findings` result file), without its
#: marker-file "fix" branch: this loop is meant to keep finding the same
#: thing every round so the judge always has a real trend to react to.
#:
#: The `file` field increments a round counter each invocation, on purpose:
#: a truly identical finding (same file/message) across two consecutive
#: cycles trips the pre-existing "stuck" detector in `walk_node` -- unrelated
#: to the judge, and a real behavior this fixture would otherwise fight with
#: on every scenario below that needs more than one judged round. `message`
#: stays the literal "still broken" throughout -- that's what
#: `test_judge_stop_downgrade_does_not_downgrade_a_failing_task` asserts on.
_ALWAYS_FAILS_SCRIPT = (
    "import json, os, pathlib\n"
    "counter = pathlib.Path('.check_round')\n"
    "n = int(counter.read_text()) if counter.exists() else 0\n"
    "counter.write_text(str(n + 1))\n"
    "result_path = os.environ.get('KRAFT_RESULT_PATH')\n"
    "if result_path:\n"
    "    open(result_path, 'w').write(json.dumps({'status': 'failed', 'findings': ["
    "{'severity': 'important', 'message': 'still broken', 'file': f'a{n}.py', "
    "'line': 1, 'source_plugin': 'on.check'}]}))\n"
    "raise SystemExit(1)\n"
)

#: Same shape as `_ALWAYS_FAILS_SCRIPT` -- an incrementing `file` field so the
#: "stuck" detector never trips -- but the task *succeeds*: it reports a
#: real, eligible finding without ever failing. This is the only shape a
#: `stop_downgrade` verdict is allowed to end the loop on.
_SUCCEEDS_WITH_FINDING_SCRIPT = (
    "import json, os, pathlib\n"
    "counter = pathlib.Path('.check_round')\n"
    "n = int(counter.read_text()) if counter.exists() else 0\n"
    "counter.write_text(str(n + 1))\n"
    "result_path = os.environ.get('KRAFT_RESULT_PATH')\n"
    "if result_path:\n"
    "    open(result_path, 'w').write(json.dumps({'status': 'done', 'findings': ["
    "{'severity': 'important', 'message': 'still broken', 'file': f'a{n}.py', "
    "'line': 1, 'source_plugin': 'on.check'}]}))\n"
)


def _judge_registry(check_script=_ALWAYS_FAILS_SCRIPT):
    fake = f"{sys.executable} {_FAKE_AGENT}"
    return Registry(
        hooks={
            "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
            "on.check": {
                "kind": "subprocess",
                "command": [sys.executable, "-c", check_script],
            },
            "on.implementation.start": {"kind": "agent", "command": fake},
            dispatch.JUDGE_HOOK: {"kind": "agent", "command": fake},
        }
    )


def _judge_policy(tmp_path, *, attempts):
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"loops:\n  verify_fix_loop: {{ attempts: {attempts}, wall_clock_s: 3600 }}\n"
        f"default: {{ attempts: {attempts}, wall_clock_s: 3600 }}\n"
        "auto_escalate_stuck: false\n"
    )
    return policy.load_policy(p)


def _run_judge_loop(tmp_path, monkeypatch, plan, *, attempts, registry=None):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PLAN", str(plan_path))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            pol = _judge_policy(tmp_path, attempts=attempts)
            wid = await executor.intake(
                database,
                rd,
                title="judged loop",
                repo=str(repo),
                template=_judge_template(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry or _judge_registry(),
                bd_cwd=str(tracker),
                policy=pol,
            )
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id=?", (wid,)).fetchone()
            )
            return result, evts, row
        finally:
            await database.close()

    return asyncio.run(scenario())


def test_round_one_fixes_freely_no_judge_call(tmp_path, monkeypatch):
    """The very first fix cycle dispatches with no judge call at all
    (`previous_fix` is None on that pass) -- only the loop's *next* pass,
    after that fix has actually run, ever consults it (spec decision 2:
    "from round 2 on"). With a cap of 1, that next pass is also the one
    whose bump breaches the cap; the judge still gets asked on it (its own
    "continue" changes nothing here), so the cap remains the backstop."""
    result, evts, row = _run_judge_loop(tmp_path, monkeypatch, plan=[{}], attempts=1)
    types = [e["type"] for e in evts]
    first_fix = types.index("fix_cycle_started")
    assert "judge_verdict" not in types[: first_fix + 1]
    assert types.count("fix_cycle_started") == 1
    assert result == "needs_human"
    assert row["status"] == "needs_human"


def test_judge_continue_behaves_like_no_judge_present(tmp_path, monkeypatch):
    """Regression parity with `test_fix_loop.py::test_fix_loop_cap_breach`:
    a judge that always says continue changes nothing about when the loop
    caps out -- the cap is still the hard backstop."""
    plan = [
        {},  # fix cycle 1 (free)
        {"status": "done", "verdict": "continue", "concerns": "still shrinking"},
        {},  # fix cycle 2
        {"status": "done", "verdict": "continue", "concerns": "still shrinking"},
    ]
    result, evts, row = _run_judge_loop(tmp_path, monkeypatch, plan, attempts=2)
    types = [e["type"] for e in evts]
    assert result == "needs_human"
    assert row["status"] == "needs_human"
    assert types.count("fix_cycle_started") == 2
    judge_events = [e for e in evts if e["type"] == "judge_verdict"]
    assert len(judge_events) == 2
    assert all(e["payload"]["verdict"] == "continue" for e in judge_events)


def test_judge_stop_needs_human_preempts_the_cap(tmp_path, monkeypatch):
    plan = [
        {},  # fix cycle 1 (free)
        {"status": "done", "verdict": "stop_needs_human", "concerns": "recurring, not converging"},
    ]
    result, evts, row = _run_judge_loop(tmp_path, monkeypatch, plan, attempts=99)
    types = [e["type"] for e in evts]
    assert result == "needs_human"
    assert types.count("fix_cycle_started") == 1  # never reached a second cycle
    assert row["status"] == "needs_human"
    needs_human = next(e for e in evts if e["type"] == "work_item_needs_human")
    assert needs_human["payload"]["reason"] == "judge: recurring, not converging"
    assert "capped" not in needs_human["payload"]  # this is not a cap breach


def test_judge_stop_downgrade_exits_the_loop_clean(tmp_path, monkeypatch):
    plan = [
        {},  # fix cycle 1 (free)
        {"status": "done", "verdict": "stop_downgrade", "concerns": "real but not worth chasing"},
    ]
    result, evts, row = _run_judge_loop(
        tmp_path,
        monkeypatch,
        plan,
        attempts=99,
        registry=_judge_registry(_SUCCEEDS_WITH_FINDING_SCRIPT),
    )
    types = [e["type"] for e in evts]
    assert result == "completed"
    assert types.count("fix_cycle_started") == 1
    assert "work_item_needs_human" not in types
    downgrade = next(e for e in evts if e["type"] == "judge_verdict")
    assert downgrade["payload"]["verdict"] == "stop_downgrade"
    assert [f["message"] for f in downgrade["payload"]["findings"]] == ["still broken"]


def test_judge_stop_downgrade_does_not_downgrade_a_failing_task(tmp_path, monkeypatch):
    """`on.check` here fails every round (mirrors `on.ci.poll` on a red
    pipeline, which fails *and* reports one finding per broken job). A
    `stop_downgrade` verdict must not short-circuit that: downgrading the
    eligible findings would still leave a task that failed outright, which
    is not "as if there were no eligible findings" -- it's still failing.
    So this falls through to the ordinary cap machinery, same as `continue`
    would (mirrors `test_judge_continue_behaves_like_no_judge_present`)."""
    plan = [
        {},  # fix cycle 1 (free)
        {"status": "done", "verdict": "stop_downgrade", "concerns": "real but not worth chasing"},
        {},  # fix cycle 2
        {"status": "done", "verdict": "stop_downgrade", "concerns": "still not worth chasing"},
    ]
    result, evts, row = _run_judge_loop(tmp_path, monkeypatch, plan, attempts=2)
    types = [e["type"] for e in evts]
    assert result == "needs_human"
    assert row["status"] == "needs_human"
    assert types.count("fix_cycle_started") == 2
    downgrades = [e for e in evts if e["type"] == "judge_verdict"]
    assert len(downgrades) == 2
    assert all(e["payload"]["verdict"] == "stop_downgrade" for e in downgrades)


def test_judge_dispatch_failure_falls_open_to_continue(tmp_path, monkeypatch):
    """The judge session itself reports `status: failed` -- untrusted, so it
    falls open. The loop proceeds exactly as an un-judged loop would: the
    *cap*, not the judge, is what eventually stops it."""
    plan = [{}, {"status": "failed"}]
    result, evts, row = _run_judge_loop(tmp_path, monkeypatch, plan, attempts=1)
    types = [e["type"] for e in evts]
    assert result == "needs_human"
    assert types.count("fix_cycle_started") == 1  # cap=1 breaches on the next bump
    judge_events = [e for e in evts if e["type"] == "judge_verdict"]
    assert len(judge_events) == 1
    assert judge_events[0]["payload"]["verdict"] == "continue"
    needs_human = next(e for e in evts if e["type"] == "work_item_needs_human")
    assert needs_human["payload"]["reason"].startswith("verify_fix_loop exhausted")
    assert "capped" in needs_human["payload"]  # a genuine cap breach, not a judge stop


def test_retry_fixes_freely_no_judge_call_on_the_first_post_retry_cycle(tmp_path, monkeypatch):
    """`/retry` (`store.retry_after_cap`) deletes the loop counter but leaves
    the pre-retry `worker_sessions` rows in place, so `_previous_fix_session`
    (history-wide) is still non-None right after a retry. Gating the judge on
    that alone would call it before the steered post-retry cycle ever
    dispatches, discarding the human's steer on exactly the trend it retried
    to escape. Round 1 of *this* entry -- the loop counter having no row yet
    -- must fix freely, retry or not."""
    plan = [
        {},  # pre-retry fix cycle 1 (free)
        {"status": "done", "verdict": "continue", "concerns": "still shrinking"},
        {},  # pre-retry fix cycle 2
        {"status": "done", "verdict": "continue", "concerns": "still shrinking"},
        # cap (2) breaches here -- needs_human, no third pre-retry cycle
        {},  # post-retry fix cycle 1 -- must be free, no judge call
        {"status": "done", "verdict": "continue", "concerns": "still shrinking"},
        {},  # post-retry fix cycle 2
        {"status": "done", "verdict": "continue", "concerns": "still shrinking"},
        # cap (2) breaches again
    ]
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PLAN", str(plan_path))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            pol = _judge_policy(tmp_path, attempts=2)
            registry = _judge_registry()
            wid = await executor.intake(
                database,
                rd,
                title="judged retry",
                repo=str(repo),
                template=_judge_template(),
                bd_cwd=str(tracker),
            )
            first = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
            )
            assert first == "needs_human"  # pre-retry cap breach

            await database.write(
                lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"])
            )
            await database.write(
                lambda c: store.retry_after_cap(
                    c, wid, "verify", "verify_fix_loop", "steer toward the real cause"
                )
            )
            second = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                start_index=1,
            )
            assert second == "needs_human"  # post-retry cap breaches too

            evts = database.read(lambda c: events.read_after(c, 0, wid))
            return evts
        finally:
            await database.close()

    evts = asyncio.run(scenario())
    types = [e["type"] for e in evts]
    assert types.count("fix_cycle_started") == 4
    assert types.count("judge_verdict") == 4  # 2 pre-retry, 2 post-retry

    retry_idx = types.index("work_item_retried")
    post_retry_fix1 = types.index("fix_cycle_started", retry_idx)
    # The first post-retry fix cycle must dispatch with no judge call
    # in between the retry and it.
    assert "judge_verdict" not in types[retry_idx:post_retry_fix1]


def _measured(cycle: int, message: str) -> dict:
    return {
        "node_id": "verify",
        "cycle": cycle,
        "findings": [
            {
                "severity": "critical",
                "message": message,
                "file": "a.py",
                "line": 1,
                "source_plugin": "p",
            }
        ],
    }


def test_judge_history_keeps_both_entries_first_measurements_at_cycle_zero(tmp_path):
    """`walk_node` resets `round` to 0 on every re-entry while the loop counter
    persists, so two entries' first measurements collide on cycle 0. Keyed by
    that number, the older one vanished and the newest was relabelled the
    oldest — the judge's trend pointed backwards."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id=None,
                    title="t",
                    repo="r",
                    chain_template="default",
                    chain_definition="{}",
                )
            )
            # entry 1: cycle 0 then cycle 1; a repair pass; entry 2 re-enters at
            # cycle 0 again, then resumes from the counter at cycle 2.
            for payload in (
                _measured(0, "first entry, oldest"),
                _measured(1, "first entry, after one fix"),
                _measured(-1, "a repair pass, not a paid cycle"),
                _measured(0, "second entry, after the retry"),
                _measured(2, "second entry, newest"),
            ):
                await database.write(
                    lambda c, p=payload: events.append(c, wid, "findings_measured", p)
                )
            history = dispatch.judge_history(
                database, wid, "verify", frozenset({"critical", "important"})
            )
            assert [h["findings"][0].message for h in history] == [
                "first entry, oldest",
                "first entry, after one fix",
                "second entry, after the retry",
                "second entry, newest",
            ]
            # Renumbered by position, so "round 0" really is the oldest.
            assert [h["round"] for h in history] == [0, 1, 2, 3]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_needs_context_question_ignores_a_judge_session(tmp_path):
    """The judge must never be a second way to get stuck. `_judge_result` fails
    open in-process, but the session row persists — and `needs_context_question`
    deliberately scans every hook point, so without a filter the next re-entry
    strands the item on the judge's own question."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id=None,
                    title="t",
                    repo="r",
                    chain_template="default",
                    chain_definition="{}",
                )
            )
            result = tmp_path / "judge.json"
            result.write_text(json.dumps({"status": "needs_context", "question": "which cap?"}))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="judge1",
                    work_item_id=wid,
                    node_id="verify",
                    hook_point=dispatch.JUDGE_HOOK,
                    log_path="/l",
                    result_path=str(result),
                    round=0,
                )
            )
            await database.write(
                lambda c: store.session_exited(c, "judge1", "needs_context", question="which cap?")
            )
            node = {"id": "verify", "tasks": ["on.check"]}
            assert dispatch.needs_context_question(database, wid, node, 0) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def _seeded_judge_scenario(tmp_path, monkeypatch, *, steer):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps([{"status": "done", "verdict": "continue"}]))
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PLAN", str(plan_path))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _judge_registry()
            pol = _judge_policy(tmp_path, attempts=1)
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_judge_template(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id=?", (wid,)).fetchone()
            )
            wt = rd.worktrees / wid
            await database.write(lambda c: store.load_chain(c, wid, "env_setup"))
            env_node = {"id": "env_setup", "tasks": ["on.env.prepare"], "fix_loop": None}
            assert await executor.walk_node(database, rd, wid, env_node, row, registry, wt) == "ok"

            await database.write(lambda c: store.enter_node(c, wid, "verify"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="prior-fix",
                    work_item_id=wid,
                    node_id="verify",
                    hook_point="on.implementation.start",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_exited(c, "prior-fix", "done"))
            cap = policy.resolve_cap(pol, "verify_fix_loop")
            await database.write(lambda c: store.bump_counter(c, wid, "verify_fix_loop", cap))

            node = _judge_template().nodes[1]
            result = await executor.walk_node(
                database, rd, wid, node, row, registry, wt, policy=pol, steer=steer
            )
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]
            return result, types
        finally:
            await database.close()

    return asyncio.run(scenario())


def test_judge_fires_on_a_kraft_seeded_steer_at_the_re_entry_it_exists_to_brake(
    tmp_path, monkeypatch
):
    """Kraft-7sec's seeded retry steer must not defeat the fix-loop judge the
    way a genuine human steer is designed to -- only a human-authored steer
    gets `judge_due`'s free pass."""
    result, types = _seeded_judge_scenario(
        tmp_path,
        monkeypatch,
        steer=executor.Steer("findings the last review left unresolved", human=False),
    )
    assert result == "needs_human"  # cap (1) still breaches regardless
    assert "judge_verdict" in types


def test_human_steer_still_suppresses_the_judge_in_the_same_shape(tmp_path, monkeypatch):
    """The control for the test above: a human-typed steer in the identical
    pre-seeded shape still gets the free pass, unchanged."""
    result, types = _seeded_judge_scenario(
        tmp_path,
        monkeypatch,
        steer=executor.Steer("fix the timeout, not the retry logic", human=True),
    )
    assert result == "needs_human"  # cap (1) still breaches regardless
    assert "judge_verdict" not in types
