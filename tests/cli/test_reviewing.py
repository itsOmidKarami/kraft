"""diff, docs, doc — reading what an agent did before approving it."""

from __future__ import annotations

import asyncio
import json

import pytest

from kraft import cli, client, render

# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.transport.http()
# to the ASGI app with the lifespan entered per client.


def test_diff_on_an_item_with_no_baseline_is_explicit(app, make_item, repo):
    wid = make_item(repo)
    payload = asyncio.run(client.diff(wid))
    # a paused, never-run item has no env_setup node stamped yet
    assert payload["base_ref"] is None
    assert payload["diff"] == ""
    assert payload["truncated"] is False


def test_diff_defaults_to_the_resolved_work_item(app, monkeypatch, make_item, repo):
    wid = make_item(repo)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    assert asyncio.run(client.diff())["work_item_id"] == wid


def test_documents_is_empty_for_a_fresh_item(app, make_item, repo):
    wid = make_item(repo)
    assert asyncio.run(client.documents(wid)) == []


def test_document_404_is_a_readable_message(app):
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.document("no-such-doc"))


def test_open_document_404_is_a_readable_message(app):
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.open_document("no-such-doc"))


def test_diff_no_baseline_prints_the_explicit_line(app, capsys, make_item, repo):
    wid = make_item(repo)
    cli.main(["view", "diff", wid])
    assert "no baseline" in capsys.readouterr().out


def test_diff_stat_and_truncation_reach_stdout(app, monkeypatch, capsys, make_item, repo):
    wid = make_item(repo)

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
    cli.main(["view", "diff", wid, "--stat"])
    out = capsys.readouterr().out
    assert "x.py" in out
    assert "new.txt" in out
    assert "truncated" in out.lower()  # asserted on the rendered output, not the payload


def test_diff_name_only_prints_paths_one_per_line(app, monkeypatch, capsys, make_item, repo):
    wid = make_item(repo)

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
    cli.main(["view", "diff", wid, "--name-only"])
    # untracked paths are included: they are files the agent touched
    assert capsys.readouterr().out.split() == ["a.py", "b.py", "c.py"]


def test_diff_json_is_the_raw_payload(app, capsys, make_item, repo):
    wid = make_item(repo)
    cli.main(["view", "diff", wid, "--json"])
    printed = json.loads(capsys.readouterr().out)
    assert printed == asyncio.run(client.diff(wid))


def test_docs_lists_linked_documents(app, monkeypatch, capsys, make_item, repo):
    wid = make_item(repo)

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
    cli.main(["view", "docs", wid])
    out = capsys.readouterr().out
    assert "doc-1" in out and "spec" in out and "docs/a.md" in out


def test_docs_empty_says_nothing_rather_than_crashing(app, capsys, make_item, repo):
    wid = make_item(repo)
    cli.main(["view", "docs", wid])
    assert "(nothing)" in capsys.readouterr().out


def test_doc_prints_content(app, monkeypatch, capsys):
    async def fake_document(doc_id):
        return {"id": doc_id, "title": "A spec", "path": "docs/a.md", "content": "# Hello\n"}

    monkeypatch.setattr(client, "document", fake_document)
    cli.main(["view", "doc", "doc-1"])
    assert "# Hello" in capsys.readouterr().out


def test_doc_open_hands_off_to_the_server(app, monkeypatch, capsys):
    seen = {}

    async def fake_open(doc_id, editor=None):
        seen.update(doc_id=doc_id, editor=editor)
        return {"document_id": doc_id, "path": "/abs/docs/a.md", "editor": editor or "system"}

    monkeypatch.setattr(client, "open_document", fake_open)
    cli.main(["view", "doc", "doc-1", "--open", "code"])
    assert seen == {"doc_id": "doc-1", "editor": "code"}
    assert "/abs/docs/a.md" in capsys.readouterr().out


def test_doc_open_on_a_headless_server_is_a_kraft_message(app, capsys, monkeypatch):
    async def fake_open(doc_id, editor=None):
        raise ValueError("kraft 501: no editor available on the server for default")

    monkeypatch.setattr(client, "open_document", fake_open)
    with pytest.raises(SystemExit) as caught:
        cli.main(["view", "doc", "doc-1", "--open"])
    assert caught.value.code == 1
    assert "501" in capsys.readouterr().err


def test_diff_name_only_without_a_baseline_says_so(app, capsys, make_item, repo):
    """Empty and unknown are different answers in every view, --name-only too."""
    wid = make_item(repo)
    cli.main(["view", "diff", wid, "--name-only"])
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


def test_the_truncation_warning_names_the_limit_and_the_worktree(app, capsys):
    text = render.diff_stat(_truncated_payload("wi-1"))
    assert "1000000 bytes" in text
    assert "/tmp/kraft/worktrees/wi-1" in text


def test_an_untruncated_diff_says_nothing_about_a_limit(app):
    payload = _truncated_payload("wi-1")
    payload["truncated"] = False
    assert "1000000" not in render.diff_stat(payload)


