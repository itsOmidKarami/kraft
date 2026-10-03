"""Fire work items from intake.yaml's cron schedules (Kraft-859).

Off by default the same way intake.py's poller is: no `schedules` in
intake.yaml means nothing fires. Every schedule files its item `paused` -- an
agent cannot start work here any more than it can from manual intake; a
scheduled item still needs a human to resume it. `policy.yaml`'s pre-2.0
`triggers:` is read too (`schedules`).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import HTTPException

from kraft import executor
from kraft.api import deps as api_deps
from kraft.policy import Trigger, cron_due

logger = logging.getLogger(__name__)

#: Matches the tick's own resolution (cron is minute-grained); cheap enough
#: to run unconditionally like rate_limit_retry's poller.
_INTERVAL_S = 60


def schedules(st) -> list[tuple[str, Trigger]]:
    """Every schedule this instance runs, each with its source: `intake.yaml`'s
    `schedules:` (`config.Intake`), then `policy.yaml`'s pre-2.0 `triggers:`,
    which is still read so an upgraded file keeps firing."""
    out: list[tuple[str, Trigger]] = []
    intake = getattr(st, "intake", None) or {}
    for index, entry in enumerate(intake.get("schedules") or []):
        out.append((f"intake.yaml schedule {index}", Trigger(**entry)))
    pol = getattr(st, "policy", None)
    for index, trig in enumerate(pol.triggers if pol is not None else ()):
        out.append((f"policy.yaml trigger {index}", trig))
    return out


async def tick(app, *, now: datetime | None = None) -> list[str]:
    """One pass over the schedules (`schedules`). Returns the work item ids filed.

    `app.state.trigger_last_fired` maps a schedule's source name to the last
    "YYYY-MM-DDTHH:MM" it fired for, so a tick landing twice in the same
    minute does not double-file. Reset on restart -- a missed minute is not
    an incident (spec, Kraft-7izl).
    """
    st = app.state
    if getattr(st, "policy", None) is None:
        return []
    now = now or datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    filed: list[str] = []
    for index, trig in schedules(st):
        if not cron_due(trig.cron, now):
            continue
        if st.trigger_last_fired.get(index) == stamp:
            continue
        st.trigger_last_fired[index] = stamp
        if getattr(st, "library", None) is None:
            # Not "unknown chain template": see `intake._start`. One bad file
            # makes every chain unresolvable, and naming the chain id sends the
            # operator to the wrong file.
            logger.warning(
                "%s: the template library is invalid (%s), skipped",
                index,
                "; ".join(getattr(st, "invalid_library", None) or ["config/library.yaml"]),
            )
            continue
        chain = api_deps.resolve_chain(st, trig.chain)
        if chain is None:
            logger.warning("%s: unknown chain %r, skipped", index, trig.chain)
            continue
        try:
            api_deps.connected_or_422(st, trig.repo)
        except HTTPException as exc:
            # Same door the HTTP intake routes use (`deps.connected_or_422`,
            # Kraft-ta8nv): an operator-authored trigger naming an unconnected
            # repo skips, not a whole tick over one bad `policy.yaml` entry.
            logger.warning("%s: %s, skipped", index, exc.detail)
            continue
        try:
            wid = await executor.intake(
                st.db,
                st.run_dirs,
                title=trig.title,
                description=trig.description,
                repo=trig.repo,
                chain=chain,
                effective_policy=api_deps.item_policy(st, trig.repo),
                repository_steering=api_deps.repository_steering(st, trig.repo),
                chain_template=trig.chain,
                status="paused",
            )
        except ValueError as exc:
            # Intake's own refusal (a chain `policy:` past the instance maxima,
            # an unreadable attachment), raised before any side effect. One
            # trigger's bad config skips that trigger, not the rest of the tick
            # (Kraft-ib2af).
            logger.warning("%s: refused at intake, skipped: %s", index, exc)
            continue
        filed.append(wid)
    return filed


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    while True:
        await asyncio.sleep(_INTERVAL_S)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("trigger tick failed")
