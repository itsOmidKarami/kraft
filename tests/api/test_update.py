"""`GET /api/update` and `POST /api/update/check`: the version check
`kraft.update` makes, as the board's update banner reads it."""

from __future__ import annotations

import json
import sys

import httpx
import pytest
from support.releases import release_entry

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
    assert body["python"] == f"{sys.version_info.major}.{sys.version_info.minor}"
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


# ── marking a release for when Kraft is free ──


@pytest.fixture
def release_install(feed, monkeypatch):
    """A release install on a uv tool, which can take a mark."""
    monkeypatch.setattr(update, "is_dev_install", lambda entry=None: False)
    monkeypatch.setattr(update, "install_kind", lambda: "uv")
    feed["payload"] = [release_entry("v0.4.1"), release_entry("v0.4.0")]
    return feed


def test_mark_then_get_shows_it(client, release_install):
    body = client.post("/api/update/mark", json={"tag": "v0.4.1"}).json()

    assert body["marked"]["tag"] == "v0.4.1" and body["marked"]["by"] == "you"
    assert body["pending"]["release"] == "v0.4.1"
    assert client.get("/api/update").json()["marked"]["tag"] == "v0.4.1"


def test_a_tag_without_the_v_is_the_same_release(client, release_install):
    client.post("/api/update/mark", json={"tag": "0.4.1"})
    assert client.get("/api/update").json()["marked"]["tag"] == "v0.4.1"


def test_mark_refused_on_dev_install(client, release_install, monkeypatch):
    monkeypatch.setattr(update, "is_dev_install", lambda entry=None: True)
    r = client.post("/api/update/mark", json={"tag": "v0.4.1"})
    assert r.status_code == 409 and "dev install" in r.json()["detail"]
    assert client.get("/api/update").json()["marked"] is None


@pytest.mark.parametrize("kind", ["brew", "pipx", "pip", "source"])
def test_mark_refused_when_uv_did_not_install_it(client, release_install, monkeypatch, kind):
    monkeypatch.setattr(update, "install_kind", lambda: kind)
    r = client.post("/api/update/mark", json={"tag": "v0.4.1"})
    assert r.status_code == 409
    assert client.get("/api/update").json()["marked"] is None


@pytest.mark.parametrize(
    ("tag", "status", "says"),
    [
        pytest.param("v9.9.9", 404, "no release v9.9.9", id="no-such-release"),
        pytest.param("v0.3.0", 409, "not newer", id="the-installed-one"),
    ],
)
def test_mark_refuses_what_it_could_not_install(client, release_install, tag, status, says):
    release_install["payload"] = [release_entry("v0.4.1"), release_entry("v0.3.0")]
    r = client.post("/api/update/mark", json={"tag": tag})
    assert (r.status_code, says in r.json()["detail"]) == (status, True)


def test_mark_a_major_by_hand_is_accepted(client, release_install):
    release_install["payload"] = [release_entry("v1.0.0"), release_entry("v0.4.1")]
    body = client.post("/api/update/mark", json={"tag": "v1.0.0"}).json()
    assert body["marked"]["tag"] == "v1.0.0"


def test_unmark(client, release_install):
    client.post("/api/update/mark", json={"tag": "v0.4.1"})
    body = client.delete("/api/update/mark").json()
    assert body["marked"] is None and body["pending"]["release"] is None


def test_restart_when_free_stores_the_one_shot(client, release_install):
    from kraft import when_free

    body = client.post("/api/update/restart-when-free").json()

    assert body["pending"]["restart"] == ["a restart was asked for"]
    assert when_free._read(client.app.state)["restart_asked"]


def test_update_shows_latest_in_major_beside_a_newer_major(client, release_install):
    release_install["payload"] = [
        release_entry("v1.0.0"),
        release_entry("v0.4.1"),
        release_entry("v0.4.0"),
    ]
    body = client.get("/api/update").json()
    assert (body["latest"], body["latest_in_major"], body["behind"]) == ("v1.0.0", "v0.4.1", True)


def test_update_says_check_disabled(client, release_install, monkeypatch):
    assert client.get("/api/update").json()["check_disabled"] is False
    monkeypatch.setenv("KRAFT_NO_UPDATE_CHECK", "1")
    assert client.get("/api/update").json()["check_disabled"] is True


def test_update_uses_the_stored_channel(client, release_install, templates_dir):
    release_install["payload"] = [
        release_entry("v0.4.1"),
        release_entry("v0.5.0rc1", prerelease=True),
    ]
    assert client.get("/api/update").json()["latest"] == "v0.4.1"
    client.put("/api/update/settings", json={"channel": "rc"})

    body = client.get("/api/update").json()
    assert (body["channel"], body["latest"]) == ("rc", "v0.5.0rc1")
    # An explicit channel is for that read only.
    assert client.get("/api/update?channel=stable").json()["latest"] == "v0.4.1"
    assert client.get("/api/update").json()["channel"] == "rc"


def test_update_says_what_it_is_waiting_for_and_what_became_of_the_last_try(
    client, release_install
):
    from kraft import when_free

    client.post("/api/update/mark", json={"tag": "v0.4.1"})
    st = client.app.state
    when_free.record_outcome(st, status="failed", key="v0.4.1", detail="would not start")

    body = client.get("/api/update").json()

    assert body["outcome"]["status"] == "failed"
    assert any("would not start" in why for why in body["blocked"])
