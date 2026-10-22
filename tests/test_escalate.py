"""One escalation turn, driven directly against a real DB — no subprocess,
no chain. `escalate.dispatch`'s only external dependency is
`_agent.run_agent_task`, monkeypatched the same way
`test_adapters_agent.py::_capture_cmd` does, plus a fake log file so
`usage.READERS["claude-stream-json"].session_id` has something to read.
"""

from __future__ import annotations

import contextlib
import json

import pytest
from support.addons import drop_store, install_release, pinned_chain
from support.harness import entry_of, v1_chain, v1_resolved, write_harness_profiles
from support.never_signal import NEVER_SIGNAL, NEVER_SIGNAL_TEXT

from kraft import escalate, executor, store
from kraft import events as events_mod
from kraft.db import Database
from kraft.paths import RunDirs

#: The chain every item here is on: an escalation turn runs under the policy of
#: the node its item stopped at, read from the item's snapshot (Kraft-l8ype).
_NODE = {
    "id": "implementation",
    "kind": "exec",
    "tasks": [{"id": "work", "kind": "subprocess", "command": "true"}],
}
_CHAIN = v1_chain([_NODE], repo="/r")


@pytest.fixture(autouse=True)
def escalation_profile(tmp_path, monkeypatch):
    """The profile `escalate.ESCALATION_TASK` selects, in this test's own
    templates dir: an escalation turn resolves its harness like any agent task,
    so without one every turn here would stop as a config error."""
    templates = tmp_path / "escalation-templates"
    write_harness_profiles(
        templates, {"claude": {"provider": "claude", "defaults": {"model": "sonnet"}}}
    )
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(templates))
    return templates


async def _seed_needs_human(database, rd, wid: str, chain=None, node="implementation") -> None:
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="the thing that broke",
            description="fix the widget so it stops throwing",
            repo=str(rd.base),
            chain_template="quick-task",
            chain_definition="{}",
            materialized_chain=(chain or _CHAIN).to_json(),
        )
    )
    await database.write(lambda c: store.enter_node(c, wid, node))
    await database.write(
        lambda c: store.mark_needs_human(
            c, wid, node, "task failed in node implementation", kind="failed"
        )
    )


async def _seed_paused(database, rd, wid: str) -> None:
    """A `paused` item with a current node but no `needs_human` reason to
    its name -- the Kraft-k5ol shape `escalate_work_item` now accepts."""
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="the thing that's paused",
            description="",
            repo=str(rd.base),
            chain_template="quick-task",
            chain_definition="{}",
            materialized_chain=_CHAIN.to_json(),
        )
    )
    await database.write(lambda c: store.enter_node(c, wid, "implementation"))
    await database.write(lambda c: store.pause_work_item(c, wid, []))


async def test_an_escalation_restores_the_pinned_store(monkeypatch, database, run_dirs, tmp_path):
    """The store the item pinned is gone and the lock has moved on: the
    escalation's launch puts the item's own version back."""

    async def fake_run_agent_task(db, run_dirs, *, session_id, **kw):
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps({"type": "system", "session_id": "cli-abc"}) + "\n")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)
    pins, store_dir = install_release(tmp_path, "1.0.0", provider="claude")
    install_release(tmp_path, "1.1.0", provider="claude")
    drop_store(store_dir)
    nodes = [_NODE]
    await _seed_needs_human(database, run_dirs, "w1", chain=pinned_chain(nodes, pins, repo="/r"))

    await escalate.dispatch(
        database,
        run_dirs,
        work_item_id="w1",
        message="any update?",
        launch=executor.LaunchContext(repo_entry=None, skills_dir=None),
    )

    assert store_dir.is_dir()


