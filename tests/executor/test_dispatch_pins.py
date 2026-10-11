"""A launch restores the plugin versions its item pinned before it reads the
agent profile table: a profile's `fallback:` list comes from the plugin, and
the plugin is not there until its store is (`dispatch.restore_pins`)."""

import json
import time

from support.harness import v1_walk
from support.plugins import drop_store, install_release, pinned_chain


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
