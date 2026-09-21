import asyncio
import json
import shlex
import sys
from pathlib import Path

import pytest
from support.chain_run import loop_policy, run_chain
from support.harness import (
    isolated_bd,
    make_repo,
    v1_fix_loop_node,
    v1_resolved,
    v1_seeded_chain,
)

from kraft import db, events, executor, policy, store
from kraft import findings as _findings
from kraft.executor import dispatch, prompts
from kraft.paths import RunDirs

_FAKE_AGENT = Path(__file__).resolve().parents[1] / "support" / "fake_agent.py"
_FAKE = f"{sys.executable} {_FAKE_AGENT}"


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


def test_stuck_fingerprint_none_below_the_limit():
    """Two rounds of the same fingerprint, each separated by a fix -- one
    short of the default limit of 3 -- must not read as stuck."""
    history = [
        {
            "round": 0,
            "findings": [_findings.Finding("critical", "boom", "a.py", 1, "p")],
            "fix_result_path": "/r0",
        },
        {
            "round": 1,
            "findings": [_findings.Finding("critical", "boom", "a.py", 1, "p")],
            "fix_result_path": "/r1",
        },
    ]
    assert dispatch.stuck_fingerprint(history, min_repeats=3) is None


def test_stuck_fingerprint_found_once_it_survives_enough_fixes():
    """The same fingerprint surviving 3 fix attempts is stuck, even though a
    sibling finding changes shape every round (Kraft-0i6z4)."""
    persistent = _findings.Finding("critical", "still red", None, None, "on.test.run")
    history = [
        {
            "round": 0,
            "findings": [persistent, _findings.Finding("critical", "one", "a.py", 1, "p")],
            "fix_result_path": "/r0",
        },
        {
            "round": 1,
            "findings": [persistent, _findings.Finding("critical", "two", "b.py", 1, "p")],
            "fix_result_path": "/r1",
        },
        {
            "round": 2,
            "findings": [persistent, _findings.Finding("critical", "three", "c.py", 1, "p")],
            "fix_result_path": "/r2",
        },
    ]
    assert dispatch.stuck_fingerprint(history, min_repeats=3) == persistent.fingerprint


def test_stuck_fingerprint_streak_breaks_when_the_finding_disappears():
    """A fingerprint absent from one round resets its streak -- resolving and
    then recurring later starts counting from 1 again, not from where it left
    off."""
    finding = _findings.Finding("critical", "boom", "a.py", 1, "p")
    history = [
        {"round": 0, "findings": [finding], "fix_result_path": "/r0"},
        {"round": 1, "findings": [finding], "fix_result_path": "/r1"},
        {"round": 2, "findings": [], "fix_result_path": "/r2"},
        {"round": 3, "findings": [finding], "fix_result_path": "/r3"},
    ]
    assert dispatch.stuck_fingerprint(history, min_repeats=3) is None


def test_stuck_fingerprint_requires_a_fix_between_rounds():
    """Two measurements of the same fingerprint with no fix session between
    them (e.g. a crash/resume re-measuring before fixing) must not count as a
    streak -- the finding never had a chance to change."""
    finding = _findings.Finding("critical", "boom", "a.py", 1, "p")
    history = [
        {"round": 0, "findings": [finding], "fix_result_path": None},
        {"round": 1, "findings": [finding], "fix_result_path": None},
        {"round": 2, "findings": [finding], "fix_result_path": None},
    ]
    assert dispatch.stuck_fingerprint(history, min_repeats=3) is None


async def test_judge_history_keeps_only_eligible_findings_with_their_fix_pointer(database):
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
            hook_point="verify.fix_loop.main.fix",
            log_path="/l",
            result_path="/r1",
            round=1,
        )
    )
    history = dispatch.judge_history(
        database,
        wid,
        "verify",
        frozenset({"critical", "important"}),
        fix_paths=["verify.fix_loop.main.fix"],
    )
    assert [h["round"] for h in history] == [0, 1]
    assert [f.message for f in history[0]["findings"]] == ["boom"]  # minor excluded
    assert history[0]["fix_result_path"] is None  # cycle 0 predates any fix
    assert history[1]["fix_result_path"] == "/r1"


