"""An item that names its base branch (Kraft-v9gbi): its worktree starts from
that branch, rebases onto it, and a base change is movement on it -- never on
the repository's default branch, which is what every item used before."""

import subprocess
import sys

import pytest
from support import worktree as wtree
from support.harness import _git, make_repo, v1_chain

from kraft import builtins as kraft_builtins
from kraft.config import base_ignore_args, git_read
from kraft.templates.environment import WorkItemTarget

_ONE_NODE = [
    {"id": "n", "kind": "exec", "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}]}
]


def _land(other, branch, name):
    """Commit `name` on `branch` in `other` and push it to origin; the new tip."""
    _git(other, "checkout", "-q", branch)
    (other / name).write_text(f"landed on {branch}\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", f"{branch}: {name}")
    _git(other, "push", "-q", "origin", branch)
    return git_read(other, "rev-parse", "HEAD")


@pytest.fixture
def origin(tmp_path):
    """`repo` (on `main`) pushed to a bare origin that also has `release`,
    which carries `release.txt` that `main` does not; plus `other`, a second
    clone through which commits land on origin but not in `repo`."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    repo = make_repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    _git(other, "config", "user.email", "o@o")
    _git(other, "config", "user.name", "o")
    _git(other, "checkout", "-q", "-b", "release")
    _land(other, "release", "release.txt")
    return repo, other


def _item_on(database, repo, base_branch):
    target = WorkItemTarget.for_repository("target", base_branch=base_branch)
    chain = v1_chain(_ONE_NODE, repo=repo, target=target)
    return wtree.make_item(database, repo, materialized_chain=chain.to_json())


async def test_an_items_worktree_starts_from_its_base_branch(database, run_dirs, origin):
    repo, other = origin
    release = git_read(other, "rev-parse", "origin/release")

    await _item_on(database, repo, "release")
    worktree = await wtree.ensure(database, run_dirs, repo)

    assert wtree.base_ref(database) == release
    assert (worktree / "release.txt").is_file()


@pytest.mark.parametrize(
    "base_branch, lands_on, moves",
    [
        ("release", "release", True),
        # A default-branch commit is not this item's base moving.
        ("release", "main", False),
        # An item that names nothing is on the default branch, as before.
        (None, "main", True),
    ],
    ids=["its-base-moved", "only-the-default-moved", "no-base-named-default-moved"],
)
async def test_the_pre_mr_rebase_follows_the_items_base_branch(
    database, run_dirs, origin, base_branch, lands_on, moves
):
    """`mr_rebase` is the base-change detection a node's `on_base_changed`
    keys off: it moves `base_ref` exactly when the item's own base moved."""
    repo, other = origin
    await _item_on(database, repo, base_branch)
    worktree = await wtree.ensure(database, run_dirs, repo)
    before = wtree.base_ref(database)
    landed = _land(other, lands_on, "moved.txt")

    await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="n",
        hook_point="on.mr.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=wtree.branch(database),
    )

    assert wtree.base_ref(database) == (landed if moves else before)
    assert (worktree / "moved.txt").is_file() is moves


async def test_the_base_branch_names_the_items_own_repository_or_its_default(
    database, run_dirs, origin
):
    repo, _other = origin
    await _item_on(database, repo, "release")
    await wtree.make_item(database, repo, wid="legacy")

    assert await kraft_builtins.base_branch(database, "w1", repo) == "release"
    assert await kraft_builtins.base_branch(database, "legacy", repo) == "main"


def test_the_ignore_rules_come_from_the_items_base_branch(origin):
    """A rule the base branch gained after the worktree was cut still binds
    it -- the base's, not `main`'s, which an item on `release` never merges
    into."""
    repo, other = origin
    _git(other, "checkout", "-q", "release")
    (other / ".gitignore").write_text("release-only/\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "ignore release-only")
    _git(other, "push", "-q", "origin", "release")
    _git(repo, "fetch", "-q", "origin")

    with base_ignore_args(repo, "release") as args:
        rules = open(args[1].split("=", 1)[1]).read()
    assert rules == "release-only/\n"
    with base_ignore_args(repo, "main") as args:
        assert args == []


@pytest.mark.parametrize("door, stopped", [("retry", "needs_human"), ("resume", "paused")])
def test_a_door_rebases_a_stopped_item_onto_its_base_branch(
    client, origin, monkeypatch, door, stopped
):
    """`/retry` and `/resume` rebase the worktree before the walk: onto the
    item's base branch, like every other rebase."""
    from support.api import _force_node

    from kraft import executor

    repo, _other = origin
    bases = []

    async def refresh(*_args, base, **_kwargs):
        bases.append(base)

    async def walk(*_args, **_kwargs):
        return "completed"

    monkeypatch.setattr(kraft_builtins, "refresh_worktree_base", refresh)
    monkeypatch.setattr(executor, door if door == "retry" else "run", walk)
    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "autostart": False, "base_branch": "release"},
    )
    _force_node(r.json()["id"], "spec", stopped)

    assert client.post(f"/api/work-items/{r.json()['id']}/{door}", json={}).status_code == 200
    assert bases == ["release"]


async def test_an_escalations_self_retry_rebases_onto_the_items_base_branch(
    item_on, run_dirs, repo, monkeypatch
):
    from kraft import events, store
    from kraft.executor import gates

    bases = []

    async def refresh(*_args, base, **_kwargs):
        bases.append(base)

    async def walk(*_args, **_kwargs):
        return "completed"

    monkeypatch.setattr(kraft_builtins, "refresh_worktree_base", refresh)
    # The module, not `kraft.executor.retry`, which the package rebinds to
    # the function.
    monkeypatch.setattr(sys.modules["kraft.executor.retry"], "retry", walk)
    target = WorkItemTarget.for_repository("target", base_branch="release")
    it = await item_on(_ONE_NODE, "n", target=target)
    await it.database.write(lambda c: store.mark_needs_human(c, it.id, "n", "stuck", stuck=True))
    cursor = it.events()[-1]["seq"]
    request = {"node_id": "n", "key": None, "gate_key": None, "steer": None}
    await it.database.write(
        lambda c: events.append(c, it.id, "work_item_self_retry_requested", request)
    )

    await gates.resume_after_escalation(it.database, run_dirs, work_item_id=it.id, cursor=cursor)

    assert bases == ["release"]


async def test_the_merge_request_lists_the_commits_it_adds_to_its_base(
    database, run_dirs, origin, monkeypatch
):
    """The description's commit list is the branch against its base: an item
    on `release` does not claim `release`'s own commits as its work."""
    from kraft.adapters import forge

    repo, _other = origin
    await _item_on(database, repo, "release")
    worktree = await wtree.ensure(database, run_dirs, repo)
    (worktree / "work.txt").write_text("the item's work\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", "the item's work")
    fake = forge.FakeForge()
    monkeypatch.setattr(forge.run, "resolve", lambda _name: fake)

    await forge.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="n",
        hook_point="n.main.t",
        handler="open_mr",
        backend="fake",
        repo=worktree,
        orig_repo=repo,
        branch=wtree.branch(database),
        title="t",
    )

    assert "- the item's work" in fake.opened_bodies[1]
    assert "release.txt" not in fake.opened_bodies[1]
