"""Kraft-m2q: REPEAT marking and no-progress escalation read the same
measurement but ask different questions of it.

`kraft.executor.dispatch.last_measurement` used to be `_previous_fingerprints`, which returned None
unless a fix cycle had followed the measurement. That gate is right for
escalation and wrong for REPEAT marking, which only needs "was this finding in
the last measurement" -- so on the first fix cycle after a steered `POST /retry`
the finding that caused the stop was dispatched with no REPEAT tag and no repeat
note, which spec §5 calls the part that matters more than the findings
themselves.

Kept out of tests/test_findings_loop.py, which is sub-project F's
backward-compatibility guarantee and stays unedited.
"""

import asyncio
import json
import shlex
import sys
import tempfile
from pathlib import Path

from support.chain_run import loop_policy
from support.harness import isolated_bd, make_repo, v1_fix_loop_node, v1_seeded_chain

from kraft import db, events, executor, store
from kraft.paths import RunDirs


def _seed(tmp_path, seq):
    """Run `seq` as (event_type, payload) against one work item, then return
    `kraft.executor.dispatch.last_measurement` for node 'verify'."""
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B-1",
                    title="t",
                    repo=str(repo),
                    chain_template="quick-task",
                    chain_definition=json.dumps({"template_id": "quick-task", "nodes": []}),
                )
            )
            for etype, payload in seq:
                await database.write(lambda c, e=etype, p=payload: events.append(c, "w1", e, p))
            return executor.last_measurement(database, "w1", "verify")
        finally:
            await database.close()

    return asyncio.run(scenario())


def _payload(message="boom", severity="critical", **extra):
    return {
        "severity": severity,
        "message": message,
        "file": "a.py",
        "line": 1,
        "source_plugin": "p",
        **extra,
    }


_MEASURED = (
    "findings_measured",
    {"node_id": "verify", "cycle": 0, "fingerprints": ["fp1"], "findings": [_payload()]},
)
_FIXED = ("fix_cycle_started", {"node_id": "verify", "cycle": 1})


def _messages(previous):
    return [f.message for f in previous]


def test_a_measurement_with_no_fix_after_it_still_yields_its_findings(tmp_path):
    """The REPEAT half. Pre-fix this returned None, so the first fix cycle after
    a steered retry lost the tag on the very finding that caused the stop."""
    previous, fix_ran, _ = _seed(tmp_path, [_MEASURED])
    assert _messages(previous) == ["boom"]
    assert fix_ran is False


def test_a_fix_after_the_measurement_is_reported(tmp_path):
    previous, fix_ran, _ = _seed(tmp_path, [_MEASURED, _FIXED])
    assert _messages(previous) == ["boom"]
    assert fix_ran is True


def test_a_fix_before_the_measurement_does_not_count(tmp_path):
    """Order matters: escalation needs a fix that ran *since* the measurement."""
    previous, fix_ran, _ = _seed(tmp_path, [_FIXED, _MEASURED])
    assert _messages(previous) == ["boom"]
    assert fix_ran is False


def test_another_node_s_events_are_ignored(tmp_path):
    other = ("findings_measured", {"node_id": "elsewhere", "findings": [_payload("nope")]})
    previous, _, _ = _seed(tmp_path, [_MEASURED, other])
    assert _messages(previous) == ["boom"]


def test_nothing_measured_yet(tmp_path):
    assert _seed(tmp_path, []) == (None, False, None)


def test_the_whole_finding_comes_back_not_just_its_tag(tmp_path):
    """Kraft-s7c04.1 needs the messages to hand the reviewer back what it said,
    and Kraft-s7c04.3 needs the severities so a repeat cannot be re-rated down
    on a tree nobody touched. The fingerprints alone answer neither."""
    previous, _, _ = _seed(tmp_path, [_MEASURED])
    (f,) = previous
    assert (f.severity, f.file, f.source_plugin) == ("critical", "a.py", "p")


def test_a_same_as_tag_survives_the_round_trip(tmp_path):
    """The carry is only worth anything if identity comes back with it."""
    tagged = (
        "findings_measured",
        {"node_id": "verify", "cycle": 0, "findings": [_payload(same_as="0123456789abcdef")]},
    )
    previous, _, _ = _seed(tmp_path, [tagged])
    assert previous[0].fingerprint == "0123456789abcdef"


def test_a_measurement_survives_a_retry(tmp_path):
    """Deliberately NOT bounded the way `store.last_rejection` is. A review of
    this plan argued for a `work_item_retried` boundary by analogy with it; the
    analogy is wrong, because a retry does not change the tree and Kraft-m2q
    exists precisely because the first fix cycle after a steered retry lost the
    REPEAT tag on the finding that caused the stop."""
    previous, _, _ = _seed(tmp_path, [_MEASURED, ("work_item_retried", {})])
    assert _messages(previous) == ["boom"]


