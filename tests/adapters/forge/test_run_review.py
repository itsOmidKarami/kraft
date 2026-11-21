"""`mr.automated_review` through the executor against a `FakeForge`: which
reviewers reach the task (`dispatch._reviewers`), and where a task may name
them. What each reviewer's read returns is test_automated_review.py."""

# ruff: noqa: F811 -- `rig` is an imported fixture, taken by name.

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from support.harness import commit_all, entry_of, v1_resolved, write
from support.review_rig import A as _A
from support.review_rig import B as _B
from support.review_rig import feedback as _feedback
from support.review_rig import review_node as _review_node
from support.review_rig import rig  # noqa: F401 -- the fixture

from kraft import executor, store, waits
from kraft.adapters import forge
from kraft.automated_review import CheckReviewer, as_reviewers

from .nodes import forge_node
from .test_run_chain import _multi_repo_item, _run_task, _with_origin

_BOT = {"bot": "coderabbitai"}
_TASK_BOT = {"bot": "task-bot"}
_ITEM_CHECK = {"check": "item-check"}


@pytest.mark.parametrize(
    "item, in_repo, task, asked",
    [
        (None, _BOT, None, [_BOT]),
        (None, [_BOT, {"check": "scan"}], None, [_BOT, {"check": "scan"}]),
        (None, None, [_TASK_BOT], [_TASK_BOT]),
        (None, _BOT, [_TASK_BOT], [_BOT]),
        # `[]` is a layer saying "none", not an unset one.
        (None, [], [_TASK_BOT], []),
        ([_ITEM_CHECK], _BOT, [_TASK_BOT], [_ITEM_CHECK]),
        (None, None, None, []),
    ],
    ids=[
        "repo",
        "repo-list",
        "task",
        "repo-over-task",
        "repo-says-none",
        "item-over-repo-and-task",
        "none-anywhere",
    ],
)
async def test_the_nearest_layer_s_reviewers_reach_the_review_task(
    item_on, database, run_dirs, monkeypatch, item, in_repo, task, asked
):
    """Ruling 171: which reviewers is the item's node override's, else the
    repository's (`repos.yaml`), else the review task's own -- handed to the
    backend at dispatch."""
    fake = forge.FakeForge()
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    node = forge_node(
        "review", "mr.automated_review", **({} if task is None else {"config": {"reviewers": task}})
    )
    it = await item_on([node])
    if item is not None:
        await database.write(
            lambda c: store.set_node_overrides(c, it.id, {"review": {"reviewers": item}})
        )
    entry = entry_of(
        {
            "setup_command": "",
            "forge": "github",
            **({} if in_repo is None else {"automated_review": in_repo}),
        }
    )

    await executor.run(
        database,
        run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=entry),
    )

    assert fake.reviewers_asked == [as_reviewers(asked)]


@pytest.mark.parametrize(
    "target, fields, refused",
    [
        ("mr.automated_review", {"config": {"reviewers": [{"bot": "b"}]}}, False),
        ("mr.ci", {"config": {"reviewers": [{"bot": "b"}]}}, True),
        ("mr.automated_review", {"config": {"reviewers": [{"login": "b"}]}}, True),
        ("mr.automated_review", {"config": {"grace_period": "30s"}}, False),
        ("mr.ci", {"config": {"grace_period": "30s"}}, True),
        ("mr.ci", {"config": {"watch_ci": True}}, True),
        ("mr.automated_review", {"config": {"watch_ci": True}}, True),
        ("mr.automated_review", {"reviewers": [{"bot": "b"}]}, False),
        ("mr.ci", {"reviewers": [{"bot": "b"}]}, True),
        ("mr.automated_review", {"reviewers": [], "config": {"reviewers": []}}, True),
    ],
    ids=[
        "the-review",
        "another-wait",
        "malformed-entry",
        "grace-period-on-the-review",
        "grace-period-on-another-wait",
        "watch-ci-on-another-wait",
        "watch-ci-with-no-ci-wait-after-it",
        "flat-reviewers-on-the-review",
        "flat-reviewers-on-another-wait",
        "flat-beside-config",
    ],
)
def test_only_the_review_task_names_reviewers(target, fields, refused):
    """The chain may name *which* reviewers, and how long a bot must stay quiet,
    on the one task that waits for them; any other forge task naming either, or
    a malformed entry, is refused at load."""
    task = {"id": "t", "kind": "forge", "target": target, **fields}
    nodes = [{"id": "feedback", "kind": "exec", "tasks": [task]}]
    if refused:
        with pytest.raises(ValidationError):
            v1_resolved(nodes)
    else:
        v1_resolved(nodes)


