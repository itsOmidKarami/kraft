from support.harness import _git, make_repo, v1_named_chain

from kraft import executor, policy, store
from kraft.config import git_read

#: A repo that deliberately needs no preparation. Most tests here are about
#: resuming a chain, not environments.
NO_SETUP = {"setup_command": ""}


def _quick_task(tmp_path):
    """The shipped gateless `quick-task` (its agent never launches here)."""
    return v1_named_chain(tmp_path / "templates")


def _resume(database, run_dirs, it, *, repo_entry=NO_SETUP, **kwargs):
    return executor.resume(
        database,
        run_dirs,
        work_item_id=it.id,
        registry=None,
        adopted={},
        launch=executor.LaunchContext(repo_entry=repo_entry, steering_dir=None),
        **kwargs,
    )


async def test_reconcile_accepts_a_done_with_concerns_session(
    item_on, database, run_dirs, tmp_path
):
    """A resume over a node whose only session ended `done_with_concerns` must
    advance the chain, not report 'did not resolve cleanly'."""
    it = await item_on(_quick_task(tmp_path), "implementation", worktree=True)
    await it.session("s-impl", "implementation.main.implement", "done_with_concerns")

    assert await _resume(database, run_dirs, it) == "completed"
    assert it.status() == "completed"


async def test_reconcile_reuses_a_done_measuring_session_after_a_crash(item_on, database, run_dirs):
    """Kraft-gl9d, the story from the spec's Why section: a crash mid-`verify`
    that already saw one measuring task exit 'done' must not spend a second
    agent session re-confirming it on reconciliation -- only the failed task
    is redispatched. The fix loop's cap (attempts=0) trips on the very first
    bump, so this proves the point without needing a working fix agent."""
    it = await item_on(
        """
        - id: verify
          kind: exec
          tasks:
            - {id: suite, kind: subprocess, command: "false"}
            - {id: review, kind: subprocess, command: "true"}
          fix_loop:
            tasks: [{id: fix, kind: subprocess, command: "true"}]
        """,
        "verify",
    )
    # Build the worktree by hand, bypassing env_setup, the same way
    # test_reconcile_accepts_a_done_with_concerns_session does above --
    # `ensure_worktree` is idempotent on an existing directory, so
    # `executor.resume`'s own call to it just returns this untouched.
    it.worktree.mkdir(parents=True)
    _git(it.worktree, "init", "-q", "-b", "main")
    (it.worktree / "f.txt").write_text("x")
    _git(it.worktree, "add", "-A")
    _git(it.worktree, "commit", "-q", "-m", "init")
    head = git_read(it.worktree, "rev-parse", "HEAD")
    await it.session("s-done", "verify.main.review", "done", head_sha=head)
    await it.session("s-failed", "verify.main.suite", "failed", head_sha=head)

    # A zero cap cannot be built through the constructor (nor written in
    # a policy.yaml); force one so the first bump breaches.
    zero = policy.Cap(1, 3600)
    object.__setattr__(zero, "attempts", 0)
    pol = policy.Policy(loops={"verify.fix_loop": zero}, default=zero, auto_escalate_stuck=False)

    result = await _resume(database, run_dirs, it, policy=pol)

    assert result == "needs_human"  # verify.fix_loop's attempts=0 cap breaches on the first bump
    hooks = [s["hook_point"] for s in it.sessions("verify")]
    # the done review was never redispatched; the failed test was
    assert hooks.count("verify.main.review") == 1
    assert hooks.count("verify.main.suite") == 2


async def test_reconcile_reproduces_kraft_s15p0s_discarded_plan_session(
    item_on, database, run_dirs
):
    """The exact shape Kraft-s15p0 recorded: a single-task node whose first
    attempt failed alongside a failed escalation, and whose second attempt's
    task succeeded alongside a done escalation. `len(final) == len(tasks)`
    must be checked against attempt 2's own task row only -- 4 worker_sessions
    rows exist in total, but only 1 is the node's own current-attempt task."""
    it = await item_on(
        """
        - id: plan
          kind: exec
          tasks: [{id: author, kind: agent, harness: claude, prompt: p}]
        """,
        "plan",
        worktree=True,
    )
    # Attempt 1: the task fails, an escalation over it also fails.
    await it.session("s-task-1", "plan.main.author", "failed")
    await it.session("s-esc-1", "escalation", "failed")
    # Attempt 2 (a retry): the escalation succeeds, then the task
    # itself succeeds -- the server restarts here, mid-session, in
    # Kraft-s15p0's own story, and `session_reattached` adopts it.
    await database.write(lambda c: store.enter_node(c, it.id, "plan"))
    await it.session("s-esc-2", "escalation", "done")
    await it.session("s-task-2", "plan.main.author", "done")

    assert await _resume(database, run_dirs, it) == "completed"
    assert it.status() == "completed"


async def test_reconcile_still_needs_human_when_latest_attempt_failed(
    item_on, database, run_dirs, tmp_path
):
    """Only the latest attempt's status is authoritative — an earlier done
    attempt does not rescue a task whose most recent attempt failed."""
    it = await item_on(_quick_task(tmp_path), "implementation", worktree=True)
    await it.session("s-impl-1", "implementation.main.implement", "done")
    await it.session("s-impl-2", "implementation.main.implement", "failed")

    assert await _resume(database, run_dirs, it) == "needs_human"
    assert it.status() == "needs_human"


async def test_resume_threads_local_files_from_the_launch_context(
    item_on, database, run_dirs, tmp_path
):
    """Kraft-gxcmy's production wiring, the resume-side copy of the check in
    `test_executor_walk.py::test_run_once_threads_local_files_from_the_launch_context`.
    `resume_once` (`resuming.py:167`) is a copy-paste of `run_once`'s
    `local_files=` line -- a copy-paste that could just as easily have been
    dropped. Resuming a freshly-intaken item (crash before the first
    `load_chain`, `current_node_id` still NULL) takes `resume`'s own
    worktree-creation path rather than reusing one `run_once` already made, so
    this actually exercises `_carry_local_files`, not just an early return."""
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")
    # No env node in V1: the worktree is prepared before the first node.
    it = await item_on(
        "[{id: work, kind: exec, tasks: [{id: noop, kind: subprocess, command: 'true'}]}]",
        repo=repo,
    )

    repo_entry = {"local_files": [".python-version"], "setup_command": ""}
    assert await _resume(database, run_dirs, it, repo_entry=repo_entry) == "completed"
    assert (it.worktree / ".python-version").read_text() == "3.11\n"
