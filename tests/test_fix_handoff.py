"""Sub-project G §4: fix cycle N > 1 carries cycle N-1's own result forward.

Follows `tests/test_fix_loop.py`'s shape (no fixtures, plain `async def
scenario(): ...` under `asyncio.run`) rather than editing that file or
`tests/test_findings_loop.py`, both of which stay green and unedited.
"""

import json
import shlex
import sys
from pathlib import Path

import pytest
from support.chain_run import loop_policy, run_chain
from support.harness import isolated_bd, v1_fix_loop_node, v1_seeded_chain

from kraft import executor, policy, store
from kraft.adapters.subprocess import read_concerns

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE = f"{sys.executable} {_FAKE_AGENT}"
#: The fix loop's one task, where its sessions land.
_FIX = "verify.fix_loop.main.fix"

#: A "never fixed" `on.test.run` whose failure text differs every invocation
#: (via a `KRAFT_FAKE_TESTRUN_COUNTER` sidecar, same pattern as
#: `tests/support/fake_reviewer.py`'s own invocation counter). This suite's
#: whole point is exercising multiple fix cycles' prompt handoff; a *real*
#: pytest run against the sample repo now fails with byte-identical output
#: every round (`findings.from_blind_failure`, Kraft), so with a noop fix
#: agent the identical fingerprint recurring at round 1 correctly trips the
#: stuck-detector before a second cycle ever dispatches -- exactly the
#: behaviour this whole feature exists to add. That's real progress, and
#: also incompatible with this file's fixture: it needs the loop to keep
#: cycling, so its failure must keep changing shape, never converging.
_VARYING_FAILING_SUBPROCESS = (
    "import os, pathlib, sys; "
    "counter = pathlib.Path(os.environ['KRAFT_FAKE_TESTRUN_COUNTER']); "
    "n = int(counter.read_text()) if counter.exists() else 0; "
    "counter.write_text(str(n + 1)); "
    "print(f'FAILED tests/test_calc.py::test_add - AssertionError: attempt {n}'); "
    "sys.exit(1)"
)


def _fixloop_template(tmp_path, *, fix_model: str | None = None):
    """`verify` measured by the never-fixed varying subprocess, with the fix
    loop on the fake agent.

    No fix-loop judge in this suite. Bound to the same fake agent as the fix
    task it would write the same `KRAFT_FAKE_AGENT_CONCERNS` text, which
    `prompts.judge_note` carries into the next fix prompt (Kraft-s7c04.5) --
    so the marker `test_the_path_is_passed_not_the_contents` looks for would
    become ambiguous between "the previous fix's contents were pasted" (the
    bug it guards) and "the judge said the same words" (fine). The judge's
    own handoff is covered by tests/skills/test_fix_loop_judge.py. No
    `env_setup` node: V1 prepares the worktree first."""
    suite = {
        "id": "suite",
        "kind": "subprocess",
        "command": shlex.join([sys.executable, "-c", _VARYING_FAILING_SUBPROCESS]),
    }
    node = v1_fix_loop_node("verify", suite, judge=False)
    if fix_model is not None:
        node["fix_loop"]["tasks"][0]["model"] = fix_model
    return v1_seeded_chain(tmp_path / "templates", [node], agent_command=_FAKE)


def _run(tmp_path, monkeypatch, prompts_path):
    """Drive a fix loop that never fixes the code, so it runs multiple cycles.

    Returns the fix task's worker_sessions rows, ordered by round.
    """
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")  # never patches calc.py
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts_path))
    monkeypatch.setenv("KRAFT_FAKE_TESTRUN_COUNTER", str(tmp_path / "attempt.count"))
    out = run_chain(
        tmp_path,
        _fixloop_template(tmp_path),
        policy=loop_policy(tmp_path, "verify.fix_loop", attempts=5),
        title="never fixed",
    )
    assert out["result"] == "needs_human"  # cap breaches; irrelevant to what's asserted below
    return sorted((s for s in out["sessions"] if s["hook_point"] == _FIX), key=lambda s: s["round"])


