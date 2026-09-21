"""The fix loop's memory: what one round of `verify` tells the next one.

Batch B of the 2026-09-15 outlier post-mortem. `verify` was 50.5% of all Kraft
spend ($194.45 of $385 across 20 work items) and almost none of it was test
execution -- it was a review agent re-reading a whole branch diff, cold, once
per round, never told what it said the round before.

Kept out of `tests/test_findings_loop.py`, which is sub-project F's
backward-compatibility guarantee and stays unedited -- the same reason
`tests/test_repeat_marking.py` is its own file.
"""

import asyncio
import json
import shlex
import sys
from pathlib import Path

import pytest
from support.chain_run import loop_policy, run_chain
from support.harness import isolated_bd, make_repo, v1_fix_loop_node, v1_seeded_chain
from support.store_fixtures import mk_item, open_db

from kraft import db, events, executor
from kraft.executor import dispatch, prompts, walk
from kraft.findings import Finding
from kraft.paths import RunDirs

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE_REVIEWER = Path(__file__).parent / "support" / "fake_reviewer.py"


_FAKE = f"{sys.executable} {_FAKE_AGENT}"


# --------------------------------------------------------------------------
# carried_findings_note -- what the reviewer is handed (Kraft-s7c04.1 + .2)
# --------------------------------------------------------------------------


def test_a_carried_findings_note_lists_last_rounds_findings_with_their_tags():
    f = Finding("important", "Swallows the OSError", "a.py", 10, "code-review")
    note = prompts.carried_findings_note([f])
    assert f.fingerprint in note, "without the tag there is no way to claim identity"
    assert "Swallows the OSError" in note
    assert "a.py:10" in note
    assert "same_as" in note, "the reviewer must be told how to claim identity"


def test_the_note_asks_for_both_halves_of_the_answer():
    """A finding still present and a finding fixed have to produce different
    output, or the loop cannot tell a fix from a reviewer that went quiet."""
    note = prompts.carried_findings_note([Finding("minor", "m", "a.py", 1, "p")])
    assert "Still present" in note
    assert "Fixed" in note


def test_the_note_does_not_turn_the_review_into_a_checklist():
    """The carry is context, not scope. A reviewer that only re-checks last
    round's list stops finding what this round's fix just broke."""
    note = prompts.carried_findings_note([Finding("minor", "m", "a.py", 1, "p")])
    assert "report anything new you find" in note


def test_no_previous_round_means_no_note_at_all():
    """Round 0 stays byte-identical to today: this batch must not add a line to
    the first and most important review of every work item."""
    assert prompts.carried_findings_note([]) == ""


# --------------------------------------------------------------------------
# the dispatch actually carries it
# --------------------------------------------------------------------------


