"""The auto-archive poller (UI v2 · 03)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from support.harness import fake_registry, make_repo

from kraft import archive, db, policy, store
from kraft.paths import RunDirs

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


@dataclass
class _Stub:
    state: SimpleNamespace


async def _stub(tmp_path, *, archive_after_days) -> _Stub:
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    registry = fake_registry(sys.executable, _FAKE_AGENT)
    return _Stub(
        state=SimpleNamespace(
            db=database,
            run_dirs=rd,
            registry=registry,
            templates_dir=tmp_path / "templates",
            skills_dir=tmp_path / "skills",
            policy=policy.Policy(
                loops={},
                default=policy.Cap(attempts=3, wall_clock_s=3600),
                archive_after_days=archive_after_days,
            ),
            tasks={},
        )
    )


async def _seed_completed_item(app, repo: Path, *, updated_days_ago: int, wid: str = "w1") -> Path:
    """A completed item with a real worktree, backdated `updated_at`."""
    branch = f"kraft/{wid}"
    worktree = app.state.run_dirs.worktrees / wid
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(worktree)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    updated_at = (datetime.now(UTC) - timedelta(days=updated_days_ago)).isoformat()

    def _write(c):
        store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo=str(repo),
            chain_template="quick-task",
            chain_definition="{}",
        )
        store.mark_completed(c, wid)
        c.execute("UPDATE work_items SET updated_at = ? WHERE id = ?", (updated_at, wid))

    await app.state.db.write(_write)
    return worktree


def _run(build, body):
    async def main():
        app = await build()
        try:
            return await body(app)
        finally:
            await app.state.db.close()

    return asyncio.run(main())


def test_tick_archives_a_completed_item_past_after_days(tmp_path):
    repo = make_repo(tmp_path)

    async def body(app):
        worktree = await _seed_completed_item(app, repo, updated_days_ago=31)

        archived = await archive.tick(app)

        assert archived == ["w1"]
        row = app.state.db.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
        )
        assert row["archived_by"] == "auto"
        assert not worktree.exists()

    _run(lambda: _stub(tmp_path, archive_after_days=30), body)


def test_tick_ignores_an_item_not_yet_due(tmp_path):
    repo = make_repo(tmp_path)

    async def body(app):
        await _seed_completed_item(app, repo, updated_days_ago=1)
        assert await archive.tick(app) == []

    _run(lambda: _stub(tmp_path, archive_after_days=30), body)


def test_tick_does_nothing_when_after_days_is_none_or_zero(tmp_path):
    for after_days in (None, 0):
        repo = make_repo(tmp_path, name=f"repo-{after_days}")
        wid = f"w-{after_days}"

        async def body(app, repo=repo, wid=wid):
            await _seed_completed_item(app, repo, updated_days_ago=999, wid=wid)
            assert await archive.tick(app) == []

        _run(
            lambda after_days=after_days: _stub(
                tmp_path / str(after_days), archive_after_days=after_days
            ),
            body,
        )
