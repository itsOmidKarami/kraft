"""Choosing a backend, and running one forge node against one or several
repos.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from kraft import builtins as _builtins
from kraft import events, store
from kraft.adapters import beads
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
from kraft.adapters.forge.models import MR, FailedJob, FakeForge, Forge, ForgeError

logger = logging.getLogger(__name__)

#: The empty metadata a node with no `on.mr.describe` artifact, or a run
#: before Task 4's node exists, falls back to -- one shared instance so this
#: stays a lint-clean default rather than a fresh call per signature.
_EMPTY_META = mr_ops.MRMeta()

#: Which internal handler each V1 `ForgeAction` runs. The vocabulary a chain
#: author writes (`target: mr.open_draft`) is the schema's; which code path it
#: reaches is this adapter's, so the mapping lives here rather than in the
#: executor -- `dispatch` hands over the typed target and nothing else.
#:
#: Two actions have no handler yet: `mr.automated_review` and
#: `mr.external_approval`, external waits the shared due scheduler owns
#: (Task 9). An unmapped target falls through to `_run_one`'s last arm, which
#: **stops the item for a human** rather than failing the node -- see there for
#: why.
V1_HANDLERS: dict[str, str] = {
    "mr.open_draft": "open_mr",
    "mr.sync": "sync_mr",
    "mr.ci": "ci_poll",
    "mr.mark_ready": "mark_ready",
    "mr.merge": "merge",
    "mr.post_merge_ci": "merge_watch",
}


#: Who implements each target that `V1_HANDLERS` does not map yet, named in the
#: stop reason so the human reading it knows this is Kraft's gap and not theirs.
_UNIMPLEMENTED_TARGETS = {
    "mr.automated_review": "Task 9 implements it as an external wait",
    "mr.external_approval": "Task 9 implements it as an external wait",
}


def handler_for(target: str) -> str:
    """The handler name for a V1 `ForgeAction` value, or the target itself when
    nothing maps it -- `_run_one` then reports it as unknown by the name the
    chain actually wrote."""
    return V1_HANDLERS.get(target, target)


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
#: `fake` is dev-only (Ruling 147): the in-process `FakeForge`, so `just dev`
#: reaches the merge-request half of a chain. It opens nothing anywhere, and
#: each call gets a fresh instance -- no MR survives from one node to the next.
_FORGE_CLI = {"gitlab": "glab", "github": "gh", "fake": "fake"}


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
            "(or repos.yaml); `forge: fake` is a dev-only in-process forge "
            "that opens nothing"
        )
    return cli


async def _rebase_conflict_away(
    db, forge: Forge, *, repo: Path, orig_repo: Path, branch: str, work_item_id: str
) -> tuple[str, str]:
    """Force-rebase onto the target's current default branch -- the one
    thing that can turn a real conflict into nothing left to fix, since a
    conflict against wherever main was when this branch was cut may not
    exist against main's current tip. Shared by `ci_poll` (Kraft-9h7v, the
    original) and `merge` (draft-MR workflow spec): same rebase, same
    "done, not conflict" report that lets `run_once`'s `rebase_bounce_to`
    machinery see the moved `base_ref` and bounce the chain back to
    `verify` in the same walk, before either node ever calls `forge.merge`.

    Returns ("<why it didn't take>\\n", "conflict") unchanged on a rebase
    that could not resolve it (a real conflict, `git rebase --abort`ed, or
    nothing to rebase) -- the caller appends this to its own log and keeps
    its own status. Returns ("<what changed>\\n", "done") when it worked,
    with the branch already rebased, `base_ref` persisted, and pushed.
    """
    default = await git.default_branch(orig_repo)
    try:
        new_head = await _builtins.mr_rebase_forced(repo, orig_repo, default)
    except RuntimeError as exc:
        return f"rebase onto {default} failed: {exc}\n", "conflict"
    if not new_head:
        return "", "conflict"
    await db.write(lambda c, h=new_head: store.set_base_ref(c, work_item_id, h))
    await forge.push(repo=repo, branch=branch)
    return f"rebased {branch} onto {new_head} and re-pushed\n", "done"


async def _run_one(
    forge: Forge,
    db,
    *,
    repo: Path,
    orig_repo: Path,
    branch: str,
    title: str,
    work_item_id: str,
    node_id: str,
    handler: str,
    hook_point: str,
    poll_timeout: float,
    poll_interval: float,
    merge_timeout: float,
    merge_interval: float,
    meta: mr_ops.MRMeta = _EMPTY_META,
    has_rebase_bounce: bool = False,
) -> tuple[str, str, list[dict] | None]:
    """One forge handler against one repo. Extracted from `run_task` so the
    multi-repo loop there can call it once per `work_item_repos` row; a
    single-repo item's behaviour is unchanged -- one call, same log and status
    strings as before this split.

    The third element is None for every handler but a code-red `ci_poll`: one
    finding per failed job, for the fix-loop plumbing `on.ci.poll` now feeds
    (Kraft-cbr §3).

    `meta` is resolved once by the caller against the item's own worktree,
    not `repo` -- a multi-repo item's submodule targets are not where the
    agent's `on.mr.describe` artifact lives.

    `has_rebase_bounce` is whether *this* node's own `chain_definition` entry
    carries `rebase_bounce_to` -- read from the frozen chain, not from
    whatever `templates/default.yaml` says today. The `merge` handler's
    conflict rebase relies on it: without a configured bounce nothing will
    ever re-verify the rebased head, so reporting "done" without calling
    `forge.merge` would leave the branch unmerged while the walk moves on
    regardless (code-review).
    """
    findings: list[dict] | None = None
    body = mr_ops.mr_body(work_item_id, branch, await git.commits_on(repo, branch), meta)
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
                # see, and the head has to be pushed. Retitling an existing
                # MR is not something either CLI does in this call, so the
                # reused path keeps today's title.
                await forge.push(repo=repo, branch=branch)
                await forge.update_mr(repo=repo, branch=branch, body=body)
                number, url = existing.number, existing.url
                log, status = f"reusing !{number}: {url}\n", "done"
            else:
                # ponytail: the description predates any commit `mr_checks`'
                # fix loop adds -- `sync_mr` rebuilds the body from this same
                # artifact, not from the newer head. Re-run on.mr.describe
                # from the fix loop if CI-fix commits start changing the
                # story.
                mr = await forge.open_mr(
                    repo=repo,
                    branch=branch,
                    title=mr_ops.mr_title_for(title, meta),
                    body=body,
                    meta=meta,
                )
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
            # An MR merged outside Kraft -- a human merging by hand, or an
            # auto-merge racing this node's own poll -- usually has its
            # source branch deleted with it (`force_remove_source_branch`),
            # and the push below then dies `! [rejected] ... (stale info)`
            # against a remote ref that no longer exists (Kraft-7itv): the
            # lease targets the sha origin last showed us, and origin has no
            # ref at all to compare it to. Same shortcut `sync_mr` and
            # `merge` already take for the identical race -- nothing here is
            # left to check once the forge itself says merged.
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "merged":
                return f"already merged (!{existing.number}); nothing to check\n", "done", findings
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
            head_sha = await git._head_sha(repo)
            # Pin to the pipeline the last poll of this same head already
            # saw, instead of re-resolving "latest on branch" every re-entry
            # (Kraft-ivh1). A rebase or a repair's push moves head_sha, which
            # invalidates the stored ref on its own -- no separate signal
            # for "something changed the branch" needed.
            stored = db.read(
                lambda c: c.execute(
                    "SELECT ci_pipeline_ref FROM work_items WHERE id = ?", (work_item_id,)
                ).fetchone()
            )
            stored_ref = stored["ci_pipeline_ref"] if stored else None
            pipeline_id = ""
            if stored_ref and ":" in stored_ref:
                stored_sha, stored_pipeline_id = stored_ref.split(":", 1)
                if stored_sha == head_sha:
                    pipeline_id = stored_pipeline_id
            ci_status = await forge.ci_status(
                repo=repo, mr=MR(number=0, url=""), branch=branch, pipeline_id=pipeline_id
            )
            if ci_status.pipeline_ref:
                await db.write(
                    lambda c, ref=f"{head_sha}:{ci_status.pipeline_ref}": store.set_ci_pipeline_ref(
                        c, work_item_id, ref
                    )
                )
            log, status = await ci.render_ci(
                ci_status, forge=forge, repo=repo, branch=branch, head_sha=head_sha
            )
            if status == "infra":
                # `forge.retry_jobs` only *starts* the job again; the pipeline
                # is not settled the instant it returns. See `ci.retry_infra_
                # once`'s docstring for why the retry budget is a persisted
                # counter, bumped once per `ci_poll` entry, rather than a loop
                # inside this one call.
                from kraft import policy as _policy
                from kraft.store import _now as _now

                count, started_at, cap = await db.write(
                    lambda c: store.bump_counter(
                        c,
                        work_item_id,
                        f"ci_infra:{node_id}",
                        _policy.Cap(
                            attempts=ci._INFRA_RETRY_CAP, wall_clock_s=ci._INFRA_WALL_CLOCK_S
                        ),
                    )
                )
                if (
                    _policy.check(count=count, started_at=started_at, cap=cap, now=_now())
                    == "breached"
                ):
                    await db.write(
                        lambda c, log=log: events.append(
                            c,
                            work_item_id,
                            "ci_infra_exhausted",
                            {"node_id": node_id, "reason": log},
                        )
                    )
                    # A plain literal, matching `executor.context.INFRA_STOP`
                    # ("infra_stop") without importing that package here --
                    # `kraft.executor` imports `kraft.adapters.forge` (through
                    # `dispatch.py`), so the reverse import would cycle.
                    status = "infra_stop"
                else:
                    log, status = await ci.retry_infra_once(
                        forge, repo=repo, branch=branch, head_sha=head_sha, first=ci_status
                    )
                    # `retry_infra_once`'s re-read is almost always "waiting"
                    # (a kick only starts the job, it does not settle it), but
                    # an instantly-red re-read is not impossible -- "infra" is
                    # not a sentinel `dispatch.measure_node` recognises, and
                    # would otherwise fall through neither "failed" nor "ok"
                    # and silently read as a clean node. The counter above
                    # already recorded this attempt; the next separate
                    # ci_poll entry re-checks it against the cap, so parking
                    # as "waiting" here costs nothing.
                    if status == "infra":
                        status = "waiting"
            if status == "conflict":
                rebase_log, status = await _rebase_conflict_away(
                    db,
                    forge,
                    repo=repo,
                    orig_repo=orig_repo,
                    branch=branch,
                    work_item_id=work_item_id,
                )
                log += rebase_log
            if status == "failed" and ci_status.failed_jobs:
                findings = [
                    {
                        "severity": "important",
                        "message": _job_finding_message(ci_status.jobs, j),
                        "file": _trace_file(log),
                        "line": _trace_line(log),
                        "source_plugin": "on.ci.poll",
                    }
                    for j in ci_status.failed_jobs
                ]
        case "sync_mr":
            # An MR merged outside Kraft -- a human merging by hand and
            # skipping `human_review` -- usually has its source branch
            # deleted with it, and then this node's push dies `! [rejected]
            # ... (stale info)`: the lease expects the sha origin last showed
            # us, origin has no ref at all, and the item stops one node from
            # the end with the work already on main (Kraft-7itv). Same
            # shortcut `merge` takes, for the same reason: pushing to and
            # re-describing a merged MR has nothing left to accomplish.
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "merged":
                return f"already merged (!{existing.number}); nothing to sync\n", "done", findings
            # Undraft before anything else: every MR opens as a draft now
            # (open_mr always does), and mr_sync is the one node every
            # chain shape runs before merge, gated or not -- see the
            # draft-MR workflow spec's "Undraft" decision. A no-op on an
            # MR that is already ready (both CLIs' own docs), so calling
            # it unconditionally costs nothing on a re-entry.
            await forge.mark_ready(
                repo=repo,
                branch=branch,
                mr=MR(number=existing.number if existing else 0, url=""),
            )
            # Push first: every commit after `open_mr` -- verify's fixes,
            # mr_checks' findings -- is local only until this runs, and
            # `merge` refuses a branch ahead of its remote (Kraft-nh5m).
            await forge.push(repo=repo, branch=branch)
            # The description `open_mr` wrote predates every commit verify
            # and mr_checks added, so it is rewritten from the branch head
            # before a human is asked to read it (Kraft-c09h).
            await forge.update_mr(repo=repo, branch=branch, body=body)
            log, status = "pushed and synced the merge request description\n", "done"
        case "mark_ready":
            # `mr.mark_ready` in V1: publication, split out of `sync_mr`'s
            # push-and-describe so a chain can put its final human gate between
            # the two. Not an external wait -- both `gh` and `glab` undraft in
            # one call -- so it needs no scheduler and no persisted condition.
            #
            # Idempotent on both CLIs (their own docs: marking a ready MR ready
            # is a no-op), which is what makes a re-entered walk safe here. An
            # MR already merged has nothing left to publish: same shortcut
            # `sync_mr` and `merge` take, for the same reason -- its source
            # branch is usually deleted with the merge, and the node's stated
            # end state is already true.
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "merged":
                return (
                    f"already merged (!{existing.number}); nothing to mark ready\n",
                    "done",
                    findings,
                )
            await forge.mark_ready(
                repo=repo,
                branch=branch,
                mr=MR(number=existing.number if existing else 0, url=""),
            )
            log, status = "marked the merge request ready for review\n", "done"
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
                rebased = False
                unresolved_conflict = gate_status == "conflict"
                if gate_status == "conflict":
                    # mr_sync's push, or a rebase since mr_checks last
                    # looked, can turn up a conflict only merge ever sees
                    # -- give it the same rebase-and-bounce mr_checks
                    # already has (draft-MR workflow spec) rather than
                    # failing a conflict a rebase might dissolve.
                    rebase_log, gate_status = await _rebase_conflict_away(
                        db,
                        forge,
                        repo=repo,
                        orig_repo=orig_repo,
                        branch=branch,
                        work_item_id=work_item_id,
                    )
                    ci_log += rebase_log
                    rebased = gate_status == "done"
                    unresolved_conflict = not rebased
                    if rebased and not has_rebase_bounce:
                        # No `rebase_bounce_to` on *this* node's own
                        # chain_definition -- an installed
                        # templates/default.yaml seeded before the field
                        # existed, or a chain_definition frozen before this
                        # node grew it. Nothing will bounce the walk back to
                        # verify to re-run tests over the rebased diff, so
                        # the "done, not merged" shortcut below would leave
                        # the branch unmerged while `post_merge_watch` and
                        # `mark_completed`/`close_beads` still run as if it
                        # had landed (code-review). Re-check CI on the
                        # rebased head instead -- the one guarantee this
                        # node can still make on its own -- and fall through
                        # to the ordinary gate below to merge only if it's
                        # actually green.
                        recheck_log, gate_status = await ci.wait_for_ci(
                            forge,
                            repo=repo,
                            branch=branch,
                            timeout=poll_timeout,
                            interval=poll_interval,
                        )
                        ci_log += recheck_log
                        rebased = False
                        unresolved_conflict = gate_status == "conflict"
                if rebased:
                    # Rebased the conflict away, not merged: this node's
                    # `rebase_bounce_to: verify` (templates/default.yaml)
                    # is what acts on the moved base_ref next, re-running
                    # tests over the rebased diff before anything is ever
                    # merged -- the same guarantee a rebase at mr_checks
                    # already gets. "rebased", not "done" -- `run_task`'s
                    # multi-repo loop must not count this target as merged
                    # (code-review); it normalizes back to "done" itself
                    # once it knows no later target got skipped over a
                    # rebase that only looked like a landing.
                    log, status = ci_log, "rebased"
                elif unresolved_conflict:
                    # Thread the real status through rather than collapsing
                    # to a bare "failed" (draft-MR workflow spec): a real
                    # conflict a rebase couldn't dissolve either, same
                    # status `ci_poll` already reports for the identical
                    # case -- both read the same by `dispatch`'s failure
                    # handling, but "conflict" names the actual problem.
                    log, status = ci_log, "conflict"
                elif gate_status != "done":
                    log, status = ci_log, "failed"
                else:
                    # An approval rule the pipeline can't see for itself:
                    # `mergeable` reads this as undecided (mr_checks must
                    # not fail on it pre-gate, since it's an ordinary state
                    # before human_review even runs), so it survives
                    # unnoticed all the way here. One more read, now that
                    # CI is confirmed green and conflict-free, catches it
                    # before forge.merge() does, with a message a human
                    # reads at a glance instead of the CLI's own refusal
                    # text (draft-MR workflow spec).
                    fresh = await forge.ci_status(repo=repo, mr=MR(number=0, url=""), branch=branch)
                    if fresh.block_reason == "not_approved":
                        log = (
                            "merge request needs approval before it can land: "
                            f"{fresh.merge_detail}\n"
                        )
                        status = "failed"
                        return log, status, findings
                    # A genuine refusal -- unmet approval rules this read
                    # didn't catch, or anything else -- still raises inside
                    # forge.merge and still fails the node.
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
        case "merge_watch":
            # Local imports, same as `ci_poll`'s own "infra" branch a few
            # cases up -- both counters this case bumps need them.
            from kraft import policy as _policy
            from kraft.store import _now as _now

            default = await git.default_branch(orig_repo)
            head_sha = await _builtins.upstream_head(orig_repo)
            if head_sha is None:
                # Peer callers (`ensure_worktree`, `refresh_worktree_base`) treat a
                # failed `rev-parse HEAD` as "nothing to report yet", not a value to
                # hand onward -- `git_read`'s 10s timeout or a transient fetch failure
                # both land here. Passing None through would crash
                # `GhCli.branch_ci_status` (`head_sha[:7]`) or, on GitLab, silently pin
                # the previous commit's pipeline to this merge's head (code-review).
                log, status = f"rev-parse HEAD failed in {orig_repo}, will retry\n", "waiting"
            else:
                stored = db.read(
                    lambda c: c.execute(
                        "SELECT ci_pipeline_ref FROM work_items WHERE id = ?", (work_item_id,)
                    ).fetchone()
                )
                stored_ref = stored["ci_pipeline_ref"] if stored else None
                pipeline_id = ""
                if stored_ref and ":" in stored_ref:
                    stored_sha, stored_pipeline_id = stored_ref.split(":", 1)
                    if stored_sha == head_sha:
                        pipeline_id = stored_pipeline_id
                # `branch_ci_status`, not `ci_status`: the checked-out branch's own
                # MR is already merged, so `ci_status`'s `gh pr view`/`glab mr
                # view` has nothing left to resolve and would raise instead of
                # reading the target branch's pipeline at all (code-review). It
                # also takes `head_sha` explicitly -- the freshly-fetched upstream
                # head above, not whatever this checkout's local HEAD happens to
                # be if it has not been pulled (code-review, second finding).
                ci_status = await forge.branch_ci_status(
                    repo=orig_repo, branch=default, head_sha=head_sha, pipeline_id=pipeline_id
                )
                # Only pin a read that is actually *for* head_sha (plan-review
                # finding 2). Right after a merge, the target branch usually has
                # no pipeline for the new head yet, so this first read is almost
                # always the *previous* commit's settled pipeline -- pinning it
                # unconditionally would map head_sha to that wrong, already-done
                # id, and every re-entry would poll it forever instead of ever
                # finding the real one. `""` covers a backend that reports no sha
                # at all -- same as an unset pipeline_ref, nothing to pin either
                # way.
                if ci_status.pipeline_ref and ci_status.sha in ("", head_sha):
                    await db.write(
                        lambda c, ref=f"{head_sha}:{ci_status.pipeline_ref}": (
                            store.set_ci_pipeline_ref(c, work_item_id, ref)
                        )
                    )
                log, status = await ci.render_ci(
                    ci_status, forge=forge, repo=orig_repo, branch=default, head_sha=head_sha
                )
                if status == "infra":
                    # Same counter, same key format, as `ci_poll`'s own
                    # `ci_infra:<node_id>` (plan-review finding 3, first half):
                    # `retry_infra_once` only starts the job again, it does not
                    # settle it, so an unbounded number of *separate* `ci_wait`
                    # re-entries could each see "infra" again and kick
                    # `retry_jobs` again forever against a persistently broken
                    # runner. `node_id` already namespaces this from `mr_checks`'
                    # own counter row, so this needs no new policy.yaml entry.
                    count, started_at, cap = await db.write(
                        lambda c: store.bump_counter(
                            c,
                            work_item_id,
                            f"ci_infra:{node_id}",
                            _policy.Cap(
                                attempts=ci._INFRA_RETRY_CAP, wall_clock_s=ci._INFRA_WALL_CLOCK_S
                            ),
                        )
                    )
                    if (
                        _policy.check(count=count, started_at=started_at, cap=cap, now=_now())
                        == "breached"
                    ):
                        await db.write(
                            lambda c, log=log: events.append(
                                c,
                                work_item_id,
                                "ci_infra_exhausted",
                                {"node_id": node_id, "reason": log},
                            )
                        )
                        # `walk.py`'s node loop already turns this into
                        # `stops.stop_for_infra` -> `needs_human` for any node
                        # (`walk.py:245,340,394`) -- no new plumbing needed here.
                        return log, "infra_stop", findings
                    log, status = await ci.retry_infra_once(
                        forge,
                        repo=orig_repo,
                        branch=default,
                        head_sha=head_sha,
                        first=ci_status,
                        # Same reason the first read above uses
                        # `branch_ci_status`: this item's merge request is merged
                        # and gone, so the re-read after the kick must not try to
                        # resolve one either (judge finding, rounds 0/2/3).
                        branch_only=True,
                    )
                if status == "waiting":
                    # A separate, tighter budget for a pipeline that never
                    # settles at all (plan-review finding 3, second half):
                    # `ci_wait.py`'s poller applies one shared cap
                    # (`policy.yaml`'s `ci_wait`, 1800s/60 attempts) to *every*
                    # node parked in "waiting", with no notion of which handler
                    # is behind it. Left alone, a target-branch pipeline slower
                    # than that would turn into `needs_human` after this item's
                    # own work is already merged, and -- since `post_merge_watch`
                    # is now this chain's terminal node -- its tracking bead
                    # would never close either. `_POST_MERGE_WAIT_CAP`/
                    # `_POST_MERGE_WAIT_WALL_CLOCK_S` are plain module constants
                    # in `ci.py`, not a policy.yaml key, same as the infra cap
                    # above -- `_run_one` has no `policy` object to resolve one
                    # from -- and deliberately smaller than `ci_wait`'s shared
                    # default so this always resolves first.
                    count, started_at, cap = await db.write(
                        lambda c: store.bump_counter(
                            c,
                            work_item_id,
                            f"post_merge_wait:{node_id}",
                            _policy.Cap(
                                attempts=ci._POST_MERGE_WAIT_CAP,
                                wall_clock_s=ci._POST_MERGE_WAIT_WALL_CLOCK_S,
                            ),
                        )
                    )
                    if (
                        _policy.check(count=count, started_at=started_at, cap=cap, now=_now())
                        == "breached"
                    ):
                        # Not a human page: the merge already succeeded and
                        # nothing here is this item's own defect. A pipeline
                        # that never finishes reporting anything is a fact about
                        # the target branch's CI, not about this item -- log it
                        # and let the chain complete rather than block on it.
                        log += (
                            f"gave up watching {default}'s pipeline after "
                            f"{count - 1} re-entry(ies): it never settled\n"
                        )
                        status = "done"
                if status in ("infra", "failed", "conflict"):
                    # Settled red after at most one infra kick: nothing here is
                    # this item's own code to fix loop over (Kraft-43kw). File
                    # what a human needs, warn whoever else is sitting on the
                    # break, and still report "done" -- this node's job is
                    # reporting what happened to the branch, not fixing it.
                    job = ci_status.failed_jobs[0] if ci_status.failed_jobs else None
                    trace = (
                        "\n\n".join(
                            _job_finding_message(ci_status.jobs, j) for j in ci_status.failed_jobs
                        )
                        or log
                    )
                    title = (
                        f"post-merge pipeline broke on {default}: "
                        f"{job.name if job else 'unknown job'}"
                    )[: beads.MAX_TITLE]
                    info = db.read(
                        lambda c: c.execute(
                            "SELECT bead_cwd FROM work_items WHERE id = ?", (work_item_id,)
                        ).fetchone()
                    )
                    # `bead_cwd` wins, `orig_repo` (already in scope -- it is
                    # `row["repo"]`, the same column, read once at `run_task`'s
                    # call site) otherwise: the item's own recorded workspace
                    # always wins, same precedence `entry.close_beads` uses
                    # (plan-review finding 4) -- see this task's design note
                    # above for why the fallback value itself differs from
                    # `close_beads`'s `bd_cwd`.
                    cwd = (info["bead_cwd"] if info else None) or str(orig_repo)
                    follow_up: str | None = None
                    try:
                        follow_up = await beads.intake(title, description=trace, cwd=cwd)
                    except Exception as exc:  # noqa: BLE001 -- a bd failure must not block "done"
                        logger.warning(
                            "merge_watch: follow-up bead not filed for %r: %r", title, exc
                        )
                    broken = db.read(
                        lambda c: [
                            r["id"]
                            for r in c.execute(
                                # `waiting` as well as `active` (gate review):
                                # an item parked on its own pipeline sits on
                                # this same broken commit and is the one most
                                # likely to re-discover the break in its own
                                # fix loop -- exactly what this warning
                                # exists to prevent. No other status can
                                # usefully be warned: `paused`/`needs_human`
                                # are already stopped, and a completed item
                                # has nothing left to rebase.
                                "SELECT id FROM work_items WHERE status IN ('active', 'waiting') "
                                "AND base_ref = ? AND id != ?",
                                (head_sha, work_item_id),
                            ).fetchall()
                        ]
                    )
                    for other_id in broken:
                        await db.write(
                            lambda c, oid=other_id: store.pause_for_broken_base(
                                c, oid, broken_by=work_item_id, follow_up_bead=follow_up
                            )
                        )
                    log += f"filed {follow_up or '(no bead filed)'}, paused {len(broken)} item(s)\n"
                    status = "done"
        case _:
            # A declared-but-unimplemented action is a *configuration limit*,
            # not a bug in this run: the seeded V1 chain names all three of the
            # unmapped targets (see `V1_HANDLERS`), so `failed` told an operator
            # their pipeline had broken when in fact Kraft had not built that
            # step yet -- and burned the node's fix loop finding out. Same
            # posture as `unavailable-selected-harness-needs-human`: name the
            # target, name the task that implements it, and stop for a person
            # (Ruling 48). `config_error` is terminal at every tier
            # (`executor.context.SCOPE`), so `walk_node` turns it straight into
            # `needs_human` with this reason instead of a retry.
            owner = _UNIMPLEMENTED_TARGETS.get(handler, "a later task")
            log = (
                f"forge target {handler!r} is declared by this chain but Kraft does not "
                f"implement it yet ({owner}). Nothing is wrong with the merge request; "
                f"this node cannot run until that lands.\n"
            )
            status = "config_error"
    return log, status, findings


def _job_finding_message(jobs: tuple[str, ...], job: FailedJob) -> str:
    """The finding message for one failed job: its own trace tail when the
    backend attached one to `ci_status.jobs` (glab's `_failure_detail`
    interleaves each failed job's header line -- `"job {name}: failed
    (...)"` -- with that job's own indented trace lines, in job order), the
    bare name-and-reason otherwise (gh's one-liner `"{name}: {conclusion}"`
    has no trace to give).

    Carrying whatever per-failure content is available, rather than just the
    job name, is what lets two successive code-reds on the same job carry
    different `findings.Finding.fingerprint`s when the underlying failure
    actually changed, and the identical fingerprint when it did not --
    fingerprint hashes the normalised message (`findings.py`), so the message
    is the only thing that can tell those two cases apart.
    """
    prefix = f"job {job.name}:"
    block: list[str] = []
    capturing = False
    for line in jobs:
        if line.startswith(prefix):
            capturing = True
            block.append(line)
            continue
        if capturing:
            if line.startswith("  "):
                block.append(line)
                continue
            break
    if block:
        return "\n".join(block)
    return f"job {job.name} failed" + (f": {job.failure_reason}" if job.failure_reason else "")


_TRACE_LOC = re.compile(r"([^\s:]+\.\w+):(\d+)")


def _trace_file(log: str) -> str | None:
    m = _TRACE_LOC.search(log)
    return m.group(1) if m else None


def _trace_line(log: str) -> int | None:
    m = _TRACE_LOC.search(log)
    return int(m.group(2)) if m else None


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
    #: The original repo, not the worktree -- `ci_poll`'s confirmed-conflict
    #: rebase (Kraft-9h7v) needs origin's current default branch tip, which
    #: `refresh_worktree_base` fetches from here. None (every test call site
    #: that predates this, and any future one that never exercises the
    #: conflict path) falls back to `repo` -- harmless, since that path is
    #: the only reader.
    orig_repo: Path | None = None,
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
    #: Whether this node's own `chain_definition` entry carries
    #: `rebase_bounce_to` -- see `_run_one`'s docstring. Defaults to False,
    #: the safe assumption for any caller (a test, a future handler) that
    #: does not know any better.
    has_rebase_bounce: bool = False,
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
    actual_session_id, log_path, result_path = await _builtins.start_session(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        round=round,
        head_sha=head_sha,
        # A pipeline still pending is the same wait episode, not a new
        # attempt (Kraft-ivh1) -- every other handler keeps minting a fresh
        # row every dispatch.
        reuse_if_waiting=(handler in ("ci_poll", "merge_watch")),
    )
    reused = actual_session_id != session_id
    session_id = actual_session_id
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

    if handler == "merge_watch" and targets:
        # `merge_watch` ignores the per-target `repo` entirely -- it reads
        # `orig_repo`'s own default branch. One pass, not one per target: a
        # multi-repo item would otherwise poll the same branch N times and
        # file N identical follow-up beads (gate review). Root by preference,
        # first target when `root_policy == "skip"` already dropped it -- the
        # choice is cosmetic (it only decides `_run_one`'s unused `repo`
        # argument), but it keeps the log line naming the repo a reader
        # expects. After the `multi` block above on purpose: that block still
        # needs the root row to resolve `root_has_changes`.
        targets = [next((t for t in targets if t[2] == "root"), targets[0])]

    log, status = "", "done"
    # Findings only ever reach `finish_session` for a single-target run: a
    # submodule's own CI is not this item's `mr_checks` node, and findings
    # from it would double-count against the wrong job (Kraft-cbr §3).
    findings: list[dict] | None = None
    try:
        # Inside the try: `backend_for` can raise, and an exception escaping
        # here would skip `finish_session` and strand the session row started
        # above (Kraft-41b, Kraft-7xt are the same wound from the other side).
        live_forge = resolve(backend_for(backend, repo_forge))
        # Resolved once against the item's own worktree (`repo`, not whichever
        # target the loop below is on) -- a multi-repo item's submodule paths
        # are not where the agent's `on.mr.describe` artifact lives.
        meta = mr_ops.read_mr_meta(repo, work_item_id)
        for row_id, target_repo, role in targets:
            if handler == "open_mr" and role == "root" and multi:
                await git._assert_submodules_covered(target_repo, {t for _, t, _ in targets})
            one_log, one_status, one_findings = await _run_one(
                live_forge,
                db,
                repo=target_repo,
                orig_repo=orig_repo or target_repo,
                branch=branch,
                title=title,
                work_item_id=work_item_id,
                node_id=node_id,
                handler=handler,
                hook_point=hook_point,
                poll_timeout=poll_timeout,
                poll_interval=poll_interval,
                merge_timeout=merge_timeout,
                merge_interval=merge_interval,
                meta=meta,
                has_rebase_bounce=has_rebase_bounce,
            )
            if not multi:
                findings = one_findings
            log += (f"[{target_repo.name}] " if multi else "") + one_log
            if row_id is not None:
                new_state = {
                    "open_mr": "open",
                    # "rebased" (conflict rebased away, not actually merged --
                    # see `_run_one`) maps to `None`: this row stays whatever
                    # it already was (`open`), not "merged" -- the bounce
                    # this triggers below re-enters `merge` from scratch once
                    # verify has re-run, and `_run_one`'s own "already
                    # merged" check is what makes that re-entry idempotent.
                    "merge": (
                        "merged"
                        if one_status == "done"
                        else ("failed" if one_status != "rebased" else None)
                    ),
                }.get(handler)
                if new_state:
                    await db.write(
                        lambda c, i=row_id, s=new_state: store.update_repo_state(
                            c, i, merge_state=s
                        )
                    )
            if one_status != "done":
                # Stop here rather than merging later targets (root last) or
                # running the root pointer-bump below: a "rebased" result
                # means this target never actually landed, and root must not
                # bump a submodule pointer past a branch nothing merged yet
                # (code-review). Normalized back to "done" below once the
                # loop is known to have stopped for this reason alone.
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
        if status == "rebased":
            # Internal-only marker (worker_sessions.status has no "rebased"
            # value, and the walk's own bounce -- keyed off base_ref moving,
            # not this string -- already treats a plain "done" as the signal
            # to re-verify). Everything above that must not treat this target
            # as landed has already run off the un-normalized value.
            status = "done"
    except ForgeError as exc:
        log, status, findings = f"{hook_point} failed: {exc}\n", "failed", None

    return await _builtins.finish_session(
        db,
        log_path,
        result_path,
        session_id=session_id,
        status=status,
        log=log,
        findings=findings,
        reused=reused,
    )
