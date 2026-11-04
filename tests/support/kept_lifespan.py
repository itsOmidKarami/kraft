"""The app's lifespan, held open for a whole test (`app` in `tests/conftest.py`)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading

import httpx


class KeptLifespan:
    """The app's lifespan held open on a loop of its own for a whole test, entered
    on the first call. `cli.main()` runs `asyncio.run()` per command, so a loop of
    the caller's cannot hold it; each call is forwarded into this one instead."""

    def __init__(self, app):
        self.app = app
        self.loop = None
        self.transport = httpx.ASGITransport(app=app)

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _start(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        started = concurrent.futures.Future()

        async def hold():
            # One task enters and leaves it: anyio's cancel scopes insist on that.
            self.stop = asyncio.Event()
            try:
                async with self.app.router.lifespan_context(self.app):
                    started.set_result(None)
                    await self.stop.wait()
            except BaseException as exc:
                if not started.done():
                    started.set_exception(exc)
                raise

        self.task = self._run(hold())
        started.result()

    async def call(self, request):
        if self.loop is None:
            self._start()
        return await asyncio.wrap_future(self._run(self.transport.handle_async_request(request)))

    def close(self):
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self.stop.set)
        self.task.result()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()
