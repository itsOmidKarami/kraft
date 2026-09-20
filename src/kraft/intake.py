"""Autonomous pickup from `bd ready` (sub-project E §4-§5).

Off by default, and the one thing in this sub-project that starts work rather
than refusing to. The boundary it must not cross is in §5 and is worth stating
where the code is: **an auto-started work item passes no gate automatically.**
It is created with the same `executor.intake` and run with the same
`executor.run` as a typed-in one, so it reaches its first gate and stops for a
person. Auto-intake removes the typing, not the judgement. An auto-start that
also auto-approved would be a different product, and would get its own design
document before any of it was written.
"""

from __future__ import annotations

import asyncio
import logging

from kraft import config as config_mod
from kraft import executor, store
from kraft import policy as policy_mod
from kraft.adapters import beads
from kraft.templates.models import GateNode

logger = logging.getLogger(__name__)


def _known_beads(db) -> set[str]:
    return {
        r[0]
        for r in db.read(
            lambda c: c.execute(
                "SELECT bead_id FROM work_items WHERE bead_id IS NOT NULL"
            ).fetchall()
        )
    }


def _daily_breached(db, budget: policy_mod.Budget) -> bool:
    """Only the daily cap is evaluable here: a candidate has no work item yet, so
    it has no per-item spend. The per-item cap does its work at first dispatch."""
    if budget.daily_usd is None:
        return False
    since = store.local_midnight_utc()
    # The work-item argument is irrelevant to the daily figure; pass a value that
    # matches nothing rather than inventing an overload.
    _item, daily = db.read(lambda c: store.budget_spend(c, "", since=since))
    return daily >= budget.daily_usd


async def tick(app) -> list[str]:
    """One poll. Returns the work item ids started, which is usually none."""
    from kraft.api import deps

    st = app.state
    cfg = st.intake
    if not cfg.get("enabled"):
        return []
    if st.invalid_policy:
        return []
    budget = st.policy.budget if st.policy else policy_mod.NO_BUDGET
    if _daily_breached(st.db, budget):
        logger.info("auto-intake: daily budget reached, starting nothing")
        return []
    # Every active item counts, not only auto-started ones: a person working on
    # three things must not find the poller adding a fourth.
    slots = int(st.policy.max_concurrent if st.policy else 1) - st.db.read(store.active_count)
    if slots <= 0:
        return []

    try:
        repos = config_mod.load_repos(deps.repos_path(st), validate_steering=False)
    except config_mod.ConfigError as exc:
        logger.warning("auto-intake: repo config invalid, skipping this tick: %s", exc)
        return []
    wanted = set(cfg.get("repos") or [])
    # `enabled` defaults to True here because `load_repos` does not default it and
    # the API's own `RepoBody` does — an entry hand-written without it is on.
    repos = [r for r in repos if r.get("enabled", True) and (not wanted or r["path"] in wanted)]
    # `repos.yaml` paths are stored as written, so a `~` or a trailing slash in
    # the filter matches nothing and the poller ticks forever picking nothing up.
    if wanted and not repos:
        logger.warning("auto-intake: repos filter %r matched no configured repo", sorted(wanted))

    known = _known_beads(st.db)
    ceiling = int(cfg.get("priority_ceiling", 2))
    started: list[str] = []
    for repo in repos:
        if slots <= 0:
            break
        for row in await beads.ready(cwd=repo["path"]):
            if slots <= 0:
                break
            if row["id"] in known or not isinstance(row.get("priority"), int):
                continue
            # An epic is a container for work, not work: auto-starting one files a
            # chain against a title that describes a quarter.
            if row.get("issue_type") == "epic":
                continue
            # P0 is the *highest* priority, so "P2 and below" is `>= ceiling`.
            # The highest-priority work is what a human should be looking at;
            # unattended pickup is for the backlog.
            if row["priority"] < ceiling:
                continue
            wid = await _start(app, repo, row)
            if wid is not None:
                started.append(wid)
                known.add(row["id"])
                slots -= 1
    return started


async def _start(app, repo: dict, row: dict) -> str | None:
    from kraft.api import deps

    st = app.state
    chain = deps.resolve_chain(st, repo.get("default_chain_template") or "default")
    if chain is None:
        logger.warning("auto-intake: %s has no valid chain template, skipping", repo["path"])
        return None
    # Spec §5 is "an auto-started item passes no gate automatically". A template
    # with no gate at all satisfies that by having nothing to pass, which is the
    # letter of the rule and the opposite of its point: it would run to merge
    # unattended. A human can still start such a chain by hand — they are the
    # judgement the gates exist to invoke.
    # A gate is a node whose kind says so (`gate-is-an-ordered-node`), not a
    # `gate_after` name hung off the node in front of it.
    if not any(isinstance(n, GateNode) for n in chain.chain.nodes):
        logger.warning(
            "auto-intake: %s uses %r, which has no gate — refusing to start it "
            "unattended; a person can start it from the board",
            repo["path"],
            chain.id,
        )
        return None
    try:
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=row["title"],
            description=row.get("description"),
            repo=repo["path"],
            chain=chain,
            effective_policy=st.instance_policy,
            bd_cwd=deps.bd_cwd(),
            bead_id=row["id"],
            # the bead was never filed in KRAFT_BD_CWD — it is adopted from the
            # repo's own .beads and can only be closed there.
            bead_cwd=repo["path"],
            source="auto_intake",
            bead_priority=row.get("priority"),
        )
    except Exception:  # noqa: BLE001 -- one bad bead must not stop the poller
        logger.exception("auto-intake: could not file %s", row["id"])
        return None
    logger.info("auto-intake: started %s from %s", wid, row["id"])
    try:
        deps.spawn(
            app,
            wid,
            deps.guard(
                st.db,
                wid,
                executor.run(
                    st.db,
                    st.run_dirs,
                    work_item_id=wid,
                    registry=st.registry,
                    bd_cwd=deps.bd_cwd(),
                    policy=st.policy,
                    launch=deps.launch(st, repo["path"]),
                ),
            ),
        )
    except deps.AlreadyRunning:
        # Structurally unreachable today (every `_start` call is a freshly
        # intaken id), kept for the same reason ci_wait/rate_limit_retry are:
        # a poller finding its own target already running must log and move
        # on, never crash the tick.
        logger.warning("auto-intake: %s already has a live walk, not double-starting", wid)
    return wid


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    raw = app.state.intake.get("interval_s", config_mod.INTAKE_DEFAULT["interval_s"])
    try:
        interval = max(30, int(raw))
    except TypeError, ValueError:
        # Nothing validates a hand-edited intake.yaml, and raising here would
        # kill the poller task at birth — the operator would see a poller they
        # just enabled quietly never pick anything up.
        interval = config_mod.INTAKE_DEFAULT["interval_s"]
        logger.warning("auto-intake: interval_s %r is not a number, using %ss", raw, interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("auto-intake tick failed")
