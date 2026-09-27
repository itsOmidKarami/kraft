from __future__ import annotations

import subprocess

from support.harness import make_repo
from support.store_fixtures import mk_item

from kraft import node_runs, store


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, name, text):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", name)
    return _git(repo, "rev-parse", "HEAD")


async def test_completed_pins_a_ref_that_survives_a_reset(database, tmp_path):
    await mk_item(database)
    repo = make_repo(tmp_path)
    await node_runs.entered(database, repo, "w1", "impl")
    sha = _commit(repo, "a.py", "x = 1\n")
    await node_runs.completed(database, repo, "w1", "impl")
    rows = database.read(lambda c: store.node_run_rows(c, "w1"))
    assert [(r["node_id"], r["attempt"], r["end_sha"]) for r in rows] == [("impl", 1, sha)]
    _git(repo, "reset", "-q", "--hard", "HEAD~1")  # what a rebase does to the old tip
    assert _git(repo, "rev-parse", "refs/kraft/w1/impl/1") == sha


async def test_drop_refs_removes_only_this_items_refs(database, tmp_path):
    repo = make_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    node_runs.pin_ref(repo, "w1", "impl", 1, head)
    node_runs.pin_ref(repo, "w2", "impl", 1, head)
    node_runs.drop_refs(repo, "w1")
    refs = _git(repo, "for-each-ref", "--format=%(refname)", "refs/kraft/")
    assert refs.splitlines() == ["refs/kraft/w2/impl/1"]


async def test_no_worktree_still_writes_the_events(database):
    """The first node runs before the worktree exists: events, no row."""
    await mk_item(database)
    await node_runs.entered(database, None, "w1", "env_setup")
    await node_runs.completed(database, None, "w1", "env_setup")
    assert database.read(lambda c: store.node_run_rows(c, "w1")) == []


async def test_a_hanging_ref_write_is_bounded_and_never_fails_the_walk(
    database, tmp_path, monkeypatch
):
    """Kraft-dl5fl 4: `update-ref` runs off the event loop with a timeout; one
    that hangs costs the old attempt's ref, never the node's completion."""
    await mk_item(database)
    repo = make_repo(tmp_path)
    seen = {}

    def hang(cmd, **kw):
        if cmd[:2] == ["git", "update-ref"]:
            seen["timeout"] = kw.get("timeout")
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout") or 0)
        return real(cmd, **kw)

    real = subprocess.run
    monkeypatch.setattr(node_runs.subprocess, "run", hang)
    await node_runs.entered(database, repo, "w1", "impl")
    await node_runs.completed(database, repo, "w1", "impl")

    assert seen["timeout"] == 10
    rows = database.read(lambda c: store.node_run_rows(c, "w1"))
    assert rows[0]["end_sha"] is not None
