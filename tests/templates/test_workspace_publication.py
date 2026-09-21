"""A workspace work item end to end: fan-out by repository and the policy
each repository runs under, area setup before an area's test scope, and
publication order across the members and the root (Task 10).

Git is real (a root with one real submodule, `make_repo_with_submodule`);
the forge is `FakeForge`, and a task's process is a spy wherever what it ran
under is the question."""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest
from support import worktree as wtree
from support.harness import _git, make_repo, make_repo_with_submodule, v1_chain
from support.workspace import workspace_target

from kraft import store
from kraft.adapters import forge
from kraft.executor import dispatch
from kraft.executor.context import LaunchContext
from kraft.policy import InstancePolicy, InstancePolicyInput, SandboxPolicy

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
    **materialize,
):
    """A root with one submodule `pkg` at `repos/pkg` (and `pkg2` at
    `repos/pkg2` when `second`), filed as a workspace item selecting them under
    root-pointer policy `pointer`, on one exec node of `tasks`; its checkout
    assembled. Returns `(row, node, worktree)`."""
    root, _ = make_repo_with_submodule(tmp_path)
    mounts = {"pkg": "repos/pkg"}
    if second:
        pkg2 = make_repo(tmp_path, name="pkg2")
        _git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(pkg2), "repos/pkg2")
        _git(root, "commit", "-qm", "add a second submodule")
        mounts["pkg2"] = "repos/pkg2"
    chain = v1_chain(
        [{"id": "n", "kind": "exec", "tasks": tasks}],
        repo=root,
        target=None if legacy else workspace_target(mounts, root_pointer_policy=pointer),
    )
    if materialize:
        chain = chain.chain.materialize(target=chain.target, **materialize)
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
    origin `main`, and which records, in order, every merge and every
    readiness -- the root's with the member pointer its head carried then."""

    def __init__(self, refuse: str | None = None, raise_on_merge: bool = False):
        super().__init__(ci_states=["success"])
        self.order: list[tuple] = []
        self.refuse = refuse
        self.raise_on_merge = raise_on_merge

    async def ci_status(self, *, repo, mr, branch="", pipeline_id=""):
        status = await super().ci_status(repo=repo, mr=mr, branch=branch, pipeline_id=pipeline_id)
        if Path(repo).name != self.refuse or self.raise_on_merge:
            return status
        return dataclasses.replace(status, block_reason="not_approved", merge_detail="1 approval")

    async def merge(self, *, repo, branch="", mr):
        if self.raise_on_merge and Path(repo).name == self.refuse:
            raise forge.ForgeError("merge refused: the branch is protected")
        await super().merge(repo=repo, branch=branch, mr=mr)
        _git(repo, "push", "-q", "origin", "HEAD:main")
        self.order.append(("merge", Path(repo).name))

    async def mark_ready(self, *, repo, branch, mr):
        await super().mark_ready(repo=repo, branch=branch, mr=mr)
        root = not Path(repo).name.startswith("pkg")
        pointer = _git_out(repo, "rev-parse", "HEAD:repos/pkg") if root else None
        self.order.append(("ready", Path(repo).name, pointer))


async def _publishable(
    database, run_dirs, tmp_path, *, pointer, root_denies_push=False, second=False, legacy=False
):
    """A workspace item whose root and member each have an origin that takes a
    push (the member's is its source repository), the member carrying a
    commit. Returns `(row, worktree, root_origin)`."""
    row, _, worktree = await _workspace_item(
        database, run_dirs, tmp_path, [_task("t")], pointer=pointer, second=second, legacy=legacy
    )
    root = Path(row["repo"])
    members = ["pkg", "pkg2"] if second else ["pkg"]
    origin = tmp_path / "root-origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(root), str(origin))
    if root_denies_push:
        hook = origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'protected branch' >&2\nexit 1\n")
        hook.chmod(0o755)
    _git(root, "remote", "add", "origin", str(origin))
    _git(worktree, "fetch", "-q", "origin")
    for member in members:
        _git(tmp_path / member, "config", "receive.denyCurrentBranch", "updateInstead")
        (worktree / "repos" / member / "lib.py").write_text("x = 1\n")
        _git(worktree / "repos" / member, "add", "-A")
        _git(worktree / "repos" / member, "commit", "-qm", "member change")
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
    request, from the item's own branch in the root."""
    row, worktree, origin = await _publishable(
        database, run_dirs, tmp_path, pointer="bump", root_denies_push=True
    )
    before = _git_out(origin, "rev-parse", "main")
    fake = _LandingForge()
    await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr")

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "done"

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
    row, worktree, _ = await _publishable(database, run_dirs, tmp_path, pointer="ignore")
    (worktree / "root.txt").write_text("root source\n")
    _git(worktree, "add", "root.txt")
    _git(worktree, "commit", "-qm", "root source change")
    fake = _LandingForge()

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr") == "done"
    assert len(fake.opened) == 2 and all(fake.opened_draft.values())
    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "mark_ready") == "done"
    assert fake.order == [("ready", "pkg", None)], "the root stays a draft"

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == "done"

    merged = _git_out(tmp_path / "pkg", "rev-parse", "main")
    assert fake.order[1:] == [("merge", "pkg"), ("ready", row["id"], merged), ("merge", row["id"])]


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
        database, run_dirs, tmp_path, pointer="bump", second=second
    )
    if root_source:
        (worktree / "root.txt").write_text("root source\n")
        _git(worktree, "add", "root.txt")
        _git(worktree, "commit", "-qm", "root source change")
    before = _git_out(origin, "rev-parse", "main")
    fake = _LandingForge(refuse="pkg2" if second else "pkg", raise_on_merge=refused)
    await _run(database, run_dirs, row, worktree, fake, monkeypatch, "open_mr")

    assert await _run(database, run_dirs, row, worktree, fake, monkeypatch, "merge") == status

    assert fake.order == landed
    assert _git_out(origin, "rev-parse", "main") == before
    assert _repos(database, row)["root"] != "merged"


