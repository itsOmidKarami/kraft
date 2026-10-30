"""`kraft.review_reply`: the agent that answers review threads after a
`comment` review (docs/superpowers/specs/2026-09-27-review-flow-backend-design.md §3)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from support.addons import drop_store, install_release, pinned_chain
from support.harness import (
    IN_PLACE,
    entry_of,
    git,
    make_repo,
    seed_v1_library,
    write,
    write_harness_profiles,
)

from kraft import executor, inplace, item_paths, review_reply, reviewed_head, store

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
FAKE_AGENT_COMMAND = f"{sys.executable} {_FAKE_AGENT}"

LAUNCH = executor.LaunchContext(repo_entry=entry_of({"setup_command": ""}))

GATE = "review"

NODES = [
    {
        "id": "implementation",
        "kind": "exec",
        "tasks": [{"id": "a", "kind": "agent", "harness": "fake", "prompt": "Fix it."}],
    },
    {"id": GATE, "kind": "gate"},
]


def _seed(tmp_path, monkeypatch):
    """The `fake` harness launches `tests/support/fake_agent.py`, logged."""
    seed_v1_library(tmp_path / "templates", agent_command=FAKE_AGENT_COMMAND)
    prompts = tmp_path / "prompts.txt"
    argv = tmp_path / "argv.jsonl"
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts))
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv))
    return prompts, argv


async def _item(item_on, run_dirs, chain=NODES):
    """An item at a pending gate, with a real git repo standing in for its
    worktree (`review_reply.run` reads it through `read_only`).

    `.engineering/` is gitignored, matching every real Kraft repo (CLAUDE.md):
    otherwise every launch's own session-summary write -- required of every
    agent, `read_only` or not -- would read as a worktree change on its own.
    """
    it = await item_on(chain, GATE, status="needs_human")
    worktree = make_repo(run_dirs.worktrees, name=it.id)
    (worktree / ".gitignore").write_text(".engineering/\n")
    git(worktree, "add", ".gitignore")
    git(worktree, "commit", "-qm", "ignore .engineering")
    return it


async def _published_unanswered_thread(it, *, body="why a list?"):
    tid = await it.database.write(
        lambda c: store.create_thread(c, wid=it.id, gate=GATE, anchor_sha="deadbeef", body=body)
    )
    await it.database.write(
        lambda c: store.submit_review(
            c,
            wid=it.id,
            gate=GATE,
            outcome="comment",
            summary=None,
            head_sha="deadbeef",
            base_sha="deadbeef",
        )
    )
    return tid


@pytest.mark.parametrize("final", [False, True], ids=["a-gate", "the-final-gate"])
async def test_the_reply_agent_runs_under_the_gate_told_to_change_nothing(
    item_on, run_dirs, tmp_path, monkeypatch, final
):
    """At the final gate its session records the head it starts over, so a
    person's approval that stops it pins only a head it did not move
    (`reviewed_head.moved_under`).

    It is told to change nothing, and never to commit or run tests; it is
    denied no tool, which a harness without a deny list would refuse to
    launch -- the before/after comparison is what catches a write."""
    prompts, argv = _seed(tmp_path, monkeypatch)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    if final:
        monkeypatch.setattr(reviewed_head, "final_gate", lambda row: GATE)
    it = await _item(item_on, run_dirs)
    worktree = inplace.item_folder(it.run_dirs, it.row())
    git(worktree, "branch", store.branch_for(it.row()))
    head = git(worktree, "rev-parse", f"refs/heads/{store.branch_for(it.row())}")
    tid = await _published_unanswered_thread(it, body="why is this a list, not a set?")

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert status == "replied"
    [session] = it.sessions(GATE)
    assert session["hook_point"] == f"{GATE}.reply"
    [created] = it.events("worker_session_created")
    assert created["payload"].get("tips") == ({str(worktree): head} if final else None)

    argv_lines = argv.read_text().splitlines()
    assert argv_lines, "the fake agent was never launched"
    # The harness's own denials (Monitor) stay; the reply agent adds none.
    launches = [json.loads(line) for line in argv_lines]
    denied = {
        tool
        for a in launches
        for i, flag in enumerate(a[:-1])
        if flag == "--disallowed-tools"
        for tool in a[i + 1].split(",")
    }
    assert "Monitor" in denied
    assert not denied & {"Edit", "NotebookEdit"}

    prompt = prompts.read_text()
    launched = argv.read_text() + prompt
    assert "Kraft compares it after you" in launched
    assert "Commit everything you change" not in launched
    assert "Run the tests your change touches" not in launched
    assert tid in prompt
    assert "why is this a list, not a set?" in prompt


async def test_a_review_reply_restores_the_pinned_store(
    item_on, run_dirs, repo, tmp_path, monkeypatch
):
    """The store the item pinned is gone and the lock has moved on: the reply
    agent's launch puts the item's own version back."""
    _seed(tmp_path, monkeypatch)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    write_harness_profiles(
        Path(os.environ["KRAFT_HOME"]) / "config", {"fake": {"provider": "fake"}}
    )
    pins, store_dir = install_release(tmp_path, "1.0.0")
    install_release(tmp_path, "1.1.0")
    drop_store(store_dir)
    it = await _item(item_on, run_dirs, pinned_chain(NODES, pins, repo=repo))
    await _published_unanswered_thread(it)

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert status == "replied"
    assert store_dir.is_dir()


async def test_a_sandbox_without_network_gets_no_reply_agent(
    item_on, run_dirs, tmp_path, monkeypatch
):
    """It answers by `kraft item reply`, which such a sandbox cannot reach."""
    _seed(tmp_path, monkeypatch)
    it = await _item(item_on, run_dirs)
    await _published_unanswered_thread(it)
    launch = executor.LaunchContext(
        repo_entry=entry_of({"setup_command": "", "sandbox": {"kind": "docker", "image": "x"}})
    )

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=launch,
    )

    assert status == "no_channel"
    assert it.sessions(GATE) == []
    [skipped] = it.events("reply_agent_skipped")
    assert skipped["payload"]["gate"] == GATE


async def test_nothing_unanswered_launches_nothing(item_on, run_dirs, tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    it = await _item(item_on, run_dirs)

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert status == "nothing_to_answer"
    assert it.sessions(GATE) == []


async def test_a_crashing_reply_agent_leaves_the_thread_unanswered_not_escalated(
    item_on, run_dirs, tmp_path, monkeypatch
):
    """PR #236: a reply agent that raises (launch failure, git error) must not
    reach `deps.guard`'s `mark_needs_human` -- that would pile a second stop
    reason on top of the pending gate and page a human for an agent's crash,
    not a person's problem."""
    _seed(tmp_path, monkeypatch)
    it = await _item(item_on, run_dirs)
    await _published_unanswered_thread(it)
    await it.database.write(lambda c: store.request_gate(c, it.id, GATE, GATE))

    async def _boom(*a, **kw):
        raise RuntimeError("agent launch failed")

    monkeypatch.setattr(review_reply._agent, "run_agent_task", _boom)

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert status == "failed"
    assert it.events("work_item_needs_human") == []
    assert it.status() == "needs_human"
    assert executor.pending_gate(it.database, it.id) == GATE
    [event] = it.events("reply_agent_failed")
    assert event["payload"]["gate"] == GATE
    assert "agent launch failed" in event["payload"]["error"]


