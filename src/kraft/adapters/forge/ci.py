"""Waiting for a pipeline to settle, and reading a merge request back until it
lands.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from kraft.adapters.forge import git
from kraft.adapters.forge.models import MR, CIStatus, Forge, ForgeError, MRRef

logger = logging.getLogger(__name__)

#: How long a `ci_poll` node waits for a pipeline to settle, and how long it
#: sleeps between checks. Both are overridable per node in the registry; a
#: pipeline slower than this needs a human whatever the number is.
DEFAULT_POLL_TIMEOUT = 1800.0
DEFAULT_POLL_INTERVAL = 5.0
_MAX_POLL_INTERVAL = 60.0

#: How long the merge node waits for the forge to report the merge request
#: actually merged, and how long it sleeps between reads. Five minutes rather
#: than DEFAULT_POLL_TIMEOUT's thirty: by the time this wait starts, `merge`
#: has just re-validated CI itself (`wait_for_ci`, same wait `ci_poll` uses),
#: so a merge that has not landed by now is waiting on something a person has
#: to see, not on a pipeline `mr_sync`'s push may have re-armed (Kraft-266b,
#: Kraft-x10m). Deliberately not registry-tunable — nothing has asked, and
#: these are one edit away if something does.
MERGE_VERIFY_TIMEOUT = 300.0
MERGE_VERIFY_INTERVAL = 5.0


#: Kraft-x92: one flaky/rate-limited `ci_status` call used to fail the whole
#: wait -- an error at minute 12 of a 30-minute poll read the same as a red
#: pipeline. Tolerate a short run of consecutive errors before giving up;
#: reset the count the moment a call succeeds, so this bounds a burst, not
#: the wait's total error budget.
_MAX_CONSECUTIVE_POLL_ERRORS = 3


async def poll_ci(
    forge: Forge, *, repo: Path, branch: str, timeout: float, interval: float
) -> tuple[CIStatus, bool]:
    """Wait for a pipeline to settle.

    Returns the last status seen and whether the wait ran out with it still
    pending. Backoff rather than a fixed interval: a thirty-minute pipeline
    should not cost three hundred CLI invocations, and the early checks are the
    ones worth making promptly.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    # The cap bounds how far the backoff *grows*, not what the caller asked
    # for: a registry that sets a 300s interval for a rate-limited forge must
    # not silently get 60s and five times the CLI calls.
    cap = max(_MAX_POLL_INTERVAL, interval)
    consecutive_errors = 0
    while True:
        try:
            ci = await forge.ci_status(repo=repo, mr=MR(number=0, url=""), branch=branch)
        except ForgeError:
            consecutive_errors += 1
            if consecutive_errors > _MAX_CONSECUTIVE_POLL_ERRORS:
                raise
            logger.warning(
                "ci_status failed mid-poll (%d/%d consecutive), retrying",
                consecutive_errors,
                _MAX_CONSECUTIVE_POLL_ERRORS,
            )
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise
            await asyncio.sleep(min(interval, remaining))
            interval = min(interval * 2, cap)
            continue
        consecutive_errors = 0
        # A branch that cannot merge is an answer, not a wait: a conflict will
        # not resolve itself in thirty minutes (Kraft-ejj9).
        if ci.state != "pending" or ci.mergeable is False:
            return ci, False
        remaining = deadline - loop.time()
        if remaining <= 0:
            return ci, True
        await asyncio.sleep(min(interval, remaining))
        interval = min(interval * 2, cap)


async def render_ci(
    ci: CIStatus,
    *,
    forge: Forge,
    repo: Path,
    branch: str,
    head_sha: str | None,
    _retried: bool = False,
) -> tuple[str, str]:
    """Render the log line and verdict for one CI read.

    Order matters and is the fix (Kraft-bjjm): a pipeline not for this head,
    or still pending, is a wait — never a failure — however the merge status
    reads, because GitLab recomputes merge status asynchronously after a push
    and a head pushed seconds ago is routinely reported unmergeable for a
    moment. Only a *settled* green pipeline's unmergeable reading is trusted,
    and only after a second read confirms it (Kraft-ejj9 stays true, just
    later). A settled red pipeline is `"infra"` when every failed job is the
    forge's own fault, `"failed"` (code-red) otherwise, and green+confirmed-
    unmergeable is `"conflict"`.
    """
    log = f"pipeline {ci.state}: {ci.url}\n" + "".join(f"  {j}\n" for j in ci.jobs)
    if ci.sha and head_sha and ci.sha != head_sha:
        return log, "waiting"
    if ci.state == "pending":
        return log, "waiting"
    if ci.state == "success" and ci.mergeable is False:
        if _retried:
            log += f"merge request is not mergeable: {ci.merge_detail or 'unknown'}\n"
            return log, "conflict"
        fresh = await forge.ci_status(repo=repo, mr=MR(number=0, url=""), branch=branch)
        return await render_ci(
            fresh, forge=forge, repo=repo, branch=branch, head_sha=head_sha, _retried=True
        )
    if ci.state == "success":
        return log, "done"
    if is_infra_red(ci):
        return log, "infra"
    return log, "failed"


