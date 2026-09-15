"""resolve_repo() and the dead-server message — the two things A adds to client.py."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo, make_repo_with_submodule

from kraft import client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def test_a_dead_server_is_a_sentence_not_a_traceback(monkeypatch, tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "access.yaml").write_text("bind: 127.0.0.1\nport: 8765\n")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    monkeypatch.delenv("KRAFT_PORT", raising=False)

    def refuse(*_args, **_kwargs):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx.AsyncClient, "request", refuse)

    with pytest.raises(ValueError) as caught:
        asyncio.run(client.list_work_items())
    # the address is in the message: KRAFT_PORT means it is not always 8765
    assert "no Kraft server at http://127.0.0.1:8765" in str(caught.value)
    assert "start one with `kraft`" in str(caught.value)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """The app, with client.transport.http() pointed at it in-process (as test_client_read)."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    import kraft.api as api

    monkeypatch.setattr(
        client.transport,
        "http",
        lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://kraft"
        ),
    )
    return api


def run_with_app(api, scenario):
    async def wrapper():
        async with api.app.router.lifespan_context(api.app):
            return await scenario()

    return asyncio.run(wrapper())


def test_resolve_repo_finds_the_connected_repo_containing_the_cwd(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        await client.ensure_repo(str(repo))
        return await client.resolve_repo(repo)

    assert run_with_app(wired, scenario) == str(repo)


def test_resolve_repo_walks_up_from_a_subdirectory(wired, tmp_path):
    repo = make_repo(tmp_path)
    deep = repo / "src" / "nested"
    deep.mkdir(parents=True)

    async def scenario():
        await client.ensure_repo(str(repo))
        return await client.resolve_repo(deep)

    assert run_with_app(wired, scenario) == str(repo)


def test_resolve_repo_maps_a_linked_worktree_to_its_connected_repo(wired, tmp_path):
    """Kraft-tc33: a plain `git worktree add` checkout's `--show-toplevel` is
    itself, not the main repo — resolve_repo must normalize through
    `--git-common-dir` the way `config.probe_repo` already does, or a session
    standing in a worktree of a connected repo reads as unconnected."""
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "side", str(worktree)],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    async def scenario():
        await client.ensure_repo(str(repo))
        return await client.resolve_repo(worktree)

    assert run_with_app(wired, scenario) == str(repo)


def test_resolve_repo_skips_an_auto_connected_disabled_child(wired, tmp_path):
    """Connecting a workspace auto-registers its submodule as a disabled
    `managed: false` child (repos.py `_auto_connect_children`). Phase 1 does
    not change item routing, so a cwd inside that submodule must still
    resolve to the workspace, not the nearer disabled child."""
    root, sub = make_repo_with_submodule(tmp_path)

    async def scenario():
        await client.ensure_repo(str(root))
        return await client.resolve_repo(root / "repos" / "pkg")

    assert run_with_app(wired, scenario) == str(root)


def test_resolve_repo_prefers_a_nested_managed_repo_over_its_ancestor(wired, tmp_path):
    """A hand-connected child inside a connected parent (an operator flipping
    an auto-connected submodule's `managed` flag on via `PATCH /repos`, the
    same edit the Settings screen makes) must resolve to the nearer managed
    repo, not the outermost one — Phase 1 leaves routing unchanged for
    anything the operator connected themselves."""
    root, sub = make_repo_with_submodule(tmp_path)
    child = str(root / "repos" / "pkg")

    async def scenario():
        await client.ensure_repo(str(root))
        # Any PATCH flips `managed` to True (repos.py `update_repo`) — this is
        # the edit the Settings screen makes when an operator connects a
        # detected child by hand. Re-setting the probed name is a no-op
        # otherwise, so it doesn't also need a test_command to pass validation.
        await client.transport._patch(f"/repos?path={quote(child)}", {"name": "pkg"})
        return await client.resolve_repo(root / "repos" / "pkg")

    assert run_with_app(wired, scenario) == child


def test_resolve_repo_is_none_for_an_unconnected_repo(wired, tmp_path):
    repo = make_repo(tmp_path, name="stranger")

    async def scenario():
        return await client.resolve_repo(repo)

    assert run_with_app(wired, scenario) is None


def test_resolve_repo_is_none_outside_any_git_repo(wired, tmp_path):
    plain = tmp_path / "not-git"
    plain.mkdir()

    async def scenario():
        return await client.resolve_repo(plain)

    assert run_with_app(wired, scenario) is None
