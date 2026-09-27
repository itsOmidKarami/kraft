"""`kraft.review_reply`: the agent that answers review threads after a
`comment` review (docs/superpowers/specs/2026-09-27-review-flow-backend-design.md §3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from support.harness import entry_of, make_repo, seed_v1_library

from kraft import executor, review_reply, store

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


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


async def _item(item_on, run_dirs):
    """An item at a pending gate, with a real git repo standing in for its
    worktree (`review_reply.run` reads it through `read_only`).

    `.engineering/` is gitignored, matching every real Kraft repo (CLAUDE.md):
    otherwise every launch's own session-summary write -- required of every
    agent, `read_only` or not -- would read as a worktree change on its own.
    """
    it = await item_on(NODES, GATE, status="needs_human")
    worktree = make_repo(run_dirs.worktrees, name=it.id)
    (worktree / ".gitignore").write_text(".engineering/\n")
    _git(worktree, "add", ".gitignore")
    _git(worktree, "commit", "-qm", "ignore .engineering")
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


async def test_the_reply_agent_runs_under_the_gate_with_edit_denied(
    item_on, run_dirs, tmp_path, monkeypatch
):
    prompts, argv = _seed(tmp_path, monkeypatch)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    it = await _item(item_on, run_dirs)
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

    argv_lines = argv.read_text().splitlines()
    assert argv_lines, "the fake agent was never launched"
    deny_line = next(line for line in argv_lines if "--disallowed-tools" in line)
    assert "Edit" in deny_line and "NotebookEdit" in deny_line

    prompt = prompts.read_text()
    assert tid in prompt
    assert "why is this a list, not a set?" in prompt


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