#: Kraft-h81i, Kraft-s8ul, Kraft-ddxn: a failed job whose own `failure_reason`
#: names one of these is the forge's own infrastructure, not the branch's
#: code. `script_failure`, an unrecognised reason, or a red pipeline that
#: named no reason at all, are code-red — the failure a fix loop is for.
#:
#: Deliberately NOT `"cancelled"`. By the time a job's `failure_reason` is read
#: here, `render_ci`'s sha guard has already confirmed this pipeline belongs to
#: the current head, so nothing newer could have superseded it through
#: GitLab's redundant-pipeline auto-cancel — the only thing left that could
#: have cancelled *this* pipeline is a human clicking Cancel. Auto-retrying
#: that through the forge would silently override a person's own decision,
#: which is worse than spending one code-red fix cycle that finds nothing to
#: change and reports back quickly with the cancellation named in the finding.
_INFRA_REASONS = frozenset(
    {
        "runner_system_failure",
        "stuck_or_timeout_failure",
        "job_execution_timeout",
        "scheduler_failure",
    }
)


def is_infra_red(ci: CIStatus) -> bool:
    """A settled red pipeline with no jobs at all (a config error stopped
    anything from running) is infra-shaped by definition — there is no job to
    blame, so there is nothing for a fix loop to fix.

    `not ci.failed_jobs` must mean *the forge confirmed* zero jobs, never "we
    could not find out" — that ambiguity is exactly Kraft-h81i's real
    regression (caught on review of this plan): a `glab ci get` call that
    itself fails (`ForgeError`, unparseable JSON) has nothing to report either,
    and treating that the same as a genuine zero-job pipeline reads a
    perfectly ordinary `script_failure` this fix loop exists to catch as
    infra instead, self-retrying twice and stopping at `needs_human` with no
    fix loop ever run. `glab.py`'s `_failure_detail` is the only caller that
    can produce an empty `failed_jobs` tuple, and it is written so the
    unreadable case never does — it returns a placeholder job with
    `failure_reason=None` instead, which is not a member of `_INFRA_REASONS`,
    so the `all(...)` below reads it as code-red by default. Only a
    *successful* read that confirms no job ran returns the real empty tuple
    this function treats as infra.
    """
    if not ci.failed_jobs:
        return True
    return all(j.failure_reason in _INFRA_REASONS for j in ci.failed_jobs)


#: A runner failure or a config error resolves on its own or it does not --
#: two retries, not thirty minutes of them (Kraft-h81i). Widen this if a real
#: install's infra is flakier than that. The count that matters is run.py's
#: persisted `ci_infra:<node_id>` counter, not a variable in this module --
#: see `retry_infra_once`'s docstring for why.
_INFRA_RETRY_CAP = 2
#: Generous on purpose: this counter is bookkeeping across `ci_poll` entries,
#: not a user-facing loop with its own `policy.yaml` entry, so its wall-clock
#: side of the cap should essentially never be what trips it -- the attempts
#: side always will be, first.
_INFRA_WALL_CLOCK_S = 3600

#: Kraft-43kw: merge_watch's own budget for a target-branch pipeline that
#: never settles at all -- plain module constants, not a policy.yaml key,
#: same as the pair above (`_run_one` has no `policy` object to resolve one
#: from). Deliberately smaller than `ci_wait.py`'s shared `ci_wait` cap
#: (1800s/60 attempts) so this always resolves first: the merged work is
#: already on the target branch by the time this node runs, so giving up on
#: watching and finishing is a smaller consequence than paging a human over
#: a pipeline this item cannot fix either way.
_POST_MERGE_WAIT_CAP = 40
_POST_MERGE_WAIT_WALL_CLOCK_S = 900


