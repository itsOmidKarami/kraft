"""A workspace work item end to end: fan-out by repository and the policy
each repository runs under, area setup before an area's test scope, and
publication order across the members and the root (Task 10).

Git is real (a root with one real submodule, `make_repo_with_submodule`);
the forge is `FakeForge`, and a task's process is a spy wherever what it ran
under is the question."""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest
from support import worktree as wtree
from support.harness import _git, make_repo, make_repo_with_submodule, v1_chain
from support.workspace import workspace_target

from kraft import events, store
from kraft.adapters import forge
from kraft.executor import dispatch
from kraft.executor.context import LaunchContext
from kraft.policy import InstancePolicy, InstancePolicyInput, SandboxPolicy
from kraft.templates.models import DEFAULT_WAIT

NO_SETUP = {"setup_command": ""}
_SANDBOX = SandboxPolicy(kind="docker", image="kraft/member:1")


def _policy(**override) -> InstancePolicy:
    base = InstancePolicy.from_input(InstancePolicyInput())
    return base.apply_template_override(override) if override else base


async def _workspace_item(
    database,
    run_dirs,
    tmp_path,
    tasks,
    *,
    pointer="ignore",
    second=False,
    legacy=False,
    nodes=None,
    **materialize,
):
    """A root with one submodule `pkg` at `repos/pkg` (and `pkg2` at
    `repos/pkg2` when `second`), filed as a workspace item selecting them under
    root-pointer policy `pointer`, on one exec node of `tasks` (or the chain
    `nodes`); its checkout assembled. Returns `(row, node, worktree)`."""
    root, _ = make_repo_with_submodule(tmp_path)
    mounts = {"pkg": "repos/pkg"}
    if second:
        pkg2 = make_repo(tmp_path, name="pkg2")
        _git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(pkg2), "repos/pkg2")
        _git(root, "commit", "-qm", "add a second submodule")
        mounts["pkg2"] = "repos/pkg2"
    chain = v1_chain(
        nodes or [{"id": "n", "kind": "exec", "tasks": tasks}],
        repo=root,
        target=None if legacy else workspace_target(mounts, root_pointer_policy=pointer),
    )
    if materialize:
        # Built past `materialize`, as an item filed before Ruling 180 froze
        # it: `materialize` now refuses a sandbox over submodule mounts
        # (Kraft-dshto), and dispatch's per-repository resolution is what
        # this pins. The chain declares no policy, so no layer is skipped.
        chain = dataclasses.replace(
            chain,
            policy=materialize["effective_policy"],
            repository_policies=materialize["repository_policies"],
        )
    # `legacy`: the shape every item filed before Task 10 has -- a
    # single-repository target, its submodules and pointer policy in columns.
    columns = {"submodules": list(mounts.values()), "root_merge_policy": pointer} if legacy else {}
    await wtree.make_item(database, root, materialized_chain=chain.to_json(), **columns)
    worktree = await wtree.ensure(database, run_dirs, root)
    row = database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())
    return row, chain.chain.nodes[0], worktree


@pytest.fixture
def ran(monkeypatch):
    """Every subprocess task launch, as `(cwd, sandbox)`; each reports done."""
    calls: list[tuple[Path, dict | None]] = []

    async def run_task(db, run_dirs, *, cwd, sandbox=None, **_):
        calls.append((Path(cwd), sandbox))
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    return calls


def _task(id, **fields):
    return {"id": id, "kind": "subprocess", "command": "true", **fields}


LAUNCH = LaunchContext(
    repo_entry=NO_SETUP, steering_dir=None, repositories={"ws": NO_SETUP, "pkg": NO_SETUP}
)


# ── fan-out (`task-may-explicitly-fan-out-by-repository`) ──


async def test_a_task_opting_in_runs_once_per_selected_repository_and_others_once(
    database, run_dirs, tmp_path, ran
):
    """Only `scope: each_repository` fans out -- once in each selected
    repository's own checkout, root first; a task that does not opt in runs
    once, in the assembled checkout (`workspace-tasks-have-an-assembled-
    checkout`)."""
    row, node, worktree = await _workspace_item(
        database, run_dirs, tmp_path, [_task("each", scope="each_repository"), _task("once")]
    )
    each, once = node.tasks()

    assert (
        await dispatch.dispatch_node(database, run_dirs, each, node, row, worktree, launch=LAUNCH)
        == "done"
    )
    assert [cwd for cwd, _ in ran] == [worktree, worktree / "repos" / "pkg"]

    ran.clear()
    assert (
        await dispatch.dispatch_node(database, run_dirs, once, node, row, worktree, launch=LAUNCH)
        == "done"
    )
    assert [cwd for cwd, _ in ran] == [worktree]


async def test_a_fanned_out_task_fails_when_any_repository_fails(
    database, run_dirs, tmp_path, monkeypatch
):
    statuses = iter(["done", "failed"])

    async def run_task(db, run_dirs, **_):
        return next(statuses)

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    row, node, worktree = await _workspace_item(
        database, run_dirs, tmp_path, [_task("each", scope="each_repository")]
    )
    (each,) = node.tasks()
    assert (
        await dispatch.dispatch_node(database, run_dirs, each, node, row, worktree, launch=LAUNCH)
        == "failed"
    )