async def test_dispatch_on_a_paused_item_does_not_claim_needs_human_or_offer_retry(
    monkeypatch, database, run_dirs
):
    """The prompt built for a `paused` item must not lie about its status or
    tell the agent to run a self-action route that 409s on it: `kraft item
    retry` refuses anything but `needs_human`, and `kraft item resume` 409s
    while this very escalation session is running (Kraft-k5ol code-review
    finding)."""
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, cwd, task_instruction, **kw):
        seen["task_instruction"] = task_instruction
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "cli-abc"}) + "\n"
        )
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    wid = "w1"
    await _seed_paused(database, run_dirs, wid)
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(
        database,
        run_dirs,
        work_item_id=wid,
        message="any update?",
        launch=launch,
    )
    instruction = seen["task_instruction"]
    assert "Status: paused" in instruction
    assert "Status: needs_human" not in instruction
    assert "Why it is stopped" not in instruction
    assert "run `kraft item retry` yourself" not in instruction
    assert "will fail here" in instruction


async def test_dispatch_sends_the_message_and_state_and_marks_the_session_non_worker(
    monkeypatch, database, run_dirs
):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, cwd, task_instruction, **kw):
        seen["kwargs"] = kw
        seen["task_instruction"] = task_instruction
        seen["cwd"] = cwd
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "cli-abc"}) + "\n"
        )
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    status = await escalate.dispatch(
        database,
        run_dirs,
        work_item_id=wid,
        message="the widget is at src/widget.py",
        launch=launch,
    )
    row = database.read(
        lambda c: c.execute(
            "SELECT escalation_session_id FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
    )
    cli_session_id = row[0]
    assert status == "done"
    assert cli_session_id == "cli-abc"
    assert seen["kwargs"]["identify_as_worker"] is False
    assert seen["kwargs"]["resume_session_id"] is None  # first turn
    assert "the widget is at src/widget.py" in seen["task_instruction"]
    assert "task failed in node implementation" in seen["task_instruction"]


async def test_dispatch_resumes_an_existing_thread(monkeypatch, database, run_dirs):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, resume_session_id, **kw):
        seen["resume_session_id"] = resume_session_id
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    await database.write(lambda c: store.set_escalation_session(c, wid, "cli-existing"))
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(
        database, run_dirs, work_item_id=wid, message="try again", launch=launch
    )
    assert seen["resume_session_id"] == "cli-existing"


async def test_dispatch_default_continues_the_latest_thread(monkeypatch, database, run_dirs):
    seen = {}

    async def fake_run_agent_task(
        db, run_dirs, *, session_id, work_item_id, node_id, hook_point, cwd, task_instruction, **kw
    ):
        seen["kwargs"] = kw
        # Mirrors what `_subprocess.run_task` really does on every dispatch --
        # `store.latest_escalation_thread`, which the *next* dispatch() call
        # reads, only sees this turn once its row actually lands.
        await db.write(
            lambda c: store.create_session(
                c,
                id=session_id,
                work_item_id=work_item_id,
                node_id=node_id,
                hook_point=hook_point,
                log_path=str(kw["files"].log),
                result_path=str(kw["files"].result),
                thread=kw["thread"],
            )
        )
        await kw["on_session"]()  # the session is written
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "cli-1"}) + "\n"
        )
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(database, run_dirs, work_item_id=wid, message="m1", launch=launch)
    assert seen["kwargs"]["thread"] == 1
    assert seen["kwargs"]["resume_session_id"] is None  # first-ever turn
    await escalate.dispatch(database, run_dirs, work_item_id=wid, message="m2", launch=launch)
    assert seen["kwargs"]["thread"] == 1  # still thread 1, no new_thread asked
    assert seen["kwargs"]["resume_session_id"] == "cli-1"  # resumes the CLI thread
    evts = database.read(lambda c: events_mod.read_after(c, 0, wid))
    turns = [e["payload"]["turn"] for e in evts if e["type"] == "escalation_message"]
    assert turns == [1, 2]


