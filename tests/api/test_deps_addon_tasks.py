"""The server's background add-on tasks: every one tracked, so shutdown stops
each, and none of their file work on the event loop."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from support.addons import drop_store, load_release

from kraft.addons import load as addons_load
from kraft.api import deps


def _addon_state():
    """The three attributes the server keeps its add-on work in."""
    return SimpleNamespace(addon_tasks=set(), addon_threads=set(), addon_cancel=threading.Event())


async def test_two_background_addon_tasks_are_both_tracked():
    app = SimpleNamespace(state=SimpleNamespace(addon_tasks=set()))
    gates = [asyncio.Event(), asyncio.Event()]

    def work(i):
        async def run(_app):
            await gates[i].wait()

        return run

    first = deps.in_background(app, work(0))
    second = deps.in_background(app, work(1))
    await asyncio.sleep(0)
    assert app.state.addon_tasks == {first, second}

    gates[0].set()
    await first
    assert app.state.addon_tasks == {second}

    gates[1].set()
    await second
    assert app.state.addon_tasks == set()


async def test_shutdown_cancels_every_addon_task_and_stops_the_thread(monkeypatch):
    monkeypatch.setattr(deps, "ADDON_SHUTDOWN_GRACE_S", 2)
    st = _addon_state()
    app = SimpleNamespace(state=st)
    running = [threading.Event(), threading.Event()]
    ended = [threading.Event(), threading.Event()]

    def work(i, cancel):  # loops on the flag, as `restore_missing` does between add-ons
        running[i].set()
        while not cancel.is_set():
            time.sleep(0.005)
        time.sleep(0.05)  # winding down: shutdown must wait for this, not only cancel
        ended[i].set()

    def task(i):
        async def run(app_):
            await deps._in_thread(app_.state, work, i, cancel=app_.state.addon_cancel)

        return run

    tasks = [deps.in_background(app, task(i)) for i in (0, 1)]
    try:
        for started in running:  # both are in their thread: neither is only queued
            assert await asyncio.to_thread(started.wait, 2)

        await deps.stop_addon_work(st)

        assert all(e.is_set() for e in ended)
        assert all(t.done() for t in tasks)
        assert st.addon_tasks == set() and st.addon_threads == set()
    finally:
        st.addon_cancel.set()


async def test_shutdown_names_a_thread_that_outlives_the_grace(monkeypatch, caplog):
    monkeypatch.setattr(deps, "ADDON_SHUTDOWN_GRACE_S", 0.05)
    st = _addon_state()
    release = threading.Event()

    def stuck_in_a_fetch():
        release.wait(5)

    async def run(app_):
        await deps._in_thread(app_.state, stuck_in_a_fetch)

    deps.in_background(SimpleNamespace(state=st), run)
    await asyncio.sleep(0.05)

    await deps.stop_addon_work(st)

    assert "add-on worker thread still running at shutdown" in caplog.text
    assert "stuck_in_a_fetch" in caplog.text
    release.set()
    await asyncio.sleep(0.05)


def test_a_restore_reloads_the_library_off_the_loop(client, tmp_path, monkeypatch):
    st = client.app.state
    seen = {}
    real = deps._read_library

    def spying(st, refused):
        seen["thread"] = threading.get_ident()
        return real(st, refused)

    monkeypatch.setattr(deps, "_read_library", spying)
    drop_store(load_release(client, tmp_path))  # a missing store: the restore reloads
    loop_thread = client.portal.call(threading.get_ident)

    client.portal.call(deps.restore_addons, client.app)

    assert seen["thread"] != loop_thread
    assert "release:ship" in st.library.chain_ids


def test_a_reload_that_a_newer_one_overtook_does_not_publish(client, tmp_path, monkeypatch):
    """The read runs in a thread, so a sync reload can finish while it is in
    flight: the older read must not replace what the newer one published."""
    st = client.app.state
    load_release(client, tmp_path)
    started, release = threading.Event(), threading.Event()
    real = deps._read_library
    reads = []

    def slow_first(st, refused):
        reads.append(1)
        if len(reads) == 1:
            started.set()
            release.wait(5)
            return None, ["stale"]
        return real(st, refused)

    monkeypatch.setattr(deps, "_read_library", slow_first)

    async def overtaken():
        reload = asyncio.ensure_future(deps.reload_templates(st))
        await asyncio.to_thread(started.wait, 5)
        deps._reload_templates(st)  # a sync caller, on the loop, finishes first
        release.set()
        await reload

    client.portal.call(overtaken)

    assert st.library is not None and st.invalid_library == []


def test_collect_addon_stores_reads_the_db_on_the_loop(client, monkeypatch):
    where = {}
    real_gc = addons_load.gc

    def gc(keep, root=None):
        where["gc"] = threading.get_ident()
        return real_gc(keep, root)

    monkeypatch.setattr(addons_load, "gc", gc)
    real_read = client.app.state.db.read

    def read(fn):
        where.setdefault("db", threading.get_ident())
        return real_read(fn)

    monkeypatch.setattr(client.app.state.db, "read", read)
    loop_thread = client.portal.call(threading.get_ident)

    client.portal.call(deps.collect_addon_stores, client.app.state)

    assert where["db"] == loop_thread and where["gc"] != loop_thread
