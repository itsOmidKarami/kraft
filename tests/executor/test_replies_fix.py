"""`executor.replies`, the completion work: "Fixed in" once the triage on the fix's
commit confirms it, `fix_pending` in the triage's copy, the summary comment and the
reply report. Walked against a `FakeForge`; the verdict's halves over seeded events."""

# ruff: noqa: F811 -- `rig` is an imported fixture, taken by name.

import json
from dataclasses import replace
from pathlib import Path

import pytest
from support.bot_threads import VIEWER, bot_thread, note
from support.harness import entry_of, git
from support.review_rig import (  # noqa: F401
    FLAG,
    bot_on,
    filed,
    fix_walk,
    review_node,
    rig,
    t1,
    walk_to_completion,
)
from support.review_rig import events_of as _of

from kraft import events, executor, store
from kraft import findings as _findings
from kraft.adapters import forge
from kraft.adapters.forge.models import ForgeError
from kraft.adapters.forge.replies import body, facts, record
from kraft.automated_review import BotReviewer
from kraft.executor import prompts, replies
from kraft.vocab import ChainEvent, ForgeEvent

#: What the cycle recorded for T1: the prose fingerprint of `FLAG`.
_FP = _findings.prose_fingerprint(
    _findings.Finding(**{k: v for k, v in FLAG.items() if k != "threads"})
)


async def _direct(item_on, database, run_dirs):
    """The review node filed, and a posting point of its run 0 to call directly:
    `(item, node, point, event)`, `event(type, payload)` appending to the item."""
    it = await item_on([review_node(30, reviewers=(bot_on(),), loop=True)])
    node = store.materialized_chain_of(it.row()).chain.nodes[0]

    async def event(type_, payload):
        await database.write(lambda c: events.append(c, it.id, type_, payload))

    point = replies._Point(
        database, run_dirs, it.id, node, it.row(), it.worktree, None, ("a",), ("a",), 0, ""
    )
    return it, node, point, event


async def _verdict(
    item_on,
    database,
    run_dirs,
    *,
    pushes=("C1",),
    later=False,
    triage=("C1", [], True),
    completing=False,
    file=None,
):
    """`_fix_verdict` for T1 of cycle 1 (from head B0 to commit C1), with the heads
    Kraft pushed since in `pushes`, a later cycle when `later`, and the latest triage
    `(head, findings, T1 collected)`."""
    it, node, point, event = await _direct(item_on, database, run_dirs)
    repo = str(Path(it.row()["repo"]))
    started = {"node_id": node.id, "cycle": 1, "threads": {"T1": _FP}, "head_sha": "B0"}
    await event(ChainEvent.FIX_CYCLE_STARTED, started)
    await event(
        ChainEvent.FIX_CYCLE_FINISHED,
        {"node_id": node.id, "cycle": 1, "committed": True, "head_sha": "C1"},
    )
    for sha in pushes:
        await event(ForgeEvent.BRANCH_PUSHED, {"node_id": node.id, "repo": repo, "sha": sha})
    if later:
        await event(ChainEvent.FIX_CYCLE_STARTED, {**started, "cycle": 2, "head_sha": "C1"})
    head, found, collected = triage
    _, _, result = await it.session("s1", "feedback.review_feedback.triage", "done", head_sha=head)
    Path(result).parent.mkdir(parents=True, exist_ok=True)
    Path(result).write_text(json.dumps({"status": "done", "findings": found}))
    copy = it.files("s1").bot_feedback
    copy.parent.mkdir(parents=True, exist_ok=True)
    thread = {"id": "T1", "file": file, "line": 1, "notes": [{"id": "N1", "by": "bot"}]}
    reviewers = [{"reviewer": "bot:a", "replies": True, "threads": [thread] if collected else []}]
    copy.write_text(json.dumps({"head": head, "reviewers": reviewers}))
    cycles = database.read(lambda c: replies._cycles(c, it.id, node.id, 0))
    return await point._fix_verdict(cycles[0], "T1", cycles, completing)


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ({}, "send"),
        ({"pushes": ("C1", "D2")}, ("superseded", "head_moved")),
        ({"pushes": ()}, "wait"),
        ({"pushes": (), "completing": True}, ("superseded", "not_pushed")),
        ({"later": True}, ("superseded", "later_cycle")),
        ({"triage": ("B0", [], True)}, "wait"),
        ({"triage": ("B0", [], True), "completing": True}, ("superseded", "not_confirmed")),
        ({"triage": ("C1", [], False)}, ("superseded", "not_collected")),
        (
            {"triage": ("C1", [{**FLAG, "threads": [], "same_as": "0123456789abcdef"}], True)},
            ("superseded", "raised_again"),
        ),
    ],
    ids=[
        "confirmed",
        "another-head-pushed-since",
        "not-pushed-yet",
        "not-pushed-at-completion",
        "a-later-cycle",
        "no-triage-on-the-commit-yet",
        "unconfirmed-at-completion",
        "left-the-collection",
        "a-bogus-same-as",
    ],
)
async def test_the_fix_verdict(item_on, database, run_dirs, arguments, expected):
    assert await _verdict(item_on, database, run_dirs, **arguments) == expected


