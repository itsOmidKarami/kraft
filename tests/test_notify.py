"""Outbound notifications (sub-project B). The URL is the first real secret
Kraft stores, so half of these tests are about where it must *not* appear."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import stat
import time
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from support.harness import fake_templates_dir, isolated_bd

from kraft import config, events, notify
from kraft import db as kdb
from kraft.vocab import BY_VALUE

DEFAULT_EVENTS = ["gate_requested", "work_item_needs_human"]
#: A `notify.yaml` that does not parse. The token sits on the broken line, the
#: one a parser error would quote back.
MALFORMED = b'url: "https://hook.invalid/t0ken\n'
#: Valid YAML, but not UTF-8: `read_text()` raises `UnicodeDecodeError`, a
#: `ValueError` rather than an `OSError`. The token sits on an otherwise-valid
#: line; the file fails to decode as a whole, so it must never surface.
INVALID_UTF8 = b'url: "https://hook.invalid/t0ken"\nbad: "\xff\xfe garbage"\n'


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, {"enabled": False, "url": None, "base_url": None, "events": DEFAULT_EVENTS}),
        (
            "enabled: true\n",
            {"enabled": True, "url": None, "base_url": None, "events": DEFAULT_EVENTS},
        ),
    ],
    ids=["no-file", "partial-file"],
)
def test_notify_yaml_loads_with_every_missing_key_defaulted(tmp_path, text, expected):
    """A missing file reads as the shipped default; a partial one keeps what it
    says and fills in the rest."""
    path = tmp_path / "notify.yaml"
    if text is not None:
        path.write_text(text)
    assert config.Notify.load(path).model_dump() == expected


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (MALFORMED.decode(), "^notify.yaml: not valid YAML$"),
        ("url: https://hook.invalid/t0ken\nbase_url: 8080\n", "^notify.yaml: base_url: "),
        (
            "url: https://hook.invalid/t0ken\nevents: [gate_requsted]\n",
            "^notify.yaml: events: unknown event type 'gate_requsted'; see the Events reference "
            "for the names$",
        ),
        (
            "events: [" + "https://hook.invalid/" + "a" * 20 + "MARKER" + "]\n",
            r"^notify.yaml: events: unknown event type 'https://hook.invalid/a{19}\.\.\.'; see",
        ),
        (
            "events: [" + ", ".join(f"bad{i}" for i in range(7)) + "]\n",
            r"unknown event types 'bad0', 'bad1', 'bad2', 'bad3', 'bad4' and 2 more; see",
        ),
    ],
    ids=[
        "malformed-yaml",
        "malformed-base-url",
        "unknown-event",
        "long-unknown-event-is-cut",
        "many-unknown-events-are-counted",
    ],
)
def test_notify_load_refuses_a_file_it_cannot_use(tmp_path, text, error):
    """A file that does not parse, or that parses into a value the model
    refuses (`base_url: 8080`, a YAML int), fails at load, not at send time."""
    path = tmp_path / "notify.yaml"
    path.write_text(text)
    with pytest.raises(config.ConfigError, match=error) as refused:
        config.Notify.load(path)
    assert "t0ken" not in str(refused.value)
    assert "MARKER" not in str(refused.value)


def test_notify_events_accept_every_known_event_type_and_none():
    """The vocabulary is the rule: every name `kraft.vocab` defines is accepted
    and kept as the plain string it was written as; an empty list is valid."""
    assert config.Notify(events=list(BY_VALUE)).events == list(BY_VALUE)
    assert config.Notify(events=[]).events == []


def test_saved_notify_yaml_is_0600_because_it_holds_a_token(tmp_path):
    path = tmp_path / "notify.yaml"
    config.Notify(url="https://ntfy.sh/secret-topic").save(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_saved_notify_yaml_holds_only_the_known_keys(tmp_path):
    """A stray key cannot reach the file: the model refuses it before `save`."""
    with pytest.raises(ValidationError, match="nonsense"):
        config.Notify.model_validate({"nonsense": 1})
    path = tmp_path / "notify.yaml"
    config.Notify().save(path)
    assert set(yaml.safe_load(path.read_text())) == set(config.Notify.model_fields)


async def _seed_item(database, wid="w1", title="Ship the thing"):
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
            "status, created_at, updated_at) VALUES (?, ?, '/r', 'quick-task', '{}', "
            "'active', 'now', 'now')",
            (wid, title),
        )
    )


def _notifier(tmp_path, database, sends, *, answer=200, base_url=None, **kw):
    """A Notifier whose transport is a recording stub. `sends` collects
    (url, body) tuples; `answer` is what the endpoint does: a status code to
    answer with, or an exception to raise."""
    cfg = tmp_path / "notify.yaml"
    config.Notify(enabled=True, url="https://hook.invalid/t0ken", base_url=base_url).save(cfg)

    async def transport(request: httpx.Request) -> httpx.Response:
        sends.append((str(request.url), json.loads(request.content)))
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer)

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


@pytest.mark.parametrize(
    ("event_type", "payload", "base_url", "expected"),
    [
        (
            "gate_requested",
            {"gate": "spec_approval"},
            None,
            {
                "type": "gate_requested",
                "gate": "spec_approval",
                "url": "http://127.0.0.1:8765/work-items/w1",
            },
        ),
        (
            "work_item_needs_human",
            {"node_id": "build", "reason": "capped"},
            None,
            {
                "type": "work_item_needs_human",
                "gate": None,
                "url": "http://127.0.0.1:8765/work-items/w1",
            },
        ),
        (
            "gate_requested",
            {"gate": "spec_approval"},
            "https://kraft.tail1234.ts.net/",
            {
                "type": "gate_requested",
                "gate": "spec_approval",
                "url": "https://kraft.tail1234.ts.net/work-items/w1",
            },
        ),
    ],
    ids=["gate-requested", "needs-human-has-no-gate", "base-url-beats-the-bind-fallback"],
)
async def test_a_stop_sends_one_message_that_links_to_the_item(
    tmp_path, database, event_type, payload, base_url, expected
):
    """The two stop types each send once, to the configured URL, with a link a
    phone can open: `base_url` from the config when there is one, else the
    address Kraft is bound to."""
    await _seed_item(database)
    sends: list = []
    n = _notifier(tmp_path, database, sends, base_url=base_url)
    await n.start()
    await database.write(lambda c: events.append(c, "w1", event_type, payload))
    await _drain(database, n)
    await n.stop()

    assert sends == [
        (
            "https://hook.invalid/t0ken",
            {"work_item_id": "w1", "title": "Ship the thing", **expected},
        )
    ]


async def test_fires_on_nothing_else_across_every_emitted_type(tmp_path, database):
    """The restraint is the feature: a notifier that fires on progress gets
    muted, and a muted channel is the bug this sub-project exists to fix."""

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

    assert sends == []


@pytest.mark.parametrize(
    ("stops", "sent_to"),
    [
        (
            [
                ("w1", "gate_requested", {"gate": "plan_approval"}),
                ("w1", "work_item_needs_human", {"reason": "exhausted"}),
            ],
            ["w1"],
        ),
        (
            [
                ("w1", "gate_requested", {"gate": "spec_approval"}),
                ("w2", "gate_requested", {"gate": "spec_approval"}),
            ],
            ["w1", "w2"],
        ),
    ],
    ids=["one-item-two-stops", "two-items"],
)
async def test_stops_in_one_window_coalesce_per_item(tmp_path, database, stops, sent_to):
    """`POST /gates/{gate}/reject` writes `reject_gate` then, on an exhausted
    reject loop, `mark_needs_human` in the same second, on an item that was
    just notified about. One window, one message -- per item: a different item
    stopping in the same window is news of its own."""

    await _seed_item(database, "w1")
    await _seed_item(database, "w2", "Other thing")
    sends: list = []
    n = _notifier(tmp_path, database, sends)
    await n.start()
    for wid, event_type, payload in stops:
        await database.write(
            lambda c, wid=wid, t=event_type, p=payload: events.append(c, wid, t, p)
        )
    await _drain(database, n)
    await n.stop()

    assert sorted(b["work_item_id"] for _, b in sends) == sent_to


async def test_startup_does_not_replay_events_older_than_the_process(tmp_path, database):
    """A notification the human already acted on is worse than one they
    missed: it trains them to ignore the channel."""

    await _seed_item(database)
    await database.write(
        lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
    )
    sends: list = []
    n = _notifier(tmp_path, database, sends)
    await n.start()
    await _drain(database, n)
    await n.stop()

    assert sends == []


async def test_start_honours_a_passed_cursor_instead_of_a_fresh_max_seq(tmp_path, database):
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

    await _seed_item(database)
    await database.write(
        lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
    )
    seq_before = database.read(lambda c: c.execute("SELECT MIN(seq) - 1 FROM events").fetchone()[0])
    sends: list = []
    n = _notifier(tmp_path, database, sends)
    await n.start(cursor=seq_before)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if sends:
            break
    await n.stop()

    assert len(sends) == 1


@pytest.mark.parametrize(
    "text",
    [
        "enabled: false\nurl: https://hook.invalid/t0ken\n",
        "enabled: true\n",
        MALFORMED.decode(),
        "enabled: true\nurl: https://hook.invalid/t0ken\nbase_url: 8080\n",
    ],
    ids=["disabled", "enabled-without-a-url", "malformed-yaml", "malformed-base-url"],
)
async def test_a_notify_yaml_that_cannot_send_sends_nothing_and_keeps_up(tmp_path, database, text):
    """Off, armed with nowhere to send, or a file `Notify.load` refuses: the
    notifier sends nothing and records no failure, and its cursor still keeps
    up, so enabling later does not burst a day of stale stops. A refused file
    reads as the defaults, not as the config the notifier had before it."""

    await _seed_item(database)
    sends: list = []
    n = _notifier(tmp_path, database, sends, retry_delay=0.0)
    # Written as an operator would, by hand: the model refuses to save these.
    path = tmp_path / "notify.yaml"
    path.write_text(text)
    n.reload()
    await n.start()
    await database.write(
        lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
    )
    await _drain(database, n)
    rows = database.read(lambda c: events.read_after(c, 0))
    await n.stop()

    assert sends == []
    assert [r["type"] for r in rows] == ["gate_requested"]  # no notification_failed either
    assert n.cursor == rows[-1]["seq"]


@pytest.mark.parametrize(
    ("answer", "status", "error"),
    [
        (500, 500, None),
        (httpx.ConnectError("boom to https://hook.invalid/t0ken"), None, "ConnectError"),
    ],
    ids=["http-500", "connect-error"],
)
async def test_a_failed_send_retries_once_and_records_no_url(
    tmp_path, caplog, database, answer, status, error
):
    """One retry, then a `notification_failed` event with the status and the
    host -- never the URL, in the event or in a log line. The security-critical
    line is `error = type(exc).__name__`, never `str(exc)`: httpx bakes the full
    request URL into several of its exception messages, so the connect-error
    row is the one that would catch a regression to `error = str(exc)`."""

    await _seed_item(database)
    sends: list = []
    n = _notifier(tmp_path, database, sends, answer=answer, retry_delay=0.0)
    await n.start()
    with caplog.at_level("DEBUG"):
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        await _drain(database, n)
    rows = database.read(lambda c: events.read_after(c, 0))
    await n.stop()

    assert len(sends) == 2  # the send, then exactly one retry
    failed = [r for r in rows if r["type"] == "notification_failed"]
    assert len(failed) == 1
    assert failed[0]["payload"] == {
        "event_type": "gate_requested",
        "status": status,
        "host": "hook.invalid",
        "error": error,
    }
    assert "notification to hook.invalid failed" in caplog.text
    assert "t0ken" not in json.dumps(rows)
    assert "t0ken" not in caplog.text


async def test_a_hanging_endpoint_does_not_block_the_drain_pass(tmp_path, database):
    """The fan-out is shared with the WebSocket broadcaster and the indexer."""
    await _seed_item(database)

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    cfg = tmp_path / "notify.yaml"
    config.Notify(enabled=True, url="https://hook.invalid/t0ken").save(cfg)
    n = notify.Notifier(database, cfg, fallback_base_url="http://127.0.0.1:8765")
    n._transport = httpx.MockTransport(hang)
    await n.start()
    try:
        await database.write(
            lambda c: events.append(c, "w1", "gate_requested", {"gate": "spec_approval"})
        )
        n.notify()
        seq = database.read(lambda c: c.execute("SELECT MAX(seq) AS s FROM events").fetchone()["s"])
        # Well inside the endpoint's 30s hang, so a drain that waited on the
        # POST could not get here in time.
        deadline = asyncio.get_running_loop().time() + 5
        while n.cursor != seq:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(
                    f"the drain pass never got past the hanging POST (cursor={n.cursor}, "
                    f"event seq={seq})"
                )
            await asyncio.sleep(0.01)
        assert n._inflight  # and the POST really is still in flight
    finally:
        await n.stop()


def test_get_notify_defaults_before_the_file_exists(client):
    body = client.get("/api/notify").json()
    assert body == {
        "enabled": False,
        "url_set": False,
        "base_url": None,
        "events": ["gate_requested", "work_item_needs_human"],
        "last_test": None,
    }


def test_get_notify_never_returns_the_url(client):
    client.put("/api/notify", json={"enabled": True, "url": "https://hook.invalid/t0ken"})
    body = client.get("/api/notify").json()
    assert body["url_set"] is True
    assert "t0ken" not in json.dumps(body)
    assert "url" not in body


@pytest.mark.parametrize(
    ("first", "second", "saved"),
    [
        (
            {"enabled": True, "url": "https://hook.invalid/t0ken"},
            {"events": ["gate_requested"]},
            {
                "enabled": True,
                "url": "https://hook.invalid/t0ken",
                "base_url": None,
                "events": ["gate_requested"],
            },
        ),
        (
            {"url": "https://hook.invalid/t0ken"},
            {"url": ""},
            {"enabled": False, "url": None, "base_url": None, "events": DEFAULT_EVENTS},
        ),
        (
            {"enabled": True, "url": "https://hook.invalid/t0ken"},
            {"url": ""},
            {"enabled": False, "url": None, "base_url": None, "events": DEFAULT_EVENTS},
        ),
    ],
    ids=["omitted-url-is-kept", "empty-url-clears", "empty-url-clears-and-disables"],
)
def test_put_merges_its_body_over_the_stored_file(client, first, second, saved):
    """A field the body omits keeps its stored value: editing the event list
    must not require re-entering a token the UI is never allowed to show back.
    `url: ""` is the explicit clear, and the only UI path to revoke a leaked
    token ("Clear URL" sends `{url: ""}` alone). If that 422'd because
    `enabled` is still true, an operator could never get rid of a live token
    through the UI -- clearing the URL implies turning the feature off, not
    failing the invariant that a config cannot be enabled with nowhere to
    send."""
    assert client.put("/api/notify", json=first).status_code == 200

    res = client.put("/api/notify", json=second)

    assert res.status_code == 200
    assert res.json() == {
        "enabled": saved["enabled"],
        "url_set": saved["url"] is not None,
        "base_url": saved["base_url"],
        "events": saved["events"],
        "last_test": None,
    }
    templates_dir = Path(client.app.state.templates_dir)
    assert config.Notify.load(templates_dir / "notify.yaml").model_dump() == saved


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"url": "file:///etc/passwd"}, "url must be an http or https URL"),
        ({"base_url": "file:///etc/passwd"}, "base_url must be an http or https URL"),
        ({"url": "http://[x"}, "url must be an http or https URL"),
        ({"enabled": True}, "set a webhook URL before enabling notifications"),
        (
            {"events": ["gate_requsted"]},
            "notify.yaml: events: unknown event type 'gate_requsted'; see the Events reference "
            "for the names",
        ),
    ],
    ids=[
        "non-http-url",
        "non-http-base-url",
        "unparseable-url",
        "enabling-without-a-url",
        "unknown-event",
    ],
)
def test_put_refuses_a_bad_setting_and_writes_nothing(client, body, detail):
    """A rejected PUT must not leave a half-applied config on disk -- for
    `enabling-without-a-url`, `enabled` must not get written even though it was
    the only field the request set."""
    res = client.put("/api/notify", json=body)

    assert res.status_code == 422
    assert res.json() == {"detail": detail}
    templates_dir = Path(client.app.state.templates_dir)
    assert config.Notify.load(templates_dir / "notify.yaml") == config.Notify()


def test_put_reloads_the_running_notifier(client):
    client.put("/api/notify", json={"enabled": True, "url": "https://hook.invalid/t0ken"})
    assert client.app.state.notifier.config["enabled"] is True
    # Task 2's `config` property carries `url_set`, not `url` — its first real
    # caller is this route, so pin the shape here.
    assert "url" not in client.app.state.notifier.config
    assert client.app.state.notifier.config["url_set"] is True


def test_the_notifier_is_on_the_commit_fan_out(client, tmp_path):
    """`_task is not None` alone is set by `start()` regardless of whether the
    fan-out actually wakes it -- deleting `notifier.notify()` from the
    `set_on_commit` lambda in kraft.api.startup would still pass that assertion. Drive a
    real commit through the app instead: on a fresh DB the cursor starts at 0,
    so it can only advance if the commit really reached the notifier."""
    notifier = client.app.state.notifier
    assert notifier.cursor == 0
    client.post(
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
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(fake_templates_dir(tmp_path, "claude")))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)):
        pass

    assert order == ["notifier", "database"]


@pytest.mark.parametrize(
    ("content", "warning"),
    [
        (MALFORMED, "not valid YAML"),
        (INVALID_UTF8, "not valid YAML"),
        (
            b"enabled: true\nurl: https://hook.invalid/t0ken\nevents: [gate_requsted]\n",
            "events: unknown event type 'gate_requsted'",
        ),
    ],
    ids=["malformed", "invalid-utf8", "unknown-event"],
)
def test_a_bad_notify_yaml_at_startup_disables_instead_of_crashing(
    request, templates_dir, caplog, content, warning
):
    """`Notifier.__init__` calls `Notify.load` before `lifespan`'s `try:` --
    letting a broken file raise there would abort startup over an entirely
    optional config, and leak the broadcaster, indexer, `index_conn` and
    database un-stopped on the way out. `read_text()`'s `UnicodeDecodeError`
    is a `ValueError`, not an `OSError`, so `read_yaml` must catch it too, or
    a file in the wrong encoding crashes startup exactly like a YAML syntax
    error used to. The warning is the record, and it must not carry the
    token."""
    (templates_dir / "notify.yaml").write_bytes(content)
    caplog.set_level("WARNING")

    client = request.getfixturevalue("client")  # starts the app on the file above

    assert client.app.state.notifier.config["enabled"] is False
    assert f"notify.yaml: {warning}" in caplog.text
    assert "-- notifications disabled" in caplog.text
    assert "t0ken" not in caplog.text


_GET = {"method": "GET"}
_PUT = {"method": "PUT", "json": {"enabled": True}}


@pytest.mark.parametrize(
    ("content", "call", "detail"),
    [
        (MALFORMED, _GET, "notify.yaml: not valid YAML"),
        (INVALID_UTF8, _GET, "notify.yaml: not valid YAML"),
        (
            b"url: [https://hook.invalid/t0ken]\n",
            _GET,
            "notify.yaml: url: Input should be a valid string",
        ),
        (MALFORMED, _PUT, "notify.yaml: not valid YAML"),
        (
            b"url: https://hook.invalid/t0ken\nevents: [gate_requsted]\n",
            _GET,
            "notify.yaml: events: unknown event type 'gate_requsted'; see the Events reference "
            "for the names",
        ),
    ],
    ids=["malformed", "utf8", "invalid-value", "malformed-put", "unknown-event"],
)
def test_a_bad_notify_file_is_reported_cleanly(client, caplog, content, call, detail):
    """`read_yaml`'s `ConfigError` normally quotes the offending source line,
    and pydantic's error echoes the offending value -- for `notify.yaml` that
    line or value is the webhook URL. `config.Notify.load` sanitizes both
    before the route (and the global `ConfigError` handler) ever see them, for
    `GET` and for `PUT`, which loads the file before it touches the request
    body, and leaves the file as it was."""
    path = Path(client.app.state.templates_dir) / "notify.yaml"
    path.write_bytes(content)

    res = client.request(url="/api/notify", **call)

    assert res.status_code == 422
    assert res.json() == {"detail": detail}
    assert "t0ken" not in res.text
    assert "t0ken" not in caplog.text
    assert path.read_bytes() == content


def test_an_unreadable_notify_file_is_reported_as_unreadable(client, monkeypatch):
    """Not blanketed into "not valid YAML": an operator who cannot read their
    own file needs to be told that, not sent hunting for a typo that is not
    there. The read fails the way it does for a non-root reader of a 0o000
    file (root ignores the mode bits, so the test raises it itself)."""
    path = Path(client.app.state.templates_dir) / "notify.yaml"
    path.write_text("url: https://hook.invalid/t0ken\n")
    read_text = Path.read_text

    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(self))
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    res = client.get("/api/notify")

    assert res.status_code == 422
    assert res.json() == {"detail": "notify.yaml: cannot be read: Permission denied"}


@pytest.mark.parametrize(
    ("answer", "status", "error"),
    [
        (200, 200, None),
        (httpx.ConnectError("boom to https://hook.invalid/t0ken"), None, "ConnectError"),
    ],
    ids=["delivered", "connect-error"],
)
async def test_send_test_records_its_outcome(tmp_path, database, answer, status, error):
    """`send_test` is one synchronous attempt; what came back is kept as
    `last_test`. A transport error records its class name, never its message,
    which carries the URL."""
    sends: list = []
    n = _notifier(tmp_path, database, sends, answer=answer)

    result = await n.send_test()

    assert {k: result[k] for k in ("status", "error")} == {"status": status, "error": error}
    assert result["ms"] >= 0
    assert n.last_test == result
    assert [body["type"] for _, body in sends] == ["test"]


def test_notify_test_endpoint_needs_a_url(client):
    """`send_test` raises `ValueError` with no URL set; the route turns that
    into a 422 rather than sending to nowhere."""
    resp = client.post("/api/notify/test")
    assert resp.status_code == 422
    assert resp.json() == {"detail": "no webhook URL is set"}


def test_get_notify_carries_last_test_after_a_send(client, monkeypatch):
    client.put("/api/notify", json={"url": "https://ntfy.sh/x", "enabled": True})

    async def fake_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client.app.state.notifier._transport = httpx.MockTransport(fake_transport)
    resp = client.post("/api/notify/test")
    assert resp.status_code == 200
    assert client.get("/api/notify").json()["last_test"] is not None