async def test_dispatch_new_thread_starts_fresh_and_bumps_thread_number(
    monkeypatch, database, run_dirs
):
    seen = {}

    async def fake_run_agent_task(
        db, run_dirs, *, session_id, work_item_id, node_id, hook_point, cwd, task_instruction, **kw
    ):
        seen["kwargs"] = kw
        await db.write(
            lambda c: store.create_session(
                c,
                id=session_id,
                work_item_id=work_item_id,
                node_id=node_id,
                hook_point=hook_point,
                log_path=str(kw["files"].log),
                result_path=str(kw["files"].result),
                thread=kw["thread"],
            )
        )
        await kw["on_session"]()  # the session is written
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": f"cli-{session_id[:4]}"})
            + "\n"
        )
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(database, run_dirs, work_item_id=wid, message="m1", launch=launch)
    assert seen["kwargs"]["thread"] == 1
    await escalate.dispatch(
        database, run_dirs, work_item_id=wid, message="m2", launch=launch, new_thread=True
    )
    assert seen["kwargs"]["thread"] == 2
    # A fresh CLI session despite escalation_session_id already being
    # set from the first turn.
    assert seen["kwargs"]["resume_session_id"] is None
    evts = database.read(lambda c: events_mod.read_after(c, 0, wid))
    msg_events = [e for e in evts if e["type"] == "escalation_message"]
    payload = msg_events[-1]["payload"]
    assert (payload["thread"], payload["turn"]) == (2, 1)  # turn restarts in the new thread


async def test_dispatch_new_thread_on_first_ever_escalation_is_still_thread_one(
    monkeypatch, database, run_dirs
):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, cwd, task_instruction, **kw):
        seen["kwargs"] = kw
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(
        database, run_dirs, work_item_id=wid, message="m1", launch=launch, new_thread=True
    )
    # No prior thread to be "new" from -- there is no prior thread at all.
    assert seen["kwargs"]["thread"] == 1
    assert seen["kwargs"]["resume_session_id"] is None  # same as it would have been either way


@pytest.mark.parametrize(
    "fails", [None, "launch", "archive"], ids=["launched", "refused", "archive-fails"]
)
async def test_dispatch_records_the_message_as_an_event(monkeypatch, database, run_dirs, fails):
    """Once, a launch refused before its session was written included, or one
    whose turn failed before its launch: it counts as a turn all the same."""
    from kraft.adapters import agent

    async def archive_fails(*a, **kw):
        raise RuntimeError("disk full")

    if fails == "archive":
        monkeypatch.setattr(escalate, "_archive_last_turn", archive_fails)

    async def fake_run_agent_task(db, run_dirs, *, session_id, cwd, task_instruction, **kw):
        if fails == "launch":
            raise agent.LaunchRefused("no such tool")
        await kw["on_session"]()  # the session is written
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "cli-abc"}) + "\n"
        )
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    with pytest.raises(RuntimeError) if fails == "archive" else contextlib.nullcontext():
        await escalate.dispatch(
            database, run_dirs, work_item_id=wid, message="look at src/widget.py", launch=launch
        )
    evs = database.read(lambda c: events_mod.read_after(c, 0, wid))
    msg_events = [e for e in evs if e["type"] == "escalation_message"]
    assert len(msg_events) == 1
    assert msg_events[0]["payload"]["message"] == "look at src/widget.py"
    assert isinstance(msg_events[0]["payload"]["session_id"], str)


async def test_dispatch_auto_uses_the_synthesized_message_and_auto_opening(
    monkeypatch, database, run_dirs
):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, task_instruction, **kw):
        seen["task_instruction"] = task_instruction
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(
        database,
        run_dirs,
        work_item_id=wid,
        message="(Auto-escalated: no one has looked at this yet.)",
        launch=launch,
        auto=True,
    )
    assert "This is an automatic escalation" in seen["task_instruction"]
    assert "Kraft itself is asking you" in seen["task_instruction"]


async def test_dispatch_manual_is_unchanged_by_the_auto_param(monkeypatch, database, run_dirs):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, task_instruction, **kw):
        seen["task_instruction"] = task_instruction
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(
        database, run_dirs, work_item_id=wid, message="please look", launch=launch
    )
    assert "This is an escalation: a human is asking" in seen["task_instruction"]
    assert "automatic escalation" not in seen["task_instruction"]


