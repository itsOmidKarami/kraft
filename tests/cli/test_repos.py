"""repos, connect, path, open — the repo and worktree verbs."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
from pathlib import Path

import pytest
import yaml
from support.harness import make_repo, make_repo_with_submodule

from kraft import cli, client

# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.transport.http()
# to the ASGI app with the lifespan entered per client.


def _make_item(repo, title="locate me"):
    async def go():
        async with client.transport.http() as http:
            response = await http.post(
                "/api/work-items", json={"title": title, "repo": str(repo), "autostart": False}
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
    cli.main(["repo", "list"])
    lines = capsys.readouterr().out.splitlines()
    marked = [line for line in lines if line.startswith("*")]
    assert len(marked) == 1
    assert str(here) in marked[0]


def test_repos_json_is_the_raw_list(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    cli.main(["repo", "list", "--json"])
    assert json.loads(capsys.readouterr().out) == asyncio.run(client.repos())


def test_connect_defaults_to_the_cwd(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    monkeypatch.chdir(repo)
    cli.main(["repo", "connect"])
    assert str(repo) in capsys.readouterr().out
    assert [entry["path"] for entry in asyncio.run(client.repos())] == [str(repo)]


def test_connect_twice_is_fine_and_says_so(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    cli.main(["repo", "connect", str(repo)])
    capsys.readouterr()
    cli.main(["repo", "connect", str(repo)])  # must not raise SystemExit
    assert "already connected" in capsys.readouterr().out


def test_connect_a_non_git_directory_surfaces_the_api_error(app, tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(SystemExit) as caught:
        cli.main(["repo", "connect", str(plain)])
    assert caught.value.code == 1
    assert "not a git repository" in capsys.readouterr().err


def test_path_prints_exactly_one_line(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["repo", "path", wid])
    out = capsys.readouterr().out
    # consumed by cd "$(kraft path ID)": one line, no decoration, nothing else
    assert out.endswith("\n")
    assert out.count("\n") == 1
    assert out.strip() == asyncio.run(client.get_work_item(wid))["worktree_path"]


def test_cd_is_an_alias_for_path(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["repo", "path", wid])
    expected = capsys.readouterr().out
    cli.main(["repo", "cd", wid])
    assert capsys.readouterr().out == expected


def test_path_defaults_to_the_resolved_work_item(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    cli.main(["repo", "path"])
    assert wid in capsys.readouterr().out


def test_path_shell_prints_a_function(capsys):
    cli.main(["repo", "path", "--shell"])
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
        cli.main(["repo", "open", wid])
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
    cli.main(["repo", "open", wid, "--editor", "zed"])
    assert seen == {"work_item_id": wid, "editor": "zed"}


def test_path_rejects_json_rather_than_ignoring_it(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    with pytest.raises(SystemExit) as caught:
        cli.main(["repo", "path", wid, "--json"])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "kraft show --json" in captured.err
    # nothing on stdout: a caller that piped this must not get a path anyway
    assert captured.out == ""


def test_repos_says_disabled_in_words_not_only_in_colour(app, tmp_path, monkeypatch, capsys):
    """pytest has no tty, so this is exactly the piped case: dim paint is gone
    and the word has to carry it (spec D §3, spec F §2.5)."""
    monkeypatch.setenv("COLUMNS", "300")
    on = make_repo(tmp_path, name="on")
    off = make_repo(tmp_path, name="off")

    async def go():
        async with client.transport.http() as http:
            r = await http.post("/api/repos", json={"path": str(on), "test_command": "pytest"})
            assert r.status_code == 201, r.text
            r = await http.post("/api/repos", json={"path": str(off), "test_command": "pytest"})
            assert r.status_code == 201, r.text
            response = await http.patch(
                "/api/repos", params={"path": str(off)}, json={"enabled": False}
            )
        assert response.status_code < 400, response.text

    asyncio.run(go())
    cli.main(["repo", "list"])
    lines = capsys.readouterr().out.splitlines()
    assert "disabled" in next(line for line in lines if str(off) in line)
    assert "enabled" in next(line for line in lines if str(on) in line)


def test_repos_hides_auto_connected_children_by_default(app, tmp_path, monkeypatch, capsys):
    """Kraft-jknn0: the CLI's answer to Settings' collapsed Detected section
    -- connecting a superproject must not dump every auto-connected,
    never-touched child into the plain table."""
    monkeypatch.setenv("COLUMNS", "300")
    root, _sub = make_repo_with_submodule(tmp_path)
    child = root / "repos" / "pkg"
    asyncio.run(client.ensure_repo(str(root)))

    cli.main(["repo", "list"])
    out = capsys.readouterr().out
    assert str(root) in out
    assert str(child) not in out
    assert "1 more detected, not managed" in out
    assert "--all" in out


def test_repos_all_shows_the_detected_children(app, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "300")
    root, _sub = make_repo_with_submodule(tmp_path)
    child = root / "repos" / "pkg"
    asyncio.run(client.ensure_repo(str(root)))

    cli.main(["repo", "list", "--all"])
    out = capsys.readouterr().out
    assert str(root) in out
    assert str(child) in out
    assert "detected" not in out


def test_repos_json_ignores_the_managed_filter(app, tmp_path, capsys):
    """`--json` is the raw API payload contract (common.emit's docstring) --
    it must not silently drop rows `--all` would otherwise be needed for."""
    root, _sub = make_repo_with_submodule(tmp_path)
    child = root / "repos" / "pkg"
    asyncio.run(client.ensure_repo(str(root)))

    cli.main(["repo", "list", "--json"])
    paths = {entry["path"] for entry in json.loads(capsys.readouterr().out)}
    assert paths == {str(root), str(child)}


def test_disconnect_removes_the_connected_repo(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    cli.main(["repo", "disconnect", str(repo)])
    assert str(repo) in capsys.readouterr().out
    assert asyncio.run(client.repos()) == []


def test_disconnect_of_an_unconnected_path_is_a_readable_404(app, tmp_path, capsys):
    """DELETE /repos answers 204 with no body, so the client must not try to
    parse one — and its 404 has to read as a sentence, like every other verb."""
    repo = make_repo(tmp_path)
    with pytest.raises(SystemExit) as caught:
        cli.main(["repo", "disconnect", str(repo)])
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "404" in err
    assert "not connected" in err
    assert "Traceback" not in err


def test_disconnect_from_inside_a_worktree_disconnects_the_repo(app, tmp_path, monkeypatch, capsys):
    """The symmetric half of Kraft-97e: after Task 1 the stored path is the main
    checkout, so sending the raw cwd from a worktree would 404. `disconnect_repo`
    probes first, the way `ensure_repo` does for its 409 branch."""
    import subprocess

    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree), "-b", "wt-branch"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(worktree)
    cli.main(["repo", "disconnect"])
    assert asyncio.run(client.repos()) == []


def test_disconnect_removes_an_entry_registered_under_a_worktree_path(
    app, tmp_path, monkeypatch, capsys
):
    """Kraft-7qgb, the gap Kraft-sws6 + Kraft-97e left between them. An entry
    written before 97e is keyed by a *worktree* path, and every CLI door probes
    now, so the probe rewrites the argument to the main checkout and no door can
    address the stale entry -- removing one needed a raw
    `curl -X DELETE /repos?path=...`.

    Written into repos.yaml by hand because that is the only way the state
    exists: `POST /repos` stores git's resolved toplevel, so the API cannot
    create this row any more.
    """
    import subprocess

    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree), "-b", "wt-branch"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    repos_yaml = Path(os.environ["KRAFT_TEMPLATES_DIR"]) / "repos.yaml"
    on_disk = yaml.safe_load(repos_yaml.read_text())
    on_disk["repos"].append({"path": str(worktree), "name": "stale", "enabled": True})
    repos_yaml.write_text(yaml.safe_dump(on_disk))

    cli.main(["repo", "disconnect", str(worktree)])
    assert str(worktree) in capsys.readouterr().out
    assert [entry["path"] for entry in asyncio.run(client.repos())] == [str(repo)]


def test_repos_yaml_round_trips_a_test_command(tmp_path):
    from kraft import config

    p = tmp_path / "repos.yaml"
    config.save_repos(p, [{"path": "/r", "test_command": "just ci"}])
    (entry,) = config.load_repos(p, validate_steering=False)
    assert entry["test_command"] == "just ci"


def test_repos_yaml_defaults_test_command_to_none(tmp_path):
    from kraft import config

    p = tmp_path / "repos.yaml"
    p.write_text("repos:\n  - path: /r\n")
    (entry,) = config.load_repos(p, validate_steering=False)
    assert entry["test_command"] is None


def test_repos_yaml_rejects_a_non_string_test_command(tmp_path):
    from kraft import config

    p = tmp_path / "repos.yaml"
    p.write_text("repos:\n  - path: /r\n    test_command: 3\n")
    with pytest.raises(config.ConfigError):
        config.load_repos(p, validate_steering=False)


# ── test_scopes (Kraft-9wzy) ─────────────────────────────────────────────────


def test_repos_yaml_does_not_synthesize_test_scopes_from_test_command(tmp_path):
    """The wrap-into-`["**"]` happens where scopes are used (`executor.py`),
    not on load -- baking it into the loaded entry is what let a `PATCH
    /repos` re-save persist a stale synthesized scope past a later
    `test_command` edit (verify finding, Kraft-9wzy)."""
    from kraft import config

    p = tmp_path / "repos.yaml"
    config.save_repos(p, [{"path": "/r", "test_command": "just ci"}])
    (entry,) = config.load_repos(p, validate_steering=False)
    assert entry["test_scopes"] is None
    assert entry["test_command"] == "just ci"


def test_repos_yaml_test_command_edit_is_not_shadowed_by_a_stale_scope(tmp_path):
    """Regression for the exact sequence that used to go stale: load (which
    used to synthesize and persist a scope for the old command), edit
    `test_command`, save, reload."""
    from kraft import config

    p = tmp_path / "repos.yaml"
    config.save_repos(p, [{"path": "/r", "test_command": "just ci"}])
    (entry,) = config.load_repos(p, validate_steering=False)
    entry["test_command"] = "just ci-v2"
    config.save_repos(p, [entry])
    (reloaded,) = config.load_repos(p, validate_steering=False)
    assert reloaded["test_command"] == "just ci-v2"
    assert reloaded["test_scopes"] is None


def test_repos_yaml_test_scopes_is_none_when_both_fields_are_absent(tmp_path):
    from kraft import config

    p = tmp_path / "repos.yaml"
    p.write_text("repos:\n  - path: /r\n")
    (entry,) = config.load_repos(p, validate_steering=False)
    assert entry["test_scopes"] is None


def test_repos_yaml_round_trips_explicit_test_scopes(tmp_path):
    from kraft import config

    p = tmp_path / "repos.yaml"
    scopes = [
        {"paths": ["frontend/**"], "command": "just test-ui"},
        {"paths": ["*", "src/**", "!frontend/**"], "command": "just test"},
    ]
    config.save_repos(p, [{"path": "/r", "test_scopes": scopes}])
    (entry,) = config.load_repos(p, validate_steering=False)
    assert entry["test_scopes"] == scopes


def test_repos_yaml_rejects_test_scopes_missing_paths(tmp_path):
    from kraft import config

    p = tmp_path / "repos.yaml"
    p.write_text("repos:\n  - path: /r\n    test_scopes:\n      - command: just test\n")
    with pytest.raises(config.ConfigError):
        config.load_repos(p, validate_steering=False)


def test_repos_yaml_rejects_test_scopes_with_an_empty_command(tmp_path):
    from kraft import config

    p = tmp_path / "repos.yaml"
    p.write_text(
        "repos:\n  - path: /r\n    test_scopes:\n      - paths: ['**']\n        command: ''\n"
    )
    with pytest.raises(config.ConfigError):
        config.load_repos(p, validate_steering=False)


def test_repos_yaml_rejects_a_non_list_test_scopes(tmp_path):
    from kraft import config

    p = tmp_path / "repos.yaml"
    p.write_text("repos:\n  - path: /r\n    test_scopes: nope\n")
    with pytest.raises(config.ConfigError):
        config.load_repos(p, validate_steering=False)


def test_probe_repo_excludes_the_nested_scope_from_the_root_scope(tmp_path):
    """Mirrors Kraft's own layout — root `pyproject.toml`, `package.json`
    under `frontend/` — the failure mode Kraft-9wzy names directly."""
    from support.harness import make_repo

    from kraft import config

    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}")

    probed = config.probe_repo(repo)
    nested = next(s for s in probed["test_scopes"] if s["command"] == "npm test")
    assert nested["paths"] == ["frontend/**"]
    root = next(s for s in probed["test_scopes"] if s["command"] == "uv run pytest -q")
    assert "frontend" not in root["paths"]
    assert "pyproject.toml" in root["paths"]


def test_probe_repo_root_scope_globs_match_files_inside_its_directories(tmp_path):
    """A bare directory name in `paths` (e.g. "src") never matches
    `fnmatch`-checked paths like "src/foo.py", so a backend-only diff failed
    open to every scope, nested ones included (verify finding, Kraft-9wzy).
    Root-level directories need the `/**` suffix; root-level files don't."""
    from support.harness import make_repo

    from kraft import config

    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    src = repo / "src"
    src.mkdir()
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}")

    probed = config.probe_repo(repo)
    root = next(s for s in probed["test_scopes"] if s["command"] == "uv run pytest -q")
    assert "src/**" in root["paths"]
    assert "pyproject.toml" in root["paths"]
    src_pattern = next(p for p in root["paths"] if p.startswith("src"))
    assert fnmatch.fnmatchcase("src/kraft/config.py", src_pattern)
