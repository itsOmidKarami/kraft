"""`kraft view ...` parser rules; the verbs' behaviour is in `test_verbs.py`
and `test_watching.py`."""

from __future__ import annotations

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
