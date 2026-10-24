"""What `run_task` does to hand a step to a machine: wait for a slot, describe the job."""

from __future__ import annotations

import subprocess
from dataclasses import asdict

from kraft import caps, ids, store
from kraft.remote import placement
from kraft.remote import runs as runs_mod
from kraft.remote.hub import PlacementRefused
from kraft.remote.jobs import JobSpec


async def claim(db, *, work_item_id, session_id, node_id, runner, repo_entry, sandbox, time_cap):
    """`(claim, refusal, time_cap)`. A refusal is text for the session's log; a wait is a
    cancellable `await` here, with `time_cap` moved on by however long it lasted."""
    runs = runs_mod.current()
    if runs is None:
        return None, f"runner {runner!r} needs machines, and this process has none", time_cap
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    repos = [repo_entry] if repo_entry is not None else []
    from kraft import builtins  # `builtins` imports adapters.subprocess

    problems = placement.filing_problems(
        [runner],
        workers=runs.hub.file,
        repos=repos,
        in_place=bool(row is not None and store.is_in_place(row)),
        sandboxed=sandbox is not None,
        repo_path=row["repo"] if row is not None else ".",
        members=builtins.item_mounts(row) if row is not None else [],
    )
    if problems:
        return None, problems[0], time_cap
    pinned = (
        db.read(lambda c: store.pinned_machine(c, work_item_id, node_id))
        if runner == placement.ANY
        else None
    )
    began = caps.monotonic()
    try:
        got = await runs.hub.claim(
            runner, repos, ids.session_ref(work_item_id, session_id), pinned=pinned
        )
    except PlacementRefused as exc:
        return None, str(exc), time_cap
    waited = caps.monotonic() - began
    if time_cap is not None and waited > 0.05:
        time_cap = caps.Deadline(time_cap.at + waited, time_cap.hit)
    return got, None, time_cap


def build(
    *,
    work_item_id,
    session_id,
    node_id,
    hook_point,
    cmd,
    cwd,
    env,
    repo_entry,
    branch,
    base,
    identity,
    run_dirs,
    entries,
    time_cap: caps.Deadline | None,
    idle_s: float | None,
    sandbox: dict | None = None,
    egress: dict | None = None,
    credentials: list[dict] | None = None,
    sentinels: dict[str, str] | None = None,
    withheld: list[str] | None = None,
) -> JobSpec:
    done = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"branch {branch} is not in {cwd}, so there is nothing to send to a machine"
        )
    rw = [e for e in entries if e.rw]
    return JobSpec(
        work_item_id=work_item_id,
        session_id=session_id,
        node_id=node_id,
        hook_point=hook_point,
        argv=list(cmd),
        cwd=str(cwd),
        env=dict(env),
        repo_env=dict(repo_entry.env) if repo_entry is not None else {},
        passthrough=list(repo_entry.env_passthrough) if repo_entry is not None else [],
        setup_command=repo_entry.setup_command if repo_entry is not None else None,
        branch=branch,
        head=done.stdout.strip(),
        base=base or done.stdout.strip(),
        identity=dict(identity),
        server_run_dir=str(run_dirs.base),
        server_worktree=str(cwd),
        dirs=[str(e.path) for e in rw if e.path.is_dir()],
        rw=[str(e.path) for e in rw],
        # Enforced on the machine as well: while the server is asleep nothing else would.
        deadline_s=None if time_cap is None else max(0.0, time_cap.at - caps.monotonic()),
        cap=None if time_cap is None else asdict(time_cap.hit),
        idle_s=idle_s,
        sandbox=sandbox,
        egress=egress,
        credentials=list(credentials or []),
        sentinels=dict(sentinels or {}),
        withheld=list(withheld or []),
    )