_WATCHING = {
    "id": "review",
    "kind": "forge",
    "target": "mr.automated_review",
    "config": {"watch_ci": True},
}
_CI_WAIT = {"id": "ci", "kind": "forge", "target": "mr.ci"}


def _steps(*groups):
    return [{"id": f"s{i}", "tasks": list(g)} for i, g in enumerate(groups)]


@pytest.mark.parametrize(
    "nodes, refused",
    [
        ([{"id": "n", "kind": "exec", "steps": _steps([_WATCHING], [_CI_WAIT])}], False),
        ([{"id": "n", "kind": "exec", "steps": _steps([_WATCHING, _CI_WAIT])}], True),
        ([{"id": "n", "kind": "exec", "steps": _steps([_CI_WAIT], [_WATCHING])}], True),
        (
            [
                {
                    "id": "n",
                    "kind": "exec",
                    "steps": _steps([_WATCHING]),
                    "on_failure": {"tasks": [_CI_WAIT]},
                }
            ],
            True,
        ),
        (
            [
                {
                    "id": "n",
                    "kind": "exec",
                    "steps": _steps([_WATCHING]),
                    "fix_loop": {"tasks": [_CI_WAIT]},
                }
            ],
            True,
        ),
        (
            [
                {
                    "id": "n",
                    "kind": "exec",
                    "steps": _steps([_CI_WAIT], [_CI_WAIT | {"id": "ci2"}]),
                    "fix_loop": {"tasks": [_WATCHING]},
                }
            ],
            True,
        ),
        (
            [
                {"id": "n", "kind": "exec", "steps": _steps([_WATCHING])},
                {"id": "m", "kind": "exec", "tasks": [_CI_WAIT]},
            ],
            True,
        ),
    ],
    ids=[
        "a-later-step",
        "the-same-step",
        "an-earlier-step",
        "only-in-on-failure",
        "only-in-the-fix-loop",
        "watching-from-the-fix-loop",
        "in-a-later-node",
    ],
)
def test_watch_ci_looks_ahead_to_a_ci_wait_in_its_own_node(nodes, refused):
    """The CI wait owns CI; the review wait only looks ahead for it, so it needs
    one in a later step of its own node, never a handler's or another node's."""
    if refused:
        with pytest.raises(ValidationError, match="watch_ci"):
            v1_resolved(nodes)
    else:
        [node] = v1_resolved(nodes).nodes
        assert node.ci_wait_after("n.s0.review").path == "n.s1.ci"


# --- the collect path ------------------------------------------------------------


