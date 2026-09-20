"""Helpers shared by several route modules.

Renamed public at this split (Kraft api.py -> kraft/api package): `guard`,
`bd_cwd`, `spawn`, `launch`, `repos_path` used to be `_guard`, `_bd_cwd`,
`_spawn`, `_launch`, `_repos_path` on `kraft.api` — `kraft.intake`,
`kraft.ci_wait`, and `kraft.rate_limit_retry` import them lazily by name, so a
star re-export (which skips underscore names) would otherwise have stranded
them.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from kraft import config as config_mod
from kraft import executor, harness, store
from kraft.templates import load_registry, load_templates
from kraft.templates.library import TemplateLibrary, TemplateLibraryError

logger = logging.getLogger(__name__)

#: Kraft-c5ui: the `cancel(..., timeout=...)` a caller passes when it has
#: nothing riding on the old task actually being gone by the time it returns
#: -- gate approve/reject already SIGTERM'd the review process themselves,
#: and pause spawns no replacement -- so all three would rather return late
#: than hang the request on a git/forge call the old task is parked in.
#: `skip` does not use this: it spawns a replacement off the assumption the
#: old task is gone, so it still waits unbounded.
CANCEL_TIMEOUT = 5.0


class AlreadyRunning(RuntimeError):
    """A live executor task already holds this key. Not `HTTPException`:
    three of `spawn`'s callers are pollers, not routes, and making `ci_wait`
    import FastAPI to catch its own race is backwards. Routes translate this
    into a 409; pollers catch it and log-and-skip, because a poller finding
    an item already running is a normal race, not a poller crash."""


async def guard(db, wid: str, coro) -> None:
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("executor task crashed for %s", wid)
        reason = f"executor crashed: {exc!r}"
        try:
            await db.write(lambda c: store.mark_needs_human(c, wid, None, reason))
        except Exception:  # noqa: BLE001
            logger.exception("could not mark %s needs_human after crash", wid)


def bd_cwd() -> str | None:
    return os.environ.get("KRAFT_BD_CWD") or None


def _bead_warning(st, wid: str) -> str | None:
    """Why intake filed no bead, or None — read back from the event
    `executor.intake` wrote in the same transaction as the row (Kraft-7gy)."""
    row = st.db.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE work_item_id = ? AND type = 'bead_not_filed'",
            (wid,),
        ).fetchone()
    )
    return json.loads(row["payload"])["reason"] if row else None


def task_is_live(app: FastAPI, wid: str) -> bool:
    """True if `wid` already has an undone task -- the same test `spawn` makes
    internally, exposed so a gate route can check it *before* a mutating store
    write. A gate has no status column to claim (§1 doesn't cover it): calling
    `store.approve_gate`/`apply_rejection` and only then discovering `spawn`
    refuses would leave the gate cleared and the item `active` with no walk
    behind it -- the exact stranding `claim_for_run`'s ordering exists to
    prevent everywhere else. Checking first, with no `await` before the write
    that follows, shrinks that race to the width of the write itself."""
    existing = app.state.tasks.get(wid)
    return existing is not None and not existing.done()


def spawn(app: FastAPI, wid: str, coro) -> asyncio.Task:
    if task_is_live(app, wid):
        # A refused coroutine that is never awaited raises "coroutine was
        # never awaited" at garbage-collection time and leaks whatever it
        # closed over (the `guard` wrapper, the executor.run frame, ...).
        coro.close()
        raise AlreadyRunning(wid)
    task = asyncio.ensure_future(coro)
    app.state.tasks[wid] = task

    def _done(t, wid=wid):
        # Preemption paths (approve/reject/retry) SIGTERM the old task and
        # immediately spawn a new one under the same wid before the old
        # task's callback fires. Popping unconditionally would let the old
        # task's callback evict the new, still-live task -- dropping the
        # tick loop's only concurrency guard and orphaning the new run.
        # Only pop the slot if it still holds *this* task.
        if app.state.tasks.get(wid) is t:
            app.state.tasks.pop(wid, None)

    task.add_done_callback(_done)
    return task


async def cancel(app: FastAPI, wid: str, timeout: float | None = None) -> None:
    """Cancel `wid`'s live walk task, if any, and wait for it to actually
    finish before returning.

    A bare `.cancel()` only *schedules* a `CancelledError` at the task's next
    await point -- `task.done()` stays False until the event loop resumes it.
    `skip` cancels the current walk and spawns its replacement in the same
    request, with no `await` in between; without waiting here, `spawn`'s
    `AlreadyRunning` check would see the old task as still live and refuse the
    new one every time. Awaiting the task makes cancellation synchronous from
    the caller's point of view: by the time this returns, `spawn`'s own
    done-callback has already popped `app.state.tasks[wid]`.

    `deps.guard` re-raises `CancelledError` and swallows every other
    exception itself, so `CancelledError` is the only thing `await task` can
    raise here.

    `timeout`, when given, bounds the wait (Kraft-c5ui): a task parked in
    `asyncio.to_thread` (a git/forge call) does not see the cancellation
    until that call returns, so an unbounded wait here can block the whole
    request for as long as that call takes. Past the deadline this returns
    anyway, with the task still cancelling in the background -- its own
    done-callback still pops `app.state.tasks[wid]` whenever it actually
    exits. Only safe for a caller with nothing that depends on the task
    being gone by the time this returns: `skip` still needs the unbounded
    wait, because it spawns a replacement off exactly that assumption.
    `asyncio.shield` keeps the timeout from cancelling anything itself --
    the task already got its `.cancel()` above and is left to finish that on
    its own.
    """
    task = app.state.tasks.get(wid)
    if task is None or task.done():
        return
    task.cancel()
    try:
        if timeout is None:
            await task
        else:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.CancelledError:
        pass
    except TimeoutError:
        logger.warning(
            "cancel: task for %s did not unwind within %.1fs; proceeding anyway", wid, timeout
        )


def skip_lock(app: FastAPI, wid: str) -> asyncio.Lock:
    """A per-`wid` lock serializing `/skip` end to end.

    `/skip` can start from `paused`/`needs_human`, where there is no live task
    yet for `spawn`'s own check to catch, and `claim_for_run`'s `to_status`
    (`"active"`) is also one of `/skip`'s own `from_statuses` -- so two
    concurrent skips both win that claim too and both write `skip_node`
    before either reaches `spawn`. The caller checks `.locked()` before
    entering, refusing a second caller outright rather than queuing it behind
    the first -- a queued second caller would wake up to a state the first
    already advanced, and would then skip the *next* node too instead of
    getting the 409 two callers racing the same node are supposed to get.
    """
    locks = getattr(app.state, "_skip_locks", None)
    if locks is None:
        locks = {}
        app.state._skip_locks = locks
    return locks.setdefault(wid, asyncio.Lock())


def _work_item_row(st, wid):
    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown work item")
    return row


def _reload_templates(st) -> None:
    # Loaded once per reload and shared, so a registry with twelve agent
    # hooks reads the harness directory once rather than twelve times.
    st.harnesses = harness.load(None)
    st.registry = load_registry(
        st.templates_dir / "registry.yaml",
        skills_dir=st.skills_dir,
        harnesses=st.harnesses,
    )
    st.templates = load_templates(st.templates_dir, st.registry)
    st.library, st.invalid_library = load_library(st.templates_dir)


def load_library(templates_dir: Path) -> tuple[TemplateLibrary | None, list[str]]:
    """The V1 template library for `templates_dir`, and why it is missing.

    Degrades rather than raising, the same way `policy.yaml` does at startup: a
    library that cannot be parsed must still leave the Settings screens
    reachable, because they are how an operator fixes it. Every caller checks
    for `None`; nothing falls back to the legacy loader.
    """
    try:
        return TemplateLibrary.from_yaml_dir(templates_dir), []
    except TemplateLibraryError as exc:
        logger.warning("template library unreadable: %s", exc)
        return None, [str(exc)]


def resolve_chain(st, chain_template: str | None):
    """The resolved V1 chain `chain_template` names, or `None`.

    `None` for `chain_template` means "no explicit template was chosen"
    (Kraft-cd47) and resolves `default`, the same way the legacy lookup did --
    the *stored* value stays `None`, which is the distinction that matters.
    Resolution failures are `None` too: a chain that does not resolve is not
    selectable, and `lint()` is where an author reads why.
    """
    if st.library is None:
        return None
    try:
        return st.library.resolve_chain(chain_template if chain_template is not None else "default")
    except TemplateLibraryError:
        logger.warning("chain template %r does not resolve", chain_template, exc_info=True)
        return None


def resolve_chain_or_422(st, chain_template: str | None):
    """`resolve_chain`, as the 422 every intake door owes its caller. One
    function so the three doors (`POST /work-items`, `POST /triggers`, and the
    auto-intake poller's own check) cannot answer differently."""
    chain = resolve_chain(st, chain_template)
    if chain is None:
        raise HTTPException(422, "unknown or invalid template")
    return chain


def repos_path(st) -> Path:
    return st.templates_dir / "repos.yaml"


def _connected(repos: list[dict], path: str) -> dict | None:
    """Find a connected repo by path.

    `POST /repos` stores git's `--show-toplevel`, which resolves symlinks (on
    macOS /var -> /private/var), so the path a client added with is not always
    the path stored. Match either, or a caller cannot patch or delete the repo
    it just connected.
    """
    entry = next((r for r in repos if r["path"] == path), None)
    if entry is not None:
        return entry
    resolved = str(Path(path).expanduser().resolve())
    return next((r for r in repos if r["path"] == resolved), None)


def _on_approve(st) -> executor.OnApprove:
    """`apply_approval` bound to this app state — the door `_review_gates` calls
    so an agent's `approve` verdict has a human approval's effects."""
    from kraft.api.routes import gates

    return functools.partial(gates.apply_approval, st)


class _PoisonedRepoEntry(dict):
    """Stand-in `repo_entry` for a `repos.yaml` that failed to load.

    Every real reader of `repo_entry` -- `sandbox.resolve`,
    `adapters.agent.resolve_invocation`, `dispatch`'s own test-scope fallback
    -- reads it with `.get(...)`. Raising from that `.get` surfaces the
    original `ConfigError` from *inside* the dispatch this entry was actually
    needed for, which is already wrapped in `guard`'s blanket `except
    Exception` -- the same needs_human treatment a `SteeringError` raised
    deep in a dispatch already gets. Non-empty (the `_poisoned` key), so
    `launch.repo_entry or {}` never discards it for a fresh, harmless `{}`
    before that `.get` runs -- a repo config a caller can't parse must not
    quietly resolve to "no sandbox, no deny_tools, no steering" instead."""

    def __init__(self, exc: config_mod.ConfigError) -> None:
        super().__init__(_poisoned=True)
        self._exc = exc

    def get(self, *_args, **_kwargs):
        raise self._exc


def launch(st, repo: str) -> executor.LaunchContext:
    """The repo config for one agent dispatch.

    A malformed `repos.yaml` must not crash `lifespan` on reattach (locking
    an operator out of the Settings UI that would let them fix it) or 500 the
    approve/reject/resume/retry routes, so this itself never raises. But a
    repo entry can carry `sandbox:` now, so silently degrading to
    `repo_entry=None` would run that dispatch's worker unsandboxed on the
    host with nothing but a log line nobody reads -- the exact bare-metal
    fallback the sandbox design rules out. Instead the failure is carried in
    a `_PoisonedRepoEntry` that raises the instant the dispatch that actually
    needed this repo's config reads it, landing in `guard` as needs_human
    the same way a bad `sandbox:` value would if it were only caught then.

    Steering is deliberately *not* validated here (`validate_steering=False`):
    a name whose file has since been deleted must still let the repo entry
    load normally, model/deny_tools intact, rather than losing them along with
    everything else. The dispatch that actually reads that steering file is
    what surfaces the problem — `steering.read` raises `SteeringError` naming
    the file, and it reaches `guard` from there — needs_human for that one
    launch, not a crash."""
    steering_dir = st.templates_dir / "steering"
    try:
        repos = config_mod.load_repos(repos_path(st), validate_steering=False)
    except config_mod.ConfigError as exc:
        logger.warning("repo config invalid, failing dispatch that reads it: %s", exc)
        return executor.LaunchContext(
            repo_entry=_PoisonedRepoEntry(exc), steering_dir=steering_dir, skills_dir=st.skills_dir
        )
    return executor.LaunchContext(
        repo_entry=_connected(repos, repo),
        steering_dir=steering_dir,
        skills_dir=st.skills_dir,
    )
