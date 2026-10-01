"""The `stop_kind` a bare `ConfigError` from `walk.run_once`'s own runtime-prep
except carries, the other branch of the except `test_walk.py`'s wrapped-
`ConfigError` case covers. A sibling of test_walk.py."""

from __future__ import annotations

from kraft import builtins as kraft_builtins
from kraft.config import ConfigError

from .test_walk import _walk


async def test_a_bare_configerror_preparing_the_runtime_stops_config(
    item_on, fake_agent, monkeypatch
):
    """The other branch of the same except (`walk.run_once`): a `ConfigError`
    that reaches it undressed -- not wrapped in `dispatch.SandboxUnresolved`,
    the shape every repo_entry-read failure takes today -- is `config`."""
    it = await item_on(fake_agent.quick_task)

    async def boom(*a, **kw):
        raise ConfigError("repos.yaml: boom")

    monkeypatch.setattr(kraft_builtins, "ensure_worktree", boom)

    assert await _walk(it) == "needs_human"
    assert it.row()["stop_kind"] == "config"


async def test_needs_human_names_the_session_that_failed(item_on, fake_agent, monkeypatch):
    """Kraft-eh6p. `work_item_needs_human` is the event a human lands on, and
    its reason names the hook ('task failed in node open_mr: on.mr.open'), not
    the failure — which lives in the failed session's log. Without the session
    id on this event the timeline has nothing to hang a 'view log' button on,
    and the only route to the reason is noticing the preceding
    worker_session_exited row."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "error")
    it = await item_on(fake_agent.quick_task)

    assert await _walk(it) == "needs_human"
    failed = [
        e["payload"]["session_id"]
        for e in it.events("worker_session_exited")
        if e["payload"]["status"] == "failed"
    ]
    assert failed, "the scenario did not produce a failed session"
    assert it.events("work_item_needs_human")[0]["payload"].get("session_id") == failed[-1], (
        "needs_human does not name the session whose log holds the reason"
    )
    assert it.row()["stop_kind"] == "failed"
