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
from kraft import executor, store
from kraft.templates import load_registry, load_templates

logger = logging.getLogger(__name__)


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


def spawn(app: FastAPI, wid: str, coro) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    app.state.tasks[wid] = task
    task.add_done_callback(lambda _t, wid=wid: app.state.tasks.pop(wid, None))
    return task


def _work_item_row(st, wid):
    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown work item")
    return row


def _reload_templates(st) -> None:
    st.registry = load_registry(st.templates_dir / "registry.yaml", skills_dir=st.skills_dir)
    st.templates = load_templates(st.templates_dir, st.registry)


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


def launch(st, repo: str) -> executor.LaunchContext:
    """The repo config for one agent dispatch, degrading like `invalid_policy`
    rather than raising: a malformed `repos.yaml` must not crash `lifespan` on
    reattach (locking an operator out of the Settings UI that would let them
    fix it) or 500 the approve/reject/resume/retry routes — the agent launches
    without repo-level model/steering, which is today's behaviour anyway.

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
        logger.warning("repo config invalid, launching without it: %s", exc)
        return executor.LaunchContext(
            repo_entry=None, steering_dir=steering_dir, skills_dir=st.skills_dir
        )
    return executor.LaunchContext(
        repo_entry=_connected(repos, repo),
        steering_dir=steering_dir,
        skills_dir=st.skills_dir,
    )
