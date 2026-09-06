"""health and reindex — the server's own view, from a terminal."""

from __future__ import annotations

import asyncio
import json

import pytest

from kraft import cli, client

# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.http()
# to the ASGI app with the lifespan entered per client.


def test_health_returns_the_status_block(app):
    payload = asyncio.run(client.health())
    assert payload["status"] in ("ok", "degraded")
    assert "index" in payload and "bind" in payload


def test_reindex_all_returns_totals(app):
    payload = asyncio.run(client.reindex())
    assert payload["repo"] is None
    assert set(payload["stats"]) == {"inserted", "updated", "renamed", "deleted"}


def test_reindex_unknown_repo_is_a_readable_404(app):
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.reindex("/no/such/repo"))


def test_health_exit_code_follows_status(app, monkeypatch, capsys):
    async def degraded():
        return {
            "status": "degraded",
            "bind": "127.0.0.1",
            "invalid_templates": {"x.yaml": "bad"},
            "invalid_policy": None,
            "reattach_summary": {
                "scanned": 0,
                "adopted": [],
                "resolved_from_file": [],
                "unknown": [],
                "resumed_work_items": [],
            },
            "index": {
                "documents": 0,
                "repos_scanned": 0,
                "last_scan_at": None,
                "embeddings": {"available": True, "model": "m", "chunks": 0, "reason": None},
                "errors": [],
            },
        }

    monkeypatch.setattr(client, "health", degraded)
    with pytest.raises(SystemExit) as caught:
        cli.main(["health"])
    assert caught.value.code == 1
    assert "x.yaml" in capsys.readouterr().out  # the reason is on stdout, it is not an error


def test_health_ok_exits_zero(app, capsys):
    cli.main(["health"])  # a fresh fixture home is healthy; no SystemExit
    assert "ok" in capsys.readouterr().out


def test_health_json_is_the_raw_payload(app, capsys):
    cli.main(["health", "--json"])
    printed = json.loads(capsys.readouterr().out)
    direct = asyncio.run(client.health())
    # last_scan_at moves between two calls; everything else is the payload verbatim
    for payload in (printed, direct):
        payload["index"].pop("last_scan_at")
    assert printed == direct


def test_reindex_prints_the_counts(app, capsys):
    cli.main(["reindex"])
    out = capsys.readouterr().out
    for key in ("inserted", "updated", "renamed", "deleted"):
        assert key in out


def test_reindex_unknown_repo_is_a_kraft_message(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["reindex", "--repo", "/no/such/repo"])
    assert caught.value.code == 1
    assert "404" in capsys.readouterr().err
