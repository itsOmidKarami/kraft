"""`run_chain`: one work item, filed and walked to its end, for the fix-loop suites."""

from __future__ import annotations

import asyncio
from pathlib import Path

from support.harness import isolated_bd, make_repo


def run_chain(workdir: Path, chain, *, policy, title: str = "t") -> dict:
    """File `chain` as one work item under `workdir/run` and walk it with
    `executor.run` -- every node, fix loops included, not `v1_walk`'s single
    `run_once` pass -- on a fresh repo and bd workspace under `workdir`.
    Returns `wid`, `result`, `events` and `sessions` (every worker session,
    oldest first) as plain dicts, and `row`, with the database already closed.

    Env the fake agent reads (`KRAFT_FAKE_AGENT*`) is the caller's to set first.
    """
    from kraft import db, events, executor
    from kraft.paths import RunDirs

    tracker = isolated_bd(workdir)
    repo = make_repo(workdir)

    async def go():
        rd = RunDirs(workdir / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title=title, repo=str(repo), chain=chain, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database, rd, work_item_id=wid, bd_cwd=str(tracker), policy=policy
            )
            return {
                "wid": wid,
                "result": result,
                "events": [dict(e) for e in database.read(lambda c: events.read_after(c, 0, wid))],
                "sessions": [
                    dict(r)
                    for r in database.read(
                        lambda c: c.execute(
                            "SELECT * FROM worker_sessions WHERE work_item_id = ? ORDER BY rowid",
                            (wid,),
                        ).fetchall()
                    )
                ],
                "row": dict(
                    database.read(
                        lambda c: c.execute(
                            "SELECT * FROM work_items WHERE id = ?", (wid,)
                        ).fetchone()
                    )
                ),
            }
        finally:
            await database.close()

    return asyncio.run(go())


def loop_policy(workdir: Path, *loops: str, attempts: int = 3, wall_clock_s: int = 3600, extra=""):
    """A policy.yaml under `workdir` capping each of `loops` (e.g.
    `"verify.fix_loop"`) and the default at `attempts`/`wall_clock_s`, loaded.

    `auto_escalate_stuck` is off: these suites are about a loop's own cap, not
    the unrelated auto-escalate trigger a `needs_human` breach would otherwise
    also fire (Kraft-lpdd). `extra` is appended YAML.
    """
    from kraft import policy

    cap = f"{{ attempts: {attempts}, wall_clock_s: {wall_clock_s} }}"
    path = workdir / "policy.yaml"
    path.write_text(
        "loops:\n"
        + "".join(f"  {loop}: {cap}\n" for loop in loops)
        + f"default: {cap}\nauto_escalate_stuck: false\n"
        + extra
    )
    return policy.load_policy(path)
