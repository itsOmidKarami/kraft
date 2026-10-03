"""`kraft.update`'s cache: what a failed check leaves behind, and `cache_only`.
A sibling of `tests/test_update.py`, which is at its line budget.

A page load that checks for updates must not cost an offline host its timeout
each time, so a failure is remembered for `update.RETRY_AFTER`."""

from __future__ import annotations

import json

import httpx
import pytest

from kraft import update

RELEASE_JSON = [
    {
        "tag_name": "v0.4.0",
        "draft": False,
        "prerelease": False,
        "assets": [{"name": "k.whl", "browser_download_url": "https://x/k.whl"}],
    }
]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Where the check is remembered, under a scratch KRAFT_HOME."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    return update._cache_path()


@pytest.mark.parametrize(
    ("age", "fetches"),
    [(update.RETRY_AFTER - 60, 1), (update.RETRY_AFTER + 60, 2), (-60, 2)],
    ids=["inside-the-retry-window", "after-it", "failed-in-the-future"],
)
@pytest.mark.parametrize(
    "feed",
    [httpx.ConnectError("no network"), [], {"not": "a list"}],
    ids=["unreachable", "no-releases", "garbage-body"],
)
def test_a_failed_check_is_not_repeated_until_the_retry_window_ends(
    cache, monkeypatch, feed, age, fetches
):
    """An offline host paid `TIMEOUT` on every page load: a failure is cached too."""
    calls = []

    def fetch(_url, _timeout):
        calls.append(1)
        if isinstance(feed, Exception):
            raise feed
        return feed

    monkeypatch.setattr(update, "_fetch", fetch)
    assert update.latest() is None
    failed = json.loads(cache.read_text())
    cache.write_text(json.dumps({**failed, "failed_at": failed["failed_at"] - age}))
    assert update.latest() is None
    assert len(calls) == fetches


def test_a_failed_check_is_retried_when_forced_and_keeps_the_last_good_one(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", (lambda _u, _t: RELEASE_JSON))
    assert update.latest().tag == "v0.4.0"
    good = json.loads(cache.read_text())

    def down(_url, _timeout):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(update, "_fetch", down)
    assert update.latest(force=True) is None
    # The success is still what `last_checked` and a later cache read see.
    assert update.last_checked() == good["checked_at"]
    assert update.latest().tag == "v0.4.0"
    # ... and the force went out although the failure was recent.
    calls = []
    monkeypatch.setattr(update, "_fetch", lambda *_: calls.append(1) or RELEASE_JSON)
    assert update.latest(force=True).tag == "v0.4.0"
    assert calls == [1]


def test_cache_only_never_fetches(cache, monkeypatch):
    def explode(*_):
        raise AssertionError("fetched")

    monkeypatch.setattr(update, "_fetch", explode)
    assert update.latest(cache_only=True) is None
    assert not cache.exists()
    monkeypatch.setattr(update, "_fetch", (lambda _u, _t: RELEASE_JSON))
    update.latest()
    monkeypatch.setattr(update, "_fetch", explode)
    assert update.latest(cache_only=True).tag == "v0.4.0"


@pytest.mark.parametrize(
    "text", ["[]", "null", "1", '"x"'], ids=["list", "null", "number", "string"]
)
def test_a_cache_file_that_is_not_an_object_is_a_miss_not_a_crash(cache, monkeypatch, text):
    cache.parent.mkdir(parents=True)
    cache.write_text(text)
    assert update.last_checked() is None
    monkeypatch.setattr(update, "_fetch", lambda *_: RELEASE_JSON)
    assert update.latest().tag == "v0.4.0"
    assert json.loads(cache.read_text())["tag"] == "v0.4.0"
    cache.write_text(text)
    monkeypatch.setattr(update, "_fetch", lambda *_: [])
    assert update.latest() is None
    assert json.loads(cache.read_text())["channel"] == "stable"


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_the_cache_is_replaced_whole_never_written_in_place(cache, monkeypatch, fails):
    """A reader must never catch a half-written file."""
    swaps = []
    real = update.os.replace
    monkeypatch.setattr(update.os, "replace", lambda a, b: swaps.append((a, b)) or real(a, b))
    monkeypatch.setattr(update, "_fetch", lambda *_: [] if fails else RELEASE_JSON)
    update.latest()
    update.latest(force=True)
    (src, dst), (again, _) = swaps
    assert (dst, src.parent) == (cache, cache.parent)
    assert src != again, "each write needs its own temp file: the server writes from threads"
    assert [p.name for p in cache.parent.iterdir()] == [cache.name]