async def test_each_repository_binds_its_own_task_and_the_checkout_binds_all(
    database, run_dirs, tmp_path, ran
):
    """Kraft-jc39p, at launch: a task fanned out to a member runs under that
    member's frozen policy -- here, its sandbox -- and the root's run under
    the root's; the assembled checkout's task under the item's policy, the
    meet of them all."""
    row, node, worktree = await _workspace_item(
        database,
        run_dirs,
        tmp_path,
        [_task("each", scope="each_repository"), _task("once")],
        effective_policy=_policy(sandbox=_SANDBOX.model_dump()),
        repository_policies={"ws": _policy(), "pkg": _policy(sandbox=_SANDBOX.model_dump())},
    )
    each, once = node.tasks()

    await dispatch.dispatch_node(database, run_dirs, each, node, row, worktree, launch=LAUNCH)
    await dispatch.dispatch_node(database, run_dirs, once, node, row, worktree, launch=LAUNCH)

    assert [sandbox for _, sandbox in ran] == [None, _SANDBOX.model_dump(), _SANDBOX.model_dump()]


async def test_a_fanned_out_run_reads_its_own_repositorys_entry(database, run_dirs, tmp_path, ran):
    """A member's run is configured by the member's `repos.yaml` entry -- its
    live sandbox here -- never the root's."""
    member = {"setup_command": "", "sandbox": {"kind": "docker", "image": "member:live"}}
    launch = LaunchContext(
        repo_entry=NO_SETUP, steering_dir=None, repositories={"ws": NO_SETUP, "pkg": member}
    )
    row, node, worktree = await _workspace_item(
        database, run_dirs, tmp_path, [_task("each", scope="each_repository")]
    )
    (each,) = node.tasks()

    await dispatch.dispatch_node(database, run_dirs, each, node, row, worktree, launch=launch)

    assert [sandbox for _, sandbox in ran] == [None, member["sandbox"]]


# ── areas (`repository-area-can-declare-setup-and-test-scopes`) ──

_AREAS = {
    "setup_command": "",
    "test_scopes": [{"paths": ["src/**"], "command": "just test"}],
    "areas": {
        "python_api": {
            "paths": ["services/api/**"],
            "setup": "uv sync",
            "verification": {
                "test_scopes": [{"paths": ["services/api/**"], "command": "just test-api"}]
            },
        },
        "java_worker": {
            "paths": ["services/worker/**"],
            "setup": "./gradlew classes",
            "verification": {
                "test_scopes": [{"paths": ["services/worker/**"], "command": "./gradlew test"}]
            },
        },
    },
}


@pytest.fixture
def commands(monkeypatch):
    """Every command a verification task ran, in order; each reports done."""
    calls: list[list[str]] = []

    async def run_task(db, run_dirs, *, cmd, **_):
        calls.append(list(cmd))
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    return calls


async def test_a_changed_path_in_an_area_runs_its_setup_then_its_scope(
    database, run_dirs, tmp_path, commands
):
    """An area's test scopes join the repository's in one table, selected by
    changed paths the same way; before an area's scope runs, its setup runs
    (`selected-test-scope-activates-its-area-setup`). No area is ever chosen
    at intake, so this one is "unexpected" in the requirement's sense, and is
    still set up and tested (`unexpected-area-changes-are-tested`); the area
    nothing changed is neither."""
    repo = make_repo(tmp_path)
    chain = v1_chain(
        [
            {
                "id": "v",
                "kind": "exec",
                "tasks": [
                    {"id": "t", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}
                ],
            }
        ],
        repo=repo,
    )
    await wtree.make_item(database, repo, materialized_chain=chain.to_json())
    worktree = await wtree.ensure(database, run_dirs, repo)
    (worktree / "services" / "api").mkdir(parents=True)
    (worktree / "services" / "api" / "app.py").write_text("x = 1\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-qm", "touch the api area")
    row = database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())
    node = chain.chain.nodes[0]

    status = await dispatch.dispatch_node(
        database,
        run_dirs,
        next(iter(node.tasks())),
        node,
        row,
        worktree,
        launch=LaunchContext(repo_entry=_AREAS, steering_dir=None),
    )

    assert status == "done"
    assert commands == [["uv", "sync"], ["just", "test-api"]]


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
    database, run_dirs, tmp_path, *, pointer, root_denies_push=False, root_source=False, **item
):
    """A workspace item whose root and member each have an origin that takes a
    push (the member's is its source repository), the member carrying a
    commit, and the root one too when `root_source`. `root_denies_push`
    protects the root's `main` from every push but the forge's own merge.
    `item` goes to `_workspace_item`. Returns `(row, worktree, root_origin)`."""
    row, _, worktree = await _workspace_item(
        database, run_dirs, tmp_path, [_task("t")], pointer=pointer, **item
    )
    root = Path(row["repo"])
    members = ["pkg", "pkg2"] if item.get("second") else ["pkg"]
    origin = tmp_path / "root-origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(root), str(origin))
    if root_denies_push:
        hook = origin / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n[ -n \"$FORGE_LANDS\" ] && exit 0\necho 'protected branch' >&2\nexit 1\n"
        )
        hook.chmod(0o755)
    _git(root, "remote", "add", "origin", str(origin))
    _git(worktree, "fetch", "-q", "origin")
    for member in members:
        _git(tmp_path / member, "config", "receive.denyCurrentBranch", "updateInstead")
        (worktree / "repos" / member / "lib.py").write_text("x = 1\n")
        _git(worktree / "repos" / member, "add", "-A")
        _git(worktree / "repos" / member, "commit", "-qm", "member change")
    if root_source:
        (worktree / "root.txt").write_text("root source\n")
        _git(worktree, "add", "root.txt")
        _git(worktree, "commit", "-qm", "root source change")
    return row, worktree, origin


async def _run(database, run_dirs, row, worktree, fake, monkeypatch, handler):
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
        launch=LaunchContext(repo_entry={**NO_SETUP, "forge": "github"}, steering_dir=None),
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
