"""A workspace work item's publication order across the members and the
root (Task 10).

Git is real (a root with one real submodule, `make_repo_with_submodule`);
the forge is `FakeForge`."""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest
from support.harness import _git, entry_of
from support.workspace import workspace_item

from kraft import events, store
from kraft.adapters import forge
from kraft.executor.context import LaunchContext
from kraft.templates.models import DEFAULT_WAIT

NO_SETUP = entry_of({"setup_command": ""})


def _task(id, **fields):
    return {"id": id, "kind": "subprocess", "command": "true", **fields}


# ── publication order ──


def _git_out(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class _LandingForge(forge.FakeForge):
    """A `FakeForge` whose merge really lands the branch on its repository's
    origin `main` -- past a branch protection, as a forge's own merge does --
    and which records, in order, every merge, every approval read and every
    readiness -- the root's with the member pointer its head carried then.
    `awaiting` holds a repository's approval pending for that many reads."""

    def __init__(
        self,
        refuse: str | None = None,
        raise_on_merge: bool = False,
        awaiting: dict[str, int] | None = None,
        merge_delay: int = 0,
    ):
        super().__init__(ci_states=["success"], merge_delay=merge_delay)
        self.order: list[tuple] = []
        self.refuse = refuse
        self.raise_on_merge = raise_on_merge
        self.awaiting = dict(awaiting or {})

    async def approval_state(self, *, repo, branch):
        name = Path(repo).name
        self.order.append(("approval", name))
        if self.awaiting.get(name, 0) > 0:
            self.awaiting[name] -= 1
            return "pending"
        return "approved"

    async def ci_status(self, *, repo, mr, branch="", pipeline_id=""):
        status = await super().ci_status(repo=repo, mr=mr, branch=branch, pipeline_id=pipeline_id)
        if Path(repo).name != self.refuse or self.raise_on_merge:
            return status
        return dataclasses.replace(status, block_reason="not_approved", merge_detail="1 approval")

    async def merge(self, *, repo, branch="", mr):
        if self.raise_on_merge and Path(repo).name == self.refuse:
            raise forge.ForgeError("merge refused: the branch is protected")
        await super().merge(repo=repo, branch=branch, mr=mr)
        subprocess.run(
            ["git", "push", "-q", "origin", "HEAD:main"],
            cwd=repo,
            check=True,
            capture_output=True,
            env={**os.environ, "FORGE_LANDS": "1"},
        )
        self.order.append(("merge", Path(repo).name))

    async def mark_ready(self, *, repo, branch, mr):
        await super().mark_ready(repo=repo, branch=branch, mr=mr)
        root = not Path(repo).name.startswith("pkg")
        pointer = _git_out(repo, "rev-parse", "HEAD:repos/pkg") if root else None
        self.order.append(("ready", Path(repo).name, pointer))


async def _publishable(
    database,
    run_dirs,
    tmp_path,
    *,
    pointer,
    root_denies_push=False,
    root_source=False,
    untouched=(),
    **item,
):
    """A workspace item whose root and member each have an origin that takes a
    push (the member's is its source repository), the member carrying a
    commit, and the root one too when `root_source`. `root_denies_push`
    protects the root's `main` from every push but the forge's own merge.
    A member named in `untouched` is selected but gets no commit. `item`
    goes to `workspace_item`. Returns `(row, worktree, root_origin)`."""
    row, _, worktree = await workspace_item(
        database, run_dirs, tmp_path, [_task("t")], pointer=pointer, **item
    )
    root = Path(row["repo"])
    members = ["pkg", "pkg2"] if item.get("second") else ["pkg"]
    origin = tmp_path / "root-origin.git"
    if not origin.exists():
        _git(tmp_path, "clone", "-q", "--bare", str(root), str(origin))
        _git(root, "remote", "add", "origin", str(origin))
    if root_denies_push:
        hook = origin / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n[ -n \"$FORGE_LANDS\" ] && exit 0\necho 'protected branch' >&2\nexit 1\n"
        )
        hook.chmod(0o755)
    _git(worktree, "fetch", "-q", "origin")
    for member in members:
        _git(tmp_path / member, "config", "receive.denyCurrentBranch", "updateInstead")
        if member in untouched:
            continue
        (worktree / "repos" / member / "lib.py").write_text("x = 1\n")
        _git(worktree / "repos" / member, "add", "-A")
        _git(worktree / "repos" / member, "commit", "-qm", "member change")
    if root_source:
        (worktree / "root.txt").write_text("root source\n")
        _git(worktree, "add", "root.txt")
        _git(worktree, "commit", "-qm", "root source change")
    return row, worktree, origin


async def _run(database, run_dirs, row, worktree, fake, monkeypatch, handler, **kw):
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    return await forge.run_task(
        database,
        run_dirs,
        session_id=f"s-{handler}",
        work_item_id=row["id"],
        node_id=handler,
        hook_point=f"{handler}.main.t",
        handler=handler,
        backend="fake",
        repo=worktree,
        branch=store.branch_for(row),
        title="t",
        **kw,
    )


def _repos(database, row) -> dict[str, str]:
    return {r["role"]: r["state"] for r in database.read(lambda c: store.repos_for(c, row["id"]))}


@pytest.mark.parametrize(
    ("legacy", "pointer"),
    [(False, "bump"), (True, "bump"), (True, "bump_no_mr")],
    ids=["workspace", "filed-before-workspaces", "filed-before-workspaces-bump-no-mr"],
)
async def test_child_merge_precedes_workspace_pointer_update(
    database, run_dirs, tmp_path, monkeypatch, legacy, pointer
):
    """`child-merge-precedes-parent-pointer-update`, and a requested bump of a
    pointer-only root goes straight to the root's default branch
    (`workspace-pointer-bump-prefers-direct-push`): after the member has
    merged, naming the member's merged revision. An item filed before
    workspaces keeps the pointer policy it was filed with (Kraft-zvqwl)."""
    row, worktree, origin = await _publishable(
        database, run_dirs, tmp_path, pointer=pointer, legacy=legacy
    )
    fake = _LandingForge()
    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr") == "done"

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "done"

    assert fake.order == [("merge", "pkg")]
    merged = _git_out(tmp_path / "pkg", "rev-parse", "main")
    assert _git_out(origin, "rev-parse", "main:repos/pkg") == merged
    assert len(fake.opened) == 1, "the member's merge request only; the bump needed none"


class _OvertakenForge(_LandingForge):
    """Someone else lands a commit on a member's origin right after the
    item's merge, before the root's turn."""

    async def merge(self, *, repo, branch="", mr):
        await super().merge(repo=repo, branch=branch, mr=mr)
        if Path(repo).name.startswith("pkg"):
            origin = Path(_git_out(repo, "remote", "get-url", "origin"))
            _git(origin, "commit", "-q", "--allow-empty", "-m", "landed after the item's merge")


async def test_the_pointer_bump_moves_only_merged_members_and_to_what_merged(
    database, run_dirs, tmp_path, monkeypatch
):
    """Kraft-n60oh: the root pointer moves only for a member whose merge
    request merged in this item, and to the revision that merge landed --
    never to whatever its origin's tip has become, which the item never
    built. A selected member the item never changed keeps its pointer."""
    row, worktree, origin = await _publishable(
        database, run_dirs, tmp_path, pointer="bump", second=True, untouched=("pkg2",)
    )
    untouched = _git_out(origin, "rev-parse", "main:repos/pkg2")
    _git(tmp_path / "pkg2", "commit", "-q", "--allow-empty", "-m", "upstream, never built here")
    merged = _git_out(worktree / "repos" / "pkg", "rev-parse", "HEAD")
    fake = _OvertakenForge()
    await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr")

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "done"

    assert _git_out(tmp_path / "pkg", "rev-parse", "main") != merged, "someone landed after it"
    assert _git_out(origin, "rev-parse", "main:repos/pkg") == merged
    assert _git_out(origin, "rev-parse", "main:repos/pkg2") == untouched


async def test_a_workspace_items_base_branch_is_its_roots_and_members_keep_their_own(
    database, run_dirs, tmp_path, monkeypatch
):
    """Kraft-v9gbi: the item's base branch names the root only. The member's
    merge request targets the member's own default branch, and the bump
    lands on the root's base branch, leaving its default alone."""
    row, worktree, origin = await _publishable(
        database, run_dirs, tmp_path, pointer="bump", base_branch="release"
    )
    before = _git_out(origin, "rev-parse", "main")
    fake = _LandingForge()
    compared = []
    source_changed = forge.run.git.source_changed

    async def recorded(repo, branch, *, base, exclude):
        compared.append(base)
        return await source_changed(repo, branch, base=base, exclude=exclude)

    monkeypatch.setattr(forge.run.git, "source_changed", recorded)
    await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr")

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "done"

    assert set(compared) == {"release"}, "the root's own changes are against its base"
    assert fake.opened_base == {1: "main"}, "the member's merge request, into its own default"
    merged = _git_out(tmp_path / "pkg", "rev-parse", "main")
    assert _git_out(origin, "rev-parse", "release:repos/pkg") == merged
    assert _git_out(origin, "rev-parse", "main") == before


def _base_ref(database, row) -> str | None:
    return database.read(
        lambda c: c.execute("SELECT base_ref FROM work_items WHERE id = ?", (row["id"],)).fetchone()
    )["base_ref"]


@pytest.mark.parametrize("handler", ["ci_poll", "merge"])
@pytest.mark.parametrize(
    ("bounce", "expected"),
    [(True, "base_moved"), (False, "waiting")],
    ids=["a-declared-restart-re-verifies-it", "undeclared-it-waits-for-the-rebased-heads-ci"],
)
async def test_a_members_conflict_is_rebased_onto_its_own_origin(
    database, run_dirs, tmp_path, monkeypatch, handler, bounce, expected
):
    """Kraft-puqxq: a member's conflict rebase reads the member's own origin
    and default branch, never the root's, though dispatch hands the forge
    node the root's source repository as `orig_repo`. The item's `base_ref`
    is the root's, so the member's move is reported as one instead of being
    written there. Kraft-tx0dz: a node with no base-change restart to
    re-verify the rebased head waits for that head's own CI instead -- the
    pipeline here still answers for the head before the rebase."""
    row, worktree, _ = await _publishable(database, run_dirs, tmp_path, pointer="ignore")
    (tmp_path / "pkg" / "moved.txt").write_text("landed meanwhile\n")
    _git(tmp_path / "pkg", "add", "moved.txt")
    _git(tmp_path / "pkg", "commit", "-qm", "the member's main moves")
    moved = _git_out(tmp_path / "pkg", "rev-parse", "main")
    member = worktree / "repos" / "pkg"
    fake = forge.FakeForge(
        ci_states=["success"],
        ci_shas=[_git_out(member, "rev-parse", "HEAD")],
        mergeable=False,
        merge_detail="conflict",
    )
    await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr")
    base_ref = _base_ref(database, row)

    result = await _run(
        *(database, run_dirs, row, worktree, fake, monkeypatch, handler),
        orig_repo=Path(row["repo"]),
        has_rebase_bounce=bounce,
    )

    assert _git_out(member, "merge-base", "--is-ancestor", moved, "HEAD") == ""
    assert (result, fake.merged) == (expected, [])
    assert _base_ref(database, row) == base_ref


@pytest.mark.parametrize(
    ("legacy", "pointer"),
    [(False, "ignore"), (True, "skip")],
    ids=["workspace", "filed-before-workspaces-skip"],
)
async def test_the_default_root_pointer_policy_leaves_the_root_unchanged(
    database, run_dirs, tmp_path, monkeypatch, legacy, pointer
):
    """`workspace-root-pointer-update-defaults-to-ignore`; a legacy `skip` is
    the same decision."""
    row, worktree, origin = await _publishable(
        database, run_dirs, tmp_path, pointer=pointer, legacy=legacy
    )
    before = _git_out(origin, "rev-parse", "main")
    fake = _LandingForge()
    await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr")

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "done"

    assert fake.order == [("merge", "pkg")]
    assert _git_out(origin, "rev-parse", "main") == before


async def test_pointer_bump_falls_back_to_merge_request_when_push_is_denied(
    database, run_dirs, tmp_path, monkeypatch
):
    """`workspace-pointer-bump-falls-back-to-merge-request`: a root whose
    default branch refuses the direct push gets the same bump as a merge
    request, from the item's own branch in the root. (Its approval is held
    here: what follows it to its merge is the test after the next.)"""
    row, worktree, origin = await _publishable(
        database, run_dirs, tmp_path, pointer="bump", root_denies_push=True
    )
    before = _git_out(origin, "rev-parse", "main")
    fake = _LandingForge(awaiting={row["id"]: 1})
    await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr")

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "waiting"

    assert _git_out(origin, "rev-parse", "main") == before, "the refused push changed nothing"
    (pointer_mr,) = [n for n, r in fake._opened_repo.items() if r == str(worktree)]
    assert fake.opened[pointer_mr] == store.branch_for(row)
    assert _git_out(worktree, "rev-parse", "HEAD:repos/pkg") == _git_out(
        tmp_path / "pkg", "rev-parse", "main"
    )
    assert _repos(database, row)["root"] == "open"


async def test_root_mr_not_ready_until_child_mrs_have_merged(
    database, run_dirs, tmp_path, monkeypatch
):
    """A root with source changes of its own gets its own merge request,
    drafted early beside the member's (`root-source-draft-merge-request-may-
    run-early`), but it is not marked ready until the member has merged and
    the root names the member's merged revision
    (`root-source-merge-request-readiness-waits-for-child-merges`)."""
    row, worktree, _ = await _publishable(
        database, run_dirs, tmp_path, pointer="ignore", root_source=True
    )
    fake = _LandingForge()

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr") == "done"
    assert len(fake.opened) == 2 and all(fake.opened_draft.values())
    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "mark_ready") == "done"
    assert fake.order == [("ready", "pkg", None)], "the root stays a draft"

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "done"

    merged = _git_out(tmp_path / "pkg", "rev-parse", "main")
    root = row["id"]
    assert fake.order[1:] == [
        ("merge", "pkg"),
        ("ready", root, merged),
        ("approval", root),
        ("merge", root),
    ]