# ── publication in the chain's own order ──


def _chain(*nodes):
    from kraft.templates.models import Chain

    return Chain.model_validate({"id": "c", "nodes": list(nodes)})


def _forge(id, target):
    return {"id": id, "kind": "exec", "tasks": [{"id": id, "kind": "forge", "target": target}]}


_FINAL = {"id": "final", "kind": "gate", "message": "m", "chain_finalized": True}


_WORK = {"id": "w", "kind": "subprocess", "command": "true"}


def _placed(where: str, task: dict) -> dict:
    """An execution node running `task` from position `where`: every task
    position the executor can run in an execution node (a gate's
    `auto_review` is an agent task by type, so it cannot publish)."""
    node = {"id": "n", "kind": "exec", "tasks": [_WORK]}
    recovery = {"tasks": [task]}
    match where:
        case "node-task":
            node["tasks"] = [task]
        case "step-task":
            del node["tasks"]
            node["steps"] = [{"id": "s", "tasks": [task]}]
        case "task-on-failure":
            node["tasks"] = [{**_WORK, "on_failure": recovery}]
        case "step-on-failure":
            del node["tasks"]
            node["steps"] = [{"id": "s", "tasks": [_WORK], "on_failure": recovery}]
        case "node-on-failure":
            node["on_failure"] = recovery
        case "fix-loop":
            node["fix_loop"] = recovery
        case "fix-loop-judge":
            node["fix_loop"] = {"tasks": [_WORK], "judge": task}
        case "escalation":
            node["escalation"] = task
        case "on-conflict":
            node["on_base_changed"] = {"restart_from": "n", "on_conflict": recovery}
    return node


_POSITIONS = [
    "node-task",
    "step-task",
    "task-on-failure",
    "step-on-failure",
    "node-on-failure",
    "fix-loop",
    "fix-loop-judge",
    "escalation",
    "on-conflict",
]


@pytest.mark.parametrize("target", ["mr.mark_ready", "mr.merge"])
@pytest.mark.parametrize("where", _POSITIONS)
def test_nothing_readies_or_merges_before_the_final_gate(target, where):
    """`draft-merge-request-enables-external-checks`: a draft may be opened
    before final-gate approval, but a chain that could mark it ready or merge
    it before that approval -- from any task position the executor can run in
    a node ahead of the gate -- is refused when it is loaded, not discovered
    once it has published (Kraft-nwonj)."""
    node = _placed(where, {"id": "t", "kind": "forge", "target": target})
    with pytest.raises(ValueError, match=f"{target}.*before.*'final'"):
        _chain(_forge("draft", "mr.open_draft"), node, _FINAL)
    # After the gate, the same node is the ordinary publication.
    assert _chain(_forge("draft", "mr.open_draft"), _FINAL, node)


def test_the_seeded_default_chain_publishes_in_the_required_order():
    """`default-post-draft-flow-is-ordered` and `final-gate-governs-merge-
    request-readiness`: draft, CI then automated review, the summary, the
    final gate; only then ready, external approval, merge."""
    from kraft.templates.library import TemplateLibrary

    chain = TemplateLibrary.from_yaml_dir(Path(__file__).parents[2] / "templates").resolve_chain(
        "default"
    )
    order = []
    for n in chain.nodes:
        if n.node.kind == "gate":
            order += ["final gate"] if n.node.chain_finalized else []
        else:
            order += [t.task.target for t in n.tasks() if t.task.kind == "forge"]
    assert [str(o) for o in order if o != "mr.sync"] == [
        "mr.open_draft",
        "mr.ci",
        "mr.automated_review",
        "final gate",
        "mr.mark_ready",
        "mr.external_approval",
        "mr.merge",
        "mr.post_merge_ci",
    ]
    ids = [n.id for n in chain.nodes]
    assert ids.index("work_item_summary") < ids.index("chain_review") < ids.index("mark_ready")


async def test_a_pre_draft_gate_keeps_the_work_local(item_on, database, run_dirs, monkeypatch):
    """`optional-pre-draft-gate-keeps-work-local`: until a gate before the
    draft is approved, the walk stops there and nothing reaches the forge."""
    from kraft import executor

    it = await item_on(
        [
            {"id": "local_review", "kind": "gate", "message": "Approve the draft."},
            _forge("draft", "mr.open_draft"),
        ]
    )
    fake = forge.FakeForge()
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    await executor.run(
        database,
        run_dirs,
        work_item_id=it.id,
        registry=None,
        policy=None,
        launch=LaunchContext(repo_entry={**NO_SETUP, "forge": "github"}, steering_dir=None),
    )

    assert it.row()["current_node_id"] == "local_review"
    assert fake.opened == {} and fake.pushed == []