async def test_a_write_to_the_worktree_is_recorded_not_escalated(
    item_on, run_dirs, tmp_path, monkeypatch
):
    _seed(tmp_path, monkeypatch)  # default fake agent mode ("fix") edits calc.py
    it = await _item(item_on, run_dirs)
    await _published_unanswered_thread(it)

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert status == "wrote"
    [event] = it.events("reply_agent_wrote")
    assert "calc.py" in event["payload"]["files"]
    assert it.status() == "needs_human"
    assert it.events("work_item_needs_human") == []


async def test_a_failure_before_the_launch_is_recorded_not_escalated(
    item_on, run_dirs, tmp_path, monkeypatch
):
    """Kraft-dl5fl 6: resolving the agent's config can fail with more than a
    `SteeringError`; that must reach `reply_agent_failed` too, not escape the
    spawned task unrecorded."""
    _seed(tmp_path, monkeypatch)
    it = await _item(item_on, run_dirs)
    await _published_unanswered_thread(it)
    await it.database.write(lambda c: store.request_gate(c, it.id, GATE, GATE))

    def _bad_config(*a, **kw):
        raise KeyError("no such profile")

    monkeypatch.setattr(review_reply._agent, "resolve_agent_task", _bad_config)

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert status == "failed"
    assert it.events("work_item_needs_human") == []
    [event] = it.events("reply_agent_failed")
    assert "no such profile" in event["payload"]["error"]