async def test_a_root_merge_request_opened_for_source_since_reverted_is_still_merged(
    database, run_dirs, tmp_path, monkeypatch
):
    """The item never completes while a merge request it opened is still
    open: a root whose source changes were reverted after its merge request
    opened has no source left, but it has a merge request, and `merge`
    follows it through -- even under the `ignore` pointer policy."""
    row, worktree, _ = await _publishable(
        database, run_dirs, tmp_path, pointer="ignore", root_source=True
    )
    fake = _LandingForge()
    await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr")
    _git(worktree, "revert", "--no-edit", "HEAD")

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "done"

    assert fake.order[-1] == ("merge", row["id"])
    assert _repos(database, row)["root"] == "merged"


#: Publication as a chain walks it: draft, approval, ready, merge.
_PUBLISH = [
    {"id": id, "kind": "exec", "tasks": [{"id": id, "kind": "forge", "target": f"mr.{target}"}]}
    for id, target in [
        ("draft", "open_draft"),
        ("approval", "external_approval"),
        ("ready", "mark_ready"),
        ("merge", "merge"),
    ]
]


async def _walk(database, run_dirs, row, fake, monkeypatch) -> str:
    """`executor.run` on the item from its own cursor, re-entered as the wait
    scheduler would, with every forge task answered by `fake`."""
    from kraft import executor

    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    # Every item here is active or waiting, and a waiting one is re-entered.
    await database.write(lambda c: store.mark_reentered(c, row["id"]))
    return await executor.run(
        database,
        run_dirs,
        work_item_id=row["id"],
        policy=None,
        launch=LaunchContext(
            repo_entry=entry_of({"setup_command": "", "forge": "github"}), steering_dir=None
        ),
    )


