"""The trigger poller fires a paused work item on a due cron entry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

from support.harness import isolated_bd, v1_library

from kraft import db, policy, triggers
from kraft.paths import RunDirs


@dataclass
class _Stub:
    state: SimpleNamespace


async def _stub(tmp_path, *, policy_obj) -> _Stub:
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    return _Stub(
        state=SimpleNamespace(
            db=database,
            run_dirs=rd,
            # The seeded V1 library: `triggers.tick` resolves a trigger's
            # chain through it (`deps.resolve_chain`), never a template set.
            library=v1_library(tmp_path / "templates"),
            policy=policy_obj,
            trigger_last_fired={},
            tasks={},
        )
    )


def _run(build, body):
    async def main():
        app = await build()
        try:
            return await body(app)
        finally:
            await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
            await app.state.db.close()

    return asyncio.run(main())


def test_tick_ignores_a_not_yet_due_trigger(tmp_path):
    repo = isolated_bd(tmp_path)  # a real bd workspace, so intake's `bd create` succeeds
    pol = policy.Policy(
        loops={},
        default=policy.Cap(attempts=3, wall_clock_s=3600),
        triggers=[policy.Trigger(cron="0 0 1 1 *", repo=str(repo), chain="default", title="t")],
    )

    async def body(app):
        filed = await triggers.tick(app, now=datetime(2026, 9, 10, 14, 30, tzinfo=UTC))
        assert filed == []

    _run(lambda: _stub(tmp_path, policy_obj=pol), body)


def test_tick_files_a_paused_item_on_a_due_trigger(tmp_path):
    repo = isolated_bd(tmp_path)
    pol = policy.Policy(
        loops={},
        default=policy.Cap(attempts=3, wall_clock_s=3600),
        triggers=[
            policy.Trigger(
                cron="30 14 * * *",
                repo=str(repo),
                chain="default",
                title="Nightly sweep",
                description="d",
            )
        ],
    )

    async def body(app):
        now = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)
        filed = await triggers.tick(app, now=now)
        assert len(filed) == 1
        row = app.state.db.read(
            lambda c: c.execute(
                "SELECT status, title FROM work_items WHERE id=?", (filed[0],)
            ).fetchone()
        )
        assert row["status"] == "paused"
        assert row["title"] == "Nightly sweep"

        # a second tick in the same minute must not file a second item
        assert await triggers.tick(app, now=now) == []

    _run(lambda: _stub(tmp_path, policy_obj=pol), body)


def test_tick_is_a_noop_with_no_policy(tmp_path):
    async def body(app):
        assert await triggers.tick(app, now=datetime(2026, 9, 10, 14, 30, tzinfo=UTC)) == []

    _run(lambda: _stub(tmp_path, policy_obj=None), body)
