from __future__ import annotations

from support.store_fixtures import mk_item

from kraft import events, store


async def test_start_run_numbers_attempts_and_skips_reentry(database):
    await mk_item(database)
    first = await database.write(
        lambda c: store.start_run(c, "w1", "impl", start_sha="a", base_sha="b")
    )
    again = await database.write(
        lambda c: store.start_run(c, "w1", "impl", start_sha="a2", base_sha="b")
    )
    assert (first, again) == (1, None)  # unfinished run: a resume, not a new attempt
    await database.write(lambda c: store.finish_run(c, "w1", "impl", end_sha="c", dirty=False))
    second = await database.write(
        lambda c: store.start_run(c, "w1", "impl", start_sha="c", base_sha="b")
    )
    assert second == 2


async def test_finish_run_without_a_started_run_is_a_noop(database):
    await mk_item(database)
    got = await database.write(
        lambda c: store.finish_run(c, "w1", "impl", end_sha="c", dirty=False)
    )
    assert got is None
    assert database.read(lambda c: store.node_run_rows(c, "w1")) == []


async def test_pin_gate_dedupes_a_rerequest_at_the_same_head(database):
    """Review Focus 1: a restart re-requesting a pending gate is not attempt 2."""
    await mk_item(database)
    one = await database.write(
        lambda c: store.pin_gate(c, "w1", "review", sha="h1", base_sha="b", dirty=False)
    )
    same = await database.write(
        lambda c: store.pin_gate(c, "w1", "review", sha="h1", base_sha="b", dirty=False)
    )
    two = await database.write(
        lambda c: store.pin_gate(c, "w1", "review", sha="h2", base_sha="b", dirty=False)
    )
    assert (one, same, two) == (1, None, 2)
    attempts = database.read(lambda c: store.gate_attempts(c, "w1", "review"))
    assert [(a["n"], a["sha"]) for a in attempts] == [(1, "h1"), (2, "h2")]


async def test_pending_rewind_newest_wins_and_is_spent_by_the_target_starting(database):
    """Review Focus 3: a second request replaces the first."""
    await mk_item(database)
    await database.write(
        lambda c: store.request_rewind(c, "w1", review_id="r1", target="impl", note="one")
    )
    await database.write(
        lambda c: store.request_rewind(c, "w1", review_id="r2", target="impl", note="two")
    )
    assert database.read(lambda c: store.pending_rewind(c, "w1"))["note"] == "two"
    await database.write(lambda c: store.enter_node(c, "w1", "impl"))
    assert database.read(lambda c: store.pending_rewind(c, "w1")) is None


async def test_a_cancelled_rewind_is_not_pending(database):
    await mk_item(database)
    await database.write(
        lambda c: store.request_rewind(c, "w1", review_id="r1", target="impl", note="x")
    )
    await database.write(lambda c: store.cancel_rewind(c, "w1", "r1"))
    assert database.read(lambda c: store.pending_rewind(c, "w1")) is None


async def _thread(database, **kw):
    args = dict(wid="w1", gate="review", anchor_sha="h1", body="fix it")
    args.update(kw)
    return await database.write(lambda c: store.create_thread(c, **args))


async def test_a_new_thread_is_a_draft_until_submitted(database):
    await mk_item(database)
    tid = await _thread(
        database, label="must_fix", file_path="a.py", side="new", start_line=3, end_line=4
    )
    assert database.read(lambda c: store.is_draft_thread(c, tid))
    rid = await database.write(
        lambda c: store.submit_review(
            c, wid="w1", gate="review", outcome="comment", summary=None, head_sha="h1", base_sha="b"
        )
    )
    assert not database.read(lambda c: store.is_draft_thread(c, tid))
    [t] = database.read(lambda c: store.threads_for(c, "w1"))
    assert t["comments"][0]["review_id"] == rid and t["draft"] is False
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
    assert "review_submitted" in types


async def test_submit_stamps_every_draft_on_the_item(database):
    """Threads belong to the item: one submission publishes every draft of yours."""
    await mk_item(database)
    here = await _thread(database)
    mid_run = await _thread(database, gate=None)
    await database.write(
        lambda c: store.submit_review(
            c, wid="w1", gate="review", outcome="comment", summary=None, head_sha="h", base_sha="b"
        )
    )
    assert not database.read(lambda c: store.is_draft_thread(c, here))
    assert not database.read(lambda c: store.is_draft_thread(c, mid_run))


async def test_agent_claim_moves_state_and_reply_without_claim_does_not(database):
    await mk_item(database)
    tid = await _thread(database)
    await database.write(
        lambda c: store.agent_reply(
            c, tid, author="implementation", body="why?", claim=None, attempt=2
        )
    )
    assert database.read(lambda c: store.thread_row(c, tid))["state"] == "open"
    await database.write(
        lambda c: store.agent_reply(
            c, tid, author="implementation", body="done", claim="fixed", attempt=2
        )
    )
    assert database.read(lambda c: store.thread_row(c, tid))["state"] == "claimed"
    await database.write(
        lambda c: store.agent_reply(
            c, tid, author="implementation", body="also", claim=None, attempt=2
        )
    )
    last = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
    assert last["type"] == "thread_updated" and last["payload"]["state"] == "claimed"


