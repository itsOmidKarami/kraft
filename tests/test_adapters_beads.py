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