def test_only_the_most_recent_measurement_is_ever_returned(tmp_path):
    """What keeps an unbounded scan safe: however long the history, at most one
    round's tags can reach `resolve_identity`'s `known`, so nothing can claim
    identity with a finding from five rounds back."""
    older = ("findings_measured", {"node_id": "verify", "findings": [_payload("ancient")]})
    previous, _, _ = _seed(tmp_path, [older, _MEASURED])
    assert _messages(previous) == ["boom"]


# --- the behavioural half: REPEAT on the first fix cycle after a steered retry ---
#
# Mirrors tests/test_findings_loop.py::test_retry_after_no_progress_dispatches_a_
# fix_instead_of_re_escalating, which pins that the retry reaches a fix at all.
# This pins what that fix is *told*. Rebuilt here rather than extended there,
# because that file is sub-project F's backward-compatibility guarantee.

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE_REVIEWER = Path(__file__).parent / "support" / "fake_reviewer.py"


def _template(tmp_path):
    """`review` measured by the scripted reviewer, with the fix loop and judge
    on the fake agent. No `env_setup` node: V1 prepares the worktree first."""
    reviewer = {
        "id": "review",
        "kind": "subprocess",
        "command": shlex.join([sys.executable, str(_FAKE_REVIEWER)]),
    }
    return v1_seeded_chain(
        tmp_path / "templates",
        [v1_fix_loop_node("review", reviewer)],
        agent_command=f"{sys.executable} {_FAKE_AGENT}",
    )


_UNFIXED = {
    "severity": "critical",
    "message": "unfixed",
    "file": "a.py",
    "line": 3,
    "source_plugin": "fake",
}


def test_the_fix_after_a_steered_retry_still_marks_the_finding_repeat(tmp_path, monkeypatch):
    """The reported symptom. A no-progress stop, a `POST /retry`, and then the
    first fix cycle of the re-entered loop: the finding that caused the stop was
    in the last measurement, so it must carry REPEAT.

    It does NOT carry the repeat *note* here -- see the sibling test.
    """
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")  # never patches the code
    log = tmp_path / "prompts.log"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(log))
    call_dir = Path(tempfile.mkdtemp(dir=tmp_path))
    plan = call_dir / "review-plan.json"
    # Cycle 0 fails; cycle 1 re-measures unchanged code and escalates on
    # no_progress. A third invocation (the retry's re-measurement) clamps to the
    # last entry and reports the same finding again.
    plan.write_text(json.dumps([{"status": "failed", "findings": [_UNFIXED]}] * 2))
    monkeypatch.setenv("KRAFT_FAKE_REVIEW_PLAN", str(plan))
    tracker = isolated_bd(call_dir)
    repo = make_repo(call_dir)

    async def scenario():
        rd = RunDirs(call_dir / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            pol = loop_policy(call_dir, "review.fix_loop", attempts=5)
            wid = await executor.intake(
                database,
                rd,
                title="review me",
                repo=str(repo),
                chain=_template(call_dir),
                bd_cwd=str(tracker),
            )
            first = await executor.run(
                database, rd, work_item_id=wid, registry=None, bd_cwd=str(tracker), policy=pol
            )
            assert first == "needs_human"
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            stops = [e for e in evts if e["type"] == "work_item_needs_human"]
            reason = stops[-1]["payload"]["reason"]
            assert reason.startswith("stuck:")

            before = len(_prompts(log))
            await database.write(
                lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"])
            )
            await database.write(
                lambda c: store.retry_after_cap(c, wid, "review", "review.fix_loop", None)
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                start_index=0,
            )
            return before
        finally:
            await database.close()

    before = asyncio.run(scenario())
    post_retry = _prompts(log)[before:]
    assert post_retry, "the steered retry dispatched no fix at all"
    assert "REPEAT" in post_retry[0]
    assert "you attempted this on an earlier cycle" not in post_retry[0]


def _prompts(log):
    """Fix-agent prompts only. The fix-loop judge shares this same prompt log
    (dispatched through the same fake agent) and is asked before every fix
    cycle past the first, whether that first cycle was in this walk_node
    call or an earlier one a retry resumed from -- filtered out here since
    these tests are about the *fix* agent's own prompt."""
    if not log.is_file():
        return []
    return [
        p
        for p in log.read_text().split("\n\x00\n")
        if p.strip() and "is about to spend another cycle" not in p
    ]