def _dispatch_brief(tmp_path, monkeypatch, *, seed_events=()):
    """Dispatch one agent task that produces the review brief -- what the
    deferred-findings note keys on (`AgentTask.produces`) -- and return the
    prompt it was launched with."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompt_log = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompt_log))
    brief = {
        "id": "write_summary",
        "kind": "agent",
        "harness": "fake",
        "prompt": "Write the work-item summary and review brief.",
        "produces": "review_brief",
    }
    chain = v1_seeded_chain(
        tmp_path / "templates",
        [{"id": "human_review", "kind": "exec", "tasks": [brief]}],
        agent_command=_FAKE,
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=chain, bd_cwd=str(tracker)
            )
            for etype, payload in seed_events:
                await database.write(lambda c, e=etype, p=payload: events.append(c, wid, e, p))
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            node = chain.nodes[0]
            await dispatch.dispatch_node(
                database,
                rd,
                node.steps[0].tasks[0],
                node,
                row,
                repo,
                launch=executor.LaunchContext(repo_entry=None, steering_dir=None),
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    return prompt_log.read_text()


def _measured(node_id="verify", **finding):
    base = {
        "severity": "important",
        "message": "Swallows the OSError",
        "file": "a.py",
        "line": 10,
        "source_plugin": "code-review",
    }
    return (
        "findings_measured",
        {"node_id": node_id, "cycle": 0, "findings": [{**base, **finding}]},
    )


# --------------------------------------------------------------------------
# a reworded repeat is recognised end to end (Kraft-s7c04.2)
# --------------------------------------------------------------------------


def _loop_chain(tmp_path):
    """`review` is the fix-loop node under test, measured by the scripted
    reviewer. No `env_setup` node: V1 prepares the worktree first."""
    reviewer = {
        "id": "review",
        "kind": "subprocess",
        "command": shlex.join([sys.executable, str(_FAKE_REVIEWER)]),
    }
    return v1_seeded_chain(
        tmp_path / "templates", [v1_fix_loop_node("review", reviewer)], agent_command=_FAKE
    )


def _run_loop(tmp_path, monkeypatch, entries, *, attempts=3):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    plan = tmp_path / "review-plan.json"
    plan.write_text(json.dumps(entries))
    monkeypatch.setenv("KRAFT_FAKE_REVIEW_PLAN", str(plan))
    return run_chain(
        tmp_path,
        _loop_chain(tmp_path),
        policy=loop_policy(tmp_path, "review.fix_loop", attempts=attempts),
        title="review me",
    )


def _stop_reason(out):
    for e in reversed(out["events"]):
        if e["type"] == "work_item_needs_human":
            return e["payload"]["reason"]
    return None


def _finding(message, severity="important", **extra):
    return {
        "severity": severity,
        "message": message,
        "file": "a.py",
        "line": 3,
        "source_plugin": "fake",
        **extra,
    }


_FIRST = "Swallows the OSError in resolve_repo"
_TAG = Finding("important", _FIRST, "a.py", 3, "fake").fingerprint


def _cycles(out):
    return sum(e["type"] == "fix_cycle_started" for e in out["events"])


_REWORDINGS = [
    "the OSError is caught and dropped",
    "resolve_repo hides a failed lookup",
    "the failed lookup is swallowed",
    "an error path in resolve_repo returns None instead of raising",
]


def test_a_reworded_repeat_is_recognised_as_the_same_finding(tmp_path, monkeypatch):
    """Kraft-s7c04.2 end to end. On 49c0cefd one reattach.py defect was reported
    in FIVE consecutive measurements under five distinct fingerprints, so the
    loop could never tell it was looking at one unmoved defect. Here the
    reviewer restates it in new words each round and says so with `same_as`:
    the loop recognises the repeat on the very next round and stops."""
    out = _run_loop(
        tmp_path,
        monkeypatch,
        [{"status": "done", "findings": [_finding(_FIRST)]}]
        + [{"status": "done", "findings": [_finding(m, same_as=_TAG)]} for m in _REWORDINGS],
    )
    assert out["result"] == "needs_human"
    assert _stop_reason(out).startswith("stuck:")
    assert _cycles(out) == 1, "one wasted cycle, not a run to the cap"


def test_the_same_rewording_untagged_runs_to_the_cap(tmp_path, monkeypatch):
    """The control, and the defect itself. Identical findings, identical
    rewordings, no `same_as`: every round is a fresh fingerprint, nothing ever
    looks unchanged, and the loop spends its whole budget on one defect that
    never moved. This is what batch B is paying to stop."""
    out = _run_loop(
        tmp_path,
        monkeypatch,
        [{"status": "done", "findings": [_finding(_FIRST)]}]
        + [{"status": "done", "findings": [_finding(m)]} for m in _REWORDINGS],
    )
    assert out["result"] == "needs_human"
    assert "exhausted" in _stop_reason(out), "the loop should have run out of budget, not stopped"
    assert _cycles(out) == 3, "the full cap, against a single unmoved defect"


def test_a_tag_the_reviewer_was_never_shown_is_not_honoured(tmp_path, monkeypatch):
    """An invented tag must not build identity: `resolve_identity` strips it and
    the finding falls back to its own prose hash, so this behaves exactly like
    the untagged control rather than collapsing four defects into one."""
    out = _run_loop(
        tmp_path,
        monkeypatch,
        [{"status": "done", "findings": [_finding(_FIRST)]}]
        + [{"status": "done", "findings": [_finding(m, same_as="f" * 16)]} for m in _REWORDINGS],
    )
    assert "exhausted" in _stop_reason(out)
    assert _cycles(out) == 3


# --------------------------------------------------------------------------
# the review package starts where the last review finished (Kraft-s7c04.1)
# --------------------------------------------------------------------------


def _seed_session(tmp_path, rows, whole_row=False):
    """Insert worker_sessions rows straight, then ask what head was last
    reviewed on `on.review.local.run`. `whole_row` returns the row itself,
    which is what `previous_review_note` needs."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            for i, (hook, status, head, created) in enumerate(rows):
                await database.write(
                    lambda c, i=i, hook=hook, status=status, head=head, created=created: c.execute(
                        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, "
                        "log_path, result_path, status, created_at, head_sha) "
                        "VALUES (?, 'w1', 'verify', ?, '/l', '/r', ?, ?, ?)",
                        (f"s{i}", hook, status, created, head),
                    )
                )
            row = prompts.last_review_session(database, "w1", "on.review.local.run")
            if whole_row:
                return row
            return row["head_sha"] if row else None
        finally:
            await database.close()

    return asyncio.run(scenario())