async def test_judge_verdict_fails_open_when_the_hook_is_not_registered(database, run_dirs):
    """A fix loop with no judge at all (`fix-loop-judge-is-optional`) must
    never raise, only fail open, same as any other untrusted judge outcome."""

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
    node = v1_resolved(
        [v1_fix_loop_node("verify", _check(_ALWAYS_FAILS_SCRIPT), judge=False)]
    ).nodes[0]
    verdict, reasoning = await dispatch.judge_verdict(
        database,
        run_dirs,
        wid,
        node,
        row,
        run_dirs.worktrees / wid,
        round=1,
        key="verify.fix_loop",
        cap=pol.default,
        policy=pol,
        launch=None,
        budget=policy.NO_BUDGET,
    )
    assert (verdict, reasoning) == ("continue", "")


def _check(script: str) -> dict:
    return {
        "id": "check",
        "kind": "subprocess",
        "command": shlex.join([sys.executable, "-c", script]),
    }


def _judge_template(tmp_path, check_script=None):
    """`verify` measured by `check_script`, with a fix loop and judge on the
    fake agent. No `env_setup` node: V1 prepares the worktree first."""
    node = v1_fix_loop_node("verify", _check(check_script or _ALWAYS_FAILS_SCRIPT))
    return v1_seeded_chain(tmp_path / "templates", [node], agent_command=_FAKE)


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


def _run_judge_loop(tmp_path, monkeypatch, plan, *, attempts, check_script=None, prompt_log=None):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PLAN", str(plan_path))
    if prompt_log is not None:
        monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompt_log))
    out = run_chain(
        tmp_path,
        _judge_template(tmp_path, check_script),
        policy=loop_policy(tmp_path, "verify.fix_loop", attempts=attempts),
        title="judged loop",
    )
    return out["result"], out["events"], out["row"]


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
        check_script=_SUCCEEDS_WITH_FINDING_SCRIPT,
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
    assert needs_human["payload"]["reason"].startswith("verify.fix_loop exhausted")
    assert "capped" in needs_human["payload"]  # a genuine cap breach, not a judge stop


