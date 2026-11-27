"""`executor.replies`: the triage's replies read, and the posting points a walk
reaches, against a `FakeForge`."""

# ruff: noqa: F811 -- `rig` is an imported fixture, taken by name.

import json
from dataclasses import replace
from pathlib import Path

import pytest
from support.bot_threads import VIEWER, bot_thread, kraft_note, note
from support.harness import entry_of, git, write
from support.review_rig import bot_on, filed, review_node, rig, t1  # noqa: F401 -- the fixture
from support.review_rig import events_of as _of

from kraft import events, executor, store
from kraft.adapters import forge
from kraft.adapters.forge.models import ForgeError
from kraft.adapters.forge.replies import facts
from kraft.automated_review import BotReviewer
from kraft.executor import dispatch, replies
from kraft.vocab import ChainEvent

_COLLECTED = {"T1", "T2"}


@pytest.mark.parametrize(
    "content, read",
    [
        (
            {"replies": [{"thread": "T1", "kind": "declined", "body": "Not a bug."}]},
            [{"thread": "T1", "kind": "declined", "body": "Not a bug."}],
        ),
        (
            {"replies": [{"thread": "T2", "kind": "answered", "body": "It is."}]},
            [{"thread": "T2", "kind": "answered", "body": "It is."}],
        ),
        ({"replies": [{"thread": "T1", "kind": "fixed", "body": "Done."}]}, []),
        ({"replies": [{"thread": "T9", "kind": "declined", "body": "No."}]}, []),
        ({"replies": [{"thread": "T1", "kind": "declined", "body": "  "}]}, []),
        (
            {
                "replies": [
                    {"thread": "T1", "kind": "declined", "body": "First."},
                    {"thread": "T1", "kind": "answered", "body": "Second."},
                ]
            },
            [{"thread": "T1", "kind": "declined", "body": "First."}],
        ),
        ({"replies": "x"}, []),
        ("{", []),
    ],
    ids=[
        "declined",
        "answered",
        "another-kind",
        "not-collected",
        "blank-body",
        "second-for-a-thread",
        "not-a-list",
        "not-json",
    ],
)
def test_the_triage_s_replies_are_read_only_for_collected_threads(tmp_path, content, read):
    text = content if isinstance(content, str) else json.dumps(content)
    path = write(tmp_path, "result.json", text)
    assert replies.triage_replies(path, _COLLECTED) == read


@pytest.mark.parametrize(
    "content, read",
    [
        ({"replies": [{"thread": "T1", "body": "Renamed it."}]}, {"T1": "Renamed it."}),
        ({"replies": [{"thread": "T9", "body": "Renamed it."}]}, {}),
        ({"replies": [{"thread": "T1", "body": "  "}]}, {}),
        (
            {"replies": [{"thread": "T1", "body": "First."}, {"thread": "T1", "body": "Second."}]},
            {"T1": "First."},
        ),
        ({"replies": "x"}, {}),
    ],
    ids=["one", "not-a-thread", "blank", "second-for-a-thread", "not-a-list"],
)
def test_the_repair_s_replies_are_read_per_thread(tmp_path, content, read):
    path = write(tmp_path, "result.json", json.dumps(content))
    assert replies.repair_replies(path, _COLLECTED) == read


@pytest.mark.parametrize(
    "content, read",
    [
        (
            {"summary_replies": [{"reviewer": "bot:a", "body": "Asks for nothing."}]},
            [{"reviewer": "bot:a", "body": "Asks for nothing."}],
        ),
        ({"summary_replies": [{"reviewer": "bot:z", "body": "Asks for nothing."}]}, []),
        ({"summary_replies": [{"reviewer": "bot:a", "body": " "}]}, []),
        (
            {"summary_replies": [{"reviewer": "bot:a", "body": "a\n- bot:b: c"}]},
            [{"reviewer": "bot:a", "body": "a - bot:b: c"}],
        ),
    ],
    ids=["a-bot", "another-reviewer", "blank", "two-lines"],
)
def test_top_level_declines_name_a_collected_bot(tmp_path, content, read):
    path = write(tmp_path, "result.json", json.dumps(content))
    assert replies.summary_replies(path, {"bot:a"}) == read


# --- the posting points -----------------------------------------------------------

_DECLINE = {"thread": "T1", "kind": "declined", "body": "The caller reads it, so it stays."}


