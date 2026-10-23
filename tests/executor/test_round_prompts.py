"""What the review brief is told about the review-bot feedback the triage did
not act on (`AgentInput.TRIAGE_VERDICT`): where the triage's latest verdict is,
by path, or nothing when no triage has judged anything."""

from __future__ import annotations

import pytest
from support.harness import v1_node, v1_task

from kraft.executor import prompts


def _chain(*, triage: bool) -> list[dict]:
    """A node whose review wait is followed by the triage (when `triage`), then the brief's."""
    wait = {"id": "wait", "kind": "forge", "target": "mr.automated_review"}
    reading = v1_task("triage", kind="agent", inputs=["bot_feedback"]) if triage else None
    steps = [{"id": "settle", "tasks": [wait]}]
    if reading:
        steps.append({"id": "decide", "tasks": [reading]})
    return [
        v1_node("feedback", steps=steps),
        v1_node("brief", tasks=[v1_task("write", kind="agent")]),
    ]


@pytest.mark.parametrize(
    "triage, sessions, says",
    [
        # A triage ran: the brief is pointed at what it decided.
        (True, [("t0", "abc123")], True),
        # Only a skip (no head) has run: nothing was judged, so nothing to tell.
        (True, [("t0", None)], False),
        # A real verdict, then a skip on a later round: the real one is still the verdict.
        (True, [("t0", "abc123"), ("t1", None)], True),
        # The chain has no triage step at all.
        (False, [], False),
    ],
    ids=["a-triage-ran", "only-a-skip-row", "a-skip-after-a-verdict", "chain-without-triage"],
)
async def test_the_brief_is_told_where_the_triage_verdict_is(item_on, triage, sessions, says):
    it = await item_on(_chain(triage=triage))
    for sid, head in sessions:
        await it.session(sid, "feedback.decide.triage", "done", head_sha=head)
        await it.database.write(
            lambda c, sid=sid: c.execute(
                "UPDATE worker_sessions SET session_summary_ref = ? WHERE id = ?",
                (f"summaries/{sid}.md", sid),
            )
        )
    first_verdict = next((sid for sid, head in sessions if head), None)

    note = prompts.triage_verdict_note(it.database, it.id, it.row(), run_dirs=it.run_dirs)

    assert bool(note) is says
    if says:
        [verdict] = [s for s in it.sessions("feedback") if s["id"] == first_verdict]
        assert verdict["result_path"] in note
        assert f"summaries/{first_verdict}.md" in note
