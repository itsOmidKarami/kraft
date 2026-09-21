"""Forge nodes walked through the executor: the back half of a chain
(open_mr -> mr_checks -> merge -> post_merge_watch), multi-repo items, the
CI fix loop, and the merge-time conflict rebase. `forge.run.resolve` answers
a `FakeForge`; git is real."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support.harness import isolated_bd, make_repo_with_submodule

from kraft import builtins as _builtins
from kraft import events, executor, policy, store
from kraft.adapters import forge

from .nodes import back_half, forge_node

#: A repo that needs no preparation, on a forge. A V1 forge task always runs on
#: `backend: auto`, which reads the forge off the repo entry.
NO_SETUP = {"setup_command": ""}
ON_A_FORGE = {**NO_SETUP, "forge": "github"}
RED = [(forge.FailedJob("test", "failed", "script_failure"),)]


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True)


def _commit(repo: Path, rel: str, text: str, message: str = "m") -> None:
    (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _policy(tmp_path, text: str):
    path = tmp_path / "policy.yaml"
    path.write_text(text)
    return policy.load_policy(path)


@pytest.fixture
def walk(database, run_dirs, monkeypatch):
    """`await walk(fake, item, entry=ON_A_FORGE, policy=None)`: `executor.run`
    on `item` with `resolve` answering `fake`. Returns the run's status."""

    async def go(fake, it, *, entry=ON_A_FORGE, pol=None):
        monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
        return await executor.run(
            database,
            run_dirs,
            work_item_id=it.id,
            registry=None,
            policy=pol,
            launch=executor.LaunchContext(repo_entry=entry, steering_dir=None),
        )

    return go


