"""The trigger poller fires a paused work item on a due cron entry."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from support.harness import isolated_bd, v1_library

from kraft import policy, triggers


def _state(tmp_path, *, policy_obj) -> dict:
    """What `triggers.tick` reads off `app.state` besides the database (`stub_app`)."""
    return {
        # The seeded V1 library: `triggers.tick` resolves a trigger's chain
        # through it (`deps.resolve_chain`), never a template set.
        "library": v1_library(tmp_path / "templates"),
        # Where intake reads the repository policy layer (`repos.yaml`).
        "templates_dir": tmp_path / "templates",
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


async def test_a_triggered_item_is_bound_by_its_repositorys_policy(tmp_path, stub_app):
    """`repository-policy-cannot-relax-instance-safety` at the cron door: the
    repository layer is frozen into what the trigger files."""
    import yaml

    from kraft import store

    repo = isolated_bd(tmp_path)
    pol = policy.Policy(
        loops={},
        default=policy.Cap(attempts=3, wall_clock_s=3600),
        triggers=[policy.Trigger(cron="30 14 * * *", repo=str(repo), chain="default", title="t")],
    )
    app = stub_app(**_state(tmp_path, policy_obj=pol))
    (tmp_path / "templates" / "repos.yaml").write_text(
        yaml.safe_dump({"repos": [{"path": str(repo), "deny_tools": ["WebFetch"]}]})
    )

    (wid,) = await triggers.tick(app, now=datetime(2026, 9, 10, 14, 30, tzinfo=UTC))

    row = app.state.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id=?", (wid,)).fetchone()
    )
    assert store.materialized_chain_of(row).policy.deny_tools == ("WebFetch",)


@pytest.fixture
def fast_trigger_poller(monkeypatch):
    """Ticks every 10 ms and counts them; set before `client` starts the app."""
    ticks = []

    async def counting_tick(app, **_):
        ticks.append(len(app.state.policy.triggers))
        return []

    monkeypatch.setattr(triggers, "_INTERVAL_S", 0.01)
    monkeypatch.setattr(triggers, "tick", counting_tick)
    return ticks


def test_the_poller_runs_with_no_triggers_at_boot(fast_trigger_poller, client):
    """Kraft-ygnw6: a trigger added later (PUT /policy, `kraft admin reload`)
    is read by the next tick -- only if a poller is ticking. One that started
    only for a boot-time trigger never fires a trigger added after it."""
    assert client.app.state.policy.triggers == []
    deadline = time.monotonic() + 5
    while not fast_trigger_poller and time.monotonic() < deadline:
        time.sleep(0.02)
    assert fast_trigger_poller, "no trigger poller is running"