def _pending(database, row, task="merge.main.merge") -> list[str]:
    """What each pending observation of the merge task's wait was waiting for."""
    return [
        e["payload"]["condition"]
        for e in database.read(lambda c: events.read_after(c, 0, row["id"]))
        if e["type"] == "external_wait_observed"
        and e["payload"]["task"] == task
        and e["payload"]["state"] == "pending"
    ]


async def test_a_root_merge_request_awaits_its_own_approval_then_merges_then_the_item_completes(
    database, run_dirs, tmp_path, monkeypatch, wait_clock
):
    """Kraft-ontlw: the root's approval is read only once it is ready -- after
    its member merged, never while it was a draft at the chain's approval
    node -- and the merge node waits on it, then on its merge landing,
    through the one wait scheduler. The item completes only once the root
    merged. The forge lands every merge a read late."""
    row, worktree, _ = await _publishable(
        database, run_dirs, tmp_path, pointer="ignore", root_source=True, nodes=_PUBLISH
    )
    root = row["id"]
    fake = _LandingForge(awaiting={root: 1}, merge_delay=1)

    for _ in range(3):
        assert await _walk(database, run_dirs, row, fake, monkeypatch) == "waiting"
    assert ("merge", root) in fake.order and _repos(database, row)["root"] == "open"
    assert await _walk(database, run_dirs, row, fake, monkeypatch) == "completed"

    merged = _git_out(tmp_path / "pkg", "rev-parse", "main")
    root_reads = [e for e in fake.order if e[1] == root]
    assert root_reads[0] == ("ready", root, merged), "read before the root was ready"
    assert fake.order.index(("merge", "pkg")) < fake.order.index(root_reads[0])
    assert [e for e in fake.order if e[0] == "merge"] == [("merge", "pkg"), ("merge", root)]
    assert fake.order[-1] == ("merge", root), "readied or re-read after its merge was asked"
    assert _pending(database, row) == ["merge", "external_approval", "merge"]
    assert _repos(database, row)["root"] == "merged"
    # A later pass over a landed root only reads it: its source branch may be
    # gone with the merge, and nothing is pushed to it again.
    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "done"
    assert fake.order[-1] == ("merge", root)


