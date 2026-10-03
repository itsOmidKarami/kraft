"""`GET /api/update` and `POST /api/update/check`: the version check
`kraft.update` makes, as the board's update banner reads it."""

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
        "assets": [
            {
                "name": "kraft-0.4.0-py3-none-any.whl",
                "browser_download_url": "https://x/w.whl",
            }
        ],
    }
]


@pytest.fixture
def feed(tmp_path, monkeypatch):
    """The releases feed as a list the test swaps, counting each fetch."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    # The suite sets it everywhere (conftest); these tests are about the check.
    monkeypatch.delenv("KRAFT_NO_UPDATE_CHECK", raising=False)
    state = {"payload": RELEASE_JSON, "fetches": 0}

    def fetch(_url, _timeout):
        state["fetches"] += 1
        if isinstance(state["payload"], Exception):
            raise state["payload"]
        return state["payload"]

    monkeypatch.setattr(update, "_fetch", fetch)
    monkeypatch.setattr(update, "installed", lambda: "0.3.0")
    return state


def test_a_newer_feed_is_behind_and_the_same_version_is_not(client, feed, monkeypatch):
    body = client.get("/api/update").json()
    assert (body["installed"], body["latest"], body["channel"], body["behind"]) == (
        "0.3.0",
        "v0.4.0",
        "stable",
        True,
    )
    assert body["checked_at"]
    monkeypatch.setattr(update, "installed", lambda: "0.4.0")
    assert client.get("/api/update").json()["behind"] is False


def test_an_unreachable_feed_is_unknown_never_up_to_date(client, feed):
    feed["payload"] = httpx.ConnectError("no route")
    body = client.get("/api/update").json()
    assert (body["latest"], body["behind"], body["checked_at"]) == (None, None, None)


def test_an_unreachable_feed_keeps_the_time_of_the_last_good_check(client, feed):
    client.get("/api/update")
    cache = json.loads(update._cache_path().read_text())
    update._cache_path().write_text(json.dumps({**cache, "checked_at": 1.0}))  # expired
    feed["payload"] = httpx.ConnectError("no route")
    body = client.get("/api/update").json()
    assert (body["latest"], body["behind"]) == (None, None)
    assert body["checked_at"].startswith("1970-01-01T00:00:01")


def test_a_cached_check_is_used_until_ttl_and_post_check_bypasses_it(client, feed):
    client.get("/api/update")
    client.get("/api/update")
    assert feed["fetches"] == 1
    assert client.post("/api/update/check").json()["latest"] == "v0.4.0"
    assert feed["fetches"] == 2


def test_the_channel_defaults_to_the_installed_ones_and_is_validated(client, feed, monkeypatch):
    monkeypatch.setattr(update, "installed", lambda: "0.3.0rc1")
    assert client.get("/api/update").json()["channel"] == "rc"
    assert client.get("/api/update?channel=alpha").json()["channel"] == "alpha"
    assert client.get("/api/update?channel=nightly").status_code == 400
    assert client.post("/api/update/check?channel=nightly").status_code == 400


def test_no_update_check_makes_the_get_read_the_cache_only_and_leaves_the_posts_alone(
    client, feed, monkeypatch
):
    """The sidebar reads GET on every page load; that is not an explicit check."""
    monkeypatch.setenv("KRAFT_NO_UPDATE_CHECK", "1")
    body = client.get("/api/update").json()
    assert (body["latest"], body["behind"], body["checked_at"]) == (None, None, None)
    assert feed["fetches"] == 0
    assert not update._cache_path().exists()
    # A person asking ("check now") is still answered ...
    assert client.post("/api/update/check").json()["latest"] == "v0.4.0"
    assert feed["fetches"] == 1
    # ... and what it found is then what the quiet GET reports.
    assert client.get("/api/update").json()["latest"] == "v0.4.0"
    assert feed["fetches"] == 1


def test_a_failed_check_is_not_retried_by_the_next_page_load(client, feed):
    feed["payload"] = httpx.ConnectError("no route")
    client.get("/api/update")
    client.get("/api/update")
    assert feed["fetches"] == 1
    # "Check now" is not held to the window.
    feed["payload"] = RELEASE_JSON
    assert client.post("/api/update/check").json()["latest"] == "v0.4.0"
    assert feed["fetches"] == 2