def _ended(rig) -> list[tuple]:
    return [
        (e["payload"]["outcome"], e["payload"]["why"]) for e in _of(rig, ForgeEvent.REPLY_ENDED)
    ]


def _minor(**over):
    """A re-raised finding, rated below the loop's severities so no second fix runs."""
    return {**FLAG, "severity": "minor", **over}


_TRIAGE_REPLY = {
    "findings": [],
    "replies": [{"thread": "T1", "kind": "answered", "body": "It holds."}],
}


@pytest.mark.parametrize(
    "second, state, writes, ended, posted",
    [
        ([], "open", [("reply", "T1")], [], "Fixed in {sha}:\n\nRenamed it."),
        ([_minor(message="rename x still")], "open", [], [("superseded", "raised_again")], None),
        ([_minor(threads=[])], "open", [], [("superseded", "raised_again")], None),
        ([], "closed", [], [("refused", "mr_not_open")], None),
        (
            _TRIAGE_REPLY,
            "open",
            [("reply", "T1")],
            [("superseded", "triage_replied")],
            "Answer:\n\nIt holds.",
        ),
    ],
    ids=[
        "confirmed",
        "raised-again",
        "the-same-finding-without-its-thread",
        "the-merge-request-closed",
        "the-triage-replied-instead",
    ],
)
async def test_a_fix_reply_waits_for_the_triage_on_its_commit(
    rig, monkeypatch, second, state, writes, ended, posted
):
    fake = await fix_walk(rig, monkeypatch, second, state=state)

    assert rig.item.status() == "completed"
    assert fake.writes == writes
    assert _ended(rig) == ended
    if posted:
        sha = _of(rig, ChainEvent.FIX_CYCLE_FINISHED)[0]["payload"]["head_sha"][:7]
        assert fake.threads["T1"].notes[-1].body.startswith(posted.format(sha=sha))


async def test_a_fix_reply_needs_its_commit_on_the_forge(rig, monkeypatch, repo):
    """A person's force-push dropped the fix: the forge's head is not the cycle's commit."""
    fake = await fix_walk(rig, monkeypatch, [], head=lambda: git(repo, "rev-parse", "HEAD"))

    assert rig.item.status() == "completed"
    assert fake.writes == []
    assert _ended(rig) == [("superseded", "head_moved")]


async def test_a_fix_reply_needs_the_thread_s_file_changed(rig, monkeypatch):
    """The repair commits only `repaired.txt`; the thread is on `calc.py`."""
    fake = await fix_walk(
        rig,
        monkeypatch,
        [],
        threads=[bot_thread("T1", note("N1", "a", "x"), file="calc.py")],
        flags=[{**FLAG, "file": "calc.py"}],
    )

    assert rig.item.status() == "completed"
    assert fake.writes == []
    assert _ended(rig) == [("superseded", "file_unchanged")]