class _RecordingForge(forge.FakeForge):
    """Records the cwd each `open_mr` ran in."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.cwds: list[Path] = []

    async def open_mr(self, *, repo, branch, title, body, meta=None):
        self.cwds.append(Path(repo))
        return await super().open_mr(repo=repo, branch=branch, title=title, body=body, meta=meta)


@pytest.mark.parametrize(
    "ci, entry, expected, opened, merged",
    [
        # open_mr -> ci_poll -> merge. This whole stretch was builtin:noop.
        ({"ci_states": ["success"]}, ON_A_FORGE, "completed", True, [1]),
        # The repo's forge has to reach the node: without it every node of a
        # `backend: auto` chain fails on a repo whose forge is recorded fine.
        ({"ci_states": ["success"]}, {**NO_SETUP, "forge": "gitlab"}, "completed", True, [1]),
        # The one that matters: a failed pipeline must never reach merge.
        ({"ci_states": ["failed"], "ci_failed_jobs": RED}, ON_A_FORGE, "needs_human", True, []),
        # A repo Kraft holds no forge for: `auto` fails the node rather than
        # guessing a CLI.
        ({"ci_states": ["success"]}, NO_SETUP, "needs_human", False, []),
    ],
    ids=["green-github", "green-gitlab", "red-pipeline-stops-before-merge", "no-forge-recorded"],
)
async def test_the_back_half_walks_to_merge_only_on_a_green_pipeline(
    walk, item_on, ci, entry, expected, opened, merged
):
    fake = _RecordingForge(**ci)

    assert await walk(fake, await item_on(back_half()), entry=entry) == expected

    assert bool(fake.opened) is opened
    assert fake.merged == merged
    # `glab mr create` uses the *current branch* of its cwd. Run from the main
    # repo, that is whatever the human has checked out -- not kraft/<id>.
    assert all("worktrees" in str(cwd) for cwd in fake.cwds), fake.cwds


async def test_the_executor_hands_the_task_s_resolved_wait_to_the_forge(walk, item_on, monkeypatch):
    """A task's `wait:` reaches `run_task` resolved (`ForgeTask.wait_bounds`):
    the authored timeout and initial interval, and the default maximum it
    left unsaid. Read off the adapter call rather than inferred."""
    from kraft.templates.models import WaitBounds

    fake = forge.FakeForge(ci_states=["pending", "success"])
    seen: list[dict] = []
    real = forge.run_task

    async def spy(*a, **kw):
        seen.append(kw)
        return await real(*a, **kw)

    monkeypatch.setattr("kraft.executor.dispatch._forge.run_task", spy)
    chain = back_half(wait={"timeout": "5m", "polling": {"initial_interval": "30s"}})

    assert await walk(fake, await item_on(chain)) == "waiting"

    assert fake.opened and fake.merged == [], "a pipeline still pending must not reach merge"
    ci = next(kw for kw in seen if kw["handler"] == "ci_poll")
    assert ci["wait"] == WaitBounds.from_seconds(timeout=300, initial=30, maximum=300)
    opened = next(kw for kw in seen if kw["handler"] == "open_mr")
    assert opened["wait"] is None, "opening a draft is not a wait"


async def test_post_merge_watch_delays_completion_and_bead_close_until_it_runs(
    bd, database, run_dirs, repo, tmp_path, monkeypatch
):
    """Kraft-43kw: `work_item_completed` and the bead close land after
    `post_merge_watch`'s own `node_completed`, not after `merge`'s."""
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    wid = await executor.intake(
        database,
        run_dirs,
        title="watch after merge",
        repo=str(repo),
        chain=back_half(forge_node("post_merge_watch", "mr.post_merge_ci")),
        bd_cwd=str(tracker),
    )

    result = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        registry=None,
        bd_cwd=str(tracker),
        launch=executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
    )

    assert result == "completed"
    it_row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    assert it_row["status"] == "completed"
    evts = database.read(lambda c: events.read_after(c, 0, wid))
    nodes = [e["payload"]["node_id"] for e in evts if e["type"] == "node_completed"]
    assert nodes == ["open_mr", "mr_checks", "merge", "post_merge_watch"]
    assert evts[-1]["type"] == "work_item_completed"
    assert bd.status(it_row["bead_id"], cwd=tracker) == "closed"


async def test_ci_fix_loop_stops_for_waiting_on_the_repairs_re_measure(walk, item_on, tmp_path):
    """`recover_node`'s re-measure calls `on.ci.poll` again, and a repaired
    head's pipeline being merely `pending` is the *routine* case: a
    metadata-only repair touches nothing CI runs against. Handing WAITING to
    the fix-cycle machinery would spend a paid fix cycle on a pipeline that
    has not even settled. The repair and fix are `true` stand-ins."""
    node = forge_node("mr_checks", "mr.ci")
    node["on_failure"] = {"tasks": [{"id": "repair", "kind": "subprocess", "command": "true"}]}
    node["fix_loop"] = {"tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]}
    fake = forge.FakeForge(ci_states=["failed", "pending"], ci_failed_jobs=RED * 2)
    pol = _policy(
        tmp_path,
        "loops:\n  mr_checks.fix_loop: { attempts: 3, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n",
    )
    it = await item_on([node])

    assert await walk(fake, it, pol=pol) == "waiting"

    assert not it.events("fix_cycle_started"), "a paid fix cycle on an unsettled pipeline"
    assert it.status() == "waiting"


async def test_ci_poll_stops_for_a_human_on_a_real_rebase_conflict(
    walk, item_on, database, run_dirs, repo, tmp_path, monkeypatch
):
    """The worktree's branch and origin each edit the same line of `calc.py`,
    so `ci_poll`'s forced rebase itself conflicts -- the fixture shape
    `test_refresh_worktree_base_raises_and_aborts_on_conflict` pins for
    `refresh_worktree_base` alone, driven through the whole node. With no
    `fix_loop`/`on_failure`, `walk_node` stops for a human; `base_ref` is
    never touched. The node declares no `on_base_changed.on_conflict`, so the
    conflict is an ordinary task failure (`rebase-conflict-requires-explicit-
    handler`; the handler's own path is tests/executor/test_base_change.py)."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    _commit(repo, ".gitignore", ".engineering/\n", "gitignore .engineering")
    verify = {
        "id": "verify",
        "kind": "exec",
        "tasks": [{"id": "suite", "kind": "subprocess", "command": "true"}],
    }
    it = await item_on([verify, forge_node("mr_checks", "mr.ci")])
    worktree = await _builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id=it.id, repo_entry=NO_SETUP
    )
    original_base = it.row()["base_ref"]
    _commit(worktree, "calc.py", "def add(a, b):\n    return a - b - 1  # bug: should be +\n")
    _commit(repo, "calc.py", "def add(a, b):\n    return a - b - 2  # bug: should be +\n")
    fake = forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict")
    # Kraft-lpdd: about the rebase's own stop, not the auto-escalate trigger.
    pol = _policy(
        tmp_path, "default: { attempts: 3, wall_clock_s: 3600 }\nauto_escalate_stuck: false\n"
    )

    assert await walk(fake, it, pol=pol) == "needs_human"

    assert it.row()["base_ref"] == original_base, "a real conflict must never move base_ref"
    # The stop reason names the task's kind; the detail is in the session log.
    (session,) = [
        s for s in it.sessions("mr_checks") if s["hook_point"] == "mr_checks.main.mr_checks"
    ]
    log = Path(session["log_path"]).read_text().lower()
    assert "rebase" in log and "failed" in log