@pytest.mark.parametrize(
    "minutes, plan, ended, finished, threads",
    [
        # Both bots are quiet for the grace period after their last activity.
        (
            30,
            [(10, None), (63, None)],
            "settled",
            ["activity", "activity"],
            {"a": ["a1"], "b": ["b1"]},
        ),
        # Neither ever posts: the wait's deadline settles it.
        (1, [(0, None), (61, None)], "timed_out", ["timeout", "timeout"], {"a": [], "b": []}),
        # The deadline falls between Kraft's own reads of the clock: the decision
        # and the wait's record must agree on the one instant it was made at.
        (1, [(0, None), (0, 61)], "timed_out", ["timeout", "timeout"], {"a": [], "b": []}),
    ],
    ids=["both-settle", "silent-until-timeout", "deadline-crossed-between-reads"],
)
async def test_the_collect_path_settles_then_hands_on_the_collection(
    rig, monkeypatch, minutes, plan, ended, finished, threads
):
    quiet = ended == "settled"
    fake = forge.FakeForge(
        feedback_script=[
            [
                _feedback(_A, 1 if quiet else None, threads=threads["a"]),
                _feedback(_B, 2 if quiet else None, threads=threads["b"]),
            ]
        ]
    )
    await rig(_review_node(minutes), fake)

    for clock, _ in plan[:-1]:
        rig.clock(clock)
        assert await rig.walk() == "waiting"
        assert rig.triaged == []
    clock, decided_at = plan[-1]
    rig.clock(clock)
    if decided_at is not None:
        monkeypatch.setattr(waits, "now", lambda: rig.moment(decided_at))
    status = await rig.walk()

    assert status == "completed", "a silent bot is nothing to stop a person for"
    assert rig.wait_session()["status"] == "done"
    assert [e["payload"]["outcome"] for e in rig.item.events("external_wait_ended")] == [ended]
    got = rig.collection()
    assert got["read_at"] == rig.moment(clock if decided_at is None else decided_at).isoformat()
    assert [(r["reviewer"], r["finished"]) for r in got["reviewers"]] == list(
        zip(["bot:a", "bot:b"], finished, strict=True)
    )
    assert {r["reviewer"][4:]: [t["id"] for t in r["threads"]] for r in got["reviewers"]} == threads
    # Bots with threads open are read; silent ones leave nothing for the triage to
    # read, so no agent is started for it.
    assert rig.triaged == (["feedback.review_feedback.triage"] if quiet else [])


@pytest.mark.parametrize(
    "said, advance, heard, silent",
    [([_feedback(_A, 1)], 61, ["a"], []), ([_feedback(_A)], 1801, [], ["a"])],
    ids=["heard", "never-posted"],
)
async def test_a_settled_review_records_which_bots_were_heard(rig, said, advance, heard, silent):
    await rig(_review_node(30, reviewers=(_A,)), forge.FakeForge(feedback_script=[said]))

    rig.clock(0)
    assert await rig.walk() == "waiting"
    rig.clock(advance)
    await rig.walk()

    [settled] = rig.item.events("automated_review_settled")
    assert (settled["payload"]["heard"], settled["payload"]["silent"]) == (heard, silent)
    log = Path(rig.wait_session()["log_path"]).read_text()
    assert ("never posted on" in log) == bool(silent)


async def test_the_next_check_comes_when_a_bots_grace_period_ends_not_at_the_backoff(rig):
    await rig(
        _review_node(30), forge.FakeForge(feedback_script=[[_feedback(_A, 1), _feedback(_B, 2)]])
    )

    rig.clock(10)
    assert await rig.walk() == "waiting"

    # a was last active at 1s, so its 60s of quiet ends at 61s: the 30s backoff
    # would have looked again at 40s.
    assert rig.item.row()["retry_at"] == rig.moment(61).isoformat()


async def test_no_reviewer_named_settles_with_an_empty_collection(rig):
    await rig(_review_node(30, reviewers=()), forge.FakeForge())

    assert await rig.walk() == "completed"

    assert len(rig.item.events("automated_review_not_configured")) == 1
    assert rig.collection()["reviewers"] == []
    assert rig.triaged == [], "nothing was collected, so nothing is triaged"


def _said(reviewer, active, *, state=None, body=""):
    """`_feedback` with one summary: a comment, or a review in `state`."""
    from dataclasses import replace

    at = _feedback(reviewer, active).last_activity.isoformat()
    kind = "comment" if state is None else "review"
    summary = {"kind": kind, "state": state, "body": body, "updated_at": at}
    return replace(_feedback(reviewer, active), summaries=(summary,))


_SCAN = CheckReviewer(check="scan")
#: A check still running.
_RUNNING = forge.ReviewerFeedback(
    _SCAN, None, check={"name": "scan", "finished": False, "conclusion": None, "output": ""}
)
#: A check that finished red.
_FAILED = forge.ReviewerFeedback(
    _SCAN,
    None,
    check={"name": "scan", "finished": True, "conclusion": "failed", "output": "2 issues"},
)


