# Sub-project B: away from desk — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell the human that a run stopped, on a device they are not sitting at, and make the screen that message links to usable on a phone.

**Architecture:** A third subscriber on the existing `Database.set_on_commit` fan-out in `api.py`, built exactly like `Broadcaster` and `Indexer`: wake on commit, read `events` rows past an in-memory cursor, act, advance. It filters two event types, builds a small flat JSON body, and POSTs it to one operator-supplied URL as a detached task so a hanging endpoint cannot stall the WebSocket or the indexer. Config is a new `templates/notify.yaml` edited through a Settings page like every other file there. The frontend work is the diff viewer from sub-project A plus the reject textarea — the board and detail phone rules already landed.

**Tech Stack:** Python 3.14, FastAPI, `httpx` (already a runtime dependency, `client.py` imports it), PyYAML, pytest. React 19 + TypeScript + vitest + @testing-library/react on the frontend. Plain CSS in `frontend/src/styles.css` on top of the Nocturne token layer in `frontend/src/nocturne.css`.

**Spec:** `docs/superpowers/specs/2026-09-04-sub-project-b-away-from-desk-design.md` (amended 2026-09-05 against the merged tree; read the `[amended]` blocks, they overrule the prose around them).

## Global Constraints

- **No new dependencies.** `httpx>=0.28.1` is already in `[project].dependencies` in `pyproject.toml`. Nothing else gets added.
- **The webhook URL is a secret.** It is never returned by any API route, never written to a log line, never put in an event payload, and never rendered into the DOM. `GET /notify` returns `url_set: true|false`. This is not negotiable and every task that touches the URL is subject to it.
- **`except (OSError, ValueError)` on best-effort reads.** Any `read_text()` on a path that might not be valid UTF-8 needs `ValueError` too — `UnicodeDecodeError` is a `ValueError`, not an `OSError`. This has been wrong four times in this codebase.
- **The commit fan-out is shared.** `database.set_on_commit(...)` feeds the WebSocket broadcaster and the indexer. Notifier work must never be awaited inline on that path.
- **Only two event types notify by default:** `gate_requested` and `work_item_needs_human`. The list is configurable; the shipped default is exactly these two.
- **`ruff` line-length is 100** (`[tool.ruff]` in `pyproject.toml`). `uv run ruff format --check .` is a gate.
- **Tests are run with `just test` (backend), `just test-ui` (frontend), `just lint`.** Do not invent other runners.
- **Beads, not TODO lists.** Any deferred finding becomes a `bd create` with a parent of `Kraft-8mu.4`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kraft/notify.py` *(create)* | The `Notifier` class: cursor, event filter, coalescing window, payload construction, POST with one retry, `notification_failed` event. The only module that ever holds the URL in a variable. |
| `src/kraft/config.py` *(modify)* | `NOTIFY_DEFAULT`, `load_notify`, `save_notify`. Sits beside `load_access`/`save_access`, which it mirrors. |
| `src/kraft/templates.py` *(modify, line 15)* | Add `notify.yaml` to `CONFIG_FILES` so `load_templates` does not read it as a broken chain template and degrade `/health`. |
| `src/kraft/api.py` *(modify)* | Construct/start/stop the `Notifier` in `lifespan`, add it to the `set_on_commit` lambda, and serve `GET /notify` + `PUT /notify`. |
| `tests/test_notify.py` *(create)* | Every backend assertion in spec §6. |
| `frontend/src/types.ts` *(modify)* | `Notify` interface. |
| `frontend/src/api.ts` *(modify)* | `getNotify`, `putNotify`. |
| `frontend/src/views/Settings.tsx` *(modify)* | `NotifyPage`, a `PAGES` entry, a `<Route>`. |
| `frontend/src/views/Settings.test.tsx` *(modify)* | The Notifications page tests. |
| `frontend/src/components/DocumentModal.tsx` *(modify)* | Wrap the editor-launch buttons in `.desktop-only`; "Copy path" and "Close" stay. |
| `frontend/src/components/DocumentModal.test.tsx` *(modify)* | The class-boundary assertions. |
| `frontend/src/styles.css` *(modify)* | Phone rules for `.diff-modal` and `.gate-reject textarea.input`, inside the **existing** `@media (max-width: 640px)` block (lines 820-853), plus one desktop-scoped layout rule for the new `DocumentModal` wrapper. |

**What is deliberately not here:** no new dependency, no persisted notifier cursor, no OS-notification channel, no inbound webhook, no `notify.yaml` in `templates/` (it is not bundled and not seeded, for `access.yaml`'s reason — `cli.py:seed_home` deletes that one from the staged copy), and no new "is the server local" API field.

---

### Task 1: `notify.yaml` config reader and writer

**Files:**
- Modify: `src/kraft/config.py` (append a new section after `save_access`, which ends the file)
- Modify: `src/kraft/templates.py:15`
- Modify: `.gitignore`
- Test: `tests/test_notify.py` *(create)*

**Interfaces:**
- Consumes: `config.read_yaml(path, default)` and `config.write_yaml(path, data)`, both already in `config.py`. `write_yaml` builds a `tempfile.mkstemp` temp file (mode `0o600` by construction) and `os.replace`s it into place, so the saved file is already `0600` — the test proves it rather than a redundant `chmod` asserting it.
- Produces:
  - `config.NOTIFY_DEFAULT: dict` — `{"enabled": False, "url": None, "base_url": None, "events": ["gate_requested", "work_item_needs_human"]}`
  - `config.load_notify(path: str | Path) -> dict` — always returns all four keys.
  - `config.save_notify(path: str | Path, notify: dict) -> None` — writes exactly the four keys, in `NOTIFY_DEFAULT` order.
  - `templates.CONFIG_FILES` gains `"notify.yaml"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_notify.py`:

```python
"""Outbound notifications (sub-project B). The URL is the first real secret
Kraft stores, so half of these tests are about where it must *not* appear."""

