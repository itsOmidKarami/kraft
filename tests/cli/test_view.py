"""`kraft view ...` parser rules and the search verb; the other verbs'
behaviour is in `test_verbs.py` and `test_watching.py`."""

from __future__ import annotations

import json
import re

import pytest

from kraft import cli, db
from kraft.cli import view


def test_list_status_offers_every_status_the_schema_allows():
    allowed = re.findall(r"'(\w+)'", re.search(r"status IN\s*\(([^)]*)\)", db.SCHEMA_SQL)[1])
    assert set(view.STATUSES) == set(allowed)


def test_list_refuses_a_status_typo_instead_of_showing_an_empty_board(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["view", "list", "--status", "needs-human"])
    assert caught.value.code == 2
    assert "invalid choice: 'needs-human'" in capsys.readouterr().err


@pytest.mark.parametrize(
    "note", [None, "vector search is failing (403), so this is text search only"]
)
def test_search_prints_why_it_fell_back_to_text(note):
    hit = {"kind": "specs", "repo": "/r", "path": "a.md"}
    payload = {"mode": "fts", "results": [hit], **({"note": note} if note else {})}

    out = view._render_search(payload)

    assert "a.md" in out
    assert (f"note: {note}" in out) if note else ("note:" not in out)


def test_search_renders_results(monkeypatch, capsys):
    """One row per hit, with `--limit` passed through to the API."""
    seen = []

    async def fake_search(q, limit=20):
        seen.append((q, limit))
        hits = [
            {"kind": "spec", "repo": "/r", "path": "specs/a.md", "score": 1.0},
            {"kind": "session", "repo": "/s", "path": "sessions/b.md", "score": 0.5},
        ]
        return {"query": q, "results": hits}

    monkeypatch.setattr("kraft.client.search", fake_search)
    cli.main(["view", "search", "anything", "--limit", "3"])
    rows = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert rows == [
        ["KIND", "REPO", "PATH"],
        ["spec", "/r", "specs/a.md"],
        ["session", "/s", "sessions/b.md"],
    ]
    cli.main(["view", "search", "anything"])
    assert seen == [("anything", 3), ("anything", 20)]


def test_search_json_is_the_api_payload(app, capsys):
    cli.main(["view", "search", "anything", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "anything"
