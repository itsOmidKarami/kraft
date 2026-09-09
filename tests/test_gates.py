import asyncio
import json
import subprocess
import sys
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, policy, store
from kraft.paths import RunDirs
from kraft.templates import load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _default_template():
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["default"]


def _events(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def _payloads(database, wid, etype):
    return [
        e["payload"]
        for e in database.read(lambda c: events.read_after(c, 0, wid))
        if e["type"] == etype
    ]


def _bd_status(repo, bead_id):
    out = subprocess.run(
        ["bd", "show", bead_id, "--json"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)[0]["status"]


def _gate_index(chain_json, gate):
    nodes = json.loads(chain_json)["nodes"]
    return next(i for i, n in enumerate(nodes) if n.get("gate_after") == gate)


def test_walk_stops_at_first_gate(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_default_template(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "awaiting_gate"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            assert row["current_node_id"] == "spec"
            types = _events(database, wid)
            assert types.count("gate_requested") == 1
            assert _payloads(database, wid, "gate_requested")[0]["gate"] == "spec_approval"
            assert "work_item_completed" not in types
        finally:
            await database.close()

    asyncio.run(scenario())


def test_approving_all_four_gates_completes_chain(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            pol = policy.load_policy(_REPO_ROOT / "templates" / "policy.yaml")
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_default_template(),
                bd_cwd=str(tracker),
            )
            chain_json = database.read(
                lambda c: c.execute(
                    "SELECT chain_definition FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )["chain_definition"]

            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker), policy=pol
            )
            for gate in (
                "spec_approval",
                "plan_approval",
                "chain_finalized",
                "human_review_approval",
            ):
                assert result == "awaiting_gate"
                assert _payloads(database, wid, "gate_requested")[-1]["gate"] == gate
                await database.write(lambda c, g=gate: store.approve_gate(c, wid, g))
                start = _gate_index(chain_json, gate) + 1
                result = await executor.run(
                    database,
                    rd,
                    work_item_id=wid,
                    registry=registry,
                    bd_cwd=str(tracker),
                    start_index=start,
                    policy=pol,
                )
            assert result == "completed"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, bead_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "completed"
            assert _bd_status(tracker, row["bead_id"]) == "closed"
            types = _events(database, wid)
            assert types.count("gate_requested") == 4
            assert types.count("gate_approved") == 4
            assert types.count("node_completed") == 11
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reject_records_the_note_and_reopen_flips_the_row(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_default_template(),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            status = lambda: database.read(  # noqa: E731
                lambda c: c.execute("SELECT status FROM work_items WHERE id=?", (wid,)).fetchone()
            )["status"]

            # a terminal reject leaves the item stopped where it is
            await database.write(
                lambda c: store.reject_gate(
                    c, wid, "spec_approval", "not specific enough", reopen=False
                )
            )
            assert status() == "needs_human"

            # a re-planning reject hands the node back to the executor
            await database.write(
                lambda c: store.reject_gate(
                    c, wid, "spec_approval", "not specific enough", reopen=True
                )
            )
            assert status() == "active"
            rej = _payloads(database, wid, "gate_rejected")
            assert (
                rej == [{"gate": "spec_approval", "note": "not specific enough", "node": None}] * 2
            )
            assert "gate_approved" not in _events(database, wid)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_spec_worker_that_wrote_no_artifact_opens_no_gate(tmp_path, monkeypatch):
    """The empty gate from work item 6363c65e, end to end (Kraft-7lu).

    The worker there was refused every Write, produced nothing, and still
    exited 0 — so the chain completed `spec` and asked a human to approve
    `spec_approval` with `kraft artifact` returning 404. The node must fail
    instead, and no gate may open over an artifact that does not exist.
    """
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SKIP_ARTIFACT", "1")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_default_template(),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            types = _events(database, wid)
            assert "gate_requested" not in types
            assert "node_completed" not in types
            assert not (rd.worktrees / wid / ".engineering" / "specs" / f"{wid}.md").exists()
        finally:
            await database.close()

    asyncio.run(scenario())
