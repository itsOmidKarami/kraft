"""`kraft.when_free`: when Kraft is free, what waits for it, and the claim that
keeps new work from starting while an update installs and the server restarts."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kraft import apply, update, when_free
from kraft.cli import admin


@pytest.fixture(autouse=True)
def _no_claim_leaks_between_tests():
    """The claim is a module-level flag: one test that takes it would refuse every start after."""
    when_free.release_claim()
    yield
    when_free.release_claim()


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


def test_the_rollback_record_is_written_in_two_phases(run_dirs):
    assert when_free.read_rollback(run_dirs) is None
    when_free.write_rollback(run_dirs, old_pid=7, from_version="2.2.0", requirement=None)
    when_free.write_rollback(run_dirs, config_snapshot="/x", db_snapshots={})

    assert when_free.read_rollback(run_dirs) == {
        "old_pid": 7,
        "from_version": "2.2.0",
        "requirement": None,
        "config_snapshot": "/x",
        "db_snapshots": {},
    }
    when_free.clear_rollback(run_dirs)
    assert when_free.read_rollback(run_dirs) is None


def test_a_snapshot_restores_config_and_databases_and_drops_a_stale_wal(tmp_path, run_dirs):
    import sqlite3

    config = tmp_path / "config"
    (config / "chains").mkdir(parents=True)
    (config / "access.yaml").write_text("port: 8765\n")
    (config / "chains" / "default.yaml").write_text("id: default\n")
    with sqlite3.connect(run_dirs.db) as c:
        c.execute("CREATE TABLE t (v TEXT)")
        c.execute("INSERT INTO t VALUES ('before')")
    record = when_free.snapshot(run_dirs, config)

    (config / "access.yaml").write_text("port: 1\n")
    (config / "chains" / "default.yaml").unlink()
    (config / "new.yaml").write_text("x: 1\n")
    with sqlite3.connect(run_dirs.db) as c:
        c.execute("UPDATE t SET v = 'after'")
    wal = run_dirs.db.parent / f"{run_dirs.db.name}-wal"
    wal.write_text("stale")

    when_free.restore_snapshot(run_dirs, record)

    assert (config / "access.yaml").read_text() == "port: 8765\n"
    assert (config / "chains" / "default.yaml").read_text() == "id: default\n"
    assert not (config / "new.yaml").exists()
    assert not wal.exists()
    with sqlite3.connect(run_dirs.db) as c:
        assert c.execute("SELECT v FROM t").fetchone()[0] == "before"


# ── apply_once ───────────────────────────────────────────────────────────────


@pytest.fixture
def attempt(state, monkeypatch, tmp_path):
    """A release install that could update itself, with the installer, the
    start check, the snapshot, the reinstall and the restarter faked. `calls`
    records them in order; `app` is what `apply_once` takes."""

    monkeypatch.delenv("KRAFT_NO_UPDATE_CHECK", raising=False)
    calls = []
    installed = {"version": "2.2.0"}
    busy = {"now": False}
    seen = {}
    release = {"result": update.Release("v2.2.1", "https://x/k.whl")}
    prechecks = {"reason": None}
    performs = {"exit": 0, "raises": None}
    start_check = {"why": None}

    def perform(rel, **_k):
        calls.append(("perform", rel.tag))
        if performs["raises"]:
            raise SystemExit(performs["raises"])
        installed["version"] = rel.tag.removeprefix("v")
        return performs["exit"]

    def latest_in_major(major, **kwargs):
        calls.append(("latest_in_major", major, kwargs["channel"]))
        return release["result"]

    def take(run_dirs, config_dir):
        calls.append(("snapshot",))
        busy["now"] = seen.get("busy_after_snapshot", False)
        return {"config_dir": str(config_dir), "config_snapshot": "/s", "db_snapshots": {}}

    monkeypatch.setattr(update, "installed", lambda: installed["version"])
    monkeypatch.setattr(update, "installed_requirement", lambda: REQUIREMENT)
    monkeypatch.setattr(update, "_python", lambda: "3.13")
    monkeypatch.setattr(update, "perform", perform)
    monkeypatch.setattr(update, "latest_in_major", latest_in_major)
    monkeypatch.setattr(
        admin,
        "update_prechecks",
        lambda rel, **kw: calls.append(("prechecks", kw["interactive"])) or prechecks["reason"],
    )
    monkeypatch.setattr(
        when_free, "_start_check", lambda: calls.append(("start_check",)) or start_check["why"]
    )
    monkeypatch.setattr(when_free, "snapshot", take)
    monkeypatch.setattr(
        when_free, "_reinstall", lambda req: calls.append(("reinstall", req)) or True
    )
    real_counts = when_free.busy_counts
    monkeypatch.setattr(
        when_free, "busy_counts", lambda db: (1, 0) if busy["now"] else real_counts(db)
    )
    state.when_free_restarter = lambda st: calls.append(("restart",))
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        st=state,
        calls=calls,
        installed=installed,
        busy=busy,
        seen=seen,
        release=release,
        prechecks=prechecks,
        performs=performs,
        start_check=start_check,
        config=lambda **s: _configure(state, **s),
        names=lambda: [c[0] for c in calls],
    )


REQUIREMENT = {"name": "kraft-sdlc", "specifier": "==2.2.0"}


async def test_nothing_pending_does_nothing_and_claims_nothing(attempt):
    assert await when_free.apply_once(attempt.app, now=NOON) is None
    assert attempt.names() == []
    assert when_free.claimed() is False


async def test_auto_update_marks_a_patch_installs_it_and_hands_the_restart_over(attempt):
    attempt.config(auto_update=True)

    line = await when_free.apply_once(attempt.app, now=NOON)

    assert attempt.names() == [
        "latest_in_major",
        "prechecks",
        "perform",
        "start_check",
        "snapshot",
        "restart",
    ]
    assert attempt.calls[0] == ("latest_in_major", 2, "stable")
    assert ("prechecks", False) in attempt.calls  # unattended: it asks nothing
    assert when_free.marked(attempt.st)["by"] == "auto"
    assert when_free.claimed() is True  # held until the restart replaces the process
    record = when_free.read_rollback(attempt.st.run_dirs)
    assert (record["from_version"], record["target"], record["kind"]) == (
        "2.2.0",
        "2.2.1",
        "release",
    )
    assert record["requirement"] == REQUIREMENT
    assert record["config_snapshot"] == "/s"
    assert line.startswith("restarting 2.2.0")


async def test_an_rc_install_checks_the_rc_channel(attempt, monkeypatch):
    attempt.installed["version"] = "2.3.0rc1"
    attempt.st.version = "2.3.0rc1"
    attempt.release["result"] = update.Release("v2.3.0rc2", "u")
    attempt.config(auto_update=True)
    await when_free.apply_once(attempt.app, now=NOON)
    assert attempt.calls[0] == ("latest_in_major", 2, "rc")


async def test_the_configured_channel_beats_the_installed_ones(attempt):
    attempt.config(auto_update=True, channel="beta")
    await when_free.apply_once(attempt.app, now=NOON)
    assert attempt.calls[0] == ("latest_in_major", 2, "beta")


async def test_it_never_auto_marks_a_major(attempt):
    """The check asks for the installed major only; a 3.0.0 on the feed is
    shown on About and taken on a person's mark."""
    attempt.release["result"] = None
    attempt.config(auto_update=True)
    assert await when_free.apply_once(attempt.app, now=NOON) is None
    assert attempt.calls == [("latest_in_major", 2, "stable")]
    assert when_free.marked(attempt.st) is None


