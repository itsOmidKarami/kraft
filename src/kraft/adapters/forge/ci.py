"""Waiting for a pipeline to settle, and reading a merge request back until it
lands.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

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


def render_ci(ci: CIStatus) -> tuple[str, str]:
    """Render the log line and verdict for one CI check. Shared by `ci_poll`'s
    single check and `wait_for_ci`'s settled result, so the two callers
    cannot drift in how they describe the same pipeline (Kraft-ru98).

    Returns the log text and "done"/"failed"/"waiting" -- "waiting" only for a
    pipeline that is still pending and mergeable, which `wait_for_ci` never
    passes through here (it resolves pending itself, into a timeout).
    """
    log = f"pipeline {ci.state}: {ci.url}\n" + "".join(f"  {j}\n" for j in ci.jobs)
    if ci.mergeable is False:
        # Green *and* unmergeable is the exact shape of the bug: the pipeline
        # passes, the gate passes, and the merge node meets the conflict
        # (Kraft-ejj9). `is False` and not falsiness: None is undecided, and
        # undecided is this node's normal.
        log += f"merge request is not mergeable: {ci.merge_detail or 'unknown'}\n"
        return log, "failed"
    if ci.state == "pending":
        return log, "waiting"
    return log, "done" if ci.state == "success" else "failed"


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
    return render_ci(ci)


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
