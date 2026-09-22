"""An escalation turn on a stop that carries a `suggested_action` starts from
it (Kraft-s7c04.27): on 2026-09-15 a repair concluded "a human should skip"
and four escalation turns re-derived it instead."""

from __future__ import annotations

import json

import pytest
from support.harness import v1_chain, write_harness_profiles

from kraft import escalate, executor, store

_CHAIN = v1_chain(
    [
        {
            "id": "implementation",
            "kind": "exec",
            "tasks": [{"id": "work", "kind": "subprocess", "command": "true"}],
        }
    ],
    repo="/r",
)

SKIP = {"action": "skip", "reason": "the branch has no MR; a retry would re-push it"}


async def _instruction(monkeypatch, tmp_path, database, run_dirs, *, auto=False, paused=False):
    """The instruction one escalation turn on `w1` is handed: stopped for a
    human with a suggested skip, or paused after that."""
    templates = tmp_path / "templates"
    write_harness_profiles(templates, {"claude": {"provider": "claude"}})
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, task_instruction, **kw):
        seen["task_instruction"] = task_instruction
        log = run_dirs.logs / f"{session_id}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"type": "system", "subtype": "init", "session_id": "c"}) + "\n")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id=None,
            title="t",
            description="",
            repo=str(run_dirs.base),
            chain_template="quick-task",
            chain_definition="{}",
            materialized_chain=_CHAIN.to_json(),
        )
    )
    await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
    await database.write(
        lambda c: store.mark_needs_human(c, "w1", "implementation", "failed", suggested=SKIP)
    )
    if paused:
        await database.write(lambda c: store.pause_work_item(c, "w1", []))

    await escalate.dispatch(
        database,
        run_dirs,
        work_item_id="w1",
        message="please skip it",
        launch=executor.LaunchContext(repo_entry=None, skills_dir=None),
        auto=auto,
    )
    return seen["task_instruction"]


async def test_an_escalation_turn_is_told_the_stop_s_suggestion(
    monkeypatch, tmp_path, database, run_dirs
):
    instruction = await _instruction(monkeypatch, tmp_path, database, run_dirs)
    assert f"What Kraft suggests a person do: skip -- {SKIP['reason']}" in instruction


@pytest.mark.parametrize(
    ("auto", "paused"),
    [(False, False), (True, False), (False, True)],
    ids=["manual", "automatic", "paused"],
)
async def test_an_escalation_turn_hands_a_skip_to_the_person(
    monkeypatch, tmp_path, database, run_dirs, auto, paused
):
    """Ruling 209 (Kraft-s7c04.67): nothing pre-approves `kraft item skip` for
    an escalation agent, so every turn is told it may not skip or abandon the
    item itself, and to give the person the one command that does."""
    instruction = await _instruction(
        monkeypatch, tmp_path, database, run_dirs, auto=auto, paused=paused
    )
    assert ("This item is paused" in instruction) is paused
    assert "You are not allowed to skip or abandon this work item yourself" in instruction
    assert "`kraft item skip w1`" in instruction
    assert "`kraft item abandon --yes w1`" in instruction
