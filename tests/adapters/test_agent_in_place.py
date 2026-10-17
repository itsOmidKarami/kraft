"""`kraft.adapters.agent.run_agent_task` for an in-place item, which runs in a
person's own checkout: what its agent is told, and the take, mark and check
an agent outside the walk (an escalation, reply or gate review) runs around
itself, as `dispatch` does around a walk task."""

from pathlib import Path

import pytest
from support.harness import IN_PLACE, git, v1_node, write

from kraft import inplace, store
from kraft.adapters import agent

from .test_agent import PY_FAKE, _system_prompt


def test_an_in_place_agent_is_told_whose_checkout_it_is(run, in_place):
    prompt = _system_prompt(run(**in_place)["cmd"])
    for phrase in (
        "You are working in a person's own checkout, on this work item's branch `kraft/w1`.",
        "`git --literal-pathspecs add -- <paths>`, then "
        '`git --literal-pathspecs commit -m "..." -- <paths>`',
        "Never `git add -A`, `git add .`, `git commit -a`, `git stash`, `git checkout`, "
        "`git switch` or `git reset`",
        "Do not push and do not merge",
        "Exception: `.engineering/`",
    ):
        assert phrase in prompt
    assert "destroyed with the worktree" not in prompt
    assert "git worktree cut for this work item" not in prompt
    assert prompt.count("Commit everything you change before you exit") == 1


@pytest.mark.parametrize(
    ("script", "stop"),
    [
        (
            "echo agent >> calc.py && git add -- calc.py && git commit -qm agent -- calc.py",
            "the agent committed calc.py, which already had uncommitted changes",
        ),
        ("git checkout -q main", "moved to main during the run of escalation"),
        ("echo x > left.txt", None),
    ],
    ids=["overlap", "moved", "leftover"],
)
async def test_an_in_place_session_outside_the_walk_takes_marks_and_checks_its_checkout(
    item_on, monkeypatch, script, stop
):
    """An escalation, reply or gate-review agent runs the steps a walk task
    does: the checkout switched to the item's branch, marked, checked after."""
    it = await item_on([v1_node()], status="needs_human", target=IN_PLACE)
    branch = store.branch_for(it.row())
    git(it.repo, "branch", branch)
    write(it.repo, "calc.py", "# the person's edit\n")
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", f"git symbolic-ref HEAD > .git/on && {script}")

    status = await _outside_the_walk(it)

    assert status == "done"
    assert (it.repo / ".git" / "on").read_text().strip() == f"refs/heads/{branch}"
    [marked] = it.events("task_marked")
    assert (marked["payload"]["task"], marked["payload"]["dirty"]) == ("escalation", ["calc.py"])
    needs_human = it.events("work_item_needs_human")
    if stop:
        assert it.row()["stop_kind"] == "checkout"
        reason = needs_human[-1]["payload"]["reason"]
        assert stop in reason
        assert "retry the item" in reason, "names a verb a checkout stop refuses"
    else:
        assert needs_human == []
        [left] = it.events("left_uncommitted")
        assert left["payload"]["paths"] == ["left.txt"]


async def test_an_in_place_session_refused_its_checkout_never_launches(item_on, monkeypatch):
    """The branch is gone: nothing switches, the agent never runs, and the
    refusal is the session's log for the caller to stop or skip on."""
    it = await item_on([v1_node()], status="needs_human", target=IN_PLACE)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", "touch ran")

    with pytest.raises(inplace.CheckoutRefused, match="is gone"):
        await _outside_the_walk(it)

    assert not (it.repo / "ran").exists()
    [session] = it.sessions()
    assert session["status"] == "config_error"
    assert "is gone" in Path(session["log_path"]).read_text()
    assert it.events("task_marked") == []


def _outside_the_walk(it):
    return agent.run_agent_task(
        it.database,
        it.run_dirs,
        session_id="s1",
        work_item_id=it.id,
        node_id="build",
        hook_point="escalation",
        command=PY_FAKE,
        title="t",
        task_instruction="look again",
        repo_path=str(it.repo),
        cwd=it.repo,
        takes_checkout=True,
    )
