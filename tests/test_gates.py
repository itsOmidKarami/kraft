import asyncio
import json
import subprocess
import sys
from pathlib import Path

from support.harness import isolated_bd, make_repo, v1_named_chain

from kraft import db, events, executor, policy, store
from kraft.adapters import forge as _forge
from kraft.paths import RunDirs

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _default_template(tmp_path):
    """The shipped V1 `default` chain, its agent tasks on the fake agent."""
    return v1_named_chain(
        tmp_path / "templates", "default", agent_command=f"{sys.executable} {_FAKE_AGENT}"
    )


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


def _launch(tmp_path, **repo_entry):
    # The shipped spec task names a `steering:` profile, which dispatch still
    # resolves to a file (`seed_v1_library` writes it out beside the library).
    return executor.LaunchContext(
        repo_entry=repo_entry or None, steering_dir=tmp_path / "templates" / "steering"
    )


def test_walk_stops_at_first_gate(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_default_template(tmp_path),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=_launch(tmp_path),
            )
            assert result == "awaiting_gate"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            # A V1 gate is its own node, so the walk stands on it.
            assert row["current_node_id"] == "spec_approval"
            types = _events(database, wid)
            assert types.count("gate_requested") == 1
            assert _payloads(database, wid, "gate_requested")[0]["gate"] == "spec_approval"
            assert "work_item_completed" not in types
        finally:
            await database.close()

    asyncio.run(scenario())


def _walk_default_chain_approving_every_gate(tmp_path, launch):
    """Walk the shipped `default` chain, approving each gate as it opens, to
    wherever it first stops. Returns what a test asserts on."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    chain = _default_template(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            pol = policy.load_policy(_REPO_ROOT / "templates" / "policy.yaml")
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=chain,
                bd_cwd=str(tracker),
            )
            ids = [n.id for n in chain.nodes]

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=launch,
            )
            for gate in ("spec_approval", "plan_approval", "local_review"):
                assert result == "awaiting_gate"
                assert _payloads(database, wid, "gate_requested")[-1]["gate"] == gate
                await database.write(lambda c, g=gate: store.approve_gate(c, wid, g))
                result = await executor.run(
                    database,
                    rd,
                    work_item_id=wid,
                    registry=None,
                    bd_cwd=str(tracker),
                    start_index=ids.index(gate) + 1,
                    policy=pol,
                    launch=launch,
                )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id, bead_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            return {
                "result": result,
                "node": row["current_node_id"],
                "bead_status": _bd_status(tracker, row["bead_id"]),
                "reason": _payloads(database, wid, "work_item_needs_human")[-1]["reason"],
                "types": _events(database, wid),
                "completed": [p["node_id"] for p in _payloads(database, wid, "node_completed")],
            }
        finally:
            await database.close()

    return asyncio.run(scenario())


def test_approving_the_gates_walks_the_default_chain_to_its_first_unimplemented_node(
    tmp_path, monkeypatch
):
    """Was `test_approving_all_four_gates_completes_chain`. The V1 `default`
    chain cannot complete yet, and this pins exactly where it stops rather than
    pretending otherwise: its `merge_request_feedback` node waits on
    `mr.automated_review`, which has no handler until Task 9 and stops for a
    human (`forge.run._UNIMPLEMENTED_TARGETS`). Everything before it -- both
    planning gates, implementation, `local_review`, the draft merge request and
    its CI -- walks for real."""
    fake = _forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(_forge.run, "resolve", lambda name: fake)
    walked = _walk_default_chain_approving_every_gate(
        tmp_path, _launch(tmp_path, setup_command="", forge="github")
    )
    assert walked["result"] == "needs_human"
    assert walked["node"] == "merge_request_feedback"
    assert walked["bead_status"] != "closed"
    assert "await_review" in walked["reason"]
    assert walked["types"].count("gate_requested") == 3
    assert walked["types"].count("gate_approved") == 3
    assert fake.opened, "the draft merge request was never opened"


def test_a_repo_on_the_fake_forge_walks_the_default_chain_to_the_same_stop(tmp_path):
    """Ruling 147: `forge: fake` on a repo is how `just dev` reaches the merge-
    request half of the default chain. Nothing is monkeypatched here -- the real
    `backend_for`/`resolve` pair has to turn the repo's `fake` into `FakeForge`,
    or the walk stops at `draft_merge_request` for want of a forge."""
    walked = _walk_default_chain_approving_every_gate(
        tmp_path, _launch(tmp_path, setup_command="", forge="fake")
    )
    assert "draft_merge_request" in walked["completed"], walked["reason"]
    assert walked["node"] == "merge_request_feedback"
    assert "await_review" in walked["reason"]
    # The stop names its own cause on the card (Task 4b: "for a human naming
    # the target"), not merely "see the session log".
    assert "mr.automated_review" in walked["reason"] and "Task 9" in walked["reason"]


def test_reject_records_the_note_and_reopen_flips_the_row(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                chain=_default_template(tmp_path),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=_launch(tmp_path),
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
                rej
                == [
                    {
                        "gate": "spec_approval",
                        "note": "not specific enough",
                        "node": None,
                        "by": "human",
                        # Kraft-s7c04.16. None, not "reject": this test drives
                        # `store.reject_gate` directly and names no verdict. The
                        # key is always present so a reader never has to tell
                        # "no verdict given" from "event predates the field".
                        "verdict": None,
                    }
                ]
                * 2
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
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_default_template(tmp_path),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=_launch(tmp_path),
            )
            types = _events(database, wid)
            assert "gate_requested" not in types
            assert "node_completed" not in types
            # ...and for the missing document, not for something incidental:
            # without a steering dir the spec task fails before it ever runs.
            stopped = _payloads(database, wid, "worker_session_exited")
            assert [p["status"] for p in stopped] == ["failed"]
            assert not (rd.worktrees / wid / ".engineering" / "specs" / f"{wid}.md").exists()
        finally:
            await database.close()

    asyncio.run(scenario())