async def test_dispatch_auto_tags_the_event(monkeypatch, database, run_dirs):
    async def fake_run_agent_task(db, run_dirs, *, session_id, **kw):
        await kw["on_session"]()  # the session is written
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(
        database, run_dirs, work_item_id=wid, message="msg", launch=launch, auto=True
    )
    evs = database.read(lambda c: events_mod.read_after(c, 0, wid))
    msg_events = [e for e in evs if e["type"] == "escalation_message"]
    assert msg_events[0]["payload"]["auto"] is True


#: `implementation`, then `merge`, which merges and declares `on_merge_rejected`.
_MERGING = v1_chain(
    [
        _NODE,
        {
            "id": "merge",
            "kind": "exec",
            "tasks": [{"id": "merge", "kind": "forge", "target": "mr.merge"}],
            "on_merge_rejected": {
                "restart_from": "implementation",
                "repair": {"tasks": [_NODE["tasks"][0]]},
            },
        },
    ],
    repo="/r",
)


@pytest.mark.parametrize(
    ("chain", "node", "commits", "final"),
    [
        (_MERGING, "merge", False, False),
        (_MERGING, "merge", True, False),
        (_CHAIN, "implementation", False, False),
        (_CHAIN, "implementation", False, True),
    ],
    ids=["a-merge-node", "a-merge-node-whose-agent-commits", "any-other-node", "the-final-gate"],
)
async def test_a_turn_on_a_merge_node_records_its_tips_and_is_told_a_commit_goes_back(
    monkeypatch, database, run_dirs, chain, node, commits, final
):
    """What `walk.hold_after_escalation` compares once the turn is over -- the
    branch tips before the agent ran, or its commit is compared with itself --
    rides the turn's `escalation_message`, and the agent is told so. At the
    final gate its session carries them (`reviewed_head.moved_under`)."""
    from support.harness import commit_all, git, make_repo, write

    seen = {}
    worktree = run_dirs.worktrees / "w1"
    await _seed_needs_human(database, run_dirs, "w1", chain, node)
    if final:
        monkeypatch.setattr("kraft.reviewed_head.final_gate", lambda row: node)
    if commits or final:
        make_repo(run_dirs.worktrees, "w1")
        row = database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())
        git(worktree, "checkout", "-q", "-b", store.branch_for(row))
    before = git(worktree, "rev-parse", "HEAD") if commits or final else None

    async def fake_run_agent_task(db, run_dirs, *, session_id, task_instruction, **kw):
        seen["task_instruction"] = task_instruction
        seen["tips"] = kw["tips"]
        await kw["on_session"]()
        kw["files"].log.parent.mkdir(parents=True, exist_ok=True)
        kw["files"].log.write_text("")
        if commits:
            write(worktree, "fix.txt", "the agent's\n")
            commit_all(worktree, "the escalation's commit")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(database, run_dirs, work_item_id="w1", message="m", launch=launch)

    evs = database.read(lambda c: events_mod.read_after(c, 0, "w1"))
    [message] = [e["payload"] for e in evs if e["type"] == "escalation_message"]
    merging = node == "merge"
    recorded = message.get("merge_node")
    tips = {str(worktree): before}
    assert recorded == ({"node_id": "merge", "tips": tips} if merging else None)
    told = "Kraft sends the work item back to node implementation"
    assert (told in seen["task_instruction"]) is merging
    assert seen["tips"] == (tips if final else None)


def test_dispatch_forwards_the_repo_s_resolved_sandbox(tmp_path, monkeypatch):
    """Kraft-rki: a repo's `sandbox:` reaches the escalation turn's
    `run_agent_task`, through `dispatch.item_sandbox` (Ruling 189); a turn
    that dropped it is the silent 'sometimes not actually sandboxed' case the
    spec rules out.
    """
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, sandbox, **kw):
        seen["sandbox"] = sandbox
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "cli-abc"}) + "\n"
        )
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_needs_human(database, rd, wid)
            launch = executor.LaunchContext(
                repo_entry=entry_of({"sandbox": {"kind": "docker", "image": "kraft-worker:py"}}),
                skills_dir=None,
            )
            return await escalate.dispatch(
                database, rd, work_item_id=wid, message="msg", launch=launch
            )
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())
    assert seen["sandbox"] == {"kind": "docker", "image": "kraft-worker:py"}


