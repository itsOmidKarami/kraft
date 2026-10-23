"""The `rig` and `rig_offline` fixtures (`support.remote`), registered by `tests/conftest.py`."""

import pytest


@pytest.fixture
async def rig(tmp_path, database, run_dirs, item_on):
    """An in-process server (hub, runs, attach listener) and one online machine `gpu` (capacity 2),
    a repo on the item's branch, and an item (`rig1`) whose rows the events are written against."""
    from kraft.remote import runs as runs_mod
    from support.remote import make_rig

    r = await make_rig(tmp_path, database, run_dirs, item_on)
    yield r
    await r.listener.close()
    runs_mod.install(None)
    await r.hub.close()


@pytest.fixture
async def rig_offline(tmp_path, database, run_dirs, item_on):
    """The same, with the machine not yet connected: `await rig_offline.connect()` brings it up."""
    from kraft.remote import runs as runs_mod
    from support.remote import make_rig

    r = await make_rig(tmp_path, database, run_dirs, item_on, online=False)
    yield r
    await r.listener.close()
    runs_mod.install(None)
    await r.hub.close()
