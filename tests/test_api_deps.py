"""`api.deps.spawn`/`cancel`: one live task per key, and a cancel that
actually finishes before it returns."""

from __future__ import annotations

import asyncio
import gc
import warnings
from types import SimpleNamespace

import pytest

from kraft.api import deps


def _app():
    return SimpleNamespace(state=SimpleNamespace(tasks={}))


async def _never_returning():
    await asyncio.Event().wait()


def test_spawn_refuses_a_second_live_task_for_the_same_id():
    async def scenario():
        app = _app()
        first = deps.spawn(app, "w1", _never_returning())
        assert app.state.tasks["w1"] is first
        with pytest.raises(deps.AlreadyRunning):
            deps.spawn(app, "w1", _never_returning())
        assert app.state.tasks["w1"] is first  # untouched by the refused call
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)

    asyncio.run(scenario())


def test_spawn_closes_the_refused_coroutine_with_no_leak_warning():
    async def scenario():
        app = _app()
        first = deps.spawn(app, "w1", _never_returning())
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(deps.AlreadyRunning):
                deps.spawn(app, "w1", _never_returning())
            gc.collect()
        assert not any("was never awaited" in str(w.message) for w in caught)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)

    asyncio.run(scenario())


def test_spawn_allows_a_new_task_once_the_old_one_is_done():
    async def scenario():
        app = _app()

        async def quick():
            return "done"

        first = deps.spawn(app, "w1", quick())
        await first
        await asyncio.sleep(0)  # let the done-callback pop the entry
        assert "w1" not in app.state.tasks
        second = deps.spawn(app, "w1", quick())
        assert app.state.tasks["w1"] is second
        await second

    asyncio.run(scenario())


def test_cancel_awaits_the_task_so_a_fresh_spawn_is_not_refused():
    async def scenario():
        app = _app()

        async def guarded():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise  # deps.guard's own shape: re-raise, never swallow

        task = deps.spawn(app, "w1", guarded())
        await deps.cancel(app, "w1")
        assert task.done() and task.cancelled()
        assert "w1" not in app.state.tasks
        # the point of `cancel`: no await between it and the next spawn, and
        # spawn must still not be refused
        second = deps.spawn(app, "w1", guarded())
        assert app.state.tasks["w1"] is second
        await deps.cancel(app, "w1")

    asyncio.run(scenario())


def test_cancel_with_timeout_returns_before_a_slow_task_finishes_unwinding():
    """Kraft-c5ui: a task parked somewhere that doesn't see the cancellation
    right away (a to_thread git/forge call, in production) must not block
    the caller forever. `timeout` returns at the deadline regardless, and
    the task is left to keep cancelling in the background on its own."""

    async def scenario():
        app = _app()
        started_cancel = asyncio.Event()

        async def slow_to_unwind():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                started_cancel.set()
                await asyncio.sleep(0.2)  # stands in for a to_thread call still in flight
                raise

        task = deps.spawn(app, "w1", slow_to_unwind())
        await asyncio.sleep(0)  # let it reach its own `await` before cancelling --
        # cancel()'d before its first tick, a task's body never runs at all (its
        # own try/except included), and `started_cancel` would never be set.
        await deps.cancel(app, "w1", timeout=0.01)
        await started_cancel.wait()
        assert not task.done()  # still unwinding -- cancel returned anyway
        assert "w1" in app.state.tasks  # its done-callback hasn't fired yet

        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)  # let the done-callback run
        assert "w1" not in app.state.tasks  # pops on its own once it actually exits

    asyncio.run(scenario())


def test_cancel_on_an_absent_or_already_done_task_is_a_noop():
    async def scenario():
        app = _app()
        await deps.cancel(app, "nope")  # nothing there at all

        async def quick():
            return 1

        task = deps.spawn(app, "w1", quick())
        await task
        await deps.cancel(app, "w1")  # already done -- must not raise

    asyncio.run(scenario())