@pytest.mark.parametrize(
    ("network", "told"),
    [({"runtime": {"allow": ["x.io"]}}, True), (None, False)],
    ids=["network", "no-network"],
)
async def test_a_sandboxed_turn_is_told_to_retry_only_with_a_route_to_kraft(
    monkeypatch, database, run_dirs, network, told
):
    """Its channel carries `kraft item retry` to the self-retry carve-out; a
    sandbox without `network:` has none, so the turn reports instead."""
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, task_instruction, **kw):
        seen["task_instruction"] = task_instruction
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)
    await _seed_needs_human(database, run_dirs, "w1")
    sandbox = {"kind": "docker", "image": "x", **({"network": network} if network else {})}
    launch = executor.LaunchContext(repo_entry=entry_of({"sandbox": sandbox}), skills_dir=None)
    await escalate.dispatch(database, run_dirs, work_item_id="w1", message="m", launch=launch)
    instruction = seen["task_instruction"]
    assert ("run `kraft item retry` yourself" in instruction) is told
    assert ("no route to Kraft" in instruction) is not told


async def test_escalation_running_reports_a_pending_or_running_session(database, run_dirs):

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    assert escalate.escalation_running(database, wid) is None
    # The second live at once is the newer: it is answered, though its id
    # sorts after the older's.
    for sid in ("s1", "z-newer"):
        await database.write(
            lambda c, sid=sid: store.create_session(
                c,
                id=sid,
                work_item_id=wid,
                node_id="implementation",
                hook_point="escalation",
                log_path=str(run_dirs.logs / f"{sid}.log"),
                result_path=str(run_dirs.logs / f"{sid}.json"),
            )
        )
        assert escalate.escalation_running(database, wid) == sid


async def test_last_escalation_status_is_the_newest_sessions_status(database, run_dirs):

    wid = "w1"
    await _seed_needs_human(database, run_dirs, wid)
    assert escalate.last_escalation_status(database, wid) is None
    await database.write(
        lambda c: store.create_session(
            c,
            id="s1",
            work_item_id=wid,
            node_id="implementation",
            hook_point="escalation",
            log_path=str(run_dirs.logs / "s1.log"),
            result_path=str(run_dirs.logs / "s1.json"),
        )
    )
    await database.write(lambda c: store.session_exited(c, "s1", "needs_context", work_item_id=wid))
    assert escalate.last_escalation_status(database, wid) == "needs_context"


async def test_dispatch_resolves_its_agent_through_its_harness_profile(
    monkeypatch, database, run_dirs, escalation_profile
):
    """`agent-roles-use-ordinary-agent-task-runtime-configuration`: an
    escalation turn is an ordinary `AgentTask` on a `harnesses.yaml` profile,
    so the profile's provider, executable and defaults reach the launch --
    never a hardcoded `claude` command (Kraft-jxu39), and never a harness id
    special to escalation."""
    write_harness_profiles(
        escalation_profile,
        {
            "claude": {
                "provider": "claude",
                "executable": "/opt/fake-claude",
                "defaults": {"model": "opus", "effort": "high"},
            }
        },
    )
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, command, harness, **kw):
        seen.update(command=command, harness=harness, model=kw["model"], effort=kw["effort"])
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    await _seed_needs_human(database, run_dirs, "w1")
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(database, run_dirs, work_item_id="w1", message="m", launch=launch)
    assert seen == {
        "command": "/opt/fake-claude",
        "harness": "claude",
        "model": "opus",
        "effort": "high",
    }


async def test_an_escalation_on_an_unavailable_profile_launches_nothing(
    monkeypatch, database, run_dirs, escalation_profile
):
    """`unavailable-selected-harness-needs-human`, for the escalation role too:
    the turn is recorded as a config error naming the profile, and nothing is
    substituted for it."""
    write_harness_profiles(escalation_profile, {"claude": {"provider": "claude", "enabled": False}})
    launched = []

    async def fake_run_agent_task(*a, **kw):
        launched.append(kw)
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)
    await _seed_needs_human(database, run_dirs, "w1")
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)

    status = await escalate.dispatch(
        database, run_dirs, work_item_id="w1", message="m", launch=launch
    )

    assert status == "config_error"
    assert launched == []
    [session] = database.read(
        lambda c: c.execute(
            "SELECT status, log_path FROM worker_sessions WHERE hook_point = 'escalation'"
        ).fetchall()
    )
    assert session["status"] == "config_error"
    assert "'claude'" in open(session["log_path"]).read()


