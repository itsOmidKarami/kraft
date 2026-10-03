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


@pytest.mark.parametrize(
    ("stop", "hinted"),
    [
        ({"kind": "budget", "scope": "work_item"}, True),
        ({"kind": "budget", "limit": {"path": "", "key": "budget_usd", "value": 5}}, True),
        ({"kind": "budget", "scope": "daily"}, False),
        ({"kind": "budget", "scope": "tokens"}, False),
        ({"kind": "gate"}, False),
    ],
    ids=["own-cap", "policy-cap", "daily", "token", "not-budget"],
)
def test_show_names_raise_budget_on_a_stop_it_can_raise(monkeypatch, capsys, stop, hinted):
    """R10a-10: the board offers Raise cap; `kraft view show` printed the stop
    reason and `suggested_action None`, and named no command."""
    from kraft.client import transport

    async def get(path, **_):
        return {
            "id": "w1",
            "status": "needs_human",
            "current_node_id": "plan",
            "stop_reason": "budget cap reached: $0.04 spent on this work item, cap $0.03.",
            "suggested_action": None,
            "stop": stop,
            "chain_definition": {"nodes": []},
        }

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    out = capsys.readouterr().out
    assert ("next kraft item raise-budget w1 --usd N" in " ".join(out.split())) is hinted
    assert "chain_definition" not in out
    cli.main(["view", "show", "w1", "--json"])
    assert json.loads(capsys.readouterr().out)["stop"] == stop


def test_show_prints_a_title_1_4_stored_as_plain_text(monkeypatch, capsys):
    """R12F-08: `view list` drops a 1.4-stored title's controls, and `view
    show` printed them to the terminal: an escape and a bidi override."""
    from kraft.client import transport

    title = "first line\nsecond\tline \x1b[31mred\u202eevil"

    async def get(path, **_):
        return {"id": "w1", "status": "paused", "title": title, "chain_definition": {"nodes": []}}

    monkeypatch.setattr(transport, "_get", get)
    cli.main(["view", "show", "w1"])
    out = capsys.readouterr().out
    assert [c for c in out if c in "\x1b\u202e\t"] == []
    assert "second line [31mredevil" in out
    cli.main(["view", "show", "w1", "--json"])
    assert json.loads(capsys.readouterr().out)["title"] == title
