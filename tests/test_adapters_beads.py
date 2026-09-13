import asyncio
import json
import subprocess

import pytest
from support.harness import isolated_bd

from kraft.adapters import beads


def test_intake_then_complete_roundtrip(tmp_path):
    repo = isolated_bd(tmp_path)

    def _status(bead_id: str) -> str:
        out = subprocess.run(
            ["bd", "show", bead_id, "--json"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout
        return json.loads(out)[0]["status"]

    async def scenario():
        bead_id = await beads.intake("wire the thing", cwd=str(repo))
        assert bead_id
        assert _status(bead_id) in ("open", "in_progress")
        await beads.complete(bead_id, cwd=str(repo))
        assert _status(bead_id) == "closed"

    asyncio.run(scenario())


def test_intake_writes_the_description_onto_the_bead(tmp_path):
    repo = isolated_bd(tmp_path)

    def _description(bead_id: str) -> str:
        out = subprocess.run(
            ["bd", "show", bead_id, "--json"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout
        return json.loads(out)[0]["description"]

    async def scenario():
        bead_id = await beads.intake(
            "wire the thing", description="the brief, at length", cwd=str(repo)
        )
        assert _description(bead_id) == "the brief, at length"

        plain = await beads.intake("wire the other thing", cwd=str(repo))
        assert _description(plain) == "Created by the Kraft orchestrator."

        empty = await beads.intake("wire a third thing", description="", cwd=str(repo))
        assert _description(empty) == "Created by the Kraft orchestrator."

    asyncio.run(scenario())


def test_ready_projects_the_description(tmp_path):
    """Auto-intake reads a bead the human already wrote. Dropping its description
    is dropping the brief in the one case where nobody is present to notice."""
    repo = isolated_bd(tmp_path)

    async def scenario():
        await beads.intake("caulk the transom", description="it leaks at the seam", cwd=str(repo))
        rows = await beads.ready(cwd=str(repo))
        assert rows
        row = next(r for r in rows if r["title"] == "caulk the transom")
        assert row["description"] == "it leaks at the seam"

    asyncio.run(scenario())


def test_ready_tolerates_a_bead_with_no_description(monkeypatch):
    """`ready` is best-effort by contract; a row without the key must not raise."""

    class _Proc:
        returncode = 0
        stdout = '[{"id": "X-1", "title": "t", "priority": 1}]'

    monkeypatch.setattr(beads.subprocess, "run", lambda *a, **k: _Proc())
    rows = asyncio.run(beads.ready(cwd="/tmp"))
    assert rows == [
        {"id": "X-1", "title": "t", "priority": 1, "issue_type": None, "description": None}
    ]


def test_intake_error_carries_bd_stderr(tmp_path, monkeypatch):
    """Kraft-ibwj: `CalledProcessError` names the argv and the exit status and
    not the one line that explains them. The raised message must carry bd's own
    words, because that message is what reaches the user."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    bd = stub_dir / "bd"
    bd.write_text('#!/bin/sh\necho "Error: no beads database found" >&2\nexit 1\n')
    bd.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir))
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(beads.intake("x", cwd=str(tmp_path)))
    assert "no beads database found" in str(excinfo.value)


def test_search_finds_a_closed_bead(tmp_path):
    """Kraft-evm: a completed work item closes its bead, and that is exactly the
    work the search strip has to be able to find."""
    repo = isolated_bd(tmp_path)

    async def scenario():
        bead_id = await beads.intake("caulk the transom", cwd=str(repo))
        await beads.complete(bead_id, cwd=str(repo))
        hits = await beads.search("caulk the transom", cwd=str(repo))
        assert [(h["id"], h["status"]) for h in hits] == [(bead_id, "closed")]

    asyncio.run(scenario())


def test_search_degrades_when_bd_is_missing(tmp_path, monkeypatch):
    """Kraft-9m4: best-effort by contract — no `bd` on PATH is an empty strip,
    not a 500 out of GET /beads/search."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert asyncio.run(beads.search("anything")) == []


def test_ready_returns_open_beads_with_int_priorities(tmp_path):
    repo = isolated_bd(tmp_path)
    bid = asyncio.run(beads.intake("pick me up", cwd=str(repo)))
    rows = asyncio.run(beads.ready(cwd=str(repo)))
    row = next(r for r in rows if r["id"] == bid)
    assert isinstance(row["priority"], int)
    assert row["title"] == "pick me up"


def test_ready_is_best_effort_on_a_directory_with_no_beads(tmp_path):
    """A poller that raises kills its own task; an empty list is the honest answer."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert asyncio.run(beads.ready(cwd=str(plain))) == []


def test_ready_is_best_effort_when_bd_emits_undecodable_bytes(tmp_path, monkeypatch):
    """`subprocess.run(text=True)` decodes stdout as UTF-8 and raises
    `UnicodeDecodeError` on bytes that are not; the poller's caller must still
    get a list."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    bd = stub_dir / "bd"
    bd.write_text('#!/bin/sh\nprintf "[\\377\\376]"\n')
    bd.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir))
    assert asyncio.run(beads.ready(cwd=str(tmp_path))) == []


def test_blocked_by_returns_the_row_s_blockers(tmp_path):
    repo = isolated_bd(tmp_path)

    async def scenario():
        a = await beads.intake("A", cwd=str(repo))
        b = await beads.intake("B", cwd=str(repo))
        subprocess.run(
            ["bd", "dep", "add", b, a, "--type", "blocks"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert await beads.blocked_by([b], cwd=str(repo)) == [a]
        assert await beads.blocked_by([a], cwd=str(repo)) == []

    asyncio.run(scenario())


def test_blocked_by_unions_blockers_across_every_id_passed(tmp_path):
    """The motivating case (plan-review finding 1): a work item's own
    tracking bead has no edges, but a sub-bead named in `implements_beads`
    does -- one call must answer for both ids."""
    repo = isolated_bd(tmp_path)

    async def scenario():
        tracking = await beads.intake("tracking bead", cwd=str(repo))
        sub = await beads.intake("sub bead", cwd=str(repo))
        blocker = await beads.intake("blocker", cwd=str(repo))
        subprocess.run(
            ["bd", "dep", "add", sub, blocker, "--type", "blocks"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        # tracking has no edges of its own; sub is blocked. Passing both in
        # one call must still surface the blocker.
        return await beads.blocked_by([tracking, sub], cwd=str(repo)), blocker

    result, blocker = asyncio.run(scenario())
    assert result == [blocker]


def test_blocked_by_is_best_effort_when_bd_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert asyncio.run(beads.blocked_by(["X-1"], cwd=str(tmp_path))) == []


def test_blocked_by_tolerates_unparsable_output(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = "not json"

    monkeypatch.setattr(beads.subprocess, "run", lambda *a, **k: _Proc())
    assert asyncio.run(beads.blocked_by(["X-1"], cwd="/tmp")) == []


def test_blocked_by_of_no_ids_is_a_no_op(monkeypatch):
    """No bead, no implements_beads: nothing to ask bd about, and no
    subprocess launched to ask it with."""

    def _boom(*a, **k):
        raise AssertionError("bd should not be invoked with an empty id list")

    monkeypatch.setattr(beads.subprocess, "run", _boom)
    assert asyncio.run(beads.blocked_by([], cwd="/tmp")) == []
