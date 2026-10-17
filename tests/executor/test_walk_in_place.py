"""`executor.walk` over an in-place item: the checkout is taken right before a
task that needs the folder, never on the walk's entry, and never by force."""

from __future__ import annotations

import pytest
from support.harness import commit_all, entry_of, git, write

from kraft import inplace, store
from kraft.adapters import forge as _forge
from kraft.executor import dispatch
from kraft.templates.environment import WorkItemTarget

from .test_walk import FORGE_REPO, _agent, _exec, _forge_task, _stop_reason, _walk

_IN_PLACE = WorkItemTarget.for_repository("sample", in_place=True)


@pytest.mark.parametrize("read_only", [False, True], ids=["plain", "read-only-node"])
async def test_a_ci_wait_reentry_does_not_move_the_checkout(item_on, repo, monkeypatch, read_only):
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _forge.FakeForge(ci_states=["pending"]))
    ci = _exec(
        "mr_checks", _forge_task("ci_poll", "mr.ci"), **({"read_only": True} if read_only else {})
    )
    chain = [_exec("implementation", _agent()), ci]
    it = await item_on(chain, "mr_checks", target=_IN_PLACE)

    assert await _walk(it, repo_entry=FORGE_REPO) == "waiting"
    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert it.events("checkout_switched") == []


async def test_a_refused_take_stops_the_item_as_checkout(item_on, fake_agent, repo):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    branch = store.branch_for(it.row())
    git(repo, "checkout", "-q", "-b", branch)
    write(repo, "calc.py", "the item's version\n")
    commit_all(repo, "on the item's branch")
    git(repo, "checkout", "-q", "main")
    write(repo, "calc.py", "the person's version\n")

    assert await _walk(it, repo_entry=entry_of({"setup_command": "touch SETUP_RAN"})) == (
        "needs_human"
    )

    assert it.row()["stop_kind"] == "checkout"
    assert "calc.py" in _stop_reason(it)
    assert "stuck" not in it.events("work_item_needs_human")[-1]["payload"]
    assert (repo / "calc.py").read_text() == "the person's version\n"
    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert fake_agent.prompts() == [], "the agent ran in a checkout Kraft could not take"
    assert not (repo / "SETUP_RAN").exists(), "a first walk in place ran the setup command"


async def test_a_sandbox_added_after_filing_stops_as_config(item_on, fake_agent, repo):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    sandboxed = entry_of({"setup_command": "", "sandbox": {"kind": "docker", "image": "x"}})

    assert await _walk(it, repo_entry=sandboxed) == "needs_human"

    assert it.row()["stop_kind"] == "config"
    assert "in-place items run unsandboxed" in _stop_reason(it)
    assert fake_agent.prompts() == []
    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main"


@pytest.mark.parametrize("dirty", [False, True], ids=["clean", "the-persons-edit"])
async def test_an_agent_task_runs_on_the_items_branch(item_on, fake_agent, repo, dirty):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    branch = store.branch_for(it.row())
    git(repo, "checkout", "-q", "-b", branch)
    write(repo, "ITEM", "already on the item's branch\n")
    tip = commit_all(repo, "on the item's branch")
    git(repo, "checkout", "-q", "main")
    if dirty:
        write(repo, "test_calc.py", "# the person's edit\n")

    await _walk(it)

    if dirty:
        assert (repo / "test_calc.py").read_text() == "# the person's edit\n"
        assert "test_calc.py" in git(repo, "diff", "--name-only").split(), "not left unstaged"
        assert git(repo, "log", "--format=%h", f"{tip}..{branch}", "--", "test_calc.py") == "", (
            "Kraft committed the person's edit"
        )

    assert git(repo, "symbolic-ref", "HEAD") == f"refs/heads/{branch}"
    assert [e["payload"] for e in it.events("checkout_switched")] == [
        {"from": "main", "to": branch}
    ]
    assert it.sessions("implementation")[0]["head_sha"] == tip
    started = it.database.read(
        lambda c: c.execute(
            "SELECT start_sha FROM node_runs WHERE work_item_id = ? AND node_id = ?",
            (it.id, "implementation"),
        ).fetchone()
    )
    assert started["start_sha"] == tip, "the node's run started from the person's HEAD"


async def test_no_sweep_after_an_in_place_task_moves_or_commits_anything(item_on, run_dirs, repo):
    """The straggler sweep forces the branch back and commits with `add -A`:
    in the person's checkout it would discard their switch and take their edits."""
    it = await item_on([_exec("implementation", _agent())], target=_IN_PLACE)
    git(repo, "branch", store.branch_for(it.row()))
    write(repo, "calc.py", "# the person's edit\n")
    head = git(repo, "rev-parse", "HEAD")

    await dispatch.sweep_stragglers(
        it.database,
        it.row(),
        repo,
        node_id="implementation",
        task_path="implementation.main.implement",
        launch=None,
        sandboxed=False,
    )

    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main", "the checkout was forced back"
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == "M calc.py"


async def test_a_branch_deleted_while_the_item_waits_is_a_checkout_stop(
    item_on, fake_agent, database, run_dirs, repo
):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    await inplace.prepare(database, run_dirs, it.row(), base="main")
    branch = store.branch_for(it.row())
    git(repo, "branch", "-D", branch)

    assert await _walk(it) == "needs_human"

    assert it.row()["stop_kind"] == "checkout"
    assert f"{branch} is gone" in _stop_reason(it)
    assert git(repo, "branch", "--list", branch) == "", "Kraft made the branch again"