async def test_merge_completes_the_merge_after_a_rebase_when_no_bounce_is_configured(
    walk, item_on, database, run_dirs, repo, monkeypatch
):
    """The forced rebase at `merge` reports a bare "done" without calling
    `forge.merge` only when this node's own chain entry carries
    `rebase_bounce_to` (`test_merge_rebases_a_conflict_away_before_calling_merge`).
    A chain with no such node -- every V1 chain -- must still call
    `forge.merge` once the rebased head is confirmed green, or the walk sails
    on to completion with the branch never merged (code-review)."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    _commit(repo, ".gitignore", ".engineering/\n", "gitignore .engineering")

    class ResolvesAfterRebase(forge.FakeForge):
        async def push(self, *, repo, branch):
            await super().push(repo=repo, branch=branch)
            # The conflict-rebase's own push is the second one: a real forge
            # sees the rebased head as mergeable again by the next read.
            if len(self.pushed) >= 2:
                self.mergeable = True

    fake = ResolvesAfterRebase(ci_states=["success"], mergeable=False, merge_detail="conflict")
    it = await item_on([forge_node("open_mr", "mr.open_draft"), forge_node("merge", "mr.merge")])
    await _builtins.ensure_worktree(
        database, run_dirs, repo=str(repo), work_item_id=it.id, repo_entry=NO_SETUP
    )
    # Origin moves in a way the branch does not touch: the forced rebase is clean.
    _commit(repo, "moved.txt", "moved on\n", "moved on upstream")

    assert await walk(fake, it) == "completed"

    assert fake.merged == [1], "a rebase with no configured bounce must still land the merge"


# --- multi-repo items ------------------------------------------------------------


async def _multi_repo_item(item_on, database, run_dirs, tmp_path, policy_name, *, sub="repos/pkg"):
    """A root with one submodule at `sub`, filed with `root_merge_policy`, its
    worktree cut. Returns `(item, worktree, branch)`."""
    root, _ = make_repo_with_submodule(tmp_path, submodule_path=sub)
    it = await item_on(back_half(), repo=root, submodules=[sub], root_merge_policy=policy_name)
    worktree = await _builtins.ensure_worktree(
        database, run_dirs, repo=str(root), work_item_id=it.id, repo_entry=NO_SETUP
    )
    return it, worktree, store.branch_for(it.row())


async def _run_task(
    database, run_dirs, it, worktree, branch, fake, monkeypatch, handler="open_mr", **kw
):
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    return await forge.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id=it.id,
        node_id=handler,
        hook_point=f"on.{handler}",
        handler=handler,
        backend="fake",
        repo=worktree,
        branch=branch,
        title="t",
        **kw,
    )


async def test_run_task_opens_a_merge_request_per_repo_deepest_first(
    item_on, database, run_dirs, tmp_path, monkeypatch
):
    """Root goes through the ordinary loop too, deepest submodule first, when
    it has changes of its own to review (Task 7 exempts only a root with
    nothing of its own -- the next test)."""
    it, worktree, branch = await _multi_repo_item(item_on, database, run_dirs, tmp_path, "bump")
    # `_commits_on` diffs against origin/main; with no origin, root always reads
    # as "no changes" and drops out of the loop whatever changed.
    origin = tmp_path / "origin.git"
    root = Path(it.repo)
    subprocess.run(["git", "clone", "--bare", "-q", str(root), str(origin)], check=True)
    _git(root, "remote", "add", "origin", str(origin))
    _git(worktree, "fetch", "-q", "origin")
    _commit(worktree, "root-change.txt", "x\n", "root change")
    fake = _RecordingForge(ci_states=["success"])

    assert await _run_task(database, run_dirs, it, worktree, branch, fake, monkeypatch) == "done"

    repos = database.read(lambda c: store.repos_for(c, it.id))
    assert fake.cwds == [worktree / "repos" / "pkg", worktree], "submodule first, root last"
    assert [r["role"] for r in repos] == ["submodule", "root"]
    assert repos[0]["state"] == "open"


async def test_root_with_no_changes_of_its_own_never_opens_a_merge_request(
    item_on, database, run_dirs, tmp_path, monkeypatch
):
    it, worktree, branch = await _multi_repo_item(
        item_on, database, run_dirs, tmp_path, "bump_no_mr"
    )
    _commit(worktree / "repos" / "pkg", "new.txt", "x\n")  # the agent's half: submodule only
    fake = forge.FakeForge(ci_states=["success"])

    await _run_task(database, run_dirs, it, worktree, branch, fake, monkeypatch)

    assert len(fake.opened) == 1, "the submodule only"
    repos = database.read(lambda c: store.repos_for(c, it.id))
    assert next(r for r in repos if r["role"] == "root")["state"] == "pending"


async def test_run_task_is_unchanged_for_a_single_repo_item(
    item_on, database, run_dirs, repo, monkeypatch
):
    """The regression the multi-repo loop is not allowed to cause."""
    it = await item_on(back_half())
    fake = forge.FakeForge(ci_states=["success"])

    assert await _run_task(database, run_dirs, it, repo, "kraft/w1", fake, monkeypatch) == "done"

    assert len(fake.opened) == 1


async def test_merge_does_not_treat_a_rebased_submodule_as_landed_in_a_multi_repo_item(
    item_on, database, run_dirs, tmp_path, monkeypatch
):
    """`_run_one`'s conflict-rebase shortcut reports "rebased", not "merged",
    for a target it only rebased (guaranteed a re-verify via
    `has_rebase_bounce`). `run_task`'s per-target loop must stop right there --
    no `merge_state='merged'` for that row, no later targets, and no root
    pointer bump (which would push root pointing at a branch nothing merged)."""
    it, worktree, branch = await _multi_repo_item(item_on, database, run_dirs, tmp_path, "bump")
    calls: list[Path] = []

    async def fake_run_one(*args, **kwargs):
        calls.append(kwargs["repo"])
        return "rebased away, not merged\n", "rebased", None

    monkeypatch.setattr(forge.run, "_run_one", fake_run_one)
    fake = forge.FakeForge(ci_states=["success"])

    status = await _run_task(
        database,
        run_dirs,
        it,
        worktree,
        branch,
        fake,
        monkeypatch,
        handler="merge",
        has_rebase_bounce=True,
    )

    assert status == "done", "a guaranteed bounce still reports 'done' to the walk"
    assert len(calls) == 1, "must stop right after the rebased target, never reach root"
    repos = database.read(lambda c: store.repos_for(c, it.id))
    assert all(r["state"] != "merged" for r in repos), "a rebased target is not a merged one"
    assert fake.merged == []


async def test_the_shape_that_broke_on_9d0ab38ff3c9439b90506df0f6966660(
    walk, item_on, database, run_dirs, tmp_path
):
    """A work item whose entire deliverable is inside a submodule, root told
    not to bump its pointer: the submodule gets its own merge request and the
    root gets none. The standing regression test for the real occurrence."""
    it, worktree, _ = await _multi_repo_item(
        item_on, database, run_dirs, tmp_path, "skip", sub="repos/packages"
    )
    _commit(worktree / "repos" / "packages", "metrics.py", "ATTRS = 6\n")
    fake = forge.FakeForge(ci_states=["success"])

    assert await walk(fake, it) == "completed"

    assert fake.merged == [1], "only the submodule's MR, never a root one"
    repos = {r["role"]: r["state"] for r in database.read(lambda c: store.repos_for(c, it.id))}
    assert repos == {"submodule": "merged", "root": "pending"}, "skip: root untouched, as asked"
