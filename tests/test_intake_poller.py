"""Auto-intake picks backlog beads up; it never passes a gate (spec §4-§5)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo, v1_library

from kraft import config, db, policy, store
from kraft import intake as intake_mod
from kraft.paths import RunDirs
from kraft.templates import load_registry, load_templates


def _ready(rows, *, seen: list | None = None):
    """A stand-in for `beads.ready`: the poller's scheduling logic is what is
    under test, and `test_adapters_beads.py` already covers the passthrough."""

    async def ready(*, cwd=None):
        if seen is not None:
            seen.append(cwd)
        return [dict(r) for r in rows]

    return ready


@dataclass
class _Stub:
    """What `intake.tick` reads off `app.state`, and nothing else.

    Not a TestClient: `tick` makes a scheduling decision, and driving a whole
    server to observe one is slower and hides which of these it actually used.
    """

    state: SimpleNamespace


async def _stub(
    tmp_path, *, repo_entry=None, budget=policy.NO_BUDGET, max_concurrent=3, **intake_overrides
) -> _Stub:
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    templates_dir = fake_templates_dir(tmp_path, sys.executable)
    repo = make_repo(tmp_path)
    entry = {"path": str(repo), "enabled": True, "default_chain_template": "default"}
    entry.update(repo_entry or {})
    (templates_dir / "repos.yaml").write_text(yaml.safe_dump({"repos": [entry]}))
    registry = load_registry(templates_dir / "registry.yaml")
    return _Stub(
        state=SimpleNamespace(
            db=database,
            run_dirs=rd,
            registry=registry,
            templates=load_templates(templates_dir, registry),
            library=v1_library(templates_dir),
            templates_dir=templates_dir,
            skills_dir=tmp_path / "skills",
            policy=policy.Policy(
                loops={},
                default=policy.Cap(attempts=3, wall_clock_s=3600),
                budget=budget,
                max_concurrent=max_concurrent,
            ),
            invalid_policy=[],
            instance_policy=policy.InstancePolicy.from_input(policy.InstancePolicyInput()),
            intake={**config.INTAKE_DEFAULT, "enabled": True, **intake_overrides},
            tasks={},
        )
    )


def _work_items(app) -> list[dict]:
    rows = app.state.db.read(
        lambda c: c.execute(
            "SELECT id, bead_id, title, description, status FROM work_items"
        ).fetchall()
    )
    return [dict(r) for r in rows]


async def _file(app, *, bead_id: str, status: str) -> str:
    """A pre-existing work item, as a person typing one in would have left it."""
    wid = uuid.uuid4().hex
    await app.state.db.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=bead_id,
            title="already here",
            repo="/repo",
            chain_template="default",
            chain_definition="{}",
            status=status,
        )
    )
    return wid


async def _spend(app, usd: float) -> None:
    """A finished session that cost `usd`, as usage capture would have left it.

    Sessions are foreign-keyed to a work item, so this parks one in needs_human:
    finished, and not occupying a `max_concurrent` slot.
    """
    wid = await _file(app, bead_id="SPENT-1", status="needs_human")
    sid = uuid.uuid4().hex
    await app.state.db.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id=wid,
            node_id="prior",
            hook_point="on.implementation.start",
            log_path="/tmp/l",
            result_path="/tmp/r",
        )
    )
    await app.state.db.write(
        lambda c: c.execute(
            "UPDATE worker_sessions SET cost_usd = ?, status = 'done' WHERE id = ?", (usd, sid)
        )
    )


@pytest.fixture(autouse=True)
def _no_agent_launch(request, monkeypatch):
    """`tick` starts real chains; nothing here wants an agent process.

    Except the §5 regression test, whose entire claim is about what the *real*
    executor does with an auto-started item — it opts out with `real_executor`.
    """
    if "real_executor" in request.keywords:
        return

    async def run(*a, **k):
        return None

    monkeypatch.setattr(intake_mod.executor, "run", run)


def _run(build, body):
    """Build a stub, run `body(app)` against it, always close the database."""

    async def main():
        app = await build()
        try:
            return await body(app)
        finally:
            # Let the spawned chains finish rather than cancelling them: with
            # `executor.run` stubbed they return immediately, and a task
            # cancelled before it starts leaves its inner coroutine un-awaited.
            await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
            await app.state.db.close()

    return asyncio.run(main())


def test_disabled_by_default_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "t", "priority": 3}])
    )

    async def body(app):
        assert await intake_mod.tick(app) == []
        assert _work_items(app) == []

    _run(lambda: _stub(tmp_path, enabled=False), body)


def test_starts_one_bead_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "pick me up", "priority": 3}])
    )

    async def body(app):
        started = await intake_mod.tick(app)
        assert len(started) == 1
        rows = _work_items(app)
        assert [(r["id"], r["bead_id"], r["title"]) for r in rows] == [
            (started[0], "B-1", "pick me up")
        ]

    _run(lambda: _stub(tmp_path), body)


def test_a_started_pickup_is_recorded_with_source_and_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "pick me up", "priority": 3}])
    )

    async def body(app):
        started = await intake_mod.tick(app)
        assert len(started) == 1
        pickups = app.state.db.read(store.recent_auto_pickups)
        assert len(pickups) == 1
        assert pickups[0]["priority"] == 3
        last = app.state.db.read(store.last_auto_pickup_at)
        assert last  # at least one repo recorded

    _run(lambda: _stub(tmp_path), body)


def test_auto_intake_carries_the_beads_description(tmp_path, monkeypatch):
    """The bead already carries the brief its author wrote. Auto-intake is the one
    path with no human present to notice it being dropped."""
    monkeypatch.setattr(
        intake_mod.beads,
        "ready",
        _ready([{"id": "B-1", "title": "pick me up", "priority": 3, "description": "the brief"}]),
    )

    async def body(app):
        started = await intake_mod.tick(app)
        assert len(started) == 1
        assert [r["description"] for r in _work_items(app)] == ["the brief"]

    _run(lambda: _stub(tmp_path), body)


def test_respects_max_concurrent_counting_every_active_item(tmp_path, monkeypatch):
    """A person working on one thing must not find the poller adding a second."""
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "t", "priority": 3}])
    )

    async def body(app):
        await _file(app, bead_id="HAND-1", status="active")
        assert await intake_mod.tick(app) == []
        assert [r["bead_id"] for r in _work_items(app)] == ["HAND-1"]

    _run(lambda: _stub(tmp_path, max_concurrent=1), body)


def test_a_waiting_item_does_not_hold_an_intake_slot(tmp_path, monkeypatch):
    """Kraft-g15w: three slow pipelines used to stall auto-intake for the full
    poll_timeout. A waiting row is not an active one, so the slot is free.

    Pinned deliberately even though no production line implements it: the fix is
    a consequence of the status, and `active_count`'s query says nothing about
    waiting -- a later edit could re-stall intake without touching anything that
    looks related to this bead.
    """
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "t", "priority": 3}])
    )

    async def body(app):
        wid = await _file(app, bead_id="HAND-1", status="active")
        await app.state.db.write(
            lambda c: store.mark_waiting(c, wid, "mr_checks", "2099-01-01T00:00:00+00:00")
        )
        assert app.state.db.read(lambda c: store.active_count(c)) == 0
        started = await intake_mod.tick(app)
        assert len(started) == 1
        assert sorted(r["bead_id"] for r in _work_items(app)) == ["B-1", "HAND-1"]

    _run(lambda: _stub(tmp_path, max_concurrent=1), body)


def test_skips_a_bead_that_already_has_a_work_item(tmp_path, monkeypatch):
    monkeypatch.setattr(
        intake_mod.beads,
        "ready",
        _ready(
            [
                {"id": "B-1", "title": "t", "priority": 3},
                {"id": "B-2", "title": "u", "priority": 3},
            ]
        ),
    )

    async def body(app):
        # needs_human, so it is not active and does not consume a slot either.
        await _file(app, bead_id="B-1", status="needs_human")
        started = await intake_mod.tick(app)
        assert len(started) == 1
        assert sorted(r["bead_id"] for r in _work_items(app)) == ["B-1", "B-2"]

    _run(lambda: _stub(tmp_path, max_concurrent=5), body)


def test_skips_a_bead_above_the_priority_ceiling(tmp_path, monkeypatch):
    """P0 is the highest priority, so `priority_ceiling: 2` keeps P2 and below."""
    monkeypatch.setattr(
        intake_mod.beads,
        "ready",
        _ready(
            [
                {"id": "B-URGENT", "title": "urgent", "priority": 1},
                {"id": "B-BACKLOG", "title": "backlog", "priority": 2},
            ]
        ),
    )

    async def body(app):
        started = await intake_mod.tick(app)
        assert len(started) == 1
        assert [r["bead_id"] for r in _work_items(app)] == ["B-BACKLOG"]

    _run(lambda: _stub(tmp_path, max_concurrent=5, priority_ceiling=2), body)


def test_does_not_run_while_the_daily_budget_is_breached(tmp_path, monkeypatch):
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "t", "priority": 3}])
    )

    async def body(app):
        await _spend(app, 5.0)
        assert await intake_mod.tick(app) == []
        assert [r["bead_id"] for r in _work_items(app)] == ["SPENT-1"]

    _run(lambda: _stub(tmp_path, budget=policy.Budget(daily_usd=1.0)), body)


def test_starts_when_the_daily_budget_is_not_reached(tmp_path, monkeypatch):
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "t", "priority": 3}])
    )

    async def body(app):
        await _spend(app, 0.25)
        assert len(await intake_mod.tick(app)) == 1

    _run(lambda: _stub(tmp_path, budget=policy.Budget(daily_usd=1.0)), body)


def test_skips_a_repo_that_is_not_enabled(tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        intake_mod.beads,
        "ready",
        _ready([{"id": "B-1", "title": "t", "priority": 3}], seen=seen),
    )

    async def body(app):
        assert await intake_mod.tick(app) == []
        assert seen == []

    _run(lambda: _stub(tmp_path, repo_entry={"enabled": False}), body)


def test_refuses_a_template_with_no_gate(tmp_path, monkeypatch):
    """Spec §5 reached by configuration: a chain that gates nowhere would take a
    bead to merge unattended with no human anywhere."""
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "t", "priority": 3}])
    )

    # A real, resolvable, gateless chain -- written before `_stub` builds the
    # library out of this directory. Pointing the repo at a chain id that does
    # not exist would make this pass on the *unknown template* branch instead,
    # which is a different refusal and would leave the gate rule unpinned.
    chains = tmp_path / "templates" / "chains"
    chains.mkdir(parents=True, exist_ok=True)
    (chains / "gateless.yaml").write_text(
        "id: gateless\n"
        "nodes:\n"
        "  - id: implementation\n"
        "    kind: exec\n"
        "    tasks:\n"
        "      - { id: build, kind: subprocess, command: 'true' }\n"
    )

    async def body(app):
        assert app.state.library.resolve_chain("gateless"), "the fixture chain must resolve"
        assert await intake_mod.tick(app) == []
        assert _work_items(app) == []

    _run(lambda: _stub(tmp_path, repo_entry={"default_chain_template": "gateless"}), body)


def test_skips_an_epic(tmp_path, monkeypatch):
    """An epic is a container for work, not work: its title describes a quarter."""
    monkeypatch.setattr(
        intake_mod.beads,
        "ready",
        _ready(
            [
                {"id": "B-EPIC", "title": "Q3", "priority": 3, "issue_type": "epic"},
                {"id": "B-TASK", "title": "t", "priority": 3, "issue_type": "task"},
            ]
        ),
    )

    async def body(app):
        assert len(await intake_mod.tick(app)) == 1
        assert [r["bead_id"] for r in _work_items(app)] == ["B-TASK"]

    _run(lambda: _stub(tmp_path, max_concurrent=5), body)


def test_an_auto_started_item_is_left_at_its_first_gate(tmp_path, monkeypatch):
    """§5: auto-intake removes the typing, not the judgement. The chain it files
    is the template's own, gates intact — nothing is pre-satisfied."""
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "t", "priority": 3}])
    )

    async def body(app):
        (wid,) = await intake_mod.tick(app)
        row = app.state.db.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        )
        # The V1 snapshot, not `chain_definition`: `materialized_chain` is the
        # executor's only input, and an auto-intaken item is materialized from
        # the library chain with nothing satisfied in advance.
        nodes = store.materialized_chain_of(row).chain.chain.nodes
        gates = [n.id for n in nodes if n.kind == "gate"]
        assert gates, "the filed chain has no gate at all; §5 has nothing to stop at"
        assert gates[0] == "spec_approval"

    _run(lambda: _stub(tmp_path), body)