@pytest.mark.parametrize(
    "said, advance, status, logged",
    [
        # The bot left a thread and went quiet: Kraft reads it as a finding.
        ([_feedback(_A, 1, threads=["a1"])], 61, "failed", "calc.py:1: fix a1"),
        # Active (an approval, a thread it resolved), quiet, nothing open: clean.
        ([_feedback(_A, 1)], 61, "done", "1 reviewer(s) finished"),
        # A review that requests changes, with no inline comment.
        ([_said(_A, 1, state="CHANGES_REQUESTED", body="split this")], 61, "failed", "split this"),
        # A summary only an agent could read: clean, and the log says so.
        ([_said(_A, 1, body="LGTM")], 61, "done", "not read: the summary from bot:a"),
        # Never active: the deadline settles it, and the item carries on.
        ([_feedback(_A)], 601, "done", "settled at the wait's deadline"),
        # A check beside the bot that finished red is a finding, like the bot's.
        ([_feedback(_A, 1), _FAILED], 61, "failed", "scan failed: 2 issues"),
        # A check beside the bot is never skipped: unfinished at the deadline,
        # it stops for a person as it would alone.
        ([_feedback(_A), _RUNNING], 601, "capped_out", "not finished by the deadline: scan"),
    ],
    ids=[
        "thread-fails",
        "quiet-is-clean",
        "changes-requested",
        "summary-unread",
        "silent",
        "failed-check-beside-the-bot",
        "unfinished-check-stops",
    ],
)
async def test_a_bot_with_nothing_reading_its_feedback_is_settled_then_judged_by_kraft(
    rig, said, advance, status, logged
):
    fake = forge.FakeForge(feedback_script=[said])
    await rig(_review_node(10, reads=False, reviewers=tuple(f.reviewer for f in said)), fake)

    rig.clock(0)
    assert await rig.walk() == "waiting", "a bot is waited for until it has finished with the head"
    rig.clock(advance)
    await rig.walk()

    session = rig.wait_session()
    assert session["status"] == status
    assert logged in Path(session["log_path"]).read_text()
    assert rig.collection() is None, "nothing reads a collection, so none is written"


@pytest.mark.parametrize(
    "threads, summary, mount, findings",
    [
        # A file-level comment has no line to name.
        (
            [{"id": "d1", "file": "calc.py", "line": None, "body": "split this file"}],
            None,
            None,
            ["calc.py: split this file"],
        ),
        # Changes requested with inline comments: the comments are the findings,
        # not the walkthrough above them.
        (
            [{"id": "d1", "file": "calc.py", "line": 3, "body": "unused x"}],
            "walkthrough",
            None,
            ["calc.py:3: unused x"],
        ),
        # A member's finding with no path of its own still names the member.
        ([], "split this", "repos/pkg", ["[repos/pkg] split this"]),
    ],
    ids=["file-without-line", "changes-requested-with-threads", "a-member-s-pathless-finding"],
)
def test_kraft_s_own_verdict_names_each_finding_once(threads, summary, mount, findings):
    review = {"kind": "review", "state": "CHANGES_REQUESTED", "body": summary, "updated_at": ""}
    entry = {
        "reviewer": "bot:a",
        "finished": "activity",
        "summaries": [review] if summary else [],
        "threads": threads,
        "check": None,
    }

    assert forge.run._verdict([entry], mount) == (findings, [], [])


async def test_a_check_with_nothing_reading_it_takes_the_direct_path_and_its_timeout_stops(
    item_on, database, run_dirs, monkeypatch, wait_clock
):
    check = CheckReviewer(check="scan")
    it = await item_on([_review_node(1, reads=False, reviewers=(check,))])
    fake = forge.FakeForge(review_results=[forge.ReviewResult("pending")])
    ask = {
        "handler": "automated_review",
        "reviewers": (check,),
        "reads_feedback": False,
        "wait": forge.WaitBounds.from_seconds(timeout=60, initial=30, maximum=120),
    }

    got = await _run_task(database, run_dirs, it, it.worktree, "kraft/w1", fake, monkeypatch, **ask)
    assert got == "waiting"
    wait_clock.advance(61)
    got = await _run_task(database, run_dirs, it, it.worktree, "kraft/w1", fake, monkeypatch, **ask)

    assert got == "capped_out"


