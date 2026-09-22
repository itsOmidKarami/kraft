"""`kraft item set-attachments` (Kraft-s7c04.28): the CLI door onto PATCH
/work-items' `attachments`. The route's own rules are pinned in
tests/api/test_attachment_patch.py; this is the round trip."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

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


def test_a_worker_cannot_set_its_own_items_attachments(app, capsys, monkeypatch, make_item, repo):
    """The same self-action door as `set-policy` and `set-overrides`."""
    wid = make_item(repo)
    (repo / "s.md").write_text("# mine\n")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)

    with pytest.raises(SystemExit) as caught:
        cli.main(["item", "set-attachments", "--spec", "s.md"])

    assert caught.value.code == 1
    assert "cannot act on its own work item" in capsys.readouterr().err
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID")
    cli.main(["view", "show", wid, "--json"])
    assert json.loads(capsys.readouterr().out)["attachments"] == []
