"""What an escalation turn is told of the fix-loop judge's standing verdict."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_escalate import _seed_needs_human, escalation_profile  # noqa: F401 -- autouse

from kraft import escalate, events, executor
from kraft.db import Database
from kraft.paths import RunDirs


def _dispatch_with(monkeypatch, tmp_path, seed_events):
    """Seed a needs_human item plus `seed_events`, escalate, return the prompt."""
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, cwd, task_instruction, **kw):
        seen["task_instruction"] = task_instruction
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "cli-abc"}) + "\n"
        )
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            await _seed_needs_human(database, rd, "w1")
            for etype, payload in seed_events:
                await database.write(lambda c, e=etype, pl=payload: events.append(c, "w1", e, pl))
            await escalate.dispatch(
                database,
                rd,
                work_item_id="w1",
                message="have a look",
                launch=executor.LaunchContext(repo_entry=None, skills_dir=None),
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    return seen["task_instruction"]


def _verdict(reasoning, node_id="implementation", verdict="stop_needs_human"):
    return ("judge_verdict", {"node_id": node_id, "verdict": verdict, "reasoning": reasoning})


def test_an_escalation_carries_the_standing_judge_verdict(tmp_path, monkeypatch):
    """Kraft-s7c04.5: on 43717ee6 the judge named the correct root cause and fix
    shape at 14:07, no prompt carried it, and the escalation re-derived the same
    conclusion 17 minutes and $3.72 later."""
    prompt = _dispatch_with(monkeypatch, tmp_path, [_verdict("the retry path is never awaited")])
    assert "the retry path is never awaited" in prompt


@pytest.mark.parametrize(
    "closing",
    [
        ("work_item_retried", {}),
        ("gate_approved", {"gate": "spec_approval"}),
        ("gate_rejected", {"gate": "spec_approval", "note": "no"}),
    ],
    ids=["retried", "gate-approved", "gate-rejected"],
)
def test_a_judge_verdict_from_before_the_last_gate_is_not_carried(tmp_path, monkeypatch, closing):
    """A verdict from an episode a gate or a retry has already closed describes
    a trend that is over."""
    prompt = _dispatch_with(monkeypatch, tmp_path, [_verdict("ancient history"), closing])
    assert "ancient history" not in prompt
    assert "fix-loop judge" not in prompt


def test_a_judge_verdict_with_no_reasoning_adds_no_judge_line(tmp_path, monkeypatch):
    """A verdict the judge gave no reason for has nothing to hand on: no line
    introducing an empty explanation."""
    prompt = _dispatch_with(monkeypatch, tmp_path, [_verdict("   ")])
    assert "fix-loop judge" not in prompt


def test_another_nodes_judge_verdict_is_not_carried(tmp_path, monkeypatch):
    """judge_verdict payloads name their node; without filtering on it an
    earlier node's fix-loop verdict lands in an escalation about a later one."""
    prompt = _dispatch_with(monkeypatch, tmp_path, [_verdict("about verify", node_id="verify")])
    assert "about verify" not in prompt


def test_no_judge_verdict_means_no_judge_line(tmp_path, monkeypatch):
    prompt = _dispatch_with(monkeypatch, tmp_path, [])
    assert "fix-loop judge" not in prompt