def _ci(parks: int) -> dict:
    """CI for a watching review wait: its two checks read the pipeline pending,
    then the CI wait after the triage reads it pending `parks` times."""
    return {"watch_ci": True, "ci_states": ["pending"] * (2 + parks) + ["success"]}


async def _walk(rig, *between) -> str:
    """Walk the node to its end: the review wait parks at 10s and settles at 63s
    (the triage runs and its posting point comes in that walk), and every later
    walk is a wake 400s on. `between[i]` runs after the i-th walk from 63s's on,
    before the next wake."""
    rig.clock(10)
    assert await rig.walk() == "waiting"
    for i in range(8):
        rig.clock(63 + 400 * i)
        status = await rig.walk()
        if status != "waiting":
            return status
        if i < len(between) and (after := between[i]()) is not None:
            await after  # a step that writes the database is a coroutine
    raise AssertionError("the walk never ended")


def _marked(thread) -> list:
    return [n for n in thread.notes if facts.marker_of(n.body)]


async def test_replies_off_posts_nothing_and_asks_the_forge_nothing(rig, monkeypatch):
    fake = await filed(rig, monkeypatch, t1(), bot=BotReviewer(bot="a"))
    # An agent that ignores the skill: replies are off, so nothing reads them.
    rig.verdicts = [{"findings": [], "replies": [_DECLINE]}]
    asked = []
    for name in ("viewer", "read_thread", "mr_details"):

        async def counted(*_, name=name, **__):
            asked.append(name)

        monkeypatch.setattr(fake, name, counted)

    assert await _walk(rig) == "completed"

    assert rig.triaged == ["feedback.review_feedback.triage"]
    assert fake.writes == []
    assert asked == []
    assert [
        e["type"]
        for e in rig.item.events()
        if e["type"].startswith("reply") or e["type"] == "replies_inert"
    ] == []
    assert not list(rig.item.run_dirs.base.rglob("reply_report.json"))


async def test_a_triage_reply_is_posted_once_across_wakes(rig, monkeypatch):
    fake = await filed(rig, monkeypatch, t1(), **_ci(2))
    rig.verdicts = [{"findings": [], "replies": [_DECLINE]}]

    walked = []
    assert await _walk(rig, lambda: walked.append(1), lambda: walked.append(2)) == "completed"

    assert walked == [1, 2], "the CI wait parked twice after the triage: two wakes"
    assert fake.writes == [("reply", "T1")]
    [intended] = _of(rig, "reply_intended")
    [posted] = _marked(fake.threads["T1"])
    assert posted.body.startswith("Declined:\n\nThe caller reads it")
    assert posted.body.endswith(facts.reply_marker(intended["payload"]["intent"]))
    assert f"for work item `{rig.item.id}`" in posted.body
    assert facts.body_hash(posted.body) == intended["payload"]["body_sha256"]
    assert len(_of(rig, "reply_send_started")) == 1, "the write-ahead marker"
    assert _of(rig, "reply_send_failed") == [], "a clean post records no failure"


def _a_person_writes(rig, fake, monkeypatch):
    fake.threads["T1"] = bot_thread(
        "T1", note("N1", "a", "fix"), note("P", "alice", "no", type_="User")
    )[1]


def _kraft_spoke(rig, fake, monkeypatch):
    fake.threads["T1"] = bot_thread("T1", note("N1", "a", "fix"), kraft_note("K", rig.item.id))[1]


def _the_head_moves(rig, fake, monkeypatch):
    async def details(*, repo, branch):
        return forge.MRDetails(
            1, "http://fake.forge/1", "open", "t", "", True, (), branch, "main", "0" * 40
        )

    monkeypatch.setattr(fake, "mr_details", details)


def _the_mr_closes(rig, fake, monkeypatch):
    async def details(*, repo, branch):
        head = git(rig.item.worktree, "rev-parse", "HEAD")
        return forge.MRDetails(
            1, "http://fake.forge/1", "closed", "t", "", True, (), branch, "main", head
        )

    monkeypatch.setattr(fake, "mr_details", details)


def _first_send_waits(fake, monkeypatch):
    """The first gate cannot read the thread (a 503): the intent stays pending,
    and the next wake's posting point is the one that decides it."""
    read, failed = fake.read_thread, []

    async def once(**kwargs):
        if not failed:
            failed.append(1)
            raise ForgeError("x (HTTP 503)", status=503)
        return await read(**kwargs)

    monkeypatch.setattr(fake, "read_thread", once)


