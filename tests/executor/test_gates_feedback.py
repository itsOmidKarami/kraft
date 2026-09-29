"""Task 7: a gate never asks for feedback it already has (review threads
anywhere §1). Split out of test_gates.py to keep it under the line budget."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from support.harness import entry_of, fake_harness_home

from kraft import executor, store
from kraft import policy as _policy
from kraft.adapters import agent as agent_mod
from kraft.api.routes import gates as gates_route
from kraft.executor import gates as gates_module
from kraft.executor.context import LaunchContext

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}))


def _exec(node_id, task_id="run"):
    return {
        "id": node_id,
        "kind": "exec",
        "tasks": [{"id": task_id, "kind": "subprocess", "command": "true"}],
    }


def _walk(it, **kwargs):
    return executor.run_once(
        it.database, it.run_dirs, work_item_id=it.id, launch=NO_SETUP, **kwargs
    )


def _cap(attempts):
    return _policy.Policy(loops={}, default=_policy.Cap(attempts=attempts, wall_clock_s=3600))


async def _published_thread(it, *, gate, label, body="fix this"):
    """A thread filed and published against `gate` -- `store.submit_review`
    stamps `record_review`'s draft comment with a `review_id`, which is what
    `store.unanswered` requires to count it at all (a draft is invisible to
    every reader but its own author)."""
    tid = await it.database.write(
        lambda c: store.create_thread(
            c, wid=it.id, gate=gate, anchor_sha="h", body=body, label=label
        )
    )
    await it.database.write(
        lambda c: store.submit_review(
            c, wid=it.id, gate=gate, outcome="comment", summary=None, head_sha="h", base_sha="h"
        )
    )
    return tid


async def test_a_gate_with_an_unanswered_must_fix_bounces_instead_of_opening(item_on):
    """A person's unanswered must-fix must never let the gate it targets open
    silently (review threads anywhere §1): the walk rejects it on their
    behalf, `by="kraft"`, and re-enters at its own `reject_to` -- before the
    gate is ever requested at all."""
    it = await item_on(
        [_exec("implementation"), {"id": "review", "kind": "gate", "reject_to": "implementation"}]
    )
    await _published_thread(it, gate="review", label="must_fix")

    status = await _walk(it, policy=_cap(2))

    evts = it.events()
    types = [e["type"] for e in evts]
    rejected = it.events("gate_rejected")
    assert rejected, "the gate must bounce, not open, while the must-fix is unanswered"
    first = rejected[0]
    assert first["payload"]["gate"] == "review"
    assert first["payload"]["by"] == "kraft"
    assert first["payload"]["verdict"] == "review_threads"
    starts = [e["payload"]["node_id"] for e in evts if e["type"] == "node_started"]
    assert starts.count("implementation") >= 2, "the reject target must rerun"
    first_reject_idx = types.index("gate_rejected")
    assert "gate_requested" not in types[:first_reject_idx], (
        "the gate opened before it ever bounced the unanswered must-fix"
    )
    assert status in ("awaiting_gate", "needs_human")


async def test_the_bounce_counts_against_the_reject_cap_and_then_opens(item_on):
    """Bounded by the gate's own reject-loop cap: once one more bounce would
    breach it, the gate opens for the person instead of bouncing forever."""
    it = await item_on(
        [_exec("implementation"), {"id": "review", "kind": "gate", "reject_to": "implementation"}]
    )
    await _published_thread(it, gate="review", label="must_fix")

    status = await _walk(it, policy=_cap(1))

    assert status == "awaiting_gate"
    rejected = it.events("gate_rejected")
    assert len(rejected) == 1, "exactly one bounce, then the cap stops it"
    requested = it.events("gate_requested")
    assert [r["payload"]["gate"] for r in requested] == ["review"]
    assert executor.pending_gate(it.database, it.id) == "review"


async def test_the_bounce_cap_check_uses_the_counters_own_snapshot_not_a_fresh_resolve(item_on):
    """The gate's reject-loop counter already exists with its own snapshotted
    cap (bumped once before, under an earlier policy.yaml). A later
    `kraft admin reload` can hand `bounce_on_feedback` a fresh, stricter cap
    for the same key -- `store.bump_counter`'s own invariant (its docstring)
    says a cap is fixed at first fire, so the pre-check must predict against
    the row's snapshot, not the freshly resolved value, the same way
    `apply_rejection` already does."""
    it = await item_on(
        [_exec("implementation"), {"id": "review", "kind": "gate", "reject_to": "implementation"}]
    )
    key = gates_module.reject_loop_key("review")
    # Seed one prior bounce, snapshotting a generous cap (5 attempts).
    await it.database.write(
        lambda c: store.bump_counter(c, it.id, key, _policy.Cap(attempts=5, wall_clock_s=3600))
    )
    await _published_thread(it, gate="review", label="must_fix")

    # The live policy has since been edited and reloaded to a much stricter
    # cap for the same key -- a fresh resolve of it would wrongly predict a
    # breach the row's own snapshot does not permit.
    status = await _walk(it, policy=_cap(1))

    rejected = it.events("gate_rejected")
    assert rejected, (
        "the row's snapshotted cap (5) still permits this bounce -- a fresh "
        "resolve of the reloaded policy's cap (1) must not veto it"
    )
    assert rejected[-1]["payload"]["by"] == "kraft"
    assert status in ("awaiting_gate", "needs_human")


async def test_a_gate_with_only_unanswered_questions_opens_with_the_reply_agent(
    item_on, tmp_path, monkeypatch
):
    """No must-fix, only an unanswered question: the gate opens as normal, and
    the reply agent -- the one that wrote the code -- is launched to answer
    it, under the gate's own `<gate>.reply` hook point."""
    it = await item_on(
        [
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [
                    {"id": "build", "kind": "agent", "harness": "fake", "prompt": "implement"}
                ],
            },
            {"id": "review", "kind": "gate", "reject_to": "implementation"},
        ]
    )
    fake_harness_home(tmp_path, ["true"])
    launched = []

    async def _fake_run_agent_task(_db, _rd, **kw):
        launched.append(kw)
        return "done"

    monkeypatch.setattr(agent_mod, "run_agent_task", _fake_run_agent_task)
    await _published_thread(it, gate="review", label="question")

    status = await _walk(it)

    assert status == "awaiting_gate"
    assert executor.pending_gate(it.database, it.id) == "review"
    reply_calls = [kw for kw in launched if kw["hook_point"] == "review.reply"]
    assert len(reply_calls) == 1, (
        "the reply agent must launch exactly once, the moment the gate opens with "
        "only unanswered questions/nits left"
    )


