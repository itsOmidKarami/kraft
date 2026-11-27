"""A review node walked through the executor against a `FakeForge`: the review
wait, the shipped triage after it, and (with `loop=True`) the recovery the
shipped `post_draft_feedback` has. Import the `rig` fixture by name into the test
module (it shadows `support.rig_fixtures`' remote `rig` there):

    from support.review_rig import A, feedback, review_node, rig  # noqa: F401

    async def test_x(rig):
        await rig(review_node(30, reviewers=(A,)),
                  forge.FakeForge(feedback_script=[[feedback(A, 1, threads=["a1"])]]))
        rig.clock(10)
        await rig.walk()  # the wait parks
        rig.clock(63)
        assert await rig.walk() == "completed"
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from kraft import builtins as _builtins
from kraft import executor, policy, store, waits
from kraft.adapters import forge
from kraft.automated_review import BotReviewer
from kraft.executor import dispatch, replies, round_prompts
from kraft.templates.models import AgentInput
from support.bot_threads import bot_thread, note
from support.harness import entry_of, fake_harness_home, git

ROOT = Path(__file__).resolve().parents[2]

A, B = BotReviewer(bot="a"), BotReviewer(bot="b")


def feedback(reviewer, active=None, *, threads=()):
    """What `reviewer` has said, its newest activity `active` seconds after the
    clock's start (the item's head is dated there)."""
    at = None if active is None else datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=active)
    return forge.ReviewerFeedback(
        reviewer,
        at,
        threads=tuple({"id": t, "file": "calc.py", "line": 1, "body": f"fix {t}"} for t in threads),
    )


def shipped_triage_step() -> dict:
    """The step the shipped `post_draft_feedback` runs after its review wait,
    its task as the shipped library defines it, on the test's fake harness."""
    library = yaml.safe_load((ROOT / "config" / "library.yaml").read_text())
    [step] = [
        s for s in library["nodes"]["post_draft_feedback"]["steps"] if s["id"] == "review_feedback"
    ]
    [task] = step["tasks"]
    shipped = library["tasks"][task["extends"]]
    # The harness and what it selects are the test's own; everything else is the library's.
    own = {k: v for k, v in shipped.items() if k not in ("harness", "profile", "skill")}
    return {**step, "tasks": [{"id": task["id"], **own, "harness": "fake"}]}


def review_node(minutes, *, reads=True, reviewers=(A, B), loop=False, watch_ci=False):
    """One node: the review wait, and (when `reads`) a task that reads what it
    collected, in the step after. `loop` gives it the recovery the shipped
    `post_draft_feedback` has: `on_failure` for a failed check, a fix loop for
    findings."""
    wait = {
        "id": "review",
        "kind": "forge",
        "target": "mr.automated_review",
        "config": {"reviewers": [r.model_dump(exclude_none=True) for r in reviewers]},
        "policy": {"total_time_cap_minutes": minutes},
        "wait": {"polling": {"initial_interval": "30s", "max_interval": "2m"}},
    }
    if watch_ci:
        wait["config"]["watch_ci"] = True
    steps = [{"id": "settle", "tasks": [wait]}]
    if reads:
        steps.append(shipped_triage_step())
    if watch_ci:
        steps.append(
            {"id": "ci", "tasks": [{"id": "await_ci", "kind": "forge", "target": "mr.ci"}]}
        )
    node = {"id": "feedback", "kind": "exec", "steps": steps}
    if loop:

        def repair(id):
            return {"id": id, "kind": "agent", "harness": "fake", "prompt": "Fix it."}

        node["on_failure"] = {"steps": [{"id": "checks", "tasks": [repair("repair_checks")]}]}
        node["fix_loop"] = {
            "steps": [{"id": "fix", "tasks": [repair("repair")]}],
            "max_attempts": 2,
        }
    return node


FLAG = {
    "severity": "important",
    "message": "rename x",
    "file": "repaired.txt",
    "line": 1,
    "source_plugin": "review-feedback",
    "threads": ["T1"],
}
FIXED = {"replies": [{"thread": "T1", "body": "Renamed it."}]}
#: A triage verdict that finds nothing and declines T1's comment.
DECLINE = {
    "findings": [],
    "replies": [{"thread": "T1", "kind": "declined", "body": "The call is right."}],
}


def bot_on(resolve_fixed=False):
    """Bot `a` with replies on, and `resolve_fixed` as given."""
    return BotReviewer(bot="a", replies=True, resolve_fixed=resolve_fixed)