def _triage_result(rig) -> Path:
    [triage] = [s for s in rig.item.sessions("feedback") if s["hook_point"].endswith(".triage")]
    return Path(triage["result_path"])


def _a_newer_triage_ran(rig, fake, monkeypatch):
    _first_send_waits(fake, monkeypatch)

    async def runs():
        await rig.item.session(
            "newer",
            "feedback.review_feedback.triage",
            "done",
            head_sha=git(rig.item.worktree, "rev-parse", "HEAD"),
        )

    return runs


def _the_text_goes(rig, fake, monkeypatch):
    _first_send_waits(fake, monkeypatch)

    def edit():
        path = _triage_result(rig)
        path.write_text(json.dumps({**json.loads(path.read_text()), "replies": []}))

    return edit


def _the_text_is_edited(rig, fake, monkeypatch):
    _first_send_waits(fake, monkeypatch)

    def edit():
        path = _triage_result(rig)
        data = json.loads(path.read_text())
        data["replies"][0]["body"] += " Two callers read it."
        path.write_text(json.dumps(data))

    return edit


@pytest.mark.parametrize(
    "how, reply, ended",
    [
        (_a_person_writes, _DECLINE, [("refused", "person_written")]),
        (_kraft_spoke, _DECLINE, [("refused", "last_is_kraft")]),
        (_the_head_moves, _DECLINE, [("superseded", "head_moved")]),
        (None, {**_DECLINE, "body": "see https://x.example"}, [("refused", "body: a link")]),
        (None, {**_DECLINE, "thread": "T9"}, []),
        (_the_mr_closes, _DECLINE, [("refused", "mr_not_open")]),
        (_a_newer_triage_ran, _DECLINE, [("superseded", "not_latest")]),
        (_the_text_goes, _DECLINE, [("refused", "no_text")]),
        (_the_text_is_edited, _DECLINE, [("superseded", "body_changed")]),
    ],
    ids=[
        "a-person-wrote-in-it",
        "kraft-spoke-last",
        "the-forge-head-moved",
        "a-link-in-the-reply",
        "a-thread-not-collected",
        "the-mr-is-closed",
        "a-newer-triage-ran",
        "the-text-is-gone",
        "the-result-file-changed",
    ],
)
async def test_a_triage_reply_is_refused_or_superseded(rig, monkeypatch, how, reply, ended):
    """Each change lands after the bots were read, before the reply's posting point;
    the last four after a first posting point that could not read the thread."""
    fake = await filed(rig, monkeypatch, t1(), **_ci(1))
    later = how(rig, fake, monkeypatch) if how is not None else None
    rig.verdicts = [{"findings": [], "replies": [reply]}]

    assert await _walk(rig, *([later] if later else [])) == "completed"

    got = [(e["payload"]["outcome"], e["payload"]["why"]) for e in rig.item.events("reply_ended")]
    assert got == ended
    assert len(_of(rig, "reply_intended")) == len(ended)
    assert fake.writes == []


_RED = (forge.FailedJob("test", "failed", "script_failure"),)
_TRIAGE, _REPAIR_CHECKS = (
    "feedback.review_feedback.triage",
    "feedback.on_failure.checks.repair_checks",
)
_FIX = "feedback.fix_loop.fix.repair"
_A_FINDING = {
    "severity": "important",
    "message": "rename x",
    "file": "calc.py",
    "line": 1,
    "source_plugin": "review-feedback",
}


def _the_judge_downgrades(monkeypatch):
    async def downgrade(*_, **__):
        return "stop_downgrade", "the rest is not worth a cycle"

    monkeypatch.setattr(dispatch, "judge_verdict", downgrade)


def _red_after_the_repair(ci_after):
    """The CI reads of a walk whose first measure ends red at the review wait, so
    `on_failure` repairs and re-measures: that re-measure parks the first walk, and
    in the second the red comes again and the re-measure settles, reads the triage
    and then the CI wait's `ci_after`."""
    reads = ["failed", "pending", "failed", "success", ci_after]
    return {
        "watch_ci": True,
        "ci_states": reads,
        "ci_failed_jobs": [_RED if r == "failed" else () for r in reads],
    }


