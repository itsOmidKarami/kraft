"""Kraft records each push of an item's branch (`branch_pushed`), so a wait can
tell activity after the head it is waiting on from activity before it.
Walked through `forge.run_task` against a `FakeForge`; git is real."""

from __future__ import annotations

from datetime import datetime

import pytest
from support.harness import commit_all, git, write

from kraft import waits
from kraft.adapters import forge


@pytest.fixture
def on_branch(repo):
    """`repo` standing on `kraft/w1`, one commit ahead of main."""
    git(repo, "checkout", "-b", "kraft/w1")
    write(repo, "feature.txt", "one\n")
    commit_all(repo, "feature")
    return repo


def _head(repo) -> str:
    return git(repo, "rev-parse", "kraft/w1")


@pytest.mark.parametrize(
    "handler, opened_first, rebases",
    [
        ("open_mr", False, False),
        ("open_mr", True, False),
        ("sync_mr", True, False),
        ("ci_poll", True, False),
        ("ci_poll", True, True),
        ("merge", True, False),
    ],
    ids=["open_mr", "open_mr-reused", "sync_mr", "ci_poll", "rebase_conflict", "merge"],
)
async def test_a_push_is_recorded_once_per_head(
    run_forge, on_branch, monkeypatch, handler, opened_first, rebases
):
    fake = forge.FakeForge(ci_states=["success"])
    if opened_first:
        await fake.open_mr(repo=on_branch, branch="kraft/w1", base="main", title="t", body="b")
    pushed = [_head(on_branch)]
    if rebases:
        fake.mergeable, fake.merge_detail = False, "conflict"

        async def rebased(*_, **__):
            # A real rebase moves the head; the second pass finds it moved already.
            if not (on_branch / "rebased.txt").exists():
                write(on_branch, "rebased.txt", "x\n")
                commit_all(on_branch, "rebased")
            return _head(on_branch)

        monkeypatch.setattr(forge.run._builtins, "mr_rebase_forced", rebased)
        pushed.append(None)  # the head the rebase reaches, known once it ran

    await run_forge(fake, handler, "s1", repo=on_branch, orig_repo=on_branch)
    first = run_forge.events("branch_pushed")
    await run_forge(fake, handler, "s2", repo=on_branch, orig_repo=on_branch)

    if rebases:
        pushed[1] = _head(on_branch)
    assert [(p["repo"], p["sha"], p["node_id"]) for p in first] == [
        (str(on_branch), sha, "mr_checks") for sha in pushed
    ]
    assert run_forge.events("branch_pushed") == first, "a head pushed again is not a new push"
    for sha in pushed:
        assert run_forge.database.read(
            lambda c, sha=sha: waits.pushed_at(c, "w1", repo=str(on_branch), sha=sha)
        )
    assert (
        run_forge.database.read(
            lambda c: waits.pushed_at(c, "w1", repo=str(on_branch), sha="0" * 40)
        )
        is None
    )


async def test_a_head_kraft_never_pushed_is_dated_by_its_commit(on_branch):
    """The fallback for a head an older Kraft or a person pushed: no
    `branch_pushed` names it, so its committer time stands in."""
    head = _head(on_branch)
    when = await forge.git.committed_at(on_branch, head)

    assert when == datetime.fromisoformat(git(on_branch, "log", "-1", "--format=%cI", head))