def _seed_row(tmp_path, rows):
    """`_seed_session`, but handing back the whole row rather than the head."""
    return _seed_session(tmp_path, rows, whole_row=True)


def test_the_last_review_session_carries_its_own_artifacts(tmp_path):
    """The reviewer is handed its own previous result file and summary, so the
    row -- not just the head -- is what the lookup must return."""
    row = _seed_row(
        tmp_path,
        [("on.review.local.run", "done", "aaa111", "2026-01-01T00:00:00")],
    )
    assert row["head_sha"] == "aaa111"
    assert row["result_path"] == "/r"
    assert row["session_summary_ref"] is None


@pytest.mark.parametrize(
    ("later_hook", "since"),
    [
        # head_sha is stamped at dispatch and lives in the table, so it survives
        # a crash or a resume -- which a local in the walk's stack frame would not
        ("on.review.local.run", "bbb222"),
        # each reviewer narrows against its own last look, not somebody else's:
        # `on.test.run` running later says nothing about what was reviewed
        ("on.test.run", "aaa111"),
    ],
    ids=["the-last-session-of-this-hook", "not-another-hooks-head"],
)
def test_since_comes_from_the_last_session_that_ran_this_hook(tmp_path, later_hook, since):
    got = _seed_session(
        tmp_path,
        [
            ("on.review.local.run", "done", "aaa111", "2026-01-01T00:00:00"),
            (
                later_hook,
                "done",
                "bbb222" if later_hook == "on.review.local.run" else "zzz999",
                "2026-01-01T00:01:00",
            ),
        ],
    )
    assert got == since


def test_a_session_that_did_not_finish_is_not_a_reviewed_head(tmp_path):
    """A failed or killed session still carries a head_sha. Taking it would
    narrow the next review past code no reviewer has ever seen -- the one
    outcome worse than re-reading the whole branch."""
    for status in ("failed", "config_error", "rate_limited", "paused", "running"):
        got = _seed_session(
            tmp_path / status,
            [
                ("on.review.local.run", "done", "aaa111", "2026-01-01T00:00:00"),
                ("on.review.local.run", status, "bbb222", "2026-01-01T00:01:00"),
            ],
        )
        assert got == "aaa111", f"{status} was treated as a completed review"


