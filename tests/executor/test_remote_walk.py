"""A chain walked by the executor with one node placed on a machine."""

from support.chain_builders import v1_node, v1_task
from support.harness import entry_of, git, v1_chain

from kraft import executor
from kraft.executor.context import LaunchContext

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}))


async def test_an_implement_and_test_chain_runs_its_agent_on_the_machine_and_its_tests_here(
    rig, item_on, database, run_dirs, repo, fake_agent
):
    chain = v1_chain(
        [
            v1_node("implementation", runner="gpu", tasks=[v1_task("implement", kind="agent")]),
            v1_node("verify", tasks=[v1_task("check", command="true")]),
        ],
        repo=repo,
    )
    it = await item_on(chain, repo=repo)
    await executor.run_once(database, run_dirs, work_item_id=it.id, launch=NO_SETUP)
    assert it.status() == "completed"
    assert {s["hook_point"]: s["runner"] for s in it.sessions()} == {
        "implementation.main.implement": "gpu",
        "verify.main.check": None,
    }
    committed = git(it.worktree, "log", "--name-only", "--format=", "main..HEAD").split()
    assert "calc.py" in committed  # the machine's sweep committed it and it came home
    assert not it.events("sandbox_branch_not_synced") and not it.events("remote_docs_conflict")
    assert (run_dirs.base / "items" / it.id / "sessions").exists()


async def test_any_picks_the_connected_allowed_machine_and_never_the_server(
    rig, item_on, database, run_dirs, repo, fake_agent
):
    chain = v1_chain(
        [v1_node("implementation", runner="any", tasks=[v1_task("implement", kind="agent")])],
        repo=repo,
    )
    it = await item_on(chain, repo=repo)
    await executor.run_once(database, run_dirs, work_item_id=it.id, launch=NO_SETUP)
    assert it.status() == "completed" and [s["runner"] for s in it.sessions()] == ["gpu"]
