"""Outbound notifications (sub-project B). The URL is the first real secret
Kraft stores, so half of these tests are about where it must *not* appear."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd

from kraft import config, events, notify
from kraft import db as kdb
from kraft.templates import CONFIG_FILES


def test_missing_notify_yaml_reads_as_the_shipped_default(tmp_path):
    cfg = config.load_notify(tmp_path / "notify.yaml")
    assert cfg.model_dump() == {
        "enabled": False,
        "url": None,
        "base_url": None,
        "events": ["gate_requested", "work_item_needs_human"],
    }


def test_partial_notify_yaml_fills_in_the_rest(tmp_path):
    path = tmp_path / "notify.yaml"
    path.write_text("enabled: true\n")
    cfg = config.load_notify(path)
    assert cfg.enabled is True
    assert cfg.url is None
    assert cfg.events == ["gate_requested", "work_item_needs_human"]


def test_saved_notify_yaml_is_0600_because_it_holds_a_token(tmp_path):
    path = tmp_path / "notify.yaml"
    config.save_notify(path, {**config.NOTIFY_DEFAULT, "url": "https://ntfy.sh/secret-topic"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_notify_writes_only_the_known_keys(tmp_path):
    path = tmp_path / "notify.yaml"
    config.save_notify(path, {**config.NOTIFY_DEFAULT, "nonsense": 1})
    assert set(config.load_notify(path).model_dump()) == set(config.NOTIFY_DEFAULT)


def test_notify_yaml_is_not_read_as_a_chain_template():
    # load_templates skips CONFIG_FILES; without this, every settings file the
    # UI writes shows up as a broken template and /health goes degraded.
    assert "notify.yaml" in CONFIG_FILES


async def _database(tmp_path):
    return await kdb.Database.open(tmp_path / "orchestrator.db")


async def _seed_item(database, wid="w1", title="Ship the thing"):
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
            "status, created_at, updated_at) VALUES (?, ?, '/r', 'quick-task', '{}', "
            "'active', 'now', 'now')",
            (wid, title),
        )
    )


def _notifier(tmp_path, database, sends, *, status=200, **kw):
    """A Notifier whose transport is a recording stub. `sends` collects
    (url, body) tuples; `status` is what the endpoint answers."""
    cfg = tmp_path / "notify.yaml"
    config.save_notify(
        cfg,
        {**config.NOTIFY_DEFAULT, "enabled": True, "url": "https://hook.invalid/t0ken"},
    )

    async def transport(request: httpx.Request) -> httpx.Response:
        sends.append((str(request.url), json.loads(request.content)))
        return httpx.Response(status)

    n = notify.Notifier(database, cfg, fallback_base_url="http://127.0.0.1:8765", **kw)
    n._transport = httpx.MockTransport(transport)
    return n


async def _drain(database, n: notify.Notifier) -> None:
    """Wake the notifier, wait for the drain to reach the tail, then wait out any
    detached send.

    Both halves are needed. `not n._inflight` is true *before* the drain task has
    even been scheduled, so waiting on that alone passes vacuously — and when
    notifications are disabled nothing is ever put in flight, so the cursor is
    the only signal there is."""
    tail = database.read(
        lambda c: c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
    )
    n.notify()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if n.cursor >= tail and not n._inflight:
            return
    raise AssertionError(f"notifier never settled (cursor={n.cursor}, tail={tail})")


def test_fires_on_gate_requested_with_a_working_link(tmp_path):
    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        sends: list = []
        n = _notifier(tmp_path, database, sends)
        await n.start()
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        await _drain(database, n)
        await n.stop()
        await database.close()

        assert len(sends) == 1
        url, body = sends[0]
        assert url == "https://hook.invalid/t0ken"
        assert body == {
            "work_item_id": "w1",
            "title": "Ship the thing",
            "type": "gate_requested",
            "gate": "spec_approval",
            "url": "http://127.0.0.1:8765/work-items/w1",
        }

    asyncio.run(scenario())


def test_fires_on_needs_human_with_no_gate(tmp_path):
    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        sends: list = []
        n = _notifier(tmp_path, database, sends)
        await n.start()
        await database.write(
            lambda c: events.append(
                c, "w1", "work_item_needs_human", {"node_id": "build", "reason": "capped"}
            )
        )
        await _drain(database, n)
        await n.stop()
        await database.close()

        assert [b["type"] for _, b in sends] == ["work_item_needs_human"]
        assert sends[0][1]["gate"] is None

    asyncio.run(scenario())


def test_fires_on_nothing_else_across_every_emitted_type(tmp_path):
    """The restraint is the feature: a notifier that fires on progress gets
    muted, and a muted channel is the bug this sub-project exists to fix."""

    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        sends: list = []
        n = _notifier(tmp_path, database, sends)
        await n.start()
        # Every type `events.append` is called with in src/kraft, minus the two
        # that notify. Re-derive with:
        #   grep -rn "events.append(" -A 3 src/kraft | grep -oE '"[a-z_]+",' | sort -u
        # If that grep grows a type this list does not have, this test is stale
        # and the new type is silently un-reviewed.
        others = [
            "work_item_created",
            "work_item_attachments",
            "work_item_retried",
            "chain_loaded",
            "node_started",
            "node_completed",
            "work_item_completed",
            "work_item_resumed",
            "gate_approved",
            "gate_rejected",
            "worker_session_created",
            "worker_session_started",
            "worker_session_exited",
            "worker_session_paused",
            "session_unknown",
            "session_reattached",
            "pause_requested",
            "steer_context_set",
            "fix_cycle_started",
            "findings_measured",
            "notification_failed",
        ]
        for t in others:
            await database.write(lambda c, t=t: events.append(c, "w1", t, {}))
        await _drain(database, n)
        await n.stop()
        await database.close()

        assert sends == []

    asyncio.run(scenario())


def test_two_stops_for_one_transition_coalesce_into_one_send(tmp_path):
    """`POST /gates/{gate}/reject` writes `reject_gate` then, on an exhausted
    reject loop, `mark_needs_human` in the same second, on an item that was
    just notified about. One window, one message."""

    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        sends: list = []
        n = _notifier(tmp_path, database, sends)
        await n.start()
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "plan_approval"})
        )
        await database.write(
            lambda c: events.append(c, "w1", "work_item_needs_human", {"reason": "exhausted"})
        )
        await _drain(database, n)
        await n.stop()
        await database.close()

        assert len(sends) == 1

    asyncio.run(scenario())


def test_a_different_item_in_the_same_window_still_sends(tmp_path):
    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database, "w1")
        await _seed_item(database, "w2", "Other thing")
        sends: list = []
        n = _notifier(tmp_path, database, sends)
        await n.start()
        for wid in ("w1", "w2"):
            await database.write(
                lambda c, wid=wid: events.append(
                    c, wid, "gate_requested", {"gate": "spec_approval"}
                )
            )
        await _drain(database, n)
        await n.stop()
        await database.close()

        assert sorted(b["work_item_id"] for _, b in sends) == ["w1", "w2"]

    asyncio.run(scenario())


def test_startup_does_not_replay_events_older_than_the_process(tmp_path):
    """A notification the human already acted on is worse than one they
    missed: it trains them to ignore the channel."""

    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        sends: list = []
        n = _notifier(tmp_path, database, sends)
        await n.start()
        await _drain(database, n)
        await n.stop()
        await database.close()

        assert sends == []

    asyncio.run(scenario())


def test_start_honours_a_passed_cursor_instead_of_a_fresh_max_seq(tmp_path):
    """`lifespan` snapshots `MAX(seq)` before `reattach` spawns resumed
    executor tasks, and hands that snapshot to `start(cursor=...)` -- so a
    gate a just-resumed work item raises in the gap between that snapshot and
    `start()` actually running is not skipped. If `start()` ignored the
    passed cursor and re-read `MAX(seq)` itself, it would snapshot past that
    event and drop it silently. Seed an event, then start with a cursor from
    *before* it, and confirm it is still picked up.

    Deliberately does NOT call `n.notify()` anywhere -- that would supply the
    exact wakeup `set_on_commit` provides in production, and `set_on_commit`
    is not wired up until *after* `lifespan` calls `notifier.start()`. The
    real failure this test guards is a resumed work item that stops and
    raises a gate with no further commits after it (which is what "needs
    human" means): nothing else will ever call `notify()`. So `start()` must
    wake its own drain for whatever the passed cursor already left behind,
    and this test has to prove that without helping it along."""

    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        seq_before = database.read(
            lambda c: c.execute("SELECT MIN(seq) - 1 FROM events").fetchone()[0]
        )
        sends: list = []
        n = _notifier(tmp_path, database, sends)
        await n.start(cursor=seq_before)
        for _ in range(200):
            await asyncio.sleep(0.01)
            if sends:
                break
        await n.stop()
        await database.close()

        assert len(sends) == 1

    asyncio.run(scenario())


def test_disabled_sends_nothing_and_still_keeps_up(tmp_path):
    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        sends: list = []
        n = _notifier(tmp_path, database, sends)
        config.save_notify(tmp_path / "notify.yaml", {**config.NOTIFY_DEFAULT, "enabled": False})
        n.reload()
        await n.start()
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        await _drain(database, n)
        seq = database.read(lambda c: c.execute("SELECT MAX(seq) AS s FROM events").fetchone()["s"])
        await n.stop()
        await database.close()

        assert sends == []
        # cursor kept up, so enabling later does not burst a day of stale stops
        assert n.cursor == seq

    asyncio.run(scenario())


def test_a_failing_endpoint_retries_once_then_records_the_failure(tmp_path):
    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        sends: list = []
        n = _notifier(tmp_path, database, sends, status=500, retry_delay=0.0)
        await n.start()
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        await _drain(database, n)
        rows = database.read(lambda c: events.read_after(c, 0))
        await n.stop()
        await database.close()

        assert len(sends) == 2  # the send, then exactly one retry
        failed = [r for r in rows if r["type"] == "notification_failed"]
        assert len(failed) == 1
        assert failed[0]["payload"] == {
            "event_type": "gate_requested",
            "status": 500,
            "host": "hook.invalid",
            "error": None,
        }

    asyncio.run(scenario())


def test_the_url_never_reaches_an_event_payload_or_a_log(tmp_path, caplog):
    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        sends: list = []
        n = _notifier(tmp_path, database, sends, status=500, retry_delay=0.0)
        await n.start()
        with caplog.at_level("DEBUG"):
            await database.write(
                lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
            )
            await _drain(database, n)
        rows = database.read(lambda c: events.read_after(c, 0))
        await n.stop()
        await database.close()

        assert "t0ken" not in json.dumps(rows)
        assert "t0ken" not in caplog.text

    asyncio.run(scenario())


def test_a_hanging_endpoint_does_not_block_the_drain_pass(tmp_path):
    """The fan-out is shared with the WebSocket broadcaster and the indexer."""

    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)

        async def hang(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(30)
            return httpx.Response(200)

        cfg = tmp_path / "notify.yaml"
        config.save_notify(
            cfg, {**config.NOTIFY_DEFAULT, "enabled": True, "url": "https://hook.invalid/t0ken"}
        )
        n = notify.Notifier(database, cfg, fallback_base_url="http://127.0.0.1:8765")
        n._transport = httpx.MockTransport(hang)
        await n.start()
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        n.notify()
        seq = database.read(lambda c: c.execute("SELECT MAX(seq) AS s FROM events").fetchone()["s"])
        for _ in range(100):
            await asyncio.sleep(0.01)
            if n.cursor == seq:
                break
        assert n.cursor == seq  # drained past the event while the POST is still hanging
        assert n._inflight  # and the POST really is still in flight
        await n.stop()
        await database.close()

    asyncio.run(scenario())


def test_base_url_from_config_wins_over_the_bind_fallback(tmp_path):
    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        sends: list = []
        n = _notifier(tmp_path, database, sends)
        config.save_notify(
            tmp_path / "notify.yaml",
            {
                **config.NOTIFY_DEFAULT,
                "enabled": True,
                "url": "https://hook.invalid/t0ken",
                "base_url": "https://kraft.tail1234.ts.net/",
            },
        )
        n.reload()
        await n.start()
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        await _drain(database, n)
        await n.stop()
        await database.close()

        assert sends[0][1]["url"] == "https://kraft.tail1234.ts.net/work-items/w1"

    asyncio.run(scenario())


def test_a_malformed_base_url_is_rejected_at_load_and_sends_nothing(tmp_path):
    """An operator typo like `base_url: 8080` (a YAML int) used to survive load
    and fail at send time. The model rejects it at load; the notifier then falls
    back to disabled, which is the same "sends nothing" failure mode."""

    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        sends: list = []
        n = _notifier(tmp_path, database, sends)
        config.save_notify(
            tmp_path / "notify.yaml",
            {
                **config.NOTIFY_DEFAULT,
                "enabled": True,
                "url": "https://hook.invalid/t0ken",
                "base_url": 8080,
            },
        )
        with pytest.raises(config.ConfigError, match="base_url"):
            config.load_notify(tmp_path / "notify.yaml")
        n.reload()
        await n.start()
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        await _drain(database, n)
        await n.stop()
        await database.close()

        assert sends == []

    asyncio.run(scenario())


def test_a_transport_error_records_the_class_name_never_the_message(tmp_path, caplog):
    """The security-critical line: `error = type(exc).__name__`, never
    `str(exc)` -- httpx bakes the full request URL into several of its
    exception messages, so this is the one test that would catch a
    regression to `error = str(exc)`."""

    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)

        async def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom to https://hook.invalid/t0ken")

        cfg = tmp_path / "notify.yaml"
        config.save_notify(
            cfg, {**config.NOTIFY_DEFAULT, "enabled": True, "url": "https://hook.invalid/t0ken"}
        )
        n = notify.Notifier(
            database, cfg, fallback_base_url="http://127.0.0.1:8765", retry_delay=0.0
        )
        n._transport = httpx.MockTransport(boom)
        await n.start()
        with caplog.at_level("DEBUG"):
            await database.write(
                lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
            )
            await _drain(database, n)
        rows = database.read(lambda c: events.read_after(c, 0))
        await n.stop()
        await database.close()

        failed = [r for r in rows if r["type"] == "notification_failed"]
        assert len(failed) == 1
        assert failed[0]["payload"] == {
            "event_type": "gate_requested",
            "status": None,
            "host": "hook.invalid",
            "error": "ConnectError",
        }
        assert "t0ken" not in json.dumps(rows)
        assert "t0ken" not in caplog.text

    asyncio.run(scenario())


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "claude")))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)) as c:
        yield c


def test_get_notify_defaults_before_the_file_exists(api_client):
    body = api_client.get("/api/notify").json()
    assert body == {
        "enabled": False,
        "url_set": False,
        "base_url": None,
        "events": ["gate_requested", "work_item_needs_human"],
        "last_test": None,
    }


def test_get_notify_never_returns_the_url(api_client):
    api_client.put("/api/notify", json={"enabled": True, "url": "https://hook.invalid/t0ken"})
    body = api_client.get("/api/notify").json()
    assert body["url_set"] is True
    assert "t0ken" not in json.dumps(body)
    assert "url" not in body


def test_put_omitting_url_preserves_the_stored_secret(api_client):
    api_client.put("/api/notify", json={"enabled": True, "url": "https://hook.invalid/t0ken"})
    api_client.put("/api/notify", json={"events": ["gate_requested"]})
    templates_dir = Path(api_client.app.state.templates_dir)
    saved = config.load_notify(templates_dir / "notify.yaml")
    assert saved.url == "https://hook.invalid/t0ken"
    assert saved.events == ["gate_requested"]
    assert saved.enabled is True


def test_put_empty_string_clears_the_url(api_client):
    api_client.put("/api/notify", json={"url": "https://hook.invalid/t0ken"})
    api_client.put("/api/notify", json={"url": ""})
    assert api_client.get("/api/notify").json()["url_set"] is False


def test_clearing_the_url_while_enabled_disables_instead_of_422ing(api_client):
    """The only UI path to revoke a leaked token is "Clear URL", which sends
    `{url: ""}` alone. If that 422s because `enabled` is still true, an
    operator can never actually get rid of a live token through the UI --
    clearing the URL must imply turning the feature off, not fail the
    invariant that a config cannot be enabled with nowhere to send."""
    res = api_client.put("/api/notify", json={"url": "https://hook.invalid/t0ken", "enabled": True})
    assert res.status_code == 200

    res = api_client.put("/api/notify", json={"url": ""})
    assert res.status_code == 200
    body = res.json()
    assert body["url_set"] is False
    assert body["enabled"] is False

    templates_dir = Path(api_client.app.state.templates_dir)
    saved = config.load_notify(templates_dir / "notify.yaml")
    assert saved.url is None
    assert saved.enabled is False


def test_put_refuses_a_non_http_url(api_client):
    res = api_client.put("/api/notify", json={"url": "file:///etc/passwd"})
    assert res.status_code == 422
    assert api_client.get("/api/notify").json()["url_set"] is False


def test_put_refuses_a_non_http_base_url(api_client):
    res = api_client.put("/api/notify", json={"base_url": "file:///etc/passwd"})
    assert res.status_code == 422
    assert api_client.get("/api/notify").json()["base_url"] is None


def test_put_refuses_enabling_without_a_url(api_client):
    res = api_client.put("/api/notify", json={"enabled": True})
    assert res.status_code == 422
    # The property this test exists to prove: a rejected PUT must not leave a
    # half-applied config on disk -- `enabled` did not get written even though
    # it was the only field the request set.
    templates_dir = Path(api_client.app.state.templates_dir)
    saved = config.load_notify(templates_dir / "notify.yaml")
    assert saved.enabled is False
    assert saved.url is None


def test_put_reloads_the_running_notifier(api_client):
    api_client.put("/api/notify", json={"enabled": True, "url": "https://hook.invalid/t0ken"})
    assert api_client.app.state.notifier.config["enabled"] is True
    # Task 2's `config` property carries `url_set`, not `url` — its first real
    # caller is this route, so pin the shape here.
    assert "url" not in api_client.app.state.notifier.config
    assert api_client.app.state.notifier.config["url_set"] is True


def test_the_notifier_is_on_the_commit_fan_out(api_client, tmp_path):
    """`_task is not None` alone is set by `start()` regardless of whether the
    fan-out actually wakes it -- deleting `notifier.notify()` from the
    `set_on_commit` lambda in kraft.api.startup would still pass that assertion. Drive a
    real commit through the app instead: on a fresh DB the cursor starts at 0,
    so it can only advance if the commit really reached the notifier."""
    notifier = api_client.app.state.notifier
    assert notifier.cursor == 0
    api_client.post(
        "/api/work-items",
        json={"title": "x", "repo": str(tmp_path), "autostart": False},
    )
    for _ in range(200):
        if notifier.cursor > 0:
            break
        time.sleep(0.01)
    assert notifier.cursor > 0


def test_notifier_stops_before_the_database_closes(tmp_path, monkeypatch):
    """Pins the ordering in `lifespan`'s `finally:` block: `notifier.stop()`
    must run before `database.close()`. A detached send's `_body` does a
    `db.read` for the work item title -- swap the order and shutdown starts
    throwing an intermittent `sqlite3.ProgrammingError` from that read instead
    of draining cleanly. Nothing else in the suite pins this; this test does,
    by recording the order both real calls happen in."""
    order: list[str] = []
    orig_notifier_stop = notify.Notifier.stop
    orig_db_close = kdb.Database.close

    async def tracked_notifier_stop(self):
        order.append("notifier")
        await orig_notifier_stop(self)

    async def tracked_db_close(self):
        order.append("database")
        await orig_db_close(self)

    monkeypatch.setattr(notify.Notifier, "stop", tracked_notifier_stop)
    monkeypatch.setattr(kdb.Database, "close", tracked_db_close)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "claude")))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)):
        pass

    assert order == ["notifier", "database"]


def test_get_notify_with_a_malformed_yaml_file_returns_a_clean_422(api_client):
    """`read_yaml`'s `ConfigError` normally quotes the offending source line --
    for `notify.yaml` that line is the webhook URL. `config.load_notify`
    sanitizes it before the route (and the global `ConfigError` handler) ever
    see it."""
    templates_dir = Path(api_client.app.state.templates_dir)
    (templates_dir / "notify.yaml").write_text('url: "https://hook.invalid/t0ken\n')

    res = api_client.get("/api/notify")

    assert res.status_code == 422
    assert "t0ken" not in res.text
    assert res.json() == {"detail": "notify.yaml: not valid YAML"}


def test_a_malformed_notify_yaml_does_not_crash_startup(tmp_path, monkeypatch, caplog):
    """`Notifier.__init__` calls `load_notify` before `lifespan`'s `try:` --
    letting a malformed file raise there would abort startup over an entirely
    optional config, and leak the broadcaster, indexer, `index_conn` and
    database un-stopped on the way out."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    templates_dir = fake_templates_dir(tmp_path, "claude")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    (templates_dir / "notify.yaml").write_text('url: "https://hook.invalid/t0ken\n')
    import kraft.api as api

    with caplog.at_level("WARNING"):
        with TestClient(api.app, client=("127.0.0.1", 54321)) as c:  # must not raise
            assert c.app.state.notifier.config["enabled"] is False

    assert "t0ken" not in caplog.text


def test_a_malformed_notify_yaml_falls_back_to_disabled_and_sends_nothing(tmp_path):
    """The other half of the fail-safe: not just "does not crash", but "sends
    nothing" -- a config Kraft cannot parse must not guess at being enabled."""

    async def scenario():
        database = await _database(tmp_path)
        await _seed_item(database)
        cfg = tmp_path / "notify.yaml"
        cfg.write_text('url: "https://hook.invalid/t0ken\n')
        sends: list = []

        async def transport(request: httpx.Request) -> httpx.Response:
            sends.append((str(request.url), json.loads(request.content)))
            return httpx.Response(200)

        n = notify.Notifier(database, cfg, fallback_base_url="http://127.0.0.1:8765")
        n._transport = httpx.MockTransport(transport)
        assert n.config["enabled"] is False
        await n.start()
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        await _drain(database, n)
        await n.stop()
        await database.close()

        assert sends == []

    asyncio.run(scenario())


def test_get_notify_with_invalid_utf8_returns_a_clean_422_and_disables(
    tmp_path, monkeypatch, caplog
):
    """`read_text()`'s `UnicodeDecodeError` is a `ValueError`, not an
    `OSError` -- `read_yaml` must catch it too, or a `notify.yaml` in the
    wrong encoding crashes `Notifier.__init__` exactly like a YAML syntax
    error used to. The token sits on an otherwise-valid line; the file still
    fails to decode as a whole, so it must never surface."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    templates_dir = fake_templates_dir(tmp_path, "claude")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    (templates_dir / "notify.yaml").write_bytes(
        b'url: "https://hook.invalid/t0ken"\nbad: "\xff\xfe garbage"\n'
    )
    import kraft.api as api

    with caplog.at_level("WARNING"):
        with TestClient(api.app, client=("127.0.0.1", 54321)) as c:  # must not raise
            assert c.app.state.notifier.config["enabled"] is False
            res = c.get("/api/notify")

    assert res.status_code == 422
    assert "t0ken" not in res.text
    assert "t0ken" not in caplog.text


def test_get_notify_with_a_permission_denied_file_reports_that_not_bad_yaml(api_client):
    """`read_yaml` folds a genuine `OSError` into the same `ConfigError` as a
    YAML syntax error; `load_notify` must not blanket both into "not valid
    YAML" -- an operator who cannot read their own file needs to be told
    that, not sent hunting for a typo that is not there."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    templates_dir = Path(api_client.app.state.templates_dir)
    path = templates_dir / "notify.yaml"
    path.write_text("enabled: true\n")
    os.chmod(path, 0o000)
    try:
        res = api_client.get("/api/notify")
    finally:
        os.chmod(path, 0o600)

    assert res.status_code == 422
    detail = res.json()["detail"]
    assert "cannot be read" in detail
    assert "not valid YAML" not in detail


def test_put_notify_with_a_malformed_yaml_file_returns_a_clean_422(api_client):
    """`put_notify` also calls `config_mod.load_notify` -- before it ever
    touches the request body -- so the same sanitizing path `GET /notify`
    uses must cover it too."""
    templates_dir = Path(api_client.app.state.templates_dir)
    (templates_dir / "notify.yaml").write_text('url: "https://hook.invalid/t0ken\n')

    res = api_client.put("/api/notify", json={"enabled": True})

    assert res.status_code == 422
    assert "t0ken" not in res.text


def test_send_test_records_status_and_latency(tmp_path):
    async def scenario():
        database = await _database(tmp_path)
        n = _notifier(tmp_path, database, [])
        result = await n.send_test()
        assert result["status"] == 200
        assert result["ms"] >= 0
        assert result["error"] is None
        assert n.last_test == result
        await database.close()

    asyncio.run(scenario())


def test_send_test_without_a_url_raises(tmp_path):
    async def scenario():
        database = await _database(tmp_path)
        n = _notifier(tmp_path, database, [])
        n._config["url"] = None
        with pytest.raises(ValueError):
            await n.send_test()
        await database.close()

    asyncio.run(scenario())


def test_notify_test_endpoint_needs_a_url(api_client):
    resp = api_client.post("/api/notify/test")
    assert resp.status_code == 422


def test_get_notify_carries_last_test_after_a_send(api_client, monkeypatch):
    api_client.put("/api/notify", json={"url": "https://ntfy.sh/x", "enabled": True})

    async def fake_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    api_client.app.state.notifier._transport = httpx.MockTransport(fake_transport)
    resp = api_client.post("/api/notify/test")
    assert resp.status_code == 200
    assert api_client.get("/api/notify").json()["last_test"] is not None