@pytest.mark.parametrize(
    "fake_kw, verdicts, only_at_completion, downgrade, status, posted_after",
    [
        # The re-measure after `on_failure` is the first time a triage has run, and it
        # falls through to a fix cycle: the reply goes before that cycle, not after.
        (
            _red_after_the_repair("failed"),
            [{"findings": [], "replies": [_DECLINE]}],
            False,
            False,
            "needs_human",
            [_REPAIR_CHECKS, _TRIAGE],
        ),
        # The re-measure after `on_failure` comes back clean. The posting point after
        # `recover_node` and the one at the top post nothing: only the completion's does.
        (
            _red_after_the_repair("success"),
            [{"findings": [], "replies": [_DECLINE]}],
            True,
            False,
            "completed",
            [_REPAIR_CHECKS, _TRIAGE],
        ),
        # A fix cycle ran and the judge downgrades what is left: the loop ends clean.
        # The posting points before it post nothing.
        (
            _ci(0),
            [{"findings": [_A_FINDING], "replies": [_DECLINE]}] * 2,
            True,
            True,
            "completed",
            [_TRIAGE, _FIX, _TRIAGE],
        ),
    ],
    ids=[
        "after-the-on-failure-repair-and-before-the-fix-cycle",
        "the-repair-settles-it-and-the-node-completes",
        "the-judge-downgrades-what-is-left",
    ],
)
async def test_a_pending_reply_is_posted_at_each_posting_point_that_walk_reaches(
    rig,
    monkeypatch,
    fake_kw,
    verdicts,
    only_at_completion,
    downgrade,
    status,
    posted_after,
):
    """A reply left unposted when the node completes is dropped, so each row asserts
    the reply went at the posting point its walk reaches, with (`only_at_completion`)
    every one before the completion's posting nothing, as an unreadable viewer does:
    the next point tries again."""
    fake = await filed(rig, monkeypatch, t1(), **fake_kw)
    rig.verdicts = list(verdicts)
    point, at_write = replies._Point.post, []

    async def post(self, completing):
        if completing or not only_at_completion:
            await point(self, completing)

    reply = fake.reply_to_thread

    async def noted(**kwargs):
        at_write.append(list(rig.triaged))
        return await reply(**kwargs)

    monkeypatch.setattr(replies._Point, "post", post)
    monkeypatch.setattr(fake, "reply_to_thread", noted)
    if downgrade:
        _the_judge_downgrades(monkeypatch)

    assert await _walk(rig) == status

    assert at_write == [posted_after]
    assert fake.writes == [("reply", "T1")]


@pytest.mark.parametrize(
    "shape, viewer, reason",
    [
        ({"loop": False}, VIEWER, "no_fix_loop"),
        # No triage reads the threads, so none is open: an open one fails the wait itself.
        ({"reads": False, "threads": ()}, VIEWER, "no_triage"),
        ({}, "a", "viewer_is_a_bot"),
    ],
    ids=["no-fix-loop", "no-triage", "the-viewer-is-the-bot"],
)
async def test_replies_have_no_effect_where_nothing_can_act(
    rig, monkeypatch, shape, viewer, reason
):
    shape = dict(shape)
    threads = shape.pop("threads", (t1(),))
    fake = await filed(rig, monkeypatch, *threads, viewer_login=viewer, **shape)
    # Only work reads the viewer.
    rig.verdicts = [{"findings": [], "replies": [_DECLINE]}]

    assert await _walk(rig) == "completed"

    assert [e["payload"] for e in rig.item.events("replies_inert")] == [
        {"node_id": "feedback", "reason": reason}
    ]
    assert fake.writes == []


async def _seeded(item_on, run_dirs, *sessions):
    """The review node (bot `a` with replies on, the shipped triage, a fix loop)
    filed with `item_on`, and each `(sid, head, status, reply)` seeded as a
    triage session. A session with a `reply` gets a result file holding it and a
    one-thread collection (T1, bot `a`) in its `bot_feedback.json`."""
    it = await item_on([review_node(30, reviewers=(bot_on(),), loop=True)])
    for sid, head, status, reply in sessions:
        _, _, result = await it.session(
            sid, "feedback.review_feedback.triage", status, head_sha=head
        )
        if reply:
            Path(result).parent.mkdir(parents=True, exist_ok=True)
            Path(result).write_text(
                json.dumps({"status": "done", "findings": [], "replies": [reply]})
            )
            raw, _ = t1()
            copy = it.files(sid).bot_feedback
            copy.parent.mkdir(parents=True, exist_ok=True)
            notes = [{"id": "N1", "author": "a", "by": "bot"}]
            copy.write_text(
                json.dumps(
                    {
                        "head": head,
                        "reviewers": [
                            {
                                "reviewer": "bot:a",
                                "replies": True,
                                "threads": [{**raw, "notes": notes}],
                            }
                        ],
                    }
                )
            )
    node = store.materialized_chain_of(it.row()).chain.nodes[0]
    return it, node


