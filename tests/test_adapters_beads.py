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


def test_intake_raises_on_bd_failure(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(subprocess.CalledProcessError):
        asyncio.run(beads.intake("x", cwd=str(bare)))


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