async def test_an_untouched_selected_member_gets_no_merge_request_and_the_item_completes(
    database, run_dirs, tmp_path, monkeypatch
):
    """`changed-child-repositories-get-separate-merge-requests`: an item
    selects more members than it changes. The untouched one gets no merge
    request -- not an empty one whose CI never runs, and not a refusal that
    stops the item (Kraft-vz8e, Kraft-j14jn) -- and the item completes on
    the changed one alone."""
    kw = {"pointer": "ignore", "second": True, "untouched": ("pkg2",), "nodes": _PUBLISH}
    row, _, _ = await _publishable(database, run_dirs, tmp_path, **kw)
    # Lands a read late: by then the member's branch reads empty against its
    # base, and its open merge request must still be followed to "merged".
    fake = _LandingForge(merge_delay=1)

    for _ in range(6):
        if (status := await _walk(database, run_dirs, row, fake, monkeypatch)) != "waiting":
            break

    assert status == "completed"
    assert [Path(r).name for r in fake._opened_repo.values()] == ["pkg"]
    assert [e for e in fake.order if e[0] == "merge"] == [("merge", "pkg")]
    members = database.read(lambda c: store.repos_for(c, row["id"]))
    states = {m["repo"]: m["state"] for m in members if m["role"] == "submodule"}
    assert states == {"pkg": "merged", "pkg2": "pending"}


