"""Outbound notifications: the one channel that reaches a human who is not at
the machine (sub-project B design §1-§3).

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
route (the `config` property excludes it, exposing `url_set: bool` instead),
and never written into an event payload -- `notification_failed` records a
status code and a host. Every `logger` call and every `events.append` in this
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
        self._config = self._load_config()
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
        """`enabled`/`base_url`/`events`, plus `url_set` -- never `url` itself.
        The webhook URL must never leave `_send`; an API route (Task 3) reading
        this property to answer `GET /notify` cannot leak what it never sees."""
        return {
            **{k: v for k, v in self._config.items() if k != "url"},
            "url_set": bool(self._config.get("url")),
        }

    def reload(self) -> None:
        """Re-read `notify.yaml`. `PUT /notify` calls this after it saves, so a
        hand edit and a UI edit are the same operation to the rest of the app."""
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Fail safe on a `notify.yaml` Kraft cannot parse: `__init__` calling
        this must not raise -- that would leave the broadcaster, indexer,
        `index_conn` and database un-stopped on the way out of `lifespan` --
        and `reload()` calling this must not keep serving a stale config
        either. Falling back to `NOTIFY_DEFAULT` (`enabled: False`) means a
        config Kraft cannot parse sends nothing, which is the same failure
        mode as any other invalid setting: safe, not silent -- the warning
        below is the record. `config_mod.load_notify` already sanitizes the
        message before it ever reaches here, so logging it is not a leak."""
        try:
            return config_mod.load_notify(self._config_path)
        except config_mod.ConfigError as exc:
            logger.warning("%s -- notifications disabled until it is fixed", exc)
            return {
                **config_mod.NOTIFY_DEFAULT,
                "events": list(config_mod.NOTIFY_DEFAULT["events"]),
            }

    def notify(self) -> None:
        self._wakeup.set()

    async def start(self, *, cursor: int | None = None) -> None:
        # httpx's own logger records "HTTP Request: POST <full-url> ..." at
        # INFO, token and all -- a leak this module's own logging carefully
        # avoids. Closed here (scoped to a live notifier actually sending)
        # rather than at import time (a process-wide side effect of merely
        # importing this module). A later `logging.config.dictConfig` that
        # explicitly reconfigures the "httpx" logger would reopen this; this
        # repo has no central logging config to register the exception with,
        # so that residual is accepted rather than built around.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        # `cursor`, when given, is a `MAX(seq)` snapshot the caller took
        # *before* anything that can itself emit events ran (reattach's
        # resumed executors, the indexer's startup scan). Reading `MAX(seq)`
        # here instead would snapshot past whatever those already produced
        # while this coroutine was still waiting its turn, and silently drop
        # it -- the exact "a gate is waiting and nobody was told" failure
        # this module exists to prevent. Every existing caller has no such
        # snapshot and keeps the old behaviour.
        if cursor is not None:
            self._cursor = cursor
        else:
            self._cursor = self._db.read(
                lambda c: c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
            )
        # `set_on_commit` -- the only other thing that ever calls `notify()`
        # -- is not wired up until `lifespan` gets back from this coroutine,
        # so nothing will wake the drain for events already sitting past the
        # cursor above (a resumed work item that stops and raises a gate
        # with no further commits after it, which is what "needs human"
        # means). Self-wake instead of waiting on a commit that may never
        # come. A no-op on the no-arg path: cursor is already `MAX(seq)`,
        # so `read_after` finds nothing to send.
        self._wakeup.set()
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
        # `urlsplit` wants a str; notify.yaml has no schema validation (an
        # operator typo like `base_url: 8080` reads back as a YAML int), so a
        # malformed config value must not be assumed away here either.
        host = urlsplit(url).hostname if isinstance(url, str) else None
        status: int | None = None
        error: str | None = None
        try:
            body = self._body(ev)
            # One retry after a short delay, then give up. Two attempts, not a
            # queue: this channel reports that something stopped, and a
            # backlog of stale stops is the failure mode §1 rejects.
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
                    # `exc` is never formatted into the message: httpx puts the
                    # full request URL, token and all, into several of its
                    # exception strings. The class name and the host are what
                    # a human needs.
                    status, error = None, type(exc).__name__
                logger.warning(
                    "notification to %s failed (attempt %d): status=%s error=%s",
                    host,
                    attempt + 1,
                    status,
                    error,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Anything raised while building the request -- a malformed
            # `base_url`, a DB error reading the work item's title, whatever
            # -- must still land as `notification_failed`. This is the same
            # class of bug the module exists to fix, one level further in:
            # a stop that silently produces no record at all.
            status, error = None, type(exc).__name__
            logger.warning("notification to %s failed before sending: error=%s", host, error)
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