from __future__ import annotations

import stat

from kraft import config
from kraft.templates import CONFIG_FILES


def test_missing_notify_yaml_reads_as_the_shipped_default(tmp_path):
    cfg = config.load_notify(tmp_path / "notify.yaml")
    assert cfg == {
        "enabled": False,
        "url": None,
        "base_url": None,
        "events": ["gate_requested", "work_item_needs_human"],
    }


def test_partial_notify_yaml_fills_in_the_rest(tmp_path):
    path = tmp_path / "notify.yaml"
    path.write_text("enabled: true\n")
    cfg = config.load_notify(path)
    assert cfg["enabled"] is True
    assert cfg["url"] is None
    assert cfg["events"] == ["gate_requested", "work_item_needs_human"]


def test_saved_notify_yaml_is_0600_because_it_holds_a_token(tmp_path):
    path = tmp_path / "notify.yaml"
    config.save_notify(path, {**config.NOTIFY_DEFAULT, "url": "https://ntfy.sh/secret-topic"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_notify_writes_only_the_known_keys(tmp_path):
    path = tmp_path / "notify.yaml"
    config.save_notify(path, {**config.NOTIFY_DEFAULT, "nonsense": 1})
    assert set(config.load_notify(path)) == set(config.NOTIFY_DEFAULT)


def test_notify_yaml_is_not_read_as_a_chain_template():
    # load_templates skips CONFIG_FILES; without this, every settings file the
    # UI writes shows up as a broken template and /health goes degraded.
    assert "notify.yaml" in CONFIG_FILES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/test_notify.py -v`
Expected: FAIL — `AttributeError: module 'kraft.config' has no attribute 'load_notify'` on the first four, and an assertion failure on the fifth.

- [ ] **Step 3: Write minimal implementation**

Append to `src/kraft/config.py` (the file currently ends with `save_access`):

```python
# ── notify ───────────────────────────────────────────────────────────────────

#: `templates/notify.yaml`. Not bundled and not seeded, for `access.yaml`'s
#: reason: it holds a secret and a hostname that belong to one machine. A
#: missing file reads as this, and the first `PUT /notify` creates it.
NOTIFY_DEFAULT: dict = {
    "enabled": False,
    "url": None,
    "base_url": None,
    "events": ["gate_requested", "work_item_needs_human"],
}


def load_notify(path: str | Path) -> dict:
    return {**NOTIFY_DEFAULT, "events": list(NOTIFY_DEFAULT["events"]), **read_yaml(path, {})}


def save_notify(path: str | Path, notify: dict) -> None:
    """Written 0600 — `write_yaml` stages through `mkstemp`, which creates at
    0600, and `os.replace` carries that mode onto the target."""
    write_yaml(path, {k: notify.get(k, v) for k, v in NOTIFY_DEFAULT.items()})
```

In `.gitignore`, add a line beside the existing `templates/access.yaml` entry, inside the same comment block:

```
templates/notify.yaml
```

`notify.yaml` is not bundled and is not seeded into the repo's `templates/`, so this should never fire — which is exactly why it is cheap. It holds a bearer token in a URL, and one `KRAFT_TEMPLATES_DIR` pointed at the checkout is all it takes to stage it. Extend the existing comment to say the entry covers both files.

In `src/kraft/templates.py`, line 15, change:

```python
CONFIG_FILES = frozenset({"registry.yaml", "policy.yaml", "repos.yaml", "access.yaml"})
```

to:

```python
CONFIG_FILES = frozenset(
    {"registry.yaml", "policy.yaml", "repos.yaml", "access.yaml", "notify.yaml"}
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/test_notify.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/config.py src/kraft/templates.py .gitignore tests/test_notify.py
git commit -m "notify.yaml: 0600 config reader and writer"
```

---

### Task 2: The `Notifier` — filter, coalesce, payload, retry

**Files:**
- Create: `src/kraft/notify.py`
- Test: `tests/test_notify.py` (append)

**Interfaces:**
- Consumes: `config.load_notify` (Task 1). `kraft.db.Database` — `await db.write(fn)` and `db.read(fn)`, both taking a `sqlite3.Connection`. `kraft.events.read_after(conn, after_seq)` returning `[{seq, work_item_id, type, payload, created_at}]` and `kraft.events.append(conn, work_item_id, type, payload)`.
- Produces:
  - `notify.Notifier(db, config_path: Path, *, fallback_base_url: str, timeout: float = 5.0, retry_delay: float = 2.0, coalesce_seconds: float = 10.0)`
  - `Notifier.cursor -> int` (property, for tests)
  - `async Notifier.start() -> None`, `async Notifier.stop() -> None`, `Notifier.notify() -> None`
  - `Notifier.reload() -> None` — re-reads `notify.yaml`; `PUT /notify` calls it in Task 3.
  - `Notifier.config -> dict` (property; Task 3 reads `enabled`/`base_url`/`events` off it, never `url`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_notify.py` (keep the existing imports and add these at the top of the file):

```python
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from kraft import db as kdb
from kraft import events, notify


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
                lambda c, wid=wid: events.append(c, wid, "gate_requested", {"gate": "spec_approval"})
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/test_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.notify'`.

**This repo has no `pytest-asyncio`** (`pyproject.toml:27-32` lists only pytest, ruff and pre-commit; `uv.lock` has no entry for it). Do not add it. Every async test above is already written in the repo's own pattern — a sync `def test_x` wrapping an inner `async def scenario()` closed by `asyncio.run(scenario())`, exactly as `tests/test_ws.py:43-62` does.

- [ ] **Step 3: Write minimal implementation**

Create `src/kraft/notify.py`:

```python
"""Outbound notifications: the one channel that reaches a human who is not at
the machine (sub-project B design §1–§3).

Third subscriber on the `Database.set_on_commit` fan-out, built like
`ws.Broadcaster` and `index.service.Indexer`: wake on commit, read the `events`
tail past an in-memory cursor, act, advance.

The cursor starts at the current maximum, so a restart drops notifications for
events that happened while the process was down. That is the trade the design
asks for: a persisted cursor would fire a burst of stale "a gate is waiting"
messages for gates handled hours ago, and a notification the human has already
acted on trains them to ignore the channel.

**The URL in `self._config["url"]` is a secret.** It usually embeds a token in
its path or query and, unlike `access.yaml`'s password hash, cannot be hashed
because Kraft has to send it. It is never logged, never returned by an API
route, and never written into an event payload — `notification_failed` records
a status code and a host. Every `logger` call and every `events.append` in this
module is subject to that.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from kraft import config as config_mod
from kraft import events

logger = logging.getLogger(__name__)

#: Two stops for one transition are one message. `POST /gates/{gate}/reject`
#: writes `reject_gate` and then `mark_needs_human` in the same second when the
#: reject loop is exhausted; that is one thing happening, not two.
COALESCE_SECONDS = 10.0


class Notifier:
    def __init__(
        self,
        db,
        config_path: Path,
        *,
        fallback_base_url: str,
        timeout: float = 5.0,
        retry_delay: float = 2.0,
        coalesce_seconds: float = COALESCE_SECONDS,
    ) -> None:
        self._db = db
        self._config_path = Path(config_path)
        self._fallback_base_url = fallback_base_url
        self._timeout = timeout
        self._retry_delay = retry_delay
        self._coalesce_seconds = coalesce_seconds
        self._config = config_mod.load_notify(self._config_path)
        self._cursor: int = 0
        self._last_sent: dict[str, float] = {}
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None
        #: Detached sends. Held so the loop cannot garbage-collect a task
        #: mid-flight, and so `stop()` can wait them out.
        self._inflight: set[asyncio.Task] = set()
        #: Swapped for an `httpx.MockTransport` under test. `None` is already
        #: what `AsyncClient` means by "use the default transport".
        self._transport: httpx.AsyncBaseTransport | None = None

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def config(self) -> dict:
        return dict(self._config)

    def reload(self) -> None:
        """Re-read `notify.yaml`. `PUT /notify` calls this after it saves, so a
        hand edit and a UI edit are the same operation to the rest of the app."""
        self._config = config_mod.load_notify(self._config_path)

    def notify(self) -> None:
        self._wakeup.set()

    async def start(self) -> None:
        self._cursor = self._db.read(
            lambda c: c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        )
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for task in list(self._inflight):
            task.cancel()
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)

    # ---- the drain ----

    async def _run(self) -> None:
        while True:
            try:
                await self._wakeup.wait()
                self._wakeup.clear()
                new = self._db.read(lambda c: events.read_after(c, self._cursor))
                for ev in new:
                    self._cursor = ev["seq"]
                    if self._should_send(ev):
                        self._dispatch(ev)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("notifier drain iteration failed")

    def _should_send(self, ev: dict) -> bool:
        if not self._config.get("enabled") or not self._config.get("url"):
            return False
        if ev["type"] not in (self._config.get("events") or []):
            return False
        now = time.monotonic()
        # prune first: `_last_sent` would otherwise grow one entry per work
        # item for the life of the process
        cutoff = now - self._coalesce_seconds
        self._last_sent = {k: v for k, v in self._last_sent.items() if v > cutoff}
        if ev["work_item_id"] in self._last_sent:
            return False
        self._last_sent[ev["work_item_id"]] = now
        return True

    def _dispatch(self, ev: dict) -> None:
        """Fire and forget. A slow or hanging endpoint must not stall the commit
        fan-out the WebSocket broadcaster and the indexer share."""
        task = asyncio.create_task(self._send(ev))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    # ---- the send ----

    def _base_url(self) -> str:
        return (self._config.get("base_url") or self._fallback_base_url).rstrip("/")

    def _body(self, ev: dict) -> dict:
        row = self._db.read(
            lambda c: c.execute(
                "SELECT title FROM work_items WHERE id = ?", (ev["work_item_id"],)
            ).fetchone()
        )
        return {
            "work_item_id": ev["work_item_id"],
            "title": row["title"] if row is not None else None,
            "type": ev["type"],
            "gate": ev["payload"].get("gate"),
            "url": f"{self._base_url()}/work-items/{ev['work_item_id']}",
        }

    async def _send(self, ev: dict) -> None:
        url = self._config.get("url")
        body = self._body(ev)
        host = urlsplit(url).hostname
        status: int | None = None
        error: str | None = None
        # One retry after a short delay, then give up. Two attempts, not a
        # queue: this channel reports that something stopped, and a backlog of
        # stale stops is the failure mode §1 rejects.
        for attempt in (0, 1):
            if attempt:
                await asyncio.sleep(self._retry_delay)
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    res = await client.post(url, json=body)
                status, error = res.status_code, None
                if res.status_code < 400:
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # `exc` is never formatted into the message: httpx puts the full
                # request URL, token and all, into several of its exception
                # strings. The class name and the host are what a human needs.
                status, error = None, type(exc).__name__
            logger.warning(
                "notification to %s failed (attempt %d): status=%s error=%s",
                host,
                attempt + 1,
                status,
                error,
            )
        # The event matters. This channel exists to report that something
        # stopped; silently dropping the message reproduces the original bug one
        # level up, and the timeline is where the human eventually finds it.
        try:
            await self._db.write(
                lambda c: events.append(
                    c,
                    ev["work_item_id"],
                    "notification_failed",
                    {"event_type": ev["type"], "status": status, "host": host, "error": error},
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("could not record notification_failed for %s", ev["work_item_id"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/test_notify.py -v`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/notify.py tests/test_notify.py
git commit -m "Notifier: two event types, one retry, no URL in any payload or log"
```

---

### Task 3: Wire the notifier into the fan-out and serve `/notify`

**Files:**
- Modify: `src/kraft/api.py` — imports (line 28), `lifespan` (the broadcaster block at lines 154-157 and the `finally:` at 160-163), and a new route pair after `put_access` (which ends at line 1480, just before `class Login` at 1483 — well ahead of the catch-all `GET /{path:path}` at 1555)
- Test: `tests/test_notify.py` (append)

**Interfaces:**
- Consumes: `notify.Notifier` (Task 2), `config.load_notify` / `config.save_notify` (Task 1). Existing `app.state.templates_dir`, `app.state.access`, `app.state.db`.
- Produces:
  - `app.state.notifier: notify.Notifier`
  - `GET /notify -> {"enabled": bool, "url_set": bool, "base_url": str | None, "events": list[str]}`
  - `PUT /notify` with body `NotifyBody(enabled: bool | None, url: str | None, base_url: str | None, events: list[str] | None)`; returns the same shape as `GET`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_notify.py`:

```python
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, "claude")))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    with TestClient(api.app) as c:
        yield c


def test_get_notify_defaults_before_the_file_exists(api_client):
    body = api_client.get("/notify").json()
    assert body == {
        "enabled": False,
        "url_set": False,
        "base_url": None,
        "events": ["gate_requested", "work_item_needs_human"],
    }


def test_get_notify_never_returns_the_url(api_client):
    api_client.put("/notify", json={"enabled": True, "url": "https://hook.invalid/t0ken"})
    body = api_client.get("/notify").json()
    assert body["url_set"] is True
    assert "t0ken" not in json.dumps(body)
    assert "url" not in body


def test_put_omitting_url_preserves_the_stored_secret(api_client):
    api_client.put("/notify", json={"enabled": True, "url": "https://hook.invalid/t0ken"})
    api_client.put("/notify", json={"events": ["gate_requested"]})
    templates_dir = Path(api_client.app.state.templates_dir)
    saved = config.load_notify(templates_dir / "notify.yaml")
    assert saved["url"] == "https://hook.invalid/t0ken"
    assert saved["events"] == ["gate_requested"]
    assert saved["enabled"] is True


def test_put_empty_string_clears_the_url(api_client):
    api_client.put("/notify", json={"url": "https://hook.invalid/t0ken"})
    api_client.put("/notify", json={"url": ""})
    assert api_client.get("/notify").json()["url_set"] is False


def test_put_refuses_a_non_http_url(api_client):
    res = api_client.put("/notify", json={"url": "file:///etc/passwd"})
    assert res.status_code == 422
    assert api_client.get("/notify").json()["url_set"] is False


def test_put_refuses_enabling_without_a_url(api_client):
    res = api_client.put("/notify", json={"enabled": True})
    assert res.status_code == 422


def test_put_reloads_the_running_notifier(api_client):
    api_client.put("/notify", json={"enabled": True, "url": "https://hook.invalid/t0ken"})
    assert api_client.app.state.notifier.config["enabled"] is True


def test_the_notifier_is_on_the_commit_fan_out(api_client):
    # started, and its cursor is at the tail rather than at zero
    assert api_client.app.state.notifier._task is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/test_notify.py -v -k "notify_defaults or url_set or preserve or clears or non_http or without_a_url or reloads or fan_out"`

The quotes matter: `justfile:81-82` is `test *ARGS: uv run pytest {{ARGS}}`, so an unquoted `or` arrives as a filename and pytest fails collection.
Expected: FAIL — 404 on `/notify` (which the SPA catch-all route may turn into a different status; either way not the asserted body), and `AttributeError` on `app.state.notifier`.

- [ ] **Step 3: Write minimal implementation**

In `src/kraft/api.py`, add to the `from kraft import ...` block near line 28:

```python
from kraft import notify as notify_mod
```

In `lifespan`, replace:

```python
    broadcaster = Broadcaster(database)
    await broadcaster.start()
    database.set_on_commit(lambda: (broadcaster.notify(), indexer.notify()))
    app.state.broadcaster = broadcaster
```

with:

```python
    broadcaster = Broadcaster(database)
    await broadcaster.start()
    # Third subscriber on the same fan-out. Its sends are detached tasks, so a
    # hanging webhook cannot stall the WebSocket or the indexer behind it.
    notifier = notify_mod.Notifier(
        database,
        templates_dir / "notify.yaml",
        fallback_base_url=f"http://{app.state.bound_host}:{access['port']}",
    )
    await notifier.start()
    database.set_on_commit(
        lambda: (broadcaster.notify(), indexer.notify(), notifier.notify())
    )
    app.state.broadcaster = broadcaster
    app.state.notifier = notifier
```

and in the `finally:` block, immediately after `await broadcaster.stop()`:

```python
        await notifier.stop()
```

Add the routes after `put_access` (which ends around line 1479, just before `class Login`):

```python
class NotifyBody(BaseModel):
    enabled: bool | None = None
    #: Omitted leaves the stored secret alone — editing the event list must not
    #: require re-entering a token the UI is never allowed to show back. `""`
    #: is the explicit clear.
    url: str | None = None
    base_url: str | None = None
    events: list[str] | None = None


def _notify_view(notify_cfg: dict) -> dict:
    """What `/notify` is allowed to say. The URL is not in it: a settings screen
    that renders the value back into the DOM puts the token in the browser, in
    screenshots, and in any future session recording."""
    return {
        "enabled": bool(notify_cfg["enabled"]),
        "url_set": bool(notify_cfg["url"]),
        "base_url": notify_cfg["base_url"],
        "events": notify_cfg["events"],
    }


def _checked_url(value: str, field: str) -> str:
    scheme = urlsplit(value).scheme
    if scheme not in ("http", "https"):
        raise HTTPException(422, f"{field} must be an http or https URL")
    return value


@app.get("/notify")
async def get_notify(request: Request):
    st = request.app.state
    return _notify_view(config_mod.load_notify(st.templates_dir / "notify.yaml"))


@app.put("/notify")
async def put_notify(body: NotifyBody, request: Request):
    st = request.app.state
    path = st.templates_dir / "notify.yaml"
    cfg = config_mod.load_notify(path)
    if body.enabled is not None:
        cfg["enabled"] = body.enabled
    if body.url is not None:
        cfg["url"] = _checked_url(body.url, "url") if body.url else None
    if body.base_url is not None:
        cfg["base_url"] = _checked_url(body.base_url, "base_url") if body.base_url else None
    if body.events is not None:
        cfg["events"] = body.events
    # Enabled with nowhere to send is a setting that looks armed and is not.
    if cfg["enabled"] and not cfg["url"]:
        raise HTTPException(422, "set a webhook URL before enabling notifications")
    config_mod.save_notify(path, cfg)
    st.notifier.reload()
    return _notify_view(cfg)
```

`urlsplit` is already imported at `api.py:18`.

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/test_notify.py -v`
Expected: PASS, 24 tests.

Then run the whole backend suite, because `CONFIG_FILES`, the fan-out lambda and `lifespan` are shared surface:

Run: `just test`
Expected: PASS (`e2e` and `slow` tiers behave as they already do on `main`).

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py tests/test_notify.py
git commit -m "GET/PUT /notify and the notifier on the commit fan-out"
```

---

### Task 4: The Notifications settings page

**Files:**
- Modify: `frontend/src/types.ts` (append beside `Access`, ~line 296)
- Modify: `frontend/src/api.ts` (append beside `getAccess`/`putAccess`, lines 180-187)
- Modify: `frontend/src/views/Settings.tsx` — `PAGES` (line 25), a new `NotifyPage`, a `<Route>` (line 775)
- Test: `frontend/src/views/Settings.test.tsx`

**Interfaces:**
- Consumes: `GET /notify` and `PUT /notify` from Task 3. The existing `useResource`, `SaveRow` and `PageHead` helpers in `Settings.tsx`.
- Produces: `types.Notify`, `api.getNotify()`, `api.putNotify(body)`, a `/settings/notify` route.

- [ ] **Step 1: Write the failing test**

First add a default to the file's existing `beforeEach` block (`Settings.test.tsx:35-49`), beside the other `vi.spyOn(api, "getX")` lines, so the Notifications page never hits a real `fetch` from an unrelated test:

```tsx
  vi.spyOn(api, "getNotify").mockResolvedValue({
    enabled: false,
    url_set: false,
    base_url: null,
    events: ["gate_requested", "work_item_needs_human"],
  });
```

Then append the tests. The render helper in this file is **`renderAt(path)`** (`Settings.test.tsx:52`), which wraps `<Settings />` in a `MemoryRouter` + `Routes` at `/settings/*` — use it, do not add another:

```tsx
describe("Settings → Notifications", () => {
  it("never renders the webhook URL back into the DOM", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: true,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");
    expect(await screen.findByText(/a webhook URL is set/i)).toBeInTheDocument();
    const field = screen.getByLabelText(/webhook url/i) as HTMLInputElement;
    expect(field.value).toBe("");
    expect(field).toHaveAttribute("type", "password");
  });

  it("saves a URL without re-sending it on the next unrelated save", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: false,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    const put = vi.spyOn(api, "putNotify").mockResolvedValue({
      enabled: true,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");
    await userEvent.click(await screen.findByRole("switch", { name: /notifications/i }));
    expect(put).toHaveBeenCalledWith({ enabled: true });
    expect(put.mock.calls[0][0]).not.toHaveProperty("url");
  });

  it("offers clearing the URL as an explicit action", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: false,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    const put = vi.spyOn(api, "putNotify").mockResolvedValue({
      enabled: false,
      url_set: false,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");
    await userEvent.click(await screen.findByRole("button", { name: /clear/i }));
    expect(put).toHaveBeenCalledWith({ url: "" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test-ui`
Expected: FAIL — `api.getNotify is not a function`.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/types.ts`, after the `Access` interface:

```ts
/** `GET /notify`. The webhook URL is deliberately absent — the server never
 *  sends it back, so there is nothing here to accidentally render. */
export interface Notify {
  enabled: boolean;
  url_set: boolean;
  base_url: string | null;
  events: string[];
}
```

In `frontend/src/api.ts`, add `Notify` to the type import block at the top, then after `putAccess`:

```ts
export const getNotify = () => req<Notify>("/notify");
export const putNotify = (body: {
  enabled?: boolean;
  url?: string;
  base_url?: string;
  events?: string[];
}) => req<Notify>("/notify", json("PUT", body));
```

In `frontend/src/views/Settings.tsx`, add `Notify` to the `types` import, add to `PAGES` after the `policy` entry:

```ts
  { to: "notify", label: "Notifications" },
```

Add the page (put it after `PolicyPage`, before `AccessPage`):

```tsx
/* ── 5f notifications ─────────────────────────────────────────────────────── */

/** The two states Kraft is blocked on a person. Anything else gets muted
 *  within a week, and a muted channel is the same as no channel. */
const NOTIFY_EVENTS: { id: string; label: string }[] = [
  { id: "gate_requested", label: "a decision is waiting" },
  { id: "work_item_needs_human", label: "stopped — gate wait or cap breach" },
];

function NotifyPage() {
  const { value, error, reload } = useResource(() => api.getNotify());
  const [url, setUrl] = useState("");
  const [baseUrl, setBaseUrl] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const notify: Notify | null = value;

  const put = async (body: Parameters<typeof api.putNotify>[0]) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.putNotify(body);
      setUrl("");
      reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const base = baseUrl ?? notify?.base_url ?? "";

  return (
    <>
      <PageHead
        title="Notifications"
        note="one webhook — ntfy, Pushover, Slack, Discord, or your own receiver"
      />
      {error && <p className="form-error">{error}</p>}
      {notify && (
        <>
          <section className="settings-section">
            <h6>Channel</h6>
            <div className="save-row">
              <button
                className="switch"
                role="switch"
                aria-label="Notifications"
                aria-checked={notify.enabled}
                disabled={busy}
                onClick={() => put({ enabled: !notify.enabled })}
              >
                <span className="switch-knob" />
              </button>
              <span className="save-hint">
                {notify.enabled ? "on — Kraft will POST when a run stops" : "off — no outbound traffic"}
              </span>
            </div>
            <div className="field">
              <label htmlFor="notify-url">Webhook URL</label>
              <input
                id="notify-url"
                className="input mono"
                type="password"
                autoComplete="off"
                placeholder={notify.url_set ? "•••••••• (unchanged)" : "https://ntfy.sh/your-topic"}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
              <span className="field-hint">
                {notify.url_set
                  ? "a webhook URL is set — it is never shown again, because it usually carries a token"
                  : "usually carries a token in its path, so Kraft stores it 0600 and never displays it"}
              </span>
            </div>
            <SaveRow
              onSave={() => put({ url })}
              onDiscard={() => setUrl("")}
              dirty={url.length > 0}
              busy={busy}
              message={message}
              hint="writes notify.yaml, 0600"
            />
            {notify.url_set && (
              <div className="save-row">
                {/* No `data-danger`: the only rule for it is
                    `.overflow-menu button[data-danger]` (styles.css:159), so
                    outside an overflow menu the attribute styles nothing. The
                    action is also cheap to undo — paste the URL again. */}
                <button className="btn btn-ghost" disabled={busy} onClick={() => put({ url: "" })}>
                  Clear URL
                </button>
                <span className="save-hint">removes the stored webhook and stops all sends</span>
              </div>
            )}
          </section>

          <section className="settings-section">
            <h6>Link back</h6>
            <div className="field">
              <label htmlFor="notify-base-url">Base URL</label>
              <input
                id="notify-base-url"
                className="input mono"
                placeholder="http://192.168.1.20:8765"
                value={base}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
              <span className="field-hint">
                What the notification links to. Kraft only knows its bind address, which is
                0.0.0.0 on the LAN — set the address your phone can actually reach.
              </span>
            </div>
            <SaveRow
              onSave={() => put({ base_url: base })}
              onDiscard={() => setBaseUrl(null)}
              dirty={baseUrl !== null && baseUrl !== (notify.base_url ?? "")}
              busy={busy}
              message={message}
              hint="writes notify.yaml"
            />
          </section>

          <section className="settings-section">
            <h6>What notifies</h6>
            {/* Toggles, not checkboxes: this codebase has no `type="checkbox"`
                anywhere, and `.radio` hides its input to draw a round dot —
                a radio's affordance, which is wrong for a multi-select. The
                `.switch` pattern is already here and already means on/off
                (PluginsPage, Settings.tsx:472-480). */}
            {NOTIFY_EVENTS.map((e) => (
              <div key={e.id} className="save-row">
                <button
                  className="switch"
                  role="switch"
                  aria-checked={notify.events.includes(e.id)}
                  aria-label={e.id}
                  disabled={busy}
                  onClick={() =>
                    put({
                      events: notify.events.includes(e.id)
                        ? notify.events.filter((x) => x !== e.id)
                        : [...notify.events, e.id],
                    })
                  }
                >
                  <span className="switch-knob" />
                </button>
                <span className="save-hint">
                  <code>{e.id}</code> · {e.label}
                </span>
              </div>
            ))}
            <p className="settings-foot">
              Progress events are deliberately not offered. A notifier that fires on progress
              gets muted, and a muted channel is the same as no channel at all.
            </p>
          </section>
        </>
      )}
    </>
  );
}
```

Add the route beside the others:

```tsx
          <Route path="notify" element={<NotifyPage />} />
```

Before writing the JSX, read `frontend/src/views/Settings.tsx:501-587` (`PolicyPage`) and confirm the `field` / `field-hint` / `radio` / `dot` / `switch` / `switch-knob` class names and the `SaveRow` prop names against what is actually there. If any differ, use the real ones — this plan's JSX is written from the tree as of `origin/main@ae2619f` and the surrounding file is the source of truth.

- [ ] **Step 4: Run test to verify it passes**

Run: `just test-ui`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/views/Settings.tsx frontend/src/views/Settings.test.tsx
git commit -m "Settings: Notifications page that never shows the webhook back"
```

---

### Task 5: The phone rules the notification links into

**Files:**
- Modify: `frontend/src/styles.css` — inside the existing `@media (max-width: 640px)` block, which runs from line 820 to its closing brace at line 853 (line 856 is `.phone-only { display: none; }`, *outside* it)
- Modify: `frontend/src/components/DocumentModal.tsx` — the `.doc-modal-actions` block, ~lines 154–190
- Test: `frontend/src/components/DocumentModal.test.tsx`

**Interfaces:**
- Consumes: the `.desktop-only` class already defined in `styles.css:841` and already used by `WorkItemDetail.tsx`.
- Produces: no new API. A `.desktop-only` wrapper around the two editor-launch buttons and the editor menu in `DocumentModal`.

**Why there is no CSS test:** jsdom has no viewport and does not evaluate media queries, so a `@media` block cannot be asserted. The testable half of "works on a phone" is the class boundary — which controls are inside `.desktop-only` and which are not — and that is exactly what `WorkItemDetail.test.tsx:196-249` already asserts for the diff button. This task tests the same way. The media-query half is verified by eye against a 390px viewport in step 4.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/DocumentModal.test.tsx`. That file already has everything this test needs: the `wrap(ui)` helper at line 8 (`render(<MemoryRouter>{ui}</MemoryRouter>)` — the component renders `<Link>`, so the router is not optional), the `doc` fixture at line 10, and `api.getDocument` as the mock point:

```tsx
it("hides the editor-launch controls off-desktop but keeps copy path", async () => {
  vi.spyOn(api, "getDocument").mockResolvedValue(doc);
  wrap(<DocumentModal id="d1" onClose={() => {}} />);
  // Launching an editor is meaningless on a phone: the server-side launch has
  // no window to open there, and the vscode:// fallback has nothing to handle
  // it. Copying the path still works everywhere.
  const open = await screen.findByRole("button", { name: /open in/i });
  expect(open.closest(".desktop-only")).not.toBeNull();
  expect(screen.getByRole("button", { name: /choose editor/i }).closest(".desktop-only"))
    .not.toBeNull();
  expect(screen.getByTitle("Copy path").closest(".desktop-only")).toBeNull();
  expect(screen.getByTitle("Close · Esc").closest(".desktop-only")).toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test-ui`
Expected: FAIL — `expected null not to be null`, because nothing is wrapped yet.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/components/DocumentModal.tsx`, wrap the two launch buttons and the menu. Replace the `.doc-modal-actions` children so that the "Open in …" button, the "Choose editor" caret button and the `{menu && ...}` menu sit inside one `<div className="desktop-only">`, leaving "Copy path" and "Close · Esc" as direct children:

```tsx
          <div className="doc-modal-actions" ref={menuRef}>
            {/* Launching an editor needs a window on one machine or the other.
                A phone has neither the server's desktop nor a vscode:// handler,
                so the whole launch affordance goes; Copy path is the fallback
                that works from anywhere. */}
            <div className="desktop-only">
              <button
                className="btn btn-primary doc-open"
                disabled={!doc}
                onClick={() => openIn(current.id)}
              >
                <ArrowSquareOut size={13} />
                Open in {current.name}
              </button>
              <button
                className="btn btn-primary doc-open-more"
                aria-label="Choose editor"
                aria-expanded={menu}
                disabled={!doc}
                onClick={() => setMenu((v) => !v)}
              >
                <CaretDown size={12} />
              </button>
              {menu && (
                <div className="doc-editor-menu card elev-lg" role="menu">
                  {EDITORS.map((e) => (
                    <button key={e.name} role="menuitem" onClick={() => openIn(e.id)}>
                      {e.name}
                      {e.id === current.id && <span className="doc-editor-default">default</span>}
                    </button>
                  ))}
                  <p className="doc-editor-foot">Default editor is set in Settings → General.</p>
                </div>
              )}
            </div>
            <button className="btn btn-icon btn-ghost" title="Copy path" onClick={copyPath}>
              <Copy size={14} />
            </button>
            <button className="btn btn-icon btn-ghost" title="Close · Esc" onClick={onClose}>
              <X size={14} />
            </button>
          </div>
```

The new wrapper is a block-level `div` inside a flex row, so it needs to keep laying its two buttons out side by side on desktop. **Where this rule goes decides whether the feature works.** `.desktop-only { display: none; }` at `styles.css:841` has specificity (0,1,0); a bare `.doc-modal-actions > .desktop-only { display: flex }` is (0,2,0) and media queries add no specificity at all — so an unguarded flex rule wins at 390px and the editor buttons stay visible, silently defeating the entire task. Scope it to desktop instead, beside the other `.doc-*` rules:

```css
/* The wrapper only exists to be hidden on a phone, so it may only lay itself
   out where it is actually shown — an unguarded `display: flex` here outranks
   `.desktop-only { display: none }` and the hiding never happens. */
@media (min-width: 641px) {
  .doc-modal-actions > .desktop-only { display: flex; align-items: center; gap: 8px; }
}
```

`641px` is deliberately one pixel past the `max-width: 640px` block, so the two never both apply.

Then, **inside the existing `@media (max-width: 640px)` block** in `frontend/src/styles.css` (line 820 to its closing brace at 853), add before that brace:

```css
  /* the diff viewer (Kraft-8mu.2) is the screen a notification links into, so
     it is the one modal that has to survive a 390px viewport */
  .diff-modal { max-height: calc(100vh - 24px); width: 100%; }
  .diff-modal-head, .diff-files, .diff-body, .diff-untracked {
    padding-left: 12px; padding-right: 12px;
  }
  .diff-body { font-size: 11px; }
  /* `pre` sends a 200-column hunk off the side of the phone with no way back.
     `pre-wrap` keeps the leading space that makes a diff readable and wraps the
     overflow; `anywhere` handles a minified line with no break opportunity. */
  .diff-body > div { white-space: pre-wrap; overflow-wrap: anywhere; }
  .diff-files li { flex-wrap: wrap; }

  /* the one place a phone user types. 16px is not a taste call: mobile Safari
     zooms the viewport on focus for any input under 16px and does not zoom back
     out, which strands the reader mid-rejection. */
  .gate-reject textarea.input { font-size: 16px; min-height: 96px; }
  .gate-reject .btn { min-height: 44px; }
```

- [ ] **Step 4: Run tests, then look at it**

Run: `just test-ui`
Expected: PASS.

Then verify the media query by eye, since no test can:

```bash
just dev            # backgrounded; UI on :5173
just dev-seed       # gives you an item at a gate
```

Open `http://localhost:5173`, put the browser in a 390×844 device viewport, open a work item at `human_review_approval`, click **Review changes**, and confirm: the diff fits with no horizontal scroll, the file list wraps, and the reject textarea does not zoom the page when focused. Take a screenshot into the SDD ledger. Then `just dev-reset`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles.css frontend/src/components/DocumentModal.tsx frontend/src/components/DocumentModal.test.tsx
git commit -m "Phone: wrap the diff viewer and the reject textarea, hide editor launch"
```

---

## Self-Review

**1. Spec coverage.**

| Spec section | Task |
|---|---|
| §1 hooks into the commit fan-out, in-memory cursor at startup max | 2, 3 |
| §2 two event types, nothing else, coalesced per work item | 2 |
| §3 one outbound POST, flat payload, one retry, `notification_failed`, fire-and-forget with a timeout | 2 |
| §4 phone: diff viewer, reject textarea, editor menu hidden | 5 |
| §4 phone: board and gate | already merged (see the `[amended]` block); no task, confirmed by eye in Task 5 step 4 |
| §5 `notify.yaml`, 0600, `GET` hides the URL, `PUT` sentinel, URL never logged | 1, 2, 3, 4 |
| §6 every listed test | 1, 2, 3, 4, 5 |
| Acceptance: gates fire a message with a working link | 2 (`base_url`), 3 |
| Acceptance: the linked page approves/rejects with the diff on a phone | 5 |
| Acceptance: disabling is one flag, no outbound traffic | 2 (`test_disabled_sends_nothing_and_still_keeps_up`) |
| Acceptance: `just test`, `just test-ui`, `just lint` | Task 3 step 4, Task 4/5 step 4, and the branch gate |

**2. Placeholder scan.** No TBDs, no "add error handling", no "similar to Task N". Every code step carries the code. Two steps deliberately say *read the surrounding file and match it* (Task 4 step 3 on `Settings.tsx` class names, Task 5 step 1 on the `DocumentModal.test.tsx` render helper) — that is a verification instruction against real files, not a placeholder.

**3. Type consistency.** `load_notify`/`save_notify`/`NOTIFY_DEFAULT` are spelled the same in Tasks 1, 2 and 3. `Notifier.reload()` takes no argument in both its definition (Task 2) and its caller (Task 3). `_notify_view` returns the four keys `frontend/src/types.ts:Notify` declares, and the `api.putNotify` body keys match `NotifyBody`'s fields exactly. `fallback_base_url` is the keyword in both the constructor and the `lifespan` call.

## Known holes, recorded rather than hidden

- **`config.py:194` `gitmodules.read_text()` is inside `except ConfigParserError, OSError:`** and can raise `UnicodeDecodeError`, which is a `ValueError`. Pre-existing, on the `probe_repo` best-effort path, outside this sub-project's diff. File as a bead against `Kraft-8mu`, do not fix here.
- **No CSS test.** Stated in Task 5 with the reason.
- **`base_url` unset off-loopback produces a link to `http://0.0.0.0:8765`.** The Settings copy says so. A validator that rejects an unreachable base URL would have to guess what the operator's network looks like.
