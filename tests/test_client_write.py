"""The write half of the agent surface: create an item, register a repo."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo
from test_client_read import run_with_app

from kraft import client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    import kraft.api as api

    monkeypatch.setattr(
        client,
        "http",
        lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://kraft"
        ),
    )
    return api


def test_create_work_item_never_starts_it(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("from an agent", repo=str(repo))
        return created, await client.get_work_item(created["id"])

    created, item = run_with_app(wired, scenario)
    assert created["status"] == "paused"
    # an agent cannot spend tokens unattended — design §6 rule 1
    assert item["status"] == "paused"
    assert item["current_node_id"] is None


def test_create_work_item_without_a_repo_or_a_context_says_so(wired, tmp_path, monkeypatch):
    """With no repo argument and no worktree, there is nothing to guess."""

    async def scenario():
        return await client.create_work_item("no repo anywhere")

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="repo"):
        run_with_app(wired, scenario)


def test_ensure_repo_registers_a_repo_kraft_has_not_seen(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        return await client.ensure_repo(str(repo))

    entry = run_with_app(wired, scenario)
    assert entry["path"] == str(Path(repo).resolve())
    assert entry["already_connected"] is False


def test_ensure_repo_is_idempotent(wired, tmp_path):
    """A 409 from POST /repos means 'already connected', which is the goal state."""
    repo = make_repo(tmp_path)

    async def scenario():
        await client.ensure_repo(str(repo))
        return await client.ensure_repo(str(repo))

    entry = run_with_app(wired, scenario)
    assert entry["path"] == str(Path(repo).resolve())
    assert entry["already_connected"] is True


def test_ensure_repo_reports_a_path_that_is_not_a_repo(wired, tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    async def scenario():
        return await client.ensure_repo(str(plain))

    with pytest.raises(ValueError, match="400"):
        run_with_app(wired, scenario)
