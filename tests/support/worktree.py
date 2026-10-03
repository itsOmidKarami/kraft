"""Worktree helpers shared by tests/test_builtins*.py: one work item, its
worktree, and the two columns those tests read back."""

import contextlib
import os
import signal
from pathlib import Path

import pytest

from kraft import builtins as kraft_builtins
from kraft import store
from kraft.config import git_read
from support.harness import commit_all, entry_of

#: A repo that deliberately needs no preparation. Most of these tests are about
#: git and attachments, not environments.
NO_SETUP = entry_of({"setup_command": ""})


async def prepare(database, rd, repo, *, repo_entry=None, attachments=None, work_item_id="w1"):
    """What `walk.run_once` does before the first node dispatches.

    `env_setup` was a node task until Template Schema V1 deleted it (there is no
    `BuiltinAction` for it, so no V1 chain can name one): its two halves are
    `ensure_worktree` and `prepare_runtime` now, called in that order, and these
    tests drive the pair the way the walk does. Returns the preparation report.
    """
    entry = NO_SETUP if repo_entry is None else repo_entry
    worktree = await kraft_builtins.ensure_worktree(
        database,
        rd,
        repo=str(repo),
        work_item_id=work_item_id,
        attachments=attachments,
        repo_entry=entry,
    )
    return await kraft_builtins.prepare_runtime(worktree, Path(repo), entry)


def make_item(database, repo, wid="w1", **kw):
    """Work item `wid` on `repo`: quick-task, empty chain definition, unless `kw` says otherwise."""
    fields = {
        "bead_id": "B",
        "title": "t",
        "chain_template": "quick-task",
        "chain_definition": "{}",
    }
    return database.write(
        lambda c: store.create_work_item(c, id=wid, repo=str(repo), **(fields | kw))
    )


def base_ref(database, wid="w1"):
    return database.read(
        lambda c: c.execute("SELECT base_ref FROM work_items WHERE id = ?", (wid,)).fetchone()
    )["base_ref"]


def branch(database, wid="w1"):
    return store.branch_for(
        database.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        )
    )


def ensure(database, run_dirs, repo, *, repo_entry=NO_SETUP, **kw):
    """`ensure_worktree` for item w1 on `repo`; no setup command unless `repo_entry` says so."""
    return kraft_builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id="w1", repo_entry=repo_entry, **kw
    )


def commit(cwd, name, text, message):
    """Write `name` under `cwd` (its directories too) and commit everything;
    returns the new HEAD."""
    (cwd / name).parent.mkdir(parents=True, exist_ok=True)
    (cwd / name).write_text(text)
    commit_all(cwd, message)
    return git_read(cwd, "rev-parse", "HEAD")


@pytest.fixture
def hung(tmp_path):
    """Where a hanging hook or filter records the pid of its `sleep`
    (`sleep_recorded`). Each is killed once the test is done: git gives up
    on the hook, but nothing ends the hook itself, which would otherwise
    outlive the test. A module takes it as `hung = wtree.hung`."""
    pids = tmp_path / "hook-pids"
    yield pids
    for pid in pids.read_text().split() if pids.exists() else ():
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid), signal.SIGKILL)


def sleep_recorded(pids, seconds: float) -> str:
    """A hook's last lines: the shell becomes the `sleep`, its pid in `pids`."""
    return f'echo $$ >> "{pids}"\nexec sleep {seconds}\n'