def test_the_diff_api_carries_the_limit_and_the_worktree(app, make_item, repo):
    wid = make_item(repo)
    payload = asyncio.run(client.diff(wid))
    # present even when nothing was truncated: the renderer must not have to ask
    assert payload["diff_max_bytes"] > 0
    assert payload["worktree_path"].endswith(wid)


def test_artifact_json_is_the_raw_payload(app, monkeypatch, capsys):
    payload = {"path": ".engineering/specs/w1.md", "title": "fake spec", "content": "# Spec\n"}

    async def fake_artifact(work_item_id=None):
        return payload

    monkeypatch.setattr(client, "artifact", fake_artifact)
    cli.main(["view", "artifact", "w1", "--json"])
    assert json.loads(capsys.readouterr().out) == payload


def test_artifact_prints_the_document_content(app, monkeypatch, capsys):
    async def fake_artifact(work_item_id=None):
        return {"path": ".engineering/specs/w1.md", "title": "fake spec", "content": "# Hello\n"}

    monkeypatch.setattr(client, "artifact", fake_artifact)
    cli.main(["view", "artifact", "w1"])
    out = capsys.readouterr().out
    assert "# Hello" in out
    # the front-matter/plumbing keys are not the reviewer's business, only the content is
    assert "fake spec" not in out


def test_artifact_truncation_reaches_stdout(app, monkeypatch, capsys):
    """`_cmd_artifact` used to page `content` straight through with no read of
    `truncated` at all -- the one surface where a reviewer could approve a
    document whose tail was silently cut."""

    async def fake_artifact(work_item_id=None):
        return {
            "path": ".engineering/specs/w1.md",
            "title": "fake spec",
            "content": "# Hello\n",
            "truncated": True,
            "artifact_max_bytes": 1_000_000,
        }

    monkeypatch.setattr(client, "artifact", fake_artifact)
    cli.main(["view", "artifact", "w1"])
    out = capsys.readouterr().out
    assert "# Hello" in out
    assert "1000000 bytes" in out
    assert ".engineering/specs/w1.md" in out


def test_an_untruncated_artifact_says_nothing_about_a_limit(app, monkeypatch, capsys):
    async def fake_artifact(work_item_id=None):
        return {
            "path": ".engineering/specs/w1.md",
            "title": "fake spec",
            "content": "# Hello\n",
            "truncated": False,
            "artifact_max_bytes": 1_000_000,
        }

    monkeypatch.setattr(client, "artifact", fake_artifact)
    cli.main(["view", "artifact", "w1"])
    assert "1000000" not in capsys.readouterr().out


def test_view_diff_prints_landed_and_in_flight_sections(app, monkeypatch, capsys, make_item, repo):
    """Kraft-nceo from the CLI side: not printing `landed` would drop committed
    work from `kraft view diff` — the same bug from the other direction."""
    wid = make_item(repo)

    async def fake_diff(work_item_id=None):
        return {
            "work_item_id": wid,
            "base_ref": "abc",
            "files": [{"path": "flight.py", "insertions": 1, "deletions": 0}],
            "diff": "diff --git a/flight.py b/flight.py\n+in flight\n",
            "untracked": [],
            "truncated": False,
            "landed": {
                "commits": ["abc1234 land the doc"],
                "files": [{"path": "doc.md", "insertions": 3, "deletions": 0}],
                "diff": "diff --git a/doc.md b/doc.md\n+landed\n",
                "truncated": False,
            },
        }

    monkeypatch.setattr(client, "diff", fake_diff)

    cli.main(["view", "diff", wid, "--stat"])
    out = capsys.readouterr().out
    assert "doc.md" in out and "flight.py" in out
    assert "1 commit already on this branch" in out
    assert "in flight" in out

    cli.main(["view", "diff", wid])
    body = capsys.readouterr().out
    assert body.index("+landed") < body.index("+in flight"), "landed must lead"

    cli.main(["view", "diff", wid, "--name-only"])
    assert capsys.readouterr().out.split() == ["flight.py", "doc.md"]


def test_a_chain_revisions_artifact_prints_the_approve_command_with_its_digest(
    app, monkeypatch, capsys
):
    """Kraft-ec66w: the digest is what binds an approval to this render."""

    async def fake_artifact(work_item_id=None):
        return {"work_item_id": "w1", "path": "p.md", "content": "# Revision\n", "digest": "d1"}

    monkeypatch.setattr(client, "artifact", fake_artifact)
    cli.main(["view", "artifact", "w1"])
    assert "kraft item approve --digest d1 w1" in capsys.readouterr().out


def test_kraft_item_approve_sends_the_digest_it_was_given(app, monkeypatch):
    from kraft.client import transport

    sent = {}

    async def fake_act(path, payload=None):
        sent[path] = payload
        return {"id": "w1", "status": "active"}

    monkeypatch.setattr(transport, "_act", fake_act)
    cli.main(["item", "approve", "w1", "--gate", "chain_revision_approval", "--digest", "d1"])
    cli.main(["item", "approve", "w1", "--gate", "spec_approval"])
    assert sent == {
        "/work-items/w1/gates/chain_revision_approval/approve": {"digest": "d1"},
        "/work-items/w1/gates/spec_approval/approve": None,
    }