async def test_a_fix_reply_goes_once(rig, monkeypatch):
    """The CI wait parks after the triage confirmed the fix: later posting points
    see the same fix, and it is written once."""
    fake = await fix_walk(rig, monkeypatch, [], ci_states=("pending",) * 6 + ("success",))

    assert fake.writes == [("reply", "T1")]
    # One intent, posted: a later posting point finds it by its key, neither making a
    # second nor ending one at the gate's `fixed_before`.
    assert len(_of(rig, ForgeEvent.REPLY_INTENDED)) == 1
    assert len(_of(rig, ForgeEvent.REPLY_POSTED)) == 1
    assert _of(rig, ForgeEvent.REPLY_ENDED) == []


def _repair_instruction(rig) -> str:
    [repair] = [i for t, i in zip(rig.triaged, rig.instructions, strict=True) if ".fix_loop." in t]
    return repair


def _second_triage_copy(rig) -> dict:
    triages = [s for s in rig.item.sessions("feedback") if s["hook_point"].endswith(".triage")]
    return json.loads(rig.item.files(triages[1]["id"]).bot_feedback.read_text())


async def test_the_triage_is_told_which_fix_waits(rig, monkeypatch):
    await fix_walk(rig, monkeypatch, [])

    short = _of(rig, ChainEvent.FIX_CYCLE_FINISHED)[0]["payload"]["head_sha"][:7]
    [thread] = _second_triage_copy(rig)["reviewers"][0]["threads"]
    assert thread["fix_pending"] == short


async def test_threads_from_a_replies_off_bot_change_nothing(rig, monkeypatch):
    """Bot `a` has replies off and takes none, so the triage's thread ids are dropped and
    the fix loop is as it was before replies."""
    fake = await fix_walk(rig, monkeypatch, [], reviewers=(BotReviewer(bot="a"),))

    [started] = _of(rig, ChainEvent.FIX_CYCLE_STARTED)
    assert "threads" not in started["payload"] and "head_sha" not in started["payload"]
    for e in _of(rig, ChainEvent.FINDINGS_MEASURED):
        assert all("threads" not in f for f in e["payload"]["findings"])
    repair = _repair_instruction(rig)
    assert "threads:" not in repair and prompts.FIX_REPLIES not in repair
    triages = [s for s in rig.item.sessions("feedback") if s["hook_point"].endswith(".triage")]
    wait = next(
        s
        for s in rig.item.sessions("feedback")
        if s["hook_point"].endswith(".review") and s["round"] == triages[1]["round"]
    )
    assert (
        rig.item.files(triages[1]["id"]).bot_feedback.read_text()
        == rig.item.files(wait["id"]).bot_feedback.read_text()
    )
    assert fake.writes == []


async def test_a_failed_read_skips_one_reply_not_the_rest(rig, monkeypatch):
    t2 = bot_thread("T2", note("N2", "a", "rename y"), file="repaired.txt")
    t1 = bot_thread("T1", note("N1", "a", "rename x"), file="repaired.txt")

    def tweak(fake):
        read = fake.read_thread

        async def read_thread(*, repo, branch, thread_id):
            if thread_id == "T1":
                raise ForgeError("fake: unreadable")
            return await read(repo=repo, branch=branch, thread_id=thread_id)

        monkeypatch.setattr(fake, "read_thread", read_thread)

    fake = await fix_walk(
        rig,
        monkeypatch,
        [],
        threads=[t1, t2],
        flags=[FLAG, {**FLAG, "message": "rename y", "threads": ["T2"]}],
        repair={
            "replies": [
                {"thread": "T1", "body": "Renamed x."},
                {"thread": "T2", "body": "Renamed y."},
            ]
        },
        tweak=tweak,
    )

    assert rig.item.status() == "completed"
    assert fake.writes == [("reply", "T2")]
    intended = {e["payload"]["thread"] for e in _of(rig, ForgeEvent.REPLY_INTENDED)}
    posted = _of(rig, ForgeEvent.REPLY_POSTED)
    assert intended == {"T1", "T2"} and len(posted) == 1
    assert [(e["thread"], e["outcome"]) for e in _report(rig)["entries"]] == [
        ("T1", "unsent"),
        ("T2", "posted"),
    ]