@pytest.mark.parametrize("named", [False, True], ids=["unnamed", "named"])
async def test_an_escalation_launch_carries_the_rule_only_when_the_repo_names_it(
    monkeypatch, database, run_dirs, named
):
    """An escalation turn is an agent launch outside any chain, with full
    tools, and resolves repository steering the same as any other launch --
    the rule only when the repo names `NEVER_SIGNAL`. Only the spawn is
    faked, so the real context builder runs."""
    from kraft.adapters import agent as agent_mod
    from kraft.policy import InstancePolicy, InstancePolicyInput
    from kraft.templates.environment import WorkItemTarget

    launched = {}

    async def _spawn(_db, run_dirs, *, cmd, session_id, **_kw):
        launched["argv"] = "\n".join(cmd)
        log_path = _kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return "done"

    monkeypatch.setattr(agent_mod._subprocess, "run_task", _spawn)

    nodes = [
        {
            "id": "implementation",
            "kind": "exec",
            "tasks": [{"id": "work", "kind": "subprocess", "command": "true"}],
        }
    ]
    # Keyed by `str(run_dirs.base)`, not an arbitrary path: `_seed_needs_human`
    # files the item's `repo` column as `str(rd.base)`, and `escalate.dispatch`
    # now looks frozen steering up by that recorded `item_repo` (Kraft-jzdyp,
    # #187), not by the launch's live `repo_entry.path`.
    chain = v1_resolved(nodes).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
        repository_steering={
            str(run_dirs.base): ({NEVER_SIGNAL: NEVER_SIGNAL_TEXT} if named else {})
        },
    )
    await _seed_needs_human(database, run_dirs, "w1", chain=chain)
    await escalate.dispatch(
        database,
        run_dirs,
        work_item_id="w1",
        message="any update?",
        launch=executor.LaunchContext(repo_entry=entry_of({"path": "/repo"}), skills_dir=None),
    )
    assert (NEVER_SIGNAL_TEXT in launched["argv"]) == named


@pytest.mark.parametrize("new_thread", [False, True], ids=["resumed", "fresh"])
async def test_a_resumed_escalation_keeps_its_original_runtime(
    monkeypatch, database, run_dirs, new_thread
):
    """`resumed-escalation-preserves-original-runtime`: the escalation profile
    changed between two turns. A turn that resumes the thread runs on the
    thread's original harness and options; only a new thread takes the new
    ones (`manual-escalation-may-start-fresh`)."""
    launches = []

    async def fake_run_agent_task(db, run_dirs, *, session_id, **kw):
        await kw["on_session"]()  # the session is written
        launches.append(kw)
        log_path = kw["files"].log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "cli-1"}) + "\n"
        )
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)
    resolve = escalate._agent.resolve_agent_task
    model = {"now": "opus"}

    def profile_says(*args, **kwargs):
        return resolve(*args, **kwargs)._replace(model=model["now"], effort="high")

    monkeypatch.setattr("kraft.escalate._agent.resolve_agent_task", profile_says)
    await _seed_needs_human(database, run_dirs, "w1")
    launch = executor.LaunchContext(repo_entry=None, skills_dir=None)
    await escalate.dispatch(database, run_dirs, work_item_id="w1", message="1", launch=launch)
    model["now"] = "sonnet"

    await escalate.dispatch(
        database, run_dirs, work_item_id="w1", message="2", launch=launch, new_thread=new_thread
    )

    first, second = launches
    assert second["resume_session_id"] == (None if new_thread else "cli-1")
    assert (second["model"], second["effort"]) == ("sonnet" if new_thread else "opus", "high")
    assert second["harness"] == first["harness"]
