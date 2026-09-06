"""Every verb through cli.main(), with client.http() on the ASGI app.

This tests the dispatch table and the rendering, not httpx: the client layer has
its own tests in test_client_*.py.
"""

from __future__ import annotations

import json

import pytest
from support.harness import make_repo

from kraft import cli, client


def test_bare_kraft_still_serves(monkeypatch):
    """The zero-argument default predates the CLI and must survive it."""
    served = []
    monkeypatch.setattr(cli, "_serve", lambda: served.append(True))
    cli.main([])
    assert served == [True]


def test_an_unknown_verb_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["nonsense"])
    assert caught.value.code == 2
    assert "nonsense" in capsys.readouterr().err


def test_mcp_still_dispatches(monkeypatch):
    called = []
    import kraft.mcp as mcp

    monkeypatch.setattr(mcp, "serve_stdio", lambda: called.append(True))
    cli.main(["mcp"])
    assert called == [True]


def test_init_still_dispatches_and_honours_repo_scope(monkeypatch, tmp_path, capsys):
    seen = {}
    import kraft.init as init_mod

    def fake_install(repo_scope):
        seen["repo_scope"] = repo_scope
        return [tmp_path / "written.json"]

    monkeypatch.setattr(init_mod, "install", fake_install)
    cli.main(["init", "--repo"])
    assert seen["repo_scope"] is True
    assert "written.json" in capsys.readouterr().out


def _make_item(app, repo, title="a thing"):
    """Create one work item through the API, returning its id."""
    import asyncio

    async def go():
        async with client.http() as http:
            response = await http.post(
                "/work-items",
                json={"title": title, "repo": str(repo), "autostart": False},
            )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return asyncio.run(go())


def _connect(repo):
    import asyncio

    return asyncio.run(client.ensure_repo(str(repo)))


def test_list_renders_a_table_with_a_header(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    _make_item(app, repo, "first thing")
    cli.main(["list"])
    out = capsys.readouterr().out
    assert "ID" in out.splitlines()[0] and "TITLE" in out.splitlines()[0]
    assert "first thing" in out


def test_list_json_matches_the_client_payload(app, tmp_path, capsys):
    import asyncio

    repo = make_repo(tmp_path)
    _make_item(app, repo, "first thing")
    cli.main(["list", "--json"])
    printed = json.loads(capsys.readouterr().out)
    assert printed == asyncio.run(client.list_work_items())


def test_list_filters_by_status(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    _make_item(app, repo, "first thing")
    cli.main(["list", "--status", "completed", "--json"])
    assert json.loads(capsys.readouterr().out) == []


def test_list_scopes_to_the_cwd_repo(app, tmp_path, monkeypatch, capsys):
    here = make_repo(tmp_path, name="here")
    elsewhere = make_repo(tmp_path, name="elsewhere")
    _connect(here)
    _make_item(app, here, "mine")
    _make_item(app, elsewhere, "theirs")
    monkeypatch.chdir(here)
    cli.main(["list", "--json"])
    titles = [item["title"] for item in json.loads(capsys.readouterr().out)]
    assert titles == ["mine"]


def test_list_all_ignores_the_cwd_scope(app, tmp_path, monkeypatch, capsys):
    here = make_repo(tmp_path, name="here")
    elsewhere = make_repo(tmp_path, name="elsewhere")
    _connect(here)
    _make_item(app, here, "mine")
    _make_item(app, elsewhere, "theirs")
    monkeypatch.chdir(here)
    cli.main(["list", "--all", "--json"])
    titles = sorted(item["title"] for item in json.loads(capsys.readouterr().out))
    assert titles == ["mine", "theirs"]


def test_show_takes_an_explicit_id(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(app, repo, "detail me")
    cli.main(["show", wid])
    out = capsys.readouterr().out
    assert wid in out and "detail me" in out


def test_show_defaults_to_the_work_item_this_session_is_in(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(app, repo, "implicit")
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    cli.main(["show"])
    assert "implicit" in capsys.readouterr().out


def test_show_with_no_context_names_both_ways_to_fix_it(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["show"])
    assert caught.value.code == 1
    assert "no work item" in capsys.readouterr().err


def test_search_renders_results(app, capsys):
    cli.main(["search", "anything", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "anything"


def test_an_operation_failure_is_a_kraft_message_on_stderr(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["show", "no-such-item"])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout stays clean so --json stays pipeable
    assert captured.err.startswith("kraft: ")
    assert "404" in captured.err


def test_create_uses_the_cwd_repo_and_lands_paused(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    _connect(repo)
    monkeypatch.chdir(repo)
    cli.main(["create", "filed from a terminal", "--json"])
    created = json.loads(capsys.readouterr().out)
    assert created["title"] == "filed from a terminal"
    # an agent (or a human) files work; a human starts it from the board
    assert created["status"] == "paused"


def test_create_outside_a_connected_repo_says_how_to_fix_it(app, tmp_path, monkeypatch, capsys):
    stranger = make_repo(tmp_path, name="stranger")
    monkeypatch.chdir(stranger)
    with pytest.raises(SystemExit) as caught:
        cli.main(["create", "nowhere"])
    assert caught.value.code == 1
    err = capsys.readouterr().err
    # the advice matches the situation: this is a git repo, it just is not
    # connected, so `kraft connect` is the fix and the message must name it
    assert "kraft connect" in err
    assert str(stranger) in err


def test_create_outside_any_git_repo_says_something_else(app, tmp_path, monkeypatch, capsys):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    monkeypatch.chdir(plain)
    with pytest.raises(SystemExit) as caught:
        cli.main(["create", "nowhere"])
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "kraft connect" not in err  # there is nothing here to connect
    assert "no repo" in err


def test_create_through_the_mcp_door_resolves_the_cwd_repo(app, tmp_path, monkeypatch):
    """The resolution the CLI does in `_repo_scope` lives in `client.py` too, so
    an agent standing in a connected repo does not have to name it (spec F §2.4)."""
    import asyncio

    repo = make_repo(tmp_path)
    _connect(repo)
    monkeypatch.chdir(repo)
    created = asyncio.run(client.create_work_item("filed by an agent"))
    assert created["status"] == "paused"


def test_reject_requires_a_note(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["reject", "Kraft-whatever"])
    assert caught.value.code == 2  # missing required argument is a usage error
    assert "--note" in capsys.readouterr().err


def test_a_worker_cannot_act_on_its_own_work_item(app, tmp_path, monkeypatch, capsys):
    """The §6 rule-2 guard fires through the CLI door too, not only through MCP."""
    repo = make_repo(tmp_path)
    wid = _make_item(app, repo, "mine to do, not to approve")
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    with pytest.raises(SystemExit) as caught:
        cli.main(["approve"])
    assert caught.value.code == 1
    assert "cannot act on its own work item" in capsys.readouterr().err


def test_resume_starts_a_paused_item(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(app, repo, "start me")
    cli.main(["resume", wid, "--steer", "go left", "--json"])
    assert capsys.readouterr().out.strip()  # the API's response, whatever shape it has


def test_pause_on_a_paused_item_surfaces_the_api_error(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(app, repo, "already paused")
    with pytest.raises(SystemExit) as caught:
        cli.main(["pause", wid])
    assert caught.value.code == 1
    assert capsys.readouterr().err.startswith("kraft: ")