async def test_agent_claim_does_not_reopen_a_resolved_thread(database):
    await mk_item(database)
    tid = await _thread(database)
    await database.write(lambda c: store.set_thread_state(c, tid, "resolved"))
    await database.write(
        lambda c: store.agent_reply(
            c, tid, author="implementation", body="done", claim="fixed", attempt=1
        )
    )
    assert database.read(lambda c: store.thread_row(c, tid))["state"] == "resolved"
    [thread] = database.read(lambda c: store.threads_for(c, "w1", "review"))
    assert thread["comments"][-1]["body"] == "done" and thread["comments"][-1]["claim"] == "fixed"
    last = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
    assert last["type"] == "thread_updated" and last["payload"]["state"] == "resolved"


async def test_open_must_fix_counts_drafts_and_skips_resolved(database):
    """Review Focus 4 at the store level."""
    await mk_item(database)
    draft = await _thread(database, label="must_fix")
    done = await _thread(database, label="must_fix")
    await _thread(database, label="nit")
    await database.write(lambda c: store.set_thread_state(c, done, "resolved"))
    assert database.read(lambda c: store.open_must_fix(c, "w1")) == [draft]


async def test_a_must_fix_filed_at_any_gate_or_none_blocks(database):
    await mk_item(database)
    elsewhere = await _thread(database, gate="spec_approval", label="must_fix")
    mid_run = await _thread(database, gate=None, label="must_fix")
    assert database.read(lambda c: store.open_must_fix(c, "w1")) == [elsewhere, mid_run]


async def test_unanswered_is_threads_whose_last_word_is_yours(database):
    await mk_item(database)
    answered = await _thread(database)
    waiting = await _thread(database)
    await database.write(
        lambda c: store.submit_review(
            c, wid="w1", gate="review", outcome="comment", summary=None, head_sha="h", base_sha="b"
        )
    )
    await database.write(
        lambda c: store.agent_reply(
            c, answered, author="implementation", body="ok", claim="answered", attempt=1
        )
    )
    got = database.read(lambda c: store.unanswered(c, "w1"))
    assert [t["id"] for t in got] == [waiting]


async def test_unanswered_includes_a_claimed_thread_after_a_new_human_reply(database):
    """A thread the reply agent already claimed `should_fix` still needs an
    answer once the human replies again and submits a fresh comment review."""
    await mk_item(database)
    tid = await _thread(database)
    await database.write(
        lambda c: store.submit_review(
            c, wid="w1", gate="review", outcome="comment", summary=None, head_sha="h", base_sha="b"
        )
    )
    await database.write(
        lambda c: store.agent_reply(
            c, tid, author="implementation", body="agreed", claim="should_fix", attempt=1
        )
    )
    assert database.read(lambda c: store.thread_row(c, tid))["state"] == "claimed"
    assert database.read(lambda c: store.unanswered(c, "w1")) == []

    await database.write(lambda c: store.add_draft_reply(c, tid, body="still broken"))
    await database.write(
        lambda c: store.submit_review(
            c, wid="w1", gate="review", outcome="comment", summary=None, head_sha="h2", base_sha="b"
        )
    )
    got = database.read(lambda c: store.unanswered(c, "w1"))
    assert [t["id"] for t in got] == [tid]


def test_render_note_lists_unresolved_threads_with_suggestions_and_replies():
    threads = [
        {
            "id": "t1",
            "file_path": "a.py",
            "start_line": 3,
            "end_line": 4,
            "label": "must_fix",
            "state": "open",
            "comments": [
                {
                    "author": "you",
                    "body": "use a set",
                    "suggestion": {"start_line": 3, "end_line": 3, "replacement": "s = set()"},
                },
                {"author": "implementation", "body": "a list is intentional", "suggestion": None},
            ],
        },
        {
            "id": "t2",
            "file_path": None,
            "start_line": None,
            "end_line": None,
            "label": None,
            "state": "resolved",
            "comments": [{"author": "you", "body": "gone", "suggestion": None}],
        },
    ]
    note = store.render_note(threads, "Two things.")
    assert note.startswith("Two things.")
    assert "[t1] a.py:3-4 (must_fix)" in note
    assert "s = set()" in note and "> implementation: a list is intentional" in note
    assert "kraft item reply t1" not in note  # the footer names the verb once, generically
    assert "kraft item reply <thread-id>" in note
    assert "t2" not in note


def test_render_threads_names_a_range_across_sides_and_quotes_its_lines():
    """What a worker reads: a removed line through its replacement is `-2 to +2`,
    with the lines as the reviewer saw them ahead of the comment. A thread with
    no `start_side` or quote, as every older one, reads as before."""

    def thread(tid, **kw):
        t = {
            "id": tid,
            "file_path": "calc.py",
            "side": "new",
            "start_line": 2,
            "end_line": 2,
            "label": "must_fix",
            "state": "open",
            "comments": [{"author": "you", "body": "keep the sign", "suggestion": None}],
        }
        return t | kw

    across = thread("t1", start_side="old", quote="-    return a - b\n+    return a + b")
    older = thread("t2", start_line=3, end_line=4)
    out = store.render_threads([across, older])
    assert (
        "[t1] calc.py:-2 to +2 (must_fix)\n"
        "    | -    return a - b\n"
        "    | +    return a + b\n"
        "keep the sign"
    ) in out
    assert "[t2] calc.py:3-4 (must_fix)\nkeep the sign" in out