async def retry_infra_once(
    forge: Forge,
    *,
    repo: Path,
    branch: str,
    head_sha: str | None,
    first: CIStatus,
    branch_only: bool = False,
) -> tuple[str, str]:
    """Kick one retry of a settled infra-red pipeline through the forge, then
    read it back once and hand back whatever verdict that read gives.

    Deliberately not a loop over `_INFRA_RETRY_CAP`: `forge.retry_jobs` only
    *starts* the job again -- the pipeline is not settled the instant it
    returns, so a synchronous re-read immediately after almost always still
    sees `"pending"` (verdict `"waiting"`). A loop with an immediate re-read
    (this function's previous shape) therefore only ever spent one kick
    before returning `"waiting"`, whatever its cap said, and reset that
    "budget" to zero on every fresh call -- once per `ci_wait` re-entry,
    forever. Real settlement takes real CI minutes, which only elapse
    *between* separate calls to this node, so the retry budget is counted by
    `run.py`'s persisted `ci_infra:<node_id>` counter instead, bumped once per
    `ci_poll` entry that is still infra-red -- this function just does the one
    kick-and-read that counter's caller decided was still within budget.

    `branch_only` picks which read the kick is followed by, and exists because
    `merge_watch` calls this after its own merge request is gone: the default
    `ci_status` resolves an MR first (`gh pr view` / `glab mr view`), which on
    a just-merged branch has nothing left to resolve and raises `ForgeError`,
    failing the terminal node -- the same MR-vs-branch split the caller's
    first read already makes with `branch_ci_status`. Both real backends
    raise there; `FakeForge` does not, so the double that holds this down
    (`tests/adapters/forge/test_run.py`) raises on the MR path on purpose.
    """
    await forge.retry_jobs(repo=repo, ci=first)
    ci_status = (
        await forge.branch_ci_status(repo=repo, branch=branch, head_sha=head_sha or "")
        if branch_only
        else await forge.ci_status(repo=repo, mr=MR(number=0, url=""), branch=branch)
    )
    return await render_ci(ci_status, forge=forge, repo=repo, branch=branch, head_sha=head_sha)


async def wait_for_ci(
    forge: Forge, *, repo: Path, branch: str, timeout: float, interval: float
) -> tuple[str, str]:
    """Wait for CI to settle, and render the log line and the mergeable/red
    verdict `merge`'s own CI gate reports (Kraft-266b, Kraft-x10m):
    `mr_sync`'s push after `human_review` can re-arm a project's
    required-pipeline rule for a head `mr_checks` never watched, so `merge`
    re-runs this exact wait, with the same timeout/interval, right before it
    calls `forge.merge()` — not a second, differently-tuned approximation of
    it. `ci_poll` no longer calls this (Kraft-ru98): it hands an unsettled
    pipeline back to the scheduler instead of blocking here.

    Returns the log text and "done"/"failed", the same vocabulary a node's
    status already uses. A timeout here still maps to "failed" -- `merge`'s
    caller has no scheduler to hand a wait back to.
    """
    ci, timed_out = await poll_ci(
        forge, repo=repo, branch=branch, timeout=timeout, interval=interval
    )
    if timed_out:
        # A timeout and a red pipeline are both a failed node, but a reviewer
        # -- and any fix loop built on this node (Kraft-cbr) -- has to tell
        # "finished red" from "never finished".
        log = f"pipeline timed out after {timeout:g}s, still pending: {ci.url}\n" + "".join(
            f"  {j}\n" for j in ci.jobs
        )
        return log, "failed"
    head_sha = await git._head_sha(repo)
    return await render_ci(ci, forge=forge, repo=repo, branch=branch, head_sha=head_sha)


async def poll_merged(
    forge: Forge, *, repo: Path, branch: str, timeout: float, interval: float
) -> MRRef | None:
    """Read the merge request back until it stops being open.

    `glab mr merge --yes` exits 0 both for "merged" and for "merge when all
    merge checks pass", and the second one merges nothing (Kraft-79x3, MR !76);
    `gh pr merge` has the same shape. The exit code is not the answer — the
    merge request's own state is. Returns the last `MRRef` read, or None if the
    branch has no merge request at all any more; the caller decides from the
    state. Same backoff as `poll_ci`, for the same reason.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    cap = max(_MAX_POLL_INTERVAL, interval)
    while True:
        ref = await forge.find_mr(repo=repo, branch=branch)
        if ref is None or ref.state != "open":
            return ref
        remaining = deadline - loop.time()
        if remaining <= 0:
            return ref
        await asyncio.sleep(min(interval, remaining))
        interval = min(interval * 2, cap)
