"""The rules a reply round changes in the walk: what the judge declined, the record's mark, the
earlier measurement's findings and the judge's history of a round. The reader's own tests
are in `test_reply_round.py`."""

# ruff: noqa: F811 -- `judge` is an imported fixture, taken by name.

import json

import pytest

from kraft import events, store
from kraft import policy as _policy
from kraft.executor import dispatch, replies, reply_round, walk
from kraft.executor.reply_round import ReplyRound

from .test_measurement import _NEEDS_CONTEXT_NODE
from .test_walk_early import (  # noqa: F401 -- the walk's own rig, and its judge fixture
    CLEAN,
    LAUNCH,
    REAL,
    RED,
    _cycles,
    _found,
    _node,
    _reporting,
    _walk,
    judge,
)


def _record(cycle, message, *, marked=False):
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
        **({"reply_round": True} if marked else {}),
    }


async def _history(it, records, fixed_rounds):
    """`judge_history` of node `verify` after `records` were measured and a fix ran at each
    of `fixed_rounds`."""
    node = it.chain.chain.nodes[0]
    for payload in records:
        await it.database.write(
            lambda c, p=payload: events.append(c, it.id, "findings_measured", p)
        )
    for n in fixed_rounds:
        await it.session(f"f{n}", dispatch.fix_task_paths(node)[0], "done", round=n)
    return dispatch.judge_history(
        it.database,
        it.id,
        "verify",
        frozenset({"critical"}),
        fix_paths=dispatch.fix_task_paths(node),
    )


def _pairs(history):
    return history[0]["fix_result_path"] is None


def _stuck(history):
    return dispatch.stuck_fingerprint(history, 2) is not None


def _regressed(history):
    return bool(dispatch.regressed_fingerprints(history))


@pytest.mark.parametrize(
    "records, check, expected",
    [
        ([_record(1, "x", marked=True)], _pairs, True),
        ([_record(1, "x")], _pairs, False),
        # Two records after fixes of the same finding are stuck; the second marked is not.
        ([_record(0, "same"), _record(1, "same")], _stuck, True),
        ([_record(0, "same"), _record(1, "same", marked=True)], _stuck, False),
        # A finding that is gone in a round after a fix and back is a regression; a marked
        # round between pairs no fix, so nothing was fixed in the gap.
        ([_record(0, "back"), _record(1, "other"), _record(2, "back")], _regressed, True),
        (
            [_record(0, "back"), _record(1, "other", marked=True), _record(2, "back")],
            _regressed,
            False,
        ),
    ],
    ids=[
        "marked-pairs-none",
        "unmarked-pairs-the-fix",
        "unmarked-with-fix-is-stuck",
        "stuck-fingerprint-ignores-the-marked-record",
        "unmarked-regresses",
        "regressed-ignores-the-marked-record",
    ],
)
async def test_a_reply_round_record_pairs_no_fix(item_on, records, check, expected):
    """`a-reply-round-s-record-pairs-no-fix`: no fix ran before a reply round's record, so
    the judge's history never pairs it with the fix session of its round, and neither the
    stall check nor the regression check reads it as following a fix."""
    it = await item_on([_NEEDS_CONTEXT_NODE], repo="/r")

    history = await _history(it, records, [r["cycle"] for r in records])

    assert check(history) is expected


async def _declined(it, *, head="h1", ended_early=False, node="verify", verdict="stop_downgrade"):
    await it.database.write(
        lambda c: events.append(
            c,
            it.id,
            "judge_verdict",
            {
                "node_id": node,
                "verdict": verdict,
                "ended_early": ended_early,
                "head_sha": head,
                "findings": [_record(0, "x")["findings"][0]],
            },
        )
    )


@pytest.mark.parametrize(
    "kwargs, every, declined",
    [
        ({"ended_early": True}, False, True),
        ({}, False, False),
        ({}, True, True),
        ({"head": "h2"}, True, False),
        ({"node": "other"}, True, False),
    ],
    ids=[
        "ended-early-counts-as-before",
        "not-ended-early-ignored-by-default",
        "not-ended-early-counts-with-every",
        "another-head-never",
        "another-node-never",
    ],
)
async def test_declined_fingerprints_every(item_on, kwargs, every, declined):
    """`a-reply-round-honours-every-decline-on-its-head`."""
    it = await item_on([_NEEDS_CONTEXT_NODE], repo="/r")
    await _declined(it, **kwargs)

    got = dispatch.declined_fingerprints(it.database, it.id, "verify", "h1", every=every)

    assert bool(got) is declined


