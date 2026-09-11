"""Choosing a backend, and running one forge node against one or several
repos.
"""

from __future__ import annotations

from pathlib import Path

from kraft import events
from kraft.adapters.forge import ci, git
from kraft.adapters.forge import mr as mr_ops
from kraft.adapters.forge.ci import (
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_TIMEOUT,
    MERGE_VERIFY_INTERVAL,
    MERGE_VERIFY_TIMEOUT,
)
from kraft.adapters.forge.gh import GhCli
from kraft.adapters.forge.glab import GlabCli
from kraft.adapters.forge.models import MR, FakeForge, Forge, ForgeError


def resolve(name: str) -> Forge:
    """Named, never probed.

    Auto-detecting an available CLI would mean the same work item takes a
    different path on a laptop than in a container, and a bug that reproduces on
    one and not the other. A `glab` that is installed but unauthenticated also
    looks available and then fails deep inside a node.

    The accepted names are duplicated in `templates._FORGE_BACKENDS`, which
    validates a registry file without importing this module. Edit both together;
    that set also carries `auto`, which `backend_for` has already translated by
    the time anything calls this.
    """
    match name:
        case "glab":
            return GlabCli()
        case "gh":
            return GhCli()
        case "fake":
            return FakeForge()
        case _:
            raise ForgeError(f"unknown forge backend {name!r}; known: gh, glab, fake")


#: repos.yaml's `forge` (config._FORGES) -> the CLI that talks to it. Two
#: vocabularies on purpose: `forge` is a fact about the remote, the backend is
#: a fact about this machine, and a self-hosted GitLab is `gitlab` with `glab`.
_FORGE_CLI = {"gitlab": "glab", "github": "gh"}


def backend_for(backend: str, repo_forge: str | None) -> str:
    """`auto` means the forge recorded for this repo; any other name is itself.

    The registry is per install and the forge is a property of the repo, so one
    backend name in one file cannot be right for two `kraft repo connect`s. This
    is the only place that gap is closed; `resolve` stays named and never sees
    `auto`.
    """
    if backend != "auto":
        return backend
    cli = _FORGE_CLI.get(repo_forge or "")
    if cli is None:
        raise ForgeError(
            "backend: auto, but no forge is recorded for this repo — set "
            "`forge: gitlab` or `forge: github` on it in Settings → Repos "
            "(or repos.yaml), or pin a `backend:` in registry.yaml"
        )
    return cli


