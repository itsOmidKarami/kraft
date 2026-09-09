"""`kraft.update` - the version check.

Its three callers (admin health, admin doctor, admin start) all sit on paths a
disconnected machine must still walk, so the contract under test is mostly
negative: no failure mode raises, and no failure mode reports an update.
"""

from __future__ import annotations

import json

import httpx
import pytest

from kraft import update

RELEASE_JSON = [
    {
        "tag_name": "v0.4.0",
        "assets": {"links": [{"name": "kraft-0.4.0-py3-none-any.whl", "url": "https://x/w.whl"}]},
    }
]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    return tmp_path / "run" / "update-check.json"


def _fetch(payload):
    def fetch(_url, _timeout):
        return payload

    return fetch


def test_latest_reads_the_newest_release(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    release = update.latest()
    assert release.tag == "v0.4.0"
    assert release.wheel_url == "https://x/w.whl"


def test_latest_caches_to_disk(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()
    assert json.loads(cache.read_text())["tag"] == "v0.4.0"


def test_a_cache_hit_does_not_fetch(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()

    def explode(_url, _timeout):
        raise AssertionError("fetched inside the TTL")

    monkeypatch.setattr(update, "_fetch", explode)
    assert update.latest().tag == "v0.4.0"


def test_an_expired_cache_refetches(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()
    stale = json.loads(cache.read_text())
    stale["checked_at"] = 0
    cache.write_text(json.dumps(stale))
    newer = [
        {
            "tag_name": "v0.5.0",
            "assets": {"links": [{"name": "k.whl", "url": "https://x/n.whl"}]},
        }
    ]
    monkeypatch.setattr(update, "_fetch", _fetch(newer))
    assert update.latest().tag == "v0.5.0"


@pytest.mark.parametrize(
    "boom",
    [
        httpx.ConnectError("no network"),
        httpx.ReadTimeout("too slow"),
        ValueError("not json"),
    ],
)
def test_every_transport_failure_is_none_not_an_exception(cache, monkeypatch, boom):
    def fetch(_url, _timeout):
        raise boom

    monkeypatch.setattr(update, "_fetch", fetch)
    assert update.latest() is None


def test_a_garbage_body_is_none(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch({"not": "a list"}))
    assert update.latest() is None


def test_an_empty_release_list_is_none(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch([]))
    assert update.latest() is None


def test_a_release_with_no_wheel_is_none(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch([{"tag_name": "v9.0.0", "assets": {"links": []}}]))
    assert update.latest() is None


@pytest.mark.parametrize(
    ("here", "there", "behind"),
    [
        ("0.3.0", "v0.4.0", True),
        ("0.4.0", "v0.4.0", False),
        ("0.5.0", "v0.4.0", False),
        ("0.3.1.dev4+g1a2b3c", "v0.3.0", False),
        ("0.3.1.dev4+g1a2b3c", "v0.4.0", True),
        ("0.0.0+source", "v0.4.0", True),
        ("0.4.0", "vnonsense", False),
    ],
)
def test_is_behind_compares_versions(monkeypatch, here, there, behind):
    monkeypatch.setattr(update, "installed", lambda: here)
    assert update.is_behind(update.Release(tag=there, wheel_url="u")) is behind


def test_is_behind_of_nothing_is_false():
    assert update.is_behind(None) is False


def test_perform_installs_the_wheel_from_the_release():
    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        return type("R", (), {"returncode": 0})()

    code = update.perform(update.Release(tag="v0.4.0", wheel_url="https://x/w.whl"), run=run)
    assert code == 0
    assert seen["command"] == [
        "uv",
        "tool",
        "install",
        "--force",
        "--from",
        "https://x/w.whl",
        "kraft",
    ]


def test_perform_reports_a_failing_installer():
    def run(_command, **_kwargs):
        return type("R", (), {"returncode": 2})()

    assert update.perform(update.Release(tag="v0.4.0", wheel_url="u"), run=run) == 2


def test_perform_without_uv_is_a_readable_failure():
    def run(_command, **_kwargs):
        raise FileNotFoundError("uv")

    with pytest.raises(SystemExit, match="uv"):
        update.perform(update.Release(tag="v0.4.0", wheel_url="u"), run=run)
