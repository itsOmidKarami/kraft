from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from kraft import apply as apply_mod
from kraft import (
    archive,
    auto_escalate_delay,
    caps,
    executor,
    inplace,
    mr_poller,
    rate_limit_retry,
    start_queue,
    storage,
    waits,
    when_free,
)
from kraft import auth as auth_mod
from kraft import builtins as _builtins
from kraft import config as config_mod
from kraft import intake as intake_mod
from kraft import notify as notify_mod
from kraft import policy as policy_mod
from kraft import triggers as triggers_mod
from kraft import update as update_mod
from kraft.adapters import hook_install
from kraft.adapters import subprocess as _subprocess
from kraft.adapters.forge import git as forge_git
from kraft.addons import load as addons_load
from kraft.api import deps
from kraft.db import Database
from kraft.index import db as index_db
from kraft.index.service import Indexer
from kraft.paths import BUNDLED, RunDirs, config_dir, default_run_dir, default_skills_dir
from kraft.worker import channel as channel_mod
from kraft.worker import reattach, sandbox
from kraft.worker.egress import EgressProxy
from kraft.ws import Broadcaster

logger = logging.getLogger(__name__)

DEFAULT_FRONTEND_DIST = BUNDLED / "web"


#: The pollers and loops `start_background` assigns to `app.state`, in the order
#: shutdown cancels them (`lifespan`); None while not started.
BACKGROUND_TASKS = (
    "intake_task",
    "rate_limit_task",
    "trigger_task",
    "wait_task",
    "queue_task",
    "caps_task",
    "auto_escalate_delay_task",
    "archive_task",
    "storage_task",
    "mr_poller_task",
    "apply_task",
    "addon_update_task",
    "when_free_task",
)


def start_background(app: FastAPI) -> None:
    """Start every poller and loop. At startup, or once an update's probation
    ends: until then the server only answers."""
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
    # parked on an external wait has to be woken by something, and that
    # something cannot be the coroutine that used to sit in the wait
    # (Kraft-ru98). One scheduler for every wait kind.
    app.state.wait_task = asyncio.ensure_future(waits.poller(app))
    # Always on, like the wait scheduler: a queued item was promised a start
    # when a slot frees, and nothing else looks.
    app.state.queue_task = asyncio.ensure_future(start_queue.poller(app))
    # Always on: a parked item's total time cap and a gate's own timeout run
    # out while nothing of the item runs, so no launch is there to see it.
    app.state.caps_task = asyncio.ensure_future(caps.poller(app))
    # Always on, for the same reason rate-limit/waits are: an item sitting
    # past its own auto_escalate_delay_s has to be re-checked by something,
    # and that something cannot be the coroutine that made the original
    # inline call and already returned (Kraft-vyk8).
    app.state.auto_escalate_delay_task = asyncio.ensure_future(auto_escalate_delay.poller(app))
    # Always on, for the same reason the rate-limit poller and wait scheduler are:
    # an item aged past policy.archive_after_days has to be archived by
    # something, and an operator who forgets to check the board is exactly
    # who auto-archive exists for (UI v2 · 03).
    app.state.archive_task = asyncio.ensure_future(archive.poller(app))
    # Always on, like the archive poller: with `storage.worktrees.limit` set,
    # something has to notice the disk filling while nobody watches. Without
    # the key a tick returns before it measures.
    app.state.storage_task = asyncio.ensure_future(storage.poller(app))
    # Always on, like the pollers above: nothing else watches for a merge
    # request closed on the forge outside Kraft, so an item parked at an MR
    # node has to be re-checked by something. Its own `forge_poll_s` cadence,
    # not the others' 10-30s: unlike them this calls `gh`/`glab` once per
    # item per tick.
    app.state.mr_poller_task = asyncio.ensure_future(mr_poller.poller(app))
    # Always, not only for boot-time triggers: each tick reads st.policy, so a
    # trigger added by PUT /policy or `kraft admin reload` fires without a
    # restart (Kraft-ygnw6). A tick with no triggers files nothing.
    app.state.trigger_task = asyncio.ensure_future(triggers_mod.poller(app))
    app.state.apply_task = asyncio.ensure_future(apply_mod.watcher(app))
    # After the library is loaded from the lock: a start never waits on the network.
    deps.in_background(app, deps.auto_update_addons)
    app.state.addon_update_task = asyncio.ensure_future(deps.auto_update_daily(app))
    # Off by default costs a look a minute at a file and the database: it
    # installs and restarts only what a person marked, or `update.yaml` allows.
    app.state.when_free_task = asyncio.ensure_future(when_free.loop(app))