async def _run_one(
    forge: Forge,
    db,
    *,
    repo: Path,
    branch: str,
    title: str,
    work_item_id: str,
    handler: str,
    hook_point: str,
    poll_timeout: float,
    poll_interval: float,
    merge_timeout: float,
    merge_interval: float,
) -> tuple[str, str]:
    """One forge handler against one repo. Extracted from `run_task` so the
    multi-repo loop there can call it once per `work_item_repos` row; a
    single-repo item's behaviour is unchanged -- one call, same log and status
    strings as before this split.
    """
    body = mr_ops.mr_body(work_item_id, branch, await git.commits_on(repo, branch))
    match handler:
        case "open_mr":
            # Ask first: a rejected review re-entering the chain at
            # implementation walks back through this node, and a retry of
            # an open_mr that crashed after the create lands here too. A
            # second `mr create` for one branch is an error on both CLIs
            # (Kraft-xron).
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "open":
                # The same two calls `sync_mr` makes, for the same reason:
                # the description has to describe the head a reviewer will
                # see, and the head has to be pushed.
                await forge.push(repo=repo, branch=branch)
                await forge.update_mr(repo=repo, branch=branch, body=body)
                number, url = existing.number, existing.url
                log, status = f"reusing !{number}: {url}\n", "done"
            else:
                mr = await forge.open_mr(repo=repo, branch=branch, title=title, body=body)
                number, url = mr.number, mr.url
                log, status = f"opened {url}\n", "done"
            # An event, not a work-item column (Kraft-d2sq): no migration,
            # it reaches the UI through the stream that already exists, and
            # it is timestamped, which a column is not. Both paths emit it,
            # so an item whose open_mr is re-entered after a rejected
            # review still carries a current record.
            await db.write(
                lambda c, n=number, u=url: events.append(
                    c, work_item_id, "mr_opened", {"number": n, "url": u}
                )
            )
        case "ci_poll":
            # The worker commits in the worktree and is told not to push
            # (adapters/agent.py:45), and only open_mr and sync_mr ever
            # pushed — sync_mr *after* human review. Without this, a commit
            # made after open_mr is local, so the pipeline polled below is
            # the previous head's: the same id on every retry, green or
            # red, forever (Kraft-bxj8). `git push -u` is a no-op when the
            # branch is up to date, so this costs one git call.
            await forge.push(repo=repo, branch=branch)
            # One check, not a wait. A pipeline that has not settled hands the
            # wait back to the scheduler as a row state (Kraft-ru98); the
            # coroutine that used to sit here for up to poll_timeout held an
            # intake slot and could not be cancelled. `merge`'s own gate still
            # calls `wait_for_ci` -- that one is Kraft-7jja.
            ci_status = await forge.ci_status(repo=repo, mr=MR(number=0, url=""), branch=branch)
            log, status = ci.render_ci(ci_status)
        case "sync_mr":
            # Push first: every commit after `open_mr` -- verify's fixes,
            # mr_checks' findings -- is local only until this runs, and
            # `merge` refuses a branch ahead of its remote (Kraft-nh5m).
            await forge.push(repo=repo, branch=branch)
            # The description `open_mr` wrote predates every commit verify
            # and mr_checks added, so it is rewritten from the branch head
            # before a human is asked to read it (Kraft-c09h).
            await forge.update_mr(repo=repo, branch=branch, body=body)
            log, status = "pushed and synced the merge request description\n", "done"
        case "merge":
            # "Someone merged it first" and "the merge was refused" were
            # indistinguishable while this only ever shelled out: both
            # arrived as a non-zero exit and stopped the item at the last
            # node with the work already on main (Kraft-xron). Auto-merge
            # reaching the branch first is an ordinary event, and this
            # node's stated end state is already true.
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "merged":
                log = f"already merged (!{existing.number}); nothing to do\n"
                status = "done"
            elif existing is not None and existing.state == "open":
                # The same push ci_poll makes, for the same reason: a
                # commit made after the last sync is local only, and
                # `_assert_pushed` inside forge.merge refuses a head origin
                # has never seen — three failed nodes, and a retry of
                # `merge` alone never re-runs mr_sync to clear it
                # (Kraft-bxj8). Below the already-merged check: a branch
                # already in main needs nothing pushed to it.
                await forge.push(repo=repo, branch=branch)
                # `mr_sync`'s push, right after the human_review gate that
                # ran after mr_checks last validated CI, can land a commit
                # -- verify's fixes, mr_checks' own findings -- on a head
                # mr_checks never watched. On a project that requires a
                # green pipeline before merge, that push re-arms the
                # requirement for a pipeline nothing here has seen finish
                # (Kraft-266b). Wait it out the same way ci_poll does, with
                # the same budget, before handing forge.merge() a head
                # nothing has validated (Kraft-x10m).
                ci_log, gate_status = await ci.wait_for_ci(
                    forge, repo=repo, branch=branch, timeout=poll_timeout, interval=poll_interval
                )
                if gate_status != "done":
                    log, status = ci_log, "failed"
                else:
                    # A genuine refusal -- conflicts, unmet approval rules --
                    # still raises inside forge.merge and still fails the node.
                    await forge.merge(repo=repo, branch=branch, mr=MR(number=0, url=""))
                    # And a refusal the CLI reported as success does not get
                    # through either. This node's stated end state is "this
                    # branch is in main", so it is read off the forge rather
                    # than inferred from an exit code (Kraft-79x3).
                    landed = await ci.poll_merged(
                        forge,
                        repo=repo,
                        branch=branch,
                        timeout=merge_timeout,
                        interval=merge_interval,
                    )
                    state = landed.state if landed is not None else "gone"
                    if state == "merged":
                        log, status = f"merged !{landed.number}\n", "done"
                    elif state == "open":
                        log = (
                            f"!{landed.number} is still open {merge_timeout:g}s after the "
                            "merge command returned: nothing has landed on main. The forge "
                            "may have scheduled an auto-merge for when its checks pass — "
                            f"look at {landed.url}\n"
                        )
                        status = "failed"
                    else:
                        log = f"the merge request is {state}, not merged; nothing landed\n"
                        status = "failed"
            else:
                raise ForgeError(
                    f"no open merge request for {branch!r}"
                    + (f": !{existing.number} is {existing.state}" if existing else "")
                )
        case _:
            log, status = f"unknown forge handler {handler!r}\n", "failed"
    return log, status