async def test_a_dev_install_never_marks(attempt, monkeypatch):
    monkeypatch.setattr(update, "is_dev_install", lambda entry=None: True)
    attempt.config(auto_update=True)
    assert await when_free.apply_once(attempt.app, now=NOON) is None
    assert attempt.calls == []


async def test_the_check_off_switch_stops_auto_update(attempt, monkeypatch):
    monkeypatch.setenv("KRAFT_NO_UPDATE_CHECK", "1")
    attempt.config(auto_update=True)
    assert await when_free.apply_once(attempt.app, now=NOON) is None
    assert attempt.calls == []


async def test_auto_update_leaves_a_persons_mark_alone(attempt):
    when_free.mark(attempt.st, "v3.0.0", "you")
    attempt.config(auto_update=True)
    await when_free._auto_mark(attempt.st, when_free.settings(attempt.st))
    assert when_free.marked(attempt.st)["tag"] == "v3.0.0"


async def test_busy_at_claim_installs_nothing(attempt, database, item_on, run_dirs, monkeypatch):
    from support.harness import v1_named_chain

    it = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation")
    await _set_status(database, it.id, "active")
    when_free.mark(attempt.st, "v2.2.1", "you")
    # Free when pending() looked, busy when the claim did.
    monkeypatch.setattr(
        when_free, "pending", lambda st, now=None: when_free.Pending(release="v2.2.1")
    )

    assert await when_free.apply_once(attempt.app, now=NOON) is None

    assert "perform" not in attempt.names() and when_free.claimed() is False


async def test_a_precheck_that_would_ask_refuses_the_install(attempt):
    when_free.mark(attempt.st, "v2.2.1", "you")
    attempt.prechecks["reason"] = "release@acme would stop loading"

    line = await when_free.apply_once(attempt.app, now=NOON)

    assert "perform" not in attempt.names() and "restart" not in attempt.names()
    assert line.startswith("refused v2.2.1")
    assert when_free.marked(attempt.st) is None
    assert when_free.claimed() is False
    assert when_free.read_rollback(attempt.st.run_dirs) is None
    assert when_free.outcome(attempt.st)["status"] == "refused"
    assert when_free.failed_key(attempt.st) == "v2.2.1"


