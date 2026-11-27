"""The review wait's collect path with a bot that takes Kraft's replies: the
login Kraft writes as is read once per wait, and the collection marks Kraft's
own notes. Through `forge.run_task` against a `FakeForge`."""

# ruff: noqa: F811 -- `on_branch` is an imported fixture, taken by name.

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from support.bot_threads import VIEWER, bot_thread, kraft_note, note

from kraft import item_paths, waits
from kraft.adapters import forge
from kraft.adapters.forge.models import Posted
from kraft.adapters.forge.replies import record
from kraft.automated_review import BotReviewer

from .test_run_push import on_branch  # noqa: F401 -- the fixture

_WAIT = "feedback.settle.review"


async def _two_checks(run_forge, on_branch, monkeypatch, wait_clock, reviewer):
    """Two checks 30s apart, the bot active at 10s and quiet past a 15s grace by
    the second: what the wait kept after the first, how often the viewer was
    read, and the collection the second wrote."""
    raw, _ = bot_thread("T1", note("N1", "a", "fix"), kraft_note("N2", "w1"))
    active = datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC)
    fake = forge.FakeForge(feedback_script=[[forge.ReviewerFeedback(reviewer, active, (), (raw,))]])
    reads = []

    async def viewer(*, repo):
        reads.append(repo)
        return VIEWER

    monkeypatch.setattr(fake, "viewer", viewer)
    ask = {
        "repo": on_branch,
        "orig_repo": on_branch,
        "node_id": "feedback",
        "hook_point": _WAIT,
        "reviewers": (reviewer,),
        "reads_feedback": True,
        "grace": timedelta(seconds=15),
        "wait": forge.WaitBounds.from_seconds(timeout=600, initial=30, maximum=120),
    }

    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "waiting"
    kept = run_forge.database.read(lambda c: waits.open_wait(c, "w1", _WAIT)).last
    wait_clock.advance(30)
    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "done"
    folder = item_paths.session_files(run_forge.run_dirs, "w1", "s1").folder
    [entry] = json.loads((folder / "bot_feedback.json").read_text())["reviewers"]
    return kept, len(reads), [n["by"] for n in entry["threads"][0]["notes"]]


async def test_a_replies_on_wait_reads_the_viewer_once_and_marks_kraft_notes(
    run_forge, on_branch, monkeypatch, wait_clock
):
    reviewer = BotReviewer(bot="a", replies=True)

    kept, reads, by = await _two_checks(run_forge, on_branch, monkeypatch, wait_clock, reviewer)

    assert (kept["viewer"], reads, by) == (VIEWER, 1, ["bot", "kraft"])


async def test_a_replies_off_wait_never_reads_the_viewer(
    run_forge, on_branch, monkeypatch, wait_clock
):
    kept, reads, by = await _two_checks(
        run_forge, on_branch, monkeypatch, wait_clock, BotReviewer(bot="a")
    )

    assert ("viewer" in kept, reads, by) == (False, 0, ["bot", "person"])


async def test_a_bot_resolving_a_thread_kraft_replied_in_does_not_settle_the_wait(
    run_forge, on_branch, monkeypatch, wait_clock
):
    """Kraft posts in T1 between two checks (the thread shows no Kraft note), then the bot
    resolves it: the thread leaving the collection is not the bot reviewing the push, so
    the wait is still waiting once the grace period has passed."""
    reviewer = BotReviewer(bot="a", replies=True)
    raw, _ = bot_thread("T1", note("N1", "a", "fix"))
    open_ = [forge.ReviewerFeedback(reviewer, None, (), (raw,))]
    gone = [forge.ReviewerFeedback(reviewer, None, (), ())]
    fake = forge.FakeForge(feedback_script=[open_, gone, gone])

    async def viewer(*, repo):
        return VIEWER

    monkeypatch.setattr(fake, "viewer", viewer)
    ask = {
        "repo": on_branch,
        "orig_repo": on_branch,
        "node_id": "feedback",
        "hook_point": _WAIT,
        "reviewers": (reviewer,),
        "reads_feedback": True,
        "grace": timedelta(seconds=15),
        "wait": forge.WaitBounds.from_seconds(timeout=600, initial=30, maximum=120),
    }
    db = run_forge.database

    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "waiting"
    intent = record.new_id("w1")
    payload = {
        "op": "reply",
        "node_id": "feedback",
        "repo": str(on_branch),
        "thread": "T1",
        "kind": "declined",
        "body_sha256": "0" * 64,
        "head": "abc123",
        "round": 0,
        "session": "s1",
    }
    await db.write(lambda c: record.intend(c, "w1", intent, payload))
    await db.write(
        lambda c: record.posted(c, "w1", intent, Posted(("1", "N1"), "t", "b"), via="send")
    )
    wait_clock.advance(30)
    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "waiting"
    wait_clock.advance(30)

    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "waiting"