# -- a walk in a reply round ---------------------------------------------------


async def _round_open(it, *, count=0, threads=()):
    """Start a reply round of node `n` on the item's head, at loop round `count`, and with
    `count` fix cycles behind it (a counter and a fix session)."""
    node = it.chain.chain.nodes[0]
    head = reply_round.item_head(it.row(), it.repo)
    if count:
        key = walk._loop_key(node)
        cap = _policy.Cap(9, 3600)
        await it.database.write(lambda c: store.bump_counter(c, it.id, key, cap))
        await it.session("f0", dispatch.fix_task_paths(node)[0], "done", round=count)
    await it.database.write(
        lambda c: events.append(
            c,
            it.id,
            "reply_round_started",
            {"node_id": "n", "head": head, "round": count, "threads": list(threads)},
        )
    )
    return head


@pytest.mark.parametrize(
    ("early", "red_first", "cycle"),
    [(True, False, 0), (False, False, 0), (False, True, walk._REPAIR_ROUND)],
    ids=["loop-early", "no-early", "re-measure-after-the-repair"],
)
async def test_a_reply_round_does_not_collect_what_the_judge_declined_on_the_head(
    item_on, script, tmp_path, early, red_first, cycle
):
    """`a-reply-round-honours-every-decline-on-its-head`: the judge declined "naming nit" on
    this head with `stop_downgrade` (not on an early start); a reply round's triage raising it
    again does not record it, enter the fix loop or reach the judge, on either node shape, nor
    on the passing re-measure after the round's CI went red and `on_failure` ran."""
    nit = _found("naming nit", "important")
    shape = {"triage": (nit, nit), "ci": (RED, CLEAN), "on_failure": True} if red_first else {}
    it = await item_on([_node(tmp_path, **{"triage": (nit,), **shape}, early=early)])
    head = await _round_open(it)
    await it.database.write(
        lambda c: events.append(
            c,
            it.id,
            "judge_verdict",
            {
                "node_id": "n",
                "verdict": "stop_downgrade",
                "ended_early": False,
                "head_sha": head,
                "findings": nit["findings"],
            },
        )
    )
    script.real = REAL

    assert await _walk(it) == "ok"

    [record] = [e["payload"] for e in it.events("findings_measured")]
    assert record["findings"] == [] and record["reply_round"] is True
    assert record["cycle"] == cycle
    assert "fix" not in script.calls


async def test_an_amend_keeps_the_reply_round_mark(item_on, script, judge, tmp_path):
    """`a-reply-round-s-record-pairs-no-fix`: the judge declines the early start of a reply
    round's measurement, the measurement goes on and amends its record, which keeps its mark
    (the round is over once the record is written, so the continuation is not in it)."""
    it = await item_on(
        [_node(tmp_path, triage=(_found("rename"), CLEAN), ci=(_found("ci note", "minor"),))]
    )
    await _round_open(it, count=1)
    script.real = REAL
    judge.verdicts = ["stop_downgrade"]

    assert await _walk(it) == "ok"

    [record] = [e["payload"] for e in it.events("findings_measured")]
    assert _cycles(it) == [1]
    assert record["reply_round"] is True
    assert [f["message"] for f in record["findings"]] == ["ci note"]
    assert "n.ci.ci" in record["measured_tasks"]


