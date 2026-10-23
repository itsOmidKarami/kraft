"""`kraft.update`: telling a dev install from a release, putting a receipt entry
back as an install argument, and the newest release of each major version."""

from __future__ import annotations

import json

import pytest
from support.releases import release_entry

from kraft import update


@pytest.mark.parametrize(
    ("entry", "dev"),
    [
        pytest.param(
            {"name": "kraft-sdlc", "directory": "/Users/omidkarami/Projects/Kraft"},
            True,
            id="dev-directory",
        ),
        pytest.param({"name": "kraft-sdlc", "editable": "/src/kraft"}, True, id="dev-editable"),
        pytest.param({"name": "kraft-sdlc", "url": "file:///tmp/k.whl"}, True, id="dev-file-url"),
        pytest.param({"name": "kraft-sdlc", "specifier": "==2.2.0"}, False, id="release-version"),
        pytest.param(
            {
                "name": "kraft-sdlc",
                "url": "https://github.com/x/y/releases/k-2.2.0-py3-none-any.whl",
            },
            False,
            id="release-wheel-url",
        ),
    ],
)
def test_is_dev_install(entry, dev):
    assert update.is_dev_install(entry) is dev


def test_is_dev_install_reads_this_installs_own_receipt(tmp_path, monkeypatch):
    prefix = tmp_path / "venv"
    prefix.mkdir()
    (prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "kraft-sdlc", directory = "/Users/me/Kraft" }]\n'
    )
    monkeypatch.setattr(update.sys, "prefix", str(prefix))
    assert update.is_dev_install() is True
    assert update.installed_requirement()["directory"] == "/Users/me/Kraft"


@pytest.mark.parametrize(
    ("entry", "argument"),
    [
        pytest.param(
            {"name": "kraft-sdlc", "specifier": "==2.2.0"}, "kraft-sdlc==2.2.0", id="version"
        ),
        pytest.param(
            {"name": "kraft-sdlc", "url": "https://x/k.whl"},
            "kraft-sdlc @ https://x/k.whl",
            id="wheel-url",
        ),
        pytest.param(
            {"name": "kraft-sdlc", "extras": ["vector"], "specifier": "==2.2.0"},
            "kraft-sdlc[vector]==2.2.0",
            id="with-extras",
        ),
    ],
)
def test_install_argument(entry, argument):
    assert update.install_argument(entry) == argument


def test_latest_in_major_older_major_still_gets_patches(tmp_path, monkeypatch):
    """Once 3.0.0 exists `latest` is 3.0.0, so a 2.x install would never be
    offered 2.9.1 if the check kept only the newest tag."""
    feed = [release_entry("v3.0.0"), release_entry("v2.9.1"), release_entry("v2.9.0")]
    monkeypatch.setattr(update, "_cache_path", lambda: tmp_path / "update-check.json")
    monkeypatch.setattr(update, "_fetch", lambda *_: feed)
    assert update.latest_in_major(2, channel="stable").tag == "v2.9.1"
    assert update.latest().tag == "v3.0.0"
    assert update.latest_in_major(1, channel="stable") is None

    # The one fetch fed all three answers: the cache holds each major's newest.
    monkeypatch.setattr(update, "_fetch", lambda *_: pytest.fail("fetched inside the TTL"))
    assert update.latest_in_major(3, channel="stable").tag == "v3.0.0"
    assert update.latest_in_major(2, channel="stable").tag == "v2.9.1"


def test_latest_in_major_refetches_a_cache_that_predates_majors(tmp_path, monkeypatch):
    import time

    cache = tmp_path / "update-check.json"
    monkeypatch.setattr(update, "_cache_path", lambda: cache)
    cache.write_text(
        json.dumps(
            {"tag": "v3.0.0", "wheel_url": "u", "channel": "stable", "checked_at": time.time()}
        )
    )
    feed = [release_entry("v3.0.0"), release_entry("v2.9.1")]
    monkeypatch.setattr(update, "_fetch", lambda *_: feed)
    assert update.latest_in_major(2, channel="stable").tag == "v2.9.1"