def test_a_failing_tick_does_not_end_the_poller(tmp_path, monkeypatch):
    """A poller that dies stops picking work up, silently and until a restart."""
    ticks = 0

    async def boom(app):
        nonlocal ticks
        ticks += 1
        raise RuntimeError("bad tick")

    async def sleep(_s):
        if ticks >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(intake_mod, "tick", boom)
    monkeypatch.setattr(intake_mod.asyncio, "sleep", sleep)

    async def body(app):
        with pytest.raises(asyncio.CancelledError):
            await intake_mod.poller(app)
        assert ticks == 2

    _run(lambda: _stub(tmp_path), body)


def test_unreadable_intake_yaml_raises_configerror(tmp_path):
    """A config file with invalid bytes must degrade like any other broken one.

    `read_text()` raises `UnicodeDecodeError` (a `ValueError`), which is not an
    `OSError` and not a `yaml.YAMLError`, so before this it escaped `read_yaml`
    uncaught and crashed startup instead of being reported as bad config.
    """
    path = tmp_path / "intake.yaml"
    path.write_bytes(b"enabled: \xff\xfe\n")
    with pytest.raises(config.ConfigError):
        config.load_intake(path)


def test_a_nonsense_interval_does_not_kill_the_poller(tmp_path, monkeypatch):
    """`intake.yaml` is hand-edited and unvalidated; a typo in it must not leave
    an enabled poller that silently never ticks."""
    slept: list = []

    async def sleep(s):
        slept.append(s)
        raise asyncio.CancelledError

    monkeypatch.setattr(intake_mod.asyncio, "sleep", sleep)

    async def body(app):
        with pytest.raises(asyncio.CancelledError):
            await intake_mod.poller(app)
        assert slept == [config.INTAKE_DEFAULT["interval_s"]]

    _run(lambda: _stub(tmp_path, interval_s="five minutes"), body)