async def test_perform_raising_systemexit_is_a_refusal_too(attempt):
    when_free.mark(attempt.st, "v2.2.1", "you")
    attempt.performs["raises"] = "uv is not on PATH"

    await when_free.apply_once(attempt.app, now=NOON)

    assert "restart" not in attempt.names()
    assert when_free.marked(attempt.st) is None and when_free.claimed() is False
    assert "uv is not on PATH" in when_free.outcome(attempt.st)["detail"]


async def test_an_install_that_exits_nonzero_is_put_back(attempt):
    when_free.mark(attempt.st, "v2.2.1", "you")
    attempt.performs["exit"] = 2

    await when_free.apply_once(attempt.app, now=NOON)

    assert ("reinstall", REQUIREMENT) in attempt.calls and "restart" not in attempt.names()
    assert when_free.outcome(attempt.st)["status"] == "failed"
    assert when_free.claimed() is False


async def test_a_start_check_that_fails_on_a_release_puts_the_old_one_back(attempt):
    when_free.mark(attempt.st, "v2.2.1", "you")
    attempt.start_check["why"] = "access.yaml: port 'x'"

    await when_free.apply_once(attempt.app, now=NOON)

    assert attempt.names() == ["prechecks", "perform", "start_check", "reinstall"]
    assert attempt.calls[-1] == ("reinstall", REQUIREMENT)
    outcome = when_free.outcome(attempt.st)
    assert outcome["status"] == "failed" and "access.yaml" in outcome["detail"]
    assert when_free.failed_key(attempt.st) == "v2.2.1"
    assert when_free.claimed() is False
    assert when_free.read_rollback(attempt.st.run_dirs) is None


async def test_becoming_busy_before_the_restart_keeps_the_install_and_waits(attempt):
    when_free.mark(attempt.st, "v2.2.1", "you")
    attempt.seen["busy_after_snapshot"] = True

    line = await when_free.apply_once(attempt.app, now=NOON)

    assert "restart" not in attempt.names() and when_free.claimed() is False
    assert "went busy" in line
    # The install stays; once free again the next look goes straight to the restart.
    attempt.seen["busy_after_snapshot"] = False
    attempt.busy["now"] = False
    attempt.calls.clear()
    attempt.st.version = "2.2.0"

    await when_free.apply_once(attempt.app, now=NOON)

    assert attempt.names() == ["start_check", "snapshot", "restart"]
    assert when_free.read_rollback(attempt.st.run_dirs)["requirement"] == REQUIREMENT


async def test_outside_the_allowed_hours_nothing_happens(attempt):
    when_free.mark(attempt.st, "v2.2.1", "you")
    attempt.config(allowed_hours=["01:00-06:00"])
    assert await when_free.apply_once(attempt.app, now=NOON) is None
    assert attempt.calls == [] and when_free.claimed() is False


async def test_a_pending_settings_restart_installs_nothing(attempt, monkeypatch):
    monkeypatch.setattr(
        apply, "pending", lambda _st: {"restart": _items("bind changed"), "reload": []}
    )
    attempt.config(restart_when_free=True)

    await when_free.apply_once(attempt.app, now=NOON)

    assert attempt.names() == ["start_check", "snapshot", "restart"]
    record = when_free.read_rollback(attempt.st.run_dirs)
    assert (record["kind"], record["requirement"], record["target"]) == ("settings", None, None)


async def test_a_start_check_that_fails_on_a_settings_restart_leaves_it_unapplied(
    attempt, monkeypatch
):
    monkeypatch.setattr(
        apply, "pending", lambda _st: {"restart": _items("bind changed"), "reload": []}
    )
    attempt.config(restart_when_free=True)
    attempt.start_check["why"] = "port 'x'"

    await when_free.apply_once(attempt.app, now=NOON)

    assert attempt.names() == ["start_check"]  # nothing installed, so nothing reinstalled
    assert when_free.outcome(attempt.st)["status"] == "failed"
    assert when_free.claimed() is False


async def test_an_exception_after_the_claim_releases_it(attempt):
    when_free.mark(attempt.st, "v2.2.1", "you")

    def explode(st):
        raise RuntimeError("boom")

    attempt.st.when_free_restarter = explode
    with pytest.raises(RuntimeError):
        await when_free.apply_once(attempt.app, now=NOON)
    assert when_free.claimed() is False


async def test_the_loop_survives_an_exception(attempt, monkeypatch):
    looks = []

    async def apply_once(app, *, now=None):
        looks.append(1)
        if len(looks) == 1:
            when_free.claim(attempt.st.db)
            raise RuntimeError("boom")
        return "ran"

    monkeypatch.setattr(when_free, "apply_once", apply_once)
    monkeypatch.setattr(when_free, "INTERVAL_S", 0.01)
    task = asyncio.ensure_future(when_free.loop(attempt.app))
    try:
        for _ in range(200):
            if len(looks) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert len(looks) >= 2
    assert when_free.claimed() is False
