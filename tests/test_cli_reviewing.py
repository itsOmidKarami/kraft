"""diff, docs, doc — reading what an agent did before approving it."""

from __future__ import annotations

import asyncio
import json

import pytest
from support.harness import make_repo

from kraft import cli, client, render

# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.http()
# to the ASGI app with the lifespan entered per client.


def _make_item(repo, title="review me"):
    async def go():
        async with client.http() as http:
            response = await http.post(
                "/work-items", json={"title": title, "repo": str(repo), "autostart": False}
            )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return asyncio.run(go())


def test_diff_on_an_item_with_no_baseline_is_explicit(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    payload = asyncio.run(client.diff(wid))
    # a paused, never-run item has no env_setup node stamped yet
    assert payload["base_ref"] is None
    assert payload["diff"] == ""
    assert payload["truncated"] is False


def test_diff_defaults_to_the_resolved_work_item(app, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    assert asyncio.run(client.diff())["work_item_id"] == wid


def test_documents_is_empty_for_a_fresh_item(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    assert asyncio.run(client.documents(wid)) == []


def test_document_404_is_a_readable_message(app):
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.document("no-such-doc"))


def test_open_document_404_is_a_readable_message(app):
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.open_document("no-such-doc"))


def test_diff_no_baseline_prints_the_explicit_line(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["diff", wid])
    assert "no baseline" in capsys.readouterr().out


def test_diff_stat_and_truncation_reach_stdout(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_diff(work_item_id=None):
        return {
            "work_item_id": wid,
            "base_ref": "abc",
            "files": [{"path": "x.py", "insertions": 1, "deletions": 0}],
            "diff": "diff --git a/x.py b/x.py\n+hi\n",
            "untracked": ["new.txt"],
            "truncated": True,
        }

    monkeypatch.setattr(client, "diff", fake_diff)
    cli.main(["diff", wid, "--stat"])
    out = capsys.readouterr().out
    assert "x.py" in out
    assert "new.txt" in out
    assert "truncated" in out.lower()  # asserted on the rendered output, not the payload


def test_diff_name_only_prints_paths_one_per_line(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_diff(work_item_id=None):
        return {
            "work_item_id": wid,
            "base_ref": "abc",
            "files": [
                {"path": "a.py", "insertions": 1, "deletions": 0},
                {"path": "b.py", "insertions": 1, "deletions": 0},
            ],
            "diff": "",
            "untracked": ["c.py"],
            "truncated": False,
        }

    monkeypatch.setattr(client, "diff", fake_diff)
    cli.main(["diff", wid, "--name-only"])
    # untracked paths are included: they are files the agent touched
    assert capsys.readouterr().out.split() == ["a.py", "b.py", "c.py"]


def test_diff_json_is_the_raw_payload(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["diff", wid, "--json"])
    printed = json.loads(capsys.readouterr().out)
    assert printed == asyncio.run(client.diff(wid))


def test_docs_lists_linked_documents(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_documents(work_item_id=None):
        return [
            {
                "document_id": "doc-1",
                "kind": "spec",
                "title": "A spec",
                "path": "docs/a.md",
                "repo": str(repo),
            }
        ]

    monkeypatch.setattr(client, "documents", fake_documents)
    cli.main(["docs", wid])
    out = capsys.readouterr().out
    assert "doc-1" in out and "spec" in out and "docs/a.md" in out


def test_docs_empty_says_nothing_rather_than_crashing(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["docs", wid])
    assert "(nothing)" in capsys.readouterr().out


def test_doc_prints_content(app, monkeypatch, capsys):
    async def fake_document(doc_id):
        return {"id": doc_id, "title": "A spec", "path": "docs/a.md", "content": "# Hello\n"}

    monkeypatch.setattr(client, "document", fake_document)
    cli.main(["doc", "doc-1"])
    assert "# Hello" in capsys.readouterr().out


def test_doc_open_hands_off_to_the_server(app, monkeypatch, capsys):
    seen = {}

    async def fake_open(doc_id, editor=None):
        seen.update(doc_id=doc_id, editor=editor)
        return {"document_id": doc_id, "path": "/abs/docs/a.md", "editor": editor or "system"}

    monkeypatch.setattr(client, "open_document", fake_open)
    cli.main(["doc", "doc-1", "--open", "code"])
    assert seen == {"doc_id": "doc-1", "editor": "code"}
    assert "/abs/docs/a.md" in capsys.readouterr().out


def test_doc_open_on_a_headless_server_is_a_kraft_message(app, capsys, monkeypatch):
    async def fake_open(doc_id, editor=None):
        raise ValueError("kraft 501: no editor available on the server for default")

    monkeypatch.setattr(client, "open_document", fake_open)
    with pytest.raises(SystemExit) as caught:
        cli.main(["doc", "doc-1", "--open"])
    assert caught.value.code == 1
    assert "501" in capsys.readouterr().err


def test_diff_name_only_without_a_baseline_says_so(app, tmp_path, capsys):
    """Empty and unknown are different answers in every view, --name-only too."""
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["diff", wid, "--name-only"])
    assert "no baseline" in capsys.readouterr().out


def _truncated_payload(wid, worktree="/tmp/kraft/worktrees/wi-1", **extra):
    return {
        "work_item_id": wid,
        "base_ref": "abc",
        "files": [{"path": "x.py", "insertions": 1, "deletions": 0}],
        "diff": "diff --git a/x.py b/x.py\n+hi\n",
        "untracked": [],
        "truncated": True,
        "diff_max_bytes": 1_000_000,
        "worktree_path": worktree,
        **extra,
    }


def test_the_truncation_warning_names_the_limit_and_the_worktree(app, tmp_path, capsys):
    text = render.diff_stat(_truncated_payload("wi-1"))
    assert "1000000 bytes" in text
    assert "/tmp/kraft/worktrees/wi-1" in text


def test_an_untruncated_diff_says_nothing_about_a_limit(app):
    payload = _truncated_payload("wi-1")
    payload["truncated"] = False
    assert "1000000" not in render.diff_stat(payload)


def test_the_diff_api_carries_the_limit_and_the_worktree(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    payload = asyncio.run(client.diff(wid))
    # present even when nothing was truncated: the renderer must not have to ask
    assert payload["diff_max_bytes"] > 0
    assert payload["worktree_path"].endswith(wid)