async def test_a_reply_round_record_keeps_the_earlier_triage_findings_as_unmeasured(
    item_on, fake_agent
):
    """`a-reply-round-s-triage-reads-only-what-is-new`: a triage skipped because nothing new
    was said did not find nothing: the record lists it as unmeasured and keeps the finding
    the earlier measurement had from it."""
    triage = {
        "id": "judge",
        "kind": "agent",
        "harness": "fake",
        "prompt": "Decide.",
        "inputs": ["bot_feedback"],
    }
    wait = {"id": "wait", "kind": "forge", "target": "mr.automated_review"}
    fix = {"id": "fix", "kind": "agent", "harness": "fake", "prompt": "Fix."}
    node_def = {
        "id": "n",
        "kind": "exec",
        "steps": [{"id": "settle", "tasks": [wait]}, {"id": "decide", "tasks": [triage]}],
        "fix_loop": {"tasks": [fix]},
    }
    it = await item_on([node_def], "n")
    node = it.chain.chain.nodes[0]
    triage_path = node.steps[1].tasks[0].path
    # The earlier measurement: its triage found something, and read the bots at 00:02.
    older = _found("rename", "minor", source=triage_path)["findings"][0]
    earlier = {
        "node_id": "n",
        "cycle": 0,
        "head_sha": "h0",
        "measured_tasks": [triage_path],
        "findings": [{**older, "jobs": [{"label": triage_path, "log_ref": "x"}]}],
    }
    await it.database.write(lambda c: events.append(c, it.id, "findings_measured", earlier))
    await it.session("w0", node.steps[0].tasks[0].path, "done", head_sha="abc")
    note = {"by": "bot", "created_at": "2026-01-01T00:01:00Z", "body": "text"}
    thread = {"id": "t1", "file": "a.py", "line": 1, "body": "x", "notes": [note]}
    collected = it.files("w0").bot_feedback
    collected.parent.mkdir(parents=True, exist_ok=True)
    collected.write_text(
        json.dumps({"reviewers": [{"reviewer": "bot:x", "threads": [thread], "summaries": []}]})
    )
    await it.session("t0", triage_path, "done", head_sha="abc")
    read = it.files("t0").bot_feedback
    read.parent.mkdir(parents=True, exist_ok=True)
    read.write_text(json.dumps({"read_at": "2026-01-01T00:02:00Z", "reviewers": []}))
    assert await _round_open(it, threads=("t1",)), "the round names the item's real head"

    result = await walk.walk_node(
        it.database,
        it.run_dirs,
        it.id,
        node,
        it.row(),
        it.repo,
        policy=_policy.Policy(loops={}, default=_policy.Cap(9, 3600)),
        launch=LAUNCH,
        start_step=1,
    )

    assert result == "ok"
    record = it.events("findings_measured")[-1]["payload"]
    assert record["reply_round"] is True
    assert triage_path in record["unmeasured_tasks"]
    assert [f["message"] for f in record["unmeasured"]] == ["rename"]
    assert record["findings"] == []
    assert fake_agent.argv() == []


async def test_a_round_started_after_a_doubted_repair_does_not_re_serve_the_doubts(
    item_on, script, judge, tmp_path, monkeypatch
):
    """The node's repair finished with concerns and the node then reached its completion exit (the
    red check's finding is below the loop's severities), where a reply round starts. The round is a
    fresh measurement: its fix cycle is not preceded by the judge, and its instruction carries no
    note of the old repair's doubts."""
    doubt = {"status": "done_with_concerns", "findings": [], "concerns": "needs a code change"}
    node = _node(
        tmp_path,
        triage=(CLEAN, _found("rename"), CLEAN),
        ci=(_found("lint failed", "minor", status="failed"), CLEAN),
        on_failure=True,
        early=False,
    )
    node["on_failure"]["tasks"] = [_reporting(tmp_path, "repair", [doubt])]
    it = await item_on([node])
    script.real = REAL | {"repair"}
    started = []

    async def start_once(db, run_dirs, wid, node, row, worktree, launch, *, completing=False, **_):
        if not completing or started:
            return None
        started.append(1)
        head = reply_round.item_head(row, worktree)
        round_ = ReplyRound(0, head, 0, ("T1",))
        await reply_round.begin(db, wid, round_, node.id)
        return round_

    monkeypatch.setattr(replies, "posting_point", start_once)

    assert await _walk(it) == "ok"

    assert started and script.calls.count("repair") == 1
    assert judge.rounds == []  # a stale doubt would have made the judge due on the round's cycle
    [instruction] = script.instructions["fix"]
    assert "needs a code change" not in instruction