async def test_a_reply_left_unsent_is_never_sent_by_a_later_run(
    rig, monkeypatch, database, run_dirs
):
    limited = ForgeError("x (HTTP 429)", status=429, reason="rate_limited", retry_after=3600)

    def tweak(fake):
        fake.write_script = [limited] * 5

    fake = await fix_walk(rig, monkeypatch, [], tweak=tweak)
    attempts = list(fake.writes)
    intended = len(_of(rig, ForgeEvent.REPLY_INTENDED))
    assert attempts and not _of(rig, ForgeEvent.REPLY_POSTED)
    assert _entries(rig) == [("T1", "unsent", "rate_limited")]

    # The node's `node_completed` began a new run: its cycles are not this run's.
    fake.write_script = []
    node = store.materialized_chain_of(rig.item.row()).chain.nodes[0]
    launch = executor.LaunchContext(repo_entry=entry_of({"setup_command": "", "forge": "github"}))
    await replies.posting_point(
        database,
        run_dirs,
        rig.item.id,
        node,
        rig.item.row(),
        rig.item.worktree,
        launch,
        completing=True,
    )

    assert _report(rig)["entries"] == []  # the new run lists nothing for T1
    assert fake.writes == attempts
    assert len(_of(rig, ForgeEvent.REPLY_INTENDED)) == intended  # nor is it intended again


@pytest.mark.parametrize("with_threads", [True, False], ids=["with-threads", "without"])
async def test_the_repair_is_asked_for_replies_only_when_a_finding_names_a_thread(
    rig, monkeypatch, with_threads
):
    flag = FLAG if with_threads else {k: v for k, v in FLAG.items() if k != "threads"}
    await fix_walk(rig, monkeypatch, [], flags=[flag], repair={})

    repair = _repair_instruction(rig)
    assert ("threads: T1" in repair) is with_threads
    assert (prompts.FIX_REPLIES in repair) is with_threads


def _viewer_comment(rig, text: str):
    """A top-level comment the viewer wrote, carrying this item's summary marker."""
    marker = facts.summary_marker(rig.item.id)
    return note("C1", VIEWER, f"{text}\n\n{marker}", type_="User")


def _summary(rig, text: str) -> str:
    """The summary comment Kraft renders for `text` on the item's head (the worktree
    is cut from the repository's, and gone once the item completes)."""
    head = git(rig.item.repo, "rev-parse", "HEAD")
    return body.render(
        text,
        lead=f"Declined review-bot comments on {head[:7]}:",
        work_item_id=rig.item.id,
        marker=facts.summary_marker(rig.item.id),
    )


async def _declines(rig, monkeypatch, lines, *, seed=None, replies_off=False, moved=False):
    """A review node whose bot `a` (replies on; `b` with replies off too when
    `replies_off`) has commented, whose triage writes `lines` as `summary_replies`
    and no finding, walked to completion. `seed(fake)` runs first; `moved`: the
    forge's head is not the checkout's."""
    bots = [bot_on(), BotReviewer(bot="b")] if replies_off else [bot_on()]
    said = (
        {
            "kind": "comment",
            "state": None,
            "body": "A walkthrough.",
            "updated_at": rig.moment(1).isoformat(),
        },
    )
    fake = forge.FakeForge(
        feedback_script=[[forge.ReviewerFeedback(b, rig.moment(1), summaries=said) for b in bots]]
    )
    await rig(review_node(30, reviewers=tuple(bots), loop=True), fake)

    async def details(*, repo, branch):
        sha = "f" * 40 if moved else git(rig.item.worktree, "rev-parse", "HEAD")
        return forge.MRDetails(
            1, "http://fake.forge/1", "open", "t", "", True, (), branch, "main", sha
        )

    monkeypatch.setattr(fake, "mr_details", details)
    if seed:
        await seed(fake)
    rig.verdicts = [{"findings": [], "summary_replies": lines}]
    await walk_to_completion(rig)
    return fake


