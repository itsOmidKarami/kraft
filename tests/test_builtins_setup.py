"""The stops a work item meets preparing its worktree -- a repository with
no commit to branch from, and the two `run_setup_command` ends it with --
and what each tells the person to do about it."""

from __future__ import annotations

import pytest
from support import worktree as wtree
from support.harness import _git, entry_of

from kraft import builtins as kraft_builtins


@pytest.mark.parametrize("entry", [None, entry_of({})], ids=["no-entry", "undeclared"])
async def test_an_undeclared_setup_command_names_both_ways_out(tmp_path, entry):
    with pytest.raises(RuntimeError) as stopped:
        await kraft_builtins.run_setup_command(tmp_path, tmp_path, entry)
    message = str(stopped.value)
    assert "no setup_command declared for" in message
    assert 'use "" for a repo that deliberately needs no preparation' in message
    assert "tick No setup needed under Templates › Repos" in message


async def test_a_failed_setup_command_names_the_command_and_its_output(tmp_path):
    with pytest.raises(RuntimeError) as stopped:
        await kraft_builtins.run_setup_command(
            tmp_path, tmp_path, entry_of({"setup_command": "echo broken >&2; exit 3"})
        )
    assert str(stopped.value) == (
        f"setup command failed for {tmp_path.name}: 'echo broken >&2; exit 3': broken"
    )


async def test_a_declared_empty_setup_command_needs_nothing(tmp_path):
    assert (
        await kraft_builtins.run_setup_command(tmp_path, tmp_path, entry_of({"setup_command": ""}))
        == ""
    )


async def test_a_repo_with_no_commit_gets_no_worktree(tmp_path, database, run_dirs):
    """git would make the item's branch an empty orphan, and the agent and
    the tests would run without one of the repository's files."""
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "Makefile").write_text("test:\n\ttrue\n")
    await wtree.make_item(database, repo)
    with pytest.raises(RuntimeError, match="has no commit to branch from"):
        await wtree.ensure(database, run_dirs, repo, repo_entry=entry_of({"setup_command": ""}))
    assert not (run_dirs.worktrees / "w1").exists()


async def test_the_setup_records_only_the_lockfile_it_wrote(tmp_path):
    """What the straggler sweep then leaves out: a `uv.lock` there before the
    setup ran (the agent's, from an earlier attempt) is not the setup's."""
    from support.harness import make_repo

    from kraft.adapters import forge

    repo = make_repo(tmp_path)
    (repo / "agent").mkdir()
    (repo / "agent" / "uv.lock").write_text("by the agent\n")
    entry = entry_of({"setup_command": "echo v1 > uv.lock && echo v2 >> agent/uv.lock"})
    for _ in range(2):  # every walk entry runs it again
        await kraft_builtins._prepare(repo, repo, entry)
        assert await forge.setup_wrote(repo) == {"uv.lock"}