async def run_task(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    handler: str,
    backend: str,
    #: The repo entry's `forge` (`repos.yaml`), for `backend: auto`. None means
    #: nothing recorded, which `backend_for` turns into a failed node.
    repo_forge: str | None = None,
    repo: Path,
    branch: str,
    title: str,
    round: int = 0,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    #: The merge node's read-back, not the pipeline poll: separate names
    #: because they answer different questions and are tuned differently.
    #: Keyword arguments only so tests can run them at zero; no registry key.
    merge_timeout: float = MERGE_VERIFY_TIMEOUT,
    merge_interval: float = MERGE_VERIFY_INTERVAL,
    head_sha: str | None = None,
) -> str:
    """One forge node -- against every repo `work_item_repos` names for this
    item, deepest submodule first, root last (design 3a), or just `repo` when
    the table has no rows for it (every single-repo item, unchanged from
    before this function went multi-repo).

    Records a session the way a builtin does rather than the way the subprocess
    adapter does: the work happens in this process, so there is no child to
    supervise and no log fd to hand over. The row still has to exist for the
    whole run, not just at the end -- `ci_poll` can sit in `poll_ci` for up to
    `poll_timeout` (30 minutes by default), and pause/abandon/reattach all key
    off `worker_sessions`. Recording nothing until the node finished silently
    broke all three for the length of that wait (Kraft-41b, Kraft-7xt).
    """
    from kraft import builtins as _builtins
    from kraft import store

    log_path, result_path = await _builtins.start_session(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        round=round,
        head_sha=head_sha,
    )
    rows = db.read(
        lambda c: c.execute(
            "SELECT * FROM work_item_repos WHERE work_item_id = ? ORDER BY merge_rank",
            (work_item_id,),
        ).fetchall()
    )
    multi = bool(rows)
    targets = [(r["id"], Path(r["repo_path"]), r["role"]) for r in rows] or [(None, repo, "root")]

    root_has_changes = True
    root_policy = "bump"
    if multi:
        policy_row = db.read(
            lambda c: c.execute(
                "SELECT root_merge_policy FROM work_items WHERE id = ?", (work_item_id,)
            ).fetchone()
        )
        root_policy = (policy_row["root_merge_policy"] if policy_row else None) or "bump"
        root_repo = next(t for _, t, role in targets if role == "root")
        root_has_changes = bool(await git.commits_on(root_repo, branch))
        if root_policy == "skip" or not root_has_changes:
            # Nothing of root's own to review -- it never goes through the
            # ordinary per-repo loop below. Its pointer bump, if any (never
            # under `skip`), is pushed directly after every submodule merges
            # (below), not through a merge request -- see the plan's "Design
            # correction": at `open_mr` time no submodule has merged yet, so
            # a root MR here would review a pointer that doesn't exist.
            targets = [t for t in targets if t[2] != "root"]

    log, status = "", "done"
    try:
        # Inside the try: `backend_for` can raise, and an exception escaping
        # here would skip `finish_session` and strand the session row started
        # above (Kraft-41b, Kraft-7xt are the same wound from the other side).
        live_forge = resolve(backend_for(backend, repo_forge))
        for row_id, target_repo, role in targets:
            if handler == "open_mr" and role == "root" and multi:
                await git._assert_submodules_covered(target_repo, {t for _, t, _ in targets})
            one_log, one_status = await _run_one(
                live_forge,
                db,
                repo=target_repo,
                branch=branch,
                title=title,
                work_item_id=work_item_id,
                handler=handler,
                hook_point=hook_point,
                poll_timeout=poll_timeout,
                poll_interval=poll_interval,
                merge_timeout=merge_timeout,
                merge_interval=merge_interval,
            )
            log += (f"[{target_repo.name}] " if multi else "") + one_log
            if row_id is not None:
                new_state = {
                    "open_mr": "open",
                    "merge": "merged" if one_status == "done" else "failed",
                }.get(handler)
                if new_state:
                    await db.write(
                        lambda c, i=row_id, s=new_state: store.update_repo_state(
                            c, i, merge_state=s
                        )
                    )
            if one_status != "done":
                status = one_status
                break
        if (
            handler == "merge"
            and multi
            and root_policy != "skip"
            and not root_has_changes
            and status == "done"
        ):
            # `multi` guarantees `rows` is non-empty here, so this is always
            # the root row's own path, not `repo` (the worktree root is the
            # same thing, but the row is the one source of truth).
            root_repo = Path(next(r["repo_path"] for r in rows if r["role"] == "root"))
            bumped = []
            for r in rows:
                if r["role"] != "submodule":
                    continue
                sub_path = Path(r["repo_path"])
                default = await git.default_branch(sub_path)
                await git.run_git(sub_path, ["git", "fetch", "origin", default])
                merged_sha = (
                    await git.run_git(sub_path, ["git", "rev-parse", f"origin/{default}"])
                ).strip()
                await git.run_git(sub_path, ["git", "checkout", merged_sha])
                rel = str(sub_path.relative_to(root_repo))
                await git.run_git(root_repo, ["git", "add", "--", rel])
                bumped.append(rel)
            if (
                bumped
                and (
                    await git.run_git(root_repo, ["git", "status", "--porcelain", "--cached"])
                ).strip()
            ):
                await git.run_git(
                    root_repo,
                    ["git", "commit", "-m", f"chore: bump submodule pointers for {work_item_id}"],
                )
                root_default = await git.default_branch(root_repo)
                await git.run_git(root_repo, ["git", "push", "origin", f"HEAD:{root_default}"])
                log += (
                    f"bumped {', '.join(bumped)} directly on {root_default}, "
                    "no root merge request\n"
                )
    except ForgeError as exc:
        log, status = f"{hook_point} failed: {exc}\n", "failed"

    return await _builtins.finish_session(
        db, log_path, result_path, session_id=session_id, status=status, log=log
    )
