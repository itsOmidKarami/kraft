"""The rate-limit poller relaunches a due item automatically."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, policy, rate_limit_retry, store
from kraft.paths import RunDirs

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


@dataclass
class _Stub:
    state: SimpleNamespace


async def _stub(tmp_path, *, rate_limit_retries: int = 5) -> _Stub:
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    registry = fake_registry(sys.executable, _FAKE_AGENT)
    return _Stub(
        state=SimpleNamespace(
            db=database,
            run_dirs=rd,
            registry=registry,
            templates_dir=tmp_path / "templates",  # no repos.yaml: _launch degrades cleanly
            skills_dir=tmp_path / "skills",
            policy=policy.Policy(
                loops={},
                default=policy.Cap(attempts=3, wall_clock_s=3600),
                rate_limit_retries=rate_limit_retries,
            ),
            tasks={},
        )
    )


_CHAIN = (
    '{"template_id": "quick-task", "nodes": ['
    '{"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": null},'
    '{"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": null},'
    '{"id": "verify", "tasks": ["on.test.run"], "gate_after": null}]}'
)


async def _seed_rate_limited(app, *, retry_at: str, repo: str, wid: str = "w1") -> None:
    await app.state.db.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo=repo,
            chain_template="quick-task",
            chain_definition=_CHAIN,
        )
    )
    await app.state.db.write(lambda c: store.enter_node(c, wid, "implementation"))
    await app.state.db.write(lambda c: store.mark_rate_limited(c, wid, "implementation", retry_at))


def _run(build, body):
    async def main():
        app = await build()
        try:
            return await body(app)
        finally:
            await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
            await app.state.db.close()

    return asyncio.run(main())


def test_tick_ignores_a_not_yet_due_item(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)

    async def body(app):
        await _seed_rate_limited(app, retry_at="2999-01-01T00:00:00+00:00", repo=str(repo))
        assert await rate_limit_retry.tick(app) == []
        row = app.state.db.read(
            lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
        )
        assert row["status"] == "rate_limited"

    _run(lambda: _stub(tmp_path), body)


def test_tick_relaunches_a_due_item(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")  # leaves the calc bug in place, on purpose
    isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def body(app):
        await _seed_rate_limited(app, retry_at="2000-01-01T00:00:00+00:00", repo=str(repo))
        got = await rate_limit_retry.tick(app)
        assert got == ["w1"]
        await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)

        row = app.state.db.read(
            lambda c: c.execute("SELECT status, retry_at FROM work_items WHERE id='w1'").fetchone()
        )
        # relaunched past `implementation`; verify's fake `on.test.run` subprocess
        # then fails (KRAFT_FAKE_AGENT=noop leaves the calc bug), landing needs_human
        assert row["status"] == "needs_human"
        assert row["retry_at"] is None

        types = {e["type"] for e in app.state.db.read(lambda c: events.read_after(c, 0, "w1"))}
        assert "work_item_retried" in types
        ev = [
            e["payload"]
            for e in app.state.db.read(lambda c: events.read_after(c, 0, "w1"))
            if e["type"] == "work_item_retried"
        ][0]
        assert ev["steer"] == rate_limit_retry.RESUME_PROMPT

    _run(lambda: _stub(tmp_path), body)


def test_tick_falls_back_to_needs_human_once_the_cap_breaches(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)

    async def body(app):
        await _seed_rate_limited(app, retry_at="2000-01-01T00:00:00+00:00", repo=str(repo))
        cap = policy.Cap(attempts=1, wall_clock_s=10**9)
        await app.state.db.write(
            lambda c: store.bump_counter(c, "w1", "rate_limit:implementation", cap)
        )  # count now 1, == attempts: one more bump breaches

        got = await rate_limit_retry.tick(app)
        assert got == []
        row = app.state.db.read(
            lambda c: c.execute("SELECT status, retry_at FROM work_items WHERE id='w1'").fetchone()
        )
        assert row["status"] == "needs_human"
        assert row["retry_at"] is None
        assert app.state.tasks == {}  # nothing was relaunched

    _run(lambda: _stub(tmp_path, rate_limit_retries=1), body)