async def test_an_item_model_stored_before_model_ids_were_checked_never_reaches_the_reply_agent(
    item_on, run_dirs, tmp_path, monkeypatch
):
    """1.4 stored any text as the item's model (`--model "sonnet 4"`). The
    reply agent holds it to the model-id rule before its launch, and records
    why as `reply_agent_failed` instead of putting it on a command line."""
    _, argv = _seed(tmp_path, monkeypatch)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    it = await _item(item_on, run_dirs)
    await _published_unanswered_thread(it)
    await it.database.write(
        lambda c: store.set_agent_overrides(c, it.id, json.dumps({"model": "sonnet 4"}))
    )

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert status == "failed"
    [event] = it.events("reply_agent_failed")
    assert "stored model override is not a model id" in event["payload"]["error"]
    assert f"kraft item set-overrides {it.id} --clear" in event["payload"]["error"]
    assert not argv.exists() or argv.read_text() == ""


async def test_the_comment_reviews_summary_reaches_the_reply_agent(
    item_on, run_dirs, tmp_path, monkeypatch
):
    """Kraft-dl5fl 5: a `comment` review's summary is part of what was asked."""
    prompts, _argv = _seed(tmp_path, monkeypatch)
    it = await _item(item_on, run_dirs)
    await it.database.write(
        lambda c: store.create_thread(c, wid=it.id, gate=GATE, anchor_sha="deadbeef", body="why?")
    )
    await it.database.write(
        lambda c: store.submit_review(
            c,
            wid=it.id,
            gate=GATE,
            outcome="comment",
            summary="Mostly fine; one question.",
            head_sha="deadbeef",
            base_sha="deadbeef",
        )
    )

    await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert "Mostly fine; one question." in prompts.read_text()


@pytest.mark.parametrize("_", [None], ids=["another-item-uses-it"])
async def test_no_reply_agent_starts_without_its_checkout(
    item_on, run_dirs, tmp_path, monkeypatch, _
):
    """Refused where its session would be written; the caller queues or skips it."""
    _seed(tmp_path, monkeypatch)
    await item_on(NODES, GATE, wid="a", target=IN_PLACE, title="A")
    it = await item_on(NODES, GATE, wid="b", target=IN_PLACE, status="needs_human")
    await _published_unanswered_thread(it)

    with pytest.raises(store.CheckoutBusy, match="in use by A"):
        await review_reply.run(
            it.database,
            it.run_dirs,
            work_item_id=it.id,
            gate=GATE,
            nodes=it.chain.chain.nodes,
            launch=LAUNCH,
        )

    assert it.sessions() == []
    assert not it.events("reply_agent_failed")


def _copies(run_dirs, it):
    """What is left in any of the item's session copies of its branch."""
    return list((run_dirs.base / item_paths.ITEMS / it.id / "sessions").glob("*/copy/*"))


_ID = "git -c user.email=t@t -c user.name=t"


