"""`kraft.when_free`: when Kraft is free, what waits for it, and the claim that
keeps new work from starting while an update installs and the server restarts."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kraft import apply, update, when_free
from kraft.cli import admin


async def _set_status(database, wid, status):
    await database.write(
        lambda c: c.execute("UPDATE work_items SET status = ? WHERE id = ?", (status, wid))
    )


@pytest.mark.parametrize(
    ("status", "session", "free"),
    [
        pytest.param(None, None, True, id="nothing"),
        pytest.param("active", None, False, id="active"),
        pytest.param("waiting", None, False, id="waiting"),
        pytest.param("queued", None, False, id="queued"),
        pytest.param("rate_limited", None, False, id="rate_limited"),
        pytest.param("needs_human", None, True, id="needs_human-at-gate"),
        pytest.param("paused", None, True, id="paused"),
        pytest.param("blocked", None, True, id="blocked"),
        pytest.param("completed", None, True, id="completed"),
        pytest.param("needs_human", "running", False, id="a-running-session-on-a-needs_human-item"),
    ],
)
async def test_free_means_nothing_going_on(item_on, database, run_dirs, status, session, free):
    from support.harness import v1_named_chain

    if status is not None:
        it = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation")
        await _set_status(database, it.id, status)
        if session:
            await it.session("s1", "implementation.main.implement", running=(1, 1.0))
    assert when_free.is_free(database) is free


@pytest.fixture
async def state(tmp_path, database, run_dirs, monkeypatch):
    """A server state with a settings directory, and a Kraft that could restart
    itself as a service, on a release install."""
    templates = tmp_path / "templates"
    templates.mkdir()
    st = SimpleNamespace(
        db=database, run_dirs=run_dirs, templates_dir=templates, version="2.2.0", tasks={}
    )
    monkeypatch.setattr(admin, "restart_kind", lambda _run_dir=None: "service")
    monkeypatch.setattr(update, "install_kind", lambda: "uv")
    monkeypatch.setattr(update, "is_dev_install", lambda entry=None: False)
    monkeypatch.setattr(update, "installed", lambda: "2.2.0")
    monkeypatch.setattr(apply, "pending", lambda _st: {"restart": [], "reload": []})
    return st


def _configure(st, **settings):
    import yaml

    (st.templates_dir / "update.yaml").write_text(yaml.safe_dump(settings))


def _items(*texts):
    return [{"id": f"item-{n}", "file": "access.yaml", "text": t} for n, t in enumerate(texts)]


NOON = datetime.fromisoformat("2026-10-22T12:00:00")


def _newer_install(monkeypatch, *, installed="2.3.0", running="2.2.0"):
    monkeypatch.setattr(update, "installed", lambda: installed)
    return running


def _a_restart_item(monkeypatch):
    monkeypatch.setattr(
        apply, "pending", lambda _st: {"restart": _items("bind changed"), "reload": []}
    )


def _after_applied(st, monkeypatch):
    when_free.ask_restart(st)
    when_free.record_outcome(
        st, status="applied", key=None, detail="ok", from_version="2.2.0", to_version="2.2.0"
    )


def _marked_outside_hours(st, monkeypatch):
    when_free.mark(st, "v2.3.0", "you")
    _configure(st, allowed_hours=["01:00-06:00"])
    monkeypatch.setattr(update, "is_behind", lambda release: True)


def _failed_mark(st, monkeypatch):
    when_free.mark(st, "v2.3.0", "auto")
    when_free.record_outcome(st, status="failed", key="v2.3.0", detail="would not start")
    when_free.mark(st, "v2.3.0", "auto")
    monkeypatch.setattr(update, "is_behind", lambda release: True)


def _failed_settings(st, monkeypatch):
    _a_restart_item(monkeypatch)
    _configure(st, restart_when_free=True)
    key = when_free._settings_key(_items("bind changed"))
    when_free.record_outcome(st, status="failed", key=key, detail="would not start")


def _failed_then_changed_settings(st, monkeypatch):
    _failed_settings(st, monkeypatch)
    monkeypatch.setattr(
        apply, "pending", lambda _st: {"restart": _items("port changed"), "reload": []}
    )


@pytest.mark.parametrize(
    ("setup", "running", "installed", "restart_when_free", "restarts", "blocked"),
    [
        pytest.param(
            None, "2.2.0", "2.3.0", True, True, None, id="newer-install-restart-when-free-on"
        ),
        pytest.param(
            None,
            "2.2.1.dev51+gaaa",
            "2.2.1.dev60+gbbb",
            True,
            True,
            None,
            id="dev-reinstall-same-base",
        ),
        pytest.param(None, "2.2.0", "2.3.0", False, False, None, id="newer-install-switch-off"),
        pytest.param(None, "2.2.0", "2.1.0", True, False, None, id="older-install-never-restarts"),
        pytest.param(
            lambda st, mp: when_free.ask_restart(st),
            "2.2.0",
            "2.2.0",
            False,
            True,
            None,
            id="one-shot-alone-restarts",
        ),
        pytest.param(
            _after_applied,
            "2.2.0",
            "2.2.0",
            False,
            False,
            None,
            id="one-shot-cleared-after-applied",
        ),
        pytest.param(
            _marked_outside_hours,
            "2.2.0",
            "2.2.0",
            False,
            False,
            "outside 01:00-06:00",
            id="marked-release-outside-hours",
        ),
        pytest.param(
            _failed_mark,
            "2.2.0",
            "2.2.0",
            False,
            False,
            "the last attempt failed",
            id="failed-key-not-retried",
        ),
        pytest.param(
            _failed_settings,
            "2.2.0",
            "2.2.0",
            True,
            True,
            "the last attempt failed",
            id="same-settings-item-stays-failed",
        ),
        pytest.param(
            _failed_then_changed_settings,
            "2.2.0",
            "2.2.0",
            True,
            True,
            None,
            id="changed-settings-item-clears-the-failure",
        ),
    ],
)
async def test_pending_names_what_and_why(
    state, monkeypatch, setup, running, installed, restart_when_free, restarts, blocked
):
    state.version = running
    monkeypatch.setattr(update, "installed", lambda: installed)
    _configure(state, restart_when_free=restart_when_free)
    if setup:
        setup(state, monkeypatch)

    got = when_free.pending(state, NOON)

    assert bool(got.restart) is restarts
    if blocked is None:
        assert not any("failed" in why or why.startswith("outside") for why in got.blocked), (
            got.blocked
        )
    else:
        assert any(blocked in why for why in got.blocked), got.blocked
    if "marked" in (setup.__name__ if setup else ""):
        assert got.release == "v2.3.0"


async def test_pending_says_why_a_busy_kraft_waits(state, monkeypatch, database):
    _newer_install(monkeypatch)
    state.version = "2.2.0"
    _configure(state, restart_when_free=True)
    monkeypatch.setattr(when_free, "busy_counts", lambda _db: (1, 0))
    got = when_free.pending(state, NOON)
    assert got.blocked == ["waiting for 1 running item"]


async def test_pending_says_when_the_server_is_attached_to_a_terminal(state, monkeypatch):
    _newer_install(monkeypatch)
    state.version = "2.2.0"
    _configure(state, restart_when_free=True)
    monkeypatch.setattr(admin, "restart_kind", lambda _run_dir=None: "attached")
    assert when_free.pending(state, NOON).blocked == ["attached to a terminal"]


async def test_nothing_pending_says_nothing(state):
    got = when_free.pending(state, NOON)
    assert not got and got.blocked == []


async def test_a_release_that_is_already_installed_is_not_pending(state, monkeypatch):
    when_free.mark(state, "v2.2.0", "you")
    assert when_free.pending(state, NOON).release is None


async def test_an_apply_chip_restart_lifts_a_failure(state):
    when_free.record_outcome(state, status="failed", key="v2.3.0", detail="x")
    assert when_free.failed_key(state) == "v2.3.0"
    when_free.clear_failure(state)
    assert when_free.failed_key(state) is None


async def test_a_persons_mark_lifts_the_failure_an_auto_mark_may_not(state):
    when_free.record_outcome(state, status="failed", key="v2.3.0", detail="x")
    when_free.mark(state, "v2.3.0", "auto")
    assert when_free.failed_key(state) == "v2.3.0"
    when_free.mark(state, "v2.3.0", "you")
    assert when_free.failed_key(state) is None


async def test_the_mark_survives_a_new_state(state):
    when_free.mark(state, "v2.3.0", "auto")
    fresh = SimpleNamespace(run_dirs=state.run_dirs)
    assert when_free.marked(fresh)["tag"] == "v2.3.0"
    assert when_free.marked(fresh)["by"] == "auto"
    when_free.unmark(fresh)
    assert when_free.marked(state) is None


async def test_a_refused_attempt_drops_the_mark_and_is_not_made_again(state):
    when_free.mark(state, "v3.0.0", "auto")
    when_free.record_outcome(state, status="refused", key="v3.0.0", detail="asks a question")
    assert when_free.marked(state) is None
    assert when_free.failed_key(state) == "v3.0.0"
    assert when_free.outcome(state)["status"] == "refused"


async def test_the_claim_is_taken_only_by_a_free_kraft(item_on, database, run_dirs):
    from support.harness import v1_named_chain

    it = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation")
    await _set_status(database, it.id, "active")
    assert when_free.claim(database) is False
    assert when_free.claimed() is False

    await _set_status(database, it.id, "needs_human")
    assert when_free.claim(database) is True
    assert when_free.claimed() is True
    with pytest.raises(HTTPException) as refused:
        when_free.refuse_if_claimed()
    assert (refused.value.status_code, refused.value.detail) == (503, when_free.MESSAGE)

    when_free.release_claim()
    when_free.refuse_if_claimed()
