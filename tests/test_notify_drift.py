"""The one webhook notice per new kraft-default drift state."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import yaml
from test_notify import _notifier

from kraft import config
from kraft.addons import drift
from kraft.api import deps

LINES = ["chains/default.yaml is a full copy"]


def _state(tmp_path, notifier):
    addons = tmp_path / "addons"
    addons.mkdir(exist_ok=True)
    return SimpleNamespace(
        templates_dir=tmp_path,
        run_dirs=SimpleNamespace(addons=addons),
        notifier=notifier,
        kraft_default=None,
        kraft_default_sent=None,
    )


async def _settle(n):
    await asyncio.gather(*list(n._inflight))


@pytest.fixture
def lines(monkeypatch):
    box = {"v": list(LINES)}
    monkeypatch.setattr(drift, "lines", lambda *a: list(box["v"]))
    return box


async def test_a_new_state_is_sent_once(tmp_path, database, lines):
    sends: list = []
    n = _notifier(tmp_path, database, sends)
    st = _state(tmp_path, n)
    deps.refresh_drift(st)
    await _settle(n)
    deps.refresh_drift(st)
    await _settle(n)
    assert len(sends) == 1
    assert sends[0][1] == {
        "work_item_id": None,
        "title": "Kraft",
        "type": "kraft_default_drift",
        "gate": None,
        "url": "http://127.0.0.1:8765",
        "text": "Kraft's own chains need attention: 1 item(s). Run kraft admin doctor.",
    }
    # a restart with the same state sends nothing
    deps.refresh_drift(_state(tmp_path, n))
    await _settle(n)
    assert len(sends) == 1


async def test_a_state_seen_before_the_notifier_exists_is_sent_when_it_does(
    tmp_path, database, lines
):
    sends: list = []
    n = _notifier(tmp_path, database, sends)
    st = _state(tmp_path, None)
    deps.refresh_drift(st)  # startup: lint_loaded runs before the notifier is built
    st.notifier = n
    deps.refresh_drift(st)
    await _settle(n)
    assert len(sends) == 1


async def test_a_changed_state_is_sent_again(tmp_path, database, lines):
    sends: list = []
    n = _notifier(tmp_path, database, sends)
    st = _state(tmp_path, n)
    deps.refresh_drift(st)
    await _settle(n)
    lines["v"] = [*LINES, "another"]
    deps.refresh_drift(st)
    await _settle(n)
    assert len(sends) == 2


async def test_two_overlapping_refreshes_send_once(tmp_path, database, lines):
    sends: list = []
    n = _notifier(tmp_path, database, sends)
    st = _state(tmp_path, n)
    deps.refresh_drift(st)
    deps.refresh_drift(st)
    await _settle(n)
    assert len(sends) == 1


@pytest.mark.parametrize("off", ["switch", "webhook"])
async def test_nothing_is_sent_and_it_is_marked_when_the_switch_is_off(
    tmp_path, database, lines, off
):
    sends: list = []
    n = _notifier(tmp_path, database, sends)
    cfg = config.Notify(
        enabled=off != "webhook", url="https://hook.invalid/t", kraft_default=off != "switch"
    )
    cfg.save(tmp_path / "notify.yaml")
    n._config_path = tmp_path / "notify.yaml"
    n.reload()
    st = _state(tmp_path, n)
    deps.refresh_drift(st)
    await _settle(n)
    assert sends == []
    assert drift.notified(st.run_dirs.addons) == drift.signature(LINES)


async def test_a_failed_send_is_not_marked(tmp_path, database, lines):
    sends: list = []
    n = _notifier(tmp_path, database, sends, answer=500, retry_delay=0)
    st = _state(tmp_path, n)
    deps.refresh_drift(st)
    await _settle(n)
    assert len(sends) == 2  # one retry
    assert drift.notified(st.run_dirs.addons) is None


async def test_a_send_that_lands_after_the_state_cleared_marks_nothing(tmp_path, database, lines):
    import httpx

    sends: list = []
    n = _notifier(tmp_path, database, sends, retry_delay=0)
    st = _state(tmp_path, n)

    async def transport(request: httpx.Request) -> httpx.Response:
        sends.append(request)
        if len(sends) == 1:  # a reload clears the drift while the first post fails
            lines["v"] = []
            deps.refresh_drift(st)
            return httpx.Response(500)
        return httpx.Response(200)

    n._transport = httpx.MockTransport(transport)
    deps.refresh_drift(st)
    await _settle(n)
    assert len(sends) == 2
    assert drift.notified(st.run_dirs.addons) is None


async def test_a_cleared_state_notifies_again_when_it_returns(tmp_path, database, lines):
    sends: list = []
    n = _notifier(tmp_path, database, sends)
    st = _state(tmp_path, n)
    deps.refresh_drift(st)
    await _settle(n)
    lines["v"] = []
    deps.refresh_drift(st)
    assert drift.notified(st.run_dirs.addons) is None
    lines["v"] = list(LINES)
    deps.refresh_drift(st)
    await _settle(n)
    assert len(sends) == 2


def test_settings_save_keeps_a_hand_written_switch(client, templates_dir):
    (templates_dir / "notify.yaml").write_text("kraft_default: false\n")
    res = client.put("/api/notify", json={"enabled": True, "url": "https://hook.invalid/t"})
    assert res.status_code == 200
    assert yaml.safe_load((templates_dir / "notify.yaml").read_text())["kraft_default"] is False