@pytest.mark.parametrize(
    "sessions, since, latest",
    [
        ([("s1", "h1", "done")], "", "s1"),
        ([("s1", "h1", "done"), ("s2", None, "done")], "", "s1"),
        ([("s1", "h1", "done"), ("s2", "h1", "failed")], "", "s1"),
        ([("s2", None, "done")], "", None),
        ([("s1", "h1", "done")], "9999", None),
    ],
    ids=[
        "a-triage-ran",
        "a-skip-after-the-verdict",
        "a-failure-after-the-verdict",
        "only-a-skip",
        "before-the-run-began",
    ],
)
async def test_the_latest_triage_is_the_newest_that_ran(
    item_on, run_dirs, database, sessions, since, latest
):
    it, node = await _seeded(item_on, run_dirs, *[(*s, None) for s in sessions])
    row = database.read(lambda c: replies.latest_triage(c, it.id, node, since))
    assert (row["id"] if row else None) == latest


def _no_forge(monkeypatch, database):
    async def unresolved(*_, **__):
        raise ForgeError("no forge for this repository")

    monkeypatch.setattr(forge.run, "item_forge", unresolved)


async def _many_repos(monkeypatch, database):
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_item_repos (work_item_id, repo_path, role, submodule_path, "
            "merge_rank, created_at, updated_at) VALUES ('w1', '/x', 'root', NULL, 1, 'now', 'now')"
        )
    )


@pytest.mark.parametrize(
    "how, reason, runs",
    [
        (_many_repos, "multi_repo", 2),
        # The next run has no triage of its own yet: no work, so no forge is asked for.
        (_no_forge, "no_forge", 1),
    ],
    ids=["multi-repo", "no-forge"],
)
async def test_posting_point_has_no_effect_on_a_multi_repo_item_or_with_no_forge(
    item_on, run_dirs, database, monkeypatch, caplog, how, reason, runs
):
    it, node = await _seeded(item_on, run_dirs, ("s1", "h1", "done", _DECLINE))
    if (made := how(monkeypatch, database)) is not None:
        await made
    launch = executor.LaunchContext(repo_entry=entry_of({"setup_command": "", "forge": "github"}))

    async def post():
        await replies.posting_point(database, run_dirs, it.id, node, it.row(), it.worktree, launch)

    await post()
    await post()
    # Once per node run: the node's next run records it again.
    await database.write(
        lambda c: events.append(c, it.id, ChainEvent.NODE_COMPLETED, {"node_id": "feedback"})
    )
    await post()

    assert [e["payload"] for e in it.events("replies_inert")] == [
        {"node_id": "feedback", "reason": reason}
    ] * runs
    assert not [r for r in caplog.records if r.levelname == "ERROR"], "it went no further"


async def test_an_unreadable_viewer_posts_nothing_until_it_reads(rig, monkeypatch, caplog):
    fake = await filed(
        rig, monkeypatch, t1(), viewer_login=ForgeError("x (HTTP 503)", status=503), **_ci(1)
    )
    rig.verdicts = [{"findings": [], "replies": [_DECLINE]}]
    at_first = []

    def then_it_reads():
        at_first.append((list(fake.writes), [e["type"] for e in _of(rig, *_REPLY_TYPES)]))
        fake.viewer_login = VIEWER

    assert await _walk(rig, then_it_reads) == "completed"

    assert at_first == [([], [])]
    assert fake.writes == [("reply", "T1")]
    assert not [r for r in caplog.records if r.levelname == "ERROR"], "it went no further"


_REPLY_TYPES = (
    "reply_intended",
    "reply_send_failed",
    "reply_posted",
    "reply_not_found",
    "reply_ended",
    "reply_duplicate_deleted",
    "replies_inert",
)


def _story(rig) -> list[tuple]:
    """Each reply event after the intent: its type and what it says."""
    out = []
    for e in _of(rig, *_REPLY_TYPES[1:]):
        p = e["payload"]
        out.append((e["type"], p.get("message") or p.get("via") or p.get("deleted")))
    return out