async def test_retry_fixes_freely_no_judge_call_on_the_first_post_retry_cycle(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """`/retry` (`store.retry_after_cap`) deletes the loop counter but leaves
    the pre-retry `worker_sessions` rows in place, so `previous_fix_session`
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

    pol = loop_policy(tmp_path, "verify.fix_loop", attempts=2)
    wid = await executor.intake(
        database,
        run_dirs,
        title="judged retry",
        repo=str(repo),
        chain=_judge_template(tmp_path),
        bd_cwd=str(tracker),
    )
    first = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        policy=pol,
    )
    assert first == "needs_human"  # pre-retry cap breach

    await database.write(lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"]))
    await database.write(
        lambda c: store.retry_after_cap(
            c, wid, "verify", "verify.fix_loop", "steer toward the real cause"
        )
    )
    second = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        policy=pol,
        start_index=0,
    )
    assert second == "needs_human"  # post-retry cap breaches too

    evts = database.read(lambda c: events.read_after(c, 0, wid))
    evts = evts
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


async def test_judge_history_keeps_both_entries_first_measurements_at_cycle_zero(database):
    """`walk_node` resets `round` to 0 on every re-entry while the loop counter
    persists, so two entries' first measurements collide on cycle 0. Keyed by
    that number, the older one vanished and the newest was relabelled the
    oldest — the judge's trend pointed backwards."""

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
        await database.write(lambda c, p=payload: events.append(c, wid, "findings_measured", p))
    history = dispatch.judge_history(database, wid, "verify", frozenset({"critical", "important"}))
    assert [h["findings"][0].message for h in history] == [
        "first entry, oldest",
        "first entry, after one fix",
        "second entry, after the retry",
        "second entry, newest",
    ]
    # Renumbered by position, so "round 0" really is the oldest.
    assert [h["round"] for h in history] == [0, 1, 2, 3]


async def test_needs_context_question_ignores_a_judge_session(tmp_path, database):
    """The judge must never be a second way to get stuck. `_judge_result` fails
    open in-process, but the session row persists — and `needs_context_question`
    deliberately scans every hook point, so without a filter the next re-entry
    strands the item on the judge's own question."""

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
    node = v1_resolved([v1_fix_loop_node("verify", _check(_ALWAYS_FAILS_SCRIPT))]).nodes[0]
    result = tmp_path / "judge.json"
    result.write_text(json.dumps({"status": "needs_context", "question": "which cap?"}))
    await database.write(
        lambda c: store.create_session(
            c,
            id="judge1",
            work_item_id=wid,
            node_id="verify",
            hook_point=node.judge.path,
            log_path="/l",
            result_path=str(result),
            round=0,
        )
    )
    await database.write(
        lambda c: store.session_exited(c, "judge1", "needs_context", question="which cap?")
    )
    assert dispatch.needs_context_question(database, wid, node, 0) is None


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
            pol = loop_policy(tmp_path, "verify.fix_loop", attempts=1)
            chain = _judge_template(tmp_path)
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                chain=chain,
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id=?", (wid,)).fetchone()
            )
            # The worktree the loop runs in, cut for real -- the preparation V1
            # runs before the first node.
            from kraft import builtins

            wt = await builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id=wid, repo_entry=None
            )
            await database.write(lambda c: store.load_chain(c, wid, "verify"))

            await database.write(lambda c: store.enter_node(c, wid, "verify"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="prior-fix",
                    work_item_id=wid,
                    node_id="verify",
                    hook_point="verify.fix_loop.main.fix",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_exited(c, "prior-fix", "done"))
            cap = policy.resolve_cap(pol, "verify.fix_loop")
            await database.write(lambda c: store.bump_counter(c, wid, "verify.fix_loop", cap))

            node = chain.nodes[0]
            result = await executor.walk_node(
                database, rd, wid, node, row, wt, policy=pol, steer=steer
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
        steer=executor.Steer("findings the last review left unresolved", source="seeded"),
    )
    assert result == "needs_human"  # cap (1) still breaches regardless
    assert "judge_verdict" in types


def test_human_steer_still_suppresses_the_judge_in_the_same_shape(tmp_path, monkeypatch):
    """The control for the test above: a human-typed steer in the identical
    pre-seeded shape still gets the free pass, unchanged."""
    result, types = _seeded_judge_scenario(
        tmp_path,
        monkeypatch,
        steer=executor.Steer("fix the timeout, not the retry logic", source="human"),
    )
    assert result == "needs_human"  # cap (1) still breaches regardless
    assert "judge_verdict" not in types


def _prompts(log):
    return [p for p in log.read_text().split("\n\x00\n") if p.strip()]


def test_a_continue_verdicts_reasoning_reaches_the_next_fix_task(tmp_path, monkeypatch):
    """Kraft-s7c04.5. On e983d85c the judge correctly diagnosed "you are chasing
    variants, root-cause this" TWICE and the loop restarted unchanged both
    times, at $31.65. This is the cheapest signal in the system -- already
    computed, already paid for, already correct -- and no prompt read it."""
    log = tmp_path / "prompts.txt"
    _run_judge_loop(
        tmp_path,
        monkeypatch,
        plan=[{"verdict": "continue", "concerns": "you are chasing variants; root-cause it"}],
        attempts=3,
        prompt_log=log,
    )
    fixes = [p for p in _prompts(log) if "Fix the code" in p]
    assert fixes, "no fix task was dispatched at all"
    assert any("you are chasing variants; root-cause it" in p for p in fixes)


def test_the_first_fix_carries_no_judge_note(tmp_path, monkeypatch):
    """Round 1 fixes freely with no judge call, so there is no reasoning to
    carry and nothing must claim otherwise."""
    log = tmp_path / "prompts.txt"
    _run_judge_loop(
        tmp_path,
        monkeypatch,
        plan=[{"verdict": "continue", "concerns": "later rounds only"}],
        attempts=1,
        prompt_log=log,
    )
    fixes = [p for p in _prompts(log) if "Fix the code" in p]
    assert len(fixes) == 1
    assert "The fix-loop judge reviewed the trend" not in fixes[0]
