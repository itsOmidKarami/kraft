"""`executor.replies`, the completion work: "Fixed in" once the triage on the fix's
commit confirms it, `fix_pending` in the triage's copy, the summary comment and the
reply report. Walked against a `FakeForge`; the verdict's halves over seeded events."""

# ruff: noqa: F811 -- `rig` is an imported fixture, taken by name.

import json
from pathlib import Path

import pytest
from support.bot_threads import bot_thread, note
from support.harness import entry_of, git
from support.review_rig import FIXED, FLAG, bot_on, fix_walk, review_node, rig  # noqa: F401
from support.review_rig import events_of as _of

from kraft import events, executor, store
from kraft import findings as _findings
from kraft.adapters.forge.models import ForgeError
from kraft.automated_review import BotReviewer
from kraft.executor import prompts, replies
from kraft.vocab import ChainEvent, ForgeEvent

_REPLY_TYPES = (
    ForgeEvent.REPLY_INTENDED,
    ForgeEvent.REPLY_SEND_FAILED,
    ForgeEvent.REPLY_POSTED,
    ForgeEvent.REPLY_ENDED,
)
#: What the cycle recorded for T1: the prose fingerprint of `FLAG`.
_FP = _findings.prose_fingerprint(
    _findings.Finding(**{k: v for k, v in FLAG.items() if k != "threads"})
)


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
    it = await item_on([review_node(30, reviewers=(bot_on(),), loop=True)])
    node = store.materialized_chain_of(it.row()).chain.nodes[0]
    repo = str(Path(it.row()["repo"]))

    async def event(type_, payload):
        await database.write(lambda c: events.append(c, it.id, type_, payload))

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
    point = replies._Point(
        database, run_dirs, it.id, node, it.row(), it.worktree, None, ("a",), ("a",), 0, ""
    )
    cycles = database.read(lambda c: replies._cycles(c, it.id, node.id, 0))
    return point._fix_verdict(cycles[0], "T1", cycles, completing)


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
async def test_the_fix_verdict(item_on, database, run_dirs, replies_on, arguments, expected):
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
    rig, monkeypatch, replies_on, second, state, writes, ended, posted
):
    fake = await fix_walk(rig, monkeypatch, second, state=state)

    assert rig.item.status() == "completed"
    assert fake.writes == writes
    assert _ended(rig) == ended
    if posted:
        sha = _of(rig, ChainEvent.FIX_CYCLE_FINISHED)[0]["payload"]["head_sha"][:7]
        assert fake.threads["T1"].notes[-1].body.startswith(posted.format(sha=sha))


async def test_a_fix_reply_needs_its_commit_on_the_forge(rig, monkeypatch, replies_on, repo):
    """A person's force-push dropped the fix: the forge's head is not the cycle's commit."""
    fake = await fix_walk(rig, monkeypatch, [], head=lambda: git(repo, "rev-parse", "HEAD"))

    assert rig.item.status() == "completed"
    assert fake.writes == []
    assert _ended(rig) == [("superseded", "head_moved")]


async def test_a_fix_reply_needs_the_thread_s_file_changed(rig, monkeypatch, replies_on):
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


async def test_a_fix_reply_goes_once(rig, monkeypatch, replies_on):
    """The CI wait parks after the triage confirmed the fix: later posting points
    see the same fix, and it is written once."""
    fake = await fix_walk(rig, monkeypatch, [], ci_states=("pending",) * 6 + ("success",))

    assert fake.writes == [("reply", "T1")]
    assert len(_of(rig, ForgeEvent.REPLY_POSTED)) == 1


def _repair_instruction(rig) -> str:
    [repair] = [i for t, i in zip(rig.triaged, rig.instructions, strict=True) if ".fix_loop." in t]
    return repair


def _second_triage_copy(rig) -> dict:
    triages = [s for s in rig.item.sessions("feedback") if s["hook_point"].endswith(".triage")]
    return json.loads(rig.item.files(triages[1]["id"]).bot_feedback.read_text())


async def test_the_triage_is_told_which_fix_waits(rig, monkeypatch, replies_on):
    await fix_walk(rig, monkeypatch, [])

    short = _of(rig, ChainEvent.FIX_CYCLE_FINISHED)[0]["payload"]["head_sha"][:7]
    [thread] = _second_triage_copy(rig)["reviewers"][0]["threads"]
    assert thread["fix_pending"] == short


async def test_threads_from_a_replies_off_bot_change_nothing(rig, monkeypatch):
    """No `replies_on`: bot `a` takes none, so the triage's thread ids are dropped and
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


async def test_a_failed_read_skips_one_reply_not_the_rest(rig, monkeypatch, replies_on):
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


async def test_a_reply_left_unsent_is_never_sent_by_a_later_run(
    rig, monkeypatch, replies_on, database, run_dirs
):
    limited = ForgeError("x (HTTP 429)", status=429, reason="rate_limited", retry_after=3600)

    def tweak(fake):
        fake.write_script = [limited] * 5

    fake = await fix_walk(rig, monkeypatch, [], tweak=tweak)
    attempts = list(fake.writes)
    intended = len(_of(rig, ForgeEvent.REPLY_INTENDED))
    assert attempts and not _of(rig, ForgeEvent.REPLY_POSTED)

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

    assert fake.writes == attempts
    assert len(_of(rig, ForgeEvent.REPLY_INTENDED)) == intended  # nor is it intended again


@pytest.mark.parametrize("with_threads", [True, False], ids=["with-threads", "without"])
async def test_the_repair_is_asked_for_replies_only_when_a_finding_names_a_thread(
    rig, monkeypatch, replies_on, with_threads
):
    flag = FLAG if with_threads else {k: v for k, v in FLAG.items() if k != "threads"}
    await fix_walk(rig, monkeypatch, [], flags=[flag], repair={})

    repair = _repair_instruction(rig)
    assert ("threads: T1" in repair) is with_threads
    assert (prompts.FIX_REPLIES in repair) is with_threads
