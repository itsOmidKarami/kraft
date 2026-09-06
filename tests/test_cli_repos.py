"""repos, connect, path, open — the repo and worktree verbs."""

from __future__ import annotations

import asyncio
import json

import pytest
from support.harness import make_repo

from kraft import cli, client

# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.http()
# to the ASGI app with the lifespan entered per client.


def _make_item(repo, title="locate me"):
    async def go():
        async with client.http() as http:
            response = await http.post(
                "/work-items", json={"title": title, "repo": str(repo), "autostart": False}
            )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return asyncio.run(go())


def test_repos_is_empty_until_something_is_connected(app):
    assert asyncio.run(client.repos()) == []


def test_repos_lists_a_connected_repo(app, tmp_path):
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    listed = asyncio.run(client.repos())
    assert [entry["path"] for entry in listed] == [str(repo)]


def test_open_worktree_without_a_worktree_is_a_readable_404(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)  # paused, never run: no worktree yet
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.open_worktree(wid))


def test_repos_marks_the_repo_you_are_standing_in(app, tmp_path, monkeypatch, capsys):
    # a wide terminal so the last column does not truncate the tmp path away
    monkeypatch.setenv("COLUMNS", "300")
    here = make_repo(tmp_path, name="here")
    there = make_repo(tmp_path, name="there")
    asyncio.run(client.ensure_repo(str(here)))
    asyncio.run(client.ensure_repo(str(there)))
    monkeypatch.chdir(here)
    cli.main(["repos"])
    lines = capsys.readouterr().out.splitlines()
    marked = [line for line in lines if line.startswith("*")]
    assert len(marked) == 1
    assert str(here) in marked[0]


def test_repos_json_is_the_raw_list(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    cli.main(["repos", "--json"])
    assert json.loads(capsys.readouterr().out) == asyncio.run(client.repos())


def test_connect_defaults_to_the_cwd(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    monkeypatch.chdir(repo)
    cli.main(["connect"])
    assert str(repo) in capsys.readouterr().out
    assert [entry["path"] for entry in asyncio.run(client.repos())] == [str(repo)]


def test_connect_twice_is_fine_and_says_so(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    cli.main(["connect", str(repo)])
    capsys.readouterr()
    cli.main(["connect", str(repo)])  # must not raise SystemExit
    assert "already connected" in capsys.readouterr().out


def test_connect_a_non_git_directory_surfaces_the_api_error(app, tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(SystemExit) as caught:
        cli.main(["connect", str(plain)])
    assert caught.value.code == 1
    assert "not a git repository" in capsys.readouterr().err


def test_path_prints_exactly_one_line(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["path", wid])
    out = capsys.readouterr().out
    # consumed by cd "$(kraft path ID)": one line, no decoration, nothing else
    assert out.endswith("\n")
    assert out.count("\n") == 1
    assert out.strip() == asyncio.run(client.get_work_item(wid))["worktree_path"]


def test_cd_is_an_alias_for_path(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["path", wid])
    expected = capsys.readouterr().out
    cli.main(["cd", wid])
    assert capsys.readouterr().out == expected


def test_path_defaults_to_the_resolved_work_item(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    cli.main(["path"])
    assert wid in capsys.readouterr().out


def test_path_shell_prints_a_function(capsys):
    cli.main(["path", "--shell"])
    out = capsys.readouterr().out
    assert "kcd()" in out or "function" in out
    assert "kraft path" in out


def test_open_on_a_headless_server_is_a_kraft_message(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_open(work_item_id=None, editor=None):
        raise ValueError("kraft 501: no editor available on the server for default")

    monkeypatch.setattr(client, "open_worktree", fake_open)
    with pytest.raises(SystemExit) as caught:
        cli.main(["open", wid])
    assert caught.value.code == 1
    assert "501" in capsys.readouterr().err


def test_open_passes_the_editor_through(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    seen = {}

    async def fake_open(work_item_id=None, editor=None):
        seen.update(work_item_id=work_item_id, editor=editor)
        return {"path": "/wt", "editor": editor or "system"}

    monkeypatch.setattr(client, "open_worktree", fake_open)
    cli.main(["open", wid, "--editor", "zed"])
    assert seen == {"work_item_id": wid, "editor": "zed"}