def test_done_with_concerns_is_a_finished_review(tmp_path):
    """It reviewed and had something to say -- that is a real judgement."""
    got = _seed_session(
        tmp_path, [("on.review.local.run", "done_with_concerns", "ccc333", "2026-01-01T00:00:00")]
    )
    assert got == "ccc333"


def test_nothing_reviewed_yet_means_the_whole_branch(tmp_path):
    assert _seed_session(tmp_path, []) is None


# --------------------------------------------------------------------------
# a repeat's severity does not fall on an untouched tree (Kraft-s7c04.3)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("unchanged_tree", "severity"),
    [
        # 43717ee6 priced one forge/run.py:394 defect minor -> absent -> important
        # across three reviews OF A BYTE-IDENTICAL TREE. `loop_severities` is a
        # hard cliff, so the one round that said `minor` is what let the loop
        # believe it was converging.
        (True, "important"),
        # The reason this is gated on the head at all. Flooring unconditionally
        # turns a genuine partial fix -- important reduced to minor because the
        # fix worked -- into `prints == previous_prints` and parks the item at
        # "stuck: 1 finding(s) unchanged". Real progress reported as being stuck.
        (False, "minor"),
    ],
    ids=[
        "a-repeat-on-an-unchanged-tree-keeps-its-severity",
        "a-downgrade-after-a-real-fix-is-taken",
    ],
)
def test_a_repeat_keeps_its_severity_only_when_no_fix_ran_between_measurements(
    unchanged_tree, severity
):
    was = Finding("important", "m", "a.py", 1, "p")
    now = Finding("minor", "reworded", "a.py", 9, "p", same_as=was.fingerprint)
    (out,) = walk._carry_severity([now], [was], unchanged_tree=unchanged_tree)
    assert out.severity == severity


def test_an_upgrade_is_always_taken_as_given():
    was = Finding("minor", "m", "a.py", 1, "p")
    now = Finding("critical", "m", "a.py", 1, "p")
    (out,) = walk._carry_severity([now], [was], unchanged_tree=True)
    assert out.severity == "critical"


def test_a_finding_with_no_previous_round_is_untouched():
    now = Finding("minor", "m", "a.py", 1, "p")
    assert walk._carry_severity([now], [], unchanged_tree=True) == [now]


@pytest.mark.parametrize(
    "was",
    [
        Finding("critical", "somewhere else", "b.py", 1, "p"),
        # `from_payload` does `raw.get("severity", "")` with no validation -- its
        # whole docstring is about tolerating payloads it did not write. A legacy
        # or truncated findings_measured payload must not KeyError in the loop.
        Finding("", "m", "a.py", 1, "p"),
    ],
    ids=["an-unrelated-previous-finding", "a-previous-severity-never-validated"],
)
def test_a_previous_finding_that_is_no_floor_leaves_the_severity_alone(was):
    now = Finding("minor", "m", "a.py", 1, "p")
    assert walk._carry_severity([now], [was], unchanged_tree=True) == [now]


def _findings_payloads(out):
    return [e["payload"]["findings"] for e in out["events"] if e["type"] == "findings_measured"]


def test_a_downgraded_repeat_does_not_let_the_loop_exit(tmp_path, monkeypatch):
    """End to end, and the money: below `loop_severities` a finding is dropped
    from the loop entirely, so one inconsistent re-rating of a tree nobody
    touched ends a loop that has not converged. The reviewer here reports the
    same defect, no fix in between (the scripted reviewer never fixes anything),
    and downgrades it on round 1."""
    out = _run_loop(
        tmp_path,
        monkeypatch,
        [
            {"status": "done", "findings": [_finding(_FIRST)]},
            {"status": "done", "findings": [_finding("reworded", severity="minor", same_as=_TAG)]},
        ],
    )
    # It stayed in the loop rather than completing on the downgrade.
    assert out["result"] == "needs_human"
    assert _cycles(out) >= 1