_LINE = {"reviewer": "bot:a", "body": "The walkthrough asks for nothing."}


#: What the reply report says about the summary, for the rows that it names.
_SUMMARY_REPORTED = {"deleted-by-someone": "deleted", "a-refused-line": "refused"}
_OLD = "the comment as someone left it"
_NONE = "- a: The walkthrough asks for nothing."


@pytest.mark.parametrize(
    "seed, lines, replies_off, moved, writes, comment",
    [
        pytest.param("none", [_LINE], False, False, [("upsert", "new")], _NONE, id="first"),
        pytest.param(
            "comment",
            [{**_LINE, "body": "Another line."}],
            False,
            False,
            [("upsert", "9C1")],
            "- a: Another line.",
            id="edited-in-place",
        ),
        pytest.param("current", [_LINE], False, False, [], _NONE, id="unchanged"),
        pytest.param(
            "comment",
            [],
            False,
            False,
            [("upsert", "9C1")],
            "Nothing is declined.",
            id="nothing-declined-now",
        ),
        pytest.param("none", [], False, False, [], None, id="nothing-ever"),
        pytest.param(
            "none",
            [{**_LINE, "reviewer": "bot:b"}],
            True,
            False,
            [],
            None,
            id="a-bot-with-replies-off",
        ),
        pytest.param("deleted", [_LINE], False, False, [], None, id="deleted-by-someone"),
        pytest.param(
            "none",
            [_LINE, {"reviewer": "bot:a", "body": "See https://evil.example/x for more."}],
            False,
            False,
            [("upsert", "new")],
            _NONE,
            id="a-refused-line",
        ),
        pytest.param("comment", [_LINE], False, True, [], _OLD, id="no-triage-on-the-current-head"),
    ],
)
async def test_top_level_declines_go_in_one_comment(
    request,
    rig,
    monkeypatch,
    database,
    seed,
    lines,
    replies_off,
    moved,
    writes,
    comment,
):
    async def seeded(fake):
        """The record holds what Kraft posted before: a summary intent, posted, and
        (unless someone deleted it) the comment it made."""
        if seed == "none":
            return
        wid = rig.item.id
        iid = record.new_id(wid)
        text = _summary(rig, _NONE) if seed == "current" else _OLD
        old = _viewer_comment(rig, text)
        payload = {
            "op": "summary",
            "node_id": "feedback",
            "run": 0,
            "thread": None,
            "kind": "summary",
            "body_sha256": facts.body_hash(old.body),
        }
        await database.write(lambda c: record.intend(c, wid, iid, payload))
        posted = forge.models.Posted((old.rest_id,), old.created_at, old.body)
        await database.write(lambda c: record.posted(c, wid, iid, posted, via="send"))
        if seed != "deleted":
            fake.comments.append(replace(old, body=text) if seed == "current" else old)

    fake = await _declines(
        rig, monkeypatch, lines, seed=seeded, replies_off=replies_off, moved=moved
    )

    assert rig.item.status() == "completed"
    assert fake.writes == writes
    if comment is None:
        expected = []
    elif comment == _OLD:
        expected = [_viewer_comment(rig, _OLD).body]
    else:
        expected = [_summary(rig, comment)]
    assert [c.body for c in fake.comments] == expected
    named = [e["outcome"] for e in _report(rig)["entries"] if e["kind"] == "summary"]
    want = _SUMMARY_REPORTED.get(request.node.callspec.id)
    assert named == ([want] if want else [])