_FAKE_CLAUDE = Path(__file__).resolve().parents[1] / "fixtures" / "fake-claude.sh"


def _poll_for(client, wid, event_type, timeout=30):
    """Wait for an event type to land. The executor runs in a background task, so
    a status read straight after the tick races it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matching = [
            e for e in client.get(f"/api/work-items/{wid}/events").json() if e["type"] == event_type
        ]
        if matching:
            return matching
        time.sleep(0.2)
    raise AssertionError(f"{event_type} never arrived for {wid}")


@pytest.mark.real_executor
def test_an_auto_started_item_stops_at_its_first_gate(tmp_path, monkeypatch):
    """The line auto-intake must not cross (spec §5).

    Kraft's whole differentiation is bounded autonomy with a human at the gates.
    An auto-start that also auto-approved would be the Auto-Company class of tool
    with worse marketing. If this test is ever failing, the feature is wrong, not
    the test.

    Deliberately a level above `test_an_auto_started_item_is_left_at_its_first_gate`:
    that one asserts the *stored chain* kept its gates, against a stubbed executor.
    This one runs the real executor over a real `bd ready` through a `TestClient`
    and asserts the item actually stopped.
    """
    # The repo has to be both a source tree and a beads workspace: `make_repo`
    # gives no `.beads` (so real `bd ready` would answer `[]` and this test would
    # pass having started nothing), and `isolated_bd` gives no source.
    repo = make_repo(tmp_path)
    subprocess.run(
        ["bd", "init", "--prefix", "TEST"], cwd=repo, capture_output=True, text=True, check=True
    )
    bead_id = subprocess.run(
        ["bd", "create", "pick this up unattended", "-p", "3", "--silent"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert bead_id, "bd create printed no id"

    # `fake_templates_dir` writes no repos.yaml and no intake.yaml, and `lifespan`
    # reads both at startup — so they are written before the client is entered.
    templates_dir = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    # `default`, not `quick-task`: quick-task has no gate at all, and `_start`
    # now refuses it, so using it here would test the refusal instead of the gate.
    (templates_dir / "repos.yaml").write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {
                        "path": str(repo),
                        "enabled": True,
                        "default_chain_template": "default",
                        "setup_command": "",
                    }
                ]
            }
        )
    )
    # interval_s is long so the lifespan poller never ticks on its own; the test
    # drives `tick` itself.
    (templates_dir / "intake.yaml").write_text(
        yaml.safe_dump({"enabled": True, "interval_s": 3600, "max_concurrent": 1})
    )

    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(repo))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    # `default_harnesses_dir()` is `kraft_home()/templates/harnesses`, which is
    # where `fake_templates_dir` put the overlaid `fake` harness every V1 agent
    # task in the fixture library selects. Without this the first node would
    # try to launch a real agent CLI.
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)) as client:
        # `tick` is a coroutine and the chains it spawns are tasks on the app's
        # own loop; `portal.call` is how a sync test reaches that loop.
        started = client.portal.call(intake_mod.tick, client.app)
        assert len(started) == 1, f"the poller started {started!r}, expected exactly one item"
        (wid,) = started

        gates = _poll_for(client, wid, "gate_requested")

        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert item["bead_id"] == bead_id, "auto-intake filed a duplicate bead instead of adopting"

        evts = client.get(f"/api/work-items/{wid}/events").json()
        assert [e for e in evts if e["type"] == "gate_approved"] == [], (
            "an auto-started item passed a gate with no human — spec §5"
        )

        # The V1 snapshot, not `chain_definition`: the executor's only input.
        nodes = [n["id"] for n in json.loads(item["materialized_chain"])["chain"]["nodes"]]
        gate_index = nodes.index(gates[0]["payload"]["node_id"])
        completed = {e["payload"]["node_id"] for e in evts if e["type"] == "node_completed"}
        assert not completed & set(nodes[gate_index + 1 :]), (
            f"the chain ran past its gate: completed {sorted(completed)}"
        )


def test_a_malformed_intake_yaml_still_boots_with_intake_off(tmp_path, monkeypatch):
    """`intake.yaml` has no Settings screen by design (spec amendment A9), so a
    hand-edit typo in it has no UI to fix it from — if it stopped the server from
    starting, there would be no way back in. Off is the safe degradation and the
    default anyway."""
    templates_dir = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates_dir / "intake.yaml").write_text("enabled: [unclosed\n")

    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)) as client:
        assert client.get("/api/work-items").status_code == 200
        assert client.app.state.intake["enabled"] is False
        # Not merely "the poller ticked nothing" — no poller task exists at all.
        # Without this, deleting lifespan's `if enabled` condition leaves the
        # whole suite green while a disabled instance runs a live 300s timer.
        assert client.app.state.intake_task is None


def test_a_repos_filter_that_matches_nothing_says_so(tmp_path, caplog):
    """`repos.yaml` paths are never normalised, so a `~` or a trailing slash in
    `intake.yaml`'s filter matches nothing. Without the warning that is a poller
    ticking forever, picking nothing up, saying nothing."""

    async def body(app):
        with caplog.at_level("WARNING", logger="kraft.intake"):
            assert await intake_mod.tick(app) == []
        assert "matched no configured repo" in caplog.text

    _run(lambda: _stub(tmp_path, repos=["~/code/nowhere"]), body)


def test_an_auto_intaken_bead_records_the_repo_as_its_bd_workspace(tmp_path, monkeypatch):
    """The bead is adopted from the repo's own `.beads`, never filed into the
    instance-wide `KRAFT_BD_CWD`, so closing it has to happen there (Kraft-8mu.5.2).

    Asserted on the poller's own path: passing `bead_cwd` straight to
    `executor.intake` proves the plumbing but not that this caller uses it.
    """
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "pick me up", "priority": 3}])
    )

    async def body(app):
        started = await intake_mod.tick(app)
        assert len(started) == 1
        row = app.state.db.read(
            lambda c: c.execute(
                "SELECT repo, bead_cwd FROM work_items WHERE id = ?", (started[0],)
            ).fetchone()
        )
        assert row["bead_cwd"] == row["repo"]

    _run(lambda: _stub(tmp_path), body)