def test_the_reviewers_own_rating_is_recorded_not_erased(tmp_path, monkeypatch):
    """A refused downgrade that leaves no trace makes a reviewer inconsistency
    indistinguishable from a reviewer agreeing, which is the class of blindness
    this batch exists to remove."""
    out = _run_loop(
        tmp_path,
        monkeypatch,
        [
            {"status": "done", "findings": [_finding(_FIRST)]},
            {"status": "done", "findings": [_finding("reworded", severity="minor", same_as=_TAG)]},
        ],
    )
    later = [p for p in _findings_payloads(out) if p]
    floored = [f for round_ in later for f in round_ if f.get("reported_severity")]
    assert floored, "no round recorded a reviewer rating that was overridden"
    assert floored[0]["severity"] == "important"
    assert floored[0]["reported_severity"] == "minor"


# --------------------------------------------------------------------------
# the fixer sees every round, not just the last (Kraft-s7c04.7)
# --------------------------------------------------------------------------


def test_a_fix_task_after_two_rounds_carries_the_whole_history(tmp_path, monkeypatch):
    """`last_measurement` gives one round. Two rounds back is what e983d85c
    needed: f37b90d3 pinned core.hooksPath=/dev/null to close a container
    escape, verify flagged that it broke git-lfs pre-push, 7381755f unpinned it,
    and the next round found the CRITICAL escape reopened."""
    log = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(log))
    _run_loop(
        tmp_path,
        monkeypatch,
        [
            {"status": "done", "findings": [_finding(_FIRST)]},
            {"status": "done", "findings": [_finding("a second defect")]},
            {"status": "done", "findings": [_finding("a third defect")]},
        ],
    )
    fixes = [p for p in log.read_text().split("\n\x00\n") if "Fix the code" in p]
    assert len(fixes) >= 2
    assert "Every round of this fix loop so far" in fixes[-1]
    assert "round 0" in fixes[-1] and "round 1" in fixes[-1]


def test_the_fix_round_is_unaffected_by_the_method_note(tmp_path, monkeypatch):
    """The fix round's instruction is an `instruction_override` (walk.py:870),
    which keeps absolute precedence over Task 2's skill-based selection. It
    must carry neither `prompts.METHOD_NOTE` nor the reference-style
    attachment wording -- both are for a hook with its own skill, and the fix
    round dispatches the fix loop's own agent task, which has none."""
    log = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(log))
    _run_loop(
        tmp_path,
        monkeypatch,
        [
            {"status": "done", "findings": [_finding(_FIRST)]},
            {"status": "done", "findings": [_finding("a second defect")]},
        ],
    )
    fixes = [p for p in log.read_text().split("\n\x00\n") if "Fix the code" in p]
    assert fixes
    for fix in fixes:
        assert prompts.METHOD_NOTE not in fix
        assert "Judge the change against them." not in fix


def test_the_first_fix_gets_no_history_block(tmp_path, monkeypatch):
    """Nothing to say, and the first cycle is the one this batch must not make
    more expensive."""
    log = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(log))
    _run_loop(tmp_path, monkeypatch, [{"status": "done", "findings": [_finding(_FIRST)]}])
    fixes = [p for p in log.read_text().split("\n\x00\n") if "Fix the code" in p]
    assert fixes
    assert "Every round of this fix loop so far" not in fixes[0]


def test_a_finding_that_was_fixed_and_came_back_is_flagged(tmp_path, monkeypatch):
    """The e983d85c shape, end to end: present, gone, back. The fixer is told
    so before it spends a cycle re-applying the fix that caused it."""
    log = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(log))
    _run_loop(
        tmp_path,
        monkeypatch,
        [
            {"status": "done", "findings": [_finding(_FIRST)]},
            {"status": "done", "findings": [_finding("broke git-lfs pre-push")]},
            {"status": "done", "findings": [_finding(_FIRST)]},
        ],
    )
    fixes = [p for p in log.read_text().split("\n\x00\n") if "Fix the code" in p]
    assert any(_TAG in p and "undid an earlier one" in p for p in fixes), (
        "a finding that was resolved and came back was never flagged"
    )


