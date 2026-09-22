"""Reading a pipeline once: `render_ci`'s verdict on one read, what counts as
infrastructure-red, and the one self-retry of an infra-red pipeline. Waiting
for it is `kraft.waits`' -- nothing here sleeps.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from kraft.adapters.forge.models import MR, CIStatus, Forge


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

    A pending read standing only on a run cancelled `_CANCEL_GRACE` ago or
    more is `"abandoned"` (Kraft-kbqmk): a successor to a relabel or a
    push-superseded run registers within seconds, so one that has not in
    that long is not coming, and a person decides now rather than at the
    wait's full timeout. A fresher cancel stays a wait (Kraft-zn8me).
    """
    log = f"pipeline {ci.state}: {ci.url}\n" + "".join(f"  {j}\n" for j in ci.jobs)
    if ci.sha and head_sha and ci.sha != head_sha:
        return log, "waiting"
    if ci.state == "pending":
        if _abandoned(ci.cancelled_at):
            log += (
                f"the run for {(head_sha or ci.sha)[:7] or 'this head'} was cancelled at "
                f"{ci.cancelled_at} and no new run has started since; nothing about the code "
                "failed. re-run CI, then retry this item\n"
            )
            return log, "abandoned"
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


#: How long a cancelled run may stand as the latest for its head before it
#: counts as one nobody followed up. Minutes, not seconds: a successor
#: registers within seconds, and the forge's clock is not this machine's.
_CANCEL_GRACE = timedelta(minutes=5)


def _abandoned(cancelled_at: str) -> bool:
    """Whether a cancel is `_CANCEL_GRACE` old. A time that will not parse is
    not old: it waits, and the wait's own timeout still stops it."""
    try:
        at = datetime.fromisoformat(cancelled_at)
    except ValueError:
        return False
    if at.tzinfo is None:
        return False
    return datetime.now(UTC) - at >= _CANCEL_GRACE


#: Kraft-h81i, Kraft-s8ul, Kraft-ddxn: a failed job whose own `failure_reason`
#: names one of these is the forge's own infrastructure, not the branch's
#: code. `script_failure`, an unrecognised reason, or a red pipeline that
#: named no reason at all, are code-red — the failure a fix loop is for.
#:
#: No `"cancelled"`: a cancelled run never reaches here. Both backends read it
#: as pending (Kraft-zn8me), because a cancelled run is never a verdict, and a
#: run a person cancelled with no successor is `render_ci`'s "abandoned".
#: It is never auto-retried here, which would override that person's decision.
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
    "budget" to zero on every fresh call -- once per scheduler re-entry,
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
    log, verdict = await render_ci(
        ci_status, forge=forge, repo=repo, branch=branch, head_sha=head_sha
    )
    # This kick just re-started the run, so an old cancel is not one nobody
    # followed up, and "abandoned" is no verdict any caller of this handles.
    return log, "waiting" if verdict == "abandoned" else verdict
