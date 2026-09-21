"""The trigger poller fires a paused work item on a due cron entry."""

from __future__ import annotations

from datetime import UTC, datetime

from support.harness import isolated_bd, v1_library

from kraft import policy, triggers


def _state(tmp_path, *, policy_obj) -> dict:
    """What `triggers.tick` reads off `app.state` besides the database (`stub_app`)."""
    return {
        # The seeded V1 library: `triggers.tick` resolves a trigger's chain
        # through it (`deps.resolve_chain`), never a template set.
        "library": v1_library(tmp_path / "templates"),
        "policy": policy_obj,
        "trigger_last_fired": {},
    }


async def test_tick_ignores_a_not_yet_due_trigger(tmp_path, stub_app):
    repo = isolated_bd(tmp_path)  # a real bd workspace, so intake's `bd create` succeeds
    pol = policy.Policy(
        loops={},
        default=policy.Cap(attempts=3, wall_clock_s=3600),
        triggers=[policy.Trigger(cron="0 0 1 1 *", repo=str(repo), chain="default", title="t")],
    )

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    filed = await triggers.tick(app, now=datetime(2026, 9, 10, 14, 30, tzinfo=UTC))
    assert filed == []


async def test_tick_files_a_paused_item_on_a_due_trigger(tmp_path, stub_app):
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

    app = stub_app(**_state(tmp_path, policy_obj=pol))
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


async def test_tick_is_a_noop_with_no_policy(tmp_path, stub_app):
    app = stub_app(**_state(tmp_path, policy_obj=None))
    assert await triggers.tick(app, now=datetime(2026, 9, 10, 14, 30, tzinfo=UTC)) == []


async def test_a_trigger_whose_chain_exceeds_the_ceiling_is_skipped_not_the_whole_tick(
    tmp_path, monkeypatch, stub_app
):
    """Kraft-ib2af: one trigger refused at intake is logged and skipped; the
    triggers after it in the same tick still file."""
    from kraft import executor

    repo = isolated_bd(tmp_path)
    pol = policy.Policy(
        loops={},
        default=policy.Cap(attempts=3, wall_clock_s=3600),
        triggers=[
            policy.Trigger(cron="30 14 * * *", repo=str(repo), chain="default", title="refused"),
            policy.Trigger(cron="30 14 * * *", repo=str(repo), chain="default", title="filed"),
        ],
    )
    real_intake = executor.intake

    async def intake(*a, **kw):
        if kw["title"] == "refused":
            raise policy.PolicyError("'allowed_tools' cannot widen the inherited safety ceiling")
        return await real_intake(*a, **kw)

    monkeypatch.setattr(triggers.executor, "intake", intake)

    app = stub_app(**_state(tmp_path, policy_obj=pol))
    filed = await triggers.tick(app, now=datetime(2026, 9, 10, 14, 30, tzinfo=UTC))
    assert len(filed) == 1
    titles = app.state.db.read(
        lambda c: [r["title"] for r in c.execute("SELECT title FROM work_items")]
    )
    assert titles == ["filed"]