# --------------------------------------------------------------------------
# the findings that never entered the loop reach the brief (Kraft-s7c04.4)
# --------------------------------------------------------------------------


def _minor(message="naming nit", severity="minor"):
    return {
        "severity": severity,
        "message": message,
        "file": "store.ts",
        "line": 7,
        "source_plugin": "code-review",
    }


def test_a_review_brief_dispatch_is_handed_the_sub_threshold_findings(tmp_path, monkeypatch):
    """`skills/review-brief/SKILL.md` already promises the human "the local
    review findings, including the ones ruled minor" -- and the dispatch gave
    the agent no way to know them. On 6c712ea8 four real minor defects, a
    spec-phasing violation and an empty sweep board among them, never reached
    the brief; a human later hand-filed three different ones as cb5fa680."""
    prompt = _dispatch_brief(
        tmp_path,
        monkeypatch,
        seed_events=[("findings_measured", {"node_id": "verify", "findings": [_minor()]})],
    )
    assert "naming nit" in prompt
    assert "the fix loop did not act on them" in prompt


def test_an_item_with_no_deferred_findings_gets_no_note(tmp_path, monkeypatch):
    prompt = _dispatch_brief(tmp_path, monkeypatch)
    assert "the fix loop did not act on them" not in prompt


def test_a_finding_that_burned_a_cycle_is_not_deferred(tmp_path, monkeypatch):
    """Deferred means "never entered the loop". An `important` finding did
    enter it, and the brief must not describe it as quietly dropped."""
    prompt = _dispatch_brief(
        tmp_path,
        monkeypatch,
        seed_events=[
            (
                "findings_measured",
                {"node_id": "verify", "findings": [_minor("a real defect", "important")]},
            )
        ],
    )
    assert "a real defect" not in prompt


def test_the_board_and_the_brief_read_one_function(monkeypatch):
    """One implementation, one severity set: a brief listing a different set
    from the card above it would be worse than one listing nothing."""
    assert dispatch.deferred_findings is not None
    import inspect

    from kraft.api.routes import board

    assert "executor.deferred_findings" in inspect.getsource(board._deferred_findings)


def test_previous_review_note_points_at_the_reviewers_own_last_session():
    note = prompts.previous_review_note(
        {
            "result_path": "/run/results/abc.json",
            "session_summary_ref": ".engineering/sessions/abc.md",
        }
    )
    assert "/run/results/abc.json" in note
    assert ".engineering/sessions/abc.md" in note


def test_previous_review_note_drops_a_summary_it_does_not_have():
    """A dangling path is worse than a missing line: the agent burns a tool
    call discovering the file was never written."""
    note = prompts.previous_review_note({"result_path": "/r.json", "session_summary_ref": None})
    assert "/r.json" in note
    assert "None" not in note
    assert "summary" not in note.lower()


def test_previous_review_note_does_not_re_paste_findings():
    """carried_findings_note renders them verbatim immediately above."""
    note = prompts.previous_review_note({"result_path": "/r.json", "session_summary_ref": None})
    assert "severity" not in note.lower()
    assert "[" not in note


def test_fix_attempt_note_frames_a_skipped_finding_as_a_disagreement():
    note = prompts.fix_attempt_note({"result_path": "/f.json", "session_summary_ref": None})
    assert "/f.json" in note
    assert "disagreement" in note.lower()


def test_both_notes_are_empty_without_a_previous_session():
    assert prompts.previous_review_note(None) == ""
    assert prompts.fix_attempt_note(None) == ""


# --------------------------------------------------------------------------
# a repeat reviewer is handed the fix cycle and its own last session
# (Kraft-qzkux)
# --------------------------------------------------------------------------