async def test_an_earlier_gates_must_fix_still_blocks_a_later_gate(item_on, script):
    """Review Focus 1: a must-fix filed against `spec_approval`, still open
    when the final `review` gate is reached, must never let `review` open
    silently. Unanswered -> `review` bounces instead of opening. Once an
    agent has replied (a `claim`, never a resolution) the thread is no longer
    "unanswered", so the next reach opens `review` normally -- and Task 3's
    own must-fix approval guard (`kraft.api.routes.gates.approve_gate`) still
    refuses to clear it."""
    it = await item_on(
        [
            _exec("spec", "write"),
            {"id": "spec_approval", "kind": "gate", "reject_to": "spec"},
            _exec("implementation", "build"),
            {"id": "review", "kind": "gate", "reject_to": "implementation"},
        ]
    )
    cap = _cap(5)
    assert await _walk(it, policy=cap) == "awaiting_gate"
    assert executor.pending_gate(it.database, it.id) == "spec_approval"

    # Approved with no must-fix present yet -- then the thread is filed
    # against it, unresolved and unanswered, exactly what Review Focus 1
    # describes.
    await it.database.write(lambda c: store.approve_gate(c, it.id, "spec_approval", by="human"))
    tid = await _published_thread(it, gate="spec_approval", label="must_fix")

    calls = {"n": 0}

    async def _agent_answers_the_second_time(row):
        calls["n"] += 1
        if calls["n"] == 2:
            await it.database.write(
                lambda c: store.agent_reply(
                    c, tid, author="agent", body="on it", claim="should_fix", attempt=1
                )
            )

    script.effects["build"] = _agent_answers_the_second_time
    nodes = it.chain.chain.nodes
    impl_index = executor.gate_node_index(nodes, "spec_approval") + 1

    status = await _walk(it, policy=cap, start_index=impl_index)

    assert status == "awaiting_gate"
    assert executor.pending_gate(it.database, it.id) == "review"
    assert calls["n"] == 2, "implementation must run once before the bounce and once after"

    rejected = it.events("gate_rejected")
    assert [r["payload"]["gate"] for r in rejected] == ["review"], (
        "review's unanswered must-fix must bounce it before it ever opens"
    )
    assert rejected[0]["payload"]["by"] == "kraft"
    assert rejected[0]["payload"]["verdict"] == "review_threads"

    # Half 2: claimed but unresolved -- the gate is open, but Task 3's own
    # must-fix guard still refuses to approve it.
    state = SimpleNamespace(db=it.database, tasks={})
    request = SimpleNamespace(app=SimpleNamespace(state=state), headers={})
    with pytest.raises(HTTPException) as exc:
        await gates_route.approve_gate(it.id, "review", request)
    assert exc.value.status_code == 409
    assert tid in str(exc.value.detail)