@pytest.mark.parametrize(
    "shell,files",
    [
        pytest.param("echo agent >> calc.py", ["calc.py"], id="dirty-file"),
        pytest.param(
            f"echo x > tip.txt && git add tip.txt && {_ID} commit -qm tip"
            " && git push -q {repo} HEAD:{branch} && git reset -q --hard HEAD~1",
            ["tip.txt"],
            id="moved-tip",
        ),
    ],
)
async def test_an_in_place_reply_never_touches_the_persons_checkout(
    item_on, run_dirs, repo, tmp_path, monkeypatch, shell, files
):
    """It works in a copy of the item's branch: the person, on `main` with
    unsaved work, keeps both. A write in the copy, or a commit pushed to their
    branch, is still recorded."""
    _seed(tmp_path, monkeypatch)
    it = await item_on(NODES, GATE, target=IN_PLACE, status="needs_human")
    branch = f"refs/heads/{store.branch_for(it.row())}"
    git(repo, "branch", store.branch_for(it.row()))
    write(repo, "calc.py", "the person's unsaved work\n")
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", shell.format(repo=repo, branch=branch))
    await _published_unanswered_thread(it)

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert status == "wrote"
    [event] = it.events("reply_agent_wrote")
    assert event["payload"]["files"] == files
    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert (repo / "calc.py").read_text() == "the person's unsaved work\n"
    assert git(repo, "status", "--porcelain") == "M calc.py"
    assert _copies(run_dirs, it) == []


async def test_a_reply_agent_whose_copy_fails_records_a_failure(
    item_on, run_dirs, repo, tmp_path, monkeypatch
):
    """The item's branch is gone: no agent runs anywhere, the failure is
    recorded, and the item is not stopped nor the branch made again."""
    prompts, _argv = _seed(tmp_path, monkeypatch)
    it = await item_on(NODES, GATE, target=IN_PLACE, status="needs_human")
    branch = store.branch_for(it.row())
    await _published_unanswered_thread(it)

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert status == "failed"
    [event] = it.events("reply_agent_failed")
    assert f"could not make a copy of {branch} to work in" in event["payload"]["error"]
    assert not prompts.exists()
    assert [s["status"] for s in it.sessions()] == ["config_error"]
    assert it.status() == "needs_human"
    assert it.events("work_item_needs_human") == []
    assert git(repo, "for-each-ref", f"refs/heads/{branch}") == ""
    assert _copies(run_dirs, it) == []


async def test_an_in_place_reply_agent_that_writes_nothing_did_not_write(
    item_on, repo, tmp_path, monkeypatch
):
    """Its snapshot reads the item's branch, not the checkout's HEAD: the
    person on their own branch is not a write by the reply agent."""
    _seed(tmp_path, monkeypatch)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    it = await item_on(NODES, GATE, target=IN_PLACE, status="needs_human")
    git(repo, "branch", store.branch_for(it.row()))
    write(repo, "MINE", "the person's own commit\n")
    git(repo, "add", "MINE")
    git(repo, "commit", "-qm", "mine")
    await _published_unanswered_thread(it)

    status = await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert status == "replied"
    assert it.events("reply_agent_wrote") == []


async def test_the_reply_agent_runs_where_the_item_places_its_node(
    item_on, run_dirs, tmp_path, monkeypatch
):
    _seed(tmp_path, monkeypatch)
    it = await _item(item_on, run_dirs)
    await it.database.write(
        lambda c: store.set_agent_overrides(c, it.id, json.dumps({"runner": "gpu"}))
    )
    await _published_unanswered_thread(it)
    seen = []

    async def _capture(_db, _rd, **kw):
        seen.append(kw)
        return "done"

    monkeypatch.setattr(review_reply._agent, "run_agent_task", _capture)

    await review_reply.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate=GATE,
        nodes=it.chain.chain.nodes,
        launch=LAUNCH,
    )

    assert [kw["runner"] for kw in seen] == ["gpu"]