async def filed(
    rig,
    monkeypatch,
    *threads,
    bot=None,
    loop=True,
    reads=True,
    watch_ci=False,
    tweak=None,
    policy=None,
    **fake,
):
    """The review node with a fix loop, filed on a fake whose bot `a` (replies on)
    has `threads` open, and whose merge request is open at the checkout's head.
    `tweak(node)` changes the node's definition before it is filed, `policy` is the item's own
    policy override (`{"reply_rounds_per_head": 1}`)."""
    raw = [r for r, _ in threads]
    bot = bot or bot_on()
    fake = forge.FakeForge(
        feedback_script=[[forge.ReviewerFeedback(bot, rig.moment(1), threads=tuple(raw))]],
        threads={t.id: t for _, t in threads},
        **fake,
    )
    node = review_node(30, reviewers=(bot,), loop=loop, reads=reads, watch_ci=watch_ci)
    if tweak:
        tweak(node)
    await rig(node, fake, **({"policy_override": policy} if policy else {}))

    async def details(*, repo, branch):
        head = git(rig.item.worktree, "rev-parse", "HEAD")
        return forge.MRDetails(
            1, "http://fake.forge/1", "open", "t", "", True, (), branch, "main", head
        )

    monkeypatch.setattr(fake, "mr_details", details)
    if watch_ci:
        # The watching wait pushes, on the real clock; the head's own date is where
        # the frozen one starts, so the bot's activity is read against that.
        monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    return fake


def t1():
    """Bot `a`'s one open thread, on `calc.py`."""
    return bot_thread("T1", note("N1", "a", "fix"))


def events_of(rig, *types) -> list[dict]:
    """The filed item's events of the given types, oldest first."""
    return [e for e in rig.item.events() if e["type"] in types]


def report_of(rig) -> dict:
    """The filed item's reply report, as the brief reads it."""
    return json.loads(replies.report_path(rig.item.run_dirs, rig.item.id).read_text())


async def walk_to_completion(rig, *, start=10, step=63, walks=8) -> list[str]:
    """Walk the filed item until it completes, the wait clock `step` seconds on at
    each walk (past a bot's grace period). Returns each walk's status, so a test can
    assert the parks it expects."""
    seen = []
    for n in range(walks):
        rig.clock(start + n * step)
        seen.append(await rig.walk())
        if seen[-1] == "completed":
            return seen
    raise AssertionError(f"not completed after {walks} walks: {seen}")


async def fix_walk(
    rig,
    monkeypatch,
    second,
    *,
    threads=None,
    flags=(FLAG,),
    repair=FIXED,
    reviewers=None,
    state="open",
    head=None,
    ci_states=("success",),
    tweak=None,
    policy=None,
) -> forge.FakeForge:
    """One fix cycle on a node whose bot `a` takes replies: the first triage raises
    `flags` (on T1 by default, in `threads`), the repair commits `repaired.txt` and
    writes `repair`, the review wait pushes the new head (`watch_ci`), and the second
    triage writes `second` (findings, or a dict with `replies`). The merge request is
    `state` at the checkout's head, or at `head()` when given; `tweak(fake)` runs
    before the first walk; `policy` is the item's own policy override."""
    reviewers = reviewers or (bot_on(),)
    threads = threads or [bot_thread("T1", note("N1", "a", "rename x"), file="repaired.txt")]
    fake = forge.FakeForge(
        feedback_script=[
            [
                forge.ReviewerFeedback(
                    reviewers[0], rig.moment(1), threads=tuple(raw for raw, _ in threads)
                )
            ]
        ],
        threads={t.id: t for _, t in threads},
        ci_states=list(ci_states),
    )
    node = review_node(30, reviewers=reviewers, loop=True, watch_ci=True)
    await rig(node, fake, **({"policy_override": policy} if policy else {}))

    async def details(*, repo, branch):
        sha = head() if head else git(rig.item.worktree, "rev-parse", "HEAD")
        return forge.MRDetails(
            1, "http://fake.forge/1", state, "t", "", True, (), branch, "main", sha
        )

    monkeypatch.setattr(fake, "mr_details", details)
    monkeypatch.setattr(waits, "pushed_at", lambda *_, **__: None)
    if tweak:
        tweak(fake)
    rig.verdicts = [{"findings": list(flags)}, second]
    rig.repairs = [repair]
    await walk_to_completion(rig)
    return fake