async def _probation_timer(app: FastAPI) -> None:
    """The helper that would confirm this server may be gone: being up and
    healthy for `PROBATION_S` is enough to go on, and no other process is
    left to roll back."""
    await asyncio.sleep(when_free.PROBATION_S)
    await end_probation(app)


async def end_probation(app: FastAPI) -> bool:
    """The new server has shown it can run: start the walks and pollers it held
    back, say the update was applied and drop the record. False when it was not
    on probation. The snapshot stays `KEEP_SNAPSHOT_S` more, then goes."""
    st = app.state
    if not st.probation:
        return False
    st.probation = False
    run_dirs = st.run_dirs
    record = when_free.read_rollback(run_dirs) or {}
    st.resume_walks()
    start_background(app)
    when_free.record_outcome(
        st,
        status="applied",
        key=None,
        detail=f"restarted on {st.version}",
        from_version=record.get("from_version"),
        to_version=st.version,
    )
    when_free.clear_rollback(run_dirs)
    snapshot = record.get("config_snapshot")
    if snapshot:
        keep_for = when_free.KEEP_SNAPSHOT_S
        folder = str(Path(snapshot).parent)

        async def drop() -> None:
            await asyncio.sleep(keep_for)
            await asyncio.to_thread(when_free.delete_snapshots, run_dirs)

        st.snapshot_task = asyncio.ensure_future(drop())
        when_free.delete_snapshots(run_dirs, keep=folder)
    return True