async def test_a_lost_write_is_reconciled_not_sent_again(rig, monkeypatch):
    """The reply goes at the verdict's posting point and its answer is lost; the
    completion's posting point, the next on that head, finds it."""
    fake = await filed(rig, monkeypatch, t1(), write_script=[forge.models.LOST])
    rig.verdicts = [{"findings": [], "replies": [_DECLINE]}]

    assert await _walk(rig) == "completed"

    assert fake.writes == [("reply", "T1")]
    assert _story(rig) == [
        ("reply_send_failed", "fake: the answer was lost"),
        ("reply_posted", "reconcile"),
    ]
    assert len(_of(rig, "reply_send_started")) == 1
    [failed] = _of(rig, "reply_send_failed")
    assert (failed["payload"]["failure"], failed["payload"]["counted"]) == ("unclear", True)
    assert len(_marked(fake.threads["T1"])) == 1


async def test_a_late_copy_of_an_unclear_write_is_deleted(rig, monkeypatch):
    fake = await filed(rig, monkeypatch, t1(), write_script=[forge.models.LOST], **_ci(2))
    rig.verdicts = [{"findings": [], "replies": [_DECLINE]}]
    late = []

    def a_copy_lands_late():
        assert [t for t, _ in _story(rig)][-1] == "reply_posted", "reconciled at the first wake"
        thread = fake.threads["T1"]
        [first] = _marked(thread)
        copy = replace(first, id="LATE", rest_id="9LATE", created_at="2026-01-01T00:59:00Z")
        fake.threads["T1"] = replace(thread, notes=(*thread.notes, copy))
        late.append(copy)

    # The first wake reconciles the lost write; the copy lands before the second.
    assert await _walk(rig, lambda: None, a_copy_lands_late) == "completed"

    assert ("delete", "LATE") in fake.writes
    assert [e["payload"]["deleted"] for e in _of(rig, "reply_duplicate_deleted")] == [["LATE"]]
    assert [n.id for n in _marked(fake.threads["T1"])] == ["FN1"], "the earliest is kept"


async def test_a_reply_never_goes_beside_a_finding_on_the_same_thread(rig, monkeypatch):
    fake = await filed(rig, monkeypatch, t1())
    finding = {
        "severity": "important",
        "message": "rename x",
        "file": "calc.py",
        "line": 1,
        "source_plugin": "review-feedback",
        "threads": ["T1"],
    }
    rig.verdicts = [{"findings": [finding], "replies": [_DECLINE]}]

    assert await _walk(rig) == "completed"

    assert "feedback.fix_loop.fix.repair" in rig.triaged, "the fix loop owns the thread"
    assert _of(rig, "reply_intended") == []
    assert fake.writes == []


async def test_the_thread_moved_since_the_triage_read_it(rig, monkeypatch):
    fake = await filed(rig, monkeypatch, t1())
    rig.verdicts = [{"findings": [], "replies": [_DECLINE]}]
    triaged = dispatch._launch_agent

    async def and_the_bot_writes_again(*args, **kwargs):
        out = await triaged(*args, **kwargs)
        thread = fake.threads["T1"]
        fake.threads["T1"] = replace(thread, notes=(*thread.notes, note("N9", "a", "also y")))
        return out

    # After the triage read the thread, before its reply's posting point.
    monkeypatch.setattr(dispatch, "_launch_agent", and_the_bot_writes_again)

    assert await _walk(rig) == "completed"

    got = [(e["payload"]["outcome"], e["payload"]["why"]) for e in rig.item.events("reply_ended")]
    assert got == [("superseded", "thread_moved")]
    assert fake.writes == []


@pytest.mark.parametrize(
    "found, expected",
    [(3, 3), (LookupError("no snapshot"), 2)],
    ids=["the-nodes-key", "no-snapshot-reads-the-code-default"],
)
def test_the_triage_cap_is_the_nodes_policy_key(monkeypatch, found, expected):
    def scope_policy(row, node):
        if isinstance(found, Exception):
            raise found
        return type("Policy", (), {"reply_cap_per_thread": found})()

    monkeypatch.setattr(dispatch, "scope_policy", scope_policy)
    point = replies._Point(None, None, "w", None, None, None, None, (), (), 0, "")

    assert point.cap == expected