async def _both_halves_changed(item_on, database, run_dirs, tmp_path, halves=("pkg", "root")):
    """A workspace item whose `halves` (the member `repos/pkg`, the root) changed,
    so each opens a merge request and the review wait reads those."""
    it, worktree, branch = await _multi_repo_item(item_on, database, run_dirs, tmp_path, "ignore")
    _with_origin(tmp_path, it, worktree)
    for where, name in ((worktree / "repos" / "pkg", "pkg"), (worktree, "root")):
        if name in halves:
            write(where, f"{name}.txt", "x\n")
            commit_all(where)
    return it, worktree, branch


@pytest.mark.parametrize(
    "checks, again, ended",
    [
        # pkg: baseline at 0, resolved at 30, quiet by 90. root: first reached
        # at 90, resolved at 120, quiet by 180.
        ((0, 30, 90, 120, 180), False, "done"),
        # pkg's bot opens a new thread at 130: the check stops at pkg again,
        # and root keeps what it remembered without being read.
        ((0, 30, 90, 120, 130), True, "waiting"),
    ],
    ids=["each-settles", "an-unread-repository-keeps-its-memory"],
)
async def test_a_multi_repo_item_settles_each_repository_s_bot_on_its_own_memory(
    item_on, database, run_dirs, tmp_path, monkeypatch, wait_clock, checks, again, ended
):
    """Each repository's bot is waited for until it has finished with that
    repository's head, remembering its own threads between checks: here each
    bot's only activity is resolving the thread it had open, which a check
    sees only against what it remembered of that repository."""
    it, worktree, branch = await _both_halves_changed(item_on, database, run_dirs, tmp_path)
    reads: dict[str, int] = {}

    async def feedback(*, repo, branch, reviewers, since):
        name = "pkg" if repo.name == "pkg" else "root"
        reads[name] = reads.get(name, 0) + 1
        opened = {1: [f"{name}1"], 5: ["pkg2"] if again and name == "pkg" else []}
        return [_feedback(_A, threads=opened.get(reads[name], []))]

    fake = forge.FakeForge()
    monkeypatch.setattr(fake, "reviewer_feedback", feedback)
    ask = {
        "handler": "automated_review",
        "reviewers": (_A,),
        "reads_feedback": True,
        "wait": forge.WaitBounds.from_seconds(timeout=600, initial=30, maximum=120),
    }

    got = []
    for at in checks:
        wait_clock.now = wait_clock.start + timedelta(seconds=at)
        got.append(
            await _run_task(database, run_dirs, it, worktree, branch, fake, monkeypatch, **ask)
        )

    assert got == ["waiting"] * 4 + [ended]
    assert reads == {"pkg": 5, "root": 2 if again else 3}, "every check reads the bots' feedback"
    settled = [e["payload"]["mount"] for e in it.events("automated_review_settled")]
    assert settled == (["repos/pkg"] if again else ["repos/pkg", None]), (
        "a repository read again after it settled is not recorded again"
    )
    assert not list(run_dirs.base.rglob("bot_feedback.json")), "one triage per item is follow-up"
    if again:
        wait = database.read(lambda c: waits.open_wait(c, it.id, "on.automated_review"))
        [kept] = [m for repo, m in wait.last["memory"].items() if not repo.endswith("pkg")]
        resolved = wait_clock.start + timedelta(seconds=120)
        assert kept["bot:a"]["activity_at"] == resolved.isoformat()