def _sent_prompts(prompts_path):
    """Fix-agent prompts: the suite's only agent task, since it declares no
    judge."""
    return [p for p in prompts_path.read_text().split("\n\x00\n") if p.strip()]


def test_fix_cycle_two_names_the_previous_result_file(tmp_path, monkeypatch):
    """Cycle 2's prompt names cycle 1's own fix session by path -- not cycle 1's
    re-measure session, which `store.sessions_for_round` returns at the same
    round and which must be filtered out in code."""
    prompts_path = tmp_path / "prompts.txt"
    fix_sessions = _run(tmp_path, monkeypatch, prompts_path)
    assert len(fix_sessions) >= 2  # cap=5 guarantees at least two fix cycles ran

    sent = _sent_prompts(prompts_path)
    cycle1_result_path = fix_sessions[0]["result_path"]

    # Do NOT assert ".json" not in sent[0] as a proxy for "no prior file named":
    # _FIX_FINDINGS can legitimately contain a .json path for unrelated reasons.
    assert cycle1_result_path not in sent[0]
    assert cycle1_result_path in sent[1]


def test_fix_cycle_two_names_the_previous_session_summary(tmp_path, monkeypatch):
    """Spec §4 asks for both the result path and the session summary ref, when
    the previous fix task was an agent (it always is, here)."""
    prompts_path = tmp_path / "prompts.txt"
    fix_sessions = _run(tmp_path, monkeypatch, prompts_path)
    assert len(fix_sessions) >= 2

    sent = _sent_prompts(prompts_path)
    cycle1_summary_ref = fix_sessions[0]["session_summary_ref"]
    assert cycle1_summary_ref  # the fake agent always writes one
    assert cycle1_summary_ref not in sent[0]
    assert cycle1_summary_ref in sent[1]


def test_the_path_is_passed_not_the_contents(tmp_path, monkeypatch):
    """The handoff hands over the path, never the file's content -- pasting it
    costs tokens on every cycle of every work item, and the agent can already
    read the file itself."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "done_with_concerns")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_CONCERNS", "MARKER-PREVIOUS-CONCERNS-TEXT")
    prompts_path = tmp_path / "prompts.txt"
    fix_sessions = _run(tmp_path, monkeypatch, prompts_path)
    assert len(fix_sessions) >= 2

    # Sanity: the marker really is on disk in cycle 1's result file, so a
    # trivially-true assertion below (nothing ever wrote it) is ruled out.
    assert read_concerns(Path(fix_sessions[0]["result_path"])) == "MARKER-PREVIOUS-CONCERNS-TEXT"

    sent = _sent_prompts(prompts_path)
    assert "MARKER-PREVIOUS-CONCERNS-TEXT" not in sent[1]
    assert fix_sessions[0]["result_path"] in sent[1]


# --- round-independence: retry and resume ------------------------------------
#
# `previous_fix_session` must not trust `_walk_node`'s local `round` counter,
# which resets to 0 on every fresh entry into that function while the
# persisted `worker_sessions` rows from before the re-entry are still there.
# The tests above never re-enter `_walk_node` (one uninterrupted `executor.run`
# call), so they cannot exercise either failure mode.


async def test_fix_cycle_names_the_post_retry_attempt_not_the_abandoned_one(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """`/retry` (`store.retry_after_cap`) deletes the loop counter and starts a
    fresh budget, so post-retry round numbers collide with the abandoned
    pre-retry attempt's. The second post-retry cycle must name the first
    post-retry cycle's result -- never the abandoned attempt sharing its round
    number, which a round-keyed, first-match lookup would return instead
    (it was created first)."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")  # never fixes calc.py, either side of the retry
    prompts_path = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts_path))
    monkeypatch.setenv("KRAFT_FAKE_TESTRUN_COUNTER", str(tmp_path / "attempt.count"))
    tracker = isolated_bd(tmp_path)

    # attempts=2 on both sides of the retry: 2 abandoned pre-retry cycles,
    # then 2 post-retry cycles reusing the exact same round numbers (1, 2).
    pol = loop_policy(tmp_path, "verify.fix_loop", attempts=2)
    wid = await executor.intake(
        database,
        run_dirs,
        title="never fixed",
        repo=str(repo),
        chain=_fixloop_template(tmp_path),
        bd_cwd=str(tracker),
    )
    first = await executor.run(
        database, run_dirs, work_item_id=wid, registry=None, bd_cwd=str(tracker), policy=pol
    )
    assert first == "needs_human"  # cap breached; 2 abandoned fix sessions exist

    await database.write(lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"]))
    await database.write(lambda c: store.retry_after_cap(c, wid, "verify", "verify.fix_loop", None))
    second = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        policy=pol,
        start_index=0,
    )
    assert second == "needs_human"  # the fresh post-retry budget breaches too
    fix_sessions = database.read(
        lambda c: c.execute(
            "SELECT result_path FROM worker_sessions WHERE work_item_id = ? "
            "AND hook_point = ? ORDER BY created_at",
            (wid, _FIX),
        ).fetchall()
    )
    # 2 abandoned pre-retry cycles + 2 post-retry cycles, in creation order.
    assert len(fix_sessions) == 4
    abandoned_cycle1_path = fix_sessions[0]["result_path"]  # pre-retry round 1
    post_retry_cycle1_path = fix_sessions[2]["result_path"]  # post-retry round 1

    sent = _sent_prompts(prompts_path)
    assert len(sent) == 4
    post_retry_cycle2_prompt = sent[3]
    assert abandoned_cycle1_path not in post_retry_cycle2_prompt
    assert post_retry_cycle1_path in post_retry_cycle2_prompt


