"""`kraft item set-attachments` (Kraft-s7c04.28): the CLI door onto PATCH
/work-items' `attachments`. The route's own rules are pinned in
tests/api/test_attachment_patch.py; this is the round trip."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kraft import cli, client


def _gates(capsys, wid) -> list[str | None]:
    cli.main(["view", "show", wid, "--json"])
    return [
        n["gate_after"] for n in json.loads(capsys.readouterr().out)["chain_definition"]["nodes"]
    ]


def test_set_attachments_revises_a_spec_from_a_subdirectory_and_drop_restores_its_gate(
    app, capsys, monkeypatch, make_item, repo
):
    asyncio.run(client.ensure_repo(str(repo)))
    wid = make_item(repo)
    specs = repo / "specs"
    specs.mkdir()
    (specs / "s.md").write_text("# revised\n")
    # Relative to where the command is typed, not to the repo root.
    monkeypatch.chdir(specs)

    cli.main(["item", "set-attachments", wid, "--spec", "s.md", "--json"])
    capsys.readouterr()

    cli.main(["view", "show", wid, "--json"])
    item = json.loads(capsys.readouterr().out)
    assert [(a["kind"], a["path"]) for a in item["attachments"]] == [("spec", "specs/s.md")]
    assert Path(item["attachments"][0]["source"]).read_text() == "# revised\n"
    assert "spec_approval" not in _gates(capsys, wid)

    cli.main(["item", "set-attachments", wid, "--drop", "spec", "--json"])
    capsys.readouterr()

    assert "spec_approval" in _gates(capsys, wid)
