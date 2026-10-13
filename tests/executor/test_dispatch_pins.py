"""A launch restores the add-on versions its item pinned before it reads the
agent profile table: a profile's `fallback:` list comes from the add-on, and
the add-on is not there until its store is (`dispatch.restore_pins`)."""

import json
import time

import pytest
from support.addons import (
    AGENT,
    accept_repo_library,
    drop_store,
    install_release,
    pinned_chain,
    repo_with_library,
)
from support.harness import v1_walk

from kraft import paths
from kraft.addons import load


async def test_the_fallback_table_is_read_after_the_pins_are_restored(
    tmp_path, repo, fake_agent, monkeypatch
):
    """The item pinned 1.0.0, whose `deep` falls back to `cheap`; the store is
    gone and the lock has moved on. The list is seen at the first launch."""
    pins, store = install_release(tmp_path, "1.0.0")
    install_release(tmp_path, "1.1.0", fallback=False)
    drop_store(store)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_RATE_LIMIT_MODELS", "opus")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_RESETS_AT", str(int(time.time()) + 3600))
    task = {
        "id": "implement",
        "kind": "agent",
        "harness": "claude",
        "prompt": "Do it.",
        "skill": "release:notes",
        "profile": "release:deep",
    }
    chain = pinned_chain([{"id": "build", "kind": "exec", "tasks": [task]}], pins, repo=repo)

    status, *_ = await v1_walk(tmp_path, chain, repo=repo)

    models = [argv[argv.index("--model") + 1] for argv in fake_agent.argv()]
    assert models == ["opus", "haiku"]  # `deep` was rate-limited and its list was walked
    assert status == "completed"
    assert store.is_dir()
    assert "the 1.0.0 method" in json.dumps(fake_agent.argv()[0])


@pytest.mark.parametrize(
    "item_repo_id, launched",
    [
        pytest.param("app", True, id="own-repo-item"),
        pytest.param("web", False, id="another-repos-item"),
    ],
)
async def test_a_launch_reads_its_own_repos_library_unpinned(
    tmp_path, repo, fake_agent, item_repo_id, launched
):
    """Dispatch hands the item's repository id to the launch: an item in
    `app` reads app's library from the lock, one in `web` is stopped."""
    library = {"tasks": {"base": {**AGENT, "harness": "claude"}}}
    app = repo_with_library(tmp_path, library=library, skills={"notes": "the app method"})
    accept_repo_library(paths.config_dir(), load.addons_dir(), app, "app")
    task = {
        "id": "t",
        "kind": "agent",
        "harness": "claude",
        "prompt": "Do it.",
        "skill": "app:notes",
    }
    chain = pinned_chain([{"id": "build", "kind": "exec", "tasks": [task]}], {}, repo=repo)

    status, *_ = await v1_walk(tmp_path, chain, repo=repo, item_repo_id=item_repo_id)

    assert ("the app method" in json.dumps(fake_agent.argv())) is launched
    assert (status == "completed") is launched
