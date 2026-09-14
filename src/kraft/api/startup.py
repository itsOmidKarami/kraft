from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from kraft import archive, auto_escalate_delay, ci_wait, executor, rate_limit_retry, reattach
from kraft import auth as auth_mod
from kraft import config as config_mod
from kraft import intake as intake_mod
from kraft import notify as notify_mod
from kraft import policy as policy_mod
from kraft import triggers as triggers_mod
from kraft.api import deps
from kraft.db import Database
from kraft.index import db as index_db
from kraft.index.service import Indexer
from kraft.paths import BUNDLED, RunDirs, default_run_dir, default_skills_dir, default_templates_dir
from kraft.templates import load_registry, load_templates
from kraft.ws import Broadcaster

logger = logging.getLogger(__name__)

DEFAULT_FRONTEND_DIST = BUNDLED / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.tasks = {}
    # deps.skip_lock() lazily stashes a per-work-item Lock here; reset it with
    # everything else per-lifespan so it neither grows forever in a live
    # daemon nor outlives its event loop into the next lifespan (fatal under
    # `pytest`, where `api.app` is a shared module singleton -- Kraft-2um8).
    app.state._skip_locks = {}
    # Resolved, so /health's run_dir matches the client's own
    # str(Path(...).resolve()) (kraft.client.reads.health) even when
    # KRAFT_RUN_DIR is relative or symlinked -- otherwise the honest server
    # gets refused as "a different instance".
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir()).resolve()
    run_dirs = RunDirs(run_dir).ensure()
    # The credential a non-browser client (kraft mcp, kraft <verb>) presents when
    # auth is on at all. Created once and kept, so a registered MCP client keeps
    # working across restarts.
    app.state.mcp_token = auth_mod.ensure_mcp_token(run_dirs.base)
    database = await Database.open(run_dirs.db)
    # Read the templates dir at startup, not import time, so tests (and reloads)
    # that set KRAFT_TEMPLATES_DIR after import still take effect.
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    # Set on app.state now (not after reattach below, where it lived before) so
    # `launch` — which reads st.templates_dir — can build a launch context for
    # a reattached work item's resume.
    app.state.templates_dir = templates_dir
    # Where an operator may override a bundled method file. Absent on almost
    # every install; `kraft.skill` falls back to the packaged copy.
    app.state.skills_dir = Path(os.environ.get("KRAFT_SKILLS_DIR") or default_skills_dir())
    registry = load_registry(templates_dir / "registry.yaml", skills_dir=app.state.skills_dir)
    templates = load_templates(templates_dir, registry)

    # Config the Settings screens edit. Read once here and re-read on every save,
    # so a hand edit and a UI edit are the same operation to the rest of the app.
    access = config_mod.load_access(templates_dir / "access.yaml")
    # A hand-edit typo must not refuse the boot: degrade to the default — off —
    # so Settings → Auto-intake comes up and can be used to fix the file.
    try:
        app.state.intake = config_mod.load_intake(templates_dir / "intake.yaml")
    except config_mod.ConfigError as exc:
        logger.warning("intake.yaml is unreadable, auto-intake stays off: %s", exc)
        app.state.intake = dict(config_mod.INTAKE_DEFAULT)

    policy_obj = None
    invalid_policy: list[str] = []
    try:
        policy_obj = policy_mod.load_policy(templates_dir / "policy.yaml")
    except policy_mod.PolicyError as exc:
        invalid_policy = [str(exc)]

    # Snapshot before reattach spawns anything. A resumed executor task starts
    # running at its first `await` -- some time after this line, but well
    # before `notifier.start()` below (which comes after `startup_scan`, an
    # unbounded scan on a large index). A `MAX(seq)` read taken there instead
    # would land past whatever a just-resumed work item already emitted, and
    # the notifier would never see it: not "late", gone. This snapshot is
    # taken here, before reattach, and handed straight through to
    # `notifier.start()` so the notifier's own cursor can never be later than
    # the first event a resumed task might produce.
    notify_cursor = database.read(
        lambda c: c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
    )

    summary, adopted = await reattach.reattach(
        database,
        run_dirs,
        registry,
        policy=policy_obj,
        launch_factory=lambda repo: deps.launch(app.state, repo),
        bd_cwd=deps.bd_cwd(),
        on_approve=deps._on_approve(app.state),
    )
    # Not `.update(adopted)` alone: unlike `deps.spawn`'s tasks, nothing else
    # ever pops a session_id-keyed entry, so an adopted task would sit in
    # app.state.tasks forever, live or dead (Kraft-mjwz).
    for sid, task in adopted.items():
        app.state.tasks[sid] = task
        task.add_done_callback(lambda _t, sid=sid: app.state.tasks.pop(sid, None))
    for wid in summary.resumed_work_items:
        repo_row = database.read(
            lambda c, wid=wid: c.execute(
                "SELECT repo FROM work_items WHERE id = ?", (wid,)
            ).fetchone()
        )
        deps.spawn(
            app,
            wid,
            deps.guard(
                database,
                wid,
                executor.resume(
                    database,
                    run_dirs,
                    work_item_id=wid,
                    registry=registry,
                    adopted=adopted,
                    bd_cwd=deps.bd_cwd(),
                    policy=policy_obj,
                    launch=deps.launch(app.state, repo_row["repo"]) if repo_row else None,
                    on_approve=deps._on_approve(app.state),
                ),
            ),
        )

    app.state.db = database
    app.state.run_dirs = run_dirs
    app.state.registry = registry
    app.state.templates = templates
    app.state.policy = policy_obj
    app.state.access = access
    # What the server is really listening on. __main__ reads access.yaml for this,
    # so they normally agree — until someone saves a new bind and has not restarted.
    app.state.bound_host = os.environ.get("KRAFT_HOST") or access["bind"]
    app.state.invalid_policy = invalid_policy
    app.state.reattach_summary = summary

    dist = Path(os.environ.get("KRAFT_FRONTEND_DIST") or DEFAULT_FRONTEND_DIST)
    app.state.frontend_dist = dist if dist.is_dir() else None

    index_conn = index_db.open_index(run_dirs.index_db)
    indexer = Indexer(
        index_conn,
        database,
        repos_env=os.environ.get("KRAFT_INDEX_REPOS"),
        run_dirs=run_dirs,
        repos_path=templates_dir / "repos.yaml",
    )
    await indexer.startup_scan()  # 04 §2: once, before live event handling
    await indexer.start()
    app.state.index_conn = index_conn
    app.state.indexer = indexer

    broadcaster = Broadcaster(database)
    await broadcaster.start()
    # Third subscriber on the same fan-out. Its sends are detached tasks, so a
    # hanging webhook cannot stall the WebSocket or the indexer behind it.
    notifier = notify_mod.Notifier(
        database,
        templates_dir / "notify.yaml",
        fallback_base_url=f"http://{app.state.bound_host}:{access['port']}",
    )
    await notifier.start(cursor=notify_cursor)
    database.set_on_commit(lambda: (broadcaster.notify(), indexer.notify(), notifier.notify()))
    app.state.broadcaster = broadcaster
    app.state.notifier = notifier
    # Off by default costs nothing at all: no task, no timer, no tick. On
    # `app.state` as well as in a local because that is the only way a test can
    # tell "no poller was created" from "a poller was created and did nothing" —
    # deleting the condition would otherwise leave every test green while a
    # disabled instance grew a live timer.
    intake_task = (
        asyncio.ensure_future(intake_mod.poller(app)) if app.state.intake["enabled"] else None
    )
    app.state.intake_task = intake_task
    # Always on, unlike auto-intake: waiting out a rate limit is not optional
    # behaviour an operator enables, it is what this feature promises.
    app.state.rate_limit_task = asyncio.ensure_future(rate_limit_retry.poller(app))
    # Always on, for the same reason the rate-limit poller is: a work item
    # parked on a pipeline has to be woken by something, and that something
    # cannot be the coroutine that used to sit in the wait (Kraft-ru98).
    app.state.ci_wait_task = asyncio.ensure_future(ci_wait.poller(app))
    # Always on, for the same reason rate-limit/ci-wait are: an item sitting
    # past its own auto_escalate_delay_s has to be re-checked by something,
    # and that something cannot be the coroutine that made the original
    # inline call and already returned (Kraft-vyk8).
    app.state.auto_escalate_delay_task = asyncio.ensure_future(auto_escalate_delay.poller(app))
    # Always on, for the same reason the rate-limit and ci-wait pollers are:
    # an item aged past policy.archive_after_days has to be archived by
    # something, and an operator who forgets to check the board is exactly
    # who auto-archive exists for (UI v2 · 03).
    app.state.archive_task = asyncio.ensure_future(archive.poller(app))
    # PUT /intake swaps this task, and the swap has to await the cancellation of
    # the old one. Without the lock two overlapping saves both read the same old
    # task, both start a poller, and only the last assignment is reachable --
    # the other ticks on, uncancellable, past shutdown.
    app.state.intake_lock = asyncio.Lock()
    app.state.trigger_last_fired = {}
    app.state.trigger_task = (
        asyncio.ensure_future(triggers_mod.poller(app))
        if app.state.policy and app.state.policy.triggers
        else None
    )
    try:
        yield
    finally:
        database.set_on_commit(None)
        await broadcaster.stop()
        await notifier.stop()
        await indexer.stop()
        index_conn.close()
        # Before the work item tasks, so a tick in flight cannot _spawn one
        # into the list that is about to be cancelled.
        # app.state, not the local: PUT /intake replaces this task when the
        # operator toggles the poller, and cancelling the one lifespan happened
        # to start would leave the live one running past shutdown.
        live_intake_task = app.state.intake_task
        if live_intake_task is not None:
            live_intake_task.cancel()
            await asyncio.gather(live_intake_task, return_exceptions=True)
        app.state.rate_limit_task.cancel()
        await asyncio.gather(app.state.rate_limit_task, return_exceptions=True)
        if app.state.trigger_task is not None:
            app.state.trigger_task.cancel()
            await asyncio.gather(app.state.trigger_task, return_exceptions=True)
        app.state.ci_wait_task.cancel()
        await asyncio.gather(app.state.ci_wait_task, return_exceptions=True)
        app.state.auto_escalate_delay_task.cancel()
        await asyncio.gather(app.state.auto_escalate_delay_task, return_exceptions=True)
        app.state.archive_task.cancel()
        await asyncio.gather(app.state.archive_task, return_exceptions=True)
        tasks = list(app.state.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await database.close()