def _report(rig) -> dict:
    """The reply report, less its `owed` entries: a thread Kraft replied in and the (static) bot
    never answered is owed a look at completion, which `test_reply_round_walks.py` pins."""
    report = json.loads(replies.report_path(rig.item.run_dirs, rig.item.id).read_text())
    return {**report, "entries": [e for e in report["entries"] if e["kind"] != "owed"]}


def _entries(rig) -> list[tuple]:
    return [(e["thread"], e["outcome"], e["why"]) for e in _report(rig)["entries"]]


_DECLINE = {"thread": "T1", "kind": "declined", "body": "The caller reads it, so it stays."}


async def test_the_reply_report_lists_what_was_and_was_not_posted(rig, monkeypatch):
    t1 = bot_thread("T1", note("N1", "a", "rename x"), file="repaired.txt")
    t2 = bot_thread(
        "T2", note("N2", "a", "rename y"), note("P", "alice", "I disagree", type_="User")
    )
    t3 = bot_thread("T3", note("N3", "a", "rename z"), file="repaired.txt")
    fake = await filed(rig, monkeypatch, t1, t2, t3)
    rig.verdicts = [
        {
            "findings": [{**FLAG, "severity": "minor", "threads": ["T3"]}],
            "replies": [_DECLINE, {**_DECLINE, "thread": "T2"}],
        }
    ]
    await walk_to_completion(rig)

    assert fake.writes == [("reply", "T1")]
    assert sorted(_entries(rig)) == [
        ("T1", "posted", None),
        ("T2", "refused", "person_written"),
        ("T3", "not_fixed", "never reached a fix"),
    ]
    report = _report(rig)
    assert report["node_id"] == "feedback"
    assert all(len(e) == 8 for e in report["entries"])
    [first] = [e for e in report["entries"] if e["thread"] == "T1"]
    assert first["bot_last_word"] == "rename x" and first["reviewer"] == "bot:a"


async def test_a_finding_still_raised_after_its_cycle_is_not_fixed(item_on, database, run_dirs):
    it, node, point, event = await _direct(item_on, database, run_dirs)
    await event(
        ChainEvent.FIX_CYCLE_STARTED,
        {"node_id": node.id, "cycle": 1, "threads": {"T1": _FP}, "head_sha": "B0"},
    )
    again = {"node_id": node.id, "cycle": 1, "findings": [_findings.payload_of(_flag())]}
    await event(ChainEvent.FINDINGS_MEASURED, again)

    [entry] = point._never_fixed()
    assert (entry["thread"], entry["outcome"], entry["why"]) == (
        "T1",
        "not_fixed",
        "still raised after cycle 1",
    )

    # Once its "Fixed in" went out it is not listed.
    iid = record.new_id(it.id)
    fixed = {
        "op": "reply",
        "kind": "fixed",
        "thread": "T1",
        "node_id": node.id,
        "run": 0,
        "body_sha256": "h",
    }
    await database.write(lambda c: record.intend(c, it.id, iid, fixed))
    posted = forge.models.Posted(("1",), "t", "b")
    await database.write(lambda c: record.posted(c, it.id, iid, posted, via="send"))
    assert point._never_fixed() == []


def _flag():
    return _findings.Finding(**{k: v for k, v in FLAG.items() if k != "threads"}, threads=("T1",))


async def test_a_fix_with_no_reply_is_reported(rig, monkeypatch):
    fake = await fix_walk(rig, monkeypatch, [], repair={})

    assert fake.writes == []
    assert _entries(rig) == [("T1", "no_reply", "confirmed; the repair wrote no reply")]


@pytest.mark.parametrize(
    "report_of", ["feedback", "another"], ids=["this-node-s", "another-node-s"]
)
async def test_a_completion_with_replies_off_removes_the_report(rig, monkeypatch, report_of):
    fake = await filed(rig, monkeypatch, t1(), bot=BotReviewer(bot="a"))
    path = replies.report_path(rig.item.run_dirs, rig.item.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"node_id": report_of, "head": None, "entries": []}))

    await walk_to_completion(rig)

    assert fake.writes == []
    assert path.exists() is (report_of != "feedback")