@pytest.fixture
def rig(item_on, database, run_dirs, monkeypatch, wait_clock, tmp_path):
    """`await rig(node, fake)` files the node as an item; `await rig.walk()`
    walks it from its own cursor with the clock where `rig.clock` put it.
    `rig.triaged` lists the tasks the walk launched an agent for, and each
    `triage` among them answers the next of `rig.verdicts` as a session of its
    own: a list is the findings it writes; a dict's `findings` are, and its other
    keys (`replies`, ...) go into the result file beside them. Each repair (a
    `.fix_loop.` task) writes the keys of the next of `rig.repairs` into its
    result file. `rig.fake` is the `FakeForge` it was given."""

    class Rig:
        triaged: list[str] = []
        #: The instruction each launch was given, parallel to `triaged`.
        instructions: list[str] = []
        verdicts: list[list[dict] | dict] = []
        repairs: list[dict] = []
        fake: forge.FakeForge | None = None

        async def __call__(self, node, fake, **item):
            monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
            # A harness for the reading task to select; what it launches is
            # replaced below, since only whether it is reached is asked.
            monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, ["true"])))
            self.triaged, self.instructions, self.verdicts, self.repairs = [], [], [], []
            #: What the judge answers, in turn: its `verdict` and the keys it writes.
            self.judges = []
            self.fake = fake

            async def launched(db, run_dirs, t, node, row, worktree, *, task, common, **_):
                self.triaged.append(task.path)
                self.instructions.append(_.get("instruction", ""))
                repair = ".fix_loop." in task.path and t.id != "judge"  # the judge only answers
                if repair:
                    # A repair that changes nothing stops the loop: each one writes something new.
                    (Path(worktree) / "repaired.txt").write_text(f"x{len(self.triaged)}")
                    git(worktree, "add", "repaired.txt")
                    git(worktree, "commit", "-qm", "repair")
                if AgentInput.BOT_FEEDBACK in t.inputs:
                    # The session's own copy of the collection, as the real launch makes.
                    round_prompts.bot_feedback(
                        db,
                        run_dirs,
                        row["id"],
                        node,
                        common["round"],
                        common["session_id"],
                        reply=await dispatch._reply_round_of(db, row, node, worktree),
                    )
                verdict = self.verdicts.pop(0) if t.id == "triage" and self.verdicts else []
                if t.id == "judge" and self.judges:
                    verdict = self.judges.pop(0)
                found = verdict.get("findings", []) if isinstance(verdict, dict) else verdict
                _, log, result = await _builtins.start_session(db, run_dirs, **common)
                status = await _builtins.finish_session(
                    db,
                    log,
                    result,
                    session_id=common["session_id"],
                    work_item_id=common["work_item_id"],
                    status="done",
                    log="scripted\n",
                    findings=found,
                )
                if repair and self.repairs:
                    extra = self.repairs.pop(0)
                elif isinstance(verdict, dict):
                    extra = {k: v for k, v in verdict.items() if k != "findings"}
                else:
                    extra = {}
                if extra:
                    data = json.loads(Path(result).read_text())
                    Path(result).write_text(json.dumps({**data, **extra}))
                return status, True

            monkeypatch.setattr(dispatch, "_launch_agent", launched)
            self.item = await item_on([node], **item)
            return self

        def moment(self, seconds):
            return wait_clock.start + timedelta(seconds=seconds)

        def clock(self, seconds):
            wait_clock.now = self.moment(seconds)

        async def walk(self):
            if self.item.status() == "waiting":
                await database.write(lambda c: store.mark_reentered(c, self.item.id))
            return await executor.run(
                database,
                run_dirs,
                work_item_id=self.item.id,
                policy=policy.Policy(
                    loops={},
                    default=policy.Cap(attempts=3, wall_clock_s=3600),
                    auto_escalate_stuck=False,
                ),
                launch=executor.LaunchContext(
                    repo_entry=entry_of({"setup_command": "", "forge": "github"})
                ),
            )

        def wait_session(self) -> dict:
            [wait] = [
                s
                for s in self.item.sessions("feedback")
                if s["hook_point"] == "feedback.settle.review"
            ]
            return wait

        def collection(self) -> dict | None:
            path = self.item.files(self.wait_session()["id"]).folder / "bot_feedback.json"
            return json.loads(path.read_text()) if path.exists() else None

    return Rig()
