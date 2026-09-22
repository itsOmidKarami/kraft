import sys
from pathlib import Path

from support.harness import isolated_bd, v1_named_chain

from kraft import events, executor, store

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


def _launch(tmp_path, **repo_entry):
    return executor.LaunchContext(repo_entry=repo_entry or None)


async def test_walk_stops_at_first_gate(tmp_path, database, run_dirs, repo):
    tracker = isolated_bd(tmp_path)

    wid = await executor.intake(
        database,
        run_dirs,
        title="make the failing test pass",
        repo=str(repo),
        chain=_default_template(tmp_path),
        bd_cwd=str(tracker),
    )
    result = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
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


async def test_reject_records_the_note_and_reopen_flips_the_row(tmp_path, database, run_dirs, repo):
    tracker = isolated_bd(tmp_path)

    wid = await executor.intake(
        database,
        run_dirs,
        title="t",
        repo=str(repo),
        chain=_default_template(tmp_path),
        bd_cwd=str(tracker),
    )
    await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        bd_cwd=str(tracker),
        launch=_launch(tmp_path),
    )
    status = lambda: database.read(  # noqa: E731
        lambda c: c.execute("SELECT status FROM work_items WHERE id=?", (wid,)).fetchone()
    )["status"]

    # a terminal reject leaves the item stopped where it is
    await database.write(
        lambda c: store.reject_gate(c, wid, "spec_approval", "not specific enough", reopen=False)
    )
    assert status() == "needs_human"

    # a re-planning reject hands the node back to the executor
    await database.write(
        lambda c: store.reject_gate(c, wid, "spec_approval", "not specific enough", reopen=True)
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


async def test_a_spec_worker_that_wrote_no_artifact_opens_no_gate(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """The empty gate from work item 6363c65e, end to end (Kraft-7lu).

    The worker there was refused every Write, produced nothing, and still
    exited 0 — so the chain completed `spec` and asked a human to approve
    `spec_approval` with `kraft artifact` returning 404. The node must fail
    instead, and no gate may open over an artifact that does not exist.
    """
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SKIP_ARTIFACT", "1")
    tracker = isolated_bd(tmp_path)

    wid = await executor.intake(
        database,
        run_dirs,
        title="make the failing test pass",
        repo=str(repo),
        chain=_default_template(tmp_path),
        bd_cwd=str(tracker),
    )
    await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        bd_cwd=str(tracker),
        launch=_launch(tmp_path),
    )
    types = _events(database, wid)
    assert "gate_requested" not in types
    assert "node_completed" not in types
    # ...and for the missing document, not for something incidental:
    # the spec task ran and failed for want of its document.
    stopped = _payloads(database, wid, "worker_session_exited")
    assert [p["status"] for p in stopped] == ["failed"]
    assert not (run_dirs.worktrees / wid / ".engineering" / "specs" / f"{wid}.md").exists()
