"""`executor.walk` over an in-place item: the checkout is taken right before a
task that needs the folder, never on the walk's entry, and never by force."""

from __future__ import annotations

from support.harness import commit_all, entry_of, git, write

from kraft import store
from kraft.adapters import forge as _forge
from kraft.templates.environment import WorkItemTarget

from .test_walk import FORGE_REPO, _agent, _exec, _forge_task, _stop_reason, _walk

_IN_PLACE = WorkItemTarget.for_repository("sample", in_place=True)


async def test_a_ci_wait_reentry_does_not_move_the_checkout(item_on, repo, monkeypatch):
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _forge.FakeForge(ci_states=["pending"]))
    chain = [_exec("implementation", _agent()), _exec("mr_checks", _forge_task("ci_poll", "mr.ci"))]
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


async def test_an_agent_task_runs_on_the_items_branch(item_on, fake_agent, repo):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    branch = store.branch_for(it.row())
    git(repo, "checkout", "-q", "-b", branch)
    write(repo, "ITEM", "already on the item's branch\n")
    tip = commit_all(repo, "on the item's branch")
    git(repo, "checkout", "-q", "main")

    await _walk(it)

    assert git(repo, "symbolic-ref", "HEAD") == f"refs/heads/{branch}"
    assert [e["payload"] for e in it.events("checkout_switched")] == [
        {"from": "main", "to": branch}
    ]
    assert it.sessions("implementation")[0]["head_sha"] == tip