async def test_fix_dispatch_after_resume_still_carries_the_previous_attempt(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """Crash-recovery re-enters `_walk_node` with a fresh local `round = 0`
    while cycle 1's fix session from before the crash is still in
    `worker_sessions`. A round-keyed lookup finds nothing at round 0 and the
    handoff silently vanishes; the fix dispatched after resume must still
    carry it forward."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")  # keeps failing so a 2nd cycle dispatches
    prompts_path = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts_path))
    monkeypatch.setenv("KRAFT_FAKE_TESTRUN_COUNTER", str(tmp_path / "attempt.count"))
    tracker = isolated_bd(tmp_path)

    pol = loop_policy(tmp_path, "verify.fix_loop", attempts=2)
    wid = await executor.intake(
        database,
        run_dirs,
        title="resume mid loop",
        repo=str(repo),
        chain=_fixloop_template(tmp_path),
        bd_cwd=str(tracker),
    )
    # The worktree the suite runs inside, cut for real -- the
    # preparation V1 runs before the first node.
    from kraft import builtins

    await builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id=wid, repo_entry=None
    )
    await database.write(lambda c: store.load_chain(c, wid, "verify"))

    # Seed a full cycle 1 that ran before the crash: cycle-0 measure fails,
    # a fix task runs and reports done, and its re-measure fails again --
    # then the crash lands before cycle 2 is dispatched.
    await database.write(lambda c: store.enter_node(c, wid, "verify"))
    await database.write(
        lambda c: store.create_session(
            c,
            id="s-m0",
            work_item_id=wid,
            node_id="verify",
            hook_point="verify.main.suite",
            log_path="/l0",
            result_path="/r0",
        )
    )
    await database.write(lambda c: store.session_exited(c, "s-m0", "failed"))
    cap = policy.resolve_cap(pol, "verify.fix_loop")
    n0, _started0, _ = await database.write(
        lambda c: store.bump_counter(c, wid, "verify.fix_loop", cap)
    )
    assert n0 == 1

    await database.write(
        lambda c: store.create_session(
            c,
            id="s-fix1",
            work_item_id=wid,
            node_id="verify",
            hook_point=_FIX,
            log_path="/l1",
            result_path="/marker/pre-crash-fix-result.json",
            round=1,
        )
    )
    await database.write(
        lambda c: store.session_exited(c, "s-fix1", "done", "pre-crash-summary.md")
    )
    await database.write(
        lambda c: store.create_session(
            c,
            id="s-m1",
            work_item_id=wid,
            node_id="verify",
            hook_point="verify.main.suite",
            log_path="/l2",
            result_path="/r1",
            round=1,
        )
    )
    await database.write(lambda c: store.session_exited(c, "s-m1", "failed"))

    result = await executor.resume(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        adopted={},
        bd_cwd=str(tracker),
        policy=pol,
    )
    assert result == "needs_human"  # the resumed cycle (n=2) breaches the attempts=2 cap

    sent = _sent_prompts(prompts_path)
    assert len(sent) == 1  # exactly the resumed cycle's fix dispatch
    assert "pre-crash-fix-result.json" in sent[0]
    assert "pre-crash-summary.md" in sent[0]


# --- the null-summary branch, direct on the pure helper -----------------------


def test_previous_attempt_note_omits_the_summary_sentence_when_absent():
    """A fix task that ran without writing a session summary (e.g. a
    subprocess-kind fix hook) must not leave a dangling summary sentence
    behind."""
    note = executor.previous_attempt_note(
        {"result_path": "/r/cycle1.json", "session_summary_ref": None}
    )
    assert "/r/cycle1.json" in note
    assert "session summary" not in note


def test_previous_attempt_note_includes_the_summary_sentence_when_present():
    note = executor.previous_attempt_note(
        {"result_path": "/r/cycle1.json", "session_summary_ref": ".engineering/sessions/x.md"}
    )
    assert "/r/cycle1.json" in note
    assert ".engineering/sessions/x.md" in note


def test_previous_attempt_note_is_empty_with_no_previous_attempt():
    assert executor.previous_attempt_note(None) == ""


@pytest.mark.xfail(
    strict=True,
    reason="V1 fix-loop escalation is Task 7 (node.fix_loop / node.escalation); "
    "legacy escalate_after is superseded",
)
async def test_a_fix_cycle_past_escalate_after_launches_with_escalate_model(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """Spec §6: cycles at or below `escalate_after` use `model`, cycles above it
    use `escalate_model`. Rounds 1-3 resume the same approach; a loop that
    survives them usually needs a capability bump, not another identical try."""
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")  # never patches calc.py, so it never converges
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("KRAFT_FAKE_TESTRUN_COUNTER", str(tmp_path / "attempt.count"))
    tracker = isolated_bd(tmp_path)

    chain = _fixloop_template(tmp_path, fix_model="sonnet")

    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops:\n  verify.fix_loop: { attempts: 4, wall_clock_s: 3600, escalate_after: 2 }\n"
        "default: { attempts: 4, wall_clock_s: 3600 }\n"
        "auto_escalate_stuck: false\n"
    )
    pol = policy.load_policy(p)

    wid = await executor.intake(
        database,
        run_dirs,
        title="never fixed",
        repo=str(repo),
        chain=chain,
        bd_cwd=str(tracker),
        # A V1 task carries no `escalate_model`; the item's own node
        # override is where one is set now.
        node_overrides={"verify": {"escalate_model": "opus"}},
    )
    assert (
        await executor.run(
            database,
            run_dirs,
            work_item_id=wid,
            registry=None,
            bd_cwd=str(tracker),
            policy=pol,
        )
        == "needs_human"  # the cap breaches; the models used on the way are the point
    )

    models = []
    for line in argv_log.read_text().splitlines():
        argv = json.loads(line)
        models.append(argv[argv.index("--model") + 1] if "--model" in argv else None)
    # cycles 1 and 2 are at or below the threshold, cycle 3 is past it
    assert models[:3] == ["sonnet", "sonnet", "opus"]