async def _cancel(task: asyncio.Future | None) -> None:
    """Cancel a task that may never have been started (an update on probation
    starts none of them) and wait for it to end."""
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before anything can run git: every git Kraft (or a worker it spawns)
    # runs is launched with hooks and the other program-valued config keys
    # pinned, so a hook or `core.hooksPath` a worker plants in the one gitdir
    # it must be able to write cannot execute as the invoking host user
    # (Kraft-rki).
    sandbox.harden_host_git_env()
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
    # The same kind of file, good for `POST /api/triggers` only (`_authenticate`).
    app.state.trigger_token = auth_mod.ensure_mcp_token(run_dirs.base, auth_mod.TRIGGER_TOKEN_FILE)
    app.state.login_failures = {}
    database = await Database.open(run_dirs.db)
    # Before reattach, which re-opens adopted sessions' egress channels: the
    # listeners run on this loop, the one every launch awaits on.
    # The proxy serves the worker API (host `kraft`) out of this app, and
    # the session MCP server beside it, up before an adopted session asks.
    proxy = EgressProxy(app=app)
    app.state.egress_channels = channel_mod.ChannelRegistry(run_dirs, database, proxy)
    proxy_serving = proxy.serving()
    await proxy_serving.__aenter__()
    channel_mod.install(app.state.egress_channels)
    # Where a VM-backed runtime's relays reach the same registry: up before
    # reattach too, on the port those relays were started against.
    app.state.egress_tls = channel_mod.TLSListener(app.state.egress_channels, run_dirs)
    await app.state.egress_tls.start()
    # Read the templates dir at startup, not import time, so tests (and reloads)
    # that set KRAFT_CONFIG_DIR after import still take effect.
    templates_dir = config_dir()
    # Set on app.state now (not after reattach below, where it lived before) so
    # `launch` — which reads st.templates_dir — can build a launch context for
    # a reattached work item's resume.
    app.state.templates_dir = templates_dir
    # Where an operator may override a bundled method file. Absent on almost
    # every install; `kraft.skill` falls back to the packaged copy.
    app.state.skills_dir = Path(os.environ.get("KRAFT_SKILLS_DIR") or default_skills_dir())
    # The 2.0 upgrade: an old theme.yaml's `palette` becomes the look it
    # stood for, once, before anything reads the theme.
    try:
        theme = templates_dir / "theme.yaml"
        if config_mod.migrate_theme(theme):
            logger.info(
                "theme.yaml: palette converted to surface, accent and colour_amount;"
                " the old file is %s",
                config_mod.theme_backup(theme).name,
            )
    except OSError:
        logger.exception("theme migration failed; theme.yaml left as it was")
    # From the lock and the store alone: startup never waits on the network.
    library, invalid_library = deps.load_library(
        templates_dir,
        app.state.skills_dir,
        addons_load.installed(templates_dir, run_dirs.addons, verify=True),
    )
    app.state.addons_problem = addons_load.config_problem(templates_dir)
    # Now, not with the rest of app.state below: reattach's launch factory
    # reads it, for an item filed before repository steering was frozen.
    app.state.library = library

    # Config the Settings screens edit. Read once here and re-read on every save,
    # so a hand edit and a UI edit are the same operation to the rest of the app.
    access = config_mod.Access.load(templates_dir / "access.yaml").model_dump()
    # A hand-edit typo must not refuse the boot: degrade to the default — off —
    # so Settings → Auto-intake comes up and can be used to fix the file.
    # `/health` and doctor name it (`invalid_intake`), as they name a bad policy.
    app.state.invalid_intake = None
    # The app object outlives a lifespan in tests: a measurement from one
    # start must not be read by the next.
    app.state.storage_usage = None
    app.state.storage_kick = None
    #: Whether auto-intake and intake.yaml's schedules are off for it: only a
    #: start on a bad file. A reload that refuses one keeps what was running.
    #: A trigger left in policy.yaml fires either way (`triggers.schedules`).
    app.state.intake_off = False
    try:
        app.state.intake = config_mod.Intake.load(templates_dir / "intake.yaml").model_dump()
    except config_mod.ConfigError as exc:
        logger.warning("intake.yaml is unreadable, auto-intake stays off: %s", exc)
        app.state.intake = dict(config_mod.INTAKE_DEFAULT)
        app.state.invalid_intake = str(exc)
        app.state.intake_off = True

    policy_obj = None
    invalid_policy: list[str] = []
    # One read of one file (Ruling 37): the legacy loop-cap `Policy` the
    # executor reads and V1's `defaults:`/`maxima:` instance policy come out of
    # the same parse, so the two can never disagree about what policy.yaml
    # says. An unreadable file leaves the *empty* instance policy rather than
    # None -- an unset maximum is no bound at all, which is what a materialized
    # chain needs when the process is already refusing work over
    # `invalid_policy`.
    instance_policy = policy_mod.InstancePolicy.from_input(policy_mod.InstancePolicyInput())
    try:
        policy_obj, instance_policy = deps.read_policy(templates_dir)
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

    # Before reattach, so a cursor session adopted across an upgrade runs the
    # permission hook as this Kraft writes it, not as an earlier one did.
    # A thread: it reads a file in every worktree. Never a refused boot: a
    # worker wrote each of those files.
    try:
        refreshed = await asyncio.to_thread(hook_install.refresh_cursor_hooks, run_dirs.worktrees)
    except Exception:  # noqa: BLE001
        logger.exception("cursor permission hook refresh failed; worktrees left as they were")
        refreshed = []
    for path in refreshed:
        logger.info("%s: Kraft's permission hook now runs with -I", path)

    try:
        await asyncio.to_thread(inplace.drop_stale_scratch, run_dirs)
    except Exception:  # noqa: BLE001 -- a leftover never refuses the boot
        logger.exception("could not remove the throwaway rebase worktrees in %s", run_dirs.scratch)

    try:
        for pid in await reattach.reap_stopped(database):
            logger.info("stopped the process group %s a stopped session left running", pid)
    except Exception:  # noqa: BLE001 -- never a refused boot
        logger.exception("could not stop the process groups stopped sessions left")

    summary, adopted = await reattach.reattach(
        database,
        run_dirs,
        policy=policy_obj,
        launch_factory=lambda repo: deps.launch(app.state, repo),
        bd_cwd=deps.bd_cwd(),
        on_approve=deps._on_approve(app.state),
    )
    # Not `.update(adopted)` alone: unlike `deps.spawn`'s tasks, nothing else
    # ever pops a session-keyed entry, so an adopted task would sit in
    # app.state.tasks forever, live or dead (Kraft-mjwz). Keyed by the
    # session's `ids.qualified` name, never its bare id: that is unique only
    # within its item.
    for name, task in adopted.items():
        app.state.tasks[name] = task
        task.add_done_callback(lambda _t, name=name: app.state.tasks.pop(name, None))

    def resume_walks() -> None:
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
                        adopted=adopted,
                        bd_cwd=deps.bd_cwd(),
                        policy=policy_obj,
                        launch=deps.launch(app.state, repo_row["repo"]) if repo_row else None,
                        on_approve=deps._on_approve(app.state),
                    ),
                ),
            )

    # An update that has not yet proved the new server leaves this one on
    # probation: it answers `/health` and serves the UI, and starts no walk,
    # poller or loop until the restart helper confirms it, or `PROBATION_S`
    # passes with the server up (`end_probation`). A record left by an attempt
    # nobody confirmed (the helper died mid-way) reads the same. A record whose
    # restart was never handed off is an attempt that did not get that far, and
    # a manual restart is not an update's: it starts at once.
    leftover = when_free.read_rollback(run_dirs)
    app.state.probation = when_free.handed_off(leftover)
    if leftover is not None and not app.state.probation:
        when_free.settle_unhanded(run_dirs, leftover)
    app.state.resume_walks = resume_walks
    if not app.state.probation:
        resume_walks()

    app.state.db = database
    app.state.run_dirs = run_dirs
    app.state.library = library
    app.state.invalid_library = invalid_library
    app.state.policy = policy_obj
    app.state.instance_policy = instance_policy
    deps.lint_loaded(app.state)
    app.state.loaded_hashes = {}
    app.state.apply_signature = ()
    apply_mod.record(app.state)
    if policy_obj:
        forge_git.CLI_TIMEOUT_S = policy_obj.forge_cli_timeout_s
        _subprocess.IDLE_MINUTES = policy_obj.agent_idle_minutes
    app.state.access = access
    # What the server is really listening on. __main__ reads access.yaml for this,
    # so they normally agree — until someone saves a new bind and has not restarted.
    app.state.bound_host = os.environ.get("KRAFT_HOST") or access["bind"]
    app.state.bound_port = int(os.environ.get("KRAFT_PORT") or access["port"])
    app.state.invalid_policy = invalid_policy
    app.state.reattach_summary = summary
    app.state.started_at = time.monotonic()
    # The version this process loaded, read once. `kraft admin update` replaces
    # the package under a running server, and the metadata read at call time
    # then names the new release while this one's code is still answering.
    app.state.version = update_mod.installed()

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
        fallback_base_url=(
            f"http://{config_mod.url_host(app.state.bound_host)}:{app.state.bound_port}"
        ),
    )
    await notifier.start(cursor=notify_cursor)
    database.set_on_commit(lambda: (broadcaster.notify(), indexer.notify(), notifier.notify()))
    app.state.broadcaster = broadcaster
    app.state.notifier = notifier
    app.state.mr_poll_errors = {}
    # PUT /intake swaps the intake task, and the swap has to await the cancellation of
    # the old one. Without the lock two overlapping saves both read the same old
    # task, both start a poller, and only the last assignment is reachable --
    # the other ticks on, uncancellable, past shutdown.
    app.state.intake_lock = asyncio.Lock()
    # A draft publish writes its files and reloads under this (`routes.drafts`).
    app.state.draft_publish_lock = asyncio.Lock()
    app.state.trigger_last_fired = {}
    # Every background add-on task (`deps.in_background`), so shutdown reaches all.
    app.state.addon_tasks = set()
    # Set at shutdown: the worker threads of those tasks stop between add-ons.
    app.state.addon_cancel = threading.Event()
    app.state.addon_threads = set()
    # None until `start_background` runs: not at all while an update is on probation.
    for name in BACKGROUND_TASKS:
        setattr(app.state, name, None)
    app.state.probation_task = None
    app.state.snapshot_task = None
    if app.state.probation:
        app.state.probation_task = asyncio.ensure_future(_probation_timer(app))
    else:
        start_background(app)
        # Leftovers of an attempt that was confirmed or never made.
        when_free.delete_snapshots(run_dirs, older_than_s=when_free.KEEP_SNAPSHOT_S)
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
        for task in (
            live_intake_task,
            app.state.probation_task,
            app.state.snapshot_task,
            app.state.rate_limit_task,
            app.state.trigger_task,
            app.state.wait_task,
            app.state.queue_task,
            app.state.caps_task,
            app.state.auto_escalate_delay_task,
            app.state.archive_task,
        ):
            await _cancel(task)
        storage.stop(app.state)
        await _cancel(app.state.storage_kick)
        for task in (
            app.state.storage_task,
            app.state.mr_poller_task,
            # The daily task first: it is what starts the next add-on task.
            app.state.addon_update_task,
            app.state.apply_task,
            app.state.when_free_task,
        ):
            await _cancel(task)
        await deps.stop_addon_work(app.state)
        # Before the walks are cancelled: a setup command runs in a thread
        # no cancel reaches, and would outlive this server.
        _builtins.end_running_setups()
        _subprocess.server_stopping()
        tasks = list(app.state.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # After the walks: a cancelled walk stops its own git and aborts what
        # it left. Ended before that, a `git rebase` read as a conflict.
        _builtins.end_running_git_groups()
        await app.state.egress_tls.close()
        await app.state.egress_channels.close_all()
        await proxy_serving.__aexit__(None, None, None)
        channel_mod.install(None)
        await database.close()