async def test_a_root_approval_that_never_comes_times_out_for_a_human(
    database, run_dirs, tmp_path, monkeypatch, wait_clock
):
    """`external-wait-timeout-needs-human`, for the root's own approval: the
    wait runs out, the item stops for a person, and the root is left
    unmerged -- the item never completes over it."""
    row, worktree, _ = await _publishable(
        database, run_dirs, tmp_path, pointer="ignore", root_source=True, nodes=_PUBLISH
    )
    root = row["id"]
    fake = _LandingForge(awaiting={root: 10**6})

    assert await _walk(database, run_dirs, row, fake, monkeypatch) == "waiting"
    wait_clock.advance(DEFAULT_WAIT.timeout.total_seconds())

    assert await _walk(database, run_dirs, row, fake, monkeypatch) == "needs_human"

    item = database.read(lambda c: events.read_after(c, 0, row["id"]))
    (stop,) = [e for e in item if e["type"] == "work_item_needs_human"]
    assert (
        "timed out" in stop["payload"]["reason"] and "merge.main.merge" in stop["payload"]["reason"]
    )
    assert _pending(database, row) == ["external_approval"] * 2, "the last one at the deadline"
    assert ("merge", root) not in fake.order and _repos(database, row)["root"] == "open"


async def test_a_fallback_pointer_merge_request_is_followed_to_its_merge(
    database, run_dirs, tmp_path, monkeypatch, wait_clock
):
    """Kraft-srt9v: a pointer bump the root's `main` refused goes out as a
    merge request, and the item does not complete with it open -- it is
    readied, its approval awaited, and it is merged, like any root merge
    request (`external-wait-covers-merge-request-lifecycle`)."""
    row, worktree, origin = await _publishable(
        database, run_dirs, tmp_path, pointer="bump", root_denies_push=True, nodes=_PUBLISH
    )
    root = row["id"]
    fake = _LandingForge(awaiting={root: 1})

    assert await _walk(database, run_dirs, row, fake, monkeypatch) == "waiting"
    (pointer_mr,) = [n for n, r in fake._opened_repo.items() if r == str(worktree)]
    assert fake.opened_draft[pointer_mr] is False, "the pointer merge request was never readied"
    assert await _walk(database, run_dirs, row, fake, monkeypatch) == "completed"

    assert pointer_mr in fake.merged
    assert _git_out(origin, "rev-parse", "main:repos/pkg") == _git_out(
        tmp_path / "pkg", "rev-parse", "main"
    )
    assert _pending(database, row) == ["external_approval"]
    assert _repos(database, row)["root"] == "merged"


