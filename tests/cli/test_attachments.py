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


def test_view_docs_lists_and_prints_what_an_unstarted_item_was_filed_with(
    app, capsys, monkeypatch, make_item, repo
):
    """Before it starts, nothing is indexed: `view docs` printed "(nothing)",
    though the item had a spec, and no verb could print it (R10F-06)."""
    wid = make_item(repo)
    (repo / "s.md").write_text("---\ntitle: My spec\n---\n# The spec\n")
    monkeypatch.chdir(repo)
    cli.main(["item", "set-attachments", wid, "--spec", "s.md", "--json"])
    capsys.readouterr()

    cli.main(["view", "docs", wid])
    out = capsys.readouterr().out
    assert "attached when it was filed:" in out and "spec  s.md" in out
    assert f"read one: kraft view docs {wid} --attachment spec" in out
    assert "(nothing)" not in out

    cli.main(["view", "docs", wid, "--attachment", "spec", "--no-pager"])
    assert capsys.readouterr().out == "# The spec\n\n"
    cli.main(["view", "docs", wid, "--attachment", "spec", "--json"])
    assert json.loads(capsys.readouterr().out)["title"] == "My spec"
    with pytest.raises(SystemExit) as caught:
        cli.main(["view", "docs", wid, "--attachment", "plan"])
    assert caught.value.code == 1
    assert "no plan attached" in capsys.readouterr().err


def test_view_docs_json_is_still_the_indexed_documents(app, capsys, monkeypatch, make_item, repo):
    """`--json` prints the documents route's answer and nothing else."""
    wid = make_item(repo)
    (repo / "s.md").write_text("# The spec\n")
    monkeypatch.chdir(repo)
    cli.main(["item", "set-attachments", wid, "--spec", "s.md", "--json"])
    capsys.readouterr()
    cli.main(["view", "docs", wid, "--json"])
    assert json.loads(capsys.readouterr().out) == []
