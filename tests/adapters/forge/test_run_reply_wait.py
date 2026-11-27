"""The review wait of a reply round (`run_task(reply=)`): it waits for the answers in its threads,
each bounded by its own deadline, never for the head's reviewers, never stopping the item for its
deadline. Through `forge.run_task` against a `FakeForge`."""

# ruff: noqa: F811 -- `on_branch` is an imported fixture, taken by name.

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

from support.bot_threads import VIEWER, bot_thread, kraft_note, note
from support.review_rig import bot_on

from kraft import item_paths, waits
from kraft.adapters import forge
from kraft.adapters.forge.replies import owed, record

from .test_run_push import on_branch  # noqa: F401 -- the fixture

_WAIT = "feedback.settle.review"
_TIMEOUT = timedelta(minutes=5)


def _fake(monkeypatch, *answers, at=0, **kw):
    """A forge whose thread T1 Kraft answered at `at` seconds past the clock's start, then
    `answers`: seconds past the start the bot replied at."""
    notes = [
        note("N1", "a", "fix", at=_clock(at - 10)),
        kraft_note("N2", "w1", at=_clock(at)),
        *(note(f"R{s}", "a", "fine", at=_clock(s)) for s in answers),
    ]
    raw, _ = bot_thread("T1", *notes)
    fake = forge.FakeForge(
        feedback_script=[[forge.ReviewerFeedback(bot_on(), None, (), (raw,))]], **kw
    )

    async def viewer(*, repo):
        return VIEWER

    monkeypatch.setattr(fake, "viewer", viewer)
    return fake


def _clock(seconds: int) -> str:
    from datetime import UTC, datetime

    return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)).isoformat()


def _ask(repo, reply=True, **kw):
    return {
        "repo": repo,
        "orig_repo": repo,
        "node_id": "feedback",
        "hook_point": _WAIT,
        "reviewers": (bot_on(),),
        "reads_feedback": True,
        "grace": timedelta(seconds=15),
        "wait": forge.WaitBounds.from_seconds(timeout=600, initial=30, maximum=120),
        **({"reply": owed.ReplyWait(("T1",), _TIMEOUT)} if reply else {}),
        **kw,
    }


def _ended(run_forge):
    return [e["outcome"] for e in run_forge.events("external_wait_ended")]


async def test_the_reply_wait_waits_then_settles(run_forge, on_branch, monkeypatch, wait_clock):
    """One silent thread, Kraft's note at 20s: waiting until its deadline (5 minutes on),
    then done. The observation is `settled`, never `timed_out`, so no stop."""
    fake = _fake(monkeypatch, at=20)
    ask = _ask(on_branch)

    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "waiting"
    started = run_forge.database.read(lambda c: waits.open_wait(c, "w1", _WAIT)).started
    assert started["deadline"] == wait_clock.at(300), "the thread's 320s, clamped to now + 5 min"
    wait_clock.advance(300)
    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "waiting"
    assert _ended(run_forge) == []
    wait_clock.advance(30)

    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "done"
    assert _ended(run_forge) == ["settled"]


async def test_the_reply_wait_ignores_the_push_deadline(
    run_forge, on_branch, monkeypatch, wait_clock
):
    """The push is long past (a head wait would be settled by its timeout), and the
    thread's deadline is not."""
    wait_clock.advance(400 * 86400)
    base = 400 * 86400
    fake = _fake(monkeypatch, at=base + 20)
    ask = _ask(on_branch, watch_ci=True, ci_task="feedback.ci.await_ci")

    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "waiting"

    assert _ended(run_forge) == []


async def test_the_reply_wait_records_no_settled_review(
    run_forge, on_branch, monkeypatch, wait_clock
):
    fake = _fake(monkeypatch, 40, at=20)

    assert (await run_forge(fake, "automated_review", "s1", **_ask(on_branch)))[0] == "done"

    assert run_forge.events("automated_review_settled") == []


async def test_the_reply_wait_writes_the_collection_with_read_at(
    run_forge, on_branch, monkeypatch, wait_clock
):
    fake = _fake(monkeypatch, 40, at=20)
    wait_clock.advance(60)

    assert (await run_forge(fake, "automated_review", "s1", **_ask(on_branch)))[0] == "done"

    folder = item_paths.session_files(run_forge.run_dirs, "w1", "s1").folder
    body = json.loads((folder / "bot_feedback.json").read_text())
    [entry] = body["reviewers"]
    assert body["read_at"] == wait_clock.at(60)
    assert [n["by"] for n in entry["threads"][0]["notes"]] == ["bot", "kraft", "bot"]
    assert entry["finished"] == "reply"


async def test_the_reply_wait_still_pushes_and_reads_ci_with_watch_ci(
    run_forge, on_branch, monkeypatch, wait_clock
):
    fake = _fake(monkeypatch, at=20, ci_states=["pending"])
    ask = _ask(on_branch, watch_ci=True, ci_task="feedback.ci.await_ci")

    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "waiting"

    assert run_forge.events("branch_pushed")
    assert fake.pipeline_ids_requested


async def test_an_ordinary_wait_never_waits_for_a_reply_deadline(
    run_forge, on_branch, monkeypatch, wait_clock
):
    """The same feedback with no round: the bot has shown no activity since the push, so
    the head wait is pending on its own terms (not the thread's deadline), and the thread
    leaves the settled collection's finished marker `activity`/`timeout`, never `reply`."""
    fake = _fake(monkeypatch, 40, at=20)
    ask = _ask(on_branch, reply=False)
    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "waiting"
    wait_clock.advance(700)

    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "done"

    folder = item_paths.session_files(run_forge.run_dirs, "w1", "s1").folder
    [entry] = json.loads((folder / "bot_feedback.json").read_text())["reviewers"]
    assert entry["finished"] == "timeout"


async def test_a_bot_resolving_a_thread_after_fixed_in_does_not_extend_the_next_heads_wait(
    run_forge, on_branch, monkeypatch, wait_clock
):
    """Kraft posts in T1 between two checks of a head wait (the record knows, the first read
    shows no Kraft note) and the bot then resolves it: the thread leaving the read is not bot
    activity, so the wait settles on the bot's quiet. Dropping the record's threads from the
    exclusion would read it as activity and keep waiting."""
    raw, _ = bot_thread("T1", note("N1", "a", "fix", at=_clock(10)))
    seen = forge.ReviewerFeedback(bot_on(), wait_clock.start + timedelta(seconds=10))
    fake = forge.FakeForge(
        feedback_script=[
            [replace(seen, threads=(raw,))],
            [seen],
        ]
    )

    async def viewer(*, repo):
        return VIEWER

    monkeypatch.setattr(fake, "viewer", viewer)
    ask = _ask(on_branch, reply=False)
    wait_clock.advance(20)
    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "waiting"

    iid = record.new_id("w1")
    posted = forge.Posted(("1",), _clock(25), "Fixed in abc1234:\n\nRenamed it.")

    def kraft_posted(c):
        record.intend(
            c,
            "w1",
            iid,
            {
                "op": "reply",
                "node_id": "feedback",
                "thread": "T1",
                "kind": "fixed",
                "body_sha256": "x" * 64,
            },
        )
        record.posted(c, "w1", iid, posted, via="send")

    await run_forge.database.write(kraft_posted)
    wait_clock.advance(40)

    assert (await run_forge(fake, "automated_review", "s1", **ask))[0] == "done"