@pytest.mark.parametrize(
    "opened, checks, finding",
    [
        # The member's bot opens a thread: its path is given from the item's
        # checkout, where the fix loop works.
        ({"pkg": [[], ["pkg1"]]}, (0, 30, 90), "repos/pkg/calc.py:1: fix pkg1"),
        # The root's is the checkout itself.
        (
            {"pkg": [["pkg0"], []], "root": [[], ["root1"]]},
            (0, 30, 90, 120, 180),
            "calc.py:1: fix root1",
        ),
    ],
    ids=["a-member-s-thread", "the-root-s-thread"],
)
async def test_a_multi_repo_item_s_bot_findings_reach_the_fix_loop_named_from_its_checkout(
    item_on, database, run_dirs, tmp_path, monkeypatch, wait_clock, opened, checks, finding
):
    it, worktree, branch = await _both_halves_changed(item_on, database, run_dirs, tmp_path)
    reads: dict[str, int] = {}

    async def feedback(*, repo, branch, reviewers, since):
        name = "pkg" if repo.name == "pkg" else "root"
        reads[name] = reads.get(name, 0) + 1
        script = opened.get(name, [[]])
        return [_feedback(_A, threads=script[min(reads[name], len(script)) - 1])]

    fake = forge.FakeForge()
    monkeypatch.setattr(fake, "reviewer_feedback", feedback)
    ask = {
        "handler": "automated_review",
        "reviewers": (_A,),
        "wait": forge.WaitBounds.from_seconds(timeout=600, initial=30, maximum=120),
    }

    got = []
    for at in checks:
        wait_clock.now = wait_clock.start + timedelta(seconds=at)
        got.append(
            await _run_task(database, run_dirs, it, worktree, branch, fake, monkeypatch, **ask)
        )

    assert got[-1] == "failed"
    result = Path(it.sessions()[-1]["result_path"])
    assert [f["message"] for f in json.loads(result.read_text())["findings"]] == [finding]


async def test_a_member_s_check_finding_reaches_the_fix_loop_tagged_with_the_member(
    item_on, database, run_dirs, tmp_path, monkeypatch
):
    """Check-only reviewers take the direct path; a member's failed check is
    still this item's finding, tagged with the member it was read on."""
    it, worktree, branch = await _both_halves_changed(item_on, database, run_dirs, tmp_path)
    fake = forge.FakeForge(
        review_results=[forge.ReviewResult("actionable", findings=("scan failed: 2 issues",))]
    )

    status = await _run_task(
        database, run_dirs, it, worktree, branch, fake, monkeypatch,
        handler="automated_review", reviewers=(_SCAN,),
    )  # fmt: skip

    assert status == "failed"
    result = Path(it.sessions()[-1]["result_path"])
    findings = json.loads(result.read_text())["findings"]
    assert [f["message"] for f in findings] == ["[repos/pkg] scan failed: 2 issues"]


_FLAGGED = {
    "severity": "important",
    "message": 'rename x, as the bot says: "unused x"',
    "file": "calc.py",
    "line": 1,
    "source_plugin": "review-feedback",
}


@pytest.mark.parametrize(
    "verdicts, launched",
    [
        # The triage flags a comment: the fix loop's repair runs, the node is
        # measured again, and the second triage finds nothing more to change.
        ([[_FLAGGED], []], ["triage", "repair", "triage"]),
        # The triage declines everything: the node completes with no repair.
        ([[]], ["triage"]),
    ],
    ids=["flagged", "declined"],
)
async def test_a_triaged_bot_comment_reaches_the_fix_loop(rig, verdicts, launched):
    thread = _feedback(_A, 1, threads=["a1"])
    await rig(
        _review_node(30, reviewers=(_A,), loop=True), forge.FakeForge(feedback_script=[[thread]])
    )
    rig.verdicts = list(verdicts)

    rig.clock(10)
    assert await rig.walk() == "waiting"
    rig.clock(63)
    assert await rig.walk() == "completed"

    # Bot feedback reaches the fix loop as the triage's findings, so the node's
    # `on_failure` (for a failed check) never runs for it.
    assert [path.rsplit(".", 1)[-1] for path in rig.triaged] == launched