@pytest.mark.parametrize(
    ("root_source", "second", "refused", "status", "landed"),
    [
        (True, False, False, "waiting", []),
        (False, False, True, "failed", []),
        (False, True, False, "waiting", [("merge", "pkg")]),
    ],
    ids=["root-with-source-awaiting-approval", "pointer-only-root-refused", "one-member-landed"],
)
async def test_blocked_child_merge_leaves_the_root_unchanged(
    database, run_dirs, tmp_path, monkeypatch, root_source, second, refused, status, landed
):
    """`blocked-child-merge-leaves-parent-unchanged`: while a member's merge
    request waits on an approval, or after its merge is refused, nothing of
    the root's moves -- no readiness, no merge, no pointer bump, not even to a
    member that did land before it. A refusal fails the node; the merge node
    has no recovery of its own, so the item stops for a person. A missing
    approval is an ordinary wait (`missing-external-approval-is-normal-
    pending-state`), and the root waits with it."""
    row, worktree, origin = await _publishable(
        database, run_dirs, tmp_path, pointer="bump", second=second, root_source=root_source
    )
    before = _git_out(origin, "rev-parse", "main")
    fake = _LandingForge(refuse="pkg2" if second else "pkg", raise_on_merge=refused)
    await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr")

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == status

    assert fake.order == landed
    assert _git_out(origin, "rev-parse", "main") == before
    assert _repos(database, row)["root"] != "merged"


@pytest.mark.parametrize(
    ("pointer", "root_source", "root_denies_push"),
    [("ignore", True, False), ("bump", False, True)],
    ids=["root", "fallback-pointer"],
)
async def test_a_restart_mid_the_root_merge_asks_the_forge_to_merge_it_once(
    database, run_dirs, tmp_path, monkeypatch, pointer, root_source, root_denies_push
):
    """Kraft-l98h6, for the root's own merge request and for a fallback
    pointer merge request: a restart after the root's merge was asked for
    reads the queued merge off the forge. Nothing asks again, and nothing
    readies or pushes the root again either."""
    row, worktree, _ = await _publishable(
        database,
        run_dirs,
        tmp_path,
        pointer=pointer,
        root_source=root_source,
        root_denies_push=root_denies_push,
    )
    root = row["id"]
    fake = _LandingForge(merge_delay=3)
    await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr")
    for _ in range(5):  # the member lands, then the root is asked for
        assert (
            await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "waiting"
        )
        if ("merge", root) in fake.order:
            break
    # Asked for once the member landed late, not taken for asked already.
    assert ("merge", root) in fake.order
    await database.write(lambda c: events.append(c, root, "work_item_retried", {}))

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "waiting"

    assert fake.order.count(("merge", root)) == 1 and fake.order[-1] == ("merge", root)
    for _ in range(5):
        if await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "done":
            break
    assert fake.order.count(("merge", root)) == 1 and _repos(database, row)["root"] == "merged"
